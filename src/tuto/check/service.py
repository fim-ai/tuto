"""The /check API: submit an arXiv id, poll a job, read the lead report.

Deliberately small: sqlite job store, one worker thread processing jobs strictly
one at a time (a check takes minutes and is LLM-bound; a queue position is honest
UX), per-IP daily quota, and a recent-result cache so the same paper is never
audited twice in a week.

Run: uvicorn tuto.check.service:app --host 0.0.0.0 --port 8801
Env: CITO_API_BASE, CITO_API_KEY, LLM_BASE_URL, LLM_API_KEY,
     GROBID_URL (default http://localhost:8070),
     CHECK_DAILY_LIMIT (default 5), CHECK_RESULT_TTL_DAYS (default 7).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from tuto.check.run_one import check_arxiv, normalize_arxiv_id

ROOT = Path(__file__).resolve().parents[3]
DB_PATH = ROOT / "data" / "checks" / "jobs.db"

DAILY_LIMIT = int(os.environ.get("CHECK_DAILY_LIMIT", "5"))
RESULT_TTL_DAYS = int(os.environ.get("CHECK_RESULT_TTL_DAYS", "7"))
GROBID_URL = os.environ.get("GROBID_URL", "http://localhost:8070")

app = FastAPI(title="tuto check", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://tuto.fim.ai", "http://localhost:5297"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_wake = threading.Event()


def _db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            arxiv_id TEXT NOT NULL,
            ip TEXT,
            status TEXT NOT NULL,          -- queued | running | done | error
            stage TEXT,
            created_at REAL NOT NULL,
            finished_at REAL,
            result TEXT,
            error TEXT
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_arxiv ON jobs(arxiv_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_ip ON jobs(ip, created_at)")
    return conn


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class CheckRequest(BaseModel):
    arxiv_id: str


@app.post("/check")
def submit(req: CheckRequest, request: Request) -> dict:
    aid = normalize_arxiv_id(req.arxiv_id)
    if not aid:
        raise HTTPException(422, "not a valid arXiv id or URL")
    ip = _client_ip(request)
    conn = _db()
    try:
        # Recent finished result for the same paper: reuse, costs the caller nothing.
        row = conn.execute(
            "SELECT job_id FROM jobs WHERE arxiv_id=? AND status='done' AND created_at>? "
            "ORDER BY created_at DESC LIMIT 1",
            (aid, time.time() - RESULT_TTL_DAYS * 86400),
        ).fetchone()
        if row:
            return {"job_id": row["job_id"], "reused": True}
        # A queued/running job for the same paper: attach to it.
        row = conn.execute(
            "SELECT job_id FROM jobs WHERE arxiv_id=? AND status IN ('queued','running') "
            "ORDER BY created_at DESC LIMIT 1",
            (aid,),
        ).fetchone()
        if row:
            return {"job_id": row["job_id"], "reused": True}
        n_today = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE ip=? AND created_at>?",
            (ip, time.time() - 86400),
        ).fetchone()[0]
        if n_today >= DAILY_LIMIT:
            raise HTTPException(429, f"daily limit of {DAILY_LIMIT} checks per address reached")
        job_id = uuid.uuid4().hex[:12]
        conn.execute(
            "INSERT INTO jobs (job_id, arxiv_id, ip, status, created_at) VALUES (?,?,?,?,?)",
            (job_id, aid, ip, "queued", time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    _wake.set()
    return {"job_id": job_id, "reused": False}


@app.get("/check/{job_id}")
def status(job_id: str) -> dict:
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "unknown job")
        ahead = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status='queued' AND created_at<?",
            (row["created_at"],),
        ).fetchone()[0] if row["status"] == "queued" else 0
    finally:
        conn.close()
    out = {
        "job_id": row["job_id"],
        "arxiv_id": row["arxiv_id"],
        "status": row["status"],
        "stage": row["stage"],
        "queue_ahead": ahead,
    }
    if row["status"] == "done" and row["result"]:
        out["result"] = json.loads(row["result"])
    if row["status"] == "error":
        out["error"] = row["error"]
    return out


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "time": datetime.now(UTC).isoformat()}


def _worker() -> None:
    while True:
        conn = _db()
        try:
            row = conn.execute(
                "SELECT job_id, arxiv_id FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                conn.close()
                _wake.wait(timeout=10)
                _wake.clear()
                continue
            job_id, aid = row["job_id"], row["arxiv_id"]
            conn.execute(
                "UPDATE jobs SET status='running', stage='starting' WHERE job_id=?", (job_id,)
            )
            conn.commit()
        finally:
            conn.close()

        def set_stage(stage: str, _job=job_id) -> None:
            c = _db()
            c.execute("UPDATE jobs SET stage=? WHERE job_id=?", (stage, _job))
            c.commit()
            c.close()

        try:
            result = check_arxiv(aid, grobid_url=GROBID_URL, progress=set_stage)
            c = _db()
            c.execute(
                "UPDATE jobs SET status='done', finished_at=?, result=? WHERE job_id=?",
                (time.time(), json.dumps(result, ensure_ascii=False), job_id),
            )
            c.commit()
            c.close()
        except Exception as e:  # noqa: BLE001 - a failed job must not kill the worker
            c = _db()
            c.execute(
                "UPDATE jobs SET status='error', finished_at=?, error=? WHERE job_id=?",
                (time.time(), str(e)[:500], job_id),
            )
            c.commit()
            c.close()


@app.on_event("startup")
def start_worker() -> None:
    # Requeue jobs orphaned by a restart before the worker starts.
    conn = _db()
    conn.execute("UPDATE jobs SET status='queued', stage=NULL WHERE status='running'")
    conn.commit()
    conn.close()
    threading.Thread(target=_worker, daemon=True, name="check-worker").start()

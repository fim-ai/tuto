"""Single-paper check: run the full audit funnel on one arXiv paper.

Reuses every venue-run stage unchanged by synthesizing a throwaway run directory
per job (data/runs/check-2026-<id>/). The venue key embeds the current year on
purpose: triage.llm_judge infers the citing paper's year from the venue string,
and an arXiv id like 2001.12345 would otherwise be misread as year 2001.

Output framing is a product red line: everything this returns is a LEAD for human
review, never a verdict. The result carries our own published error rates so the
UI can say so with numbers.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import defusedxml.ElementTree as ET
import httpx

from tuto.models import PaperRecord, read_jsonl, write_jsonl

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"

CHECK_YEAR = datetime.now(UTC).year
MAX_CITES = 60  # L2 cost ceiling per check
ARXIV_ID_RE = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$|^([a-z-]+(\.[A-Z]{2})?/\d{7})(v\d+)?$")

# Published numbers from the ACL 2026 audit report; shown alongside every result.
DISCLOSURES = {
    "first_pass_precision": 0.13,
    "l1_suspicious_confirm_rate": "2 of 12 human-reviewed leads confirmed in the ACL 2026 audit",
    "framing": "Everything below is a lead for human review, not a verdict.",
    "report_url": "https://tuto.fim.ai/report",
}


def normalize_arxiv_id(raw: str) -> str | None:
    """Accept bare ids, versioned ids, and abs/pdf URLs."""
    s = raw.strip()
    s = re.sub(r"^https?://(www\.)?arxiv\.org/(abs|pdf)/", "", s)
    s = re.sub(r"\.pdf$", "", s)
    m = ARXIV_ID_RE.match(s)
    if not m:
        return None
    return m.group(1) or m.group(3)


def fetch_arxiv_meta(arxiv_id: str, client: httpx.Client) -> dict:
    r = client.get(
        "https://export.arxiv.org/api/query", params={"id_list": arxiv_id, "max_results": 1}
    )
    r.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(r.text)
    entry = root.find("a:entry", ns)
    if entry is None:
        raise ValueError(f"arXiv id not found: {arxiv_id}")
    title = re.sub(r"\s+", " ", (entry.findtext("a:title", "", ns) or "")).strip()
    if not title or title.lower() == "error":
        raise ValueError(f"arXiv id not found: {arxiv_id}")
    published = entry.findtext("a:published", "", ns) or ""
    authors = [
        a.findtext("a:name", "", ns) or "" for a in entry.findall("a:author", ns)
    ]
    return {
        "title": title,
        "year": int(published[:4]) if published[:4].isdigit() else CHECK_YEAR,
        "authors": [a for a in authors if a],
    }


def venue_key(arxiv_id: str) -> str:
    return f"check-{CHECK_YEAR}-{arxiv_id.replace('/', '-')}"


def check_arxiv(
    arxiv_id: str,
    grobid_url: str = "http://localhost:8070",
    cito_rps: float = 8.0,
    workers: int = 6,
    progress: Callable[[str], None] = lambda stage: None,
    keep_run_dir: bool = True,
) -> dict:
    """Run the full funnel on one arXiv paper. Returns the result dict."""
    from tuto.ingest.acl_anthology import download_pdfs
    from tuto.l2 import arbiter as arbiter_mod
    from tuto.l2 import support_fulltext
    from tuto.l2.support_sample import pick_contexts
    from tuto.parse.grobid_extract import process_corpus
    from tuto.triage import llm_judge, rescue
    from tuto.verify.cito_backend import CitoBackend
    from tuto.verify.classify import Classifier, verdict_to_dict
    from tuto.verify.local_index import DblpIndex

    venue = venue_key(arxiv_id)
    run_dir = DATA / "runs" / venue
    if run_dir.exists():
        shutil.rmtree(run_dir)  # each job starts clean; results live in the returned dict
    run_dir.mkdir(parents=True)

    # -- ingest: metadata + PDF ------------------------------------------------
    progress("fetching paper")
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        meta = fetch_arxiv_meta(arxiv_id, client)
    paper = PaperRecord(
        paper_id=arxiv_id,
        title=meta["title"],
        authors=meta["authors"],
        year=meta["year"],
        venue=venue,
        volume="check",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        url=f"https://arxiv.org/abs/{arxiv_id}",
    )
    write_jsonl(run_dir / "papers.jsonl", [paper])
    stats = download_pdfs([paper], run_dir / "pdfs", rate_limit=1.0, workers=1)
    if stats.get("failed"):
        raise RuntimeError(f"could not download PDF for {arxiv_id}")

    # -- parse: PDF -> refs + contexts ----------------------------------------
    progress("extracting references")
    refs, contexts, failures = process_corpus(
        run_dir / "pdfs", run_dir / "tei", grobid_url, workers=1, limit=None
    )
    if failures or not refs:
        raise RuntimeError(f"reference extraction failed for {arxiv_id}")
    write_jsonl(run_dir / "refs.jsonl", refs)
    write_jsonl(run_dir / "contexts.jsonl", contexts)

    # -- L1: existence (DBLP snapshot, then Cito) ------------------------------
    progress("checking existence")
    dblp = DblpIndex(DATA / "cache" / "dblp" / "dblp.sqlite")
    cito = CitoBackend(rate_per_sec=cito_rps)
    clf = Classifier(dblp, cito=cito)
    ref_dicts = list(read_jsonl(run_dir / "refs.jsonl"))
    verdicts = [verdict_to_dict(clf.classify(r)) for r in ref_dicts]
    write_jsonl(run_dir / "verdicts.jsonl", verdicts)
    dblp.close()
    cito.close()

    # -- L1 triage: the not_found residue goes through the same funnel the
    # audit used, so raw parse noise is never surfaced as an accusation. -------
    n_not_found = sum(1 for v in verdicts if v["verdict"] == "not_found")
    l1_leads: list[dict] = []
    triage_dist: dict = {}
    judge_dist: dict = {}
    if n_not_found:
        progress("triaging unresolved references")
        rescue_report = rescue.run(venue, limit=None, cito_rps=cito_rps, workers=workers)
        triage_dist = rescue_report.get("stage_dist", {})
        judge_report = llm_judge.run(venue, limit=None, workers=workers, model=None)
        judge_dist = judge_report.get("distribution", {})
        by_rid = {r["ref_id"]: r for r in ref_dicts}
        for s in judge_report.get("suspicious", []):
            ref = by_rid.get(s["ref_id"], {})
            l1_leads.append(
                {
                    "index": ref.get("index"),
                    "raw": ref.get("raw"),
                    "rationale": s.get("rationale"),
                    "confidence": s.get("confidence"),
                }
            )

    # -- L2: claim support on every judgeable citation (capped) ---------------
    progress("judging claim support")
    exists = {v["ref_id"] for v in verdicts if v["verdict"] == "exists"}
    ctx_map = pick_contexts(venue)
    rids = [r["ref_id"] for r in ref_dicts if r["ref_id"] in exists and r["ref_id"] in ctx_map]
    capped = len(rids) > MAX_CITES
    rids = rids[:MAX_CITES]
    l2_report: dict = {}
    arb_rows: list[dict] = []
    if rids:
        l2_report = support_fulltext.run(
            venue, n=len(rids), workers=workers, cito_rps=cito_rps, rids=rids
        )
        if l2_report.get("flagged"):
            progress("arbitrating flags")
            arbiter_mod.run(venue, workers=max(2, workers // 2), cito_rps=cito_rps, model=None)
            arb_path = run_dir / "l2_arbiter_cache.jsonl"
            if arb_path.exists():
                arb_rows = list(read_jsonl(arb_path))

    arb_by_rid = {a["ref_id"]: a for a in arb_rows}
    judged = {r["ref_id"]: r for r in read_jsonl(run_dir / "l2_fulltext_cache.jsonl")} if rids else {}
    confirmed = []
    refuted_count = 0
    for f in l2_report.get("flagged", []):
        a = arb_by_rid.get(f["ref_id"])
        if a is None:
            continue
        if a.get("flag") == "confirmed":
            confirmed.append(
                {
                    "citing_sentence": f.get("citing_sentence"),
                    "cited_title": f.get("cited_title"),
                    "claim_span": a.get("claim_span") or f.get("claim_span"),
                    "first_pass": f.get("support"),
                    "final_support": a.get("support"),
                    "error_class": a.get("error_class"),
                    "rationale": a.get("rationale"),
                    "confidence": a.get("confidence"),
                }
            )
        else:
            refuted_count += 1

    claim_rows = [r for r in judged.values() if r.get("cite_type") == "claim"]
    result = {
        "arxiv_id": arxiv_id,
        "title": meta["title"],
        "year": meta["year"],
        "checked_at": datetime.now(UTC).isoformat(),
        "summary": {
            "references_total": len(ref_dicts),
            "l1": {
                "exists": sum(1 for v in verdicts if v["verdict"] == "exists"),
                "minor_mismatch": sum(1 for v in verdicts if v["verdict"] == "minor_mismatch"),
                "unparseable": sum(1 for v in verdicts if v["verdict"] == "unparseable"),
                "not_found_raw": n_not_found,
                "triage": triage_dist,
                "llm_judge": judge_dist,
                "suspicious_leads": len(l1_leads),
            },
            "l2": {
                "judged": len(judged),
                "claim_cites": len(claim_rows),
                "capped": capped,
                "first_pass_flags": len(l2_report.get("flagged", [])),
                "refuted_by_arbiter": refuted_count,
                "confirmed_leads": len(confirmed),
            },
        },
        "leads": {"existence": l1_leads, "support": confirmed},
        "disclosures": DISCLOSURES,
    }
    (run_dir / "check_result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if not keep_run_dir:
        shutil.rmtree(run_dir)
    return result


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(prog="tuto.check.run_one")
    ap.add_argument("arxiv_id")
    ap.add_argument("--grobid-url", default="http://localhost:8070")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    aid = normalize_arxiv_id(args.arxiv_id)
    if not aid:
        raise SystemExit(f"not a valid arXiv id or URL: {args.arxiv_id}")
    result = check_arxiv(aid, grobid_url=args.grobid_url, workers=args.workers, progress=print)
    print(json.dumps(result["summary"], indent=2, ensure_ascii=False))
    print(f"\nexistence leads: {len(result['leads']['existence'])}")
    print(f"support leads  : {len(result['leads']['support'])}")


if __name__ == "__main__":
    main()

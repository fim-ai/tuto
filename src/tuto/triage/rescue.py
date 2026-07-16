"""Second-pass rescue: compress the not_found residual before any LLM spend.

`not_found` from L1 is a mixed bag, not a suspect list. Two things inflate it, and this
stage removes both so that what reaches the (paid, slower) LLM arbiter is only genuine
candidates:

1. Brittle acceptance. L1's Cito rescue accepts a hit only when the record's title is a
   verbatim SUBSTRING of the raw citation. A line-break hyphen ("Prompt- bert"), an
   abbreviated word, or a venue string spliced into the title all break that test even
   though the paper plainly exists. We re-judge each Cito hit by TOKEN CONTAINMENT -- what
   fraction of the record's title words appear in the raw citation -- which survives those
   surface corruptions while staying precise: a fabricated title's words do not nearly-all
   appear inside some real record, so a high containment ratio is still strong evidence the
   cited work is real. Short titles additionally require an author or year to corroborate.

2. Non-paper artifacts. A large share of the residual is not a paper citation at all --
   a bare URL, a GitHub/HuggingFace link, a software or model name, or an OCR coordinate
   dump leaked from a figure. These can never be "found" in a paper corpus and are not
   hallucinated papers; they are bucketed as `non_paper` and excluded from the suspect
   count rather than counted as fabrication.

Output triage.jsonl has one row per L1 not_found ref, with stage_verdict in
{exists, non_paper, suspect}. Only `suspect` proceeds to llm_judge. Resumable: every Cito
result is checkpointed to rescue_cache.jsonl.

Run: python -m tuto.triage.rescue --venue acl-2026 [--limit N] [--cito-rps 25]
"""

from __future__ import annotations

import argparse
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tqdm import tqdm

from tuto.models import read_jsonl
from tuto.verify.normalize import author_keys, norm_title, title_tokens

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"

YEAR_TOLERANCE = 1
# A record title this long, this well-covered by the citation, is a real match on its own;
# shorter titles collide by chance and need an author or year to corroborate.
STRONG_LEN = 5
STRONG_CONTAINMENT = 0.85
CORROBORATED_CONTAINMENT = 0.90

_URL_RE = re.compile(r"https?://\S+")
_OCR_DUMP_RE = re.compile(r"\[\[\[|\], \[\[|`,\s*\d")  # leaked figure OCR: nested coord lists


@dataclass
class TriageRow:
    ref_id: str
    paper_id: str
    stage_verdict: str  # "exists" | "non_paper" | "suspect"
    matched_via: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


def _year_ok(ref_year: int | None, rec_year: int | None) -> bool | None:
    if ref_year is None or rec_year is None:
        return None
    return abs(ref_year - rec_year) <= YEAR_TOLERANCE


def _hit_year(hit: dict) -> int | None:
    for k in ("year", "published_year", "pub_year"):
        v = hit.get(k)
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v[:4].isdigit():
            return int(v[:4])
    return None


def _hit_authors(hit: dict) -> list[str]:
    out = []
    for a in hit.get("authors") or []:
        if isinstance(a, str):
            out.append(a)
        elif isinstance(a, dict):
            out.append(a.get("name") or "")
    return out


def containment(hit_title: str, raw_tokens: frozenset[str]) -> float:
    """Fraction of the record title's tokens that appear in the raw citation."""
    ht = title_tokens(hit_title)
    if not ht:
        return 0.0
    return len(ht & raw_tokens) / len(ht)


def best_match(raw: str, ref_authors: list[str], ref_year: int | None, hits: list[dict]) -> dict | None:
    """Pick the hit whose title is most contained in the raw citation, if it clears the bar."""
    raw_tokens = frozenset(norm_title(raw).split())
    if not raw_tokens:
        return None
    q_authors = author_keys(ref_authors)

    best = None
    for hit in hits:
        title = hit.get("title") or ""
        ht = title_tokens(title)
        if not ht:
            continue
        c = len(ht & raw_tokens) / len(ht)
        author_overlap = bool(q_authors & author_keys(_hit_authors(hit)))
        year_ok = _year_ok(ref_year, _hit_year(hit))
        corroborated = author_overlap or year_ok is True

        accept = (
            (len(ht) >= STRONG_LEN and c >= STRONG_CONTAINMENT)
            or (c >= CORROBORATED_CONTAINMENT and corroborated)
            or (c >= 0.999 and len(ht) >= 3)
        )
        if accept and (best is None or c > best["_c"]):
            best = {
                "title": title,
                "corpus_id": hit.get("corpus_id") or hit.get("id"),
                "containment": round(c, 3),
                "author_overlap": author_overlap,
                "year_ok": year_ok,
                "_c": c,
            }
    if best:
        best.pop("_c")
    return best


def is_non_paper(raw: str) -> str | None:
    """Classify a residual that is not a paper citation. Returns a reason, or None."""
    r = raw.strip()
    if not r:
        return None
    if _OCR_DUMP_RE.search(r):
        return "ocr_dump"  # figure/table OCR leaked into the reference list
    low = r.lower()
    if "github.com" in low or "huggingface.co" in low:
        return "software_or_model"
    urls = _URL_RE.findall(r)
    if urls:
        # A citation that is mostly a URL -- API docs, a blog, a dataset page -- with little
        # bibliographic text around it is a web resource, not a paper we can verify.
        without = _URL_RE.sub(" ", r)
        alpha_tokens = [t for t in norm_title(without).split() if len(t) > 1]
        if len(alpha_tokens) < 6:
            return "url_resource"
    return None


class Rescuer:
    def __init__(self, cito):
        self.cito = cito

    def judge(self, ref: dict) -> TriageRow:
        rid, pid = ref["ref_id"], ref["paper_id"]
        raw = ref.get("raw") or ""

        hits = self.cito.search(raw, limit=8) if raw else []
        m = best_match(raw, ref.get("authors") or [], ref.get("year"), hits)
        if m is not None:
            return TriageRow(rid, pid, "exists", "cito:fuzzy", m)

        reason = is_non_paper(raw)
        if reason is not None:
            return TriageRow(rid, pid, "non_paper", reason, {"raw_head": raw[:120]})

        return TriageRow(
            rid, pid, "suspect", None,
            {"title": norm_title(ref.get("title")), "year": ref.get("year"), "raw_head": raw[:200]},
        )


def run(venue: str, limit: int | None, cito_rps: float, workers: int) -> dict:
    from tuto.verify.cito_backend import CitoBackend

    run_dir = DATA / "runs" / venue
    refs = {r["ref_id"]: r for r in read_jsonl(run_dir / "refs.jsonl")}
    not_found = [v["ref_id"] for v in read_jsonl(run_dir / "verdicts.jsonl") if v["verdict"] == "not_found"]
    if limit:
        not_found = not_found[:limit]
    print(f"{len(not_found)} not_found refs to re-judge")

    cache_path = run_dir / "rescue_cache.jsonl"
    cache: dict[str, dict] = {}
    if cache_path.exists():
        for row in read_jsonl(cache_path):
            cache[row["ref_id"]] = row
    todo = [rid for rid in not_found if rid not in cache]
    print(f"{len(cache)} cached, {len(todo)} to query")

    cito = CitoBackend(rate_per_sec=cito_rps)
    rescuer = Rescuer(cito)
    write_lock = threading.Lock()
    cache_file = cache_path.open("a", encoding="utf-8")
    errors = 0

    def work(rid: str):
        try:
            row = asdict(rescuer.judge(refs[rid]))
        except Exception:  # noqa: BLE001 - one bad query must not sink the batch
            return None
        with write_lock:
            cache_file.write(json.dumps(row, ensure_ascii=False) + "\n")
            cache_file.flush()
        return row

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for row in tqdm(pool.map(work, todo), total=len(todo), desc="rescue", unit="ref"):
            if row is None:
                errors += 1
    cache_file.close()
    cito.close()

    # Reload the full cache and emit one triage row per not_found ref.
    cache = {row["ref_id"]: row for row in read_jsonl(cache_path)}
    rows = [cache[rid] for rid in not_found if rid in cache]
    (run_dir / "triage.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )

    from collections import Counter
    dist = Counter(r["stage_verdict"] for r in rows)
    via = Counter(r.get("matched_via") for r in rows if r["stage_verdict"] == "non_paper")
    return {
        "input_not_found": len(not_found),
        "processed": len(rows),
        "errors": errors,
        "stage_dist": dict(dist),
        "non_paper_reasons": dict(via),
    }


def main() -> None:
    ap = argparse.ArgumentParser(prog="tuto.triage.rescue")
    ap.add_argument("--venue", required=True)
    ap.add_argument("--limit", type=int, help="dry-run on the first N not_found refs")
    ap.add_argument("--cito-rps", type=float, default=25.0)
    ap.add_argument("--workers", type=int, default=14)
    args = ap.parse_args()

    r = run(args.venue, args.limit, args.cito_rps, args.workers)
    print("\nrescue result:")
    print(f"  input not_found : {r['input_not_found']}")
    print(f"  processed       : {r['processed']}  ({r['errors']} errors)")
    print("  stage verdicts  :")
    for k in ("exists", "non_paper", "suspect"):
        n = r["stage_dist"].get(k, 0)
        print(f"    {k:<10} {n:>7}  {n / max(r['processed'], 1):>6.1%}")
    print(f"  non_paper reasons: {r['non_paper_reasons']}")
    print(f"\n  -> suspect count after rescue: {r['stage_dist'].get('suspect', 0)} (goes to llm_judge)")


if __name__ == "__main__":
    main()

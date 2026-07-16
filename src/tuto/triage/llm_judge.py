"""LLM arbitration of the post-rescue L1 residue: what IS each unresolved reference?

After the fuzzy rescue pass, the remaining `suspect` rows are strings that (a) failed id
lookup, (b) failed raw search, (c) failed token-containment rescue, and (d) did not match
the non-paper regexes. Search cannot say more; the remaining question is not "does a
matching record exist in our indexes" (answered: no) but "what kind of artifact is this
string" -- and that is a reading-comprehension task a small LLM does well:

- known_paper      the model recognizes this exact work and is confident it is real;
                   OUR coverage failed, not the author.
- plausible_paper  a coherent scholarly citation the model does not specifically know;
                   counted as unverifiable, never as fabricated.
- non_paper        software, model, dataset, webpage, docs, standard, thesis, report --
                   things a paper index can never contain. Citation-style issue, not an
                   integrity issue.
- garbled          parse damage: merged entries, truncated lines, table/OCR debris. Our
                   noise, nobody's citation.
- suspicious       presents itself as a real scholarly paper, yet the author-title-venue
                   combination is one the model is confident does not exist. The only
                   class that feeds the integrity story, expected to be tiny, and still
                   only a LEAD for human review, never a verdict.

Run: python -m tuto.triage.llm_judge --venue acl-2026 [--limit N]
"""

from __future__ import annotations

import argparse
import json
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tqdm import tqdm

from tuto.l2.llm_client import LLMClient
from tuto.models import read_jsonl

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"

JUDGE_MODEL = "claude-haiku-4-5-20251001"  # env L1_JUDGE_MODEL overrides

SYSTEM = (
    "You classify one raw bibliography entry scraped from an NLP conference paper. "
    "Automated lookup of this entry already failed against large scholarly indexes "
    "(DBLP, arXiv, OpenAlex, a 148M-paper corpus); do not re-litigate the lookup, judge "
    "what the STRING is. Categories:\n"
    "- known_paper: you recognize this specific work and are confident it is a real "
    "scholarly paper (the lookup failure is index coverage or parsing).\n"
    "- plausible_paper: reads as a coherent scholarly citation (authors, title, venue/year "
    "that fit together) but you do not specifically know the work. DEFAULT for anything "
    "paper-like.\n"
    "- non_paper: software, ML model, dataset, API/docs page, blog, web resource, "
    "standard, thesis, technical report, or organization announcement.\n"
    "- garbled: parse damage -- truncated or merged entries, stray table/figure text, "
    "fragments that are not a usable citation.\n"
    "- suspicious: presents itself as a scholarly paper at a specific venue, yet the "
    "author-title-venue combination is one you are CONFIDENT does not exist (e.g. a "
    "well-covered venue/year you know well). Use sparingly; when torn between suspicious "
    "and plausible_paper, choose plausible_paper."
)

USER_TMPL = """REFERENCE (as parsed from the PDF):
{raw}

Parsed fields: title={title!r}, year={year!r}

Return JSON:
{{"category": one of ["known_paper","plausible_paper","non_paper","garbled","suspicious"],
  "subtype": for non_paper one of ["software","model","dataset","webpage","docs","blog","standard","thesis","report","other"], else null,
  "rationale": "<= 15 words",
  "confidence": 0-1}}"""


def run(venue: str, limit: int | None, workers: int, model: str | None) -> dict:
    run_dir = DATA / "runs" / venue
    refs = {r["ref_id"]: r for r in read_jsonl(run_dir / "refs.jsonl")}
    suspects = [t for t in read_jsonl(run_dir / "triage.jsonl") if t["stage_verdict"] == "suspect"]
    if limit:
        suspects = suspects[:limit]
    print(f"{len(suspects)} suspects to judge")

    cache_path = run_dir / "llm_judge_cache.jsonl"
    done = {}
    if cache_path.exists():
        for row in read_jsonl(cache_path):
            done[row["ref_id"]] = row

    llm = LLMClient(model=model or JUDGE_MODEL)
    write_lock = threading.Lock()
    cache_file = cache_path.open("a", encoding="utf-8")

    def judge(t: dict) -> dict | None:
        rid = t["ref_id"]
        if rid in done:
            return done[rid]
        ref = refs.get(rid) or {}
        raw = (ref.get("raw") or t.get("evidence", {}).get("raw_head") or "").strip()
        if not raw:
            return None
        verdict = llm.judge_json(
            SYSTEM,
            USER_TMPL.format(raw=raw[:400], title=(ref.get("title") or "")[:120], year=ref.get("year")),
        )
        if verdict is None:
            return None
        row = {
            "ref_id": rid, "paper_id": t["paper_id"],
            "category": verdict.get("category"), "subtype": verdict.get("subtype"),
            "rationale": verdict.get("rationale"), "confidence": verdict.get("confidence"),
            "raw_head": raw[:160],
        }
        with write_lock:
            cache_file.write(json.dumps(row, ensure_ascii=False) + "\n")
            cache_file.flush()
        return row

    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for row in tqdm(pool.map(judge, suspects), total=len(suspects), desc="l1-judge", unit="ref"):
            if row:
                results.append(row)
    cache_file.close()
    llm.close()

    dist = Counter(r["category"] for r in results)
    sub = Counter(r.get("subtype") for r in results if r["category"] == "non_paper")
    sus = [r for r in results if r["category"] == "suspicious"]
    report = {
        "suspects_in": len(suspects), "judged": len(results),
        "distribution": dict(dist),
        "non_paper_subtypes": dict(sub),
        "suspicious": sus,
    }
    (run_dir / "l1_judge_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report


def main() -> None:
    import os

    ap = argparse.ArgumentParser(prog="tuto.triage.llm_judge")
    ap.add_argument("--venue", required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--model", default=os.environ.get("L1_JUDGE_MODEL"))
    args = ap.parse_args()

    r = run(args.venue, args.limit, args.workers, args.model)
    n = max(r["judged"], 1)
    print(f"\njudged {r['judged']}/{r['suspects_in']}")
    for k in ["known_paper", "plausible_paper", "non_paper", "garbled", "suspicious"]:
        c = r["distribution"].get(k, 0)
        print(f"  {k:<16} {c:>6}  {c / n:>6.1%}")
    print(f"  non_paper subtypes: {r['non_paper_subtypes']}")
    print(f"\nsuspicious ({len(r['suspicious'])}) -- leads for human review:")
    for s in r["suspicious"][:20]:
        print(f"  conf={s.get('confidence')}  {s['raw_head'][:110]}")
        print(f"    why: {s.get('rationale')}")


if __name__ == "__main__":
    main()

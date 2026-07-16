"""Profile the not_found residual before spending any triage effort on it.

`not_found` is not a suspect list -- it is a mixed bag of real misses, parse noise, and
records outside DBLP/Cito's reach. The funnel we build (author+year rescue, then LLM
arbitration) is only worth building if we know the residual's shape, and each stage should
be sized to the bucket it actually addresses. This reads verdicts.jsonl x refs.jsonl and
reports that shape: how many carry an id, a year, a usable title, and how many are the
2026-snapshot-lag case that no amount of matching can fix.

Run: python -m tuto.triage.analyze --venue acl-2026
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from tuto.models import read_jsonl
from tuto.verify.normalize import norm_arxiv, norm_doi, norm_title

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"


def _bucket(ref: dict) -> dict:
    doi = norm_doi(ref.get("doi"))
    arxiv = norm_arxiv(ref.get("arxiv_id"))
    year = ref.get("year")
    return {
        "has_doi": bool(doi),
        "has_arxiv": bool(arxiv),
        "has_id": bool(doi or arxiv),
        "has_year": year is not None,
        "is_2026": year == 2026,
        "has_title": bool(norm_title(ref.get("title"))),
    }


def analyze(venue: str) -> dict:
    run_dir = DATA / "runs" / venue
    refs = {r["ref_id"]: r for r in read_jsonl(run_dir / "refs.jsonl")}
    verdicts = list(read_jsonl(run_dir / "verdicts.jsonl"))

    dist = Counter(v["verdict"] for v in verdicts)
    via = Counter(v.get("matched_via") for v in verdicts if v["verdict"] == "exists")

    # The residual we triage = not_found. unparseable (no id, no title) is a parse gap, not
    # a candidate, and is reported separately so it never inflates the suspect count.
    nf = [v for v in verdicts if v["verdict"] == "not_found"]
    counts = Counter()
    no_year_no_id = []
    id_bearing = []
    y2026 = []
    matchable = []  # has a title or an id -> a real candidate for author+year rescue
    for v in nf:
        ref = refs.get(v["ref_id"], {})
        b = _bucket(ref)
        for k, hit in b.items():
            if hit:
                counts[k] += 1
        if not b["has_id"] and not b["has_year"]:
            no_year_no_id.append(ref)
        if b["has_id"]:
            id_bearing.append(ref)
        if b["is_2026"]:
            y2026.append(ref)
        if b["has_title"] or b["has_id"]:
            matchable.append(ref)

    total = len(verdicts)
    n = len(nf) or 1
    return {
        "total_refs": total,
        "verdict_dist": {k: dist.get(k, 0) for k in ("exists", "minor_mismatch", "not_found", "unparseable")},
        "verdict_pct": {k: dist.get(k, 0) / total for k in ("exists", "minor_mismatch", "not_found", "unparseable")},
        "exists_via": dict(via),
        "not_found": len(nf),
        "residual_breakdown": {k: (counts[k], counts[k] / n) for k in
                               ("has_id", "has_doi", "has_arxiv", "has_year", "is_2026", "has_title")},
        "no_year_no_id": len(no_year_no_id),
        "id_bearing": len(id_bearing),
        "is_2026": len(y2026),
        "matchable_for_rescue": len(matchable),
        "_samples": {
            "id_bearing": [r.get("raw", "")[:160] for r in id_bearing[:5]],
            "no_year_no_id": [r.get("raw", "")[:160] for r in no_year_no_id[:5]],
            "matchable": [r.get("raw", "")[:160] for r in matchable[:5]],
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(prog="tuto.triage.analyze")
    ap.add_argument("--venue", required=True)
    args = ap.parse_args()
    r = analyze(args.venue)

    print(f"total refs: {r['total_refs']}")
    print("\nL1 verdicts:")
    for k, n in r["verdict_dist"].items():
        print(f"  {k:<15} {n:>7}  {r['verdict_pct'][k]:>6.1%}")
    print("  exists via:", r["exists_via"])

    print(f"\nnot_found residual: {r['not_found']}")
    print("  breakdown (share of not_found):")
    for k, (c, pct) in r["residual_breakdown"].items():
        print(f"    {k:<12} {c:>7}  {pct:>6.1%}")
    print(f"  no year AND no id (parse noise): {r['no_year_no_id']}")
    print(f"  id-bearing (id lookup already missed these): {r['id_bearing']}")
    print(f"  year==2026 (snapshot lag, unfixable): {r['is_2026']}")
    print(f"  matchable for author+year rescue (has title or id): {r['matchable_for_rescue']}")

    print("\nsamples:")
    for label, rows in r["_samples"].items():
        print(f"  [{label}]")
        for raw in rows:
            print(f"    - {raw}")


if __name__ == "__main__":
    main()

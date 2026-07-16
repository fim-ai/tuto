"""Paper-level audit: what share of papers carries at least one confirmed citation problem?

The citation-level rate is a research number; the paper-level rate is the product number.
A ~1% hard-error rate across citations reads as negligible until it is restated per paper:
with ~30-40 checkable claim cites in a typical *ACL paper, even small per-cite rates put a
flag in a substantial fraction of PAPERS, and an author cares about exactly one paper.

Pipeline: sample N papers -> judge EVERY judgeable claim cite of each (support_fulltext in
rids mode, same cache) -> arbitrate every flag (arbiter, strong model) -> report per-paper:
flagged / confirmed counts and the share of papers with >= 1 confirmed problem.

Run: python -m tuto.l2.paper_audit --venue acl-2026 --papers 100
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from tuto.l2 import arbiter as arbiter_mod
from tuto.l2 import support_fulltext
from tuto.l2.support_sample import pick_contexts
from tuto.models import read_jsonl

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"
SEED = 20260716  # distinct from the citation-sample seed: this is a fresh paper-level draw


def run(venue: str, n_papers: int, workers: int, cito_rps: float, arbiter_model: str | None) -> dict:
    run_dir = DATA / "runs" / venue
    refs = list(read_jsonl(run_dir / "refs.jsonl"))
    exists = {v["ref_id"] for v in read_jsonl(run_dir / "verdicts.jsonl") if v["verdict"] == "exists"}
    contexts = pick_contexts(venue)

    by_paper: dict[str, list[str]] = defaultdict(list)
    for r in refs:
        if r["ref_id"] in exists and r["ref_id"] in contexts:
            by_paper[r["paper_id"]].append(r["ref_id"])

    # Papers with too few judgeable cites can't answer "does this paper contain a flag"
    # comparably; require a floor so the per-paper denominator means the same thing.
    eligible = sorted(p for p, rids in by_paper.items() if len(rids) >= 10)
    rng = random.Random(SEED)
    sample = rng.sample(eligible, min(n_papers, len(eligible)))
    rids = [rid for p in sample for rid in by_paper[p]]
    print(f"{len(eligible)} eligible papers; sampled {len(sample)}, {len(rids)} cites to judge")

    support_fulltext.run(venue, n=len(rids), workers=workers, cito_rps=cito_rps, rids=rids)
    arbiter_mod.run(venue, workers=max(2, workers // 2), cito_rps=cito_rps, model=arbiter_model)

    judged = {r["ref_id"]: r for r in read_jsonl(run_dir / "l2_fulltext_cache.jsonl") if r.get("support")}
    arbitrated = {r["ref_id"]: r for r in read_jsonl(run_dir / "l2_arbiter_cache.jsonl")}

    papers = []
    for p in sample:
        rows = [judged[rid] for rid in by_paper[p] if rid in judged]
        claim = [r for r in rows if r.get("cite_type") == "claim"]
        flags = [r for r in claim if r["support"] in ("not_supported", "partial")]
        confirmed = [
            arbitrated[r["ref_id"]] for r in flags
            if arbitrated.get(r["ref_id"], {}).get("flag") == "confirmed"
        ]
        papers.append({
            "paper_id": p, "judged": len(rows), "claim_cites": len(claim),
            "first_pass_flags": len(flags), "confirmed": len(confirmed),
            "confirmed_detail": [
                {k: c.get(k) for k in ("ref_id", "error_class", "claim_span", "rationale", "confidence")}
                for c in confirmed
            ],
        })

    n_flag1 = sum(1 for p in papers if p["first_pass_flags"] >= 1)
    n_conf1 = sum(1 for p in papers if p["confirmed"] >= 1)
    total_claim = sum(p["claim_cites"] for p in papers)
    total_conf = sum(p["confirmed"] for p in papers)
    report = {
        "venue": venue, "papers": len(papers),
        "claim_cites_judged": total_claim,
        "papers_with_first_pass_flag": n_flag1,
        "papers_with_confirmed": n_conf1,
        "pct_papers_confirmed": n_conf1 / max(len(papers), 1),
        "confirmed_total": total_conf,
        "confirmed_per_cite": total_conf / max(total_claim, 1),
        "confirmed_by_class": dict(Counter(
            d["error_class"] for p in papers for d in p["confirmed_detail"]
        )),
        "per_paper": papers,
    }
    (run_dir / "paper_audit_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report


def main() -> None:
    import os

    ap = argparse.ArgumentParser(prog="tuto.l2.paper_audit")
    ap.add_argument("--venue", required=True)
    ap.add_argument("--papers", type=int, default=100)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--cito-rps", type=float, default=20.0)
    ap.add_argument("--arbiter-model", default=os.environ.get("L2_ARBITER_MODEL"))
    args = ap.parse_args()

    r = run(args.venue, args.papers, args.workers, args.cito_rps, args.arbiter_model)
    print(f"\n{r['papers']} papers, {r['claim_cites_judged']} claim cites judged")
    print(f"papers with first-pass flag : {r['papers_with_first_pass_flag']}")
    print(f"papers with CONFIRMED flag  : {r['papers_with_confirmed']}  ({r['pct_papers_confirmed']:.0%})")
    print(f"confirmed problems          : {r['confirmed_total']}  ({r['confirmed_per_cite']:.2%} of claim cites)")
    print(f"by class                    : {r['confirmed_by_class']}")


if __name__ == "__main__":
    main()

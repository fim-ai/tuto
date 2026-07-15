"""Pipeline entrypoint. Every stage writes to data/runs/<venue>/ and is resumable."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from tuto.ingest.acl_anthology import VENUES, collect_papers, download_pdfs, fetch_bib
from tuto.models import write_jsonl

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
CACHE = DATA / "cache"


def cmd_ingest(args: argparse.Namespace) -> None:
    run_dir = DATA / "runs" / args.venue
    run_dir.mkdir(parents=True, exist_ok=True)

    snapshot = fetch_bib(CACHE, force=args.refresh_bib)
    print(f"bib: {snapshot.bytes / 1e6:.1f}MB  last-modified={snapshot.last_modified}")

    papers = collect_papers(snapshot.path, args.venue)
    by_volume = Counter(p.volume for p in papers)
    print(f"\n{args.venue}: {len(papers)} papers")
    for volume, n in sorted(by_volume.items()):
        print(f"  {volume:<28} {n:>5}")

    if args.limit:
        papers = papers[: args.limit]
        print(f"\n--limit {args.limit}: keeping first {len(papers)}")

    write_jsonl(run_dir / "papers.jsonl", papers)

    stats = {"downloaded": 0, "cached": 0, "failed": 0}
    if not args.no_pdf:
        print()
        stats = download_pdfs(
            papers, run_dir / "pdfs", rate_limit=args.rate_limit, workers=args.workers
        )
        print(f"pdfs: {stats}")

    manifest = {
        "venue": args.venue,
        "volumes": VENUES[args.venue],
        "ingested_at": datetime.now(UTC).isoformat(),
        "paper_count": len(papers),
        "by_volume": dict(sorted(by_volume.items())),
        "pdf_stats": stats,
        "sources": {
            "anthology_bib": {
                "url": snapshot.url,
                "last_modified": snapshot.last_modified,
                "sha256": snapshot.sha256,
                "bytes": snapshot.bytes,
            }
        },
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"\nwrote {run_dir}/papers.jsonl + manifest.json")


def cmd_parse(args: argparse.Namespace) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from tqdm import tqdm

    from tuto.parse.grobid_extract import process_corpus
    from tuto.parse.refcount_check import compare, count_entries, select_qa_sample

    run_dir = DATA / "runs" / args.venue
    pdf_dir = run_dir / "pdfs"
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if args.limit:
        pdfs = pdfs[: args.limit]
    print(f"{len(pdfs)} pdfs in {pdf_dir}")

    refs, contexts, failures = process_corpus(
        pdf_dir, run_dir / "tei", args.grobid_url, workers=args.workers, limit=args.limit
    )
    write_jsonl(run_dir / "refs.jsonl", refs)
    write_jsonl(run_dir / "contexts.jsonl", contexts)
    print(f"\ngrobid: {len(refs)} refs, {len(contexts)} contexts, {len(failures)} failures")

    grobid_counts = Counter(r.paper_id for r in refs)

    print("\nlayout cross-check (independent counter):")
    layout: dict = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for rc in tqdm(pool.map(count_entries, pdfs), total=len(pdfs), desc="layout", unit="pdf"):
            layout[rc.paper_id] = rc

    report = compare(dict(grobid_counts), layout)
    # Papers GROBID could not parse (HTTP 204 / empty) are not silently absent from the
    # audit: pair each with the layout counter's estimate so the report states exactly how
    # many references we know we are missing. A fulltext-endpoint fallback was tested and
    # recovers ~1 of ~37 on these papers -- GROBID's model chokes on the PDF regardless of
    # endpoint (mostly very long papers, but not only), so these are disclosed, not patched.
    for fail in failures:
        rc = layout.get(fail["paper_id"])
        fail["layout_estimate"] = rc.layout_count if rc and rc.ok else None
    report["grobid_failures"] = failures
    report["grobid_failed_papers"] = len(failures)
    report["refs_missing_estimate"] = sum(f.get("layout_estimate") or 0 for f in failures)
    report["layout_failures"] = [
        {"paper_id": rc.paper_id, "note": rc.note} for rc in layout.values() if not rc.ok
    ]
    (run_dir / "parse_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))

    if not report.get("papers"):
        print("nothing to compare")
        return

    print(f"\n  papers compared      {report['papers']}")
    print(f"  refs (grobid/layout) {report['grobid_refs']} / {report['layout_refs']}")
    print(f"  median ratio         {report['median_ratio']:.4f}  (1.0 = the two counters agree)")
    print(f"  agree within +-2     {report['agree_within_2_pct']:.1%}")
    print(f"  layout counter failed on {len(report['layout_failures'])} papers")
    if report["grobid_failed_papers"]:
        print(
            f"  GROBID parse-failed  {report['grobid_failed_papers']} papers "
            f"(~{report['refs_missing_estimate']} refs missing, disclosed not dropped)"
        )

    # The two counters fail in different ways, so neither is ground truth and neither can
    # be the gate. The gate is measured by hand on the papers selected here.
    sample, sample_diag = select_qa_sample(report["rows"])
    report["qa_sample"] = sample_diag
    (run_dir / "parse_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"  {sample_diag['note']}")
    sample_path = run_dir / "qa_sample.csv"
    with sample_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f, fieldnames=["paper_id", "reason", "grobid", "layout", "diff", "true_count", "notes"]
        )
        w.writeheader()
        for row in sample:
            w.writerow(
                {
                    "paper_id": row["paper_id"],
                    "reason": row["reason"],
                    "grobid": row["grobid"],
                    "layout": row["layout"],
                    "diff": row["diff"],
                    "true_count": "",
                    "notes": "",
                }
            )

    print("\nwrote refs.jsonl, contexts.jsonl, parse_report.json")
    print(f"acceptance: fill true_count in {sample_path} ({len(sample)} papers) -> GROBID recall")


def main() -> None:
    parser = argparse.ArgumentParser(prog="tuto")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="fetch venue metadata + PDFs")
    p.add_argument("--venue", required=True, choices=sorted(VENUES))
    p.add_argument("--limit", type=int, help="only take the first N papers (smoke test)")
    p.add_argument("--no-pdf", action="store_true", help="metadata only, skip PDF download")
    p.add_argument("--rate-limit", type=float, default=3.0, help="requests/sec to Anthology")
    p.add_argument("--workers", type=int, default=4, help="concurrent PDF downloads")
    p.add_argument("--refresh-bib", action="store_true", help="re-download the bib dump")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("parse", help="PDF -> refs.jsonl + contexts.jsonl, with recall gate")
    p.add_argument("--venue", required=True, choices=sorted(VENUES))
    p.add_argument("--grobid-url", default="http://localhost:8070")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--limit", type=int, help="only cross-check the first N pdfs")
    p.set_defaults(func=cmd_parse)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

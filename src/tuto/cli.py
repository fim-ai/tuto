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


def cmd_verify(args: argparse.Namespace) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from tqdm import tqdm

    from tuto.models import read_jsonl
    from tuto.verify.classify import Classifier, verdict_to_dict
    from tuto.verify.local_index import DblpIndex

    run_dir = DATA / "runs" / args.venue
    refs = list(read_jsonl(run_dir / "refs.jsonl"))
    print(f"{len(refs)} references")

    dblp = DblpIndex(DATA / "cache" / "dblp" / "dblp.sqlite")

    # Phase 1: DBLP only. Offline, so the whole corpus resolves here in one cheap pass;
    # only the tail it cannot find is escalated, which keeps Cito load proportional to the
    # actual miss rate instead of the corpus size.
    local = Classifier(dblp, cito=None)
    verdicts = [local.classify(r) for r in tqdm(refs, desc="dblp", unit="ref")]

    # Escalate everything DBLP did not positively resolve -- both not_found and the
    # no-title unparseable refs, since Cito rescue keys on the raw string and can recover
    # refs whose parsed title was unusable.
    tail_idx = [i for i, v in enumerate(verdicts) if v.verdict in ("not_found", "unparseable")]
    print(f"\nphase 1 (DBLP): {len(refs) - len(tail_idx)} resolved, {len(tail_idx)} to escalate")

    # Phase 2: escalate the local misses to Cito (private, high-recall), if configured.
    # A batch this size takes hours, so it is checkpointed and fault-tolerant: every Cito
    # result is appended to a cache on disk, a single timed-out query cannot abort the run,
    # and a rerun resumes from the cache instead of repeating work.
    if not args.no_cito and tail_idx:
        import json as _json
        import threading

        try:
            from tuto.verify.cito_backend import CitoBackend

            cache_path = run_dir / "cito_cache.jsonl"
            cache: dict[str, dict] = {}
            if cache_path.exists():
                for row in read_jsonl(cache_path):
                    cache[row["ref_id"]] = row
            print(f"phase 2 (Cito): {len(cache)} cached, resuming")

            cito = CitoBackend(rate_per_sec=args.cito_rps)
            remote = Classifier(dblp, cito=cito)
            ref_by_id = {r["ref_id"]: r for r in refs}
            todo = [i for i in tail_idx if verdicts[i].ref_id not in cache]

            write_lock = threading.Lock()
            cache_file = cache_path.open("a", encoding="utf-8")
            errors = 0

            def recheck(i: int):
                ref_id = verdicts[i].ref_id
                try:
                    v = remote.classify(ref_by_id[ref_id])
                except Exception:  # noqa: BLE001 - one bad query must not sink the batch
                    return i, None  # leave phase-1 verdict; a rerun retries this ref
                row = verdict_to_dict(v)
                with write_lock:
                    cache_file.write(_json.dumps(row, ensure_ascii=False) + "\n")
                    cache_file.flush()
                return i, v

            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                for i, v in tqdm(
                    pool.map(recheck, todo), total=len(todo), desc="cito", unit="ref"
                ):
                    if v is None:
                        errors += 1
                    else:
                        verdicts[i] = v
            cache_file.close()
            cito.close()

            # Fold in everything the cache holds (this run's results + any prior run's).
            id_to_idx = {verdicts[i].ref_id: i for i in tail_idx}
            from tuto.verify.classify import Verdict

            for ref_id, row in cache.items():
                if ref_id in id_to_idx:
                    verdicts[id_to_idx[ref_id]] = Verdict(**row)
            recovered = sum(1 for i in tail_idx if verdicts[i].verdict == "exists")
            print(f"phase 2 (Cito): recovered {recovered} of {len(tail_idx)}, {errors} errors (retry on rerun)")
        except ValueError as e:
            print(f"phase 2 skipped: {e}")

    dblp.close()
    write_jsonl(run_dir / "verdicts.jsonl", [verdict_to_dict(v) for v in verdicts])

    dist = Counter(v.verdict for v in verdicts)
    via = Counter(v.matched_via for v in verdicts if v.verdict == "exists")
    total = len(verdicts)
    print("\nL1 verdicts:")
    for k in ("exists", "minor_mismatch", "not_found", "unparseable"):
        print(f"  {k:<15} {dist.get(k, 0):>7}  {dist.get(k, 0) / total:>6.1%}")
    print("  exists matched via:", dict(via))
    print(f"\nnot_found (suspect candidates for triage): {dist.get('not_found', 0)}")
    print(f"wrote {run_dir}/verdicts.jsonl")


def cmd_refresh_dblp(args: argparse.Namespace) -> None:
    import sys

    from tuto.verify.refresh_dblp import refresh

    # Quiet by default when nobody is watching, so cron logs stay readable.
    quiet = args.quiet or not sys.stderr.isatty()
    r = refresh(CACHE / "dblp", force=args.force, keep_dump=args.keep_dump, quiet=quiet)
    if r.status == "locked":
        raise SystemExit(0)


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

    p = sub.add_parser("verify", help="L1 existence check -> verdicts.jsonl (four-way + evidence)")
    p.add_argument("--venue", required=True, choices=sorted(VENUES))
    p.add_argument("--no-cito", action="store_true", help="DBLP only, skip Cito escalation")
    p.add_argument("--cito-rps", type=float, default=20.0, help="Cito requests/sec cap")
    p.add_argument("--workers", type=int, default=12, help="concurrent Cito lookups")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("refresh-dblp", help="rebuild the DBLP snapshot if it changed upstream")
    p.add_argument("--force", action="store_true", help="rebuild even if the md5 is unchanged")
    p.add_argument("--keep-dump", action="store_true", help="keep dblp.xml.gz (~1GB) after build")
    p.add_argument("--quiet", action="store_true", help="no progress bars (use in cron)")
    p.set_defaults(func=cmd_refresh_dblp)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

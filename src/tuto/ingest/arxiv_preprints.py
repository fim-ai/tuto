"""Ingest a 'never-reviewed' arXiv preprint set as the contrast to the accepted ACL corpus.

The whole point of the contrast is a corpus that did NOT pass peer review, so we sample
cs.CL arXiv papers and keep only those that, well after posting, still carry no journal
reference and no DOI: persistent preprints that were never published anywhere. That is an
imperfect but defensible proxy for "unreviewed" (some are simply not yet published; the age
window mitigates this). Everything downstream -- PDF download, GROBID parse, L1 verify, L2
support -- is exactly the ACL pipeline, so the two corpora are measured by an identical ruler
and the only variable is peer review.

Run: python -m tuto.ingest.arxiv_preprints --venue arxiv-cscl-2024 --from 2024-01-01 --to 2024-03-31 --n 400
"""

from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from lxml import etree

from tuto.ingest.acl_anthology import download_pdfs
from tuto.models import PaperRecord, write_jsonl

API = "https://export.arxiv.org/api/query"
UA = "tuto-citation-audit/0.1 (research; contact: team@tuto.fim.ai)"
ATOM = "{http://www.w3.org/2005/Atom}"
ARX = "{http://arxiv.org/schemas/atom}"

ROOT = Path(__file__).resolve().parents[3]  # src/tuto/ingest/ -> repo root
DATA = ROOT / "data"


def _id_from_url(url: str) -> str:
    # http://arxiv.org/abs/2401.12345v2 -> 2401.12345
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return tail.split("v")[0] if "v" in tail and tail.split("v")[0].replace(".", "").isdigit() else tail


def _is_arxiv_only(p: dict | None) -> bool:
    """True if S2 shows this paper as an arXiv preprint that was never published in a venue.

    arXiv's own journal_ref is self-reported and usually left blank even for papers that were
    later accepted, so it cannot be trusted alone (a paper that actually appeared at AAAI can
    still look unpublished on arXiv). S2/Cito carries the real publication venue, so it is the
    authoritative filter: keep only papers whose venue is arXiv (or absent) and that are not
    marked as a conference/journal publication. Papers absent from S2 entirely are obscure
    enough to count as unpublished.
    """
    if p is None:
        return True
    venue = (p.get("venue") or "").strip().lower()
    ptypes = set(p.get("publication_types") or [])
    if "Conference" in ptypes:
        return False
    return venue in ("", "arxiv.org", "arxiv")


def collect_preprints(
    venue: str, date_from: str, date_to: str, target_n: int, cito=None, page: int = 100
) -> list[PaperRecord]:
    """arXiv cs.CL papers in [date_from, date_to] that were never published in a venue.

    Two-stage filter: a cheap arXiv-side pre-filter (no self-reported journal_ref / DOI), then
    the authoritative S2/Cito venue check that catches papers published somewhere without an
    updated arXiv journal_ref.
    """
    lo = date_from.replace("-", "")
    hi = date_to.replace("-", "")
    query = f"cat:cs.CL AND submittedDate:[{lo}0000 TO {hi}2359]"
    client = httpx.Client(headers={"User-Agent": UA}, timeout=60.0)
    out: list[PaperRecord] = []
    start, seen = 0, 0
    try:
        while len(out) < target_n:
            params = {
                "search_query": query, "start": start, "max_results": page,
                "sortBy": "submittedDate", "sortOrder": "ascending",
            }
            r = client.get(API, params=params)
            r.raise_for_status()
            root = etree.fromstring(r.content)
            entries = root.findall(f"{ATOM}entry")
            if not entries:
                break
            for e in entries:
                seen += 1
                journal = e.findtext(f"{ARX}journal_ref")
                doi = e.findtext(f"{ARX}doi")
                if journal or doi:  # self-reported publication -> not a persistent preprint
                    continue
                aid = _id_from_url(e.findtext(f"{ATOM}id") or "")
                if cito is not None and not _is_arxiv_only(cito.paper_by_id(arxiv=aid)):
                    continue  # S2 says it was published in a real venue -> exclude

                title = " ".join((e.findtext(f"{ATOM}title") or "").split())
                authors = [
                    a.findtext(f"{ATOM}name") or "" for a in e.findall(f"{ATOM}author")
                ]
                pub = e.findtext(f"{ATOM}published") or ""
                year = int(pub[:4]) if pub[:4].isdigit() else 0
                out.append(PaperRecord(
                    paper_id=aid, title=title, authors=[a for a in authors if a],
                    year=year, venue=venue, volume=f"{venue}",
                    pdf_url=f"https://arxiv.org/pdf/{aid}",
                    url=f"https://arxiv.org/abs/{aid}",
                ))
                if len(out) >= target_n:
                    break
            start += page
            time.sleep(3)  # arXiv API asks for a 3s gap between calls
    finally:
        client.close()
    print(f"scanned {seen} cs.CL entries, kept {len(out)} unpublished preprints")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(prog="tuto.ingest.arxiv_preprints")
    ap.add_argument("--venue", required=True, help="run key, e.g. arxiv-cscl-2024")
    ap.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD")
    ap.add_argument("--n", type=int, default=400)
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--rate-limit", type=float, default=2.0)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    run_dir = DATA / "runs" / args.venue
    run_dir.mkdir(parents=True, exist_ok=True)
    from tuto.verify.cito_backend import CitoBackend

    cito = CitoBackend(rate_per_sec=10)
    try:
        papers = collect_preprints(args.venue, args.date_from, args.date_to, args.n, cito=cito)
    finally:
        cito.close()
    write_jsonl(run_dir / "papers.jsonl", papers)

    stats = {}
    if not args.no_pdf and papers:
        stats = download_pdfs(papers, run_dir / "pdfs", rate_limit=args.rate_limit, workers=args.workers)
        print("pdfs:", stats)

    (run_dir / "manifest.json").write_text(
        __import__("json").dumps({
            "venue": args.venue, "source": "arxiv cs.CL unpublished preprints",
            "window": [args.date_from, args.date_to], "paper_count": len(papers),
            "pdf_stats": stats, "ingested_at": datetime.now(UTC).isoformat(),
        }, indent=2), encoding="utf-8"
    )
    print(f"wrote {run_dir}/papers.jsonl ({len(papers)} papers)")


if __name__ == "__main__":
    main()

"""ACL Anthology adapter: full-corpus bib dump + predictable PDF URLs.

The Anthology publishes one bib entry per paper (its own metadata) and no reference
lists -- the bibliography of each paper exists only inside its PDF. That is why parse/
has to read PDFs at all; see docs/ARCHITECTURE.md, "Why we parse PDFs".

The bib dump is ~90MB of machine-generated BibTeX. We split entries with a line scanner
and pull fields with a regex rather than a general BibTeX parser: the format is rigid and
a real parser spends minutes on a file this size.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import httpx
from tqdm import tqdm

from tuto.models import PaperRecord

BIB_URL = "https://aclanthology.org/anthology.bib.gz"
PDF_BASE = "https://aclanthology.org"
CONTACT = "tuto@fim.ai"
USER_AGENT = f"tuto-citation-audit/0.1 (+https://tuto.fim.ai; {CONTACT})"

# One entry per venue we can audit. Anthology ids are stable, so a venue is just a
# set of volume prefixes -- adding EMNLP later is one line here, no new code.
VENUES: dict[str, list[str]] = {
    "acl-2026": ["2026.acl-long", "2026.acl-short", "2026.findings-acl"],
    "acl-2025": ["2025.acl-long", "2025.acl-short", "2025.findings-acl"],
    "emnlp-2025": ["2025.emnlp-main", "2025.findings-emnlp"],
    # Pre-2020 Anthology uses the old letter-code scheme: P18-1xxx = ACL 2018 long,
    # P18-2xxx = ACL 2018 short. (Findings did not exist yet.) Used as the pre-LLM temporal
    # contrast against acl-2026.
    "acl-2018": ["P18-1", "P18-2"],
    # Contrast corpus: never-reviewed arXiv cs.CL preprints. Ingested by
    # tuto.ingest.arxiv_preprints, not from the Anthology bib, so it has no prefixes; the key
    # only needs to exist so parse/verify accept --venue.
    "arxiv-cscl-2024": [],
}

_FIELD_RE = re.compile(r'(\w+)\s*=\s*"((?:[^"\\]|\\.)*)"', re.DOTALL)
_ID_RE = re.compile(r"https://aclanthology\.org/([^/\"]+?)/?$")
# Old-scheme id: letter(s)+2-digit-year, dash, single volume digit, then the paper number.
_OLD_ID_RE = re.compile(r"^([A-Z]\d{2})-(\d)(\d+)$")
_WS_RE = re.compile(r"\s+")


def _volume_of(paper_id: str) -> str:
    """Group key for a paper. Handles both id schemes: 2026.acl-long.1 and P18-1001."""
    m = _OLD_ID_RE.match(paper_id)
    if m:
        return f"{m.group(1)}-{m.group(2)}"  # P18-1001 -> P18-1
    return paper_id.rsplit(".", 1)[0]  # 2026.acl-long.1 -> 2026.acl-long


def _is_frontmatter(paper_id: str) -> bool:
    """The per-volume front matter is not a paper: modern '.0', old '-N000'."""
    if paper_id.endswith(".0"):
        return True
    m = _OLD_ID_RE.match(paper_id)
    return bool(m and set(m.group(3)) == {"0"})  # P18-1000


def _debrace(s: str) -> str:
    """BibTeX brace-protects casing ({O}cto{T}ools). Strip braces, keep content."""
    return _WS_RE.sub(" ", s.replace("{", "").replace("}", "")).strip()


def _split_authors(raw: str) -> list[str]:
    parts = re.split(r"\s+and\s+", _debrace(raw))
    out = []
    for p in parts:
        p = p.strip().rstrip(",")
        if not p:
            continue
        if "," in p:  # "Lu, Pan" -> "Pan Lu"
            last, _, first = p.partition(",")
            p = f"{first.strip()} {last.strip()}".strip()
        out.append(p)
    return out


def iter_bib_entries(bib_path: Path):
    """Yield raw entry text. Entries start with @ at column 0 and end with } at column 0."""
    buf: list[str] = []
    with bib_path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("@"):
                if buf:
                    yield "".join(buf)
                buf = [line]
            elif buf:
                buf.append(line)
                if line.startswith("}"):
                    yield "".join(buf)
                    buf = []
    if buf:
        yield "".join(buf)


def parse_entry(entry: str, venue: str, prefixes: list[str]) -> PaperRecord | None:
    fields = {k.lower(): v for k, v in _FIELD_RE.findall(entry)}
    url = fields.get("url", "")
    m = _ID_RE.match(url.strip())
    if not m:
        return None
    paper_id = m.group(1)

    volume = _volume_of(paper_id)
    # Volume prefixes carry a trailing part number in some years (2026.findings-acl-1),
    # so match on prefix rather than equality.
    if not any(volume.startswith(p) for p in prefixes):
        return None
    if _is_frontmatter(paper_id):
        return None

    bibkey_m = re.match(r"@\w+\{([^,]+),", entry)
    year_raw = fields.get("year", "")
    try:
        year = int(year_raw)
    except ValueError:
        return None

    return PaperRecord(
        paper_id=paper_id,
        title=_debrace(fields.get("title", "")),
        authors=_split_authors(fields.get("author", "")),
        year=year,
        venue=venue,
        volume=volume,
        pdf_url=f"{PDF_BASE}/{paper_id}.pdf",
        doi=fields.get("doi"),
        url=url,
        pages=fields.get("pages"),
        bibkey=bibkey_m.group(1) if bibkey_m else None,
    )


@dataclass
class BibSnapshot:
    path: Path
    url: str
    last_modified: str | None
    sha256: str
    bytes: int


def fetch_bib(cache_dir: Path, force: bool = False) -> BibSnapshot:
    """Download and decompress the bib dump, caching by Last-Modified."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    gz_path = cache_dir / "anthology.bib.gz"
    bib_path = cache_dir / "anthology.bib"
    meta_path = cache_dir / "anthology.meta.json"

    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(headers=headers, follow_redirects=True, timeout=120) as client:
        head = client.head(BIB_URL)
        head.raise_for_status()
        remote_lm = head.headers.get("last-modified")

        cached = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        if not force and bib_path.exists() and cached.get("last_modified") == remote_lm:
            return BibSnapshot(bib_path, BIB_URL, remote_lm, cached["sha256"], cached["bytes"])

        with client.stream("GET", BIB_URL) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            with gz_path.open("wb") as f, tqdm(
                total=total, unit="B", unit_scale=True, desc="anthology.bib.gz"
            ) as bar:
                for chunk in r.iter_bytes(chunk_size=65536):
                    f.write(chunk)
                    bar.update(len(chunk))

    with gzip.open(gz_path, "rb") as fin, bib_path.open("wb") as fout:
        fout.write(fin.read())

    digest = hashlib.sha256(bib_path.read_bytes()).hexdigest()
    size = bib_path.stat().st_size
    meta_path.write_text(
        json.dumps({"last_modified": remote_lm, "sha256": digest, "bytes": size}, indent=2)
    )
    return BibSnapshot(bib_path, BIB_URL, remote_lm, digest, size)


def collect_papers(bib_path: Path, venue: str) -> list[PaperRecord]:
    prefixes = VENUES[venue]
    papers = [
        p
        for entry in iter_bib_entries(bib_path)
        if (p := parse_entry(entry, venue, prefixes)) is not None
    ]
    papers.sort(key=lambda p: (p.volume, int(p.paper_id.rsplit(".", 1)[1])))
    return papers


class _RateLimiter:
    """Token bucket shared across workers, capping how fast we *start* requests."""

    def __init__(self, rate: float):
        self.min_interval = 1.0 / rate
        self.lock = threading.Lock()
        self.next_slot = 0.0

    def acquire(self) -> None:
        with self.lock:
            now = time.monotonic()
            wait = max(0.0, self.next_slot - now)
            self.next_slot = max(now, self.next_slot) + self.min_interval
        if wait:
            time.sleep(wait)


def download_pdfs(
    papers: list[PaperRecord],
    pdf_dir: Path,
    rate_limit: float = 3.0,
    workers: int = 4,
    max_retries: int = 3,
) -> dict[str, int]:
    """Resumable PDF fetch.

    Anthology serves ~300KB/s per connection and papers are ~1MB, so a single request
    takes 3-5s and a sequential loop needs a full day. Requests run concurrently, with
    a shared rate cap so we still start no more than `rate_limit` per second.
    """
    pdf_dir.mkdir(parents=True, exist_ok=True)
    todo = [p for p in papers if not _is_valid_pdf(pdf_dir / p.pdf_name)]
    stats = {"downloaded": 0, "cached": len(papers) - len(todo), "failed": 0}
    failures: list[dict[str, str]] = []
    limiter = _RateLimiter(rate_limit)
    lock = threading.Lock()

    headers = {"User-Agent": USER_AGENT}
    limits = httpx.Limits(max_connections=workers, max_keepalive_connections=workers)
    client = httpx.Client(headers=headers, follow_redirects=True, timeout=90, limits=limits)

    def fetch(paper: PaperRecord) -> None:
        dest = pdf_dir / paper.pdf_name
        for attempt in range(max_retries):
            limiter.acquire()
            try:
                r = client.get(paper.pdf_url)
                r.raise_for_status()
                if not r.content.startswith(b"%PDF"):
                    raise ValueError("response is not a PDF")
                tmp = dest.with_suffix(".pdf.part")
                tmp.write_bytes(r.content)
                tmp.rename(dest)  # atomic: a rerun never sees a half-written file
                with lock:
                    stats["downloaded"] += 1
                return
            except Exception as e:  # noqa: BLE001 - record and move on; rerun resumes
                if attempt == max_retries - 1:
                    with lock:
                        stats["failed"] += 1
                        failures.append({"paper_id": paper.paper_id, "error": str(e)})
                else:
                    time.sleep(2**attempt)

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(fetch, p) for p in todo]
            for _ in tqdm(as_completed(futures), total=len(todo), desc="pdfs", unit="pdf"):
                pass
    finally:
        client.close()

    if failures:
        (pdf_dir.parent / "download_failures.json").write_text(json.dumps(failures, indent=2))
    return stats


def _is_valid_pdf(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 10_000:
        return False
    with path.open("rb") as f:
        return f.read(4) == b"%PDF"

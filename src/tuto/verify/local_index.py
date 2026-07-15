"""Local DBLP index: the offline, reproducible substrate for L1 existence checks.

DBLP is near-complete for the CS/ML venues ACL papers cite, it is a free bulk download,
and querying it locally means an existence check costs a SQLite lookup instead of a
rate-limited API call. This is the PUBLIC pipeline's ground truth; Cito and the web APIs
extend recall on top of it but are never required to reproduce a result.

The dump is one big XML with HTML-named entities (&auml; etc.) that only resolve against
DBLP's DTD, so we parse with lxml + the DTD and stream with iterparse to keep memory flat.
The dump is trusted (fetched from dblp.org over TLS), so DTD/entity resolution is fine here
in a way it would not be for user-supplied XML.
"""

from __future__ import annotations

import gzip
import sqlite3
import threading
from pathlib import Path

import httpx
from lxml import etree
from tqdm import tqdm

from tuto.verify.normalize import last_name, norm_doi, norm_title

DTD_URL = "https://dblp.org/xml/dblp.dtd"
PUB_TYPES = {
    "article",
    "inproceedings",
    "incollection",
    "proceedings",
    "book",
    "phdthesis",
    "mastersthesis",
}


def fetch_dtd(dtd_path: Path) -> None:
    if dtd_path.exists() and dtd_path.stat().st_size > 1000:
        return
    with httpx.Client(follow_redirects=True, timeout=60) as c:
        r = c.get(DTD_URL)
        r.raise_for_status()
        dtd_path.write_bytes(r.content)


def _doi_from_ees(elem: etree._Element) -> str | None:
    for ee in elem.findall("ee"):
        d = norm_doi(ee.text or "")
        if d:
            return d
    return None


def build_index(bib_gz: Path, db_path: Path, dtd_path: Path) -> dict:
    """Parse the DBLP dump into SQLite. Idempotent: rebuilds the table from scratch."""
    fetch_dtd(dtd_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute(
        """CREATE TABLE dblp(
            norm_title TEXT NOT NULL,
            year INTEGER,
            last_names TEXT,
            doi TEXT,
            dblp_key TEXT
        )"""
    )

    # lxml resolves the DBLP entities from the DTD sitting next to the (decompressed) stream.
    parser_kw = dict(load_dtd=True, resolve_entities=True, no_network=True, huge_tree=True)
    stats = {"records": 0, "with_doi": 0, "skipped_no_title": 0}
    batch: list[tuple] = []

    def flush() -> None:
        if batch:
            conn.executemany("INSERT INTO dblp VALUES (?,?,?,?,?)", batch)
            batch.clear()

    with gzip.open(bib_gz, "rb") as raw:
        # iterparse needs the DTD reachable; lxml looks it up relative to the input's base.
        context = etree.iterparse(
            raw, events=("end",), tag=tuple(PUB_TYPES), dtd_validation=False, **parser_kw
        )
        bar = tqdm(desc="dblp", unit="rec", unit_scale=True)
        for _, elem in context:
            title = elem.findtext("title")
            nt = norm_title(title)
            if not nt:
                stats["skipped_no_title"] += 1
            else:
                year = elem.findtext("year")
                names = "|".join(
                    ln for a in elem.findall("author") if (ln := last_name(a.text or ""))
                )
                doi = _doi_from_ees(elem)
                if doi:
                    stats["with_doi"] += 1
                batch.append(
                    (nt, int(year) if year and year.isdigit() else None, names, doi, elem.get("key"))
                )
                stats["records"] += 1
                if len(batch) >= 50_000:
                    flush()
                bar.update(1)
            # Free the parsed subtree and its now-processed siblings: DBLP is flat, so this
            # keeps resident memory at one record instead of ten million.
            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]
        bar.close()

    flush()
    conn.execute("CREATE INDEX idx_title ON dblp(norm_title)")
    conn.execute("CREATE INDEX idx_doi ON dblp(doi) WHERE doi IS NOT NULL")
    conn.commit()
    conn.close()
    return stats


class DblpIndex:
    """Read-only lookups over the built SQLite index.

    Phase 2 fans lookups across a thread pool. A single SQLite connection is not safe for
    concurrent execute() even in read-only mode, so each thread gets its own connection to
    the unchanging file via thread-local storage.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._local = threading.local()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro", uri=True, check_same_thread=False
            )
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def by_doi(self, doi: str) -> list[sqlite3.Row]:
        d = norm_doi(doi)
        if not d:
            return []
        return self._conn().execute("SELECT * FROM dblp WHERE doi=?", (d,)).fetchall()

    def by_title(self, title: str) -> list[sqlite3.Row]:
        nt = norm_title(title)
        if not nt:
            return []
        return self._conn().execute("SELECT * FROM dblp WHERE norm_title=?", (nt,)).fetchall()

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()

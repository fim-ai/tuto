"""Keep the local DBLP snapshot current, unattended.

The snapshot is the substrate for every L1 existence check, so a stale one shows up as
false "could not find" leads on recent papers: exactly the references a reviewer most
wants checked. dblp.org rebuilds dblp.xml.gz nightly and publishes its md5 next to it,
so we can ask "did anything change" for 46 bytes instead of re-downloading a gigabyte.

Two properties make this safe to run from cron against a live service:

- The swap is atomic. We build into a temp file and os.replace() it onto the real path,
  which is a rename within one filesystem. A reader either opens the old inode or the new
  one, never a half-written database. DblpIndex opens a fresh connection per request and
  closes it, so an in-flight check keeps its old inode until it finishes.
- Only one build runs at a time. A monthly cron plus a manual run is enough to overlap
  two multi-gigabyte parses, so a second invocation takes a flock and exits instead.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from tqdm import tqdm

from tuto.verify.local_index import build_index

DUMP_URL = "https://dblp.org/xml/dblp.xml.gz"
MD5_URL = "https://dblp.org/xml/dblp.xml.gz.md5"
USER_AGENT = "tuto-citation-audit (+https://tuto.fim.ai)"


@dataclass
class RefreshResult:
    status: str  # "rebuilt" | "unchanged" | "locked"
    md5: str | None = None
    records: int = 0
    with_doi: int = 0
    seconds: float = 0.0
    db_bytes: int = 0


def _log(msg: str) -> None:
    # Timestamped and flushed: cron redirects this to a file that a human reads months later.
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def fetch_remote_md5(client: httpx.Client) -> str:
    """The published md5 of the current dump. Format: '<hex>  dblp.xml.gz'."""
    r = client.get(MD5_URL)
    r.raise_for_status()
    md5 = r.text.strip().split()[0]
    if len(md5) != 32:
        raise RuntimeError(f"unexpected md5 payload from {MD5_URL}: {r.text[:80]!r}")
    return md5


def download_dump(client: httpx.Client, gz_path: Path, expect_md5: str, quiet: bool) -> None:
    """Stream the dump to disk and verify it against the published md5."""
    h = hashlib.md5()  # noqa: S324 - integrity against truncation, not a security boundary
    tmp = gz_path.with_suffix(gz_path.suffix + ".part")
    with client.stream("GET", DUMP_URL) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        with tmp.open("wb") as f, tqdm(
            total=total, unit="B", unit_scale=True, desc="dblp.xml.gz", disable=quiet
        ) as bar:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                h.update(chunk)
                bar.update(len(chunk))
    got = h.hexdigest()
    if got != expect_md5:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"md5 mismatch: got {got}, expected {expect_md5} (truncated download?)")
    tmp.replace(gz_path)


def refresh(
    cache_dir: Path,
    force: bool = False,
    keep_dump: bool = False,
    quiet: bool = False,
    min_records: int = 1_000_000,
) -> RefreshResult:
    """Rebuild the DBLP snapshot if upstream changed. Safe to run against a live service."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    db_path = cache_dir / "dblp.sqlite"
    gz_path = cache_dir / "dblp.xml.gz"
    # The DTD must sit next to the dump: lxml resolves DBLP's named entities relative to
    # the input stream's base path.
    dtd_path = cache_dir / "dblp.dtd"
    meta_path = cache_dir / "dblp.meta.json"
    lock_path = cache_dir / "dblp.refresh.lock"

    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _log("another refresh holds the lock; exiting")
            return RefreshResult(status="locked")

        started = time.monotonic()
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}

        headers = {"User-Agent": USER_AGENT}
        with httpx.Client(headers=headers, follow_redirects=True, timeout=120) as client:
            remote_md5 = fetch_remote_md5(client)
            _log(f"upstream md5={remote_md5} local={meta.get('md5') or 'none'}")

            # The md5 alone is not enough: the build can die after the download, so we only
            # skip when a database actually exists for that md5.
            if not force and meta.get("md5") == remote_md5 and db_path.exists():
                _log("snapshot already current; nothing to do")
                return RefreshResult(
                    status="unchanged", md5=remote_md5, db_bytes=db_path.stat().st_size
                )

            _log(f"downloading {DUMP_URL}")
            download_dump(client, gz_path, remote_md5, quiet)
            _log(f"downloaded {gz_path.stat().st_size / 1e9:.2f}GB, md5 verified")

        # Build beside the live database so os.replace() stays on one filesystem.
        tmp_db = db_path.with_name("dblp.sqlite.new")
        tmp_db.unlink(missing_ok=True)
        _log("parsing dump into a new index (this takes a while)")
        stats = build_index(gz_path, tmp_db, dtd_path, quiet=quiet)
        if stats["records"] < min_records:
            # A dump that parses to almost nothing means the format moved under us. Keep the
            # working snapshot rather than swapping in a broken one.
            tmp_db.unlink(missing_ok=True)
            raise RuntimeError(
                f"refusing to swap: only {stats['records']:,} records parsed "
                f"(expected >= {min_records:,})"
            )

        os.replace(tmp_db, db_path)
        meta_path.write_text(
            json.dumps(
                {
                    "md5": remote_md5,
                    "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "records": stats["records"],
                    "with_doi": stats["with_doi"],
                },
                indent=2,
            )
        )
        if not keep_dump:
            gz_path.unlink(missing_ok=True)

        elapsed = time.monotonic() - started
        size = db_path.stat().st_size
        _log(
            f"swapped in {stats['records']:,} records "
            f"({stats['with_doi']:,} with doi, {stats['skipped_no_title']:,} skipped) "
            f"in {elapsed / 60:.1f}min, db={size / 1e9:.2f}GB"
        )
        return RefreshResult(
            status="rebuilt",
            md5=remote_md5,
            records=stats["records"],
            with_doi=stats["with_doi"],
            seconds=elapsed,
            db_bytes=size,
        )
    finally:
        os.close(lock_fd)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="tuto refresh-dblp",
        description="Rebuild the local DBLP snapshot from dblp.org if it changed upstream.",
    )
    ap.add_argument("--cache-dir", type=Path, required=True, help="dir holding dblp.sqlite")
    ap.add_argument("--force", action="store_true", help="rebuild even if the md5 is unchanged")
    ap.add_argument("--keep-dump", action="store_true", help="keep dblp.xml.gz (~1GB) after build")
    ap.add_argument("--quiet", action="store_true", help="no progress bars (use in cron)")
    args = ap.parse_args(argv)

    # Default to quiet when nobody is watching, so cron logs stay readable.
    quiet = args.quiet or not sys.stderr.isatty()
    try:
        r = refresh(args.cache_dir, force=args.force, keep_dump=args.keep_dump, quiet=quiet)
    except Exception as e:  # noqa: BLE001 - cron wants a message and a exit code, not a traceback
        _log(f"FAILED: {e}")
        return 1
    return 0 if r.status in ("rebuilt", "unchanged", "locked") else 1


if __name__ == "__main__":
    raise SystemExit(main())

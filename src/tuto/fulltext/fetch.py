"""Fetch a cited work's full text, preferring arXiv LaTeX source over any PDF.

The full-text L2 check needs the cited paper's body, not just its abstract. For this corpus
that is overwhelmingly cheap: ~81% of cited works are on arXiv, whose e-print service serves
the original LaTeX source. Source beats PDF on every axis here -- no OCR, no layout guessing,
clean section text -- so we take it whenever an arXiv id exists and fall back to a PDF path
only for the OA-but-not-arXiv minority.

arXiv asks automated callers to be gentle and to identify themselves; we cache every fetch to
disk so a rerun never re-downloads, and keep concurrency low. Bulk (all 200k) would use the
S3/S2ORC dumps instead, but the report needs a validated SAMPLE, not the whole corpus, so
polite per-paper fetching is the right tool at this scale.
"""

from __future__ import annotations

import gzip
import io
import re
import tarfile
from pathlib import Path

import httpx
from pylatexenc.latex2text import LatexNodes2Text

UA = "tuto-citation-audit/0.1 (research; contact: team@tuto.fim.ai)"
EPRINT = "https://export.arxiv.org/e-print/{}"

# Everything from the bibliography onward is references, not prose we want to check against.
_BIB_RE = re.compile(r"\\begin\{thebibliography\}.*?\\end\{thebibliography\}", re.DOTALL)
_BIB2_RE = re.compile(r"\\bibliography\{[^}]*\}")
_COMMENT_RE = re.compile(r"(?<!\\)%.*")


def make_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, timeout=60.0)


def _concat_tex(data: bytes) -> str | None:
    """Pull all .tex out of an e-print payload (tar.gz, or a single gzipped .tex)."""
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data))
    except tarfile.TarError:
        try:
            raw = gzip.decompress(data).decode("utf-8", "ignore")
            return raw if "\\" in raw else None
        except OSError:
            return None
    parts = []
    for m in tf.getmembers():
        if m.isfile() and m.name.lower().endswith(".tex"):
            f = tf.extractfile(m)
            if f:
                parts.append(f.read().decode("utf-8", "ignore"))
    return "\n".join(parts) if parts else None


def fetch_arxiv_source(arxiv_id: str, client: httpx.Client, cache_dir: Path) -> str | None:
    """Return concatenated LaTeX source for an arXiv id, cached. None if no source is served."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{arxiv_id.replace('/', '_')}.tex"
    if cached.exists():
        t = cached.read_text(encoding="utf-8", errors="ignore")
        return t or None
    try:
        r = client.get(EPRINT.format(arxiv_id))
    except httpx.HTTPError:
        return None
    if r.status_code != 200 or not r.content:
        cached.write_text("", encoding="utf-8")  # negative-cache: some ids are PDF-only
        return None
    tex = _concat_tex(r.content)
    cached.write_text(tex or "", encoding="utf-8")
    return tex


def tex_to_text(tex: str) -> str:
    """LaTeX source -> readable prose, references and comments removed."""
    tex = _COMMENT_RE.sub("", tex)
    tex = _BIB_RE.sub("", tex)
    tex = _BIB2_RE.sub("", tex)
    try:
        text = LatexNodes2Text(math_mode="text", strict_latex_spaces=False).latex_to_text(tex)
    except Exception:  # noqa: BLE001 - malformed TeX shouldn't kill the batch
        text = tex
    # collapse the blank-line explosion de-TeX tends to produce
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

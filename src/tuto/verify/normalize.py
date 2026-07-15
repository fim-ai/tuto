"""Normalization shared by every verification backend.

A citation and the record it refers to are written by different people years apart, so
matching has to survive punctuation, casing, accents, LaTeX escapes, and word order in
author names. These helpers define the ONE canonical form every backend keys on -- if the
index and the query normalize differently, nothing matches.
"""

from __future__ import annotations

import re
import unicodedata

_WS_RE = re.compile(r"\s+")
_NONALNUM_RE = re.compile(r"[^a-z0-9 ]+")
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+")
_ARXIV_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_title(title: str | None) -> str:
    """Canonical title key: accent-folded, lowercased, alphanumeric-and-space only.

    'Attention Is All You Need!' and 'Attention is all you need' collapse to the same key.
    Aggressive on purpose -- recall matters more than precision here, because a wrong
    collision is caught downstream by the author/year check, while a missed match would
    turn a real reference into a false suspect.
    """
    if not title:
        return ""
    t = _strip_accents(title)
    t = t.replace("{", "").replace("}", "").replace("\\", "")
    t = t.lower()
    t = _NONALNUM_RE.sub(" ", t)
    return _WS_RE.sub(" ", t).strip()


def title_tokens(title: str | None) -> frozenset[str]:
    return frozenset(norm_title(title).split())


def norm_doi(doi: str | None) -> str | None:
    """Bare DOI, lowercased, with any http(s)://doi.org/ prefix stripped."""
    if not doi:
        return None
    m = _DOI_RE.search(doi.lower())
    if not m:
        return None
    return m.group(0).rstrip(".").rstrip(")")


def norm_arxiv(arxiv_id: str | None) -> str | None:
    """arXiv id without version suffix (2304.05376v2 -> 2304.05376)."""
    if not arxiv_id:
        return None
    m = _ARXIV_RE.search(arxiv_id)
    return m.group(1) if m else None


def last_name(author: str) -> str:
    """Best-effort surname key from a name string in either order.

    GROBID emits both 'Pan Lu' and 'Lu, Pan'. We take the last whitespace token of the
    comma-free form, accent-folded and lowercased -- enough to corroborate a title match
    without a full name parser.
    """
    a = _strip_accents(author).strip()
    if "," in a:
        a = a.split(",")[0]
    else:
        parts = a.split()
        a = parts[-1] if parts else a
    return _NONALNUM_RE.sub("", a.lower())


def author_keys(authors: list[str]) -> frozenset[str]:
    return frozenset(k for a in authors if (k := last_name(a)))

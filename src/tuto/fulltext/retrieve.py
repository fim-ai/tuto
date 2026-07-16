"""Retrieve the passages of a cited paper that bear on the citing sentence's claim.

Feeding a whole paper to the judge is wasteful and dilutes the signal, so we chunk the full
text and pull only the few windows most relevant to the claim. BM25 (lexical) is enough here
and needs no GPU or model: the claim and the supporting passage share domain vocabulary, and
lexical overlap is a strong, cheap locator. If a claim is heavily paraphrased and BM25 misses,
the judge still sees the abstract, so the failure mode is "unverifiable", never a false
"not_supported".
"""

from __future__ import annotations

import re

from rank_bm25 import BM25Okapi

_TOK_RE = re.compile(r"[a-z0-9]+")


def _tok(s: str) -> list[str]:
    return _TOK_RE.findall(s.lower())


def chunk(text: str, size: int = 130, overlap: int = 40) -> list[str]:
    words = text.split()
    if not words:
        return []
    out, i = [], 0
    step = max(size - overlap, 1)
    while i < len(words):
        out.append(" ".join(words[i : i + size]))
        i += step
    return out


def top_passages(text: str, claim: str, k: int = 4) -> list[str]:
    """The k chunks of `text` most lexically relevant to `claim`, in reading order."""
    chunks = chunk(text)
    if not chunks:
        return []
    q = _tok(claim)
    if not q:
        return chunks[:k]
    bm = BM25Okapi([_tok(c) for c in chunks])
    scores = bm.get_scores(q)
    ranked = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)[:k]
    return [chunks[i] for i in sorted(ranked)]

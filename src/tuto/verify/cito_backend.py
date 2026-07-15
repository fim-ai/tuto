"""Cito search backend: high-recall existence check over ~236M S2 papers.

Cito (scholar-engine, api.cito.fim.ai) is our own hybrid search engine. It extends L1
recall far past DBLP -- workshop papers, preprints, non-CS venues -- but it is PRIVATE
infrastructure, so it is only ever a recall booster: a result must be reproducible from
the public sources alone (see docs/ARCHITECTURE.md). Used here to resolve the tail that
DBLP misses without hammering rate-limited public APIs.

A search engine answers "what is relevant", not "does this exact title exist", so a hit
only counts as existence when the top result's normalized title actually matches the
query. A fabricated title returns loosely-related papers with different titles, which we
must not accept.
"""

from __future__ import annotations

import os
import threading
import time

import httpx

from tuto.verify.normalize import author_keys, norm_title


class CitoBackend:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        rate_per_sec: float = 20.0,
        timeout: float = 45.0,
    ):
        self.base_url = (base_url or os.environ.get("CITO_API_BASE", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("CITO_API_KEY", "")
        if not self.base_url or not self.api_key:
            raise ValueError("CITO_API_BASE / CITO_API_KEY not set (see tuto/.env)")
        self.client = httpx.Client(
            headers={"Authorization": f"Bearer {self.api_key}"}, timeout=timeout
        )
        self._min_interval = 1.0 / rate_per_sec
        self._lock = threading.Lock()
        self._next = 0.0

    def _throttle(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next - now)
            self._next = max(now, self._next) + self._min_interval
        if wait:
            time.sleep(wait)

    def paper_by_id(self, doi: str | None = None, arxiv: str | None = None) -> dict | None:
        """Definitive existence check by DOI or arXiv id (Cito /paper reverse lookup).

        An id lookup is an indexed hit against the corpus's external ids -- no BM25, no
        fuzzy matching -- so it is both far cheaper than a title search and unambiguous: a
        200 is proof the cited work exists, a 404 is proof this exact id is not in a 236M
        corpus. Used before any title matching for the refs that carry an id.
        """
        params = {}
        if doi:
            params["doi"] = doi
        if arxiv:
            params["arxiv"] = arxiv
        if not params:
            return None
        self._throttle()
        for attempt in range(3):
            try:
                r = self.client.get(f"{self.base_url}/paper", params=params)
                if r.status_code == 404:
                    return None
                if r.status_code == 429:
                    time.sleep(2**attempt)
                    continue
                r.raise_for_status()
                return r.json()
            except httpx.HTTPError:
                if attempt == 2:
                    raise
                time.sleep(2**attempt)
        return None

    def search(self, query: str, limit: int = 8, mode: str = "keyword") -> list[dict]:
        # keyword (BM25) mode: an existence check needs the exact-title paper in the
        # candidate set, which BM25 does directly. It skips the SPECTER2 query encoder and
        # Qdrant, roughly halving latency versus hybrid on a 236M-doc index, with no recall
        # loss for exact-title retrieval.
        self._throttle()
        for attempt in range(3):
            try:
                r = self.client.get(
                    f"{self.base_url}/search", params={"q": query, "limit": limit, "mode": mode}
                )
                if r.status_code == 429:  # rate limited: back off and retry
                    time.sleep(2**attempt)
                    continue
                r.raise_for_status()
                data = r.json()
                return data.get("results") or data.get("hits") or []
            except httpx.HTTPError:
                if attempt == 2:
                    raise
                time.sleep(2**attempt)
        return []

    def rescue(self, raw: str, authors: list[str]) -> dict | None:
        """Recover a no-id reference GROBID's parsed title could not match, via the raw string.

        The parsed `title` is often corrupted -- a leaked year label ("2025a."), an author
        list mis-segmented as the title, a trailing venue -- so we both QUERY and ACCEPT on
        the raw reference string, which always contains the true title verbatim. The query
        retrieves candidates; a hit counts only when its normalized title is a SUBSTRING of
        the normalized raw reference. That test is precise (a fabricated title appears in no
        real record; a real title always appears inside its own citation) and survives every
        parse-noise failure mode. Querying with the raw string (not the parsed title) is
        what makes the author-list-as-title cases recoverable -- a title-keyed query there
        searches on author names and misses the paper. Refs that carry an id never reach
        here; they are resolved by the definitive id lookup first.
        """
        nraw = norm_title(raw)
        if not nraw:
            return None
        hits = self.search(raw, limit=8)
        q_authors = author_keys(authors)
        for hit in hits:
            ht = norm_title(hit.get("title") or "")
            if not ht or len(ht) < 16:
                # very short titles collide by chance; require an author to corroborate
                if ht and ht in nraw and q_authors and author_keys(_hit_authors(hit)) & q_authors:
                    return hit
                continue
            if ht in nraw:
                return hit
        return None

    def close(self) -> None:
        self.client.close()


def _hit_authors(hit: dict) -> list[str]:
    authors = hit.get("authors") or []
    out = []
    for a in authors:
        if isinstance(a, str):
            out.append(a)
        elif isinstance(a, dict):
            out.append(a.get("name") or "")
    return out

"""Four-way L1 classification with an evidence chain.

Each reference is resolved against the existence backends in cheap-to-expensive order,
stopping at the first strong match. The output is deliberately cautious: the terminal
"could not find it" bucket is `not_found`, a *candidate* for the suspect list, never a
verdict of fabrication. Nothing here calls a citation fake -- that word is earned only
after the triage funnel and the author appeal window.

Verdicts
- exists          a real record matched (DOI, or title corroborated by author/year)
- minor_mismatch  the work exists but the citation's metadata is off (e.g. wrong year)
- not_found       no match anywhere consulted -> candidate suspect (goes to triage)
- unparseable     no title and no id to match on -> a parse gap, NOT a hallucination signal

Every verdict carries the evidence that produced it, because that chain is what an author
sees in the appeal email and what the methodology must be able to defend.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from tuto.verify.normalize import author_keys, norm_arxiv, norm_doi, norm_title

YEAR_TOLERANCE = 1  # arXiv-vs-published and preprint drift; not a mismatch


@dataclass
class Verdict:
    ref_id: str
    paper_id: str
    verdict: str
    matched_via: str | None  # "dblp:doi" | "dblp:title" | "cito" | None
    evidence: dict[str, Any] = field(default_factory=dict)


def _year_ok(ref_year: int | None, rec_year: int | None) -> bool | None:
    if ref_year is None or rec_year is None:
        return None  # unknown, not a conflict
    return abs(ref_year - rec_year) <= YEAR_TOLERANCE


def _classify_title_rows(ref: dict, rows: list[dict]) -> Verdict | None:
    """Decide a verdict from DBLP rows that share the reference's normalized title."""
    ref_authors = author_keys(ref.get("authors") or [])
    ref_year = ref.get("year")

    best_minor: Verdict | None = None
    for row in rows:
        rec_authors = set((row["last_names"] or "").split("|")) - {""}
        rec_year = row["year"]
        author_overlap = bool(ref_authors & rec_authors) if ref_authors and rec_authors else None
        year_ok = _year_ok(ref_year, rec_year)

        evidence = {
            "dblp_key": row["dblp_key"],
            "matched_title": True,
            "ref_year": ref_year,
            "record_year": rec_year,
            "author_overlap": author_overlap,
        }
        # Exact title plus any corroboration (shared author, or agreeing year) is a real
        # match. Title-only with no corroboration is left to a broader backend rather than
        # trusted, because titles do collide.
        if author_overlap or year_ok:
            return Verdict(ref["ref_id"], ref["paper_id"], "exists", "dblp:title", evidence)
        if year_ok is False and (author_overlap or author_overlap is None):
            # same title, wrong year -> the work exists, the citation's year is off
            best_minor = Verdict(
                ref["ref_id"], ref["paper_id"], "minor_mismatch", "dblp:title", evidence
            )
    return best_minor


class Classifier:
    def __init__(self, dblp, cito=None):
        self.dblp = dblp
        self.cito = cito

    def _cito_rescue(self, ref: dict) -> Verdict | None:
        if self.cito is None:
            return None
        rid, pid = ref["ref_id"], ref["paper_id"]

        # id lookup first: definitive and cheap (indexed, no BM25). Resolves the ~36% of
        # the miss tail that carries a DOI or arXiv id, and with higher precision than any
        # title match.
        doi = norm_doi(ref.get("doi"))
        if doi and (p := self.cito.paper_by_id(doi=doi)):
            return Verdict(rid, pid, "exists", "cito:doi", {"doi": doi, "cito_title": p.get("title")})
        arxiv = norm_arxiv(ref.get("arxiv_id"))
        if arxiv and (p := self.cito.paper_by_id(arxiv=arxiv)):
            return Verdict(rid, pid, "exists", "cito:arxiv", {"arxiv": arxiv, "cito_title": p.get("title")})

        # title/raw search rescue for refs with no id.
        hit = self.cito.rescue(ref.get("raw") or "", ref.get("authors") or [])
        if hit is None:
            return None
        return Verdict(
            rid, pid, "exists", "cito",
            {"cito_title": hit.get("title"), "corpus_id": hit.get("corpus_id") or hit.get("id")},
        )

    def classify(self, ref: dict) -> Verdict:
        rid, pid = ref["ref_id"], ref["paper_id"]

        # 1. DOI is authoritative when present.
        doi = norm_doi(ref.get("doi"))
        if doi:
            rows = self.dblp.by_doi(doi)
            if rows:
                return Verdict(rid, pid, "exists", "dblp:doi", {"dblp_key": rows[0]["dblp_key"], "doi": doi})

        title = ref.get("title")
        has_id = bool(doi or norm_arxiv(ref.get("arxiv_id")))
        if not norm_title(title):
            # No usable title. The raw string may still resolve via Cito (a mis-parsed
            # title does not mean a mis-printed reference), so try that before giving up.
            if (v := self._cito_rescue(ref)) is not None:
                return v
            verdict = "not_found" if has_id else "unparseable"
            return Verdict(rid, pid, verdict, None, {"reason": "no usable title"})

        # 2. Local DBLP title match with author/year corroboration.
        rows = self.dblp.by_title(title)
        if rows:
            v = _classify_title_rows(ref, [dict(r) for r in rows])
            if v is not None:
                return v

        # 3. Cito rescue for the local-miss tail, keyed on the raw reference string.
        if (v := self._cito_rescue(ref)) is not None:
            return v

        # 4. Nothing matched -> candidate suspect for the triage funnel.
        return Verdict(rid, pid, "not_found", None, {"searched_title": norm_title(title)})


def verdict_to_dict(v: Verdict) -> dict:
    return asdict(v)

"""L2 support sample: does the cited paper actually back the claim it is cited for?

This is the pivot away from L1 (existence). On accepted venues nearly every citation
resolves to a real paper, so existence is a near-empty finding. The load-bearing question
peer review never checks is SUPPORT: the citing sentence makes a claim and hangs a
reference on it, but does that reference's content actually support the claim?

We measure a deliberately narrow, defensible proxy: whether the claim is supported *by the
cited paper's abstract*. Abstract-only means we under-claim (real support may live in the
full text), which is the safe direction: a "not supported from the abstract" is a flag to
look closer, never a verdict that the author is wrong. The judge is instructed to default
to `unverifiable_from_abstract` whenever the abstract is too thin to tell, keeping the
`not_supported` bucket high-precision.

This is a SAMPLE tool: it draws N citations, judges each, and reports the support
distribution plus the flagged cases, so we can eyeball real examples and calibrate the
taxonomy before any corpus-wide run or public number.

Run: python -m tuto.l2.support_sample --venue acl-2026 --n 150
"""

from __future__ import annotations

import argparse
import json
import random
import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tqdm import tqdm

from tuto.models import read_jsonl
from tuto.l2.llm_client import LLMClient
from tuto.verify.normalize import author_keys, norm_arxiv, norm_doi, norm_title, title_tokens

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"
SEED = 20260715  # fixed so a rerun draws the same sample (Date.now/random must be stable)

_YEAR_CITE_RE = re.compile(r"\b(19|20)\d{2}[a-z]?\b")

SYSTEM = (
    "You are a meticulous research-integrity assistant. You judge ONLY whether the claim a "
    "citing sentence attributes to a cited work is supported by that cited work's ABSTRACT. "
    "You are conservative and never accuse an author of error: you rate the evidence, not the "
    "person. When the abstract is too brief or general to confirm or deny the claim, you "
    "answer 'unverifiable_from_abstract' -- you never guess 'not_supported'. The citing "
    "sentence may cite several works at once; judge only whether THIS cited work plausibly "
    "supports the specific sub-claim its marker is attached to."
)

USER_TMPL = """CITING SENTENCE (from {paper}, section: {section}):
"{sentence}"

This sentence cites the following work (marker: {marker}):
CITED WORK: {title} ({year})
ABSTRACT: {abstract}

Return a JSON object:
{{"support": one of ["supported","partial","unverifiable_from_abstract","not_supported"],
  "claim_span": "the exact phrase in the citing sentence this citation is backing",
  "rationale": "<= 25 words, evidence-based",
  "confidence": a number 0-1}}

Label guide:
- supported: the abstract clearly backs the attributed claim.
- partial: the abstract backs a weaker or partial version of it.
- unverifiable_from_abstract: abstract too general/brief to tell (DEFAULT when unsure).
- not_supported: abstract is clearly about a different topic, or contradicts the claim. Use only when confident."""


def _specificity(sentence: str) -> int:
    """Fewer co-citations in the sentence -> the claim is more specifically this ref's."""
    return len(_YEAR_CITE_RE.findall(sentence or ""))


def pick_contexts(venue: str) -> dict[str, dict]:
    """One representative citing context per ref_id: the most specific (fewest co-cites)."""
    best: dict[str, dict] = {}
    for c in read_jsonl(DATA / "runs" / venue / "contexts.jsonl"):
        rid = c["ref_id"]
        sent = c.get("sentence") or ""
        if len(sent) < 40:  # too short to carry a checkable claim
            continue
        cur = best.get(rid)
        if cur is None or _specificity(sent) < _specificity(cur["sentence"]):
            best[rid] = c
    return best


_STOP = frozenset(
    "the of and for with from a an to in on via using our we this that is are be as by "
    "towards toward large language model models learning based approach method study "
    "networks network system systems data analysis new".split()
)


def _content(tokens: frozenset[str]) -> frozenset[str]:
    """Keep only discriminative content words: drop stopwords, generic ML filler, short tokens."""
    return frozenset(t for t in tokens if len(t) >= 3 and t not in _STOP)


def _novel_tokens(paper_tokens: frozenset[str], ref: dict) -> frozenset[str]:
    """Content words in the RESOLVED title that appear nowhere in the citation's raw string.

    The raw bibliography string always contains the cited work's real, full title, so any
    content word the resolved record's title has that the raw lacks is evidence we resolved
    a DIFFERENT paper. This is the discriminator token-overlap alone cannot provide: a short
    true title ("Long short-term memory") is fully contained in many wrong longer titles
    ("Photonic Long-Short Term Memory ..."), giving perfect one-way overlap -- but the wrong
    title's extra words (photonic, analog) never occur in the raw string.
    """
    raw_norm = norm_title(ref.get("raw") or "")
    known = _content(frozenset(raw_norm.split())) | _content(title_tokens(ref.get("title")))
    # PDF line-break hyphenation splits words in the raw ("Sci- claims", "biomed- ical"),
    # making the true title's own words look novel. A substring check against the raw with
    # spaces removed recognizes them regardless of where the split fell.
    nospace = raw_norm.replace(" ", "")
    return frozenset(t for t in paper_tokens if t not in known and t not in nospace)


def _title_consistent(paper: dict, ref: dict) -> bool:
    """Guard against a mis-attributed id: the resolved title must overlap the citation.

    GROBID sometimes bleeds an identifier from an adjacent bibliography entry onto a
    reference (e.g. a traffic-forecasting paper carrying Llama 3's arXiv id). An id lookup is
    "definitive" for existence but resolves to the WRONG paper's content, which silently
    poisons the L2 judgment into a false not_supported. So before trusting any resolved
    record we require its title tokens to actually appear in the citation's own text (its raw
    string always contains the real title). Near-zero overlap means the id is not this ref's.
    """
    pt = _content(title_tokens(paper.get("title") or ""))
    if not pt:
        return False
    # Check against the PARSED title, not the raw string: GROBID sometimes merges two
    # bibliography entries into one raw (which is how the wrong id got attached in the first
    # place), so the raw can contain the impostor paper's words while the parsed title stays
    # clean. Fall back to raw only when there is no usable parsed title.
    ref_tokens = _content(title_tokens(ref.get("title")))
    if len(ref_tokens) < 3:
        ref_tokens = _content(frozenset(norm_title(ref.get("raw") or "").split()))
    overlap = len(pt & ref_tokens)
    if overlap / len(pt) < 0.5 and overlap < 4:
        return False
    # Containment is not enough: the resolved title must not carry content words the raw
    # string lacks (see _novel_tokens). One unseen token sinks short titles; long titles
    # tolerate one for normalization noise.
    novel = _novel_tokens(pt, ref)
    return len(novel) == 0 or (len(novel) == 1 and len(pt) > 8)


def cited_paper(cito, ref: dict) -> dict | None:
    """Resolve the cited work to a record with an abstract (id lookup, then raw search)."""
    doi = norm_doi(ref.get("doi"))
    if doi and (p := cito.paper_by_id(doi=doi)) and p.get("abstract") and _title_consistent(p, ref):
        return p
    arxiv = norm_arxiv(ref.get("arxiv_id"))
    if arxiv and (p := cito.paper_by_id(arxiv=arxiv)) and p.get("abstract") and _title_consistent(p, ref):
        return p
    # No id: retrieve the abstract by searching the raw string. L1 already ESTABLISHED this
    # work exists, so here we only need to pull the right record's abstract, not re-prove
    # existence -- a looser match than classify's is appropriate. Precision beats recall here:
    # a rejected hit just becomes "unresolved" (honest), while a wrong hit poisons the L2
    # judgment into a false not_supported (six confirmed cases in the first audited sample:
    # Photonic-LSTM judged as Hochreiter 1997, Nepali word vectors as Mikolov 2013, ...).
    # So containment overlap alone never suffices: the hit must introduce no content words
    # absent from the raw string (see _novel_tokens), and anything short of an exact title
    # match needs author or year corroboration.
    raw = ref.get("raw") or ""
    if not raw:
        return None
    hits = cito.search(raw, limit=8)
    ref_tokens = title_tokens(ref.get("title")) or frozenset(norm_title(raw).split())
    ref_auth = author_keys(ref.get("authors") or [])
    ref_year = ref.get("year")
    best, best_c = None, 0.0
    for h in hits:
        if not h.get("abstract"):
            continue
        ht = title_tokens(h.get("title") or "")
        if not ht:
            continue
        overlap = len(ht & ref_tokens)
        c = max(overlap / len(ht), overlap / max(len(ref_tokens), 1))
        auth_ok = bool(ref_auth & author_keys(
            [a if isinstance(a, str) else a.get("name", "") for a in (h.get("authors") or [])]
        ))
        hy = h.get("year")
        year_ok = isinstance(hy, int) and ref_year is not None and abs(hy - ref_year) <= 1
        novel = _novel_tokens(_content(ht), ref)
        if novel and not (len(novel) == 1 and auth_ok and year_ok):
            continue  # hit title has words this citation never wrote: different paper
        exact = ht == ref_tokens
        if exact or (c >= 0.7 and (auth_ok or year_ok)) or (c >= 0.5 and auth_ok):
            if c > best_c:
                best, best_c = h, c
    return best


def run(venue: str, n: int, workers: int, cito_rps: float) -> dict:
    from tuto.verify.cito_backend import CitoBackend

    run_dir = DATA / "runs" / venue
    refs = {r["ref_id"]: r for r in read_jsonl(run_dir / "refs.jsonl")}
    exists = {v["ref_id"] for v in read_jsonl(run_dir / "verdicts.jsonl") if v["verdict"] == "exists"}
    contexts = pick_contexts(venue)

    # Sample from citations that (a) resolve to a real paper and (b) have a checkable claim
    # sentence -- the population L2 can actually judge.
    pool = [rid for rid in exists if rid in contexts and rid in refs]
    rng = random.Random(SEED)
    rng.shuffle(pool)
    print(f"{len(pool)} judgeable citations (exists + has claim sentence); sampling {n}")

    cache_path = run_dir / "l2_sample_cache.jsonl"
    done = {}
    if cache_path.exists():
        for row in read_jsonl(cache_path):
            done[row["ref_id"]] = row

    cito = CitoBackend(rate_per_sec=cito_rps)
    llm = LLMClient()
    write_lock = threading.Lock()
    cache_file = cache_path.open("a", encoding="utf-8")

    # Walk the shuffled pool until we have n judged rows (some refs fail to resolve an
    # abstract and are skipped, so we over-draw rather than under-fill the sample).
    results = list(done.values())
    todo = [rid for rid in pool if rid not in done]

    def judge(rid: str) -> dict | None:
        ref = refs[rid]
        ctx = contexts[rid]
        paper = cited_paper(cito, ref)
        if paper is None or not paper.get("abstract"):
            return {"ref_id": rid, "skipped": "no_abstract"}
        user = USER_TMPL.format(
            paper=ref["paper_id"],
            section=ctx.get("section") or "?",
            sentence=(ctx.get("sentence") or "").strip()[:700],
            marker=ctx.get("marker") or "?",
            title=paper.get("title") or "?",
            year=paper.get("year") or "?",
            abstract=(paper.get("abstract") or "")[:1600],
        )
        verdict = llm.judge_json(SYSTEM, user)
        if verdict is None:
            return None  # transient LLM failure: retried on rerun
        row = {
            "ref_id": rid,
            "paper_id": ref["paper_id"],
            "citing_sentence": (ctx.get("sentence") or "").strip(),
            "section": ctx.get("section"),
            "cited_title": paper.get("title"),
            "cited_year": paper.get("year"),
            "support": verdict.get("support"),
            "claim_span": verdict.get("claim_span"),
            "rationale": verdict.get("rationale"),
            "confidence": verdict.get("confidence"),
        }
        with write_lock:
            cache_file.write(json.dumps(row, ensure_ascii=False) + "\n")
            cache_file.flush()
        return row

    need = n - len([r for r in results if r.get("support")])
    with ThreadPoolExecutor(max_workers=workers) as pool_ex:
        futures = todo[: max(need * 3, need + 40)]  # over-draw for resolve failures
        for row in tqdm(pool_ex.map(judge, futures), total=len(futures), desc="l2", unit="cite"):
            if row and row.get("support"):
                results.append(row)
    cache_file.close()
    cito.close()
    llm.close()

    judged = [r for r in results if r.get("support")][:n]
    dist = Counter(r["support"] for r in judged)
    flagged = [r for r in judged if r["support"] in ("not_supported", "partial")]
    report = {
        "sampled": len(judged),
        "distribution": dict(dist),
        "pct": {k: dist[k] / max(len(judged), 1) for k in dist},
        "flagged": flagged,
    }
    (run_dir / "l2_sample_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report


def main() -> None:
    ap = argparse.ArgumentParser(prog="tuto.l2.support_sample")
    ap.add_argument("--venue", required=True)
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--cito-rps", type=float, default=20.0)
    args = ap.parse_args()

    r = run(args.venue, args.n, args.workers, args.cito_rps)
    print(f"\nL2 support on {r['sampled']} sampled citations:")
    order = ["supported", "partial", "unverifiable_from_abstract", "not_supported"]
    for k in order:
        n = r["distribution"].get(k, 0)
        print(f"  {k:<28} {n:>4}  {n / max(r['sampled'], 1):>6.1%}")
    print(f"\nflagged (partial / not_supported): {len(r['flagged'])}")
    for f in r["flagged"][:20]:
        print(f"\n  [{f['support']}] conf={f.get('confidence')}  {f['paper_id']}  (cites: {f['cited_title']})")
        print(f"    claim: {f.get('claim_span')}")
        print(f"    why  : {f.get('rationale')}")


if __name__ == "__main__":
    main()

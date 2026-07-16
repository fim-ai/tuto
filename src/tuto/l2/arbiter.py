"""L2 arbiter: a strong model re-examines every flagged citation before we keep the flag.

The first audited sample put the raw judge's flag precision at ~15-20%: of 41 partial /
not_supported flags, only 5-8 survived manual review. The failure modes were systematic
(wrong paper fetched, contrastive cites read backwards, multi-cite lists held to the whole
claim, claim spans cut from the citing authors' own contribution), and several were plain
misses by the cheap judge (not recognizing SQuADRUn = SQuAD 2.0). Flags are ~10% of claim
cites, so a much stronger, slower arbiter over flags-only costs little and is the difference
between a tool that finds errors and one that manufactures them.

Two-stage is the product architecture, not a patch: cheap high-recall judge over everything,
expensive high-precision arbiter over the flags.

Run: python -m tuto.l2.arbiter --venue acl-2026
"""

from __future__ import annotations

import argparse
import json
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tqdm import tqdm

from tuto.fulltext.fetch import fetch_arxiv_source, make_client, tex_to_text
from tuto.fulltext.retrieve import top_passages
from tuto.l2.llm_client import LLMClient
from tuto.l2.support_sample import _content, cited_paper
from tuto.models import read_jsonl
from tuto.verify.normalize import norm_arxiv, title_tokens

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"

ARBITER_MODEL = "claude-sonnet-5"  # env L2_ARBITER_MODEL overrides

SYSTEM = (
    "You are a senior research-integrity arbiter. A first-pass judge has FLAGGED a citation "
    "as partial/not_supported. Your job is to try to REFUTE that flag: most flags are false "
    "alarms, and a false accusation against an author is far worse than a missed error. "
    "Confirm the flag ONLY if, after honestly attempting every innocent reading, the citation "
    "still misattributes, contradicts, or overclaims what the cited work says.\n"
    "Innocent readings you MUST check:\n"
    "1. CONTRASTIVE CITES: in 'Unlike (cite), we ...' the cite backs the characterization of "
    "the cited work, not the citing paper's own claim; evidence the cited work does what it "
    "is characterized as doing REFUTES the flag.\n"
    "2. NEGATIVE CLAIMS: 'the cited work does not do X' is consistent with excerpts that "
    "never mention X.\n"
    "3. MULTI-CITE LISTS: this work only needs to back its own item in a list, not the whole "
    "sentence.\n"
    "4. OWNERSHIP: the citing authors' own contributions or critiques need no backing.\n"
    "5. NAMING: datasets/systems go by several names (SQuADRUn is SQuAD 2.0); a name mismatch "
    "is not a content mismatch.\n"
    "6. WRONG PAPER: if the excerpts are plainly from a different paper than the bibliography "
    "entry describes, the pipeline fetched the wrong text; that is our error, not the "
    "author's.\n"
    "7. HEDGES: 'to some extent', 'often', 'can' make weak versions of claims; a cited work "
    "that supports the weak version supports the claim."
)

USER_TMPL = """CITING SENTENCE (from {paper}, section: {section}):
"{sentence}"

BIBLIOGRAPHY ENTRY the marker {marker} points to:
{raw}

RESOLVED CITED WORK: {title} ({year})

FIRST-PASS FLAG: {support} -- claim_span: "{claim_span}" -- rationale: {rationale}

EXCERPTS from the cited work:
{passages}

Return a JSON object:
{{"flag": "confirmed" or "refuted",
  "support": your own label, one of ["supported","partial","unverifiable_from_text","not_supported"],
  "error_class": when refuted, why the first pass was wrong: one of
    ["wrong_paper","contrastive_misread","negative_claim","multicite_overreach",
     "span_ownership","naming","insufficient_evidence","judge_error"];
    when confirmed, the kind of real problem: one of
    ["wrong_reference","stance_inversion","overclaim","misattribution","loose_cite"],
  "rationale": "<= 40 words, evidence-based",
  "confidence": a number 0-1}}

Confirm only on clear evidence; when the excerpts cannot settle it, refute with
error_class "insufficient_evidence" and support "unverifiable_from_text"."""


def run(venue: str, workers: int, cito_rps: float, model: str | None) -> dict:
    from tuto.verify.cito_backend import CitoBackend

    run_dir = DATA / "runs" / venue
    refs = {r["ref_id"]: r for r in read_jsonl(run_dir / "refs.jsonl")}
    flagged = [
        r for r in read_jsonl(run_dir / "l2_fulltext_cache.jsonl")
        if r.get("cite_type") == "claim" and r.get("support") in ("not_supported", "partial")
    ]
    print(f"{len(flagged)} flagged claim cites to arbitrate")

    cache_path = run_dir / "l2_arbiter_cache.jsonl"
    done = {}
    if cache_path.exists():
        for row in read_jsonl(cache_path):
            done[row["ref_id"]] = row

    arxiv_cache = DATA / "cache" / "arxiv_src"
    cito = CitoBackend(rate_per_sec=cito_rps)
    llm = LLMClient(model=model or ARBITER_MODEL)
    http = make_client()
    write_lock = threading.Lock()
    cache_file = cache_path.open("a", encoding="utf-8")

    def _emit(row: dict) -> dict:
        with write_lock:
            cache_file.write(json.dumps(row, ensure_ascii=False) + "\n")
            cache_file.flush()
        return row

    def arbitrate(item: dict) -> dict | None:
        rid = item["ref_id"]
        if rid in done:
            return done[rid]
        ref = refs.get(rid)
        if ref is None:
            return None
        base = {
            "ref_id": rid, "paper_id": item["paper_id"],
            "first_support": item["support"], "first_rationale": item.get("rationale"),
            "citing_sentence": item["citing_sentence"], "claim_span": item.get("claim_span"),
        }
        # Re-resolve with the hardened matcher. If the paper judged first time was a
        # different one (or no longer resolves at all), the flag was built on the wrong
        # text: drop it without spending arbiter tokens.
        paper = cited_paper(cito, ref)
        judged_tokens = _content(title_tokens(item.get("cited_title") or ""))
        if paper is None or _content(title_tokens(paper.get("title") or "")) != judged_tokens:
            return _emit({
                **base, "flag": "refuted", "support": "unverifiable_from_text",
                "error_class": "wrong_paper",
                "rationale": "hardened resolver rejects the paper the flag was judged against",
                "resolved_title": (paper or {}).get("title"), "confidence": 1.0,
            })
        aid = norm_arxiv(paper.get("arxiv_id") or ref.get("arxiv_id") or "")
        abstract = (paper.get("abstract") or "").strip()
        claim = item["citing_sentence"]
        body = ""
        if aid:
            tex = fetch_arxiv_source(aid, http, arxiv_cache)
            if tex:
                body = tex_to_text(tex)
        if len(body) > 300:
            # Twice the first pass's evidence: the arbiter exists to catch what thin
            # excerpts missed, so give it more of the paper.
            query = f"{paper.get('title') or ''} {claim}"
            blocks = ([f"[abstract] {abstract}"] if abstract else []) + top_passages(body, query, k=12)
            passages = "\n---\n".join(blocks)[:12000]
        elif abstract:
            passages = f"[abstract only -- no full text available] {abstract[:2500]}"
        else:
            return _emit({
                **base, "flag": "refuted", "support": "unverifiable_from_text",
                "error_class": "insufficient_evidence",
                "rationale": "no text available to arbitrate against", "confidence": 1.0,
            })
        verdict = llm.judge_json(
            SYSTEM,
            max_tokens=6000,  # reasoning models think inside this budget; 400 starves them
            user=USER_TMPL.format(
                paper=item["paper_id"], section=item.get("section") or "?",
                sentence=claim[:700], marker=item.get("marker") or "?",
                raw=(ref.get("raw") or "?")[:400],
                title=paper.get("title") or "?", year=paper.get("year") or "?",
                support=item["support"], claim_span=item.get("claim_span") or "?",
                rationale=item.get("rationale") or "?", passages=passages,
            ),
        )
        if verdict is None:
            return None
        return _emit({
            **base,
            "flag": verdict.get("flag"), "support": verdict.get("support"),
            "error_class": verdict.get("error_class"),
            "rationale": verdict.get("rationale"), "confidence": verdict.get("confidence"),
            "cited_title": paper.get("title"), "model": llm.model,
        })

    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool_ex:
        for row in tqdm(pool_ex.map(arbitrate, flagged), total=len(flagged), desc="arbiter", unit="flag"):
            if row:
                results.append(row)
    cache_file.close()
    cito.close()
    llm.close()
    http.close()

    confirmed = [r for r in results if r.get("flag") == "confirmed"]
    report = {
        "flagged_in": len(flagged), "arbitrated": len(results),
        "confirmed": len(confirmed),
        "flag_precision": len(confirmed) / max(len(results), 1),
        "refuted_by_class": dict(Counter(
            r.get("error_class") for r in results if r.get("flag") == "refuted"
        )),
        "confirmed_by_class": dict(Counter(r.get("error_class") for r in confirmed)),
        "confirmed_flags": confirmed,
    }
    (run_dir / "l2_arbiter_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report


def main() -> None:
    import os

    ap = argparse.ArgumentParser(prog="tuto.l2.arbiter")
    ap.add_argument("--venue", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--cito-rps", type=float, default=20.0)
    ap.add_argument("--model", default=os.environ.get("L2_ARBITER_MODEL"))
    args = ap.parse_args()

    r = run(args.venue, args.workers, args.cito_rps, args.model)
    print(f"\narbitrated {r['arbitrated']}/{r['flagged_in']} flags; "
          f"{r['confirmed']} confirmed ({r['flag_precision']:.0%} of first-pass flags)")
    print(f"refuted by class: {r['refuted_by_class']}")
    print(f"confirmed by class: {r['confirmed_by_class']}")
    for f in r["confirmed_flags"]:
        print(f"\n  [{f.get('error_class')}] conf={f.get('confidence')}  {f['paper_id']}")
        print(f"    cites: {f.get('cited_title')}")
        print(f"    claim: {f.get('claim_span')}")
        print(f"    why  : {f.get('rationale')}")


if __name__ == "__main__":
    main()

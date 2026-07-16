"""L2 support, full-text edition: judge the claim against the cited paper's BODY, not its abstract.

The abstract-only probe (support_sample) showed its own ceiling: ~46% of citations landed in
`unverifiable_from_abstract`, half because the abstract was simply too thin to confirm a claim
that the paper's body does state. This closes that gap. For each sampled citation we fetch the
cited work's full text (arXiv LaTeX for ~81% of them, else the abstract as fallback), retrieve
the passages relevant to the claim, and judge support against those passages.

Same conservative judge and taxonomy as the abstract probe, so the two runs are directly
comparable: the story is how much of the abstract-era "unverifiable" resolves once the body is
in view, and what the real partial / not_supported rate looks like with the full paper.

Run: python -m tuto.l2.support_fulltext --venue acl-2026 --n 60
"""

from __future__ import annotations

import argparse
import json
import random
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tqdm import tqdm

from tuto.fulltext.fetch import fetch_arxiv_source, make_client, tex_to_text
from tuto.fulltext.retrieve import top_passages
from tuto.l2.llm_client import LLMClient
from tuto.l2.support_sample import cited_paper, pick_contexts
from tuto.models import read_jsonl
from tuto.verify.normalize import norm_arxiv

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"
SEED = 20260715

SYSTEM = (
    "You are a meticulous research-integrity assistant. You judge ONLY whether the claim a "
    "citing sentence attributes to a cited work is supported by the EXCERPTS provided from that "
    "cited work. You are conservative and never accuse an author of error: you rate the "
    "evidence, not the person. If the excerpts do not address the claim, answer "
    "'unverifiable_from_text' (the excerpts may simply have missed the relevant passage); only "
    "answer 'not_supported' when an excerpt clearly contradicts the claim. The citing sentence "
    "may cite several works at once; judge only whether THIS cited work supports the specific "
    "sub-claim its marker is attached to.\n"
    "Four traps that produce FALSE accusations -- check each before answering:\n"
    "1. CONTRASTIVE CITES. In 'Unlike/In contrast to (cite), we ...' or 'no X (cite) is "
    "applied', the citation backs the CHARACTERIZATION OF THE CITED WORK, not the citing "
    "paper's own claim. Evidence that the cited work does what it is characterized as doing "
    "means SUPPORTED -- even though it 'contradicts' what the citing authors do themselves.\n"
    "2. NEGATIVE CLAIMS. When the sentence says the cited work does NOT do or focus on "
    "something, excerpts that simply never mention it are CONSISTENT with the claim "
    "(supported/unverifiable), never a contradiction.\n"
    "3. MULTI-CITE LISTS. When one sentence hangs several citations on a list of items or "
    "fields, THIS work only needs to back its own item, not the whole list.\n"
    "4. OWNERSHIP. Claims owned by the citing authors themselves ('we propose', 'in this "
    "study', their critique of a benchmark's limitations) need no backing from the cited "
    "work; do not hold the cited work responsible for them."
)

USER_TMPL = """CITING SENTENCE (from {paper}, section: {section}):
"{sentence}"

It cites this work (marker: {marker}):
CITED WORK: {title} ({year})

EXCERPTS from the cited work most relevant to the claim:
{passages}

Return a JSON object:
{{"cite_type": one of ["claim","nominal"],
  "support": one of ["supported","partial","unverifiable_from_text","not_supported"],
  "claim_span": "the exact phrase in the citing sentence this citation is backing -- the
   clause its marker attaches to, NEVER the citing authors' own contribution ('we ...')",
  "rationale": "<= 25 words, cite what the excerpts do or do not say",
  "confidence": a number 0-1}}

cite_type guide (decide FIRST):
- nominal: the citation merely names/points to a dataset, model, benchmark, tool, or method
  ("we use NQ (Kwiatkowski et al., 2019)", "Qwen3-8B (Yang et al., 2025)") without attributing
  a specific finding or property. Support is not really applicable; still fill it in.
- claim: the citation backs a factual assertion, result, or property. This is what we measure.

support guide:
- supported: an excerpt clearly backs the attributed claim.
- partial: an excerpt backs a weaker or partial version of it.
- unverifiable_from_text: excerpts don't address the claim (DEFAULT when unsure).
- not_supported: an excerpt clearly contradicts the claim, or the paper is plainly about something else. Use only when confident."""


def run(venue: str, n: int, workers: int, cito_rps: float, rids: list[str] | None = None) -> dict:
    from tuto.verify.cito_backend import CitoBackend

    run_dir = DATA / "runs" / venue
    refs = {r["ref_id"]: r for r in read_jsonl(run_dir / "refs.jsonl")}
    exists = {v["ref_id"] for v in read_jsonl(run_dir / "verdicts.jsonl") if v["verdict"] == "exists"}
    contexts = pick_contexts(venue)
    if rids is not None:
        # caller-directed set (e.g. paper_audit judging EVERY cite of sampled papers)
        pool = [rid for rid in rids if rid in exists and rid in contexts and rid in refs]
        n = len(pool)
    else:
        pool = [rid for rid in exists if rid in contexts and rid in refs]
        rng = random.Random(SEED)
        rng.shuffle(pool)
    print(f"{len(pool)} judgeable citations; sampling up to {n} with full text")

    cache_path = run_dir / "l2_fulltext_cache.jsonl"
    done = {}
    if cache_path.exists():
        for row in read_jsonl(cache_path):
            done[row["ref_id"]] = row

    arxiv_cache = DATA / "cache" / "arxiv_src"
    cito = CitoBackend(rate_per_sec=cito_rps)
    llm = LLMClient()
    http = make_client()
    write_lock = threading.Lock()
    cache_file = cache_path.open("a", encoding="utf-8")

    def judge(rid: str) -> dict | None:
        if rid in done:
            return done[rid]
        ref = refs[rid]
        ctx = contexts[rid]
        paper = cited_paper(cito, ref)
        if paper is None:
            return {"ref_id": rid, "skipped": "unresolved"}
        claim = (ctx.get("sentence") or "").strip()
        # full text via arXiv source; fall back to abstract when there is no source
        aid = norm_arxiv(paper.get("arxiv_id") or ref.get("arxiv_id") or "")
        source = "abstract"
        body = ""
        if aid:
            tex = fetch_arxiv_source(aid, http, arxiv_cache)
            if tex:
                body = tex_to_text(tex)
                if len(body) > 300:
                    source = "fulltext"
        abstract = (paper.get("abstract") or "").strip()
        if source == "fulltext":
            # Always lead with the abstract (it states the paper's claims compactly), then the
            # BM25-retrieved body passages. Query on the claim plus the cited title, so
            # retrieval is anchored to the paper's own topic, not just the citing sentence.
            query = f"{paper.get('title') or ''} {claim}"
            body_passages = top_passages(body, query, k=6)
            blocks = ([f"[abstract] {abstract}"] if abstract else []) + body_passages
            passages = "\n---\n".join(blocks)[:5000]
        else:
            passages = abstract[:1600]
            if not passages:
                return {"ref_id": rid, "skipped": "no_text"}
        verdict = llm.judge_json(
            SYSTEM,
            USER_TMPL.format(
                paper=ref["paper_id"], section=ctx.get("section") or "?",
                sentence=claim[:700], marker=ctx.get("marker") or "?",
                title=paper.get("title") or "?", year=paper.get("year") or "?",
                passages=passages,
            ),
        )
        if verdict is None:
            return None
        row = {
            "ref_id": rid, "paper_id": ref["paper_id"], "source": source,
            "citing_sentence": claim, "section": ctx.get("section"),
            "cited_title": paper.get("title"), "cited_arxiv": aid,
            "cite_type": verdict.get("cite_type"),
            "support": verdict.get("support"), "claim_span": verdict.get("claim_span"),
            "rationale": verdict.get("rationale"), "confidence": verdict.get("confidence"),
        }
        with write_lock:
            cache_file.write(json.dumps(row, ensure_ascii=False) + "\n")
            cache_file.flush()
        return row

    pool_set = set(pool)
    results = [r for r in done.values() if r.get("support") and (rids is None or r["ref_id"] in pool_set)]
    todo = [rid for rid in pool if rid not in done][: max((n - len(results)) * 3, n + 30)]
    with ThreadPoolExecutor(max_workers=workers) as pool_ex:
        for row in tqdm(pool_ex.map(judge, todo), total=len(todo), desc="l2-full", unit="cite"):
            if row and row.get("support"):
                results.append(row)
    cache_file.close()
    cito.close()
    llm.close()
    http.close()

    judged = [r for r in results if r.get("support")][:n]
    src = Counter(r["source"] for r in judged)
    ctype = Counter(r.get("cite_type") for r in judged)
    dist = Counter(r["support"] for r in judged)
    # The honest denominator for a support rate is CLAIM cites; nominal (dataset/model
    # pointer) cites are reported separately, never mixed into "weak support".
    claim_rows = [r for r in judged if r.get("cite_type") == "claim"]
    claim_dist = Counter(r["support"] for r in claim_rows)
    flagged = [r for r in claim_rows if r["support"] in ("not_supported", "partial")]
    report = {
        "sampled": len(judged), "source_mix": dict(src), "cite_type_mix": dict(ctype),
        "distribution_all": dict(dist),
        "claim_cites": len(claim_rows),
        "distribution_claim": dict(claim_dist),
        "pct_claim": {k: claim_dist[k] / max(len(claim_rows), 1) for k in claim_dist},
        "flagged": flagged,
    }
    (run_dir / "l2_fulltext_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report


def main() -> None:
    ap = argparse.ArgumentParser(prog="tuto.l2.support_fulltext")
    ap.add_argument("--venue", required=True)
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--cito-rps", type=float, default=20.0)
    args = ap.parse_args()

    r = run(args.venue, args.n, args.workers, args.cito_rps)
    print(f"\nL2 full-text on {r['sampled']} citations  (source: {r['source_mix']}, type: {r['cite_type_mix']})")
    print(f"\nsupport among {r['claim_cites']} CLAIM cites (nominal/pointer cites excluded):")
    for k in ["supported", "partial", "unverifiable_from_text", "not_supported"]:
        c = r["distribution_claim"].get(k, 0)
        print(f"  {k:<26} {c:>4}  {c / max(r['claim_cites'], 1):>6.1%}")
    print(f"\nflagged claim cites (partial / not_supported): {len(r['flagged'])}")
    for f in r["flagged"][:16]:
        print(f"\n  [{f['support']}] conf={f.get('confidence')} src={f['source']}  {f['paper_id']}")
        print(f"    cites: {f['cited_title']}")
        print(f"    claim: {f.get('claim_span')}")
        print(f"    why  : {f.get('rationale')}")


if __name__ == "__main__":
    main()

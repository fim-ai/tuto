# Tuto

> cito, **tuto**, iucunde: swiftly, safely, pleasantly. Cito finds the literature. Tuto makes sure it is cited correctly.

A citation-integrity audit pipeline, plus a public integrity report for major venues. First run: the ACL 2026 proceedings (4,459 papers, main conference and Findings).

We checked all 209,985 references in ACL 2026 on two levels: whether the cited work exists (L1), and, for claim citations, whether the cited paper actually supports the claim it is attached to (L2).

- Site: [tuto.fim.ai](https://tuto.fim.ai), the public report; the single-paper self-service checker is at [/check](https://tuto.fim.ai/check)
- Report source: `docs/REPORT-acl-2026-draft.en.md`
- Dataset: [`dataset/`](dataset/), 18,724 + 3,795 citation-level judgments and 169 arbitration records, anonymized, CC BY 4.0; schema in its README
- Docs: `docs/PRD.md` (product spec), `docs/ARCHITECTURE.md` (technical architecture)

## Results at a glance (full ACL 2026 corpus)

| Metric | Number |
|---|---|
| References audited | 209,985 (across 4,459 papers) |
| Confirmed nonexistent references | 2 (0.001%): fabrication is not the problem |
| Papers with at least one confirmed unsupported citation | 16%: support is |
| Our own first-pass precision | 13% (published, not hidden): false positives are the enemy |

## Why we parse the PDFs ourselves

Reference lists are not in any metadata. `anthology.bib` carries a paper's own metadata but not who it cites. Second-hand citation databases do not have it either: Crossref reports `reference-count = 0` for ACL papers, and OpenAlex reports `referenced_works = 0`.

The deeper reason is that **those databases only store references that matched something**. A fabricated reference is, by construction, the one that failed to match and was silently dropped. Hunting hallucinated citations in a second-hand citation database is logically self-defeating. You have to read back the actual string the author wrote, from the PDF.

## Repository layout

| Directory | Contents |
|---|---|
| `src/tuto/` | The audit pipeline (ingest / parse / verify / triage / arbiter) |
| `docs/` | Full report, PRD, architecture, anonymized manual-review records |
| `web/` | tuto.fim.ai: the report and the [/check](https://tuto.fim.ai/check) self-service page |
| `tests/` | Parsing and parser-guard regression tests |

**Single-paper checking is live** at [tuto.fim.ai/check](https://tuto.fim.ai/check). Drop in an arXiv ID and it runs the same pipeline as the full audit (existence, claim support, arbitration), returning leads for human review in a few minutes. The server lives in `src/tuto/check/` (FastAPI, `pip install -e ".[api]"`) and self-hosts on one machine: `uvicorn tuto.check.service:app` plus a GROBID container and a DBLP snapshot.

## The pipeline

```
ingest → parse → verify(L1) → triage(automated review funnel) → arbiter(L2 arbitration) → report
```

Humans do only three things: spot-check a sample to compute the false-positive rate, adjudicate the suspicious list (12 items, verified one by one), and select and anonymize the cases that appear in the report.

## Usage

```bash
uv venv --python 3.12 && uv pip install -e .

# Ingest: all bib entries and PDFs (rate-limited to ~1MB/s per IP;
# 4,459 papers takes about 3 hours, resumable)
uv run tuto ingest --venue acl-2026

# Parse: GROBID → refs.jsonl + contexts.jsonl, plus a parse-acceptance sample
uv run tuto parse --venue acl-2026 --grobid-url http://localhost:8070
```

GROBID needs to be running: `docker run -d --name grobid -p 8070:8070 lfoppiano/grobid:0.8.1`

To audit a different venue, add a row to the `VENUES` table in `src/tuto/ingest/acl_anthology.py`.

## Quick facts

- Data source: ACL Anthology directly (full bib dump, PDFs at predictable URLs)
- Parsing: GROBID `processReferences`. **Not MinerU.** ACL PDFs are born-digital LaTeX output with no OCR need; MinerU and OCR are reserved as a scanned-document fallback for the V2 upload tool
- Parse acceptance: a layout-based reference counter working on a different principle from GROBID (it counts flush-left lines of hanging-indent blocks) cross-checks the whole corpus. Only the disagreements get manual review, which yields a recall estimate with an interval, published
- L1 verification: local snapshots first (DBLP / arXiv / OpenAlex / Anthology bib + the Cito index), falling back to the Crossref and OpenAlex APIs
- Hard rule: the public report carries aggregate statistics and anonymized cases only. It never names a paper or an author. There is no author notification and no appeal process (see report §3.4)

## Site development

```bash
cd web && pnpm install && pnpm dev   # http://localhost:5297
```

The site reads the report Markdown from `../docs/` at build time.

## License

- Code: Apache-2.0 (see `LICENSE`)
- Report text (`docs/REPORT-*`): CC BY 4.0

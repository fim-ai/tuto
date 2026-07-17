# Tuto ACL 2026 Citation-Audit Dataset (v1)

Anonymized citation-level judgments and arbitration records from the Tuto audit of the
ACL 2026 proceedings (main + Findings, 4,459 papers, 209,985 references). Released with
the report at https://tuto.fim.ai/report under **CC BY 4.0**. Pipeline code (Apache-2.0):
https://github.com/fim-ai/tuto

## Anonymization

Nothing in this dataset identifies a paper or an author, by design:

- No paper IDs, reference IDs, DOIs, or arXiv IDs.
- No verbatim text of any kind: citing sentences, claim spans, cited titles, section
  names, judge rationales, and raw citation strings are all removed, because every one of
  them is searchable back to a specific paper.
- Papers carry random opaque keys (`paper_key`) so per-paper aggregation stays possible;
  records carry random `record_key`s so arbitration rows join their first-pass rows. The
  key maps were generated with a CSPRNG and are not published; record order is sorted by
  the random key, so line order carries no information.

## Files

### `l1_triage.jsonl` (18,724 records)

The L1 existence-check residue (references not found in any snapshot after fuzzy rescue),
each judged by an LLM triage stage.

| field | meaning |
|---|---|
| `record_key` | random opaque id |
| `paper_key` | random opaque id of the citing paper |
| `category` | `known_paper` / `plausible_paper` / `non_paper` / `garbled` / `suspicious` |
| `subtype` | finer label within the category (e.g. `dataset`, `software`, `url`) |
| `confidence` | judge self-reported confidence, 0 to 1 |

### `l2_judgments.jsonl` (3,795 records)

Claim-citation support judgments, three runs distinguished by `run`:
`acl2026-sample-n150` (abstract-based sample), `acl2026-fulltext-100papers`
(experiment 1, full-paper granularity), `control-arxiv-cscl-2024` (control corpus).

| field | meaning |
|---|---|
| `record_key`, `paper_key` | random opaque ids |
| `run` | which run this record belongs to |
| `source` | evidence source: `abstract` or `fulltext` |
| `cite_type` | `claim` or `nominal` |
| `support` | `supported` / `partial` / `unverifiable_from_abstract` / `unverifiable_from_text` / `not_supported` |
| `cited_year` | year of the cited work, where parsed |
| `confidence` | judge self-reported confidence |

### `l2_arbiter.jsonl` (169 records)

Refutation-stance second-stage review of every first-pass flag. Join to
`l2_judgments.jsonl` on `record_key`.

| field | meaning |
|---|---|
| `first_support` | first-pass verdict being reviewed |
| `flag` | arbiter stance: `confirmed` / `refuted` |
| `support` | final verdict after arbitration |
| `error_class` | first-pass error taxonomy label when refuted (e.g. `judge_error`, `multicite_overreach`) |
| `model` | arbiter model |

### `aggregates.json`

The run-level reports the published numbers derive from, scrubbed to aggregate-shaped
data only (counts, rates, enums; all case arrays and long strings removed).

## Reproducing the headline numbers

- **First-pass precision 13%**: fraction of first-pass flags in
  `acl2026-fulltext-100papers` whose arbiter `flag` is `confirmed`.
- **16% of papers with at least one confirmed unsupported citation**: group
  arbiter-confirmed `not_supported` records by `paper_key` within
  `acl2026-fulltext-100papers`, count distinct papers over the 100 audited.
- **L1 funnel**: category counts in `l1_triage.jsonl` match report section 5
  (the 12 `suspicious` records are the ones that went to human review; 2 were confirmed
  nonexistent, and those confirmations are aggregate-only by policy).

## License and citation

Data: CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Cite as:

> Tuto (fim.ai), "Citation Integrity in ACL 2026: a Full-Corpus Audit," 2026.
> https://tuto.fim.ai/report

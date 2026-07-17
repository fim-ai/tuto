# Citation Integrity at ACL 2026: A Full-Corpus Audit

> **Draft v0.6 (English)** · 2026-07-17 · tuto.fim.ai
> Remaining before site release: final anonymization pass on the L2 case gallery (done in this draft, pending owner sign-off). The L1 suspicious review is complete; its record ships in anonymized form only. By decision there is no author notification or appeal window: the report is aggregate-only and names no one, so there is nothing to appeal.
> Hard rule throughout: aggregate statistics and anonymized cases only. No paper or author is ever named.

---

## 0. Key findings

1. **Fabricated references are not a real phenomenon at ACL 2026.** We classified all 209,985 references through a two-stage funnel (fuzzy-match rescue, then LLM triage) and hand-checked the residue. 91.0% resolve to real literature. The unmatched remainder is dominated by non-paper entities (3.5%) and parsing noise (4.2%). LLM triage left 12 suspicious leads; manual verification found 10 of them to be metadata errors on real works or non-paper entities. **Two references, 0.001% of the corpus, were confirmed as pointing to papers that do not exist.** We found no evidence of hallucinated citations at scale.
2. **The real problem is support: citations to real papers that do not say what they are cited for.** In a random sample of 100 ACL 2026 papers, **16% [10-24] contain at least one problem citation confirmed by two-stage verification**. At the citation level, the confirmed rate is 0.95% [0.6-1.5] (20 of 2,110 claim citations).
3. **Peer review barely touches citation quality.** Accepted papers and unreviewed preprints show nearly the same confirmed-problem rate (0.95% vs. 1.2%, intervals overlapping heavily). This is not a scandal; it confirms what everyone already knew. Reviewers evaluate claims and experiments, not reference lists. This layer of checking simply did not exist before.
4. **The number-one enemy of automated citation auditing is its own false positives, not its misses.** Across two batches, 188 first-pass flags survived arbitration as only 25 confirmed findings: first-pass precision 13% [9-19]. Any "AI citation checker" that does not publish its own false-positive rate should be presumed untrustworthy. We publish our two-stage architecture, our error taxonomy, and every precision number, together with the code.
5. **Every confirmed error looks human.** BibTeX pulling a same-name or adjacent entry; citing a paper against its own stated position (about a quarter of confirmed cases); compressing "handles basic tasks but fails on complex ones" into "cannot do it." These are the failure modes that deadline-pressed authors and LLM-assisted writing share, and they are exactly what ten minutes of pre-submission checking can fix.

---

## 1. Why now

- **The conference is growing fast.** ACL main-track long papers went from 1,603 (2025) to 2,223 (2026); Findings from 1,388 to 2,164. That is roughly 40% growth in one year, with no matching growth in reviewer supply.
- **Nobody has ever checked citations.** Reviewers judge claims and experiments, not reference lists. Production checks formatting, not content. "Does the cited paper support the claim" is a standing vacancy in the academic quality chain.
- **LLM-assisted writing turns that vacancy into exposure.** Language models are at their best generating citations that look right: real papers, fluent paraphrases, wrong attribution. Existence checks cannot catch this class of error. Only support checks can.

## 2. What we check: two layers

| Layer | Question | Method |
|---|---|---|
| **L1 existence** | Does this reference point to a real paper? | All 209,985 references: local literature snapshot (DBLP / arXiv / OpenAlex / ACL Anthology) plus Cito search, four-way classification |
| **L2 support** | Does the cited paper actually support the claim attached to it in the text? | Sampled: retrieve the cited paper's full text (arXiv LaTeX source), locate relevant passages, two-stage LLM judgment |

L2 first separates citation types. **Nominal** citations (naming a dataset, model, or tool, with no attributed claim) stay out of the statistics. **Claim** citations (a factual assertion attached to the cited paper) are what we verify, on a four-way scale: `supported` / `partial` / `unverifiable_from_text` / `not_supported`.

## 3. Method: two-stage judgment and a control corpus

### 3.1 Two-stage architecture

```
all claim citations
   │  first pass: light model, high recall, deliberately suspicious
   ▼
flags (partial / not_supported, ~5-7%)
   │  arbiter: strong reasoning model + double evidence passages
   │  + refutation stance (default: the flag is wrong, unless it
   │    survives every good-faith reading)
   ▼
confirmed (~12% of first-pass flags)
```

The arbiter is not an engineering compromise. It is a methodological position: **the cost of accusing an author is asymmetric**. One wrongful flag costs more than one miss. The arbiter must explicitly rule out seven benign readings (contrastive sentences, negative claims, multi-citation lists, the citing authors' own claims, aliases, wrong-fulltext retrieval, hedged wording) before a flag stands.

### 3.2 Calibration

The arbiter is not a black box stacked on a black box. We hand-read all 41 pilot first-pass flags independently, then cross-checked against the arbiter. Agreement was high, and every disagreement sat in the defensible gray zone of stance-loaded citations. The hand reading also produced a taxonomy of first-pass false positives (§7); pipeline defects (retrieving the wrong cited paper's full text) accounted for 8 of 41 and are fixed, with regression tests.

### 3.3 Control corpus

The same pipeline ran on two corpora:

- **ACL 2026** (main + Findings, 4,459 papers): peer reviewed.
- **arXiv cs.CL 2024-01** (300 papers): unreviewed preprints, taken from their submission month.

Any difference attributes to the review-and-revision axis.

### 3.4 Hard rules

- The public report contains aggregate statistics and anonymized cases only, and never names anyone. Since no one is named, there is no accusation, and therefore no notification or appeal process (see §11).
- The verdict wording is always `not supported by the cited text`, never `fabricated`. We verify text correspondence; we do not judge intent.
- Pipeline code, taxonomy, and false-positive rates are all public (Apache-2.0). The conclusions are reproducible.

## 4. Corpora and parsing acceptance

| | ACL 2026 | arXiv cs.CL 2024-01 |
|---|---|---|
| Papers | 4,459 | 300 |
| References | 209,985 | 15,377 |
| In-text citation contexts | 210,343 | 18,401 |

Parsing uses GROBID, and acceptance does not rest on spot-check intuition. An independent layout-based counter (it counts hanging-indent lines, a method unrelated to GROBID's) cross-checked all 4,459 papers, with human review only where the two disagreed. Median ratio 1.0; 70.7% of papers agree within ±2 references. The real extraction gaps this probe located (including 6 papers where GROBID returned nothing) were re-run through a fallback parser.

## 5. Finding one: fabricated references are not a real problem (L1)

Of 209,985 references:

| Verdict | Count | Share |
|---|---|---|
| exists (direct hit) | 177,561 | 84.6% |
| not_found (first-round miss) | 28,569 | 13.6% |
| unparseable | 3,824 | 1.8% |
| minor_mismatch | 31 | ~0 |

What matters is the composition of the not_found bucket, because it is **not** 28,569 suspect citations. We ran the full bucket (28,568 entries; one processing error) through two further stages, with no sampling extrapolation left.

**Stage one: fuzzy-match rescue** (token containment plus author/year matching):

| Outcome | Count | Share |
|---|---|---|
| Recovered as real literature | 8,872 | 31.1% |
| Non-paper entity by pattern (software / model / URL) | 946 | 3.3% |
| Residual suspects, sent to LLM triage | 18,750 | 65.6% |

**Stage two: light LLM triage** (a small model reads each raw entry; 18,724 judged):

| Category | Count | Share of residue | Meaning |
|---|---|---|---|
| non_paper | 6,410 | 34.2% | Model cards (1,957), blogs (987), web pages (928), datasets (593), and so on. A citation-style issue, not an integrity issue |
| garbled | 4,926 | 26.3% | Parsing debris: split or merged entries, table rows. Our noise, nobody's citation |
| known_paper | 4,623 | 24.7% | Real papers the model recognizes outright: index coverage lag, authors innocent |
| plausible_paper | 2,753 | 14.7% | Coherent scholarly citations the model does not specifically know: counted as unverifiable, never as fabricated |
| **suspicious** | **12** | **0.06%** | The model is confident the author-title-venue combination does not exist: leads for human review only |

**Final tally (share of all 209,985)**: confirmed or recognized real literature, 191,056 (91.0%); non-paper entities, 7,356 (3.5%); parsing noise (garbled + unparseable), 8,750 (4.2%); unverifiable but unsuspicious, 2,753 (1.3%); model-confident "likely nonexistent" leads, **12 (0.006%)**, of which manual verification confirmed **2 (0.001%)**.

**A false-positive incident of our own (see finding 4)**: the first version of the triage prompt reported 166 suspicious entries. Attribution showed that about 154 of them were our tool's own false positives. The prompt never told the judging model that the citing papers were published in 2026, so a model with an earlier knowledge cutoff classified 2026-dated references, and any model or paper released after its cutoff, as "future year, does not exist." After injecting the venue year into the prompt and re-judging, suspicious converged to 12. This is precisely the report's recurring point: the primary enemy of automated citation auditing is its own false positives, and every flag count is only a lead until a human has reviewed it.

**Manual verification (2026-07-17, per-entry, online)**: of the 12 suspicious entries, 9 are metadata errors on real works (a 1961 classic dated 2007; a 1945 test cited by its 1992 Springer reprint volume; a game-theory classic cited by its electronic reissue year; a textbook's journal book review cited as the book; an obscure but real venue), and 1 is a non-paper entity (a GitHub repository). **Two entries were confirmed nonexistent.** Both share one shape: real author names and a real research topic stitched onto a specific paper that does not exist (a journal issue that does not contain the article; a year and volume number that contradict each other). That is the textbook signature of LLM-hallucinated citations. Bottom line: **2 confirmed likely-fabricated references out of 209,985, or 0.001%**. What used to be a sampled "below one in a thousand" estimate is now a full-corpus measurement with human review, and it came in two orders of magnitude lower. The L1 conclusion: **on a born-digital top-venue proceedings, existence checking is a nearly empty finding, and the scare narrative should end here.**

## 6. Finding two: support defects are real but restrained (L2 pilot)

Pilot sample (about 400 judged citations per corpus; claim-type: ACL 367, arXiv 341):

### First-pass distribution (claim citations)

| Support | ACL 2026 | arXiv 2024 |
|---|---|---|
| supported | 63% | 67% |
| partial | 2% | 2% |
| unverifiable_from_text | 33% | 26% |
| not_supported | 3% | 5% |
| **first-pass flags (partial + not_sup)** | **4.6% [2.9-7.3]** | **7.0% [4.8-10.3]** |

### After arbitration

| | ACL 2026 | arXiv 2024 |
|---|---|---|
| First-pass flags | 17 | 24 |
| **Arbiter-confirmed** | **1** | **4** |
| **Confirmed hard-error rate** | **0.27% [0.05-1.53]** | **1.17% [0.46-2.98]** |

Two honest readings:

- **The absolute scale is restrained.** There is no "academic citation collapse" story here. Over nine in ten claim citations sit in the supported or partially supported range, within what is verifiable.
- **But the denominator is large.** Apply the §8 large-sample estimate of 0.95% to ACL 2026's roughly 90,000 verifiable claim citations and you get close to a thousand hard, review-proof citation errors scattered across the proceedings.
- **Updated review-axis comparison**: the post-fix large-sample ACL number (0.95%) nearly coincides with the unreviewed-preprint pilot (1.17%). On this data, the "review catches citation errors" axis is flat.

`unverifiable_from_text` (26-33%) is a method ceiling, not a finding: the cited paper has no arXiv full text (coverage is about 81%) or passage retrieval missed the claim's location. The bucket is reported as-is and never counted toward any error rate.

## 7. Finding three: the false-positive problem of automated citation auditing (methodological contribution)

Of 41 first-pass flags in the pilot, only 5 survived arbitration plus independent human review: **first-pass precision 12% [5-26]**. Had we published first-pass numbers directly, we would have overstated the problem-citation rate by roughly 8x and wrongly implicated 36 papers. This is the shared reef of every "AI paper checker," and it is rarely disclosed.

Taxonomy of first-pass false positives (attribution of the 36 rejected flags):

| Type | Count | Note |
|---|---|---|
| Pipeline wrong-fulltext (wrong_paper) | 8 | Judged against the full text of a same-name or near-name paper. Fixed: content-word consistency check between parsed title and raw bib entry, with an 11-case regression suite |
| Multi-citation overreach (multicite_overreach) | 11 | One sentence carries N citations; each needs to support only its own share, but was asked to support the whole sentence |
| Claim ownership (span_ownership) | 8 | The citing authors' own claim was treated as something the cited paper must support |
| Contrastive misread (contrastive_misread) | 2 | In "Unlike (cite), we ...", the citation supports the contrasted work's property |
| Negative claim (negative_claim) | 2 | "The cited work does not do X": silence about X is agreement, not contradiction |
| Alias blindness (naming) | 2 | SQuADRUn is SQuAD 2.0, and the like |
| Judge error / insufficient evidence | 8 | The rest |

**This table is the design spec for the /check tool**: the first four types are now parsing defenses and judging instructions respectively, and the arbiter stays as a permanent second stage.

## 8. The paper-level view: how many papers have at least one problem citation

Design: 100 papers randomly sampled from ACL 2026 (eligibility: at least 10 verifiable claim citations per paper; 4,338 papers qualify). Every verifiable claim citation in each paper went through the full two-stage pipeline (post-fix parsing defenses, first pass, strong-model arbitration). 2,110 claim citations judged in total.

| Metric | Result |
|---|---|
| Papers with at least 1 first-pass flag | 63 / 100 |
| **Papers with at least 1 confirmed problem citation** | **16 / 100 (16% [10-24])** |
| Confirmed problem citations | 20 (0.95% [0.6-1.5] of claim citations) |
| First-pass precision this round | 20/147 = 14% (consistent with the pilot's 12%) |
| Confirmed problem types | misattribution 12 · stance_inversion 4 · overclaim 3 · loose_cite 1 |

Three readings:

- **This is the number that matters to authors.** A citation-level 0.95% sounds ignorable; a paper-level "one in six papers, maybe yours" does not.
- **Problems cluster.** Two of the 16 problem papers carry 3 confirmed problems each. Citation sloppiness is a paper-level trait, not uniform noise. This favors a "check the whole paper before submission" product over per-citation spot luck.
- **The citation-level 0.95% is compatible with the pilot's 0.27% [0.05-1.53]** (the pilot confirmed a single case; its interval is very wide). The large-sample number is the more credible estimate, and this report standardizes on 0.95%.

## 9. Case gallery (anonymized)

Representative cases from the 25 confirmed findings (5 pilot + 20 paper-level), grouped by error type. The release version will add a one-click Cito repair demo (a correct substitute reference plus a ready-made bib entry). All cases are paraphrased; none is quoted verbatim.

**Stance inversion (6/25)**: citing a paper that discusses the topic, without checking which side it takes. Existence checks can never catch this type, and it hurts readers the most.

1. A benchmark dataset was cited as an example of highly regular tables, while its own description of itself is semi-structured and non-normalized. A literal reversal.
2. A paper cited a work in support of the claim that children must be born with universal grammar; the cited work's central argument is that the evidence for universal grammar does not hold.
3. An evaluation method that positions itself as an interpretable, unsupervised score was cited as an example of earlier metrics that rely on uninterpretable scores.
4. A method built explicitly around language-specific adapter experts was cited as prior work that ignores language representation and adapts all layers uniformly.

**Misattribution (14/25)**: the cited paper and the attached claim belong to two different topics.

5. A claim about RLHF aligning models via preference data was attached to a paper about polarization analysis on social media. The signature matches a reference manager picking the wrong same-surname entry.
6. A work that describes itself as a black-box jailbreak method was listed among white-box methods that require full access to model internals and parameters.
7. A prompting framework that explicitly claims to need no task-specific fine-tuning was cited as an example of the supervised fine-tuning line.

**Overclaim (3/25)**: compressing and amplifying the cited paper's limited conclusion.

8. A cited work that found a single unit encoding binary sentiment polarity was restated as the origin of the concept of emotion neurons, plural and separable.

**Loose citation (2/25)**: the claim and the cited work merely graze the same topic.

9. A claim about more precise retrieval was attached to a domain survey that talks generally about prompt usage and never discusses retrieval.

## 10. Known limitations

1. **The unverifiable ceiling**: 26-33% of claim citations cannot be judged because the cited full text is unavailable or passage retrieval missed the claim. The upper bound of the true error rate is therefore uncertain; we draw conclusions only over the evidence-backed portion.
2. **arXiv full-text coverage is about 81%**: non-arXiv references degrade to abstract-based judgment, and anything an abstract cannot settle counts as unverifiable.
3. **One context per citation**: we judge the most specific (least co-cited) occurrence. Other occurrences of the same citation are not covered.
4. **Judgments rely on LLMs**: two stages, human calibration, and fully public prompts mitigate but do not eliminate this. Arbiter and human readings disagree, both ways defensibly, in the stance-loaded gray zone (3 pilot cases).
5. **The control corpus is not fully comparable**: arXiv 2024-01 and ACL 2026 differ in time and topic mix, and the preprint batch was judged before the pipeline fixes (its flags went through arbitration, so the directional impact is limited). The comparison supports only the coarse conclusion of "no dramatic difference."
6. **Confirmed cases have a gray zone**: in a full hand read of the 20 paper-level confirmed cases, about 3-5 are borderline (generalizing sentences over multi-citation lists and similar), defensible in both directions. Removing all of them keeps the paper-level rate near 12%; the core conclusion stands. The release version will carry per-case confidence labels.
7. **L1's recall blind spot**: L1 can only confirm entries the judging model dares to declare nonexistent. A fabricated citation with a plausible enough author-title-venue combination lands in plausible_paper (2,753 entries) and is never flagged; fuzzy rescue can also "rescue" a fabricated entry onto a neighboring real paper. The 2 confirmed cases are a detectable lower bound. Read the L1 number as "detectable fabrication is extremely rare," not "fabrication is extremely rare." This is one more argument that support checking (L2) is the real battleground.

## 11. Next steps

- **[Done]** Paper-level audit of 100 papers (§8).
- **[Done]** L1 residue triage (§5, all 18,724 entries; re-judged after the prompt-v2 fix for the year-context false positives) plus manual review of the 12 suspicious entries (2 confirmed; anonymized record in `docs/SUSPICIOUS-review-checklist.md`, full evidence chain kept internal). L1 is closed end to end.
- **[Queued]** The year axis: ACL 2018/2019 vs. 2026, testing whether citation quality changed in the LLM-writing era. Precondition: a coverage precheck of Cito on pre-2015 literature; if coverage is not aligned, the comparison is invalid and we would rather not run it.
- **[Release process]** Publish the report plus the anonymized dataset (CC BY) directly. By decision, no pre-release author notification or appeal window: the report publishes aggregates and anonymized cases only, and naming no one means accusing no one. Notification and appeals are the apparatus for a world where people can be identified; that world is not this report. The "+30-day author correction rate" follow-up metric is dropped accordingly.
- **[Product]** /check, a single-paper self-service tool: this report's two-stage pipeline as a service, ten minutes before submission. The report is the traffic; the tool is the conversion.

---

## Appendix A: Terms

- **Claim citation**: a citation that attaches a factual assertion to the cited paper. The object of verification.
- **Nominal citation**: a citation that merely names a dataset, model, or tool. Excluded from error rates.
- **not_supported**: the cited paper's text contradicts the attached claim, or clearly discusses an unrelated topic.
- **confirmed**: a first-pass flag that survives strong-model refutation-stance arbitration plus (in the pilot) human review.
- **False-positive numbers**: first-pass precision 12% [5-26]; arbiter and human readings disagreed only in the stance-loaded gray zone across the 41 pilot flags.

## Appendix B: Reproducibility

Pipeline code (Apache-2.0): ingest → parse (GROBID) → L1 verify → L2 judge → arbiter → report.
Fixed random seeds; judging prompts, error taxonomy, and regression tests ship with the repository.
Anonymized dataset (citation-level judgments plus arbitration records, paper identifiers removed): CC BY, released with the report.

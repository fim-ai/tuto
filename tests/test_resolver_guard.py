"""Regression test for the hardened cited-paper resolver.

Six confirmed wrong-paper resolutions from the first audited sample must now be rejected,
while the correct resolutions (and noisy-but-right ones) must still pass. Run on the server:
    .venv/bin/python test_resolver_guard.py
"""
from tuto.l2.support_sample import _content, _novel_tokens, _title_consistent
from tuto.verify.normalize import title_tokens

# (case, resolved/wrong paper title, bib raw string, should_pass)
CASES = [
    # -- the six confirmed poisonings: must all be REJECTED now --
    ("photonic-lstm",
     "Photonic Long-Short Term Memory Neural Networks with Analog Memory",
     "Sepp Hochreiter and Jürgen Schmidhuber. 1997. Long short-term memory. Neural computation, 9(8):1735-1780.",
     False),
    ("nepali-w2v",
     "Efficient Estimation of Nepali Word Representations in Vector Space",
     "Tomás Mikolov, Kai Chen, Greg Corrado, and Jeffrey Dean. Efficient estimation of word representations in vector space. ICLR 2013 Workshop Track.",
     False),
    ("boltzmann-noise",
     "Learning Algorithm for Boltzmann Machines With Additive Weight and Bias Noise",
     "David H. Ackley, Geoffrey E. Hinton, and Terrence J. Sejnowski. 1985. A learning algorithm for boltzmann machines. Cognitive Science 9, 1 (1985), 147-169.",
     False),
    ("tkdp-vs-desprompt",
     "TKDP: Threefold Knowledge-Enriched Deep Prompt Tuning for Few-Shot Named Entity Recognition",
     "Zhiyuan Wen, Jiannong Cao, Yu Yang, Haoli Wang, Ruosong Yang, and Shuaiqi Liu. Desprompt: Personality-descriptive prompt tuning for few-shot personality recognition. Information Processing & Management, 60(5), 2023.",
     False),
    ("topological-ljp",
     "Legal Judgment Prediction via Topological Learning",
     "Jingyun Sun, Shaobin Huang, and Chi Wei. 2024. Chinese legal judgment prediction via knowledgeable prompt learning. Expert Syst. Appl., 238:122177.",
     False),
    ("compact-multitask",
     "Learning Compact Neural Networks with Deep Overparameterised Multitask Learning",
     "Ronan Collobert and Jason Weston. A unified architecture for natural language processing: Deep neural networks with multitask learning. ICML 2008, pages 160-167.",
     False),
    # -- correct resolutions: must all still PASS --
    ("exact-match",
     "Analyzing Polarization in Social Media: Method and Application to Tweets on 21 Mass Shootings",
     "Dorottya Demszky, Nikhil Garg, Rob Voigt, James Zou, et al. 2019. Analyzing Polarization in Social Media: Method and Application to Tweets on 21 Mass Shootings. NAACL 2019.",
     True),
    ("exact-short",
     "Long short-term memory",
     "Sepp Hochreiter and Jürgen Schmidhuber. 1997. Long short-term memory. Neural computation, 9(8):1735-1780.",
     True),
    ("case-punct-noise",
     "BLEU: a Method for Automatic Evaluation of Machine Translation",
     "Kishore Papineni, Salim Roukos, Todd Ward, and Wei-Jing Zhu. 2002. Bleu: a method for automatic evaluation of machine translation. ACL 2002.",
     True),
    ("truncated-parsed-title",
     "Know What You Don't Know: Unanswerable Questions for SQuAD",
     "Pranav Rajpurkar, Robin Jia, and Percy Liang. 2018. Know what you don't know: Unanswerable questions for SQuAD. ACL 2018.",
     True),
    ("hyphenation-split",  # PDF line-break hyphens must not read as novel tokens
     "SciClaims: An End-to-End Generative System for Biomedical Claim Analysis",
     "Raúl Ortega and Jose Manuel Gomez-Perez. 2025. Sci- claims: An end-to-end generative system for biomed- ical claim analysis. In EMNLP 2025 System Demonstrations, pages 141-154.",
     True),
]

failures = 0
for name, paper_title, raw, should_pass in CASES:
    # a truncated parsed title is common GROBID noise; derive it from the raw's first words
    ref = {"title": None, "raw": raw}
    # simulate the parsed title as the true title portion inside raw when available:
    # _title_consistent falls back to raw tokens when the parsed title is unusable.
    paper = {"title": paper_title}
    got = _title_consistent(paper, ref)
    novel = _novel_tokens(_content(title_tokens(paper_title)), ref)
    ok = got == should_pass
    failures += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  {name:<24} consistent={got} expected={should_pass} novel={sorted(novel)}")

print(f"\n{failures} failures / {len(CASES)} cases")
raise SystemExit(1 if failures else 0)

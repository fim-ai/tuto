"""Regression tests for reference-field extraction in parse/grobid_extract.

Covers the arXiv-id recovery and the arXiv-YYMM-as-year guard added after the GPT-3
(2005.14165) audit, where 7 CoRR "abs/…" references lost their arXiv id and one clean
2019 paper (Green AI) was stamped year 1907. Run on the server:
    .venv/bin/python tests/test_ref_extract.py
"""
from tuto.parse.grobid_extract import parse_references

TEI = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><back><listBibl>{body}</listBibl></back></text></TEI>"""


def bibl(xml_id, analytic="", monogr="", raw="", idno=""):
    return f"""<biblStruct xml:id="{xml_id}">
      <analytic>{analytic}</analytic>
      <monogr>{monogr}<imprint>{idno}</imprint></monogr>
      {f'<note type="raw_reference">{raw}</note>' if raw else ''}
    </biblStruct>"""


# (name, biblStruct, checks(ref)->list[(label, got, expected)])
def title_a(t):
    return f'<title level="a">{t}</title>'


def date(when="", text=""):
    return f'<date type="published"{f" when=\"{when}\"" if when else ""}>{text}</date>'


CASES = [
    (
        "corr-abs-recovers-id-and-year",
        bibl("b0", title_a("Green AI"),
             raw="Roy Schwartz, Jesse Dodge, Noah A. Smith, and Oren Etzioni. "
                 "Green AI. CoRR, abs/1907.10597, 2019.",
             idno=date(when="1907", text="1907")),
        lambda r: [("arxiv_id", r.arxiv_id, "1907.10597"), ("year", r.year, 2019)],
    ),
    (
        "bare-id-after-authors",
        bibl("b1", title_a("Model-agnostic meta-learning"),
             raw="Chelsea Finn, Pieter Abbeel, and Sergey Levine. Model-agnostic "
                 "meta-learning for fast adaptation of deep networks. arXiv:1703.03400, 2017.",
             idno=date(when="2017", text="2017")),
        lambda r: [("arxiv_id", r.arxiv_id, "1703.03400"), ("year", r.year, 2017)],
    ),
    (
        "clean-year-untouched",
        bibl("b2", title_a("Attention is all you need"),
             raw="Ashish Vaswani et al. Attention is all you need. NeurIPS 2017, pages 5998-6008.",
             idno=date(when="2017", text="2017")),
        lambda r: [("arxiv_id", r.arxiv_id, None), ("year", r.year, 2017)],
    ),
    (
        "doi-from-raw",
        bibl("b3", title_a("BERT"),
             raw="Jacob Devlin et al. BERT. NAACL 2019. https://doi.org/10.18653/v1/N19-1423",
             idno=date(when="2019", text="2019")),
        lambda r: [("doi", r.doi, "10.18653/v1/n19-1423"), ("year", r.year, 2019)],
    ),
    (
        "year-equals-recent-id-prefix-rederived",  # id 2005.x, GROBID misreads date as 2005
        bibl("b4", title_a("Language models are few-shot learners"),
             raw="Tom B. Brown et al. Language models are few-shot learners. "
                 "arXiv:2005.14165, 2020.",
             idno=date(when="2005", text="2005")),
        lambda r: [("arxiv_id", r.arxiv_id, "2005.14165"), ("year", r.year, 2020)],
    ),
    (
        "no-false-arxiv-on-page-range",  # "1234.5678" style numbers must not become ids
        bibl("b5", title_a("Some paper"),
             raw="A. Author. Some paper. Journal of Things 12(3), 1234.5678, 2015.",
             idno=date(when="2015", text="2015")),
        lambda r: [("arxiv_id", r.arxiv_id, None), ("year", r.year, 2015)],
    ),
]

failures = 0
for name, body, checks in CASES:
    refs = parse_references(TEI.format(body=body), "test")
    assert len(refs) == 1, f"{name}: expected 1 ref, got {len(refs)}"
    for label, got, exp in checks(refs[0]):
        ok = got == exp
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {name:<40} {label}={got!r} expected={exp!r}")

print(f"\n{failures} failures")
raise SystemExit(1 if failures else 0)

"""Independent reference counter, used to screen GROBID's recall.

We need to know whether GROBID silently drops bibliography entries. Asking GROBID is
circular, and no third party publishes reference lists for these papers, so we count
entries a second way and compare.

The trick is layout, not language: ACL bibliographies are set with a hanging indent --
an entry's first line sits flush at the column margin, its continuation lines are
indented. Counting flush-left lines in the references block counts entries, with no
model and no reading of the reference text. Being wrong in a *different* way than GROBID
is the point; where the two disagree is where a human looks.

This is a screening instrument, not a verdict. It has error in both directions, so the
acceptance number comes from hand-checking the sample it selects, not from its own count.
"""

from __future__ import annotations

import logging
import re
import statistics
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

logging.getLogger("pdfminer").setLevel(logging.ERROR)  # noisy FontBBox warnings on ACL PDFs

_HEADING_RE = re.compile(r"^\s*(references|bibliography)\s*$", re.IGNORECASE)
_LINE_TOL = 2.0  # pt: words within this vertical distance are on the same line
_MARGIN_TOL = 2.5  # pt: x0 within this of the column margin counts as flush-left
_HEADING_SIZE_DELTA = 0.4  # pt a section heading stands above the bibliography's body size
_HEADING_MAX_LEN = 80

# Where the bibliography ends is decided by font size, not by matching heading text.
# Text patterns misfired in both directions: an ignore-case rule read the sentence
# "a multi-agent framework..." as appendix heading "A M...", and a case-sensitive one read
# the *reference* "A Vaswani, N Shazeer..." the same way -- each truncating the list
# mid-way. Appendix headings are simply set larger than reference text, and no reference
# can imitate that.


@dataclass
class Line:
    col: int
    x0: float
    size: float
    text: str


@dataclass
class RefCount:
    paper_id: str
    layout_count: int
    ok: bool
    note: str = ""


def _flush(words: list[dict], col: int) -> Line:
    return Line(
        col=col,
        x0=min(w["x0"] for w in words),
        size=statistics.median([float(w.get("size") or 0.0) for w in words]),
        text=" ".join(w["text"] for w in words),
    )


def _group_lines(words: list[dict], col: int) -> list[Line]:
    words.sort(key=lambda w: (round(w["top"] / _LINE_TOL), w["x0"]))
    out: list[Line] = []
    current: list[dict] = []
    for w in words:
        if current and abs(w["top"] - current[0]["top"]) > _LINE_TOL:
            out.append(_flush(current, col))
            current = []
        current.append(w)
    if current:
        out.append(_flush(current, col))
    return out


def _lines(page: pdfplumber.page.Page) -> list[Line]:
    """Visual lines in reading order.

    ACL papers are two-column, so lines must be grouped within a column: grouping by y
    across the full page width welds the left column's line onto the right column's line
    at the same height, which destroys both the headings and the indent signal.
    """
    words = page.extract_words(use_text_flow=False, extra_attrs=["size"])
    if not words:
        return []
    mid = page.width / 2
    left = [w for w in words if w["x0"] < mid]
    right = [w for w in words if w["x0"] >= mid]
    return _group_lines(left, 0) + _group_lines(right, 1)


def _column_margin(x0s: list[float]) -> float | None:
    """The column's left margin: the leftmost x that recurs.

    Taking the minimum outright would let one stray glyph define the margin; requiring
    the position to repeat makes it the typeset margin rather than an artifact.
    """
    if not x0s:
        return None
    counts: dict[float, int] = {}
    for x in x0s:
        key = round(x * 2) / 2
        counts[key] = counts.get(key, 0) + 1
    recurring = sorted(x for x, n in counts.items() if n >= 2)
    return recurring[0] if recurring else min(x0s)


def _starts_like_reference(text: str) -> bool:
    """An author-year entry opens with a surname; appendix table rows mostly do not."""
    head = text.lstrip()
    return bool(head) and head[0].isalpha() and head[0].isupper()


def count_entries(pdf_path: Path) -> RefCount:
    paper_id = pdf_path.stem
    try:
        lines: list[Line] = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                lines.extend(_lines(page))

        start = None
        for i, line in enumerate(lines):
            if _HEADING_RE.match(line.text):
                start = i + 1  # last heading wins: an earlier mention is not the section
        if start is None:
            return RefCount(paper_id, 0, False, "no References heading found")

        block = lines[start:]
        if len(block) < 3:
            return RefCount(paper_id, 0, False, "references block is empty")

        body_size = statistics.median([ln.size for ln in block[:15]])
        for i, line in enumerate(block):
            if i <= 5:
                continue
            # Page numbers in the footer are set larger than reference text (10.9 vs 10.0)
            # and are short, so size alone reads them as section headings and cuts the
            # bibliography off at the first page break. A heading starts with a letter.
            head = line.text.lstrip()
            is_heading = (
                line.size > body_size + _HEADING_SIZE_DELTA
                and len(line.text) <= _HEADING_MAX_LEN
                and bool(head)
                and head[0].isalpha()
            )
            if is_heading:
                block = block[:i]
                break

        margins = {col: _column_margin([ln.x0 for ln in block if ln.col == col]) for col in (0, 1)}

        count = 0
        for line in block:
            margin = margins.get(line.col)
            if margin is None or len(line.text) < 3 or not _starts_like_reference(line.text):
                continue
            if abs(line.x0 - margin) <= _MARGIN_TOL:
                count += 1

        return RefCount(paper_id, count, True)
    except Exception as e:  # noqa: BLE001
        return RefCount(paper_id, 0, False, f"{type(e).__name__}: {e}")


def compare(grobid_counts: dict[str, int], layout_counts: dict[str, RefCount]) -> dict:
    rows = []
    for paper_id, layout in layout_counts.items():
        if not layout.ok or layout.layout_count == 0:
            continue
        g = grobid_counts.get(paper_id, 0)
        rows.append(
            {
                "paper_id": paper_id,
                "grobid": g,
                "layout": layout.layout_count,
                "diff": g - layout.layout_count,
                "ratio": g / layout.layout_count,
            }
        )
    if not rows:
        return {"papers": 0}

    agree = sum(1 for r in rows if abs(r["diff"]) <= 2)
    return {
        "papers": len(rows),
        "grobid_refs": sum(r["grobid"] for r in rows),
        "layout_refs": sum(r["layout"] for r in rows),
        "median_ratio": statistics.median([r["ratio"] for r in rows]),
        "agree_within_2": agree,
        "agree_within_2_pct": agree / len(rows),
        "rows": rows,
    }


# A plausible ACL reference-list length. Beyond this, a layout count is the counter
# running into an appendix, not a real bibliography -- those papers diagnose the layout
# counter, not GROBID, so they are kept out of the human sample.
_PLAUSIBLE_MAX = 160
_PROBE_MIN_DIFF = 3  # layout exceeding grobid by this much is a credible "GROBID missed a few"
_PROBE_MAX_DIFF = 40  # larger gaps at plausible lengths are rare; still credible, still checked


def select_qa_sample(
    rows: list[dict], n_random: int = 15, n_probe: int = 15, seed: int = 20260715
) -> tuple[list[dict], dict]:
    """The papers a human checks, plus a note on what was deliberately left out.

    Two groups with different jobs:

    - control: a uniform random sample. Hand-counting these gives an UNBIASED estimate of
      GROBID recall with a confidence interval -- this is the headline number.
    - probe: papers where the layout counter plausibly exceeds GROBID (it found a few more,
      and did not blow up into an appendix). These are the best leads on real GROBID
      misses, so hand-counting them bounds the worst case.

    Sorting by raw |diff| -- the earlier approach -- selected neither: it surfaced the
    papers where the LAYOUT counter exploded (503 "references"), which measures the wrong
    tool. Those are filtered out here and reported as a count, not hand-checked.
    """
    import random

    rng = random.Random(seed)

    control = rng.sample(rows, min(n_random, len(rows)))
    control_ids = {r["paper_id"] for r in control}

    probe_pool = [
        r
        for r in rows
        if r["paper_id"] not in control_ids
        and _PROBE_MIN_DIFF <= -r["diff"] <= _PROBE_MAX_DIFF  # layout > grobid, moderate gap
        and r["layout"] <= _PLAUSIBLE_MAX
    ]
    probe = sorted(probe_pool, key=lambda r: r["diff"])[:n_probe]  # widest credible gaps first

    excluded_blowup = sum(1 for r in rows if r["layout"] > _PLAUSIBLE_MAX)
    sample = [{**r, "reason": "control"} for r in control] + [
        {**r, "reason": "probe"} for r in probe
    ]
    diagnostics = {
        "control_n": len(control),
        "probe_n": len(probe),
        "probe_pool_n": len(probe_pool),
        "excluded_layout_blowup": excluded_blowup,
        "note": (
            f"{excluded_blowup} papers had layout count > {_PLAUSIBLE_MAX} "
            "(counter ran into appendix); excluded from human sample, not a GROBID signal."
        ),
    }
    return sample, diagnostics

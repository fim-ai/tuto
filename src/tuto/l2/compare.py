"""Side-by-side L2 support comparison across corpora (the contrast report).

Reads each corpus's l2_fulltext_report.json and lays the support distributions next to each
other. The whole design is a controlled comparison: accepted ACL (reviewed) vs never-reviewed
preprints, and pre-LLM vs post-LLM ACL, all measured by the identical pipeline, so a
difference in the not_supported / partial rate is attributable to the corpus, not the method.

The number that matters is the support distribution among CLAIM cites (nominal pointer cites
excluded). `unverifiable` is reported but is a method-ceiling (retrieval miss / abstract
fallback), not a finding, so the honest headline compares supported vs flagged (partial +
not_supported) across corpora.

Run: python -m tuto.l2.compare acl-2026 arxiv-cscl-2024 [acl-2018 ...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"
ORDER = ["supported", "partial", "unverifiable_from_text", "not_supported"]


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion -- honest CI on a small sample."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    center = (p + z * z / (2 * n)) / d
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (max(0.0, center - half), min(1.0, center + half))


def load(venue: str) -> dict | None:
    p = DATA / "runs" / venue / "l2_fulltext_report.json"
    if not p.exists():
        return None
    r = json.loads(p.read_text())
    return r


def main() -> None:
    venues = sys.argv[1:] or ["acl-2026", "arxiv-cscl-2024"]
    reports = {v: load(v) for v in venues}
    reports = {v: r for v, r in reports.items() if r}
    if not reports:
        print("no reports found for:", venues)
        return

    w = 26
    head = "metric".ljust(w) + "".join(v.ljust(20) for v in reports)
    print(head)
    print("-" * len(head))

    def row(label: str, fn):
        line = label.ljust(w)
        for v, r in reports.items():
            line += str(fn(r)).ljust(20)
        print(line)

    row("claim cites (n)", lambda r: r["claim_cites"])
    row("nominal cites excluded", lambda r: r.get("cite_type_mix", {}).get("nominal", 0))
    row("source fulltext/abstract", lambda r: f"{r['source_mix'].get('fulltext',0)}/{r['source_mix'].get('abstract',0)}")
    print()
    for k in ORDER:
        def fn(r, k=k):
            n = r["claim_cites"]
            c = r["distribution_claim"].get(k, 0)
            return f"{c/max(n,1):.1%} ({c})"
        row(k, fn)
    print()

    # The headline: flagged = partial + not_supported, with a Wilson CI, per corpus.
    def flagged(r):
        n = r["claim_cites"]
        k = r["distribution_claim"].get("partial", 0) + r["distribution_claim"].get("not_supported", 0)
        lo, hi = _wilson(k, n)
        return f"{k/max(n,1):.1%} [{lo:.1%},{hi:.1%}]"
    row("FLAGGED (partial+not_sup)", flagged)

    def notsup(r):
        n = r["claim_cites"]
        k = r["distribution_claim"].get("not_supported", 0)
        lo, hi = _wilson(k, n)
        return f"{k/max(n,1):.1%} [{lo:.1%},{hi:.1%}]"
    row("  of which not_supported", notsup)

    print("\nNote: unverifiable is a method ceiling (retrieval miss / abstract fallback), not a finding.")
    print("not_supported needs human validation before publication (some are pipeline artifacts).")


if __name__ == "__main__":
    main()

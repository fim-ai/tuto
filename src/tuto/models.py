"""Core records passed between pipeline stages. Every stage reads and writes JSONL."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator


@dataclass
class PaperRecord:
    """A paper under audit. Produced by an ingest adapter, consumed by parse."""

    paper_id: str  # venue-native id, e.g. "2026.acl-long.1"
    title: str
    authors: list[str]
    year: int
    venue: str  # normalized run key, e.g. "acl-2026"
    volume: str  # e.g. "2026.acl-long"
    pdf_url: str
    doi: str | None = None
    url: str | None = None
    pages: str | None = None
    bibkey: str | None = None

    @property
    def pdf_name(self) -> str:
        return f"{self.paper_id}.pdf"


@dataclass
class Reference:
    """One bibliography entry extracted from a paper. The unit of audit."""

    ref_id: str  # f"{paper_id}#{index}"
    paper_id: str
    index: int
    raw: str  # verbatim reference string as printed
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    extractor: str = "grobid"


@dataclass
class Context:
    """Where a reference is cited in the body. Raw material for L2; not used by L1."""

    paper_id: str
    ref_id: str
    marker: str  # the inline citation as printed, e.g. "(Smith et al., 2020)"
    sentence: str
    section: str | None = None


def write_jsonl(path: Path, records: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            payload = asdict(r) if hasattr(r, "__dataclass_fields__") else r
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)

"""GROBID extraction: PDF -> bibliography entries + citation contexts.

ACL PDFs are born-digital LaTeX output with a full text layer, so no OCR is involved.
GROBID's CRF models are trained on exactly this kind of document. If the recall gate in
parse/refcount_check.py fails, that is when a layout model (MinerU) earns its place --
not before.

One processFulltextDocument call yields both the reference list and the in-body citation
markers, so we make a single pass per PDF.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # noqa: N817 - element types only; parsing goes through defusedxml
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
from defusedxml.ElementTree import fromstring as safe_fromstring
from tqdm import tqdm

from tuto.models import Context, Reference

TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}
_WS_RE = re.compile(r"\s+")
_ARXIV_RE = re.compile(r"arxiv[.:\s]*(\d{4}\.\d{4,5})", re.IGNORECASE)


class GrobidError(RuntimeError):
    pass


class GrobidClient:
    def __init__(self, base_url: str = "http://localhost:8070", timeout: float = 180):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=timeout)

    def alive(self) -> bool:
        try:
            r = self.client.get(f"{self.base_url}/api/isalive", timeout=10)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def _post(self, endpoint: str, pdf_path: Path, data: dict[str, str]) -> str:
        with pdf_path.open("rb") as f:
            r = self.client.post(
                f"{self.base_url}/api/{endpoint}",
                files={"input": (pdf_path.name, f, "application/pdf")},
                data=data,
            )
        if r.status_code != 200:
            raise GrobidError(f"grobid {endpoint} returned {r.status_code} for {pdf_path.name}")
        return r.text

    def references(self, pdf_path: Path) -> str:
        """The authoritative reference list.

        processFulltextDocument silently truncates the bibliography on some papers
        (2026.acl-long.120: 13 entries out of 41). The dedicated endpoint recovers them
        and never did worse in testing, so the audit list comes from here.
        consolidateCitations stays off: resolving references against a third party is
        the very thing we are measuring, so we must not let GROBID do it for us.
        """
        return self._post(
            "processReferences", pdf_path, {"consolidateCitations": "0", "includeRawCitations": "1"}
        )

    def fulltext(self, pdf_path: Path) -> str:
        """Body text, used only to harvest citation contexts (raw material for L2)."""
        return self._post(
            "processFulltextDocument",
            pdf_path,
            {
                "consolidateCitations": "0",
                "includeRawCitations": "1",
                "segmentSentences": "1",
            },
        )

    def close(self) -> None:
        self.client.close()


def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return _WS_RE.sub(" ", "".join(el.itertext())).strip()


def _parse_biblstruct(bs: ET.Element, paper_id: str, index: int) -> Reference:
    raw = _text(bs.find("tei:note[@type='raw_reference']", TEI_NS))

    title = _text(bs.find(".//tei:title[@level='a']", TEI_NS))
    if not title:  # books and reports carry the title at monogr level
        title = _text(bs.find(".//tei:monogr/tei:title[@level='m']", TEI_NS))

    authors = []
    for pers in bs.findall(".//tei:author/tei:persName", TEI_NS):
        parts = [_text(p) for p in pers]
        name = " ".join(p for p in parts if p)
        if name:
            authors.append(name)

    year = None
    date = bs.find(".//tei:imprint/tei:date", TEI_NS)
    if date is not None:
        m = re.search(r"(19|20)\d{2}", date.get("when", "") or _text(date))
        if m:
            year = int(m.group(0))

    venue = _text(bs.find(".//tei:monogr/tei:title[@level='j']", TEI_NS)) or _text(
        bs.find(".//tei:monogr/tei:title[@level='m']", TEI_NS)
    )
    if venue == title:
        venue = ""

    doi = None
    arxiv_id = None
    for idno in bs.findall(".//tei:idno", TEI_NS):
        kind = (idno.get("type") or "").lower()
        val = _text(idno)
        if kind == "doi":
            doi = val.lower()
        elif kind == "arxiv":
            arxiv_id = val
    if not arxiv_id and raw:
        m = _ARXIV_RE.search(raw)
        if m:
            arxiv_id = m.group(1)

    return Reference(
        ref_id=f"{paper_id}#{index}",
        paper_id=paper_id,
        index=index,
        raw=raw,
        title=title or None,
        authors=authors,
        year=year,
        venue=venue or None,
        doi=doi,
        arxiv_id=arxiv_id,
    )


def _fingerprint(raw: str) -> str:
    return re.sub(r"[^a-z0-9]", "", raw.lower())[:70]


def parse_references(tei: str, paper_id: str) -> list[Reference]:
    root = safe_fromstring(tei)
    return [
        _parse_biblstruct(bs, paper_id, i)
        for i, bs in enumerate(root.findall(".//tei:listBibl/tei:biblStruct", TEI_NS))
    ]


def parse_contexts(tei_full: str, paper_id: str, refs: list[Reference]) -> list[Context]:
    """Citation contexts from the body, linked back to the authoritative reference list.

    The body's bibr targets index the fulltext run's own bibliography, which is not the
    list we audit, so we bridge the two by fingerprinting the raw reference string. A
    context whose reference we cannot bridge is still worth keeping.
    """
    root = safe_fromstring(tei_full)

    by_fingerprint = {_fingerprint(r.raw): r.ref_id for r in refs if r.raw}
    target_to_ref: dict[str, str | None] = {}
    for bs in root.findall(".//tei:listBibl/tei:biblStruct", TEI_NS):
        xml_id = bs.get("{http://www.w3.org/XML/1998/namespace}id")
        if not xml_id:
            continue
        raw = _text(bs.find("tei:note[@type='raw_reference']", TEI_NS))
        target_to_ref[f"#{xml_id}"] = by_fingerprint.get(_fingerprint(raw)) if raw else None

    contexts: list[Context] = []
    body = root.find(".//tei:text/tei:body", TEI_NS)
    if body is None:
        return contexts

    for div in body.findall(".//tei:div", TEI_NS):
        section = _text(div.find("tei:head", TEI_NS)) or None
        for sentence in div.findall(".//tei:s", TEI_NS):
            markers = sentence.findall("tei:ref[@type='bibr']", TEI_NS)
            if not markers:
                continue
            text = _text(sentence)
            for marker in markers:
                ref_id = target_to_ref.get(marker.get("target", ""))
                if ref_id is None:
                    continue
                contexts.append(
                    Context(
                        paper_id=paper_id,
                        ref_id=ref_id,
                        marker=_text(marker),
                        sentence=text,
                        section=section,
                    )
                )
    return contexts


def process_corpus(
    pdf_dir: Path,
    tei_dir: Path,
    grobid_url: str,
    workers: int = 8,
    limit: int | None = None,
) -> tuple[list[Reference], list[Context], list[dict[str, str]]]:
    """Run GROBID over every PDF. TEI is cached on disk so reparsing costs nothing."""
    client = GrobidClient(grobid_url)
    if not client.alive():
        raise GrobidError(f"no GROBID at {grobid_url}")
    tei_dir.mkdir(parents=True, exist_ok=True)

    refs_dir = tei_dir / "refs"
    full_dir = tei_dir / "full"
    refs_dir.mkdir(parents=True, exist_ok=True)
    full_dir.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if limit:
        pdfs = pdfs[:limit]
    all_refs: list[Reference] = []
    all_contexts: list[Context] = []
    failures: list[dict[str, str]] = []

    def cached(path: Path, fetch) -> str:
        if path.exists() and path.stat().st_size > 500:
            return path.read_text(encoding="utf-8")
        tei = fetch()
        path.write_text(tei, encoding="utf-8")
        return tei

    def run(pdf: Path) -> tuple[str, list[Reference], list[Context], str | None]:
        paper_id = pdf.stem
        try:
            tei_refs = cached(refs_dir / f"{paper_id}.tei.xml", lambda: client.references(pdf))
            refs = parse_references(tei_refs, paper_id)
            tei_full = cached(full_dir / f"{paper_id}.tei.xml", lambda: client.fulltext(pdf))
            contexts = parse_contexts(tei_full, paper_id, refs)
            return paper_id, refs, contexts, None
        except ET.ParseError as e:
            return paper_id, [], [], f"TEI parse: {e}"
        except Exception as e:  # noqa: BLE001
            return paper_id, [], [], str(e)

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run, p) for p in pdfs]
            for fut in tqdm(as_completed(futures), total=len(pdfs), desc="grobid", unit="pdf"):
                paper_id, refs, contexts, err = fut.result()
                if err:
                    failures.append({"paper_id": paper_id, "error": err})
                    continue
                all_refs.extend(refs)
                all_contexts.extend(contexts)
    finally:
        client.close()

    all_refs.sort(key=lambda r: (r.paper_id, r.index))
    return all_refs, all_contexts, failures

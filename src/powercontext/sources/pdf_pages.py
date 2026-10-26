# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Complete PDF page evidence and offline, citation-preserving retrieval."""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
from collections import Counter
from collections.abc import Sequence
from itertools import pairwise
from typing import Annotated, Protocol

from pydantic import BaseModel, Field, field_validator
from pypdf import PdfReader

from powercontext.builtin.artifacts.memory.models import MemoryCitation, MemoryHit
from powercontext.builtin.runtime.models import (
    GetMemoryEntryRequest,
    MemoryEntriesPage,
    MemoryEntryRecord,
    MemorySearchPage,
    SearchMemoryRequest,
)
from powercontext.sources.definitions import SourceDefinition, SourceProjection
from powercontext.sources.models import Source, SourceMaterialization, SourceRef
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


class PDFDocumentInput(BaseModel):
    """Original complete PDF bytes, never a pre-extracted text document."""

    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    """One physical page in an exact PDF revision, including blank pages."""

    document_id: str
    document_sha256: str
    page_number: Annotated[int, Field(ge=1, strict=True)]
    text: str

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        _validate_document_id(value)
        return value

    @field_validator("document_sha256")
    @classmethod
    def validate_document_digest(cls, value: str) -> str:
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("PDF document_sha256 must be a lowercase SHA-256 digest")  # noqa: TRY003
        return value


def _validate_document_id(value: str) -> None:
    if not value.strip():
        raise ValueError("PDF document_id must not be blank")  # noqa: TRY003


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Extract every page atomically using the shared, locked PDF parser."""

    _validate_document_id(value.document_id)
    digest = hashlib.sha256(value.content).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(value.content), strict=True)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF cannot be opened without a user password")  # noqa: TRY003, TRY301
        return tuple(
            PDFPageInput(
                document_id=value.document_id,
                document_sha256=digest,
                page_number=number,
                text=page.extract_text() or "",
            )
            for number, page in enumerate(reader.pages, start=1)
        )
    except Exception as error:
        raise ValueError("PDF could not be completely parsed") from error  # noqa: TRY003


class PDFPageSource(Source):
    """Captured page text whose identity includes the original byte digest."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageTextEvidenceProjection:
    """Publish revision and physical page location alongside the full text."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class: type[PDFPageSource] = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type="pdf-page",
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


class PDFPageSourceDefinition:
    """Bind full page captures to stable, bounded revision-specific identities."""

    input_class: type[PDFPageInput] = PDFPageInput
    name = "pdf-page"
    version = "1"
    source_class: type[PDFPageSource] = PDFPageSource

    def __init__(self) -> None:
        self.projections: tuple[SourceProjection[PDFPageSource], ...] = (PDFPageTextEvidenceProjection(),)

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        identity = json.dumps([value.document_id, value.document_sha256, value.page_number], ensure_ascii=False)
        return PDFPageSource(
            name=f"pdf:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}",
            materialization=SourceMaterialization.CAPTURED,
            **value.model_dump(),
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput.model_validate(source.model_dump())


PDF_PAGE_SOURCE_DEFINITION: SourceDefinition[PDFPageInput, PDFPageSource, PDFPageInput] = PDFPageSourceDefinition()


class _ScopedPDFMemory(Protocol):
    async def list(self) -> MemoryEntriesPage: ...

    async def search(self, request: SearchMemoryRequest, /) -> MemorySearchPage: ...

    async def get(self, request: GetMemoryEntryRequest, /) -> MemoryEntryRecord: ...


_STOPWORDS = frozenset(
    "a an the and or of to in on at by for from with as is are was were be been being "  # noqa: SIM905
    "what which who how why when where do does did has have had can could should would "
    "i we you it its our your this that these those any all if then than not no "
    "please answer respond response question give state define calculate assuming perspective "
    "investment analyst only use using used details shown within relying provided based "
    "according assuming reported report reports directly outlined explicitly quantity "
    "materially important ongoing primarily respond stated state following whether "
    "fy fiscal year annual report form usd million millions billion billions".split()
)

# General financial vocabulary, independent of any report, question or answer.
_FINANCIAL_ALIASES = (
    (
        frozenset({"geographies", "geography", "geographic", "countries", "regions"}),
        ("geographic", "countries", "regions", "international", "segments", "markets"),
    ),
    (
        frozenset({"legal", "litigation", "lawsuit", "lawsuits", "battles"}),
        ("litigation", "legal", "proceedings", "contingencies", "claims"),
    ),
    (frozenset({"restructuring", "reorganization"}), ("restructuring", "charges", "costs", "impairment")),
    (frozenset({"ebitda"}), ("operating", "income", "depreciation", "amortization", "cash", "flows")),
    (frozenset({"capex", "capital"}), ("capital", "expenditures", "additions", "property", "equipment")),
    (frozenset({"income", "earnings", "profit", "loss"}), ("income", "earnings", "operations", "statements")),
    (frozenset({"cash", "flows", "flow"}), ("cash", "flows", "operating", "investing", "activities")),
    (frozenset({"revenue", "revenues", "sales", "margin"}), ("revenue", "sales", "net", "income")),
    (frozenset({"debt", "borrowings", "leverage"}), ("debt", "borrowings", "maturities", "interest")),
    (frozenset({"assets", "liabilities", "equity", "balance"}), ("balance", "assets", "liabilities", "equity")),
    (frozenset({"dividend", "dividends", "repurchases", "buybacks"}), ("dividends", "repurchases", "stock", "cash")),
    (frozenset({"tax", "taxes", "taxation"}), ("tax", "taxes", "effective", "rate")),
    (frozenset({"liquidity", "current", "quick", "working"}), ("current", "assets", "liabilities", "balance")),
    (frozenset({"turnover", "inventory", "inventories"}), ("inventory", "inventories", "cost", "sales", "balance")),
    (frozenset({"return", "roa", "roe"}), ("net", "income", "assets", "equity", "balance")),
)


def _terms(value: str) -> list[str]:
    return re.findall(r"[a-z]+|\d+", value.lower())


def _stem(value: str) -> str:
    if len(value) > 5 and value.endswith("ies"):
        value = value[:-3] + "y"
    elif len(value) > 5 and value.endswith("ing"):
        value = value[:-3]
    elif len(value) > 4 and value.endswith("ed"):
        value = value[:-2]
    elif len(value) > 4 and value.endswith("s"):
        value = value[:-1]
    return value[:-1] if len(value) > 4 and value.endswith("e") else value


_SECTION_HINTS = {
    "income": (
        "income earnings operations statements",
        re.compile(
            r"^(?:(?:consolidated|combined|condensed|unaudited) )*(?:statements? (?:of )?(?:income|earnings|operations)|(?:income|earnings) statements?)"
        ),
    ),
    "cash": (
        "cash flows activities",
        re.compile(r"^(?:(?:consolidated|combined|condensed|unaudited) )*statements? (?:of )?cash flows?"),
    ),
    "balance": (
        "balance sheets assets liabilities",
        re.compile(
            r"^(?:(?:consolidated|combined|condensed|unaudited) )*(?:balance sheets?|statements? of financial position)"
        ),
    ),
    "legal": (
        "legal proceedings contingencies",
        re.compile(
            r"^(?:(?:item |note )?\d+[a-z]?\.? )?(?:legal proceedings|commitments and contingencies|contingencies)"
        ),
    ),
    "geography": (
        "geographic segments divisions",
        re.compile(r"^(?:our|reportable|geographic|business) (?:segments|divisions)"),
    ),
}


def _document_intents(question: str) -> frozenset[str]:
    terms = set(_terms(question))
    intents = set()
    if terms & {"ebitda", "income", "earnings", "profit", "margin"}:
        intents.add("income")
    if terms & {"ebitda", "cash", "capex", "depreciation", "amortization"}:
        intents.add("cash")
    if terms & {"balance", "assets", "liabilities", "equity", "liquidity", "working", "quick", "current", "roa", "roe"}:
        intents.add("balance")
    if terms & {"legal", "litigation", "lawsuit", "lawsuits", "battles"}:
        intents.add("legal")
    if terms & {"geographies", "geographic", "geography", "countries", "regions"}:
        intents.add("geography")
    return frozenset(intents)


def _heading_score(text: str, intents: frozenset[str]) -> float:
    lines = [" ".join(line.lower().split()) for line in text.partition("\n")[2].splitlines() if line.strip()]
    # Match real heading starts, not a statement mentioned inside a paragraph.
    # Trailing page numbers identify most table-of-contents / exhibit references.
    headings = [line for line in lines if not re.search(r"\d\s*$", line)]
    headings.extend(f"{left} {right}" for left, right in pairwise(lines) if not re.search(r"\d\s*$", right))
    # A financial-statement index can repeat every title without containing the
    # corresponding table. Require actual non-year figures for table headings.
    figures = sum(1 for token in _terms(text) if token.isdigit() and not re.fullmatch(r"(?:19|20)\d{2}", token))
    return sum(
        16.0
        for intent in intents
        if (intent not in {"income", "cash", "balance"} or figures >= 12)
        and any(_SECTION_HINTS[intent][1].search(line) for line in headings)
    )


def _query_plan(question: str) -> tuple[tuple[str, ...], dict[str, float], tuple[str, ...]]:
    words = _terms(question)
    salient = tuple(dict.fromkeys(word for word in words if len(word) > 1 and word not in _STOPWORDS))
    original = set(words)
    aliases = tuple(
        dict.fromkeys(alias for triggers, group in _FINANCIAL_ALIASES if triggers & original for alias in group)
    )
    weights: dict[str, float] = {}
    for term in aliases:
        weights[_stem(term)] = 0.5
    for term in salient:
        weights[_stem(term)] = 1.0
    # Short lexical subqueries avoid natural-language admission floors discarding
    # pages that express just one of several necessary financial statement facts.
    groups = [word for word in salient if not word.isdigit()][:16]
    expansions = [word for word in aliases if word not in salient][:16]
    queries = [question[:2000]]
    queries.extend(" ".join(groups[index : index + 2]) for index in range(0, len(groups), 2))
    queries.extend(" ".join(expansions[index : index + 2]) for index in range(0, len(expansions), 2))
    queries.extend(_SECTION_HINTS[intent][0] for intent in sorted(_document_intents(question)))
    return tuple(dict.fromkeys(query for query in queries if query.strip())), weights, salient


def _rank_pages(
    hits: Sequence[MemoryHit],
    weights: dict[str, float],
    salient: tuple[str, ...],
    votes: dict[tuple[str, int, str, str], float],
    intents: frozenset[str],
) -> list[MemoryHit]:
    tokens = [tuple(_stem(term) for term in _terms(hit.text.partition("\n")[2])) for hit in hits]
    counters = [Counter(row) for row in tokens]
    frequencies: Counter[str] = Counter()
    for counter in counters:
        frequencies.update(counter.keys())
    average_length = sum(map(len, tokens)) / max(1, len(tokens))
    phrases = tuple(zip((_stem(term) for term in salient[:-1]), (_stem(term) for term in salient[1:]), strict=True))
    query_words = set(salient)

    def score(index: int) -> float:
        hit, counter = hits[index], counters[index]
        length = len(tokens[index])
        total = 0.0
        for term, weight in weights.items():
            count = counter.get(term, 0)
            if count:
                rarity = math.log1p((len(hits) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
                total += weight * rarity * count * 2.2 / (count + 1.2 * (0.35 + 0.65 * length / max(1, average_length)))
        page_pairs = set(zip(tokens[index][:-1], tokens[index][1:], strict=True))
        total += sum(0.75 for phrase in phrases if phrase in page_pairs)
        title_terms = set(_terms(hit.text.partition("\n")[0]))
        company = {term for term in title_terms if term.isalpha() and len(term) > 2 and term not in _STOPWORDS}
        total += 4.0 * len(company & query_words)
        years = {term for term in title_terms if re.fullmatch(r"(?:19|20)\d{2}", term)}
        requested_years = {term for term in query_words if re.fullmatch(r"(?:19|20)\d{2}", term)}
        if years and requested_years:
            total += 3.0 if years & requested_years else -3.0
        return total + 5.0 * votes.get(_hit_key(hit), 0.0) + _heading_score(hit.text, intents)

    return [hits[index] for index in sorted(range(len(hits)), key=lambda index: (-score(index), _hit_key(hits[index])))]


def _hit_key(hit: MemoryHit) -> tuple[str, int, str, str]:
    return hit.memory_ref.artifact_id, hit.memory_ref.revision, hit.entry_id, hit.entry_version_id


def _company_candidates(hits: Sequence[MemoryHit], question: str) -> tuple[MemoryHit, ...]:
    """Prefer explicitly named report issuers when several reports share a scope."""

    compact_question = re.sub(r"[^a-z0-9]", "", question.lower())
    matched: list[MemoryHit] = []
    for hit in hits:
        document_id = hit.text.partition("\n")[0]
        prefix = re.split(r"(?:19|20)\d{2}", document_id, maxsplit=1)[0]
        issuer = re.sub(r"[^a-z0-9]", "", prefix.lower())
        if len(issuer) >= 3 and issuer in compact_question:
            matched.append(hit)
    return tuple(matched) if matched else tuple(hits)


def _single_page_id(references: Sequence[SourceRef]) -> str | None:
    if not references or any(reference.source_type != "pdf-page" for reference in references):
        return None
    identities = {reference.source_id for reference in references}
    return next(iter(identities)) if len(identities) == 1 else None


def _page_candidates(records: MemoryEntriesPage) -> dict[tuple[str, int, str, str], MemoryHit]:
    candidates = {}
    for record in records.entries:
        if _single_page_id(record.entry.sources) is None:
            continue
        hit = MemoryHit(
            memory_ref=record.memory_ref,
            entry_id=record.entry.entry_id,
            entry_version_id=record.entry.entry_version_id,
            text=record.entry.text,
            score=0.0,
            matched_by=(),
        )
        candidates[_hit_key(hit)] = hit
    return candidates


async def search_pdf_pages(memory: _ScopedPDFMemory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Search real scoped Memory, then rank and verify distinct exact page citations."""

    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValueError("PDF search limit must be an integer")  # noqa: TRY003, TRY004
    if limit <= 0 or not question.strip():
        return ()
    queries, weights, salient = _query_plan(question)
    votes: dict[tuple[str, int, str, str], float] = {}
    # The public scoped list supplies the complete active corpus. A bounded FTS
    # result is a useful ranking signal, but is not a complete page inventory:
    # admission floors and lexical truncation can hide the only evidence page.
    # Keep each citation issued by Memory; never derive it from page metadata.
    candidates = _page_candidates(await memory.list())
    for query in queries:
        page = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=max(80, min(200, limit * 6))))
        for rank, hit in enumerate(page.hits, start=1):
            key = _hit_key(hit)
            # Search may race a newer revision. Only rank the single snapshot
            # whose active, page-backed entries were returned by list().
            if key not in candidates:
                continue
            votes[key] = votes.get(key, 0.0) + 1.0 / (60 + rank)
    results: list[MemoryCitation] = []
    seen_pages: set[str] = set()
    for hit in _rank_pages(
        _company_candidates(tuple(candidates.values()), question), weights, salient, votes, _document_intents(question)
    ):
        citation = MemoryCitation(
            memory_ref=hit.memory_ref, entry_id=hit.entry_id, entry_version_id=hit.entry_version_id
        )
        record = await memory.get(GetMemoryEntryRequest(citation=citation))
        page_id = _single_page_id(record.entry.sources)
        if page_id is None:
            continue
        if page_id in seen_pages:
            continue
        seen_pages.add(page_id)
        results.append(citation)
        if len(results) == limit:
            break
    return tuple(results)


__all__ = [
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "parse_pdf_document",
    "search_pdf_pages",
]

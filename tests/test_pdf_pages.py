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

"""PDF revision fidelity and real scoped evidence search boundaries."""

from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.artifacts.memory.errors import InvalidMemoryCandidateError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    SubmitSourceObservation,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    SourceDefinitionRegistry,
    SourceMaterialization,
    manifest_for_definition,
    pdf_pages,
    project_source_for_transport,
)
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    PDFPageInput,
    parse_pdf_document,
    search_pdf_pages,
)


def _pdf(*texts: str, title: str = "Original", password: str | None = None) -> bytes:
    writer = PdfWriter()
    writer.add_metadata({"/Title": title})
    for text in texts:
        page = writer.add_blank_page(width=400, height=250)
        if text:
            font = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
            })
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 30 200 Td ({escaped}) Tj ET".encode())
            page[NameObject("/Contents")] = writer._add_object(stream)
    if password is not None:
        writer.encrypt(user_password=password, owner_password="owner")  # noqa: S106
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


async def test_all_physical_pages_and_exact_digest_survive_resolve_read_and_projection() -> None:
    raw = _pdf("First page 123.45", "", "Third page (a table) 0007")
    pages = parse_pdf_document(PDFDocumentInput(document_id=" 财报:公司/2024 ", content=raw))
    assert len(pages) == 3 and [page.page_number for page in pages] == [1, 2, 3]
    assert pages[1].text == ""
    assert "123.45" in pages[0].text and "0007" in pages[2].text
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    identities = set()
    for page in pages:
        assert page.document_sha256 == hashlib.sha256(raw).hexdigest()
        source = await registry.resolve(page)
        assert source.materialization == SourceMaterialization.CAPTURED
        assert len(source.name) <= 256
        assert await registry.read(source) == page
        identities.add(source.name)
        evidence = registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY)
        assert isinstance(evidence, dict)
        assert evidence["content"] == page.document_id + "\n" + page.text
        assert evidence["metadata"] == {
            "document_id": page.document_id,
            "document_sha256": hashlib.sha256(raw).hexdigest(),
            "page_number": page.page_number,
        }
    assert len(identities) == 3


@pytest.mark.parametrize("document_id", ["", " ", "\t\n"])
def test_blank_document_identity_is_invalid(document_id: str) -> None:
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id=document_id, content=_pdf("body")))


@pytest.mark.parametrize("raw", [b"", b"%PDF-1.7", b"<html>not PDF</html>", _pdf("complete")[:120]])
def test_invalid_complete_inputs_fail_atomically(raw: bytes) -> None:
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id="broken", content=raw))


def test_passwordless_permissions_are_accepted_but_password_required_files_fail() -> None:
    assert len(parse_pdf_document(PDFDocumentInput(document_id="open", content=_pdf("readable", password="")))) == 1
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id="locked", content=_pdf("secret", password="required")))  # noqa: S106


def test_split_numbers_are_joined_only_when_the_same_page_confirms_them(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preserve plain page content while repairing the observed pypdf line break."""
    plain = "Statement of Incom\n \ne. Year 201\n5, prior year 201\n5. Note 1\n1\n. Table cells 20\n14."
    layout = "Statement of Income. Year 2015, prior year 201 5. Note 11. Table cells 20 14."

    class Page:
        def extract_text(self, **kwargs: object) -> str:
            return layout if kwargs.get("extraction_mode") == "layout" else plain

    class Reader:
        is_encrypted = False
        pages = (Page(),)

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    monkeypatch.setattr(pdf_pages, "PdfReader", Reader)
    page = parse_pdf_document(PDFDocumentInput(document_id="issuer", content=_pdf("valid")))[0]
    assert re.findall(r"[a-z]+|\d+", page.text.lower()) == re.findall(
        r"[a-z]+|\d+", "Statement of Income. Year 2015, prior year 201 5. Note 11. Table cells 20 14.".lower()
    )


def test_split_heading_letters_use_layout_and_document_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """Handle the separate last glyphs observed in real report headings."""

    class Page:
        def __init__(self, plain: str, layout: str) -> None:
            self.plain, self.layout = plain, layout

        def extract_text(self, **kwargs: object) -> str:
            return self.layout if kwargs.get("extraction_mode") == "layout" else self.plain

    class Reader:
        is_encrypted = False
        pages = (
            Page("Statement of Incom\n \ne. Net income 400.", "Statement of Incom e. Net income 400."),
            Page("Balance Shee\n \nt.", "Balance Shee t."),
            Page("Notes about the balance sheet.", "Notes about the balance sheet."),
        )

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    monkeypatch.setattr(pdf_pages, "PdfReader", Reader)
    pages = parse_pdf_document(PDFDocumentInput(document_id="issuer", content=_pdf("valid")))
    assert pages[0].text == "Statement of Income. Net income 400."
    assert pages[1].text == "Balance Sheet."
    assert pages[2].text == "Notes about the balance sheet."


@pytest.mark.parametrize(
    "replacement",
    [
        {"page_number": 0},
        {"page_number": -1},
        {"page_number": True},
        {"document_sha256": "A" * 64},
        {"document_sha256": "x"},
    ],
)
def test_page_inputs_reject_ambiguous_locations(replacement: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        PDFPageInput.model_validate(
            {"document_id": "doc", "document_sha256": "0" * 64, "page_number": 1, "text": ""} | replacement
        )


async def test_identity_includes_document_revision_and_physical_page() -> None:
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    versions = [
        parse_pdf_document(PDFDocumentInput(document_id="issuer", content=_pdf("first", "second", title=title)))
        for title in ("old", "revised")
    ]
    names = {(await registry.resolve(page)).name for pages in versions for page in pages}
    assert len(names) == 4


class _PagePipeline:
    async def extract(self, request, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(kind=kind, text=item.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"], sources=(item,))
            for item in request.sources
            for kind in ("pdf-evidence", "duplicate-page")
        )


async def test_scoped_search_is_revision_exact_distinct_and_retrieves_financial_paraphrases(tmp_path: Path) -> None:
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    documents = {
        "ALPHA_2024_10K": _pdf(
            "Geographic segments: North America and Europe are the principal markets.",
            "Consolidated statements of cash flows. Capital expenditures 123. Operating cash flows 456.",
            "Legal proceedings: litigation claims and contingencies remain pending.",
            "Consolidated Statements of Income. Net revenue 500. Operating income 70.",
        ),
        "BETA_2024_10K": _pdf("Geographic regions and international segments. Legal proceedings and cash flows."),
    }
    observations = []
    expected = {}
    for document_id, raw in documents.items():
        for page in parse_pdf_document(PDFDocumentInput(document_id=document_id, content=raw)):
            source = await registry.resolve(page)
            observations.append(project_source_for_transport(registry, source))
            expected[source.name] = page
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'pdf.db'}"))
    async with open_builtin_runtime(config) as runtime:
        assert runtime.scopes is not None
        scope = await runtime.scopes.create(ScopeDraft(title="Reports", summary="Pages", idempotency_key="reports"))
        other = await runtime.scopes.create(ScopeDraft(title="Other", summary="Isolated", idempotency_key="other"))
        await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
        for observation in observations:
            await runtime.ingestion.submit(SubmitSourceObservation(scope_id=scope.scope_id, observation=observation))
        await runtime.ingestion.submit(SubmitSourceObservation(scope_id=scope.scope_id, observation=observations[0]))
        await runtime.ingestion.submit(SubmitSourceObservation(scope_id=other.scope_id, observation=observations[-1]))
    async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
        memory = runtime.memory.for_scope(scope.scope_id)
        assert (await memory.flush()).processed
        assert (await runtime.memory.for_scope(other.scope_id).flush()).processed
    async with open_builtin_runtime(config) as runtime:
        memory = runtime.memory.for_scope(scope.scope_id)
        citations = await search_pdf_pages(memory, "What geographies does Alpha operate in during FY2024?", limit=1)
        assert len(citations) == 1
        page = (await memory.get(GetMemoryEntryRequest(citation=citations[0]))).entry
        assert expected[page.sources[0].source_id].document_id == "ALPHA_2024_10K"
        assert expected[page.sources[0].source_id].page_number == 1
        citations = await search_pdf_pages(memory, "What legal battles does Alpha face?", limit=20)
        seen = set()
        for citation in citations:
            entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
            assert len(entry.sources) == 1
            source_id = entry.sources[0].source_id
            assert source_id not in seen
            seen.add(source_id)
            assert expected[source_id].document_id == "ALPHA_2024_10K"
        assert any(expected[source_id].page_number == 3 for source_id in seen)
        financial = await search_pdf_pages(
            memory,
            "What is Alpha FY2024 EBITDA using operating income from the income statement and depreciation from the cash flow statement?",
            limit=2,
        )
        financial_pages = set()
        for citation in financial:
            entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
            financial_pages.add(expected[entry.sources[0].source_id].page_number)
        assert financial_pages == {2, 4}

        class SparseSearchMemory:
            """An incomplete lexical index must not define the evidence corpus."""

            async def list(self):
                return await memory.list()

            async def search(self, request, /):
                result = await memory.search(request)
                return result.model_copy(update={"hits": ()})

            async def get(self, request, /):
                return await memory.get(request)

        sparse = await search_pdf_pages(
            SparseSearchMemory(),
            "What is Alpha FY2024 EBITDA using operating income and cash flows?",
            limit=2,
        )
        sparse_pages = set()
        for citation in sparse:
            entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
            sparse_pages.add(expected[entry.sources[0].source_id].page_number)
        assert sparse_pages == {2, 4}
        assert await search_pdf_pages(memory, "cash", limit=0) == ()
        assert await search_pdf_pages(memory, " ", limit=20) == ()
        isolated = await search_pdf_pages(runtime.memory.for_scope(other.scope_id), "geographic regions", limit=20)
        assert len(isolated) == 1
        entry = (await runtime.memory.for_scope(other.scope_id).get(GetMemoryEntryRequest(citation=isolated[0]))).entry
        assert expected[entry.sources[0].source_id].document_id == "BETA_2024_10K"
    async with open_builtin_contexts(config) as contexts:
        entries = await (await contexts.get(scope.scope_id)).sources.journal.entries()
        assert len(entries) == 5


async def test_short_alphanumeric_issuer_is_not_lost_among_other_reports(tmp_path: Path) -> None:
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    requested = "Consolidated Statements of Earnings. Net sales 100. Income from operations 20."
    distractor = (
        "Consolidated Statements of Income. Earnings from operations. 7Q is a customer. Revenue 100. "
        + "Operating income 20. " * 5
    )
    documents = {
        "7Q_2024_10K": _pdf(requested),
        "OMEGA_2024_10K": _pdf(*(distractor + f" Report unit {i}." for i in range(30))),
    }
    observations = []
    target_id = None
    for document_id, raw in documents.items():
        for page in parse_pdf_document(PDFDocumentInput(document_id=document_id, content=raw)):
            source = await registry.resolve(page)
            observations.append(project_source_for_transport(registry, source))
            if document_id == "7Q_2024_10K":
                target_id = source.name
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'issuers.db'}"))
    async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
        assert runtime.scopes is not None
        scope = await runtime.scopes.create(ScopeDraft(title="Reports", summary="Issuers", idempotency_key="issuers"))
        await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
        for observation in observations:
            await runtime.ingestion.submit(SubmitSourceObservation(scope_id=scope.scope_id, observation=observation))
        memory = runtime.memory.for_scope(scope.scope_id)
        for _ in observations:
            if not (await memory.flush()).processed:
                break
    async with open_builtin_runtime(config) as runtime:
        memory = runtime.memory.for_scope(scope.scope_id)
        citations = await search_pdf_pages(memory, "What is 7Q's operating income in FY2024?", limit=20)
        retrieved = [(await memory.get(GetMemoryEntryRequest(citation=citation))).entry for citation in citations]
        assert retrieved
        assert any(entry.sources[0].source_id == target_id for entry in retrieved)


async def test_company_legal_suffix_does_not_hide_its_pages(tmp_path: Path) -> None:
    """A common issuer name must find its report even when the ID has a legal suffix."""
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    documents = {
        "VERIDIAN_INC_2024_10K": _pdf("Statement of operations. Operating income 26. Net sales 400."),
        "VERIDIAN_2023_10K": _pdf(
            *(
                "Veridian was a customer. Operating income, earnings, revenue. "
                + "Veridian operating income and revenue. " * 8
                + f"Record {number}."
                for number in range(30)
            )
        ),
    }
    observations = []
    target_id = ""
    for document_id, raw in documents.items():
        for page in parse_pdf_document(PDFDocumentInput(document_id=document_id, content=raw)):
            source = await registry.resolve(page)
            observations.append(project_source_for_transport(registry, source))
            if document_id == "VERIDIAN_INC_2024_10K":
                target_id = source.name
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'legal-suffix.db'}"))
    async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
        assert runtime.scopes is not None
        scope = await runtime.scopes.create(ScopeDraft(title="Reports", summary="Issuers", idempotency_key="suffix"))
        await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
        for observation in observations:
            await runtime.ingestion.submit(SubmitSourceObservation(scope_id=scope.scope_id, observation=observation))
        memory = runtime.memory.for_scope(scope.scope_id)
        for _ in observations:
            if not (await memory.flush()).processed:
                break
    async with open_builtin_runtime(config) as runtime:
        memory = runtime.memory.for_scope(scope.scope_id)
        citations = await search_pdf_pages(memory, "What is Veridian's operating income in FY2024?", limit=20)
        entries = [(await memory.get(GetMemoryEntryRequest(citation=citation))).entry for citation in citations]
        assert any(entry.sources[0].source_id == target_id for entry in entries)


async def test_long_pdf_page_is_persisted_and_cited_without_truncation(tmp_path: Path) -> None:
    """A full captured PDF page may exceed the ordinary Memory entry size."""
    text = "operating cash flows detailed notes and commitments " * 220 + "unique ultraviolet commitment"
    pages = parse_pdf_document(PDFDocumentInput(document_id="LONG_REPORT_2024", content=_pdf(text)))
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    source = await registry.resolve(pages[0])
    observation = project_source_for_transport(registry, source)
    projection = observation.projection(TEXT_EVIDENCE_PROJECTION_KEY)
    assert isinstance(projection, dict) and isinstance(projection["content"], str)
    expected = projection["content"].strip()
    assert len(expected.encode("utf-8")) > 8192
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'long-page.db'}"))
    async with open_builtin_runtime(config) as runtime:
        assert runtime.scopes is not None
        scope = await runtime.scopes.create(ScopeDraft(title="Long page", summary="PDF", idempotency_key="long"))
        await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
        await runtime.ingestion.submit(SubmitSourceObservation(scope_id=scope.scope_id, observation=observation))
    async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
        assert (await runtime.memory.for_scope(scope.scope_id).flush()).processed
    async with open_builtin_runtime(config) as runtime:
        memory = runtime.memory.for_scope(scope.scope_id)
        citations = await search_pdf_pages(memory, "unique ultraviolet commitment", limit=20)
        assert len(citations) == 1
        entry = (await memory.get(GetMemoryEntryRequest(citation=citations[0]))).entry
        assert entry.text == expected
        assert entry.sources[0].source_id == source.name


async def test_long_pdf_memory_exception_requires_exact_source_text(tmp_path: Path) -> None:
    class AlteredPagePipeline:
        async def extract(self, request, /) -> tuple[MemoryEntryInput, ...]:
            source = request.sources[0]
            projection = source.projection(TEXT_EVIDENCE_PROJECTION_KEY)
            assert isinstance(projection, dict) and isinstance(projection["content"], str)
            return (
                MemoryEntryInput(kind="source-evidence", text=projection["content"] + " modified", sources=(source,)),
            )

    raw = _pdf("real page content " * 700)
    page = parse_pdf_document(PDFDocumentInput(document_id="report", content=raw))[0]
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    observation = project_source_for_transport(registry, await registry.resolve(page))
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'altered.db'}"))
    async with open_builtin_runtime(config, candidate_pipeline=AlteredPagePipeline()) as runtime:
        assert runtime.scopes is not None
        scope = await runtime.scopes.create(ScopeDraft(title="PDF", summary="PDF", idempotency_key="altered"))
        await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
        await runtime.ingestion.submit(SubmitSourceObservation(scope_id=scope.scope_id, observation=observation))
        with pytest.raises(InvalidMemoryCandidateError, match="8192"):
            await runtime.memory.for_scope(scope.scope_id).flush()


async def test_pdf_citation_preserves_non_normalized_document_id(tmp_path: Path) -> None:
    document_id = "Cafe\u0301_2024"
    pages = parse_pdf_document(PDFDocumentInput(document_id=document_id, content=_pdf("Exact original page")))
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    source = await registry.resolve(pages[0])
    observation = project_source_for_transport(registry, source)
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'unicode.db'}"))
    async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
        assert runtime.scopes is not None
        scope = await runtime.scopes.create(ScopeDraft(title="Unicode", summary="PDF", idempotency_key="unicode"))
        await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
        await runtime.ingestion.submit(SubmitSourceObservation(scope_id=scope.scope_id, observation=observation))
        assert (await runtime.memory.for_scope(scope.scope_id).flush()).processed
    async with open_builtin_runtime(config) as runtime:
        memory = runtime.memory.for_scope(scope.scope_id)
        citations = await search_pdf_pages(memory, "Exact original page", limit=1)
        assert len(citations) == 1
        entry = (await memory.get(GetMemoryEntryRequest(citation=citations[0]))).entry
        assert entry.text == document_id + "\n" + pages[0].text.strip()


def test_layout_reassembles_arbitrary_fragments_without_losing_rotated_text(monkeypatch: pytest.MonkeyPatch) -> None:
    class Page:
        def __init__(self, plain: str, layout: str) -> None:
            self.plain, self.layout = plain, layout

        def extract_text(self, **kwargs: object) -> str:
            return self.layout if kwargs.get("extraction_mode") == "layout" else self.plain

    class Reader:
        is_encrypted = False
        pages = (
            Page("Report agre\nement has Septem\nber 3\n0 2\n0\n2\n1.", "Report agreement has September 30 2021."),
            Page("Rotated label. Amount 12.50.", "Amount 12.50."),
            Page("Complete figures 12.50.", "Complete figures 12.50. 12.50."),
            Page("Forms filed under Form S.", "Forms filed under Form S."),
            Page("Septem\nber 3\n0 2\n0\n2\n1. Rotated label 7.", "September 30 2021."),
        )

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    monkeypatch.setattr(pdf_pages, "PdfReader", Reader)
    pages = parse_pdf_document(PDFDocumentInput(document_id="issuer", content=_pdf("valid")))
    assert pages[0].text == "Report agreement has September 30 2021."
    assert pages[1].text == "Rotated label. Amount 12.50."
    assert pages[2].text == "Complete figures 12.50."
    assert pages[3].text == "Forms filed under Form S."
    assert pages[4].text == "September 30 2021. Rotated label 7."

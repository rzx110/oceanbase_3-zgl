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

import base64
import hashlib
import io
import re
import zlib
from pathlib import Path

import pytest
from pypdf import PageObject, PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    EncodedStreamObject,
    NameObject,
    NullObject,
    NumberObject,
)

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

    class Page(PageObject):
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


def test_page_text_does_not_guess_words_from_other_occurrences(monkeypatch: pytest.MonkeyPatch) -> None:
    """A complete word elsewhere cannot justify rewriting ambiguous source text."""

    class Page(PageObject):
        def __init__(self, plain: str, layout: str) -> None:
            super().__init__()
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
    assert pages[0].text == "Statement of Incom e. Net income 400."
    assert pages[1].text == "Balance Shee t."
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
    class Page(PageObject):
        def __init__(self, plain: str, layout: str) -> None:
            super().__init__()
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


@pytest.mark.parametrize(
    "text",
    [
        "Form s is filed. Other forms follow.",
        "Plan s applies. Both plans remain.",
        "Part s is optional. Other parts remain.",
        "Rate s is variable. Other rates change.",
    ],
)
def test_literal_pdf_labels_are_not_changed_into_words(text: str) -> None:
    page = parse_pdf_document(PDFDocumentInput(document_id="labels", content=_pdf(text)))[0]
    assert page.text.strip() == text


@pytest.mark.parametrize("damage", [NumberObject(99), DictionaryObject(), ArrayObject([NumberObject(99)])])
def test_damage_on_later_page_rejects_the_complete_document(damage) -> None:
    writer = PdfWriter(clone_from=io.BytesIO(_pdf("First valid page", "Second page")))
    writer.pages[1][NameObject("/Contents")] = damage
    buffer = io.BytesIO()
    writer.write(buffer)
    raw = buffer.getvalue()
    assert "First valid page" in PdfReader(io.BytesIO(raw)).pages[0].extract_text()
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id="late-damage", content=raw))


async def test_old_citation_remains_exact_after_new_revision_and_restart(tmp_path: Path) -> None:
    old_raw = _pdf("Original reserve 12.50.", "", title="Old")
    new_raw = _pdf("Updated reserve 13.75.", "", title="New")
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    observations = []
    for raw in (old_raw, new_raw):
        revision = []
        for page in parse_pdf_document(PDFDocumentInput(document_id="REVISED_2024", content=raw)):
            revision.append(project_source_for_transport(registry, await registry.resolve(page)))
        observations.append(tuple(revision))
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'revisions.db'}"))
    async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
        assert runtime.scopes is not None
        scope = await runtime.scopes.create(ScopeDraft(title="Revisions", summary="PDF", idempotency_key="revisions"))
        await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
        for observation in observations[0]:
            await runtime.ingestion.submit(SubmitSourceObservation(scope_id=scope.scope_id, observation=observation))
        memory = runtime.memory.for_scope(scope.scope_id)
        assert (await memory.flush()).processed
        old_citation = (await search_pdf_pages(memory, "original reserve", limit=1))[0]
        old_entry = (await memory.get(GetMemoryEntryRequest(citation=old_citation))).entry
        for observation in observations[1]:
            await runtime.ingestion.submit(SubmitSourceObservation(scope_id=scope.scope_id, observation=observation))
        assert (await memory.flush()).processed
        for observation in observations[0]:
            await runtime.ingestion.submit(SubmitSourceObservation(scope_id=scope.scope_id, observation=observation))
        assert not (await memory.flush()).processed
    async with open_builtin_runtime(config) as runtime:
        memory = runtime.memory.for_scope(scope.scope_id)
        assert (await memory.get(GetMemoryEntryRequest(citation=old_citation))).entry == old_entry
        citations = await search_pdf_pages(memory, "reserve", limit=20)
        records = [(await memory.get(GetMemoryEntryRequest(citation=citation))).entry for citation in citations]
        assert len(records) == 4
        assert {entry.sources[0].source_id for entry in records} == {
            observation.name for revision in observations for observation in revision
        }
    async with open_builtin_contexts(config) as contexts:
        entries = await (await contexts.get(scope.scope_id)).sources.journal.entries()
        assert len(entries) == 4


def test_separate_pdf_drawings_preserve_the_same_word() -> None:
    writer = PdfWriter(clone_from=io.BytesIO(_pdf("Font resource")))
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 1 0 0 1 30 200 Tm (Incom) Tj ET BT /F1 12 Tf 1 0 0 1 62.676 200 Tm (e) Tj ET")
    writer.pages[0][NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    page = parse_pdf_document(PDFDocumentInput(document_id="positioned", content=buffer.getvalue()))[0]
    assert page.text.strip() == "Income"


def test_valid_stream_array_and_explicit_null_blank_page_are_accepted() -> None:
    writer = PdfWriter(clone_from=io.BytesIO(_pdf("Font resource", "")))
    streams = []
    for text, y in (("First fragment", 200), ("Second fragment", 180)):
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 30 {y} Td ({text}) Tj ET".encode())
        streams.append(writer._add_object(stream))
    writer.pages[0][NameObject("/Contents")] = ArrayObject(streams)
    writer.pages[1][NameObject("/Contents")] = NullObject()
    buffer = io.BytesIO()
    writer.write(buffer)
    pages = parse_pdf_document(PDFDocumentInput(document_id="stream-array", content=buffer.getvalue()))
    assert len(pages) == 2
    assert re.findall(r"[a-z]+", pages[0].text.lower()) == ["first", "fragment", "second", "fragment"]
    assert pages[1].text == ""


@pytest.mark.parametrize(
    "payload", [b"not-zlib", zlib.compress(b"BT (partial) Tj ET")[:-2], zlib.compress(b"BT (bad) Tj ET")[:-1] + b"!"]
)
@pytest.mark.parametrize("wrapped", [False, True])
def test_corrupt_compressed_later_page_is_not_silently_blank(payload: bytes, wrapped: bool) -> None:
    writer = PdfWriter(clone_from=io.BytesIO(_pdf("First valid page", "Second page")))
    stream = EncodedStreamObject()
    stream._data = base64.a85encode(payload, adobe=True) if wrapped else payload
    stream[NameObject("/Filter")] = (
        ArrayObject([NameObject("/ASCII85Decode"), NameObject("/FlateDecode")])
        if wrapped
        else NameObject("/FlateDecode")
    )
    writer.pages[1][NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id="corrupt-compression", content=buffer.getvalue()))


@pytest.mark.parametrize("content", [b"", b"BT /F1 12 Tf 30 200 Td (Valid compressed text) Tj ET"])
@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("encrypted", [False, True])
def test_complete_compressed_streams_include_valid_blank_pages(content: bytes, wrapped: bool, encrypted: bool) -> None:
    writer = PdfWriter(clone_from=io.BytesIO(_pdf("Font resource")))
    stream = EncodedStreamObject()
    compressed = zlib.compress(content)
    stream._data = base64.a85encode(compressed, adobe=True) if wrapped else compressed
    stream[NameObject("/Filter")] = (
        ArrayObject([NameObject("/ASCII85Decode"), NameObject("/FlateDecode")])
        if wrapped
        else NameObject("/FlateDecode")
    )
    writer.pages[0][NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt(user_password="", owner_password="owner")  # noqa: S106
    buffer = io.BytesIO()
    writer.write(buffer)
    page = parse_pdf_document(PDFDocumentInput(document_id="valid-compression", content=buffer.getvalue()))[0]
    assert page.text.strip() == ("Valid compressed text" if content else "")


@pytest.mark.parametrize("corrupt", [False, True])
def test_compressed_form_xobject_is_validated(corrupt: bool) -> None:
    writer = PdfWriter(clone_from=io.BytesIO(_pdf("Font resource")))
    page = writer.pages[0]
    form = EncodedStreamObject()
    form._data = b"bad-compression" if corrupt else zlib.compress(b"BT /F1 12 Tf 30 200 Td (Text in nested form) Tj ET")
    form[NameObject("/Filter")] = NameObject("/FlateDecode")
    form[NameObject("/Type")] = NameObject("/XObject")
    form[NameObject("/Subtype")] = NameObject("/Form")
    form[NameObject("/BBox")] = ArrayObject([NumberObject(value) for value in (0, 0, 400, 250)])
    resources = page["/Resources"]
    assert isinstance(resources, DictionaryObject)
    form[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): resources["/Font"]})
    resources[NameObject("/XObject")] = DictionaryObject({NameObject("/Fm0"): writer._add_object(form)})
    content = DecodedStreamObject()
    content.set_data(b"q /Fm0 Do Q")
    page[NameObject("/Contents")] = writer._add_object(content)
    buffer = io.BytesIO()
    writer.write(buffer)
    value = PDFDocumentInput(document_id="nested-form", content=buffer.getvalue())
    if corrupt:
        with pytest.raises(ValueError):
            parse_pdf_document(value)
    else:
        assert parse_pdf_document(value)[0].text.strip() == "Text in nested form"


def test_large_compressed_page_is_complete() -> None:
    text = "complete original financial disclosure " * 4000
    writer = PdfWriter(clone_from=io.BytesIO(_pdf("Font resource")))
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 30 200 Td ({text}) Tj ET".encode())
    writer.pages[0][NameObject("/Contents")] = writer._add_object(stream.flate_encode())
    buffer = io.BytesIO()
    writer.write(buffer)
    page = parse_pdf_document(PDFDocumentInput(document_id="long-compressed", content=buffer.getvalue()))[0]
    assert page.text.strip() == text.strip()


async def test_wrapped_statement_heading_remains_retrievable(tmp_path: Path) -> None:
    distractor = "Operating income trend. Operating income analysis. Figures 1 2 3 4 5 6 7 8 9 10 11 12."
    writer = PdfWriter(clone_from=io.BytesIO(_pdf("Font resource", *([distractor] * 25))))
    stream = DecodedStreamObject()
    stream.set_data(
        b"BT /F1 12 Tf 30 210 Td (Consolidated Statement of Incom) Tj ET "
        b"BT /F1 12 Tf 30 190 Td (e) Tj ET "
        b"BT /F1 12 Tf 30 170 Td (Operating profit 42. Figures 1 2 3 4 5 6 7 8 9 10 11 12.) Tj ET"
    )
    writer.pages[0][NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    observations = []
    for page in parse_pdf_document(PDFDocumentInput(document_id="ACME_2024", content=buffer.getvalue())):
        observations.append(project_source_for_transport(registry, await registry.resolve(page)))
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'wrapped.db'}"))
    async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
        assert runtime.scopes is not None
        scope = await runtime.scopes.create(ScopeDraft(title="Wrapped", summary="PDF", idempotency_key="wrapped"))
        await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
        for observation in observations:
            await runtime.ingestion.submit(SubmitSourceObservation(scope_id=scope.scope_id, observation=observation))
        while (await runtime.memory.for_scope(scope.scope_id).flush()).processed:
            pass
    async with open_builtin_runtime(config) as runtime:
        memory = runtime.memory.for_scope(scope.scope_id)
        citations = await search_pdf_pages(memory, "What was Acme's operating income in FY2024?", limit=1)
        assert len(citations) == 1
        entry = (await memory.get(GetMemoryEntryRequest(citation=citations[0]))).entry
        assert entry.sources[0].source_id == observations[0].name


async def test_split_singular_balance_heading_is_searchable_without_rewriting_text(tmp_path: Path) -> None:
    target = "Consolidated Balance Shee t. Assets 123. Liabilities 98. Figures 1 2 3 4 5 6 7 8 9 10 11 12."
    distractor = "Notes about balance sheet assets and liabilities. Assets 30. Liabilities 20. Figures 1 2 3 4 5 6 7 8 9 10 11 12."
    pages = parse_pdf_document(PDFDocumentInput(document_id="ACME_2024", content=_pdf(target, *([distractor] * 25))))
    assert pages[0].text.strip() == target
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    observations = []
    for page in pages:
        observations.append(project_source_for_transport(registry, await registry.resolve(page)))
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'singular-heading.db'}"))
    async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
        assert runtime.scopes is not None
        scope = await runtime.scopes.create(ScopeDraft(title="Heading", summary="PDF", idempotency_key="singular"))
        await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
        for observation in observations:
            await runtime.ingestion.submit(SubmitSourceObservation(scope_id=scope.scope_id, observation=observation))
        while (await runtime.memory.for_scope(scope.scope_id).flush()).processed:
            pass
    async with open_builtin_runtime(config) as runtime:
        memory = runtime.memory.for_scope(scope.scope_id)
        citations = await search_pdf_pages(
            memory, "What assets and liabilities did Acme report on its FY2024 balance sheet?", limit=1
        )
        assert len(citations) == 1
        entry = (await memory.get(GetMemoryEntryRequest(citation=citations[0]))).entry
        assert entry.sources[0].source_id == observations[0].name

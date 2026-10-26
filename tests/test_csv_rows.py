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

"""CSV evidence boundary cases beyond the published airport workload."""

import asyncio
import json

import pytest

from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    SourceDefinitionRegistry,
    SourceMaterialization,
    TextEvidence,
    manifest_for_definition,
    project_source_for_transport,
)
from powercontext.sources.csv_rows import (
    CSV_ROW_SOURCE_DEFINITION,
    CSVDocumentInput,
    CSVRowInput,
    CSVRowSource,
    csv_row_source_id,
    parse_csv_document,
)


def test_unicode_values_column_order_and_newlines_survive_transport() -> None:
    rows = parse_csv_document(
        CSVDocumentInput(
            document_id="机场:数据/一",
            primary_key=" id ",
            content='note, id ,id,empty,flag\r\n"第一行\r\n第二行", 001 ,0002,,false\r\n'.encode(),
        )
    )
    assert len(rows) == 1
    row = rows[0]
    registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
    source = asyncio.run(registry.resolve(row))
    assert isinstance(source, CSVRowSource)
    assert source.materialization == SourceMaterialization.CAPTURED
    assert source.definition_version == "1"
    assert asyncio.run(registry.read(source)) == row
    observation = project_source_for_transport(registry, source)
    evidence = TextEvidence.model_validate(observation.projection(TEXT_EVIDENCE_PROJECTION_KEY))
    assert evidence.source_id == csv_row_source_id("机场:数据/一", " 001 ")
    assert evidence.content == '{"note":"第一行\\r\\n第二行"," id ":" 001 ","id":"0002","empty":"","flag":"false"}'
    assert list(json.loads(evidence.content)) == list(row.columns)
    assert set(evidence.metadata) == {"document_id", "primary_key", "primary_key_value", "columns", "row"}
    assert evidence.metadata["columns"] == list(row.columns)
    manifest = manifest_for_definition(CSV_ROW_SOURCE_DEFINITION)
    assert manifest.name == "csv-row"
    assert manifest.version == "1"


@pytest.mark.parametrize(
    "content",
    [
        b"\n",
        b"\nid,name\n1,one\n",
        b"id,name\n1,one\n2,two,extra\n",
        b"id,name\n1,one\n ,blank-key\n",
        b"id,name\n1,one\n1,duplicate-at-end\n",
        b'id,name\n1,one\n2,"closed"junk\n',
        b'id,name\n1,one\n2,"unclosed\n',
        b"id,name\n1,one\n\xff,invalid\n",
        b'id\n1\n""\n',
        b"id,name\n1,one\n   \n",
        b"id,name\n1,one\n,\n",
    ],
)
def test_invalid_late_records_fail_the_whole_document(content: bytes) -> None:
    with pytest.raises(ValueError):
        parse_csv_document(CSVDocumentInput(document_id="late-errors", primary_key="id", content=content))


def test_blank_records_and_carriage_return_delimiters_preserve_field_values() -> None:
    rows = parse_csv_document(
        CSVDocumentInput(document_id="cr", primary_key="id", content=b'\xef\xbb\xbfid,note\r\r001,"a\rb"\r\r002,\r')
    )
    assert [row.values for row in rows] == [("001", "a\rb"), ("002", "")]


@pytest.mark.parametrize("document_id", ["", " ", " doc", "doc\t", "\u2003doc"])
def test_invalid_document_identity_fails_even_for_header_only_csv(document_id: str) -> None:
    with pytest.raises(ValueError):
        parse_csv_document(CSVDocumentInput(document_id=document_id, primary_key="id", content=b"id,name\n"))


def test_encoded_identity_length_boundary_and_component_collision() -> None:
    assert len(csv_row_source_id("d", "k" * 250)) == 256
    with pytest.raises(ValueError):
        csv_row_source_id("d", "k" * 251)
    with pytest.raises(ValueError):
        csv_row_source_id("d", "中" * 28)
    assert csv_row_source_id("a:b", "c") != csv_row_source_id("a", "b:c")
    assert csv_row_source_id("a%3Ab", "c") != csv_row_source_id("a:b", "c")
    assert csv_row_source_id("d", " key ") == "csv:d:%20key%20"


def test_identity_overflow_is_rejected_during_document_parsing() -> None:
    with pytest.raises(ValueError):
        parse_csv_document(
            CSVDocumentInput(
                document_id="d", primary_key="id", content=b"id,name\nok,first\n" + b"k" * 251 + b",last\n"
            )
        )


@pytest.mark.parametrize(
    "replacement",
    [
        {"primary_key": "missing"},
        {"primary_key_value": "different"},
        {"columns": ("id", "id")},
        {"columns": ("id", " ")},
        {"values": ("001",)},
        {"primary_key_value": " ", "values": (" ", "name")},
    ],
)
def test_direct_row_inputs_cannot_capture_inconsistent_evidence(replacement: dict[str, object]) -> None:
    values = {
        "document_id": "direct",
        "primary_key": "id",
        "primary_key_value": "001",
        "columns": ("id", "name"),
        "values": ("001", "name"),
    }
    with pytest.raises(ValueError):
        CSVRowInput.model_validate(values | replacement)

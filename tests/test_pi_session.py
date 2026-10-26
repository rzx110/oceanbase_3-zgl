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

"""Pi evidence fidelity and lifecycle recovery beyond the public workload."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import JsonValue

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.artifacts.memory.models import MemoryCitation
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    SearchMemoryRequest,
    SubmitSourceObservation,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    ConnectorBinding,
    SourceDefinitionRegistry,
    SourceObservation,
    SourceRef,
    manifest_for_definition,
    project_source_for_transport,
)
from powercontext.sources.connectors import ConnectorLifecycle, ConnectorRunStatus
from powercontext.sources.pi_session import (
    PI_EVENT_SOURCE_DEFINITION,
    PiEventInput,
    PiSessionConnector,
    PiSessionInput,
    parse_pi_session,
)


def _message(event_id: str, parent: str | None, text: str) -> dict[str, JsonValue]:
    return {"type": "message", "id": event_id, "parentId": parent, "message": {"role": "user", "content": text}}


def _archive(records: list[dict[str, JsonValue]], session_id: str = "session") -> bytes:
    return "\n".join(
        json.dumps(row, ensure_ascii=False) for row in [{"type": "session", "version": 3, "id": session_id}, *records]
    ).encode()


def test_unicode_line_separators_and_tool_argument_order_are_preserved() -> None:
    records: list[dict[str, JsonValue]] = [
        _message("root", None, "before\u2028middle\u2029after\u0085end"),
        {
            "type": "message",
            "id": "tool",
            "parentId": "root",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "  exact text  "},
                    {"type": "thinking", "thinking": "hidden"},
                    {"type": "toolCall", "id": "c", "name": "read", "arguments": {"z": "中文", "a": [1, False]}},
                ],
            },
        },
    ]
    events = parse_pi_session(PiSessionInput(content=_archive(records)))
    assert events[0].content == "user: before\u2028middle\u2029after\u0085end"
    assert events[1].content == 'assistant:   exact text  \n[tool_call read c] {"a":[1,false],"z":"中文"}'
    assert events[1].record == records[1]


def test_header_only_and_invisible_branches() -> None:
    assert parse_pi_session(PiSessionInput(content=_archive([]))) == ()
    nodes: list[dict[str, JsonValue]] = [
        _message("root", None, "kept"),
        {"type": "message", "id": "empty", "parentId": "root", "message": {"role": "assistant", "content": []}},
        {"type": "model_change", "id": "control", "parentId": "empty", "modelId": "offline"},
    ]
    assert [event.event_id for event in parse_pi_session(PiSessionInput(content=_archive(nodes)))] == ["root"]


@pytest.mark.parametrize(
    "content",
    [
        b'{"type":"session","version":3,"id":"s"}\n{"type":"message","id":"x"}\n',
        b'{"type":"session","version":3,"id":"s"}\n{"id":"x","parentId":null}\n',
        b'{"type":"session","version":3,"id":"s"}\n{"type":"model_change","id":"x","parentId":2}\n',
        b'{"type":"session","version":3,"id":"s"}\n{"type":"model_change","id":"x","parentId":"later"}\n',
        b'{"type":"session","version":3,"id":""}\n',
        b'{"type":"session","version":3.0,"id":"s"}\n',
        b'{"type":"session","version":3,"id":"s","id":"changed"}\n',
        b'{"type":"session","version":3,"id":"s"}\nNaN\n',
        b'{"type":"session","version":3,"id":"s"}\n{"value":Infinity}\n',
        b'{"type":"session","version":3,"id":"s"}\n[]\n',
        b'{"type":"session","version":3,"id":"s"}\n{"unterminated"',
    ],
)
def test_invalid_entire_archives_raise_value_error(content: bytes) -> None:
    with pytest.raises(ValueError):
        parse_pi_session(PiSessionInput(content=content))


async def test_event_identity_is_bounded_and_separates_structured_components() -> None:
    registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
    names = set()
    for session_id, event_id in [("a:b", "c"), ("a", "b:c"), ("中" * 400, "文" * 400)]:
        event = parse_pi_session(PiSessionInput(content=_archive([_message(event_id, None, "same")], session_id)))[0]
        source = await registry.resolve(event)
        assert len(source.name) <= 256
        assert await registry.read(source) == event
        assert (await registry.resolve(event)).name == source.name
        names.add(source.name)
    assert len(names) == 3


class _Checkpoints:
    def __init__(self) -> None:
        self.value: JsonValue | None = None

    async def load(self, binding: ConnectorBinding, /) -> JsonValue | None:
        return self.value

    async def save(
        self, binding: ConnectorBinding, checkpoint: JsonValue | None, /, *, expected: JsonValue | None
    ) -> None:
        assert self.value == expected
        self.value = json.loads(json.dumps(checkpoint))


class _Sink:
    def __init__(self, fail_on: str | None = None) -> None:
        self.values: dict[str, PiEventInput] = {}
        self.attempts: list[str] = []
        self.fail_on = fail_on

    async def submit(
        self, binding: ConnectorBinding, item_id: str, definition_name: str, value: object, /
    ) -> SourceRef:
        assert isinstance(value, PiEventInput)
        self.attempts.append(value.event_id)
        if value.event_id == self.fail_on:
            raise OSError("injected persistence failure")  # noqa: TRY003
        if item_id in self.values:
            assert self.values[item_id] == value
        self.values[item_id] = value
        return SourceRef(source_type=definition_name, source_id=item_id)


def _binding() -> ConnectorBinding:
    return ConnectorBinding(
        scope_id="pi", binding_id="history", connector_name="pi-session-history", connector_version="1"
    )


async def test_mid_write_failure_does_not_advance_checkpoint_and_replay_recovers(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(_archive([_message("a", None, "root"), _message("b", "a", "second")]))
    checkpoints = _Checkpoints()
    sink = _Sink(fail_on="b")
    with pytest.raises(OSError):
        await ConnectorLifecycle(sink=sink, checkpoints=checkpoints).run(PiSessionConnector(path), _binding())
    assert checkpoints.value is None and len(sink.values) == 1
    sink.fail_on = None
    result = await ConnectorLifecycle(sink=sink, checkpoints=checkpoints).run(PiSessionConnector(path), _binding())
    assert result.status == ConnectorRunStatus.COMPLETE and len(sink.values) == 2
    replay = await ConnectorLifecycle(sink=sink, checkpoints=checkpoints).run(PiSessionConnector(path), _binding())
    assert replay.items == () and len(sink.values) == 2


async def test_off_branch_payload_mutation_fails_before_new_submissions(tmp_path: Path) -> None:
    path = tmp_path / "tree.jsonl"
    records = [_message("root", None, "shared"), _message("left", "root", "left"), _message("right", "root", "right")]
    path.write_bytes(_archive(records))
    checkpoints, sink = _Checkpoints(), _Sink()
    lifecycle = ConnectorLifecycle(sink=sink, checkpoints=checkpoints)
    first = await lifecycle.run(PiSessionConnector(path, "left"), _binding())
    changed = json.loads(json.dumps(records))
    changed[1]["message"]["content"] = "rewritten off-branch evidence"
    path.write_bytes(_archive(changed))
    before = list(sink.attempts)
    with pytest.raises(ValueError):
        await lifecycle.run(PiSessionConnector(path, "right"), _binding())
    assert sink.attempts == before and checkpoints.value == first.committed_checkpoint
    path.write_bytes(_archive(records))
    recovered = await lifecycle.run(PiSessionConnector(path, "right"), _binding())
    assert len(recovered.items) == 1 and len(sink.values) == 3


async def test_record_only_edits_and_control_reparenting_are_not_silently_accepted(tmp_path: Path) -> None:
    path = tmp_path / "controls.jsonl"
    records: list[dict[str, JsonValue]] = [
        _message("root", None, "root"),
        {"type": "model_change", "id": "control", "parentId": "root", "modelId": "old"},
        _message("leaf", "control", "leaf"),
    ]
    path.write_bytes(_archive(records))
    checkpoints, sink = _Checkpoints(), _Sink()
    lifecycle = ConnectorLifecycle(sink=sink, checkpoints=checkpoints)
    first = await lifecycle.run(PiSessionConnector(path), _binding())
    changed = json.loads(json.dumps(records))
    changed[0]["extra"] = {"new": "metadata"}
    path.write_bytes(_archive(changed))
    with pytest.raises(ValueError):
        await lifecycle.run(PiSessionConnector(path), _binding())
    changed = json.loads(json.dumps(records))
    changed[1]["parentId"] = None
    path.write_bytes(_archive(changed))
    with pytest.raises(ValueError):
        await lifecycle.run(PiSessionConnector(path), _binding())
    assert checkpoints.value == first.committed_checkpoint


async def test_large_tool_result_retains_full_source_and_projection(tmp_path: Path) -> None:
    body = "tool evidence " + "中文 output\n" * 3000
    record: dict[str, JsonValue] = {
        "type": "message",
        "id": "large",
        "parentId": None,
        "message": {
            "role": "toolResult",
            "toolName": "read",
            "toolCallId": "c",
            "content": [{"type": "text", "text": body}],
        },
    }
    event = parse_pi_session(PiSessionInput(content=_archive([record])))[0]
    assert len(event.content.encode()) > 8192
    registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
    source = await registry.resolve(event)
    projection = registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY)
    assert isinstance(projection, dict)
    assert projection["content"] == "toolResult: [tool_result read c] " + body
    assert await registry.read(source) == event
    assert event.record == record
    observation = project_source_for_transport(registry, source)
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'large-tool.db'}"))
    async with open_builtin_runtime(config) as runtime:
        assert runtime.scopes is not None
        scope = await runtime.scopes.create(
            ScopeDraft(title="Large tool", summary="Full evidence", idempotency_key="tool")
        )
        await runtime.ingestion.register(manifest_for_definition(PI_EVENT_SOURCE_DEFINITION))
        await runtime.ingestion.submit(SubmitSourceObservation(scope_id=scope.scope_id, observation=observation))

    class ChunkPipeline:
        async def extract(self, request, /) -> tuple[MemoryEntryInput, ...]:
            return tuple(
                MemoryEntryInput(kind=f"pi-evidence-{index}", text=text[index : index + 1500], sources=(item,))
                for item in request.sources
                for text in (item.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"],)
                for index in range(0, len(text), 1500)
            )

    async with open_builtin_runtime(config, candidate_pipeline=ChunkPipeline()) as runtime:
        memory = runtime.memory.for_scope(scope.scope_id)
        assert (await memory.flush()).processed
        search = await memory.search(SearchMemoryRequest(query="toolResult", mode="fts", limit=20))
        assert search.hits
        hit = search.hits[0]
        entry = await memory.get(
            GetMemoryEntryRequest(
                citation=MemoryCitation(
                    memory_ref=hit.memory_ref, entry_id=hit.entry_id, entry_version_id=hit.entry_version_id
                )
            )
        )
        assert any(reference.source_id == source.name for reference in entry.entry.sources)
        assert len(entry.entry.text.encode()) <= 8192
    async with open_builtin_contexts(config) as contexts:
        entries = await (await contexts.get(scope.scope_id)).sources.journal.entries()
        assert len(entries) == 1
        persisted = entries[0].source
        assert isinstance(persisted, SourceObservation)
        assert persisted == observation
        assert persisted.projection(TEXT_EVIDENCE_PROJECTION_KEY) == projection

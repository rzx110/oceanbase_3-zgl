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

"""Offline Pi v3 branch evidence and lifecycle-owned incremental capture."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    ConnectorSubmissionStatus,
)
from powercontext.sources.definitions import SourceDefinition, SourceProjection
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


class PiSessionInput(BaseModel):
    """Complete Pi v3 JSONL bytes and an optional selected tree leaf."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One original visible event, including its complete input record."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """Immutable captured event evidence with no Memory text-size limit."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _event_identity(session_id: str, event_id: str) -> str:
    # Hash the structured pair so delimiters and arbitrarily long IDs stay safe.
    digest = hashlib.sha256(_compact_json([session_id, event_id]).encode("utf-8")).hexdigest()
    return f"pi:{digest}"


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Pi {field} must be a non-empty string")  # noqa: TRY003
    return value


def _text(value: JsonValue, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Pi {field} must be a string")  # noqa: TRY003, TRY004
    return value


def _content_blocks(value: JsonValue, *, tool_calls: bool) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        raise ValueError("Pi content must be a string or a list of blocks")  # noqa: TRY003, TRY004
    parts: list[str] = []
    for block in value:
        if not isinstance(block, dict):
            raise ValueError("Pi content blocks must be objects")  # noqa: TRY003, TRY004
        kind = _required_string(block.get("type"), "content block type")
        if kind == "text":
            parts.append(_text(block.get("text"), "text block"))
        elif kind == "toolCall" and tool_calls:
            name = _required_string(block.get("name"), "tool name")
            call_id = _required_string(block.get("id"), "tool call ID")
            arguments = block.get("arguments")
            if not isinstance(arguments, dict):
                raise ValueError("Pi tool call arguments must be an object")  # noqa: TRY003
            parts.append(f"[tool_call {name} {call_id}] {_compact_json(arguments)}")
    return "\n".join(parts)


def _visible_event(session_id: str, record: dict[str, JsonValue]) -> PiEventInput | None:
    event_type = cast(str, record["type"])
    if event_type == "message":
        message = record.get("message")
        if not isinstance(message, dict):
            raise ValueError("Pi message entries must contain a message object")  # noqa: TRY003
        role = _required_string(message.get("role"), "message role")
        if role == "bashExecution":
            command = _text(message.get("command", ""), "bash command")
            output = _text(message.get("output", ""), "bash output")
            body = f"$ {command}\n{output}" if command or output else ""
        else:
            body = _content_blocks(message.get("content", []), tool_calls=True)
            if role == "toolResult":
                name = _required_string(message.get("toolName"), "tool result name")
                call_id = _required_string(message.get("toolCallId"), "tool result call ID")
                body = f"[tool_result {name} {call_id}] {body}"
    elif event_type in {"compaction", "branch_summary"}:
        role = event_type
        body = _text(record.get("summary", ""), "summary")
    elif event_type == "custom_message":
        role = "custom"
        body = _content_blocks(record.get("content", []), tool_calls=False)
    else:
        return None
    if not body:
        return None
    return PiEventInput(
        session_id=session_id,
        event_id=cast(str, record["id"]),
        parent_id=cast(str | None, record["parentId"]),
        event_type=event_type,
        role=role,
        content=f"{role}: {body}",
        record=record,
    )


def _reject_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON numeric constant: {value}")  # noqa: TRY003


def _json_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON object key: {key}")  # noqa: TRY003
        result[key] = value
    return result


@dataclass(frozen=True)
class _PiArchive:
    session_id: str
    nodes: dict[str, dict[str, JsonValue]]
    branch: tuple[str, ...]
    events: tuple[PiEventInput, ...]


def _load_records(content: bytes) -> list[dict[str, JsonValue]]:
    records: list[dict[str, JsonValue]] = []
    # Only LF separates JSONL records; Unicode line separators can occur in text.
    for line in content.decode("utf-8-sig").split("\n"):
        if not line.strip():
            continue
        record = json.loads(line, object_pairs_hook=_json_object, parse_constant=_reject_constant)
        if not isinstance(record, dict):
            raise ValueError("Pi JSONL records must be objects")  # noqa: TRY003, TRY004
        records.append(record)
    if not records:
        raise ValueError("Pi session header is required")  # noqa: TRY003
    return records


def _parse_archive(value: PiSessionInput) -> _PiArchive:
    records = _load_records(value.content)
    header = records[0]
    if header.get("type") != "session" or type(header.get("version")) is not int or header["version"] != 3:
        raise ValueError("Pi requires a version 3 session header")  # noqa: TRY003
    session_id = _required_string(header.get("id"), "session ID")
    nodes: dict[str, dict[str, JsonValue]] = {}
    for record in records[1:]:
        event_id = _required_string(record.get("id"), "entry ID")
        event_type = _required_string(record.get("type"), "entry type")
        if event_id in nodes or event_type == "session":
            raise ValueError("Pi entries must have unique IDs and cannot contain another header")  # noqa: TRY003
        if "parentId" not in record:
            raise ValueError("Pi entries must declare parentId")  # noqa: TRY003
        parent_id = record["parentId"]
        if parent_id is not None and (not isinstance(parent_id, str) or parent_id not in nodes):
            raise ValueError("Pi parentId must refer to an earlier entry")  # noqa: TRY003
        nodes[event_id] = record
    leaf_id = value.leaf_id if value.leaf_id is not None else next(reversed(nodes), None)
    if leaf_id is not None and leaf_id not in nodes:
        raise ValueError("Pi selected leaf does not exist")  # noqa: TRY003
    branch: list[str] = []
    while leaf_id is not None:
        branch.append(leaf_id)
        leaf_id = cast(str | None, nodes[leaf_id]["parentId"])
    branch.reverse()
    events = tuple(event for event_id in branch if (event := _visible_event(session_id, nodes[event_id])) is not None)
    return _PiArchive(session_id, nodes, tuple(branch), events)


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Parse the whole archive, then return the selected raw ancestor history."""

    return _parse_archive(value).events


class PiTextEvidenceProjection:
    """Expose complete event text without loading connector code at read time."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class: type[PiEventSource] = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type="pi-event",
            source_id=source.name,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


class PiEventSourceDefinition:
    """Materialize full original event records and their text evidence."""

    input_class: type[PiEventInput] = PiEventInput
    name = "pi-event"
    version = "1"
    source_class: type[PiEventSource] = PiEventSource

    def __init__(self) -> None:
        self.projections: tuple[SourceProjection[PiEventSource], ...] = (PiTextEvidenceProjection(),)

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        return PiEventSource(
            name=_event_identity(value.session_id, value.event_id),
            materialization=SourceMaterialization.CAPTURED,
            **value.model_dump(),
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput.model_validate(source.model_dump())


PI_EVENT_SOURCE_DEFINITION: SourceDefinition[PiEventInput, PiEventSource, PiEventInput] = PiEventSourceDefinition()


class _PiCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    sessions: dict[str, dict[str, str]] = Field(default_factory=dict)


class PiSessionConnector:
    """Import one selected branch using only the lifecycle's durable progress."""

    name = "pi-session-history"
    version = "1"
    source_definitions = frozenset({"pi-event"})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        raw = await asyncio.to_thread(self.path.read_bytes)
        archive = _parse_archive(PiSessionInput(content=raw, leaf_id=self.leaf_id))
        progress = _PiCheckpoint() if session.checkpoint is None else _PiCheckpoint.model_validate(session.checkpoint)
        seen = progress.sessions.setdefault(archive.session_id, {})
        # Check all saved nodes, including other branches and control ancestors,
        # before submitting anything. An invalid archive cannot advance progress.
        fingerprints = {
            event_id: hashlib.sha256(_compact_json(record).encode("utf-8")).hexdigest()
            for event_id, record in archive.nodes.items()
        }
        for event_id, digest in seen.items():
            if fingerprints.get(event_id) != digest:
                raise ValueError("Previously captured Pi history was changed or removed")  # noqa: TRY003
        for event in archive.events:
            if event.event_id not in seen:
                outcome = await session.submit(_event_identity(event.session_id, event.event_id), "pi-event", event)
                if outcome.status is not ConnectorSubmissionStatus.ACCEPTED:
                    return ConnectorRunCompletion(status=ConnectorRunStatus.INCOMPLETE)
        seen.update({event_id: fingerprints[event_id] for event_id in archive.branch})
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint=progress.model_dump(mode="json"))


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
]

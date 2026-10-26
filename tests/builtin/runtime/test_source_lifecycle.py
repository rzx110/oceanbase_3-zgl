# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License").
"""Native write failure recovery, scope isolation and strict lifecycle events."""

import sqlite3

import pytest
from pydantic import ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    GetMemoryEntryRequest,
    RememberMemoryRequest,
    SearchMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.source_lifecycle import SourceLifecycleError, SourceLifecycleEvent
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import SourceRef


async def graph(runtime, suffix):
    scope = await runtime.scopes.create(
        ScopeDraft(title="Lifecycle", summary="Retry regression", idempotency_key=suffix)
    )
    source = await runtime.sources.for_scope(scope.scope_id).capture(
        CaptureSource(source_id="source", content="Independently captured evidence", metadata={})
    )
    memory = await runtime.memory.for_scope(scope.scope_id).remember(
        RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text="Mercury sensor fact"),))
    )
    service = runtime.lifecycle.for_scope(scope.scope_id)
    await service.register_source("root", source.source_ref, revision=3)
    await service.register_memory("fact", memory.entry.citation, parents=("root",))
    return scope.scope_id, service, memory.entry.citation


def event(sequence=1, available=False):
    return SourceLifecycleEvent(node_id="root", revision=3, sequence=sequence, available=available, reason="withdrawal")


@pytest.mark.asyncio
async def test_real_sqlite_write_failure_retries_after_restart_and_replay_does_not_revise(tmp_path):
    path = tmp_path / "retry.db"
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"))
    async with open_builtin_runtime(config) as runtime:
        scope, service, citation = await graph(runtime, "retry")
        before = (await runtime.memory.for_scope(scope).list()).memory_ref
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TRIGGER reject_memory BEFORE INSERT ON pc_artifacts "
                "WHEN NEW.family='memory' BEGIN SELECT RAISE(ABORT, 'injected write failure'); END"
            )
        with pytest.raises(Exception, match="injected write failure"):
            await service.apply(event())
        assert (await runtime.memory.for_scope(scope).list()).memory_ref == before
        with sqlite3.connect(path) as connection:
            connection.execute("DROP TRIGGER reject_memory")
    async with open_builtin_runtime(config) as runtime:
        service = runtime.lifecycle.for_scope(scope)
        assert await service.apply(event()) == []
        memory = runtime.memory.for_scope(scope)
        assert not (await memory.search(SearchMemoryRequest(query="Mercury", mode="fts"))).hits
        after = (await memory.list(include_inactive=True)).memory_ref
        assert after is not None and before is not None
        assert after.revision == before.revision + 1
        await service.apply(event())
        assert (await memory.list(include_inactive=True)).memory_ref == after
        original = await memory.get(GetMemoryEntryRequest(citation=citation))
        assert original.entry.text == "Mercury sensor fact"
        await service.apply(event(2, True))
        assert await service.resolve("fact") == citation
        assert (await memory.search(SearchMemoryRequest(query="Mercury", mode="fts"))).hits


@pytest.mark.asyncio
async def test_scopes_are_isolated_and_references_must_exist():
    async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
        first, service, citation = await graph(runtime, "first")
        second, other, other_citation = await graph(runtime, "second")
        assert first != second
        await service.apply(event())
        assert await other.resolve("fact") == other_citation
        assert (await runtime.memory.for_scope(second).search(SearchMemoryRequest(query="Mercury", mode="fts"))).hits
        with pytest.raises(SourceLifecycleError):
            await service.resolve("fact")
        assert await service.resolve("fact", purpose="audit") == citation
        with pytest.raises(SourceLifecycleError):
            await service.register_source("missing", SourceRef(source_type="content", source_id="missing"))
        with pytest.raises(SourceLifecycleError):
            await service.register_topic(
                "missing", ArtifactRef(family="topic-memory", artifact_id="missing", revision=1), parents=("root",)
            )
        with pytest.raises(SourceLifecycleError):
            await service.register_memory("duplicate", citation, parents=("root", "root"))
        with pytest.raises(SourceLifecycleError):
            await service.resolve("root", purpose="unknown")


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision", True),
        ("revision", 1.0),
        ("sequence", "1"),
        ("sequence", False),
        ("available", 1),
        ("available", "false"),
        ("reason", " \t"),
        ("node_id", ""),
        ("extra", 1),
    ],
)
def test_events_reject_coercions_and_unknown_fields(field, value):
    values = event().model_dump()
    values[field] = value
    with pytest.raises(ValidationError):
        SourceLifecycleEvent(**values)

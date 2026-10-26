# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License").
"""Durable Source dependency qualification without rewriting historical bodies."""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import insert, select, update

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_TABLE
from powercontext.builtin.runtime.models import GetMemoryEntryRequest
from powercontext.builtin.sources import validate_scope_id
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime


class SourceLifecycleError(ValueError):
    """Invalid lifecycle binding, event, dependency or generation eligibility."""


class SourceLifecycleEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    node_id: str
    revision: int = Field(gt=0)
    sequence: int = Field(gt=0)
    available: bool
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("lifecycle text must not be blank")  # noqa: TRY003
        return value


class _Node(BaseModel):
    node_id: str
    kind: Literal["source", "memory", "topic"]
    reference: dict[str, Any]
    parents: tuple[str, ...] = ()
    policy: Literal["all", "any"] = "all"
    revision: int = 1
    sequence: int = 0
    available: bool = True
    event: SourceLifecycleEvent | None = None


def _reference(node: _Node) -> SourceRef | MemoryCitation | ArtifactRef:
    model = {"source": SourceRef, "memory": MemoryCitation, "topic": ArtifactRef}[node.kind]
    return model.model_validate(node.reference)


def _propagate(nodes: dict[str, _Node]) -> None:
    resolved: dict[str, bool] = {}
    visiting: set[str] = set()

    def available(node_id: str) -> bool:
        if node_id in resolved:
            return resolved[node_id]
        if node_id in visiting or node_id not in nodes:
            raise SourceLifecycleError("invalid dependency graph")  # noqa: TRY003
        visiting.add(node_id)
        node = nodes[node_id]
        if node.kind == "source":
            value = node.available
        else:
            parents = [available(parent) for parent in node.parents]
            value = all(parents) if node.policy == "all" else any(parents)
        visiting.remove(node_id)
        resolved[node_id] = node.available = value
        return value

    for node_id in nodes:
        available(node_id)


class SourceLifecycleApplication:
    def __init__(self, runtime: BuiltinRuntime, database: AsyncDatabase | None) -> None:
        self.runtime = runtime
        self.database = database
        self.locks: dict[str, asyncio.Lock] = {}

    def for_scope(self, scope_id: str) -> ScopedSourceLifecycle:
        scope_id = validate_scope_id(scope_id)
        return ScopedSourceLifecycle(self, scope_id, self.locks.setdefault(scope_id, asyncio.Lock()))


class ScopedSourceLifecycle:
    def __init__(self, application: SourceLifecycleApplication, scope_id: str, lock: asyncio.Lock) -> None:
        self._application = application
        self.scope_id = scope_id
        self._lock = lock

    @property
    def _database(self) -> AsyncDatabase:
        if self._application.database is None:
            raise SourceLifecycleError("lifecycle persistence is unavailable")  # noqa: TRY003
        return self._application.database

    async def _load(self) -> dict[str, _Node]:
        async with self._database.transaction() as connection:
            payloads = await connection.scalars(
                select(SOURCE_LIFECYCLE_TABLE.c.payload).where(SOURCE_LIFECYCLE_TABLE.c.scope_id == self.scope_id)
            )
            nodes = [_Node.model_validate_json(bytes(payload)) for payload in payloads]
        return {node.node_id: node for node in nodes}

    async def _save(self, nodes: dict[str, _Node]) -> None:
        # The promised graphs have 16-20 nodes. A bounded scope snapshot keeps
        # desired state and event sequence together before native propagation;
        # retry reconciles actual native state rather than repeating commits.
        table = SOURCE_LIFECYCLE_TABLE
        async with self._database.transaction() as connection:
            existing = set(await connection.scalars(select(table.c.node_key).where(table.c.scope_id == self.scope_id)))
            for node in nodes.values():
                key = hashlib.sha256(node.node_id.encode()).hexdigest()
                ref = node.reference
                artifact = ref.get("memory_ref", ref)
                values = {
                    "kind": node.kind,
                    "reference_id": artifact.get("artifact_id", ""),
                    "reference_revision": artifact.get("revision", 1),
                    "available": node.available,
                    "payload": node.model_dump_json().encode(),
                }
                if key in existing:
                    await connection.execute(
                        update(table).where(table.c.scope_id == self.scope_id, table.c.node_key == key).values(**values)
                    )
                else:
                    await connection.execute(insert(table).values(scope_id=self.scope_id, node_key=key, **values))

    async def register_source(self, node_id: str, source_ref: SourceRef, *, revision: int = 1) -> None:
        if type(revision) is not int or revision <= 0:
            raise SourceLifecycleError("source revision must be a positive integer")  # noqa: TRY003
        await self._register(
            _Node(node_id=node_id, kind="source", reference=source_ref.model_dump(mode="json"), revision=revision)
        )

    async def register_memory(
        self, node_id: str, citation: MemoryCitation, *, parents: tuple[str, ...], policy: str = "all"
    ) -> None:
        await self._derived(node_id, "memory", citation, parents, policy)

    async def register_topic(
        self, node_id: str, artifact_ref: ArtifactRef, *, parents: tuple[str, ...], policy: str = "all"
    ) -> None:
        if artifact_ref.family != "topic-memory":
            raise SourceLifecycleError("topic binding requires Topic Memory")  # noqa: TRY003
        await self._derived(node_id, "topic", artifact_ref, parents, policy)

    async def _derived(self, node_id, kind, reference, parents, policy) -> None:
        if policy not in {"all", "any"} or isinstance(parents, str) or not parents:
            raise SourceLifecycleError("invalid dependency policy or parents")  # noqa: TRY003
        if any(not isinstance(parent, str) or not parent.strip() for parent in parents) or len(set(parents)) != len(
            parents
        ):
            raise SourceLifecycleError("parents must be unique nonblank node IDs")  # noqa: TRY003
        await self._register(
            _Node(
                node_id=node_id,
                kind=kind,
                reference=reference.model_dump(mode="json"),
                parents=tuple(sorted(parents)),
                policy=policy,
            )
        )

    async def _register(self, node: _Node) -> None:
        if not node.node_id.strip():
            raise SourceLifecycleError("node ID must not be blank")  # noqa: TRY003
        runtime = self._application.runtime
        async with runtime._scope_operation(self.scope_id), self._lock:
            nodes = await self._load()
            if node.node_id in nodes:
                old = nodes[node.node_id]
                fields = ("kind", "reference", "parents", "policy", "revision")
                if any(getattr(old, field) != getattr(node, field) for field in fields):
                    raise SourceLifecycleError("node binding conflict")  # noqa: TRY003
            else:
                if node.node_id in node.parents or any(parent not in nodes for parent in node.parents):
                    raise SourceLifecycleError("parents must be registered before their children")  # noqa: TRY003
                await self._validate_reference(node)
                nodes[node.node_id] = node
                _propagate(nodes)
                await self._save(nodes)
            await self._sync_memory(nodes)

    async def _validate_reference(self, node: _Node) -> None:
        runtime = self._application.runtime
        ref = _reference(node)
        try:
            if isinstance(ref, SourceRef):
                await runtime.records.for_scope(self.scope_id).get_source(ref.source_type, ref.source_id)
            elif isinstance(ref, MemoryCitation):
                await runtime.memory.for_scope(self.scope_id).get(GetMemoryEntryRequest(citation=ref))
            else:
                await runtime.records.for_scope(self.scope_id).get_artifact_revision(
                    ref.family, ref.artifact_id, ref.revision
                )
        except Exception as error:
            raise SourceLifecycleError("binding reference is not readable in this scope") from error  # noqa: TRY003

    async def _sync_memory(self, nodes: dict[str, _Node]) -> None:
        desired: dict[tuple[str, str], bool] = {}
        for node in nodes.values():
            if node.kind == "memory":
                ref = MemoryCitation.model_validate(node.reference)
                key = (ref.entry_id, ref.entry_version_id)
                desired[key] = desired.get(key, False) or node.available
        if not desired:
            return
        runtime = self._application.runtime
        async with runtime._context(self.scope_id) as context, runtime._locked(self.scope_id):
            service = context.artifacts.memory
            current = await service.head(context.artifacts.memory_artifact_id)
            entries = await service.entries(current)
            states = {entry.entry_id: entry.state for entry in current.content.manifest.entries}
            for active in (False, True):
                changed = tuple(
                    entry
                    for entry in entries
                    if (entry.entry_id, entry.entry_version_id) in desired
                    and desired[entry.entry_id, entry.entry_version_id] == active
                    and (states[entry.entry_id] == "active") != active
                )
                if changed:
                    method = service.reactivate if active else service.forget
                    current = await method(current, entries=changed, reason="source lifecycle dependency state")

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        runtime = self._application.runtime
        async with runtime._scope_operation(self.scope_id), self._lock:
            nodes = await self._load()
            node = nodes.get(event.node_id)
            if node is None or node.kind != "source" or node.revision != event.revision:
                raise SourceLifecycleError("unknown Source or lifecycle revision")  # noqa: TRY003
            if event.sequence < node.sequence or (event.sequence == node.sequence and event != node.event):
                raise SourceLifecycleError("event sequence conflicts with Source history")  # noqa: TRY003
            if event.sequence > node.sequence:
                node.sequence, node.available, node.event = event.sequence, event.available, event
                _propagate(nodes)
                await self._save(nodes)
            await self._sync_memory(nodes)
            return sorted(node_id for node_id, node in nodes.items() if node.available)

    async def resolve(self, node_id: str, *, purpose: str = "generation") -> SourceRef | MemoryCitation | ArtifactRef:
        if purpose not in {"generation", "audit"}:
            raise SourceLifecycleError("unknown lifecycle reference purpose")  # noqa: TRY003
        async with self._application.runtime._scope_operation(self.scope_id), self._lock:
            node = (await self._load()).get(node_id)
            if node is None or (purpose == "generation" and not node.available):
                raise SourceLifecycleError("node is unavailable for the requested purpose")  # noqa: TRY003
            return _reference(node)


__all__ = ["SourceLifecycleError", "SourceLifecycleEvent"]

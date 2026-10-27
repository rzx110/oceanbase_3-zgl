"""Task snapshots are immutable Sources in a real scoped PowerContext Runtime."""
from __future__ import annotations

from contextlib import AsyncExitStack
import hashlib
import json
from pathlib import Path

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft


class TaskState:
    def __init__(self, database: Path, project_id: str, task_id: str):
        self.database, self.project_id, self.task_id = database.resolve(), project_id, task_id

    async def __aenter__(self):
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._stack = AsyncExitStack()
        self.runtime = await self._stack.enter_async_context(open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{self.database}"))
        ))
        scope = await self.runtime.scopes.create(ScopeDraft(
            title=f"SDD task: {self.task_id}", summary=f"Project {self.project_id}: development progress",
            idempotency_key="sdd:" + hashlib.sha256(f"{self.project_id}\0{self.task_id}".encode()).hexdigest(),
        ))
        self.scope_id = scope.scope_id
        return self

    async def __aexit__(self, *_):
        await self._stack.aclose()

    async def load(self) -> dict | None:
        cursor = None
        latest = None
        while True:
            page = await self.runtime.records.for_scope(self.scope_id).list_sources(limit=100, cursor=cursor)
            for record in page.items:
                value = record.content
                if isinstance(value, str):
                    value = json.loads(value)
                if isinstance(value, dict) and value.get("schema") == "sdd-task-state/v1":
                    if latest is None or record.position > latest[0]:
                        latest = (record.position, record, value)
            cursor = page.next_cursor
            if cursor is None:
                break
        if latest is None:
            return None
        _, record, value = latest
        return {"state": value["state"], "source_id": record.source_id,
                "source_type": record.source_type, "scope_id": self.scope_id,
                "sequence": record.position, "content_digest": record.content_digest}

    async def save(self, state: dict) -> dict:
        value = {"schema": "sdd-task-state/v1", "project_id": self.project_id,
                 "task_id": self.task_id, "state": state}
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        source_id = "sdd-state:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
        receipt = await self.runtime.sources.for_scope(self.scope_id).capture(CaptureSource(
            source_id=source_id, content=text,
            metadata={"project_id": self.project_id, "task_id": self.task_id},
        ))
        loaded = await self.load()
        assert loaded is not None
        return {"saved_source_id": receipt.source_ref.source_id, "saved_sequence": receipt.sequence,
                "readback": loaded}

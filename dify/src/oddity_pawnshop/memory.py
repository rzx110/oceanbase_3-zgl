"""Append-only, scoped PowerContext sources; no alternative state database."""
from contextlib import AsyncExitStack
import asyncio
import hashlib
import json
from pathlib import Path

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import BaseValueNotFoundError
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft

from .rules import initial_state, plan_turn


class TurnConflict(ValueError):
    pass


class GameMemory:
    def __init__(self, database: Path, audit_path: Path | None = None):
        self.database = database.resolve()
        self.audit_path = audit_path
        self.locks = {}

    async def start(self):
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.stack = AsyncExitStack()
        self.runtime = await self.stack.enter_async_context(open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{self.database}"))))

    async def close(self):
        await self.stack.aclose()

    def audit(self, kind, **data):
        if self.audit_path:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"event": kind, **data}, ensure_ascii=False) + "\n")

    async def scope(self, player, item):
        key = hashlib.sha256(f"oddity-v1\0{player}\0{item}".encode()).hexdigest()
        value = await self.runtime.scopes.create(ScopeDraft(title=f"奇物当铺 {player} / {item}",
            summary="玩家与这一件奇物的互动记忆", idempotency_key="oddity:" + key))
        return value.scope_id

    @staticmethod
    def unwrap(record):
        content = json.loads(record.content) if isinstance(record.content, str) else record.content
        return {"state": content["state"], "last_story": content["narrative"],
                "memory_source": {"source_type": record.source_type, "source_id": record.source_id,
                    "scope_id": record.scope_id, "sequence": record.position, "content_digest": record.content_digest}}

    async def read(self, player, item):
        scope_id = await self.scope(player, item)
        cursor, latest = None, None
        while True:
            page = await self.runtime.records.for_scope(scope_id).list_sources(limit=100, cursor=cursor)
            for record in page.items:
                if record.source_id.startswith("oddity:") and (latest is None or record.position > latest.position):
                    latest = record
            cursor = page.next_cursor
            if cursor is None:
                break
        result = self.unwrap(latest) if latest else {"state": initial_state(), "last_story": "", "memory_source": None}
        self.audit("memory_read", player_code=player, item=item, **result)
        return result

    async def commit(self, request):
        key = (request["player_code"], request["item"])
        async with self.locks.setdefault(key, asyncio.Lock()):
            scope_id = await self.scope(*key)
            source_id = "oddity:" + request["operation_id"]
            domain = {k: request[k] for k in ("player_code", "item", "action", "utterance", "expected_turn", "operation_id", "state")}
            fingerprint = hashlib.sha256(json.dumps(domain, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            try:
                stored = await self.runtime.records.for_scope(scope_id).get_source("content", source_id)
            except BaseValueNotFoundError:
                stored = None
            if stored:
                content = json.loads(stored.content)
                if content["fingerprint"] != fingerprint:
                    raise TurnConflict("operation ID already belongs to different input")
                result = self.unwrap(stored)
                self.audit("idempotent_replay", player_code=key[0], item=key[1], source_id=source_id)
                return result
            current = await self.read(*key)
            if current["state"]["turn"] != request["expected_turn"]:
                raise TurnConflict("账本已更新，请重新运行以读取最新记忆。")
            proposed = plan_turn(*key, request["action"], request["utterance"], current["state"])
            if not proposed["write"] or proposed["operation_id"] != request["operation_id"] or proposed["state"] != request["state"]:
                raise TurnConflict("提交的游戏状态与规则不一致。")
            content = {"schema": "oddity-turn/v1", "state": request["state"], "narrative": request["narrative"],
                       "fingerprint": fingerprint, "previous_source": current["memory_source"]}
            await self.runtime.sources.for_scope(scope_id).capture(CaptureSource(
                source_id=source_id, content=json.dumps(content, ensure_ascii=False, sort_keys=True),
                metadata={"player_code": key[0], "item": key[1], "turn": request["state"]["turn"]}))
            record = await self.runtime.records.for_scope(scope_id).get_source("content", source_id)
            result = self.unwrap(record)
            self.audit("memory_write", player_code=key[0], item=key[1], **result)
            return result

    async def exact(self, player, item, source_id):
        scope_id = await self.scope(player, item)
        record = await self.runtime.records.for_scope(scope_id).get_source("content", source_id)
        result = self.unwrap(record)
        self.audit("exact_readback", player_code=player, item=item, **result)
        return result

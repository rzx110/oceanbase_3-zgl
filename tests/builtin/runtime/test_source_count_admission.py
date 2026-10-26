# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License").
"""Source identity counting remains correct when journal positions are sparse."""

from pathlib import Path
from uuid import uuid4

from sqlalchemy import update

from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository
from powercontext.builtin.persistence.tables import SOURCE_JOURNAL_HEADS_TABLE
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, open_builtin_contexts, open_builtin_runtime
from powercontext.builtin.runtime.processing_discovery import SourceProcessingPendingProvider
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentSource
from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME
from powercontext.sources import SourceMaterialization


async def test_sparse_positions_and_replays_do_not_inflate_count_or_duplicate_requests(tmp_path: Path) -> None:
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'counts.db'}"))
    async with open_builtin_runtime(config) as runtime:
        assert runtime.scopes is not None
        scope = await runtime.scopes.create(ScopeDraft(title="Sparse", summary="Identities", idempotency_key="sparse"))
        sources = runtime.sources.for_scope(scope.scope_id)
        for name in ("first", "second", "first"):
            await sources.capture(CaptureSource(source_id=name, content=name, metadata={}))
    async with open_builtin_contexts(config) as contexts:
        async with contexts.database.transaction() as connection:
            # A high-water position is not the number of persisted identities.
            await connection.execute(
                update(SOURCE_JOURNAL_HEADS_TABLE)
                .where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope.scope_id)
                .values(position=100)
            )
            lease = await ArtifactProcessingLeaseRepository().start_single_process_term(connection, str(uuid4()))
        provider = SourceProcessingPendingProvider(
            contexts.database, SOURCE_WINDOW_TRIGGER_NAME, "memory", count_threshold=3
        )
        fence = lease.fence("single-process")
        await provider.reconcile(None, 100, fence=fence)
        async with contexts.database.transaction() as connection:
            intent = await ArtifactProcessingIntentRepository().load(
                connection, scope.scope_id, SOURCE_WINDOW_TRIGGER_NAME
            )
            assert intent is not None and intent.requested_generation == 0
        context = await contexts.get(scope.scope_id)
        await context.sources.store.add(
            ContentSource(name="third", materialization=SourceMaterialization.CAPTURED, content="third")
        )
        await provider.reconcile(None, 100, fence=fence)
        await provider.reconcile(None, 100, fence=fence)
        async with contexts.database.transaction() as connection:
            intent = await ArtifactProcessingIntentRepository().load(
                connection, scope.scope_id, SOURCE_WINDOW_TRIGGER_NAME
            )
            assert intent is not None and intent.requested_generation == 1

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

"""Native seekdb comparison against the frozen two-statement index composition."""

from __future__ import annotations

import hashlib
import math
import re
import statistics
import sys
import time
from contextlib import contextmanager

import pytest
from sqlalchemy import Engine, event

from powercontext.builtin.artifacts.memory import EmbeddingProfile, MemoryEntryInput
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.memory_index import CompositeMemoryIndex
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.persistence.seekdb.memory_index import SeekDBMemoryIndex
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.tags import MemoryEntryTagTarget, TagFilter

pytestmark = pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="native seekdb requires Linux or macOS")
PROFILE = EmbeddingProfile(
    profile_id="hybrid-regression-384", model="offline-token-hash", dimension=384, distance="l2", normalization="unit"
)


class _Embedding:
    profile = PROFILE

    async def embed(self, texts, /):
        vectors = []
        for text in texts:
            vector = [0.0] * 384
            for term in re.findall(r"\w+", text.casefold()):
                digest = hashlib.sha256(term.encode()).digest()
                vector[int.from_bytes(digest[:2]) % 384] += 1.0 if digest[2] & 1 else -1.0
            norm = math.sqrt(sum(value * value for value in vector)) or 1
            vectors.append(tuple(value / norm for value in vector))
        return EmbeddingResult(vectors=tuple(vectors))


@contextmanager
def _capture():
    statements = []

    def record(_connection, _cursor, statement, parameters, _context, _many):
        if "AGAINST" in statement.upper() or "l2_distance" in statement.lower():
            statements.append((statement, parameters))

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(Engine, "before_cursor_execute", record)


async def _frozen_search(self, connection, scope_id, request, /):
    return await CompositeMemoryIndex.search(self, connection, scope_id, request)


def _identities(result):
    return tuple((hit.memory_ref, hit.entry_id, hit.entry_version_id, hit.text, hit.matched_by) for hit in result.hits)


@pytest.mark.asyncio
async def test_native_single_statement_matches_dual_query_at_384_dimensions(tmp_path, monkeypatch):
    durations = []
    async with open_builtin_contexts(
        BuiltinConfig(database=SeekDBConfig(path=tmp_path / "db")), embedding_model=_Embedding()
    ) as contexts:
        for scope_id in ("left", "right", "third"):
            memory = (await contexts.get(scope_id)).artifacts.memory
            head = await memory.remember(
                memory=None,
                mode="append",
                entries=tuple(
                    MemoryEntryInput(
                        kind="fact", text=f"Laboratory {number} cobalt instrument reports sample{number} pressure."
                    )
                    for number in range(173)
                ),
            )
            assert head is not None
            for number in range(20):
                query = f"sample{number} cobalt pressure"
                await memory.search(query, memories=(head,), mode="hybrid", limit=10)
                started = time.perf_counter()
                with _capture() as statements:
                    actual = await memory.search(query, memories=(head,), mode="hybrid", limit=10)
                durations.append((time.perf_counter() - started) * 1000)
                assert len(statements) == 1
                assert len(actual.hits) == len({hit.entry_id for hit in actual.hits}) == 10
                assert all(hit.memory_ref == head.as_ref() for hit in actual.hits)
                with monkeypatch.context() as patch:
                    patch.setattr(SeekDBMemoryIndex, "search", _frozen_search)
                    frozen = await memory.search(query, memories=(head,), mode="hybrid", limit=10)
                assert _identities(actual) == _identities(frozen)
                repeated = await memory.search(query, memories=(head,), mode="hybrid", limit=10)
                assert _identities(repeated) == _identities(actual)
                if number == 0:
                    async with contexts.database.transaction() as connection:
                        plan = "\n".join(
                            str(row[0])
                            for row in (
                                await connection.exec_driver_sql("EXPLAIN " + statements[0][0], statements[0][1])
                            ).all()
                        )
                    print(plan)
                    assert "TEXT RETRIEVAL SCAN" in plan
                    assert "VECTOR INDEX" in plan
            selected = next(entry for entry in await memory.entries(head) if "sample3 " in entry.text)
            target = MemoryEntryTagTarget(artifact_id=head.artifact_id, entry_id=selected.entry_id)
            tags = await contexts.records.get_tags(scope_id, target)
            await contexts.records.replace_tags(scope_id, target, ("selected", "instrument"), expected_etag=tags.etag)
            for match in ("all", "any"):
                tag_filter = TagFilter(tags=("selected", "instrument"), match=match)
                with _capture() as statements:
                    tagged = await memory.search(
                        "sample3 cobalt", memories=(head,), mode="hybrid", limit=10, tag_filter=tag_filter
                    )
                assert len(statements) == 1
                assert [hit.entry_id for hit in tagged.hits] == [selected.entry_id]
                assert tagged.hits[0].matched_by == ("fts", "vector")
                async with contexts.database.transaction() as connection:
                    plan = "\n".join(
                        str(row[0])
                        for row in (
                            await connection.exec_driver_sql("EXPLAIN " + statements[0][0], statements[0][1])
                        ).all()
                    )
                print("tagged", match, plan)
                assert "TEXT RETRIEVAL SCAN" in plan
                assert "VECTOR INDEX" in plan
    print(f"synthetic 60-query full-search p50_ms={statistics.median(durations):.3f}")

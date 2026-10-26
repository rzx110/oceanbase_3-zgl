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

"""HTTP protocol boundaries and identity-preserving multi-batch integration."""

from __future__ import annotations

import asyncio
import json
import math
import random
from pathlib import Path

import httpx
import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.artifacts.memory.http_reranker import HTTPMemoryReranker
from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts


def _hit(rank: int) -> MemoryHit:
    return MemoryHit(
        memory_ref=ArtifactRef(family="memory", artifact_id="original-memory", revision=3),
        entry_id=f"entry-{rank}",
        entry_version_id=f"version-{rank}",
        text=f"原始候选 {rank}: database timeout",
        score=1.0 / rank,
        matched_by=("fts",),
    )


@pytest.mark.parametrize("query_index", range(20))
async def test_full_pool_matches_global_score_oracle_and_request_budget(query_index: int) -> None:
    candidates = tuple(_hit(rank) for rank in range(1, 174))
    rng = random.Random(20261026 + query_index)  # noqa: S311 - Reproducible synthetic scores, not security.
    # Quantized negative and positive scores deliberately produce ties
    # across batches, with every response in the reverse request order.
    oracle = {rank: rng.randrange(-10, 11) / 10 for rank in range(1, 174)}
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.method == "POST" and request.url.path == "/score"
        assert body["query"] == f"query {query_index}"
        assert 1 <= len(body["candidates"]) <= 7
        for item in body["candidates"]:
            assert item["text"] == candidates[item["rank"] - 1].text
            seen.append(item["rank"])
        return httpx.Response(
            200,
            json={
                "scores": [
                    {"rank": item["rank"], "score": oracle[item["rank"]]} for item in reversed(body["candidates"])
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        reranker = HTTPMemoryReranker("https://local.invalid/score", client=client, batch_size=7)
        decision = await reranker.rerank(f"query {query_index}", candidates, 10)
        assert not client.is_closed
    assert decision.selected_ranks == tuple(sorted(oracle, key=lambda rank: (-oracle[rank], rank))[:10])
    assert sorted(seen) == list(range(1, 174))
    assert decision.usage.requests == math.ceil(173 / 7)
    assert not decision.used_fallback and decision.discarded_rank_count == 0


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"scores": None},
        {"scores": {}},
        {"scores": [None]},
        {"scores": [{"rank": True, "score": 1}]},
        {"scores": [{"rank": 1.0, "score": 1}]},
        {"scores": [{"rank": "1", "score": 1}]},
        {"scores": [{"rank": 1, "score": True}]},
        {"scores": [{"rank": 1, "score": "0.5"}]},
        {"scores": [{"rank": 1, "score": None}]},
        {"scores": [{"rank": 1, "score": float("-inf")}]},
    ],
)
async def test_response_types_are_not_silently_coerced(payload: object) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=json.dumps(payload).encode()))
    ) as client:
        with pytest.raises(ValueError):
            await HTTPMemoryReranker("https://local.invalid", client=client).rerank("q", (_hit(1),), 1)


@pytest.mark.parametrize(
    "content",
    [
        b"not JSON",
        b"\xff",
        b'{"scores":[],"scores":[{"rank":1,"score":1}]}',
        b'{"scores":[{"rank":2,"rank":1,"score":1}]}',
    ],
)
async def test_invalid_or_ambiguous_json_is_rejected(content: bytes) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))
    ) as client:
        with pytest.raises(ValueError):
            await HTTPMemoryReranker("https://local.invalid", client=client).rerank("q", (_hit(1),), 1)


async def test_later_batch_cannot_reuse_an_earlier_rank() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        rank = json.loads(request.content)["candidates"][0]["rank"]
        return httpx.Response(200, json={"scores": [{"rank": 1 if rank == 2 else rank, "score": 0.5}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            await HTTPMemoryReranker("https://local.invalid", client=client, batch_size=1).rerank(
                "q", (_hit(1), _hit(2), _hit(3)), 1
            )
    assert calls == 2


async def test_large_integer_scores_keep_their_order_and_equal_scores_keep_input_order() -> None:
    scores = {1: 2**60, 2: 2**60 + 1, 3: -5, 4: -5}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "scores": [
                    {"rank": item["rank"], "score": scores[item["rank"]]}
                    for item in json.loads(request.content)["candidates"]
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        decision = await HTTPMemoryReranker("https://local.invalid", client=client, batch_size=1).rerank(
            "q", tuple(_hit(rank) for rank in range(1, 5)), 4
        )
    assert decision.selected_ranks == (2, 1, 3, 4)
    assert decision.usage.requests == 4


@pytest.mark.parametrize("value", [True, 1.5, "2", 0, -1])
async def test_invalid_numeric_bounds_never_issue_http(value: object) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        pytest.fail("invalid bounds must fail before issuing HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            HTTPMemoryReranker("https://local.invalid", client=client, batch_size=value)  # ty: ignore[invalid-argument-type]
        with pytest.raises(ValueError):
            await HTTPMemoryReranker("https://local.invalid", client=client).rerank("q", (_hit(1),), value)  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize("status", [302, 429, 503])
async def test_http_errors_are_not_followed_or_replaced(status: int) -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, headers={"location": "https://other.invalid"}, content=b"not JSON")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(httpx.HTTPStatusError) as error:
            await HTTPMemoryReranker("https://local.invalid", client=client).rerank("q", (_hit(1),), 1)
        assert error.value.response.status_code == status
    assert len(seen) == 1


async def test_timeout_is_preserved() -> None:
    timeout = httpx.ReadTimeout("scoring timeout")

    def handler(_: httpx.Request) -> httpx.Response:
        raise timeout

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ReadTimeout) as error:
            await HTTPMemoryReranker("https://local.invalid", client=client).rerank("q", (_hit(1),), 1)
        assert error.value is timeout


async def test_real_loopback_http_and_reopened_memory_preserve_original_hits(tmp_path: Path) -> None:
    received = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            headers = (await reader.readuntil(b"\r\n\r\n")).decode("ascii")
            size = next(
                int(line.split(":", 1)[1])
                for line in headers.splitlines()
                if line.lower().startswith("content-length:")
            )
            body = json.loads(await reader.readexactly(size))
            received.append((headers, body))
            payload = json.dumps({
                "scores": [
                    {"rank": item["rank"], "score": int(item["text"].rsplit(" ", 1)[-1])}
                    for item in reversed(body["candidates"])
                ]
            }).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\n\r\n"
                + payload
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    async with server:
        port = server.sockets[0].getsockname()[1]
        async with httpx.AsyncClient(trust_env=False, headers={"X-Caller": "retained"}) as client:
            reranker = HTTPMemoryReranker(
                f"http://127.0.0.1:{port}/score", client=client, batch_size=7, policy_id="local-test"
            )
            config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}"))
            async with open_builtin_contexts(config) as contexts:
                service = (await contexts.get("http-rerank")).artifacts.memory
                memory = await service.remember(
                    memory=None,
                    mode="append",
                    entries=tuple(
                        MemoryEntryInput(kind="fact", text=f"database timeout evidence {index}") for index in range(20)
                    ),
                )
                assert memory is not None
                original = await service.search("database", memories=(memory,), mode="fts", limit=20)
            async with open_builtin_contexts(config, memory_reranker=reranker) as contexts:
                service = (await contexts.get("http-rerank")).artifacts.memory
                result = await service.search("database", memories=(memory,), mode="fts", limit=10)
                expected = tuple(sorted(original.hits, key=lambda hit: -int(hit.text.rsplit(" ", 1)[-1]))[:10])
                assert result.hits == expected
                assert result.rerank is not None
                assert result.rerank.policy_id == "local-test"
                assert result.rerank.usage.requests == 3
                assert await service.expand(result.hits)
            assert not client.is_closed
    assert len(received) == 3
    assert all("x-caller: retained" in headers.lower() for headers, _ in received)

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

"""Late joiners, provider accounting, asymmetric inputs and failure isolation."""

from __future__ import annotations

import asyncio
import gc

import pytest

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult, InferenceUsage
from powercontext.builtin.inference.coalescing import CoalescingEmbeddingModel


class Metered:
    profile = EmbeddingProfile(profile_id="coalescing-test", model="offline", dimension=1)

    def __init__(self):
        self.calls = []

    async def embed(self, texts):
        self.calls.append(texts)
        return EmbeddingResult(
            vectors=tuple((float(len(text)),) for text in texts),
            usage=InferenceUsage(requests=3, input_tokens=11, output_tokens=0),
        )


async def test_late_waiter_joins_running_work_with_cache_disabled_and_keeps_usage() -> None:
    class Blocked(Metered):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def embed(self, texts):
            self.started.set()
            await self.release.wait()
            return await super().embed(texts)

    base = Blocked()
    model = CoalescingEmbeddingModel(base, max_batch_size=1, coalesce_ms=0, cache_size=0)
    first = asyncio.create_task(model.embed(("shared", "shared")))
    await base.started.wait()
    late = asyncio.create_task(model.embed(("shared",)))
    await asyncio.sleep(0)
    base.release.set()
    one, two = await asyncio.gather(first, late)
    assert one.vectors == ((6.0,), (6.0,)) and two.vectors == ((6.0,),)
    assert one.usage == two.usage == InferenceUsage(requests=3, input_tokens=11, output_tokens=0)
    assert base.calls == [("shared",)]
    assert (await model.embed(("shared",))).usage.requests == 3
    assert len(base.calls) == 2


async def test_one_waiter_charges_each_distinct_provider_batch_once() -> None:
    base = Metered()
    model = CoalescingEmbeddingModel(base, max_batch_size=2, coalesce_ms=0)
    assert model.profile is base.profile
    result = await model.embed(("a", "bb", "a", "ccc", "dddd", "eeeee", "ccc"))
    assert result.vectors == ((1.0,), (2.0,), (1.0,), (3.0,), (4.0,), (5.0,), (3.0,))
    assert result.usage == InferenceUsage(requests=9, input_tokens=33, output_tokens=0)
    mixed = await model.embed(("bb", "new", "new", "eeeee"))
    assert mixed.usage == InferenceUsage(requests=3, input_tokens=11, output_tokens=0)
    assert base.calls == [("a", "bb"), ("ccc", "dddd"), ("eeeee",), ("new",)]


async def test_independent_failed_batch_does_not_block_success_and_can_retry() -> None:
    class Flaky(Metered):
        fail = True

        async def embed(self, texts):
            if "bad" in texts and self.fail:
                self.fail = False
                self.calls.append(texts)
                raise RuntimeError("one batch failed")  # noqa: TRY003
            return await super().embed(texts)

    base = Flaky()
    model = CoalescingEmbeddingModel(base, max_batch_size=1, coalesce_ms=0)
    results = await asyncio.gather(model.embed(("bad",)), model.embed(("good",)), return_exceptions=True)
    assert isinstance(results[0], RuntimeError)
    assert isinstance(results[1], EmbeddingResult) and results[1].vectors == ((4.0,),)
    assert (await model.embed(("good",))).usage.requests == 0
    assert (await model.embed(("bad",))).vectors == ((3.0,),)
    assert base.calls.count(("bad",)) == 2


async def test_invalid_batch_is_not_partially_cached() -> None:
    class WrongCount(Metered):
        async def embed(self, texts):
            if len(texts) == 2:
                self.calls.append(texts)
                return EmbeddingResult(vectors=((1.0,),))
            return await super().embed(texts)

    base = WrongCount()
    model = CoalescingEmbeddingModel(base, max_batch_size=2, coalesce_ms=0)
    with pytest.raises(ValueError):
        await model.embed(("a", "bb"))
    assert (await model.embed(("a",))).usage.requests == 3
    assert base.calls == [("a", "bb"), ("a",)]


async def test_all_waiters_cancelled_then_provider_failure_has_no_unhandled_exception() -> None:
    class Failing(Metered):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.finished = asyncio.Event()

        async def embed(self, texts):
            self.started.set()
            await self.release.wait()
            self.finished.set()
            raise RuntimeError("failure after callers left")  # noqa: TRY003

    base = Failing()
    model = CoalescingEmbeddingModel(base, coalesce_ms=0)
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    unhandled = []
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        callers = [asyncio.create_task(model.embed(("shared",))) for _ in range(2)]
        await base.started.wait()
        for caller in callers:
            caller.cancel()
        for caller in callers:
            with pytest.raises(asyncio.CancelledError):
                await caller
        base.release.set()
        await base.finished.wait()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        gc.collect()
        assert unhandled == []
    finally:
        loop.set_exception_handler(previous)


async def test_asymmetric_query_embeddings_do_not_share_document_vectors() -> None:
    class Asymmetric(Metered):
        async def embed_query(self, texts):
            self.calls.append(("QUERY", *texts))
            return EmbeddingResult(vectors=tuple((-1.0,) for _ in texts), usage=InferenceUsage(requests=2))

    base = Asymmetric()
    model = CoalescingEmbeddingModel(base, coalesce_ms=0)
    document, query = await asyncio.gather(model.embed(("same",)), model.embed_query(("same",)))
    assert document.vectors == ((4.0,),) and query.vectors == ((-1.0,),)
    assert (await model.embed_query(("same",))).usage.requests == 0
    assert (await model.embed(("same",))).usage.requests == 0
    assert len(base.calls) == 2


async def test_tiny_cache_does_not_drop_vectors_already_owed_to_waiters() -> None:
    model = CoalescingEmbeddingModel(Metered(), max_batch_size=2, coalesce_ms=0, cache_size=1)
    texts = tuple("x" * length for length in range(1, 16))
    result = await model.embed(texts + texts)
    assert result.vectors == tuple((float(len(text)),) for text in texts + texts)
    assert result.usage.requests == 24


@pytest.mark.parametrize(
    "settings",
    [
        {"max_batch_size": 0},
        {"max_batch_size": True},
        {"max_batch_size": 1.5},
        {"cache_size": -1},
        {"cache_size": False},
        {"cache_size": 1.5},
        {"coalesce_ms": -1},
        {"coalesce_ms": float("nan")},
        {"coalesce_ms": float("inf")},
        {"coalesce_ms": True},
        {"coalesce_ms": "5"},
    ],
)
def test_invalid_scheduling_bounds_fail_before_any_request(settings):
    with pytest.raises(ValueError):
        CoalescingEmbeddingModel(Metered(), **settings)

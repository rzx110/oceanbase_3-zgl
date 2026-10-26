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

"""Loop-local in-flight embedding sharing and a bounded successful-vector LRU."""

from __future__ import annotations

import asyncio
import math
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel, QueryEmbeddingModel

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory.models import EmbeddingProfile

_Key = tuple[bool, str]


@dataclass(frozen=True)
class _Delivered:
    vector: EmbeddingVector
    batch_id: int | None = None
    usage: InferenceUsage | None = None


def _observe_exception(future: asyncio.Future[_Delivered]) -> None:
    # If every waiter cancels, failures still have an owner. Retrieving an
    # exception here does not change the result seen by surviving waiters.
    if not future.cancelled():
        future.exception()


def _usage(delivered: list[_Delivered]) -> InferenceUsage:
    batches = {item.batch_id: item.usage for item in delivered if item.batch_id is not None and item.usage is not None}
    values = tuple(batches.values())
    return InferenceUsage(
        requests=sum(value.requests for value in values),
        input_tokens=None
        if not values or any(value.input_tokens is None for value in values)
        else sum(value.input_tokens or 0 for value in values),
        output_tokens=None
        if not values or any(value.output_tokens is None for value in values)
        else sum(value.output_tokens or 0 for value in values),
    )


class CoalescingEmbeddingModel:
    """Share pending texts without letting an individual waiter cancel the work."""

    def __init__(
        self, model: EmbeddingModel, *, max_batch_size: int = 64, coalesce_ms: float = 5.0, cache_size: int = 1024
    ) -> None:
        if type(max_batch_size) is not int or max_batch_size <= 0:
            raise ValueError("max_batch_size must be a positive integer")  # noqa: TRY003
        if type(cache_size) is not int or cache_size < 0:
            raise ValueError("cache_size must be a nonnegative integer")  # noqa: TRY003
        if (
            isinstance(coalesce_ms, bool)
            or not isinstance(coalesce_ms, (int, float))
            or not math.isfinite(coalesce_ms)
            or coalesce_ms < 0
        ):
            raise ValueError("coalesce_ms must be finite and nonnegative")  # noqa: TRY003
        self._model = model
        self._max_batch_size = max_batch_size
        self._delay = coalesce_ms / 1000
        self._cache_size = cache_size
        self._cache: OrderedDict[_Key, EmbeddingVector] = OrderedDict()
        self._inflight: dict[_Key, asyncio.Future[_Delivered]] = {}
        self._queued: dict[_Key, asyncio.Future[_Delivered]] = {}
        self._timer: asyncio.TimerHandle | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._next_batch = 0

    @property
    def profile(self) -> EmbeddingProfile:
        return self._model.profile

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return await self._embed(texts, query=False)

    async def embed_query(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        # Symmetric providers share document/query caches. Providers with a
        # distinct query port keep both requests and cached vectors separate.
        return await self._embed(texts, query=isinstance(self._model, QueryEmbeddingModel))

    async def _embed(self, texts: tuple[str, ...], *, query: bool) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=())
        loop = asyncio.get_running_loop()
        pending = {text: self._acquire((query, text), loop) for text in texts}
        delivered = await asyncio.gather(*(asyncio.shield(future) for future in pending.values()))
        rows = dict(zip(pending, delivered, strict=True))
        return EmbeddingResult(vectors=tuple(rows[text].vector for text in texts), usage=_usage(delivered))

    def _acquire(self, key: _Key, loop: asyncio.AbstractEventLoop) -> asyncio.Future[_Delivered]:
        if key in self._cache:
            self._cache.move_to_end(key)
            cached = loop.create_future()
            cached.set_result(_Delivered(self._cache[key]))
            return cached
        if key in self._inflight:
            return self._inflight[key]
        future: asyncio.Future[_Delivered] = loop.create_future()
        future.add_done_callback(_observe_exception)
        self._inflight[key] = self._queued[key] = future
        if self._timer is None:
            # Even a zero window yields once, collecting callers already ready
            # at the embedding boundary before dispatching bounded requests.
            self._timer = loop.call_later(self._delay, self._flush)
        return future

    def _flush(self) -> None:
        self._timer = None
        groups: dict[bool, list[tuple[_Key, asyncio.Future[_Delivered]]]] = {}
        for key, future in self._queued.items():
            groups.setdefault(key[0], []).append((key, future))
        self._queued.clear()
        for entries in groups.values():
            for start in range(0, len(entries), self._max_batch_size):
                self._next_batch += 1
                task = asyncio.create_task(
                    self._execute(tuple(entries[start : start + self._max_batch_size]), self._next_batch)
                )
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def _execute(self, entries: tuple[tuple[_Key, asyncio.Future[_Delivered]], ...], batch_id: int) -> None:
        try:
            texts = tuple(key[1] for key, _ in entries)
            if entries[0][0][0] and isinstance(self._model, QueryEmbeddingModel):
                result = await self._model.embed_query(texts)
            else:
                result = await self._model.embed(texts)
            if len(result.vectors) != len(entries):
                raise ValueError("embedding vector count does not match the requested text count")  # noqa: TRY003, TRY301
            for (key, future), vector in zip(entries, result.vectors, strict=True):
                if self._cache_size:
                    self._cache[key] = vector
                    self._cache.move_to_end(key)
                    while len(self._cache) > self._cache_size:
                        self._cache.popitem(last=False)
                future.set_result(_Delivered(vector, batch_id, result.usage))
        except (Exception, asyncio.CancelledError) as error:
            for _, future in entries:
                if not future.done():
                    future.set_exception(error)
        finally:
            for key, _ in entries:
                self._inflight.pop(key, None)


__all__ = ["CoalescingEmbeddingModel"]

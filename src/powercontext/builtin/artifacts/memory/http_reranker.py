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

"""Identity-preserving HTTP scoring with global, stable batch selection."""

from __future__ import annotations

import json
from typing import Annotated

import httpx
from pydantic import BaseModel, ConfigDict, Field

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage


class _ScoredRank(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    rank: int
    # Preserve integer precision instead of rounding distinct large scores into
    # a floating-point tie. Strict numeric types exclude bools and numeric strings.
    score: int | Annotated[float, Field(allow_inf_nan=False)]


class _ScoreResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scores: list[_ScoredRank]


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("HTTP rerank JSON contains a duplicate field")  # noqa: TRY003
        result[key] = value
    return result


def _batch_scores(content: bytes, expected_ranks: set[int]) -> list[_ScoredRank]:
    response = _ScoreResponse.model_validate(json.loads(content, object_pairs_hook=_unique_object))
    actual_ranks = [item.rank for item in response.scores]
    if len(actual_ranks) != len(expected_ranks) or set(actual_ranks) != expected_ranks:
        raise ValueError("HTTP rerank response must cover every batch rank exactly once")  # noqa: TRY003
    return response.scores


class HTTPMemoryReranker:
    """Score each original candidate once using the caller-owned async client."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1",
    ) -> None:
        if type(batch_size) is not int or batch_size <= 0:
            raise ValueError("HTTP rerank batch_size must be a positive integer")  # noqa: TRY003
        self.policy_id = policy_id
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size

    async def rerank(self, query: str, candidates: tuple[MemoryHit, ...], limit: int, /) -> MemoryRerankDecision:
        """Select globally by descending service score and ascending input rank."""

        if not candidates:
            raise ValueError("HTTP rerank requires at least one candidate")  # noqa: TRY003
        if type(limit) is not int or not 1 <= limit <= len(candidates):
            raise ValueError("HTTP rerank limit must be an integer within the candidate count")  # noqa: TRY003
        scores: list[_ScoredRank] = []
        requests = 0
        for start in range(0, len(candidates), self._batch_size):
            batch = [
                {"rank": rank, "text": candidate.text}
                for rank, candidate in enumerate(candidates[start : start + self._batch_size], start=start + 1)
            ]
            requests += 1
            # Redirects are non-2xx errors under this protocol; following them
            # would hide that response and issue an unaccounted extra request.
            response = await self._client.post(
                self._endpoint, json={"query": query, "candidates": batch}, follow_redirects=False
            )
            response.raise_for_status()
            scores.extend(_batch_scores(response.content, set(range(start + 1, start + len(batch) + 1))))
        selected = sorted(scores, key=lambda item: (-item.score, item.rank))[:limit]
        return MemoryRerankDecision(
            selected_ranks=tuple(item.rank for item in selected), usage=InferenceUsage(requests=requests)
        )


__all__ = ["HTTPMemoryReranker"]

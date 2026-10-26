# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""Canonical UTF-8 context packs and independently replayable prefix deltas."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

_PACK = "powercontext.context-pack.v1"
_DELTA = "powercontext.context-pack-delta.v1"


class ContextPackError(ValueError):
    """Invalid context bytes, wire encoding, or reconstruction identity."""


def _json(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ContextPackError("duplicate wire field")  # noqa: TRY003
        result[key] = value
    return result


def _decode(raw: bytes, schema: str, fields: set[str]) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
    except (ValueError, UnicodeError, AttributeError) as error:
        raise ContextPackError("invalid context pack JSON") from error  # noqa: TRY003
    if not isinstance(value, dict) or set(value) != fields or value["schema"] != schema:
        raise ContextPackError("invalid context pack wire fields")  # noqa: TRY003
    return value


def _items(items: tuple[bytes, ...]) -> None:
    if not isinstance(items, tuple) or any(not isinstance(item, bytes) for item in items):
        raise ContextPackError("chunks must be a tuple of bytes")  # noqa: TRY003
    try:
        for item in items:
            item.decode("utf-8")
    except UnicodeError as error:
        raise ContextPackError("chunks must contain valid UTF-8") from error  # noqa: TRY003


def _encoded(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _decoded(values: object) -> tuple[bytes, ...]:
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ContextPackError("wire chunks must be a Base64 string array")  # noqa: TRY003
    try:
        items = tuple(base64.b64decode(value, validate=True) for value in cast(list[str], values))
    except (ValueError, binascii.Error) as error:
        raise ContextPackError("invalid Base64 chunk") from error  # noqa: TRY003
    if _encoded(items) != values:
        raise ContextPackError("noncanonical Base64 chunk")  # noqa: TRY003
    _items(items)
    return items


def _digest(items: tuple[bytes, ...]) -> str:
    return "sha256:" + hashlib.sha256(_json({"schema": _PACK, "items": _encoded(items)})).hexdigest()


def _validate_digest(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ContextPackError("invalid digest format")  # noqa: TRY003


@dataclass(frozen=True)
class ContextPack:
    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    def __post_init__(self) -> None:
        _items(self.items)
        if self.digest != _digest(self.items):
            raise ContextPackError("context pack digest mismatch")  # noqa: TRY003
        if type(self.byte_count) is not int or self.byte_count != len(self.to_bytes()):
            raise ContextPackError("context pack byte count mismatch")  # noqa: TRY003

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        _items(items)
        digest = _digest(items)
        payload = {"schema": _PACK, "items": _encoded(items), "digest": digest, "byte_count": 0}
        while True:
            size = len(_json(payload))
            if payload["byte_count"] == size:
                return cls(items, digest, size)
            payload["byte_count"] = size

    @classmethod
    def from_content(cls, content: str | bytes, *, chunk_bytes: int = 512) -> ContextPack:
        if type(chunk_bytes) is not int or chunk_bytes <= 0:
            raise ContextPackError("chunk_bytes must be a positive integer")  # noqa: TRY003
        try:
            raw = content.encode("utf-8") if isinstance(content, str) else content
            if not isinstance(raw, bytes):
                raise ContextPackError("content must be text or bytes")  # noqa: TRY003
            raw.decode("utf-8")
        except UnicodeError as error:
            raise ContextPackError("invalid UTF-8 content") from error  # noqa: TRY003
        chunks = []
        start = 0
        while start < len(raw):
            end = min(start + chunk_bytes, len(raw))
            while end < len(raw) and raw[end] & 0xC0 == 0x80:
                end -= 1
            if end == start:
                raise ContextPackError("chunk limit cannot fit a UTF-8 character")  # noqa: TRY003
            chunks.append(raw[start:end])
            start = end
        return cls.build(tuple(chunks))

    def to_bytes(self) -> bytes:
        return _json({
            "schema": _PACK,
            "items": _encoded(self.items),
            "digest": self.digest,
            "byte_count": self.byte_count,
        })

    @classmethod
    def from_bytes(cls, raw: bytes) -> ContextPack:
        value = _decode(raw, _PACK, {"schema", "items", "digest", "byte_count"})
        return cls(_decoded(value["items"]), value["digest"], value["byte_count"])


@dataclass(frozen=True)
class ContextPackDelta:
    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    def __post_init__(self) -> None:
        _validate_digest(self.base_digest)
        _validate_digest(self.target_digest)
        if type(self.prefix_items) is not int or self.prefix_items < 0:
            raise ContextPackError("prefix_items must be a nonnegative integer")  # noqa: TRY003
        _items(self.appended)

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        prefix = 0
        for left, right in zip(base.items, target.items, strict=False):
            if left != right:
                break
            prefix += 1
        return cls(base.digest, target.digest, prefix, target.items[prefix:])

    def to_bytes(self) -> bytes:
        return _json({
            "schema": _DELTA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": _encoded(self.appended),
        })

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    @classmethod
    def from_bytes(cls, raw: bytes) -> ContextPackDelta:
        value = _decode(raw, _DELTA, {"schema", "base_digest", "target_digest", "prefix_items", "appended"})
        return cls(value["base_digest"], value["target_digest"], value["prefix_items"], _decoded(value["appended"]))


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    if base.digest != delta.base_digest or delta.prefix_items > len(base.items):
        raise ContextPackError("delta base or prefix does not match")  # noqa: TRY003
    target = ContextPack.build(base.items[: delta.prefix_items] + delta.appended)
    if target.digest != delta.target_digest:
        raise ContextPackError("delta target digest mismatch")  # noqa: TRY003
    return target


__all__ = ["ContextPack", "ContextPackDelta", "ContextPackError", "apply_context_pack_delta"]

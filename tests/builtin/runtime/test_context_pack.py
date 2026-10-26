# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License").
"""Malformed wire boundaries and independent context reconstruction."""

import base64
import json
import subprocess
import sys

import pytest

from powercontext.builtin.runtime.context_pack import (
    ContextPack,
    ContextPackDelta,
    ContextPackError,
    apply_context_pack_delta,
)


@pytest.mark.parametrize("chunk_bytes", [1, 2, 3, 4, 7, 512])
def test_chunk_limits_preserve_utf8_and_original_bytes(chunk_bytes):
    text = "字🙂 café\r\nabc\n" * 80 if chunk_bytes >= 4 else "éabc" * 80
    if chunk_bytes == 1:
        with pytest.raises(ContextPackError):
            ContextPack.from_content(text, chunk_bytes=chunk_bytes)
        return
    pack = ContextPack.from_content(text, chunk_bytes=chunk_bytes)
    assert b"".join(pack.items) == text.encode()
    assert all(0 < len(item) <= chunk_bytes and item.decode("utf-8") for item in pack.items)
    assert ContextPack.from_bytes(pack.to_bytes()) == pack


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "wrong"),
        ("extra", True),
        ("byte_count", True),
        ("byte_count", 99999),
        ("items", ["!!!"]),
        ("items", ["Zh=="]),
        ("items", [base64.b64encode(b"\xff").decode()]),
    ],
)
def test_pack_rejects_malformed_encodings_counts_and_fields(field, value):
    payload = json.loads(ContextPack.build((b"f",)).to_bytes())
    payload[field] = value
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(json.dumps(payload).encode())


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "wrong"),
        ("extra", 1),
        ("base_digest", "bad"),
        ("target_digest", None),
        ("prefix_items", "1"),
        ("prefix_items", False),
        ("appended", [3]),
        ("appended", ["bad Base64!"]),
    ],
)
def test_delta_rejects_malformed_fields(field, value):
    base = ContextPack.build((b"a",))
    payload = json.loads(ContextPackDelta.between(base, ContextPack.build((b"a", b"b"))).to_bytes())
    payload[field] = value
    with pytest.raises(ContextPackError):
        ContextPackDelta.from_bytes(json.dumps(payload).encode())


def test_duplicate_json_fields_cannot_hide_tampering():
    raw = ContextPack.build((b"x",)).to_bytes()
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(raw.replace(b'"byte_count":', b'"byte_count":1,"byte_count":'))


def test_receiver_replays_from_serialized_bytes_in_a_separate_process():
    contents = ["中文\nfirst\n", "中文\nfirst\nsecond\n", "changed at the front\n", "", "tail"]
    base = None
    frames = []
    for content in contents:
        target = ContextPack.from_content(content, chunk_bytes=7)
        packet = target if base is None else ContextPackDelta.between(base, target)
        frames.append({"wire": base64.b64encode(packet.to_bytes()).decode(), "expected": content})
        base = target
    receiver = """
import sys,json,base64
from powercontext.builtin.runtime.context_pack import ContextPack,ContextPackDelta,apply_context_pack_delta
base=None
for frame in json.load(sys.stdin):
    wire=base64.b64decode(frame["wire"])
    base=ContextPack.from_bytes(wire) if base is None else apply_context_pack_delta(base,ContextPackDelta.from_bytes(wire))
    assert b"".join(base.items).decode()==frame["expected"]
print("replayed")
"""
    result = subprocess.run(
        [sys.executable, "-c", receiver], input=json.dumps(frames), capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "replayed"


def test_any_valid_prefix_reconstructs_and_zero_prefix_supports_empty_target():
    base = ContextPack.build((b"one", b"two", b"three"))
    target = ContextPack.build((b"one", b"two", b"four"))
    for prefix in range(3):
        delta = ContextPackDelta(base.digest, target.digest, prefix, target.items[prefix:])
        assert apply_context_pack_delta(base, ContextPackDelta.from_bytes(delta.to_bytes())) == target
    empty = ContextPack.build(())
    assert apply_context_pack_delta(base, ContextPackDelta.between(base, empty)) == empty

# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License").
"""Literal provenance and budget regressions, using independently authored text."""

import json

import pytest
from pydantic import ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryHit
from powercontext.builtin.runtime import PrepareContextRequest
from powercontext.builtin.runtime.prepared_context import PreparedContextBuilder, PreparedMemoryCandidates


def group(scope, text, entry="entry"):
    reference = ArtifactRef(family="memory", artifact_id="memory", revision=3)
    return PreparedMemoryCandidates(
        scope_id=scope,
        memory_ref=reference,
        hits=(
            MemoryHit(
                memory_ref=reference,
                entry_id=entry,
                entry_version_id=entry + "-v1",
                text=text,
                score=1,
                matched_by=("fts",),
            ),
        ),
    )


def build(groups, query, budget):
    request = PrepareContextRequest(query=query, max_bytes=budget, evidence_selection=True)
    return PreparedContextBuilder().build_scopes_result(
        request=request,
        current_scope_id="one",
        memory_candidates=groups,
    )


def check(result, groups, budget):
    assert result.context.content_bytes == len((result.context.content or "").encode()) <= budget
    assert len(result.fragments) == len(result.origins)
    items = json.loads(result.context.content.splitlines()[-2])["items"] if result.context.content else []
    assert len(items) == len(result.fragments)
    for fragment, origin, item in zip(result.fragments, result.origins, items, strict=True):
        original = next(
            candidate.hits[0].text for candidate in groups if candidate.scope_id == fragment.address.memory.scope_id
        )
        assert type(fragment.start) is int and type(fragment.end) is int
        assert 0 <= fragment.start < fragment.end <= len(original)
        assert fragment.text == original[fragment.start : fragment.end] == item["content"]
        assert origin == fragment.address
        assert item["citation"]["scope_id"] == origin.memory.scope_id
        assert item["citation"]["memory_ref"] == origin.memory.artifact.model_dump(mode="json")


def test_two_separated_facts_keep_exact_intervals():
    first = "Cobalt pressure remains stable at twelve units. "
    last = "Amber temperature reaches forty units after heating."
    text = first + "The maintenance office filed another routine report. " * 60 + last
    groups = (group("one", text),)
    result = build(groups, "cobalt pressure amber temperature", 1400)
    check(result, groups, 1400)
    assert len(result.fragments) == 2
    assert any(first.strip() in fragment.text for fragment in result.fragments)
    assert any(last in fragment.text for fragment in result.fragments)


def test_identical_body_in_different_scopes_retains_both_identities():
    groups = (group("one", "A shared sensor record."), group("two", "A shared sensor record."))
    result = build(groups, "shared sensor", 2000)
    check(result, groups, 2000)
    # The ranking may choose one copy; it must never reuse another Scope's address.
    assert result.fragments
    for candidate in reversed(groups):
        isolated = build((candidate,), "shared sensor", 1000)
        check(isolated, (candidate,), 1000)
        assert isolated.fragments[0].address.memory.scope_id == candidate.scope_id


@pytest.mark.parametrize("budget", [512, 700, 1000, 1400, 1800])
def test_escaped_unicode_and_budget_probe_are_exact(budget):
    text = '海洋观测 "sensor" \\ station\n' * 4 + "The sensor detected a rapid pressure change. " * 20
    groups = (group("one", text),)
    result = build(groups, "sensor pressure change", budget)
    check(result, groups, budget)
    probe = PreparedContextBuilder().probe_budget(
        request=PrepareContextRequest(query="sensor pressure change", max_bytes=budget, evidence_selection=True),
        current_scope_id="one",
        memory_candidates=groups,
    )
    assert probe.delivered_items == len(result.fragments)
    assert probe.unused_bytes == budget - result.context.content_bytes


def test_empty_and_strict_opt_in():
    assert build((), "sensor", 512).context.status == "empty"
    for invalid in ("true", 1, 0, None):
        with pytest.raises(ValidationError):
            PrepareContextRequest.model_validate({"query": "sensor", "evidence_selection": invalid})


def test_default_and_explicit_false_prepare_remain_equal():
    candidates = group("one", "A sensor records pressure. " * 40)
    builder = PreparedContextBuilder()
    default = builder.build_scopes_result(
        request=PrepareContextRequest(query="sensor", max_bytes=1000),
        current_scope_id="one",
        memory_candidates=(candidates,),
    )
    disabled = builder.build_scopes_result(
        request=PrepareContextRequest(query="sensor", max_bytes=1000, evidence_selection=False),
        current_scope_id="one",
        memory_candidates=(candidates,),
    )
    assert default == disabled
    assert default.fragments == ()

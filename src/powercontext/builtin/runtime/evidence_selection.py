# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License").
"""Offline query-sensitive selection of literal, exactly cited Memory intervals."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import pairwise

from powercontext.artifacts import ArtifactAddress
from powercontext.builtin.runtime.models import PrepareContextRequest, PreparedContext
from powercontext.builtin.runtime.prepared_context import (
    MemoryEntryAddress,
    PreparedContextBuild,
    PreparedContextOmissions,
    PreparedEvidenceFragment,
    PreparedMemoryCandidates,
)
from powercontext.builtin.runtime.prepared_text import TRUST_POLICY

_WORDS = re.compile(r"[^\W_]+", re.UNICODE)
_BOUNDARY = re.compile(r"(?<=[.!?。！？])\s+|\n+")  # noqa: RUF001
_STOP = frozenset([
    "a",
    "an",
    "the",
    "of",
    "to",
    "in",
    "on",
    "at",
    "for",
    "from",
    "by",
    "with",
    "and",
    "or",
    "as",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "that",
    "this",
    "these",
    "those",
    "it",
    "its",
    "their",
    "there",
    "which",
    "who",
    "when",
    "where",
    "how",
    "what",
    "than",
    "then",
    "can",
    "could",
    "may",
    "might",
    "will",
    "would",
    "should",
    "do",
    "does",
    "did",
    "have",
    "has",
    "had",
    "not",
    "no",
    "both",
    "between",
    "among",
    "into",
    "through",
    "during",
    "within",
    "without",
    "also",
    "such",
    "more",
    "most",
    "less",
    "only",
])


def _stem(word: str) -> str:
    word = word.casefold()
    if len(word) > 5 and word.endswith("ies"):
        return word[:-3] + "y"
    for suffix in ("ation", "ments", "ment", "ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            root = word[: -len(suffix)]
            if suffix in {"ing", "ed"} and len(root) > 2 and root[-1] == root[-2]:
                root = root[:-1]
            return root
    return word


def _terms(text: str) -> tuple[str, ...]:
    return tuple(_stem(match[0]) for match in _WORDS.finditer(text) if match[0].casefold() not in _STOP)


@dataclass(frozen=True)
class _Document:
    address: MemoryEntryAddress
    text: str
    spans: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class _Sentence:
    document: int
    index: int
    start: int
    end: int
    score: float
    matched: frozenset[str]


@dataclass(frozen=True)
class _Span:
    document: int
    start: int
    end: int


def _documents(groups: Sequence[PreparedMemoryCandidates]) -> list[_Document]:
    documents = []
    seen: set[tuple[str, str, int, str, str]] = set()
    for group in groups:
        for hit in group.hits:
            address = MemoryEntryAddress(
                memory=ArtifactAddress(scope_id=group.scope_id, artifact=hit.memory_ref),
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            key = (
                group.scope_id,
                hit.memory_ref.artifact_id,
                hit.memory_ref.revision,
                hit.entry_id,
                hit.entry_version_id,
            )
            if key in seen or not hit.text.strip():
                continue
            seen.add(key)
            starts = [0, *(match.end() for match in _BOUNDARY.finditer(hit.text)), len(hit.text)]
            spans = tuple((start, end) for start, end in pairwise(starts) if hit.text[start:end].strip())
            documents.append(_Document(address, hit.text, spans))
    return documents


def _rank(documents: list[_Document], query: str) -> list[_Sentence]:
    query_terms = _terms(query)
    wanted = set(query_terms)
    frequency: Counter[str] = Counter()
    for document in documents:
        frequency.update(set(_terms(document.text)))
    weights = {term: 1 + math.log(1 + len(documents) / (1 + frequency[term])) for term in wanted}
    pairs = set(pairwise(query_terms))
    ranked = []
    for number, document in enumerate(documents):
        for index, (start, end) in enumerate(document.spans):
            terms = _terms(document.text[start:end])
            matched = wanted.intersection(terms)
            lexical = sum(weights[term] for term in matched)
            phrase = len(pairs.intersection(pairwise(terms)))
            # Saturating coverage rewards a sentence expressing the whole query,
            # while length normalization avoids selecting an entire long paragraph.
            score = (lexical + 0.6 * phrase) / (1 + max(0, len(terms) - 24) / 100)
            ranked.append(_Sentence(number, index, start, end, score, frozenset(matched)))
    return sorted(ranked, key=lambda sentence: (-sentence.score, sentence.document, sentence.index))


def _merge(spans: Sequence[_Span]) -> list[_Span]:
    merged: list[_Span] = []
    # Keep the selected document's rank, then its original character order.
    order = list(dict.fromkeys(span.document for span in spans))
    for number in order:
        for span in sorted((span for span in spans if span.document == number), key=lambda span: span.start):
            if merged and merged[-1].document == number and span.start <= merged[-1].end:
                merged[-1] = replace(merged[-1], end=max(merged[-1].end, span.end))
            else:
                merged.append(span)
    return merged


def _render(documents: list[_Document], spans: Sequence[_Span]) -> str:
    items = []
    for span in spans:
        document = documents[span.document]
        address = document.address
        items.append({
            "citation": {
                "scope_id": address.memory.scope_id,
                "memory_ref": address.memory.artifact.model_dump(mode="json"),
                "entry_id": address.entry_id,
                "entry_version_id": address.entry_version_id,
            },
            "content": document.text[span.start : span.end],
            "start": span.start,
            "end": span.end,
        })
    encoded = json.dumps({"trust": "untrusted_history", "items": items}, ensure_ascii=False, separators=(",", ":"))
    return f"{TRUST_POLICY}\n\nBEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n{encoded}\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1"


def _fits(documents: list[_Document], spans: Sequence[_Span], budget: int) -> bool:
    return len(_render(documents, spans).encode("utf-8")) <= budget


def _partial(
    documents: list[_Document], selected: list[_Span], candidate: _Span, budget: int, *, from_right: bool = False
) -> list[_Span]:
    """Find a fitting literal extension, counting JSON escaping and Unicode bytes."""
    low, high = 0, candidate.end - candidate.start
    best = selected
    while low < high:
        size = (low + high + 1) // 2
        span = (
            replace(candidate, start=candidate.end - size)
            if from_right
            else replace(candidate, end=candidate.start + size)
        )
        trial = _merge([*selected, span])
        if _fits(documents, trial, budget):
            low, best = size, trial
        else:
            high = size - 1
    return best


def _seeds(documents: list[_Document], ranked: list[_Sentence], budget: int) -> tuple[list[_Span], list[_Sentence]]:
    """Prioritize complete, complementary query-bearing sentences."""
    selected: list[_Span] = []
    anchors: list[_Sentence] = []
    if ranked:
        best = ranked[0]
        primary = _Span(best.document, best.start, best.end)
        if _fits(documents, [primary], budget):
            selected, anchors = [primary], [best]
        else:
            # Oversized sentences still need query-directed selection, not a
            # fixed document prefix. Start at the first matching query term.
            text = documents[best.document].text
            matches = [
                match for match in _WORDS.finditer(text, best.start, best.end) if _stem(match[0]) in best.matched
            ]
            start = max(best.start, matches[0].start() - 32) if matches else best.start
            selected = _partial(documents, [], replace(primary, start=start), budget)
            anchors = [best] if selected else []

        covered = set(best.matched)
        # Additional disjoint evidence must contribute query information. Nearby
        # sentences are cheaper as a single interval and are expanded below.
        for sentence in ranked[1:]:
            if not selected or sentence.score < best.score * 0.75 or not sentence.matched - covered:
                continue
            if any(
                anchor.document == sentence.document and abs(anchor.index - sentence.index) <= 1 for anchor in anchors
            ):
                continue
            trial = _merge([*selected, _Span(sentence.document, sentence.start, sentence.end)])
            if _fits(documents, trial, budget):
                selected, covered = trial, covered | sentence.matched
                anchors.append(sentence)
    return selected, anchors


def _expand(
    documents: list[_Document], ranked: list[_Sentence], selected: list[_Span], anchors: list[_Sentence], budget: int
) -> list[_Span]:
    """Spend remaining bytes on the most relevant adjacent original context."""
    sentence_scores = {(sentence.document, sentence.index): sentence.score for sentence in ranked}
    while selected:
        extensions = []
        for span in selected:
            document = documents[span.document]
            for index, (start, end) in enumerate(document.spans):
                if end == span.start or start == span.end:
                    distance = min(abs(index - anchor.index) for anchor in anchors if anchor.document == span.document)
                    extensions.append((
                        sentence_scores[span.document, index],
                        -distance,
                        _Span(span.document, start, end),
                        end == span.start,
                    ))
        if not extensions:
            break
        extensions.sort(key=lambda item: (-item[0], -item[1], item[2].document, -item[2].start))
        extended = False
        for _, _, extension, _ in extensions:
            trial = _merge([*selected, extension])
            if _fits(documents, trial, budget):
                selected, extended = trial, True
                break
        if not extended:
            _, _, extension, from_right = extensions[0]
            selected = _partial(documents, selected, extension, budget, from_right=from_right)
            break
    return selected


def select_evidence(request: PrepareContextRequest, groups: Sequence[PreparedMemoryCandidates]) -> PreparedContextBuild:
    """Select evidence without accessing annotations, external data, or model services."""
    documents = _documents(groups)
    ranked = _rank(documents, request.query)
    selected, anchors = _seeds(documents, ranked, request.max_bytes)
    selected = _expand(documents, ranked, selected, anchors, request.max_bytes)
    if not selected:
        return PreparedContextBuild(context=PreparedContext(status="empty", content=None, content_bytes=0), origins=())
    content = _render(documents, selected)
    fragments = tuple(
        PreparedEvidenceFragment(
            address=documents[span.document].address,
            start=span.start,
            end=span.end,
            text=documents[span.document].text[span.start : span.end],
        )
        for span in selected
    )
    truncated = sum(
        fragment.start > 0 or fragment.end < len(documents[span.document].text)
        for fragment, span in zip(fragments, selected, strict=True)
    )
    return PreparedContextBuild(
        context=PreparedContext(status="ready", content=content, content_bytes=len(content.encode("utf-8"))),
        origins=tuple(fragment.address for fragment in fragments),
        fragments=fragments,
        omissions=PreparedContextOmissions(truncated_items=truncated),
    )

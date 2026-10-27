"""Deterministic Memory candidate pipeline for research notes.

No LLM is needed. Each Source is converted into a Memory entry of kind
``research-note``. When multiple source revisions exist for the same logical
note_id (e.g. after a status update), the pipeline groups them and produces
one Memory entry per logical note using the latest revision's text, while
citing every source revision so that old citations remain valid.
"""

from __future__ import annotations

import json
import re

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.artifacts.memory.protocols import MemoryCandidateRequest
from powercontext.builtin.sources.content import ContentSource
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY
from powercontext.sources.models import Source
from powercontext.sources.observations import SourceObservation

_SOURCE_ID_RE = re.compile(r"^note:(?P<project>[^:]+):(?P<note>[^:]+?)(?::r(?P<rev>\d+))?$")


def _extract_text(source: Source) -> tuple[str, str, int]:
    """Return (text, note_id, revision) for a source."""
    if isinstance(source, ContentSource):
        content = source.content
        note_id = ""
        revision = 1
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                metadata = parsed.get("metadata", {})
                if isinstance(metadata, dict):
                    note_id = str(metadata.get("note_id", ""))
                    revision = int(metadata.get("revision", 1))
                text = str(parsed.get("text", content))
            else:
                text = content
        except (json.JSONDecodeError, TypeError, ValueError):
            text = content
        if not note_id:
            m = _SOURCE_ID_RE.match(source.name)
            if m:
                note_id = m.group("note")
                revision = int(m.group("rev") or 1)
        return text, note_id, revision

    if isinstance(source, SourceObservation):
        projection = source.projection(TEXT_EVIDENCE_PROJECTION_KEY)
        if isinstance(projection, dict):
            text = str(projection["content"])
        else:
            text = str(projection)
        return text, "", 1

    if hasattr(source, "projection"):
        try:
            projection = source.projection(TEXT_EVIDENCE_PROJECTION_KEY)
            if isinstance(projection, dict):
                return str(projection["content"]), "", 1
            return str(projection), "", 1
        except Exception:
            pass
    return str(source), "", 1


class NoteCandidatePipeline:
    """Deterministic pipeline: one Memory entry per logical note.

    Groups sources by logical note_id, uses the latest revision's text,
    and cites all source revisions for that note.
    """

    async def extract(
        self, request: MemoryCandidateRequest, /
    ) -> tuple[MemoryEntryInput, ...]:
        groups: dict[str, list[tuple[str, Source, int]]] = {}
        ungrouped: list[tuple[str, Source]] = []

        for source in request.sources:
            text, note_id, revision = _extract_text(source)
            if note_id:
                groups.setdefault(note_id, []).append((text, source, revision))
            else:
                ungrouped.append((text, source))

        entries: list[MemoryEntryInput] = []

        for note_id, revisions in groups.items():
            revisions.sort(key=lambda r: r[2])
            latest_text = revisions[-1][0]
            all_sources = tuple(r[1] for r in revisions)
            entries.append(
                MemoryEntryInput(
                    kind="research-note",
                    text=latest_text,
                    sources=all_sources,
                )
            )

        for text, source in ungrouped:
            entries.append(
                MemoryEntryInput(
                    kind="research-note",
                    text=text,
                    sources=(source,),
                )
            )

        return tuple(entries)


__all__ = ["NoteCandidatePipeline"]

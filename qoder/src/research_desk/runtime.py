"""High-level runtime wrapper for Research Desk.

Manages one shared PowerContext BuiltinRuntime with per-project scopes,
deterministic note ingestion, scoped Memory search, and citation retrieval.

Status updates use versioned source identities: each revision of a logical
note gets a new source_id (note:{project}:{note}:r{N}), preserving the
immutability of PowerContext sources. The current view resolves the latest
revision per logical note_id; old MemoryCitation objects continue to return
the content and category from their specific revision.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from powercontext.artifacts import MemoryCitation
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import BaseValueNotFoundError
from powercontext.builtin.runtime import (
    BuiltinConfig,
    BuiltinRuntime,
    GetMemoryEntryRequest,
    MemoryEntryInput,
    SearchMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.models import MemoryFlushResult
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.scope.models import ScopeExternalReference

from .pipeline import NoteCandidatePipeline

logger = logging.getLogger(__name__)

_SOURCE_ID_RE = re.compile(r"^note:(?P<project>[^:]+):(?P<note>[^:]+?)(?::r(?P<rev>\d+))?$")


def _parse_source_id(source_id: str) -> tuple[str, str, int] | None:
    m = _SOURCE_ID_RE.match(source_id)
    if m is None:
        return None
    return m.group("project"), m.group("note"), int(m.group("rev") or 1)


def _make_source_id(project_id: str, note_id: str, revision: int) -> str:
    if revision <= 1:
        return f"note:{project_id}:{note_id}"
    return f"note:{project_id}:{note_id}:r{revision}"


@dataclass
class ProjectInfo:
    scope_id: str
    project_id: str
    title: str
    summary: str
    note_count: int = 0


@dataclass
class SearchResult:
    citation: Any
    text: str
    score: float
    matched_by: list[str]
    source_id: str
    note_id: str
    project_id: str
    title: str
    body: str
    category: str
    url: str | None
    created_at: str | None
    metadata: dict[str, Any]


@dataclass
class ContinueWorkItem:
    section: str
    title: str
    body: str
    category: str
    source_id: str
    citation: Any | None = None


class ResearchDeskRuntime:
    """Owns one PowerContext BuiltinRuntime and exposes Research Desk operations."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}"),
        )
        self._runtime: BuiltinRuntime | None = None
        self._pipeline = NoteCandidatePipeline()
        self._projects: dict[str, ProjectInfo] = {}
        self._initialized = False
        self._exit_stack: AsyncExitStack | None = None
        self._note_revisions: dict[str, dict[str, int]] = {}

    async def start(self) -> None:
        if self._runtime is not None:
            return
        self._exit_stack = AsyncExitStack()
        self._runtime = await self._exit_stack.enter_async_context(
            open_builtin_runtime(
                self._config,
                candidate_pipeline=self._pipeline,
            )
        )
        self._initialized = True
        logger.info("Research Desk runtime started")

    async def stop(self) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
            self._runtime = None
            self._exit_stack = None

    @property
    def runtime(self) -> BuiltinRuntime:
        assert self._runtime is not None, "Runtime not started"
        return self._runtime

    async def ensure_project(self, project_id: str, title: str, summary: str = "") -> ProjectInfo:
        if project_id in self._projects:
            return self._projects[project_id]
        assert self.runtime.scopes is not None

        scope = await self.runtime.scopes.create(
            ScopeDraft(
                title=title,
                summary=summary or title,
                idempotency_key=f"project:{project_id}",
                external_references=(
                    ScopeExternalReference(kind="project_id", value=project_id),
                ),
            )
        )
        info = ProjectInfo(
            scope_id=scope.scope_id,
            project_id=project_id,
            title=title,
            summary=summary,
        )
        self._projects[project_id] = info
        return info

    async def _next_revision(self, project_id: str, note_id: str) -> int:
        proj_revs = self._note_revisions.setdefault(project_id, {})
        current = proj_revs.get(note_id, 0)
        new_rev = current + 1
        proj_revs[note_id] = new_rev
        return new_rev

    async def _current_revision(self, project_id: str, note_id: str) -> int:
        return self._note_revisions.get(project_id, {}).get(note_id, 0)

    async def _source_exists(self, scope_id: str, source_id: str) -> bool:
        try:
            await self.runtime.records.for_scope(scope_id).get_source("content", source_id)
            return True
        except BaseValueNotFoundError:
            return False

    async def add_note(
        self,
        project_id: str,
        note_id: str,
        title: str,
        body: str,
        category: str = "note",
        url: str | None = None,
        created_at: str | None = None,
        *,
        increment_count: bool = True,
    ) -> dict[str, Any]:
        project = self._projects.get(project_id)
        if project is None:
            raise ValueError(f"Unknown project: {project_id}")

        revision = await self._next_revision(project_id, note_id)
        source_id = _make_source_id(project_id, note_id, revision)

        metadata: dict[str, Any] = {
            "note_id": note_id,
            "project_id": project_id,
            "category": category,
            "title": title,
            "body": body,
            "revision": revision,
        }
        if url:
            metadata["url"] = url
        if created_at:
            metadata["created_at"] = created_at

        content_payload = {
            "text": f"{title}\n\n{body}" if body else title,
            "metadata": metadata,
        }
        content_json = json.dumps(content_payload, ensure_ascii=False)

        from powercontext.builtin.runtime.models import CaptureSource
        capture = CaptureSource(
            source_id=source_id,
            content=content_json,
            metadata=metadata,
        )

        receipt = await self.runtime.sources.for_scope(project.scope_id).capture(capture)

        memory = self.runtime.memory.for_scope(project.scope_id)
        result = await memory.flush()
        if increment_count:
            project.note_count += 1

        return {
            "source_id": receipt.source_ref.source_id,
            "source_type": receipt.source_ref.source_type,
            "sequence": receipt.sequence,
            "revision": revision,
            "processed": result.processed,
        }

    async def _resolve_latest_source_data(
        self, source_refs: tuple[Any, ...], scope_id: str
    ) -> dict[str, Any]:
        """Read source data from the latest revision among multiple SourceRefs.

        A Memory entry may cite several source revisions for the same logical
        note. We pick the one with the highest revision number to show current
        state, while older citations still resolve to their own revision.
        """
        if not source_refs:
            return {}

        best_data: dict[str, Any] = {}
        best_rev = -1
        for ref in source_refs:
            parsed = _parse_source_id(ref.source_id)
            rev = parsed[2] if parsed else 0
            if rev <= best_rev:
                continue
            try:
                data = await self._read_source_data(ref, scope_id)
            except Exception:
                continue
            if data:
                best_data = data
                best_rev = rev
        return best_data

    async def search(self, project_id: str, query: str, limit: int = 10) -> list[SearchResult]:
        project = self._projects.get(project_id)
        if project is None:
            raise ValueError(f"Unknown project: {project_id}")
        if not query or not query.strip():
            return []

        memory = self.runtime.memory.for_scope(project.scope_id)
        query = query.strip()

        # Fetch all matching hits, then deduplicate by logical note_id
        # (keep highest revision). This avoids truncating by limit before
        # filtering out old revisions, which would let stale entries crowd
        # out newer or distinct notes.
        page = await memory.search(SearchMemoryRequest(query=query, limit=200, mode="fts"))
        seen: dict[str, tuple[int, SearchResult]] = {}

        for hit in page.hits:
            citation = MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            entry_record = await memory.get(GetMemoryEntryRequest(citation=citation))
            entry = entry_record.entry

            source_data = await self._resolve_latest_source_data(entry.sources, project.scope_id)
            note_id = source_data.get("note_id", "")
            revision = source_data.get("revision", 1)

            latest_ref = entry.sources[0] if entry.sources else None
            for ref in entry.sources:
                parsed = _parse_source_id(ref.source_id)
                if parsed:
                    cur_parsed = _parse_source_id(latest_ref.source_id) if latest_ref else None
                    if cur_parsed is None or parsed[2] > cur_parsed[2]:
                        latest_ref = ref

            existing = seen.get(note_id)
            if existing is not None and existing[0] >= revision:
                continue

            result = SearchResult(
                citation=citation,
                text=hit.text,
                score=hit.score,
                matched_by=[str(m) for m in hit.matched_by],
                source_id=latest_ref.source_id if latest_ref else "",
                note_id=note_id,
                project_id=source_data.get("project_id", project_id),
                title=source_data.get("title", ""),
                body=source_data.get("body", ""),
                category=source_data.get("category", "note"),
                url=source_data.get("url"),
                created_at=source_data.get("created_at"),
                metadata=dict(entry.metadata) if hasattr(entry, "metadata") else {},
            )
            seen[note_id] = (revision, result)

        ranked = sorted(seen.values(), key=lambda x: x[1].score, reverse=True)
        return [r for _, r in ranked[:limit]]

    async def get_entry(self, project_id: str, citation: Any) -> dict[str, Any]:
        project = self._projects.get(project_id)
        if project is None:
            raise ValueError(f"Unknown project: {project_id}")
        memory = self.runtime.memory.for_scope(project.scope_id)
        entry_record = await memory.get(GetMemoryEntryRequest(citation=citation))
        entry = entry_record.entry
        source_data = await self._resolve_latest_source_data(entry.sources, project.scope_id)
        primary_ref = entry.sources[0] if entry.sources else None
        return {
            "text": entry.text,
            "kind": entry.kind,
            "source_id": primary_ref.source_id if primary_ref else "",
            **source_data,
        }

    async def _read_source_data(self, source_ref: Any, scope_id: str) -> dict[str, Any]:
        if source_ref is None:
            return {}

        source_type = getattr(source_ref, "source_type", "content")
        source_id = getattr(source_ref, "source_id", "")
        if not source_id:
            return {}

        record = await self.runtime.records.for_scope(scope_id).get_source(source_type, source_id)
        content_value = record.content
        if isinstance(content_value, str):
            try:
                parsed = json.loads(content_value)
                if isinstance(parsed, dict) and "metadata" in parsed:
                    metadata = parsed["metadata"]
                    return {
                        "note_id": metadata.get("note_id", ""),
                        "project_id": metadata.get("project_id", ""),
                        "title": metadata.get("title", ""),
                        "body": metadata.get("body", ""),
                        "category": metadata.get("category", "note"),
                        "url": metadata.get("url"),
                        "created_at": metadata.get("created_at"),
                        "revision": metadata.get("revision", 1),
                    }
            except (json.JSONDecodeError, TypeError):
                pass
            return {}
        elif isinstance(content_value, dict):
            return {
                "note_id": content_value.get("note_id", ""),
                "project_id": content_value.get("project_id", ""),
                "title": content_value.get("title", ""),
                "body": content_value.get("body", ""),
                "category": content_value.get("category", "note"),
                "url": content_value.get("url"),
                "created_at": content_value.get("created_at"),
                "revision": content_value.get("revision", 1),
            }
        else:
            raise TypeError(
                f"Source {source_type}:{source_id} content has unexpected type: {type(content_value)}"
            )

    async def continue_work(self, project_id: str) -> dict[str, Any]:
        project = self._projects.get(project_id)
        if project is None:
            raise ValueError(f"Unknown project: {project_id}")
        memory = self.runtime.memory.for_scope(project.scope_id)
        all_entries = await memory.list()

        seen_notes: dict[str, dict[str, Any]] = {}
        for entry_record in all_entries.entries:
            entry = entry_record.entry
            source_data = await self._resolve_latest_source_data(entry.sources, project.scope_id)
            note_id = source_data.get("note_id", "")
            rev = source_data.get("revision", 1)
            existing = seen_notes.get(note_id)
            if existing is not None and existing.get("_rev", 0) >= rev:
                continue
            category = source_data.get("category", "note")
            citation = MemoryCitation(
                memory_ref=entry_record.memory_ref,
                entry_id=entry.entry_id,
                entry_version_id=entry.entry_version_id,
            )
            latest_ref = entry.sources[0] if entry.sources else None
            for ref in entry.sources:
                parsed = _parse_source_id(ref.source_id)
                if parsed:
                    cur_parsed = _parse_source_id(latest_ref.source_id) if latest_ref else None
                    if cur_parsed is None or parsed[2] > cur_parsed[2]:
                        latest_ref = ref
            item = {
                "title": source_data.get("title", entry.text[:60]),
                "body": source_data.get("body", entry.text),
                "category": category,
                "source_id": latest_ref.source_id if latest_ref else "",
                "note_id": note_id,
                "url": source_data.get("url"),
                "created_at": source_data.get("created_at"),
                "citation": {
                    "memory_ref": citation.memory_ref.model_dump(mode="json"),
                    "entry_id": citation.entry_id,
                    "entry_version_id": citation.entry_version_id,
                },
                "_rev": rev,
            }
            seen_notes[note_id] = item

        sections: dict[str, list[dict[str, Any]]] = {
            "completed": [],
            "blocked": [],
            "decisions": [],
            "next_steps": [],
            "references": [],
        }
        for item in seen_notes.values():
            item.pop("_rev", None)
            category = item["category"]
            if category in ("progress", "done"):
                sections["completed"].append(item)
            elif category in ("blocker", "blocked"):
                sections["blocked"].append(item)
            elif category == "decision":
                sections["decisions"].append(item)
            elif category in ("todo", "next"):
                sections["next_steps"].append(item)
            else:
                sections["references"].append(item)
        return {
            "project_id": project_id,
            "project_title": project.title,
            "sections": sections,
            "total_entries": len(seen_notes),
        }

    async def update_task_status(
        self, project_id: str, note_id: str, new_category: str
    ) -> dict[str, Any]:
        """Update a task's status by creating a new source revision.

        Sources are immutable, so each status change creates a new source
        identity (note:{project}:{note}:r{N}) with the updated category.
        Setting the same category that already exists is idempotent.
        """
        project = self._projects.get(project_id)
        if project is None:
            raise ValueError(f"Unknown project: {project_id}")

        current_rev = await self._current_revision(project_id, note_id)
        if current_rev == 0:
            first_source_id = _make_source_id(project_id, note_id, 1)
            if not await self._source_exists(project.scope_id, first_source_id):
                raise ValueError(f"Note not found: {note_id}")
            await self._rebuild_revision_cache(project_id, project.scope_id)
            current_rev = await self._current_revision(project_id, note_id)
            if current_rev == 0:
                raise ValueError(f"Note not found: {note_id}")

        latest_source_id = _make_source_id(project_id, note_id, current_rev)
        try:
            old_data = await self._read_source_data(
                type("_Ref", (), {"source_type": "content", "source_id": latest_source_id})(),
                project.scope_id,
            )
        except Exception as e:
            raise ValueError(f"Failed to read current note state: {e}") from e

        if not old_data:
            raise ValueError(f"Note not found: {note_id}")

        if old_data.get("category") == new_category:
            return {
                "source_id": latest_source_id,
                "revision": current_rev,
                "category": new_category,
                "idempotent": True,
                "processed": False,
            }

        result = await self.add_note(
            project_id=project_id,
            note_id=note_id,
            title=old_data.get("title", ""),
            body=old_data.get("body", ""),
            category=new_category,
            url=old_data.get("url"),
            created_at=old_data.get("created_at"),
            increment_count=False,
        )
        result["idempotent"] = False
        return result

    async def _rebuild_revision_cache(self, project_id: str, scope_id: str) -> None:
        """Scan existing sources to rebuild the note revision counter."""
        page_cursor: str | None = None
        proj_revs = self._note_revisions.setdefault(project_id, {})
        while True:
            page = await self.runtime.records.for_scope(scope_id).list_sources(
                limit=100, cursor=page_cursor
            )
            for record in page.items:
                parsed = _parse_source_id(record.source_id)
                if parsed is None:
                    continue
                src_project, src_note, rev = parsed
                if src_project != project_id:
                    continue
                proj_revs[src_note] = max(proj_revs.get(src_note, 0), rev)
            page_cursor = page.next_cursor
            if page_cursor is None:
                break

    async def list_projects(self) -> list[ProjectInfo]:
        return list(self._projects.values())

    async def get_project(self, project_id: str) -> ProjectInfo | None:
        return self._projects.get(project_id)

    async def reload_projects(self) -> None:
        """Re-discover existing scopes from the database after a restart."""
        if not self._initialized:
            return
        assert self.runtime.scopes is not None
        descriptors = await self.runtime.scopes.list()
        for desc in descriptors:
            project_id = None
            title = desc.title
            summary = desc.summary
            if desc.external_references:
                for ref in desc.external_references:
                    if ref.kind == "project_id":
                        project_id = ref.value
                        break
            if project_id is None:
                continue
            if project_id in self._projects:
                continue
            info = ProjectInfo(
                scope_id=desc.scope_id,
                project_id=project_id,
                title=title,
                summary=summary,
            )
            self._projects[project_id] = info

        for info in self._projects.values():
            await self._rebuild_revision_cache(info.project_id, info.scope_id)
            await self._rebuild_note_count(info)

    async def _rebuild_note_count(self, info: ProjectInfo) -> None:
        """Rebuild accurate note_count from the source journal."""
        seen_notes: set[str] = set()
        page_cursor: str | None = None
        while True:
            page = await self.runtime.records.for_scope(info.scope_id).list_sources(
                limit=100, cursor=page_cursor
            )
            for record in page.items:
                parsed = _parse_source_id(record.source_id)
                if parsed is None:
                    continue
                src_project, src_note, _rev = parsed
                if src_project != info.project_id:
                    continue
                seen_notes.add(src_note)
            page_cursor = page.next_cursor
            if page_cursor is None:
                break
        info.note_count = len(seen_notes)

    async def import_demo_data(self, projects: list[dict[str, Any]]) -> dict[str, int]:
        imported_projects = 0
        imported_notes = 0
        skipped_notes = 0
        for proj in projects:
            project_id = proj["project_id"]
            existing = await self.get_project(project_id)
            if existing is None:
                await self.ensure_project(
                    project_id,
                    proj["title"],
                    proj.get("summary", ""),
                )
                imported_projects += 1
            project = self._projects.get(project_id)
            if project is None:
                continue
            for note in proj.get("notes", []):
                note_id = note["note_id"]
                source_id = _make_source_id(project_id, note_id, 1)
                if await self._source_exists(project.scope_id, source_id):
                    skipped_notes += 1
                    continue

                await self.add_note(
                    project_id=project_id,
                    note_id=note_id,
                    title=note["title"],
                    body=note["body"],
                    category=note.get("category", "note"),
                    url=note.get("url"),
                    created_at=note.get("created_at"),
                )
                imported_notes += 1
        return {
            "projects": imported_projects,
            "notes": imported_notes,
            "skipped": skipped_notes,
        }


__all__ = [
    "ContinueWorkItem",
    "ProjectInfo",
    "ResearchDeskRuntime",
    "SearchResult",
]

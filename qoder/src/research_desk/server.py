"""FastAPI backend for Research Desk.

Provides REST endpoints for project management, note ingestion,
scoped Memory search, citation retrieval, and continue-work generation.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .demo_data import DEMO_PROJECTS
from .runtime import ResearchDeskRuntime

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "research_desk.db"

VALID_CATEGORIES = {"note", "reference", "progress", "done", "blocker", "blocked", "decision", "todo", "next"}

runtime: ResearchDeskRuntime | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global runtime
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    runtime = ResearchDeskRuntime(DB_PATH)
    await runtime.start()
    await runtime.reload_projects()
    logger.info("Research Desk runtime started, database: %s", DB_PATH)
    yield
    if runtime is not None:
        await runtime.stop()
        logger.info("Research Desk runtime stopped")


app = FastAPI(
    title="研迹 / Research Desk",
    description="本地优先的研究进度记忆台",
    version="0.1.0",
    lifespan=lifespan,
)


class CreateProjectRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    summary: str = Field(default="", max_length=1024)


class AddNoteRequest(BaseModel):
    project_id: str = Field(min_length=1)
    note_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=512)
    body: str = Field(default="", max_length=10000)
    category: str = Field(default="note")
    url: str | None = None
    created_at: str | None = None


class SearchRequest(BaseModel):
    project_id: str = Field(min_length=1)
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=10, ge=1, le=50)


class CitationRequest(BaseModel):
    project_id: str
    memory_ref: dict[str, Any]
    entry_id: str
    entry_version_id: str


class UpdateTaskStatusRequest(BaseModel):
    project_id: str
    note_id: str
    new_category: str


def _get_runtime() -> ResearchDeskRuntime:
    if runtime is None:
        raise HTTPException(status_code=503, detail="Runtime not initialized")
    return runtime


def _validate_url(url: str | None) -> str | None:
    if url is None:
        return None
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")
    return url


@app.get("/")
async def index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"message": "Research Desk API is running"}


@app.get("/api/health")
async def health():
    rt = _get_runtime()
    return {"status": "ok", "projects": len(rt._projects)}


@app.post("/api/projects")
async def create_project(req: CreateProjectRequest):
    rt = _get_runtime()
    if not req.project_id.strip():
        raise HTTPException(status_code=400, detail="project_id must not be blank")
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="title must not be blank")
    try:
        info = await rt.ensure_project(req.project_id, req.title, req.summary)
        return {
            "project_id": info.project_id,
            "scope_id": info.scope_id,
            "title": info.title,
            "summary": info.summary,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/projects")
async def list_projects():
    rt = _get_runtime()
    projects = await rt.list_projects()
    return [
        {
            "project_id": p.project_id,
            "scope_id": p.scope_id,
            "title": p.title,
            "summary": p.summary,
            "note_count": p.note_count,
        }
        for p in projects
    ]


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str):
    rt = _get_runtime()
    info = await rt.get_project(project_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return {
        "project_id": info.project_id,
        "scope_id": info.scope_id,
        "title": info.title,
        "summary": info.summary,
        "note_count": info.note_count,
    }


@app.post("/api/notes")
async def add_note(req: AddNoteRequest):
    rt = _get_runtime()
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="title must not be blank")
    if req.category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Invalid category: {req.category}. Must be one of: {', '.join(sorted(VALID_CATEGORIES))}")
    url = _validate_url(req.url)
    try:
        result = await rt.add_note(
            project_id=req.project_id,
            note_id=req.note_id,
            title=req.title,
            body=req.body,
            category=req.category,
            url=url,
            created_at=req.created_at,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Failed to add note")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/search")
async def search(req: SearchRequest):
    rt = _get_runtime()
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query must not be blank")
    try:
        results = await rt.search(req.project_id, req.query, req.limit)
        return [
            {
                "text": r.text,
                "score": r.score,
                "matched_by": r.matched_by,
                "source_id": r.source_id,
                "note_id": r.note_id,
                "project_id": r.project_id,
                "title": r.title,
                "body": r.body,
                "category": r.category,
                "url": r.url,
                "created_at": r.created_at,
                "citation": {
                    "memory_ref": r.citation.memory_ref.model_dump(mode="json"),
                    "entry_id": r.citation.entry_id,
                    "entry_version_id": r.citation.entry_version_id,
                },
            }
            for r in results
        ]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Search failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/citation/get")
async def get_citation(req: CitationRequest):
    """Retrieve full entry and source data for a citation. Enforces scope boundary."""
    rt = _get_runtime()
    from powercontext.artifacts import MemoryCitation, ArtifactRef
    try:
        memory_ref = ArtifactRef(**req.memory_ref)
        citation = MemoryCitation(
            memory_ref=memory_ref,
            entry_id=req.entry_id,
            entry_version_id=req.entry_version_id,
        )
        entry = await rt.get_entry(req.project_id, citation)
        return entry
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Citation retrieval failed")
        raise HTTPException(status_code=404, detail=f"Citation not found: {e}")


@app.post("/api/tasks/update-status")
async def update_task_status(req: UpdateTaskStatusRequest):
    """Update a task's status by re-capturing with new category."""
    rt = _get_runtime()
    if req.new_category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Invalid category: {req.new_category}")
    try:
        result = await rt.update_task_status(req.project_id, req.note_id, req.new_category)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Task status update failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/continue-work/{project_id}")
async def continue_work(project_id: str):
    rt = _get_runtime()
    try:
        result = await rt.continue_work(project_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Continue-work failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/demo/import")
async def import_demo_data():
    rt = _get_runtime()
    try:
        counts = await rt.import_demo_data(DEMO_PROJECTS)
        return {
            "message": "Demo data imported",
            "projects_created": counts["projects"],
            "notes_imported": counts["notes"],
            "notes_skipped": counts["skipped"],
        }
    except Exception as e:
        logger.exception("Demo import failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/demo/data")
async def get_demo_data():
    return DEMO_PROJECTS


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def main():
    import uvicorn
    uvicorn.run(
        "research_desk.server:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()

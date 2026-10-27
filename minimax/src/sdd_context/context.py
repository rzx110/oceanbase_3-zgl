"""Build auditable developer context from queried materials and durable task state."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re

from .state import TaskState
from .storage import SeekDB


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


async def build_context(app_root: Path, project_root: Path, state_dir: Path, task: dict, query: str | None = None) -> dict:
    with SeekDB(app_root, state_dir) as db:
        rows = db.select_context(task["project_id"], query if query is not None else task["query"])
        trace = db.trace
    sources = []
    root = project_root.resolve()
    for row in rows:
        path = (root / row["path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"source does not exist: {row['path']}")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError(f"source changed; re-import project first: {row['path']}")
        content = row["content"]
        if content != raw.decode("utf-8"):
            raise ValueError("stored content does not match source bytes")
        item = {"path": row["path"], "kind": row["kind"], "sha256": row["sha256"],
                "line_start": 1, "line_end": len(content.splitlines()),
                "relevance": row["relevance"], "content": content}
        if row["kind"] in {"code", "test"}:
            item["symbols"] = [{"name": node.name, "line": node.lineno}
                               for node in ast.walk(ast.parse(content))
                               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        if row["kind"] == "spec":
            item["spec_ids"] = list(dict.fromkeys(re.findall(r"REQ-[A-Z]+-\d+", content)))
        sources.append(item)
    async with TaskState(state_dir / "powercontext.db", task["project_id"], task["task_id"]) as state:
        previous = await state.load()
    changed = []
    verified = {}
    if previous:
        verified = previous["state"].get("verified_source_hashes", {})
        current = {item["path"]: item["sha256"] for item in sources}
        if verified:
            changed = sorted(path for path in set(verified) | set(current) if verified.get(path) != current.get(path))
        for path, digest in previous["state"].get("produced_code", {}).items():
            artifact = (root / path).resolve()
            if (not artifact.is_relative_to(root) or not artifact.is_file()
                    or hashlib.sha256(artifact.read_bytes()).hexdigest() != digest):
                changed.append(path)
        changed = sorted(set(changed))
    next_action = previous["state"].get("next_action") if previous else "Implement the requested change and run feature/regression tests."
    if changed:
        next_action = "Revalidate the changed specifications/code/tests before reusing earlier verification: " + ", ".join(changed)
    return {"schema": "sdd-context/v1", "task_id": task["task_id"], "project_id": task["project_id"],
            "task": task["description"], "query": query if query is not None else task["query"],
            "sources": sources, "previous_state": previous,
            "next_action": next_action, "changed_since_verification": changed,
            "verification_current": bool(previous and verified and not changed and previous["state"].get("status") == "verified"),
            "database": {"backend": "seekdb", "queries": trace}}


def validate_sample_context(context: dict, task: dict) -> None:
    found = {item["path"] for item in context["sources"]}
    missing = set(task["expected_sources"]) - found
    if missing:
        raise ValueError(f"missing required context sources: {sorted(missing)}")
    ids = {key for item in context["sources"] for key in item.get("spec_ids", [])}
    if not set(task["expected_spec_ids"]) <= ids:
        raise ValueError("missing applicable specification IDs")

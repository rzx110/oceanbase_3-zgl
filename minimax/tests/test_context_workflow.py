"""Integration checks against real seekdb and real PowerContext; no fake backends.

Run scripts/create_seekdb.py first. Test data uses isolated random project IDs.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import pytest

from sdd_context.context import build_context, load_json, validate_sample_context
from sdd_context.state import TaskState
from sdd_context.storage import SeekDB

APP = Path(__file__).resolve().parents[1]


@pytest.fixture
def sample(tmp_path):
    root = tmp_path / "workspace"
    shutil.copytree(APP / "demo", root / "demo", ignore=shutil.ignore_patterns("__pycache__"))
    manifest = load_json(APP / "project.json")
    manifest["project_id"] = "sdd-integration-" + uuid.uuid4().hex
    (root / "project.json").write_text(json.dumps(manifest), encoding="utf-8")
    task = load_json(root / "demo/task.json")
    task["project_id"] = manifest["project_id"]
    (root / "demo/task.json").write_text(json.dumps(task), encoding="utf-8")
    state_dir = tmp_path / "state"
    yield root, manifest, task, state_dir
    # Only delete rows belonging to this fixture's exact generated project ID.
    with SeekDB(APP, state_dir) as db:
        db.execute("DELETE FROM sdd_materials WHERE project_id=?", [manifest["project_id"]])


def test_real_query_context_and_isolation(sample):
    root, manifest, task, state_dir = sample
    with SeekDB(APP, state_dir) as db:
        imported = db.import_project(root, manifest)
        assert imported["imported"] == 7
        assert "seekdb" in db.trace[0]["rows"][0]["version"].lower()
        assert db.select_context("unrelated-project-" + uuid.uuid4().hex, "task status") == []
    context = asyncio.run(build_context(APP, root, state_dir, task))
    validate_sample_context(context, task)
    assert context["previous_state"] is None
    assert context["verification_current"] is False
    assert any("SELECT path" in item["sql"] and item["rows"] for item in context["database"]["queries"])
    for source in context["sources"]:
        raw = (root / source["path"]).read_bytes()
        assert source["sha256"] == hashlib.sha256(raw).hexdigest()
        assert source["content"] == raw.decode("utf-8")
        assert source["line_end"] == len(source["content"].splitlines())
    assert asyncio.run(build_context(APP, root, state_dir, task, "zxqvnomatchtoken"))["sources"] == []


def test_state_survives_process_restart_and_is_used(sample):
    root, manifest, task, state_dir = sample
    with SeekDB(APP, state_dir) as db:
        db.import_project(root, manifest)
    payload = {"status": "baseline-tested", "completed": ["imported specifications", "ran before tests"],
               "todo": ["MiniMax code modification", "after tests"],
               "validation": {"feature_exit_code": 1, "regression_exit_code": 0},
               "next_action": "Invoke MiniMax using the retrieved specifications; keep existing regression tests."}

    async def write():
        async with TaskState(state_dir / "powercontext.db", task["project_id"], task["task_id"]) as store:
            assert await store.load() is None
            first = await store.save(payload)
            duplicate = await store.save(payload)
            assert first["saved_sequence"] == duplicate["saved_sequence"]
            assert first["saved_source_id"] == duplicate["saved_source_id"]
            return first

    saved = asyncio.run(write())
    output = state_dir / "resumed-context.json"
    command = [sys.executable, "-m", "sdd_context", "--root", str(root), "--state-dir", str(state_dir),
               "--output", str(output), "resume"]
    result = subprocess.run(command, cwd=APP, capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert result.returncode == 0, result.stderr
    resumed = load_json(output)
    assert resumed["previous_state"]["state"] == payload
    assert resumed["previous_state"]["source_id"] == saved["saved_source_id"]
    assert resumed["next_action"] == payload["next_action"]
    assert resumed["verification_current"] is False


def test_specification_refresh_rejects_stale_content_and_validation(sample):
    root, manifest, task, state_dir = sample
    with SeekDB(APP, state_dir) as db:
        db.import_project(root, manifest)
    first = asyncio.run(build_context(APP, root, state_dir, task))
    payload = {"status": "baseline-tested", "completed": ["checked baseline sources"], "todo": ["implement"],
               "next_action": "Implement original status filtering.",
               "verified_source_hashes": {x["path"]: x["sha256"] for x in first["sources"]}}

    async def save():
        async with TaskState(state_dir / "powercontext.db", task["project_id"], task["task_id"]) as store:
            await store.save(payload)

    asyncio.run(save())
    path = root / "demo/specs/task_status.md"
    updated = path.read_text(encoding="utf-8").replace(
        "`todo`, `doing`, or\n`done`", "`todo`, `doing`,\n`done`, or `blocked`")
    updated += "\n## REQ-STATUS-03: new specification revision\nThe new blocked status must be accepted in the next implementation.\n"
    path.write_text(updated, encoding="utf-8", newline="\n")
    with pytest.raises(ValueError, match="re-import"):
        asyncio.run(build_context(APP, root, state_dir, task))
    with SeekDB(APP, state_dir) as db:
        db.import_project(root, manifest)
    refreshed = asyncio.run(build_context(APP, root, state_dir, task))
    source = next(x for x in refreshed["sources"] if x["path"] == "demo/specs/task_status.md")
    assert source["content"] == updated
    assert "REQ-STATUS-03" in source["spec_ids"]
    assert refreshed["changed_since_verification"] == ["demo/specs/task_status.md"]
    assert refreshed["next_action"].startswith("Revalidate")
    assert refreshed["verification_current"] is False


def test_source_removal_is_reflected_after_import(sample):
    root, manifest, task, state_dir = sample
    with SeekDB(APP, state_dir) as db:
        db.import_project(root, manifest)
    # This is a disposable copy created by the fixture, never a product file.
    (root / "demo/tests/test_regression.py").unlink()
    with SeekDB(APP, state_dir) as db:
        db.import_project(root, manifest)
    context = asyncio.run(build_context(APP, root, state_dir, task))
    assert not any(x["path"] == "demo/tests/test_regression.py" for x in context["sources"])
    with pytest.raises(ValueError, match="missing required context"):
        validate_sample_context(context, task)

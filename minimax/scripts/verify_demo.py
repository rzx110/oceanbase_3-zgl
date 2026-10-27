"""Reproduce the submitted example from empty task state without regenerating code."""
import argparse
import asyncio
from datetime import datetime, timezone
import difflib
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from sdd_context.context import build_context, load_json, validate_sample_context
from sdd_context.state import TaskState
from sdd_context.storage import SeekDB
from run_code_tests import run_suite

ROOT = Path(__file__).resolve().parents[1]


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


async def verify(output):
    if output.exists():
        raise ValueError("Choose a new output directory; existing validation evidence is preserved.")
    if not output.resolve().is_relative_to(ROOT):
        raise ValueError("Output directory must be inside minimax/.")
    if not (ROOT / "demo/after/taskboard/service.py").is_file():
        raise ValueError("MiniMax after snapshot is missing. Complete the actual model development step first.")
    output.mkdir(parents=True)
    task = load_json(ROOT / "demo/task.json")
    manifest = load_json(ROOT / "project.json")
    task["project_id"] = manifest["project_id"] = "sdd-review-" + uuid.uuid4().hex
    dump(output / "task.json", task)
    state_dir = output / "state"
    with SeekDB(ROOT, state_dir) as db:
        result = db.import_project(ROOT, manifest)
        dump(output / "database-import.json", {"result": result, "queries": db.trace})
    context = await build_context(ROOT, ROOT, state_dir, task)
    validate_sample_context(context, task)
    assert context["previous_state"] is None
    dump(output / "context-initial.json", context)

    model = load_json(ROOT / "evidence/minimax-development.json")
    before = ROOT / "demo/before/taskboard/service.py"
    after = ROOT / "demo/after/taskboard/service.py"
    assert hashlib.sha256(before.read_bytes()).hexdigest() == model["before_sha256"]
    assert hashlib.sha256(after.read_bytes()).hexdigest() == model["after_sha256"]
    request = load_json(ROOT / model["request_file"])
    assert hashlib.sha256(request["prompt"].encode()).hexdigest() == model["prompt_sha256"]
    # A web response must match the file attributed to MiniMax byte for byte.
    response = (ROOT / model["response_file"]).read_text(encoding="utf-8").strip()
    if response.startswith("```"):
        response = "\n".join(response.splitlines()[1:-1])
    generated = json.loads(response)
    assert generated["path"] == "taskboard/service.py"
    assert after.read_text(encoding="utf-8") == generated["content"]

    runs = {}
    for phase in ("before", "after"):
        for suite, file in (("feature", "test_status_filter.py"), ("regression", "test_regression.py")):
            runs[f"{phase}_{suite}"] = run_suite(ROOT, ROOT / f"demo/{phase}",
                f"demo/tests/{file}", output / phase / suite)
    assert runs["before_feature"]["exit_code"] == 1
    assert "failed" in runs["before_feature"]["stdout"] and "ERROR collecting" not in runs["before_feature"]["stdout"]
    assert runs["before_regression"]["exit_code"] == 0
    assert runs["after_feature"]["exit_code"] == runs["after_regression"]["exit_code"] == 0
    for suite in ("feature", "regression"):
        assert runs[f"before_{suite}"]["test_sha256"] == runs[f"after_{suite}"]["test_sha256"]
    patch = "".join(difflib.unified_diff(before.read_text(encoding="utf-8").splitlines(True),
        after.read_text(encoding="utf-8").splitlines(True), "demo/before/taskboard/service.py", "demo/after/taskboard/service.py"))
    (output / "code.diff").write_text(patch, encoding="utf-8", newline="\n")

    validation = {name: {"exit_code": item["exit_code"], "summary": item["stdout"].splitlines()[-1],
                         "test_sha256": item["test_sha256"]} for name, item in runs.items()}
    progress = {"status": "verified", "completed": ["seekdb material import and contextual retrieval",
                "MiniMax actual code modification", "feature and regression tests"], "todo": [],
                "validation": validation,
                "verified_source_hashes": {item["path"]: item["sha256"] for item in context["sources"]},
                "produced_code": {"demo/after/taskboard/service.py": model["after_sha256"]},
                "next_action": "Feature and regression tests passed. Review the saved patch; do not regenerate an already completed change."}
    async with TaskState(state_dir / "powercontext.db", task["project_id"], task["task_id"]) as store:
        assert await store.load() is None
        saved = await store.save(progress)
    dump(output / "powercontext-write.json", saved)
    command = [sys.executable, "-m", "sdd_context", "--state-dir", str(state_dir), "--task", str(output / "task.json"),
               "--output", str(output / "context-resumed.json"), "resume"]
    restarted = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=90)
    dump(output / "process-restart.json", {"command": ["${PYTHON}", *command[1:]], "exit_code": restarted.returncode,
                                          "stdout": restarted.stdout, "stderr": restarted.stderr})
    assert restarted.returncode == 0, restarted.stderr
    resumed = load_json(output / "context-resumed.json")
    assert resumed["previous_state"]["state"] == progress
    assert resumed["previous_state"]["source_id"] == saved["saved_source_id"]
    assert resumed["next_action"] == progress["next_action"]
    assert resumed["verification_current"] is True
    assert resumed["previous_state"]["state"]["todo"] == []
    sdk = importlib.metadata.distribution("powercontext")
    sdk_origin = json.loads(sdk.read_text("direct_url.json"))
    assert sdk_origin["vcs_info"]["commit_id"] == "9db1753434162c0aa7c9f1189a1722e617880a52"
    report = {"verified_at_utc": datetime.now(timezone.utc).isoformat(), "result": "passed",
              "checks": {"empty_initial_task_state": True, "required_context_sources": True,
                         "real_seekdb_query": True, "model_code_matches_record": True,
                         "unchanged_tests_before_and_after": True, "functional_change_and_regressions": True,
                         "PowerContext_process_restart_and_reuse": True},
              "tests": validation, "runtime_versions": {"python": sys.version.split()[0], "powercontext": sdk.version,
                                                         "sdk_origin": sdk_origin},
              "model_session": model["session_url"]}
    dump(output / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, default=ROOT / ".local" / ("review-" + uuid.uuid4().hex[:10]))
args = parser.parse_args()
asyncio.run(verify(args.output.resolve()))

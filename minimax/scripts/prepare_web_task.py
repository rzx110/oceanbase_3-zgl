"""Prepare source-grounded context for an actual MiniMax Code web session."""
import asyncio
import hashlib
import json
from pathlib import Path

from sdd_context.context import build_context, load_json, validate_sample_context
from sdd_context.state import TaskState
from sdd_context.storage import SeekDB

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".local/state"


async def main():
    task = load_json(ROOT / "demo/task.json")
    with SeekDB(ROOT, STATE) as db:
        imported = db.import_project(ROOT, load_json(ROOT / "project.json"))
        imports = {"result": imported, "queries": db.trace}
    context = await build_context(ROOT, ROOT, STATE, task)
    validate_sample_context(context, task)
    feature = load_json(ROOT / "evidence/before/feature.json")
    regression = load_json(ROOT / "evidence/before/regression.json")
    if feature["exit_code"] != 1 or regression["exit_code"] != 0:
        raise ValueError("Run the before tests; expected functional failures and passing regressions")
    for record in (feature, regression):
        for path, digest in record["code_sha256"].items():
            if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != digest:
                raise ValueError("before source changed since tests")
    progress = {"status": "baseline-tested", "completed": ["Imported specifications, code and tests into seekdb",
                "Retrieved required sources", "Ran unchanged feature and regression tests against before code"],
                "todo": ["MiniMax actual code modification", "Run after feature/regression tests", "Verify resumed task"],
                "validation": {"before_feature_exit_code": 1, "before_regression_exit_code": 0,
                               "before_feature_summary": feature["stdout"].splitlines()[-1],
                               "before_regression_summary": regression["stdout"].splitlines()[-1]},
                "verified_source_hashes": {x["path"]: x["sha256"] for x in context["sources"]},
                "next_action": "Use MiniMax to implement list_tasks status filtering, then run the unchanged tests."}
    async with TaskState(STATE / "powercontext.db", task["project_id"], task["task_id"]) as store:
        saved = await store.save(progress)
    resumed = await build_context(ROOT, ROOT, STATE, task)
    if resumed["previous_state"]["source_id"] != saved["saved_source_id"]:
        raise ValueError("PowerContext source was not reused")
    developer_context = {k: v for k, v in resumed.items() if k != "database"}
    prompt = ("Implement this real local development task using only the following source-grounded context. "
              "Return ONLY a JSON object with path and content. path must be taskboard/service.py; "
              "content must be the entire updated Python module. Keep Task and the public signature compatible. "
              "Do not modify tests, fixtures, or dependencies. Do not include reasoning or additional files.\n\n" +
              json.dumps(developer_context, ensure_ascii=False, indent=2))
    evidence = ROOT / "evidence"
    evidence.mkdir(exist_ok=True)
    for name, value in [("database-import.json", imports), ("context-before-model.json", resumed),
                        ("powercontext-before-model.json", saved),
                        ("minimax-request.json", {"method": "MiniMax Code web", "model_selected": "M3.1-Flash default",
                                                  "prompt": prompt, "context_sources": developer_context["sources"]})]:
        (evidence / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    (evidence / "minimax-prompt.txt").write_text(prompt, encoding="utf-8", newline="\n")
    print("Prepared real seekdb context and reopened PowerContext state.")
    print("MiniMax prompt characters:", len(prompt))


asyncio.run(main())

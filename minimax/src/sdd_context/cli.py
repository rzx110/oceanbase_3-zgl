"""Documented CLI: import, context, save-state, resume and agent."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from .agent import generate_change
from .context import build_context, load_json
from .state import TaskState
from .storage import SeekDB


async def run(args):
    app_root = Path(__file__).resolve().parents[2]
    project_root = args.root.resolve()
    state_dir = args.state_dir.resolve()
    task = load_json(args.task or project_root / "demo/task.json")
    if args.command == "import":
        manifest = load_json(project_root / "project.json")
        with SeekDB(app_root, state_dir) as db:
            result = db.import_project(project_root, manifest)
            result["database_queries"] = db.trace
    elif args.command in {"context", "resume"}:
        result = await build_context(app_root, project_root, state_dir, task, args.query)
    elif args.command == "save-state":
        async with TaskState(state_dir / "powercontext.db", task["project_id"], task["task_id"]) as store:
            result = await store.save(load_json(args.state_file))
    else:
        result = await generate_change(app_root, project_root, state_dir, task, args.snapshot, args.record)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"Saved {args.output}")
    else:
        print(text)


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="规有所依 — source-grounded SDD context")
    parser.add_argument("--root", type=Path, default=root, help="workspace with project.json and imported material paths")
    parser.add_argument("--state-dir", type=Path, default=root / ".local/state", help="seekdb and PowerContext data")
    parser.add_argument("--task", type=Path)
    parser.add_argument("--output", type=Path, help="write result JSON instead of printing the full context")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("import", help="import the declared materials into real seekdb")
    for name in ("context", "resume"):
        sub.add_parser(name, help="retrieve source-grounded context and saved progress").add_argument("--query")
    sub.add_parser("save-state", help="persist a task snapshot through PowerContext").add_argument("state_file", type=Path)
    agent = sub.add_parser("agent", help="send retrieved context to MiniMax and create a code snapshot")
    agent.add_argument("--snapshot", type=Path, required=True)
    agent.add_argument("--record", type=Path, required=True)
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except (ValueError, RuntimeError, OSError) as error:
        print(f"sdd-context: {error}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()

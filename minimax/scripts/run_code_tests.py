"""Run unchanged feature/regression tests against an explicit code snapshot."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_suite(root: Path, snapshot: Path, test: str, report_prefix: Path) -> dict:
    snapshot = snapshot.resolve()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(snapshot.resolve())
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", test]
    result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    record = {"command": ["${PYTHON}", *command[1:]], "working_directory": "minimax/",
              "snapshot": snapshot.resolve().relative_to(root).as_posix(),
              "code_sha256": {p.relative_to(root).as_posix(): digest(p) for p in sorted(snapshot.rglob("*.py"))},
              "test_file": test, "test_sha256": digest(root / test),
              "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
              "executed_at_utc": datetime.now(timezone.utc).isoformat()}
    report_prefix.parent.mkdir(parents=True, exist_ok=True)
    report_prefix.with_suffix(".json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    report_prefix.with_suffix(".txt").write_text("Command: " + " ".join(record["command"]) +
        f"\nSnapshot: {record['snapshot']}\nExit code: {result.returncode}\n\n" + result.stdout + result.stderr,
        encoding="utf-8")
    return record


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.snapshot.resolve().is_relative_to(root):
        raise SystemExit("Snapshot must be inside minimax/ so its code version can be recorded portably.")
    feature = run_suite(root, args.snapshot, "demo/tests/test_status_filter.py", args.report_dir / "feature")
    regression = run_suite(root, args.snapshot, "demo/tests/test_regression.py", args.report_dir / "regression")
    print(json.dumps({"feature_exit": feature["exit_code"], "regression_exit": regression["exit_code"],
                      "feature_output": feature["stdout"].splitlines()[-1],
                      "regression_output": regression["stdout"].splitlines()[-1]}, ensure_ascii=False))
    raise SystemExit(max(feature["exit_code"], regression["exit_code"]))

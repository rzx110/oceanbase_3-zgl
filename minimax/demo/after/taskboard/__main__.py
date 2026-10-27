"""Run the sample: python -m taskboard --data demo/data/tasks.json --status todo."""
import argparse
import json
from pathlib import Path

from .service import Task, list_tasks

parser = argparse.ArgumentParser()
parser.add_argument("--data", type=Path, required=True)
parser.add_argument("--status")
args = parser.parse_args()
tasks = [Task(**item) for item in json.loads(args.data.read_text(encoding="utf-8"))]
print(json.dumps([task.__dict__ for task in list_tasks(tasks, status=args.status)], ensure_ascii=False, indent=2))

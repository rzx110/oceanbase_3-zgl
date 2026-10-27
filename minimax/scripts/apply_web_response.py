"""Apply only the permitted source file from a copied public MiniMax response.

The developer supplies the real browser session URL and saves its public answer.
No HTTP-call metadata is invented for this web-based development workflow.
"""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--response", type=Path, required=True)
parser.add_argument("--session-url", required=True)
parser.add_argument("--snapshot", type=Path, default=root / "demo/after")
args = parser.parse_args()
url = urlsplit(args.session_url)
if url.scheme != "https" or url.hostname != "agent.minimax.cn" or url.path == "/":
    raise ValueError("Supply the real MiniMax Code task/session URL")
if args.snapshot.exists():
    raise ValueError("snapshot exists; preserve the prior code and choose a new output")
request = json.loads((root / "evidence/minimax-request.json").read_text(encoding="utf-8"))
text = args.response.read_text(encoding="utf-8")
clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
value = json.loads(clean)
if value.get("path") != "taskboard/service.py" or not isinstance(value.get("content"), str):
    raise ValueError("Only taskboard/service.py may be modified")
ast.parse(value["content"])
before = root / "demo/before"
for source in request["context_sources"]:
    if hashlib.sha256((root / source["path"]).read_bytes()).hexdigest() != source["sha256"]:
        raise ValueError("context source changed since the MiniMax request")
shutil.copytree(before, args.snapshot, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
target = args.snapshot / value["path"]
target.write_text(value["content"], encoding="utf-8", newline="\n")
record = {"provider": "MiniMax", "method": "Official MiniMax Code web UI",
          "session_url": args.session_url, "model_selected_in_ui": request["model_selected"],
          "request_file": "evidence/minimax-request.json", "response_file": args.response.resolve().relative_to(root).as_posix(),
          "prompt_sha256": hashlib.sha256(request["prompt"].encode("utf-8")).hexdigest(),
          "edited_file": value["path"],
          "before_sha256": hashlib.sha256((before / value["path"]).read_bytes()).hexdigest(),
          "after_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
          "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
          "note": "Actual public web response; no API response ID or private reasoning is claimed. See browser screenshot."}
(root / "evidence/minimax-development.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
print(json.dumps({k: record[k] for k in ("provider", "method", "edited_file", "after_sha256")}, ensure_ascii=False))

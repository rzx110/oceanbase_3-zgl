"""MiniMax code-generation adapter. Only public text/code and usage are exported."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request

from .context import build_context, validate_sample_context


def credentials(root: Path) -> dict:
    local = root / ".local/credentials.json"
    config = json.loads(local.read_text(encoding="utf-8")) if local.is_file() else {}
    return {"api_key": os.environ.get("MINIMAX_API_KEY") or config.get("api_key", ""),
            "model": os.environ.get("MINIMAX_MODEL") or config.get("model", "MiniMax-M3"),
            "base_url": os.environ.get("MINIMAX_BASE_URL") or config.get("base_url", "https://api.minimax.cn/anthropic")}


async def generate_change(root: Path, project_root: Path, state_dir: Path, task: dict, output: Path, record_path: Path) -> dict:
    config = credentials(root)
    if not config["api_key"]:
        raise ValueError("MiniMax key is not configured. Run python scripts/configure_minimax.py, or set MINIMAX_API_KEY.")
    endpoint = config["base_url"].rstrip("/") + "/v1/messages"
    url = urllib.parse.urlsplit(endpoint)
    if url.scheme != "https" or url.hostname not in {"api.minimax.cn", "api.minimax.io", "api.minimaxi.com"}:
        raise ValueError("Only official MiniMax HTTPS API endpoints are accepted")
    if output.exists():
        raise ValueError("output snapshot already exists; choose a new directory to preserve prior evidence")
    context = await build_context(root, project_root, state_dir, task)
    validate_sample_context(context, task)
    editable = task["editable_file"]
    before_file = (project_root / "demo/before" / editable).resolve()
    if not before_file.is_relative_to((project_root / "demo/before").resolve()):
        raise ValueError("editable file escapes before snapshot")
    prompt_context = {key: value for key, value in context.items() if key != "database"}
    prompt = ("Complete this local development task using the supplied source-grounded context. "
              "Keep public interfaces compatible. Do not change tests or dependency files. "
              "Return ONLY a JSON object with two fields: path and content. "
              f"path must equal {editable!r}; content is the entire replacement Python source file. "
              "Do not include reasoning, markdown fences, or any other files.\n\n" +
              json.dumps(prompt_context, ensure_ascii=False, indent=2))
    request_body = {"model": config["model"], "max_tokens": 4096,
                    "system": "You are a coding assistant implementing a tested specification. Return only the requested code JSON.",
                    "messages": [{"role": "user", "content": prompt}]}
    request = urllib.request.Request(endpoint, data=json.dumps(request_body).encode("utf-8"), headers={
        "Authorization": "Bearer " + config["api_key"], "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    })
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            body = json.load(response)
            request_id = response.headers.get("x-request-id") or response.headers.get("request-id")
    except urllib.error.HTTPError as error:
        # Error status is enough for diagnostics; never print request headers or credentials.
        raise RuntimeError(f"MiniMax HTTP {error.code}; check account quota and key/region configuration") from None
    public_text = "\n".join(block["text"] for block in body.get("content", []) if block.get("type") == "text")
    if not public_text:
        raise RuntimeError("MiniMax returned no public code text; no code snapshot was produced")
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", public_text.strip())
    result = json.loads(clean)
    if result.get("path") != editable or not isinstance(result.get("content"), str):
        raise ValueError("MiniMax response does not match the permitted edit")
    ast.parse(result["content"])
    shutil.copytree(project_root / "demo/before", output, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    target = output / editable
    target.write_text(result["content"], encoding="utf-8", newline="\n")
    record = {"provider": "MiniMax", "endpoint": endpoint, "model_requested": config["model"],
              "model_returned": body.get("model"), "response_id": body.get("id"), "request_id": request_id,
              "usage": body.get("usage"), "stop_reason": body.get("stop_reason"),
              "elapsed_seconds": round(time.monotonic() - started, 3),
              "request": request_body, "response_text": public_text,
              "edited_file": editable, "before_sha256": hashlib.sha256(before_file.read_bytes()).hexdigest(),
              "after_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
              "context_sources": [{"path": x["path"], "sha256": x["sha256"]} for x in context["sources"]],
              "privacy": "Authorization and thinking blocks are not recorded; only public response text is exported."}
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {key: record[key] for key in ("provider", "model_returned", "response_id", "edited_file", "after_sha256")}

"""Real HTTP/process-restart verification. Does not claim to execute Dify."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from oddity_pawnshop.rules import plan_turn

ROOT = Path(__file__).resolve().parents[1]


def main(output):
    output = output.resolve()
    if output.exists() or not output.is_relative_to(ROOT):
        raise ValueError("Choose a NEW verification directory inside dify/.")
    output.mkdir(parents=True)
    token = secrets.token_urlsafe(32)
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    records = []
    process = None
    stop_file = None
    log = (output / "server.log").open("w", encoding="utf-8")

    def request(method, path, data=None, params=None):
        url = base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        body = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
        req = urllib.request.Request(url, data=body, method=method, headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with opener.open(req, timeout=15) as response:
            value = json.load(response)
        records.append({"method": method, "path": path, "params": params, "request_body": data, "response": value})
        return value

    def start(phase):
        nonlocal process, stop_file
        stop_file = output / f"stop-{phase}"
        env = os.environ.copy()
        env["ODDITY_BACKEND_TOKEN"] = token
        process = subprocess.Popen([sys.executable, "-m", "oddity_pawnshop.server", "--port", str(port),
            "--database", str(output / "memory.db"), "--stop-file", str(stop_file)], cwd=ROOT,
            env=env, stdout=log, stderr=subprocess.STDOUT)
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("backend exited; see server.log")
            try:
                with opener.open(base + "/healthz", timeout=1) as response:
                    if response.status == 200:
                        return
            except OSError:
                time.sleep(0.1)
        raise TimeoutError("backend startup did not complete")

    def stop():
        if process is not None and process.poll() is None:
            stop_file.touch()
            process.wait(timeout=20)

    def state(player="backend_review"):
        return request("GET", "/v1/state", params={"player_code": player, "item": "umbrella"})

    def play(action, text):
        plan = plan_turn("backend_review", "umbrella", action, text, state()["state"])
        body = {k: plan[k] for k in ("player_code", "item", "action", "utterance", "expected_turn", "operation_id", "state")}
        body["narrative"] = plan["fallback"]
        return request("POST", "/v1/interactions", body), body

    try:
        start("first")
        assert state()["state"]["turn"] == 0
        first, body = play("prank", "我把伞面换成了会说早安的海苔")
        repeated = request("POST", "/v1/interactions", body)
        assert first == repeated and state()["state"]["turn"] == 1
        first_pid = process.pid
        stop()
        start("restarted")
        assert process.pid != first_pid
        restored = state()
        assert restored == first and "海苔" in restored["state"]["history"][0]["summary"]
        apologized, _ = play("apologize", "我带着熨斗来道歉")
        assert apologized["state"]["grudge"] == 0 and apologized["state"]["pranks"] == 1
        play("gift", "一个不下雨的星期一")
        ready, _ = play("greet", "这次我认真打招呼")
        assert ready["state"]["trust"] == 3
        assert plan_turn("backend_review", "umbrella", "explore", "", ready["state"])["unlocked"] is True
        played, _ = play("explore", "请说说你的秘密")
        assert "明天的尴尬" in played["last_story"]
        exact = request("GET", "/v1/source", params={"player_code": "backend_review", "item": "umbrella", "source_id": first["memory_source"]["source_id"]})
        assert exact == first
        assert state("another_player")["state"]["turn"] == 0
        stop()
        report = {"kind": "real-backend-http-verification", "result": "passed", "dify_execution": False,
            "checks": {"empty_start": True, "idempotent_retry": True, "graceful_process_restart": True,
                "history_reused": True, "apology_and_gift_change_outcome": True, "secret_unlock": True,
                "exact_old_source_readback": True, "player_isolation": True}, "requests": records,
            "source_sha256": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT / "src").rglob("*.py"))}}
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        print(json.dumps({k: report[k] for k in ("kind", "result", "dify_execution", "checks")}, ensure_ascii=False, indent=2))
    finally:
        stop()
        log.close()


parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, default=ROOT / ".local" / ("backend-review-" + uuid.uuid4().hex[:8]))
main(parser.parse_args().output)

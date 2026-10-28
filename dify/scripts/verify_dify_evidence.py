"""Read back the exact Sources returned by saved, actual Dify executions.

Does not run Dify or synthesize its transcripts. Requires previously saved UI
results and the same local PowerContext database/backend used by those runs.
"""
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/dify-ui"
token = json.loads((ROOT / ".local/backend.json").read_text())["token"]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def readback(player, item, receipt):
    params = {"player_code": player, "item": item, "source_id": receipt["source_id"]}
    request = urllib.request.Request("http://127.0.0.1:8788/v1/source?" + urllib.parse.urlencode(params),
        headers={"Authorization": "Bearer " + token})
    with opener.open(request, timeout=15) as response:
        record = json.load(response)
    assert record["memory_source"] == receipt
    return record


def copied_receipt(path):
    return json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])


rounds = []
for index, trust in enumerate([1, -1, 1, 3, 3], start=1):
    receipt = copied_receipt(EVIDENCE / f"round-{index:02d}-output.txt")
    record = readback("review_copper_01", "umbrella", receipt)
    assert record["state"]["turn"] == index and record["state"]["trust"] == trust
    assert len(record["state"]["history"]) == index
    rounds.append(record)

recall = json.loads((EVIDENCE / "round-06-result.json").read_text(encoding="utf-8"))
assert json.loads(recall["recall_memory_source"]) == rounds[-1]["memory_source"]
assert "第 5 轮" in recall["recall_reply"]
assert "0 Tokens" in (EVIDENCE / "round-06-details.txt").read_text(encoding="utf-8")
restart = json.loads((EVIDENCE / "backend-restart.json").read_text(encoding="utf-8"))
assert restart["before"] == restart["after"] == rounds[1]
assert restart["before_pid"] != restart["after_pid"]
public = readback("public_glass_01", "umbrella", copied_receipt(EVIDENCE / "public-web-output.txt"))
assert public["state"]["turn"] == 1 and public["state"]["pranks"] == 0
fresh = readback("fresh_reimport_01", "clock", copied_receipt(EVIDENCE / "reimport-output.txt"))
assert fresh["state"]["turn"] == 1 and fresh["state"]["trust"] == 1
assert fresh["memory_source"]["scope_id"] != rounds[0]["memory_source"]["scope_id"]

audit = [json.loads(line) for line in (ROOT / ".local/memory-audit.jsonl").read_text(encoding="utf-8").splitlines()]
audit = [event for event in audit if event.get("player_code") == "review_copper_01"]
assert len([event for event in audit if event["event"] == "memory_write"]) == 5
(EVIDENCE / "powercontext-audit.jsonl").write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in audit), encoding="utf-8", newline="\n")
report = {
    "kind": "actual-Dify-UI-results-exact-PowerContext-readback", "result": "passed",
    "host": "Dify Cloud", "observed_date": "2026-10-28",
    "published_url": "https://udify.app/workflow/jvk3Zdb4WkHl6qLC",
    "main_app_id": "0af47a97-849f-4918-80e9-563bf1e1b256",
    "reimport_app_id": "9e7b235a-c80d-4ffe-9ee6-f2fb3418f613",
    "checks": {"five_ordered_interactions": True, "same_database_restart_after_round_two": True,
        "recall_does_not_add_a_turn_or_write": True, "recall_zero_model_tokens_observed": True,
        "all_five_old_source_receipts_exact": True, "published_web_app_execution": True,
        "delivered_DSL_reimported_and_run": True, "fresh_player_isolated": True},
    "round_records": rounds, "public_web_record": public, "reimport_record": fresh,
    "delivered_dsl_sha256": hashlib.sha256((ROOT / "workflow/oddity-pawnshop.yml").read_bytes()).hexdigest(),
    "limitations": "Checks recorded Dify UI results against the live backend; does not invoke a Dify private API or certify the contest score."
}
(ROOT / "evidence/dify-live-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
print(json.dumps({"result": report["result"], "checks": report["checks"]}, ensure_ascii=False, indent=2))

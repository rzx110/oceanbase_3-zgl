"""Check Dify Code-node serialization boundaries; this is not a Dify-host run."""
import json
from pathlib import Path
import runpy
import yaml

from oddity_pawnshop.rules import initial_state

BUILDER = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/build_dsl.py"))


def test_unicode_and_quotes_cross_http_payload_without_changing_state():
    plan_namespace = {}
    exec(BUILDER["PLAN_CODE"], plan_namespace)
    quoted = '礼物是「会说“早安”的云」\n不是普通雨水'
    result = plan_namespace["main"]("player001", "umbrella", "gift", quoted,
        json.dumps({"state": initial_state(), "last_story": "", "memory_source": None}), 200)
    plan = json.loads(result["plan_json"])
    assert plan["utterance"] == quoted and plan["state"]["trust"] == 2
    assert "secret" not in json.loads(result["prompt"])["奇物"]
    payload_namespace = {}
    exec(BUILDER["COMMIT_CODE"], payload_namespace)
    body = payload_namespace["main"](result["plan_json"], "ODDITY_NARRATOR_UNAVAILABLE")["body"]
    payload = json.loads(body)
    assert payload["utterance"] == quoted
    assert payload["narrative"] == plan["fallback"]
    assert payload["state"] == plan["state"]


def test_exact_memory_receipt_is_preserved_in_feedback_node():
    namespace = {}
    exec(BUILDER["OUTPUT_CODE"], namespace)
    source = {"source_id": "oddity:" + "1" * 64, "scope_id": "synthetic-boundary-test", "sequence": 1}
    result = namespace["main"](json.dumps({"state": initial_state(), "last_story": "你好，奇怪的人类。", "memory_source": source}), 200)
    assert json.loads(result["memory_source"]) == source
    assert source["source_id"] in result["reply"]


def test_delivered_cloud_export_matches_tested_code_and_has_unique_outputs():
    root = Path(__file__).resolve().parents[1]
    document = yaml.safe_load((root / "workflow/oddity-pawnshop.yml").read_text(encoding="utf-8"))
    nodes = {node["id"]: node["data"] for node in document["workflow"]["graph"]["nodes"]}
    for node_id, constant in [("plan", "PLAN_CODE"), ("payload", "COMMIT_CODE"), ("feedback", "OUTPUT_CODE")]:
        assert nodes[node_id]["code"] == BUILDER[constant]
    outputs = [output["variable"] for node in nodes.values() if node["type"] == "end" for output in node["outputs"]]
    assert len(outputs) == len(set(outputs))
    environment = {value["name"]: value["value"] for value in document["workflow"]["environment_variables"]}
    assert environment == {"BACKEND_URL": "http://host.docker.internal:8788", "BACKEND_TOKEN": "REPLACE_WITH_YOUR_LOCAL_TOKEN"}
    assert document["dependencies"] and nodes["narrator"]["model"]["name"] == "gpt-4o-mini"

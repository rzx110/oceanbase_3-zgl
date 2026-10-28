"""Backend tests use the real PowerContext Runtime, separately from Dify live runs."""
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient

from oddity_pawnshop.rules import plan_turn
from oddity_pawnshop.server import create_app

TOKEN = "test-only-token-for-oddity-memory-verification"
HEADERS = {"Authorization": "Bearer " + TOKEN}


def play(client, player, action, text="", item="umbrella"):
    current = client.get("/v1/state", params={"player_code": player, "item": item}, headers=HEADERS)
    assert current.status_code == 200
    plan = plan_turn(player, item, action, text, current.json()["state"])
    payload = {key: plan[key] for key in ("player_code", "item", "action", "utterance", "expected_turn", "operation_id", "state")}
    payload["narrative"] = plan["fallback"]
    result = client.post("/v1/interactions", json=payload, headers=HEADERS)
    assert result.status_code == 200, result.text
    return result.json(), payload


def test_real_memory_survives_restart_and_keeps_exact_old_source(tmp_path):
    database = tmp_path / "memory.db"
    with TestClient(create_app(database, TOKEN)) as client:
        first, _ = play(client, "player001", "prank", "把雨滴换成了泡泡糖")
        assert first["state"]["turn"] == 1 and first["state"]["grudge"] == 1
        first_ref = first["memory_source"]
    with TestClient(create_app(database, TOKEN)) as client:
        current = client.get("/v1/state", params={"player_code": "player001", "item": "umbrella"}, headers=HEADERS).json()
        assert current == first
        assert "泡泡糖" in current["state"]["history"][0]["summary"]
        second, _ = play(client, "player001", "apologize", "我愿意替雨伞烘干袜子")
        assert second["state"]["turn"] == 2 and second["state"]["grudge"] == 0
        assert second["state"]["pranks"] == 1
        exact = client.get("/v1/source", params={"player_code": "player001", "item": "umbrella", "source_id": first_ref["source_id"]}, headers=HEADERS)
        assert exact.status_code == 200 and exact.json() == first


def test_idempotent_retry_and_stale_turn_rejection(tmp_path):
    with TestClient(create_app(tmp_path / "memory.db", TOKEN)) as client:
        first, request = play(client, "player001", "gift", "一朵泡椒味的云")
        repeat = client.post("/v1/interactions", json=request, headers=HEADERS)
        assert repeat.status_code == 200 and repeat.json() == first
        current = client.get("/v1/state", params={"player_code": "player001", "item": "umbrella"}, headers=HEADERS).json()
        assert current["state"]["turn"] == 1 and current["state"]["gifts"] == 1
        stale = deepcopy(request)
        stale["operation_id"] = "f" * 64
        assert client.post("/v1/interactions", json=stale, headers=HEADERS).status_code == 409


def test_player_and_item_isolation_and_authentication(tmp_path):
    with TestClient(create_app(tmp_path / "memory.db", TOKEN)) as client:
        first, _ = play(client, "player001", "prank", "给雨伞贴了小胡子")
        for player, item in [("player002", "umbrella"), ("player001", "clock")]:
            current = client.get("/v1/state", params={"player_code": player, "item": item}, headers=HEADERS).json()
            assert current["state"]["turn"] == 0 and current["memory_source"] is None
        assert client.get("/v1/state", params={"player_code": "player001", "item": "umbrella"}).status_code == 401
        assert client.get("/v1/source", params={"player_code": "player002", "item": "umbrella", "source_id": first["memory_source"]["source_id"]}, headers=HEADERS).status_code == 404
        assert client.get("/v1/state", params={"player_code": "../other", "item": "umbrella"}, headers=HEADERS).status_code == 422


def test_choices_affect_secret_and_recall_does_not_write(tmp_path):
    with TestClient(create_app(tmp_path / "memory.db", TOKEN)) as client:
        first, _ = play(client, "player001", "greet", "我带着一个未完成的周末")
        locked = plan_turn("player001", "umbrella", "explore", "", first["state"])
        assert locked["unlocked"] is False
        second, _ = play(client, "player001", "gift", "一个不下雨的星期一")
        unlocked = plan_turn("player001", "umbrella", "explore", "", second["state"])
        assert unlocked["unlocked"] is True and second["state"]["trust"] == 3
        recall = plan_turn("player001", "umbrella", "recall", "", second["state"])
        assert recall["write"] is False and recall["state"] == second["state"]
        assert "星期一" in recall["fallback"]

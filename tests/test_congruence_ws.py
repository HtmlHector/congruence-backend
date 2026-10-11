"""Integration tests for Unified WebSocket Gateway."""

import json
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from src.infrastructure.database.session import get_db
from src.main import app
from tests.conftest import MockAsyncSession, MockResult


class WebSocketDbSession(MockAsyncSession):
    async def execute(self, statement, params=None):
        sql = str(statement)
        try:
            compiled = statement.compile()
            values = list((params or compiled.params).values())
        except Exception:
            values = list((params or {}).values())

        if "FROM congruence_actors" in sql:
            requested_id = next((v for v in values if isinstance(v, str) and v.startswith("act_")), None)
            if requested_id == "act_claude":
                return MockResult([SimpleNamespace(id="act_claude", project_id="proj_ws", user_id=None, role="watcher", actor_type="harness_claude", display_name="Claude")])
            if requested_id:
                return MockResult([])
            return MockResult([SimpleNamespace(id="act_human", project_id="proj_ws", user_id="ws_user", role="owner", actor_type="human", display_name="You")])
        if "FROM congruence_lanes" in sql:
            lane_id = next((v for v in values if isinstance(v, str) and v.startswith("lane_")), "lane_pair")
            return MockResult([SimpleNamespace(id=lane_id, host_id="host_ws", branch_name="main", is_pair_lane=False)])
        if "FROM congruence_hosts" in sql:
            return MockResult([SimpleNamespace(id="host_ws", project_id="proj_ws")])
        if "FROM congruence_projects" in sql:
            return MockResult([SimpleNamespace(id="proj_ws", organization_id="org_ws")])
        if "congruence_organization_members" in sql:
            return MockResult([("proj_ws",)])
        if "FROM congruence_grants" in sql:
            return MockResult([])
        return await super().execute(statement, params)


async def override_ws_db():
    yield WebSocketDbSession()


def _client():
    app.dependency_overrides[get_db] = override_ws_db
    return TestClient(app)


@pytest.fixture(autouse=True)
def restore_ws_db_override():
    previous = app.dependency_overrides.get(get_db)
    yield
    if previous is None:
        app.dependency_overrides.pop(get_db, None)
    else:
        app.dependency_overrides[get_db] = previous


@pytest.fixture
def ws_client():
    return _client()


def test_websocket_session_handshake_and_lease(ws_client):
    client = ws_client
    with client.websocket_connect(
        "/ws/session/lane_pair?token=mock_ws_user"
    ) as ws:
        # Handshake ACK
        handshake_raw = ws.receive_text()
        handshake = json.loads(handshake_raw)
        assert handshake["type"] == "handshake_ack"
        assert handshake["session_id"] == "lane_pair"

        # Grant Control
        ws.send_text(
            json.dumps(
                {
                    "type": "grant_control",
                    "lane_id": "lane_pair",
                    "actor_id": "act_claude",
                }
            )
        )
        lease_raw = ws.receive_text()
        lease = json.loads(lease_raw)
        assert lease["type"] == "lease_updated"
        assert lease["lane_id"] == "lane_pair"
        assert lease["current_writer"] == "act_claude"

        # Revoke Control
        ws.send_text(
            json.dumps(
                {
                    "type": "revoke_control",
                    "lane_id": "lane_pair",
                }
            )
        )
        revoke_raw = ws.receive_text()
        revoke = json.loads(revoke_raw)
        assert revoke["type"] == "lease_updated"
        assert revoke["current_writer"] == "act_human"


def test_websocket_pty_input_cannot_claim_another_actor(ws_client, monkeypatch):
    import src.routes.session_ws as ws_module

    class Runner:
        def __init__(self):
            self.writes = []

        async def get_host_state(self, _):
            return "awake"

        async def write_pty(self, pty_id, data):
            self.writes.append((pty_id, data))

    runner = Runner()
    monkeypatch.setattr(ws_module, "get_runner", lambda: runner)
    ws_module.manager._active_ptys["lane_pair"] = {"pty_owned"}
    ws_module.manager._pty_lanes["lane_pair"] = {"pty_owned": "lane_pair"}

    with ws_client.websocket_connect("/ws/session/lane_pair?token=mock_ws_user") as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"type": "pty_input", "lane_id": "lane_pair", "pty_id": "pty_owned", "actor_id": "act_claude", "data": "id\\n"}))
        response = json.loads(ws.receive_text())
        assert response["type"] == "pty_input_denied"
        assert runner.writes == []


def test_websocket_does_not_accept_pty_ids_from_another_session(ws_client):
    with ws_client.websocket_connect("/ws/session/lane_pair?token=mock_ws_user") as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"type": "pty_input", "lane_id": "lane_pair", "pty_id": "pty_foreign", "actor_id": "act_human", "data": "id\\n"}))
        response = json.loads(ws.receive_text())
        assert response["type"] == "pty_input_denied"


def test_websocket_sleep_wake_and_dev_server(ws_client):
    client = ws_client
    with client.websocket_connect("/ws/session/lane_main?token=mock_ws_user") as ws:
        # Consume handshake
        _ = ws.receive_text()

        # Start dev server
        ws.send_text(
            json.dumps(
                {
                    "type": "start_dev",
                    "lane_id": "lane_main",
                    "command": "echo 'running' && sleep 5",
                    "port": 3005,
                }
            )
        )
        dev_raw = ws.receive_text()
        dev_msg = json.loads(dev_raw)
        assert dev_msg["type"] == "dev_server_status"
        assert dev_msg["is_running"] is True
        assert dev_msg["port"] == 3005

        # Sleep host
        ws.send_text(json.dumps({"type": "sleep_workspace"}))
        sleep_raw = ws.receive_text()
        sleep_msg = json.loads(sleep_raw)
        assert sleep_msg["type"] == "host_state_changed"
        assert sleep_msg["state"] == "asleep"

        # Wake host
        ws.send_text(json.dumps({"type": "wake_workspace"}))
        wake_raw = ws.receive_text()
        wake_msg = json.loads(wake_raw)
        assert wake_msg["type"] == "host_state_changed"
        assert wake_msg["state"] == "awake"

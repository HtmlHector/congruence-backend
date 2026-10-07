"""Integration tests for Unified WebSocket Gateway."""

import json

from starlette.testclient import TestClient

from src.main import app


def test_websocket_session_handshake_and_lease():
    client = TestClient(app)
    with client.websocket_connect("/ws/session/sess_test_handshake") as ws:
        # Handshake ACK
        handshake_raw = ws.receive_text()
        handshake = json.loads(handshake_raw)
        assert handshake["type"] == "handshake_ack"
        assert handshake["session_id"] == "sess_test_handshake"

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


def test_websocket_sleep_wake_and_dev_server():
    client = TestClient(app)
    with client.websocket_connect("/ws/session/sess_test_dev") as ws:
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

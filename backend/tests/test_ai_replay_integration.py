from __future__ import annotations

import json

import pytest

import app as legacy_app
import db as backend_db

from tests.utils import (
    QueryPlan,
    assert_timeline_is_ordered,
    build_failure_case,
    build_workflow_case,
    install_replay_harness,
)


@pytest.fixture()
def client():
    legacy_app.app.config["TESTING"] = True
    legacy_app.app.config["RATELIMIT_ENABLED"] = False
    return legacy_app.app.test_client()


def _response_json(response):
    data = response.get_data(as_text=True)
    return json.loads(data)


def _assert_replay_payload(payload, workflow_row, expected_actions):
    assert payload["status"] == "success"
    assert payload["workflow_id"] == workflow_row["id"]
    assert payload["user_id"] == workflow_row["user_id"]
    assert payload["workflow_type"] == workflow_row["workflow_type"]
    assert payload["replay_run_id"]
    assert isinstance(payload["metrics"], dict)
    assert payload["metrics"]["action_count"] == len(expected_actions)
    assert payload["timeline"]
    assert_timeline_is_ordered(payload["timeline"], expected_actions)
    for entry in payload["timeline"]:
        assert isinstance(entry["duration_ms"], int)
        assert entry["started_at"] is not None
        assert entry["completed_at"] is not None
        assert entry["retry_count"] >= 0
        assert entry["execution_status"]


@pytest.mark.integration
@pytest.mark.smoke
@pytest.mark.parametrize(
    "workflow_name, expected_actions",
    [
        ("booking", ["CREATE_SHIPMENT", "ASSIGN_DRIVER"]),
        ("tracking", ["TRACK_SHIPMENT", "GET_PRICE_ESTIMATE"]),
        ("payment", ["MAKE_PAYMENT", "GENERATE_INVOICE"]),
        ("delivery", ["DELIVERY_CONFIRMATION", "CUSTOMER_NOTIFICATION"]),
    ],
)
def test_replay_endpoint_returns_timeline_and_events(monkeypatch, client, workflow_name, expected_actions):
    workflow_row, action_rows = build_workflow_case(workflow_name)
    plan = QueryPlan(workflow_row=workflow_row, action_rows=action_rows)
    install_replay_harness(monkeypatch, legacy_app, backend_db, plan)

    response = client.post(f"/api/admin/ai/replay/{workflow_row['id']}")
    payload = _response_json(response)

    assert response.status_code == 200
    _assert_replay_payload(payload, workflow_row, expected_actions)

    progress_events = [event for event in plan.emitted_events if event[0] == "ai:replay:progress"]
    complete_events = [event for event in plan.emitted_events if event[0] == "ai:replay:complete"]

    assert len(progress_events) == len(expected_actions)
    assert len(complete_events) == 1
    assert complete_events[0][1]["workflow_id"] == workflow_row["id"]
    assert complete_events[0][1]["timeline_length"] == len(expected_actions)
    assert complete_events[0][1]["metrics"]["action_count"] == len(expected_actions)
    assert complete_events[0][1]["metrics"]["success_rate"] >= 0
    assert plan.saved_audits and plan.saved_audits[0]["action"] == "REPLAY"


@pytest.mark.integration
@pytest.mark.parametrize(
    "failure_name, expected_status, expected_execution_status, expected_error_fragment",
    [
        ("failed_payment", "failed", "failed", "payment gateway rejected"),
        ("gps_timeout", "error", "timeout", "GPS timeout"),
        ("ai_timeout", "error", "timeout", "AI execution timeout"),
        ("delayed_shipment", "success", "success", None),
    ],
)
def test_replay_handles_controlled_failures(monkeypatch, client, failure_name, expected_status, expected_execution_status, expected_error_fragment):
    workflow_row, action_rows = build_failure_case(failure_name)
    plan = QueryPlan(workflow_row=workflow_row, action_rows=action_rows)
    install_replay_harness(monkeypatch, legacy_app, backend_db, plan)

    response = client.post(f"/api/admin/ai/replay/{workflow_row['id']}")
    payload = _response_json(response)

    assert response.status_code == 200
    assert payload["timeline"][0]["status"] == expected_status or payload["timeline"][0]["execution_status"] == expected_status
    assert payload["timeline"][0]["execution_status"] == expected_execution_status
    assert payload["timeline"][0]["retry_count"] >= 0
    if expected_error_fragment:
        assert expected_error_fragment.lower() in (payload["timeline"][0].get("error_message") or "").lower()
    assert payload["metrics"]["action_count"] == 1
    assert payload["timeline"][0]["duration_ms"] >= 0
    assert payload["metrics"]["success_rate"] >= 0


@pytest.mark.integration
def test_replay_survives_websocket_disconnect(monkeypatch, client):
    workflow_row, action_rows = build_failure_case("websocket_disconnect")
    plan = QueryPlan(workflow_row=workflow_row, action_rows=action_rows, socket_raise=RuntimeError("socket disconnected"))
    install_replay_harness(monkeypatch, legacy_app, backend_db, plan)

    response = client.post(f"/api/admin/ai/replay/{workflow_row['id']}")
    payload = _response_json(response)

    assert response.status_code == 200
    assert payload["timeline"][0]["action"] == "DELIVERY_CONFIRMATION"
    assert payload["metrics"]["action_count"] == 1
    assert plan.emitted_events == []


@pytest.mark.integration
def test_admin_metrics_endpoint_returns_summary(monkeypatch, client):
    workflow_row, action_rows = build_workflow_case("booking")
    plan = QueryPlan(workflow_row=workflow_row, action_rows=action_rows)
    install_replay_harness(monkeypatch, legacy_app, backend_db, plan)

    expected_metrics = {
        "summary": {
            "total_actions": 2,
            "avg_duration_ms": 105.0,
            "precise_avg_ms": 105.0,
            "failure_count": 0,
            "retry_count": 0,
            "slowest_ms": 120,
            "avg_ai_response_latency_ms": 105.0,
            "avg_websocket_latency_ms": 0.0,
            "workflow_success_rate": 100.0,
        },
        "tools": [
            {
                "tool_name": "create_shipment",
                "executions": 1,
                "avg_duration_ms": 120,
                "failure_count": 0,
                "retried_count": 0,
                "max_duration_ms": 120,
            }
        ],
        "slowest_tool": {
            "tool_name": "create_shipment",
            "executions": 1,
            "avg_duration_ms": 120,
            "failure_count": 0,
            "retried_count": 0,
            "max_duration_ms": 120,
        },
    }

    monkeypatch.setattr(backend_db, "get_ai_tool_metrics", lambda filters=None: expected_metrics)
    response = client.get("/api/admin/ai/metrics")
    payload = _response_json(response)

    assert response.status_code == 200
    assert payload["status"] == "success"
    assert payload["summary"]["total_actions"] == 2
    assert payload["tools"][0]["tool_name"] == "create_shipment"
    assert "avg_ai_response_latency_ms" in payload["summary"]
    assert "avg_websocket_latency_ms" in payload["summary"]
    assert "workflow_success_rate" in payload["summary"]

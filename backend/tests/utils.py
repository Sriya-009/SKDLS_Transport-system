from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def build_workflow_row(
    workflow_id: int,
    user_id: str,
    workflow_type: str,
    *,
    current_step: str = "replay_ready",
    context_json: dict[str, Any] | None = None,
    workflow_state: dict[str, Any] | None = None,
    updated_at: datetime | None = None,
) -> dict[str, Any]:
    updated_at = updated_at or datetime(2026, 5, 29, 10, 0, 0)
    return {
        "id": workflow_id,
        "user_id": user_id,
        "workflow_type": workflow_type,
        "workflow_state": _json(workflow_state or {"workflow_id": workflow_id, "type": workflow_type}),
        "current_step": current_step,
        "context_json": _json(context_json or {"workflow_type": workflow_type, "user_id": user_id}),
        "updated_at": updated_at,
    }


def build_action_rows(
    user_id: str,
    shipment_id: int,
    actions: list[dict[str, Any]],
    *,
    start_at: datetime | None = None,
) -> list[dict[str, Any]]:
    start_at = start_at or datetime(2026, 5, 29, 9, 59, 0)
    rows: list[dict[str, Any]] = []
    cursor_time = start_at

    for index, action in enumerate(actions, start=1):
        duration_ms = int(action.get("duration_ms") or 120)
        started_at = cursor_time
        completed_at = started_at + timedelta(milliseconds=duration_ms)
        retry_count = int(action.get("retry_count") or 0)
        execution_status = str(action.get("execution_status") or action.get("status") or "success")
        row = {
            "id": index,
            "user_id": user_id,
            "role": action.get("role") or "admin",
            "action": action.get("action"),
            "intent": action.get("intent") or action.get("action"),
            "tool_name": action.get("tool_name"),
            "shipment_id": shipment_id,
            "status": action.get("status") or execution_status,
            "request_payload": _json(action.get("request_payload") or {"step": index, "action": action.get("action")}),
            "response_payload": _json(action.get("response_payload") or {"ok": execution_status == "success", "step": index}),
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_ms": duration_ms,
            "retry_count": retry_count,
            "execution_status": execution_status,
            "error_message": action.get("error_message"),
            "created_at": started_at,
        }
        rows.append(row)
        cursor_time = completed_at + timedelta(milliseconds=25)

    return rows


def build_workflow_case(name: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    name = name.lower().strip()
    if name == "booking":
        workflow_row = build_workflow_row(101, "admin-001", "booking", current_step="shipment_created")
        actions = [
            {"action": "CREATE_SHIPMENT", "tool_name": "create_shipment", "intent": "BOOK_SHIPMENT", "duration_ms": 120},
            {"action": "ASSIGN_DRIVER", "tool_name": "assign_driver", "intent": "DRIVER_UPDATE", "duration_ms": 90},
        ]
    elif name == "payment":
        workflow_row = build_workflow_row(102, "admin-001", "payments", current_step="payment_completed")
        actions = [
            {"action": "MAKE_PAYMENT", "tool_name": "create_payment", "intent": "MAKE_PAYMENT", "duration_ms": 140},
            {"action": "GENERATE_INVOICE", "tool_name": "generate_invoice", "intent": "GENERATE_INVOICE", "duration_ms": 85},
        ]
    elif name == "tracking":
        workflow_row = build_workflow_row(103, "admin-001", "tracking", current_step="tracking_live")
        actions = [
            {"action": "TRACK_SHIPMENT", "tool_name": "track_shipment", "intent": "TRACK_SHIPMENT", "duration_ms": 110},
            {"action": "GET_PRICE_ESTIMATE", "tool_name": "calculate_eta", "intent": "GET_PRICE_ESTIMATE", "duration_ms": 75},
        ]
    elif name == "delivery":
        workflow_row = build_workflow_row(104, "admin-001", "delivery", current_step="delivery_confirmed")
        actions = [
            {"action": "DELIVERY_CONFIRMATION", "tool_name": "confirm_delivery", "intent": "DELIVERY_CONFIRMATION", "duration_ms": 130},
            {"action": "CUSTOMER_NOTIFICATION", "tool_name": "notify_customer", "intent": "CUSTOMER_NOTIFICATION", "duration_ms": 60},
        ]
    else:
        raise ValueError(f"Unknown workflow case: {name}")

    shipment_id = 7000 + workflow_row["id"]
    rows = build_action_rows(workflow_row["user_id"], shipment_id, actions)
    return workflow_row, rows


def build_failure_case(name: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    name = name.lower().strip()
    if name == "failed_payment":
        workflow_row = build_workflow_row(201, "admin-002", "payments", current_step="payment_failed")
        actions = [
            {
                "action": "MAKE_PAYMENT",
                "tool_name": "create_payment",
                "intent": "MAKE_PAYMENT",
                "status": "failed",
                "execution_status": "failed",
                "duration_ms": 180,
                "retry_count": 2,
                "error_message": "payment gateway rejected the request",
            }
        ]
    elif name == "gps_timeout":
        workflow_row = build_workflow_row(202, "admin-003", "tracking", current_step="gps_timeout")
        actions = [
            {
                "action": "TRACK_SHIPMENT",
                "tool_name": "track_shipment",
                "intent": "TRACK_SHIPMENT",
                "status": "error",
                "execution_status": "timeout",
                "duration_ms": 5100,
                "retry_count": 1,
                "error_message": "GPS timeout while waiting for truck location",
            }
        ]
    elif name == "ai_timeout":
        workflow_row = build_workflow_row(203, "admin-004", "booking", current_step="ai_timeout")
        actions = [
            {
                "action": "CREATE_SHIPMENT",
                "tool_name": "create_shipment",
                "intent": "BOOK_SHIPMENT",
                "status": "error",
                "execution_status": "timeout",
                "duration_ms": 7600,
                "retry_count": 0,
                "error_message": "AI execution timeout while creating shipment",
            }
        ]
    elif name == "websocket_disconnect":
        workflow_row = build_workflow_row(204, "admin-005", "delivery", current_step="disconnect")
        actions = [
            {
                "action": "DELIVERY_CONFIRMATION",
                "tool_name": "confirm_delivery",
                "intent": "DELIVERY_CONFIRMATION",
                "status": "success",
                "execution_status": "success",
                "duration_ms": 95,
                "retry_count": 0,
            }
        ]
    elif name == "delayed_shipment":
        workflow_row = build_workflow_row(205, "admin-006", "delivery", current_step="delay_added")
        actions = [
            {
                "action": "DELAY_MANAGEMENT",
                "tool_name": "manage_delay",
                "intent": "DELAY_MANAGEMENT",
                "status": "success",
                "execution_status": "success",
                "duration_ms": 210,
                "retry_count": 0,
            }
        ]
    else:
        raise ValueError(f"Unknown failure case: {name}")

    shipment_id = 8000 + workflow_row["id"]
    rows = build_action_rows(workflow_row["user_id"], shipment_id, actions)
    return workflow_row, rows


@dataclass
class QueryPlan:
    workflow_row: dict[str, Any]
    action_rows: list[dict[str, Any]]
    saved_audits: list[dict[str, Any]] = field(default_factory=list)
    emitted_events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    socket_raise: Exception | None = None


class FakeCursor:
    def __init__(self, plan: QueryPlan):
        self.plan = plan
        self._rows: list[dict[str, Any]] = []
        self._row: dict[str, Any] | None = None
        self.rowcount = 0

    def execute(self, query: str, params=None):
        lower_query = query.lower()
        self._rows = []
        self._row = None

        if "from ai_workflow_memory" in lower_query and "where id = %s" in lower_query:
            self._row = self.plan.workflow_row
            self.rowcount = 1 if self._row else 0
            return

        if "from ai_action_logs" in lower_query and "where user_id = %s and created_at <= %s" in lower_query:
            self._rows = list(self.plan.action_rows)
            self.rowcount = len(self._rows)
            return

        if "from ai_action_logs" in lower_query and "where id = %s" in lower_query:
            log_id = int(params[0]) if params else None
            self._row = next((row for row in self.plan.action_rows if int(row["id"]) == log_id), None)
            self.rowcount = 1 if self._row else 0
            return

        raise AssertionError(f"Unexpected SQL in test harness: {query}")

    def fetchone(self):
        if self._row is not None:
            return self._row
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        return None


class FakeConnection:
    def __init__(self, plan: QueryPlan):
        self.plan = plan

    def cursor(self, dictionary=False):
        return FakeCursor(self.plan)

    def commit(self):
        return None

    def close(self):
        return None

    def is_connected(self):
        return True


def install_replay_harness(monkeypatch, app_module, db_module, plan: QueryPlan):
    monkeypatch.setattr(app_module, "verify_jwt_in_request", lambda *args, **kwargs: None)
    monkeypatch.setattr(app_module, "get_jwt_identity", lambda: "admin-user-1")
    monkeypatch.setattr(app_module, "get_jwt", lambda: {"role": "admin"})
    monkeypatch.setattr(app_module, "get_db_connection", lambda: FakeConnection(plan))
    monkeypatch.setattr(db_module, "get_db_connection", lambda: FakeConnection(plan))
    monkeypatch.setattr(db_module, "save_ai_action_log", lambda **kwargs: plan.saved_audits.append(kwargs) or 1)

    def emit_socket_event(event_name, payload):
        if plan.socket_raise is not None:
            raise plan.socket_raise
        plan.emitted_events.append((event_name, payload))
        return True

    monkeypatch.setattr(app_module.socketio, "emit", emit_socket_event)


def assert_timeline_is_ordered(timeline: list[dict[str, Any]], expected_actions: list[str]):
    assert [entry["action"] for entry in timeline] == expected_actions
    assert [entry["step"] for entry in timeline] == list(range(1, len(timeline) + 1))
    assert all("duration_ms" in entry for entry in timeline)
    assert all("execution_status" not in entry or entry["execution_status"] for entry in timeline)

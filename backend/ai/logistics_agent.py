"""Logistics AI agent orchestration.

This module keeps the planning layer separate from the Flask route layer.
The Flask app injects concrete tool handlers so the agent can execute real
backend operations without importing the application module and creating
cycles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import time
from typing import Any, Callable, Dict, Iterable, Mapping, Optional


ToolHandler = Callable[[Dict[str, Any]], Dict[str, Any]]
IntentResult = Mapping[str, Any]


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_upper(value: Any) -> str:
    return _normalize_text(value).upper()


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _tail(values: Iterable[Any], limit: int = 12) -> list[Any]:
    items = list(values or [])
    if len(items) <= limit:
        return items
    return items[-limit:]


# Optional DB persistence helpers. Import lazily to avoid circular imports during startup.
try:
    from db import save_ai_action_log, save_ai_tool_metric, save_workflow_memory, save_workflow_retry_job  # type: ignore
except Exception:
    save_ai_action_log = None
    save_ai_tool_metric = None
    save_workflow_memory = None
    save_workflow_retry_job = None


@dataclass(slots=True)
class LogisticsAgentResponse:
    message: str
    action: str
    card_type: str
    data: Dict[str, Any] = field(default_factory=dict)
    status: str = "success"
    http_status: int = 200
    confidence: float = 0.0
    intent: str = "UNRELATED"
    workflow: str = "fallback"
    route: str = ""
    suggestions: list[str] = field(default_factory=list)
    handled: bool = True
    error: str = ""

    def as_dict(self) -> Dict[str, Any]:
        payload = {
            "status": self.status,
            "message": self.message,
            "reply": self.message,
            "action": self.action,
            "card_type": self.card_type,
            "type": self.card_type,
            "data": self.data,
            "confidence": round(float(self.confidence or 0.0), 2),
            "intent": self.intent,
            "workflow": self.workflow,
            "route": self.route,
            "suggestions": self.suggestions,
            "handled": self.handled,
        }

        if self.error:
            payload["error"] = self.error

        return payload


class LogisticsAgent:
    """Plan and execute logistics actions from chat messages."""

    ROLE_INTENTS = {
        "customer": {"BOOK_SHIPMENT", "TRACK_SHIPMENT", "GET_PRICE_ESTIMATE", "MAKE_PAYMENT", "CUSTOMER_SUPPORT"},
        "admin": {"BOOK_SHIPMENT", "TRACK_SHIPMENT", "GET_PRICE_ESTIMATE", "MAKE_PAYMENT", "GET_ANALYTICS", "DRIVER_UPDATE", "CUSTOMER_SUPPORT", "RECONCILE_PAYMENT", "GENERATE_INVOICE", "DELIVERY_CONFIRMATION", "DELAY_MANAGEMENT", "FAILED_SHIPMENT", "CUSTOMER_NOTIFICATION"},
        "driver": {"TRACK_SHIPMENT", "DRIVER_UPDATE", "CUSTOMER_SUPPORT", "DELIVERY_CONFIRMATION", "DELAY_MANAGEMENT", "CUSTOMER_NOTIFICATION"},
    }

    INTENT_TO_TOOL = {
        "BOOK_SHIPMENT": "create_shipment",
        "TRACK_SHIPMENT": "track_shipment",
        "GET_PRICE_ESTIMATE": "calculate_eta",
        "MAKE_PAYMENT": "create_payment",
        "RECONCILE_PAYMENT": "reconcile_payment",
        "GENERATE_INVOICE": "generate_invoice",
        "DELIVERY_CONFIRMATION": "confirm_delivery",
        "DELAY_MANAGEMENT": "manage_delay",
        "FAILED_SHIPMENT": "handle_failed_shipment",
        "CUSTOMER_NOTIFICATION": "notify_customer",
        "GET_ANALYTICS": "get_analytics",
        "DRIVER_UPDATE": "update_shipment_status",
        "CUSTOMER_SUPPORT": "customer_support",
    }

    CARD_TYPES = {
        "create_shipment": "booking_summary_card",
        "track_shipment": "tracking_card",
        "calculate_eta": "eta_status_card",
        "assign_driver": "driver_assignment_card",
        "create_payment": "payment_card",
        "reconcile_payment": "payment_card",
        "generate_invoice": "invoice_card",
        "confirm_delivery": "delivery_confirmation_card",
        "manage_delay": "delay_alert_card",
        "handle_failed_shipment": "tracking_card",
        "notify_customer": "text_reply",
        "get_analytics": "analytics_card",
        "update_shipment_status": "tracking_card",
        "customer_support": "text_reply",
    }

    def __init__(
        self,
        *,
        tool_registry: Mapping[str, ToolHandler],
        intent_resolver: Callable[[str, str, Optional[Dict[str, Any]], Optional[Dict[str, Any]]], Dict[str, Any]],
        role_policy_resolver: Callable[[str], Dict[str, Any]],
        logger: Any,
    ) -> None:
        self.tool_registry = dict(tool_registry)
        self.intent_resolver = intent_resolver
        self.role_policy_resolver = role_policy_resolver
        self.logger = logger

    def handle(
        self,
        message: str,
        user_state: Optional[Dict[str, Any]],
        auth_context: Optional[Dict[str, Any]],
        db_context: Optional[Dict[str, Any]] = None,
        intent_result: Optional[IntentResult] = None,
    ) -> LogisticsAgentResponse:
        user_state = user_state or {}
        auth_context = auth_context or {}
        db_context = db_context or {}

        role = _normalize_text((auth_context or {}).get("role") or db_context.get("role") or "customer").lower() or "customer"
        role_policy = self.role_policy_resolver(role)
        intent_data = dict(intent_result or self.intent_resolver(message, user_state.get("stage", "idle"), user_state.get("booking"), db_context) or {})

        intent = _normalize_upper(intent_data.get("intent") or "UNRELATED")
        confidence = _coerce_float(intent_data.get("confidence"), 0.0)
        intent_data["intent"] = intent
        intent_data["confidence"] = confidence

        if not self._role_allows(role_policy, intent):
            return self._record_response(
                user_state,
                LogisticsAgentResponse(
                    message=self._forbidden_message(intent, role_policy),
                    action=intent,
                    card_type="text_reply",
                    data={"role": role, "allowed_intents": sorted(role_policy.get("allowed_intents", []))},
                    status="forbidden",
                    http_status=403,
                    confidence=confidence,
                    intent=intent,
                    workflow=self._workflow_for_intent(intent),
                    route=f"{self._workflow_for_intent(intent)}.access_restricted",
                    suggestions=list(role_policy.get("quick_actions", []))[:4],
                ),
                tool_name="access_control",
                intent_data=intent_data,
                role=role,
                db_context=db_context,
            )

        tool_name = self._select_tool(intent, message, user_state, role)
        if tool_name is None:
            return self._record_response(
                user_state,
                LogisticsAgentResponse(
                    message=intent_data.get("reply") or "Tell me what shipment action you want me to take.",
                    action=intent,
                    card_type="text_reply",
                    data={"role": role},
                    status="needs_input",
                    http_status=200,
                    confidence=confidence,
                    intent=intent,
                    workflow=self._workflow_for_intent(intent),
                    route="assistant.needs_input",
                    suggestions=list(role_policy.get("quick_actions", []))[:4],
                ),
                tool_name="no_action",
                intent_data=intent_data,
                role=role,
                db_context=db_context,
            )

        handler = self.tool_registry.get(tool_name)
        if handler is None:
            return self._record_response(
                user_state,
                LogisticsAgentResponse(
                    message="This logistics action is not wired yet.",
                    action=intent,
                    card_type="text_reply",
                    data={"tool": tool_name},
                    status="error",
                    http_status=500,
                    confidence=confidence,
                    intent=intent,
                    workflow=self._workflow_for_intent(intent),
                    route="assistant.missing_tool",
                    suggestions=list(role_policy.get("quick_actions", []))[:4],
                    handled=False,
                    error="missing_tool_handler",
                ),
                tool_name=tool_name,
                intent_data=intent_data,
                role=role,
                db_context=db_context,
            )

        request_context = {
            "message": message,
            "user_state": user_state,
            "auth_context": auth_context,
            "db_context": db_context,
            "intent": intent,
            "intent_result": intent_data,
            "role": role,
            "role_policy": role_policy,
            "tool_name": tool_name,
        }

        try:
            tool_started_at = datetime.utcnow()
            tool_start_perf = time.perf_counter()
            tool_payload = handler(request_context) or {}
            tool_completed_at = datetime.utcnow()
            tool_duration_ms = int((time.perf_counter() - tool_start_perf) * 1000)
            normalized = self._normalize_tool_payload(
                tool_payload,
                tool_name=tool_name,
                intent=intent,
                confidence=confidence,
                role_policy=role_policy,
                intent_data=intent_data,
            )
            normalized["started_at"] = tool_started_at.isoformat(sep=" ", timespec="seconds")
            normalized["completed_at"] = tool_completed_at.isoformat(sep=" ", timespec="seconds")
            normalized["duration_ms"] = tool_duration_ms
            return self._record_response(
                user_state,
                LogisticsAgentResponse(
                    message=normalized["message"],
                    action=normalized["action"],
                    card_type=normalized["card_type"],
                    data=normalized["data"],
                    status=normalized["status"],
                    http_status=normalized["http_status"],
                    confidence=normalized["confidence"],
                    intent=normalized["intent"],
                    workflow=normalized["workflow"],
                    route=normalized["route"],
                    suggestions=normalized["suggestions"],
                    handled=normalized["handled"],
                    error=normalized.get("error", ""),
                ),
                tool_name=tool_name,
                intent_data=intent_data,
                role=role,
                db_context=db_context,
                execution_timing={
                    "started_at": tool_started_at,
                    "completed_at": tool_completed_at,
                    "duration_ms": tool_duration_ms,
                    "retry_count": 0,
                    "execution_status": normalized.get("status", "success"),
                },
            )
        except Exception as error:  # pragma: no cover - defensive fallback
            if hasattr(self.logger, "exception"):
                self.logger.exception(f"[ai][agent][error] tool={tool_name} intent={intent} error={error}")

            recovery_handler = self.tool_registry.get("customer_support")
            recovery_payload = {
                "message": "I hit a temporary logistics error. Please retry with the shipment ID, booking reference, or driver ID.",
                "action": intent,
                "card_type": "text_reply",
                "data": {"tool": tool_name, "error": str(error)},
                "status": "error",
                "http_status": 500,
                "confidence": confidence,
                "intent": intent,
                "workflow": self._workflow_for_intent(intent),
                "route": "assistant.recovery",
                "suggestions": list(role_policy.get("quick_actions", []))[:4],
                "handled": True,
                "error": str(error),
            }

            if recovery_handler is not None and tool_name != "customer_support":
                try:
                    recovery_result = recovery_handler({**request_context, "tool_name": "customer_support", "error": str(error)}) or {}
                    recovery_payload.update(
                        self._normalize_tool_payload(
                            recovery_result,
                            tool_name="customer_support",
                            intent=intent,
                            confidence=confidence,
                            role_policy=role_policy,
                            intent_data=intent_data,
                        )
                    )
                    recovery_payload["status"] = recovery_payload.get("status") or "error"
                    recovery_payload["error"] = str(error)
                except Exception:
                    pass

            return self._record_response(
                user_state,
                LogisticsAgentResponse(
                    message=_normalize_text(recovery_payload.get("message") or recovery_payload.get("reply") or "Something went wrong."),
                    action=_normalize_upper(recovery_payload.get("action") or intent),
                    card_type=_normalize_text(recovery_payload.get("card_type") or recovery_payload.get("type") or "text_reply") or "text_reply",
                    data=dict(recovery_payload.get("data") or {}),
                    status=_normalize_text(recovery_payload.get("status") or "error") or "error",
                    http_status=int(recovery_payload.get("http_status") or 500),
                    confidence=confidence,
                    intent=intent,
                    workflow=self._workflow_for_intent(intent),
                    route=_normalize_text(recovery_payload.get("route") or "assistant.recovery") or "assistant.recovery",
                    suggestions=list(recovery_payload.get("suggestions") or list(role_policy.get("quick_actions", []))[:4]),
                    handled=True,
                    error=str(error),
                ),
                tool_name=tool_name,
                intent_data=intent_data,
                role=role,
                db_context=db_context,
                execution_timing={
                    "started_at": datetime.utcnow(),
                    "completed_at": datetime.utcnow(),
                    "duration_ms": 0,
                    "retry_count": 0,
                    "execution_status": "error",
                },
            )

    def _role_allows(self, role_policy: Mapping[str, Any], intent: str) -> bool:
        allowed = {str(item or "").strip().upper() for item in role_policy.get("allowed_intents", [])}
        return intent in allowed or intent == "UNRELATED"

    def _select_tool(self, intent: str, message: str, user_state: Dict[str, Any], role: str) -> Optional[str]:
        normalized_message = _normalize_text(message).lower()

        if intent in {"RECONCILE_PAYMENT", "GENERATE_INVOICE", "DELIVERY_CONFIRMATION", "DELAY_MANAGEMENT", "FAILED_SHIPMENT", "CUSTOMER_NOTIFICATION"}:
            return self.INTENT_TO_TOOL.get(intent)

        if intent == "MAKE_PAYMENT" and any(keyword in normalized_message for keyword in {"invoice", "receipt", "bill"}):
            return "generate_invoice"

        if intent == "MAKE_PAYMENT" and any(keyword in normalized_message for keyword in {"reconcile", "reconciliation", "settle", "settlement", "verify"}):
            return "reconcile_payment"

        if intent == "DRIVER_UPDATE":
            if any(keyword in normalized_message for keyword in {"assign", "dispatch", "allocate", "allocate driver"}):
                return "assign_driver"
            if any(keyword in normalized_message for keyword in {"deliver", "delivered", "pod", "proof of delivery"}):
                return "confirm_delivery"
            if any(keyword in normalized_message for keyword in {"delay", "delayed", "late", "stuck", "held"}):
                return "manage_delay"
            if any(keyword in normalized_message for keyword in {"failed", "cancel", "cancelled", "unable", "rejected"}):
                return "handle_failed_shipment"
            if any(keyword in normalized_message for keyword in {"notify customer", "customer notification", "send update"}):
                return "notify_customer"
            return "update_shipment_status"

        if intent == "TRACK_SHIPMENT":
            if any(keyword in normalized_message for keyword in {"eta", "arrival", "time left", "when"}):
                return "calculate_eta"
            return "track_shipment"

        if intent == "GET_PRICE_ESTIMATE":
            return "calculate_eta"

        if intent == "BOOK_SHIPMENT":
            if self._has_shipment_details(user_state, normalized_message):
                return "create_shipment"
            return "calculate_eta"

        if intent == "GET_ANALYTICS":
            return "get_analytics"

        if intent == "CUSTOMER_SUPPORT":
            return "customer_support"

        if intent == "MAKE_PAYMENT":
            return "create_payment"

        return None

    def _has_shipment_details(self, user_state: Dict[str, Any], normalized_message: str) -> bool:
        booking = user_state.get("booking") if isinstance(user_state.get("booking"), dict) else {}
        fields = [
            booking.get("source"),
            booking.get("destination"),
            booking.get("tons"),
            booking.get("truck_type"),
            booking.get("pickup_location"),
            booking.get("drop_location"),
        ]
        if sum(1 for value in fields if _normalize_text(value)) >= 3:
            return True

        return any(keyword in normalized_message for keyword in {"from", "to", "tons", "weight", "pickup", "drop"})

    def _workflow_for_intent(self, intent: str) -> str:
        return {
            "BOOK_SHIPMENT": "booking",
            "TRACK_SHIPMENT": "tracking",
            "GET_PRICE_ESTIMATE": "pricing",
            "MAKE_PAYMENT": "payments",
            "RECONCILE_PAYMENT": "payments",
            "GENERATE_INVOICE": "payments",
            "DELIVERY_CONFIRMATION": "delivery",
            "DELAY_MANAGEMENT": "delivery",
            "FAILED_SHIPMENT": "delivery",
            "CUSTOMER_NOTIFICATION": "notifications",
            "GET_ANALYTICS": "analytics",
            "DRIVER_UPDATE": "driver_management",
            "CUSTOMER_SUPPORT": "support",
        }.get(intent, "fallback")

    def _forbidden_message(self, intent: str, role_policy: Mapping[str, Any]) -> str:
        focus = _normalize_text(role_policy.get("assistant_focus") or "logistics operations")
        return f"This action is restricted for your role. I can still help with {focus}."

    def _default_card_type(self, tool_name: str) -> str:
        return self.CARD_TYPES.get(tool_name, "text_reply")

    def _normalize_tool_payload(
        self,
        payload: Mapping[str, Any],
        *,
        tool_name: str,
        intent: str,
        confidence: float,
        role_policy: Mapping[str, Any],
        intent_data: Mapping[str, Any],
    ) -> Dict[str, Any]:
        data = dict(payload or {})
        message = _normalize_text(
            data.get("message")
            or data.get("reply")
            or intent_data.get("reply")
            or ""
        )
        suggestions = data.get("suggestions")
        if not isinstance(suggestions, list):
            suggestions = list(role_policy.get("quick_actions", []))[:4]

        normalized = {
            "status": _normalize_text(data.get("status") or "success") or "success",
            "message": message,
            "reply": message,
            "action": _normalize_upper(data.get("action") or intent or tool_name),
            "card_type": _normalize_text(data.get("card_type") or data.get("type") or self._default_card_type(tool_name)) or self._default_card_type(tool_name),
            "data": dict(data.get("data") or data.get("payload") or {}),
            "confidence": _coerce_float(data.get("confidence"), confidence),
            "intent": _normalize_upper(data.get("intent") or intent),
            "workflow": _normalize_text(data.get("workflow") or self._workflow_for_intent(intent)) or self._workflow_for_intent(intent),
            "route": _normalize_text(data.get("route") or f"{self._workflow_for_intent(intent)}.{tool_name}"),
            "suggestions": suggestions,
            "http_status": int(data.get("http_status") or 200),
            "handled": bool(data.get("handled", True)),
        }

        if "error" in data:
            normalized["error"] = str(data.get("error") or "")

        return normalized

    def _record_response(
        self,
        user_state: Dict[str, Any],
        response: LogisticsAgentResponse,
        *,
        tool_name: str,
        intent_data: Mapping[str, Any],
        role: str,
        db_context: Mapping[str, Any],
        execution_timing: Optional[Mapping[str, Any]] = None,
    ) -> LogisticsAgentResponse:
        self._append_memory(
            user_state,
            {
                "tool": tool_name,
                "intent": response.intent,
                "action": response.action,
                "card_type": response.card_type,
                "status": response.status,
                "role": role,
                "message": response.message,
                "confidence": round(float(response.confidence or 0.0), 2),
                "route": response.route,
                "timestamp": db_context.get("timestamp") or None,
                "error": response.error,
            },
        )

        if hasattr(self.logger, "info"):
            self.logger.info(
                "[ai][agent] tool=%s intent=%s action=%s status=%s role=%s",
                tool_name,
                response.intent,
                response.action,
                response.status,
                role,
            )

        # Persist action log to the database if helper is available
        try:
            if save_ai_action_log is not None:
                user_id = str(user_state.get("user_id") or db_context.get("user_id") or "").strip() or None
                shipment_id = None
                try:
                    shipment_id = int(response.data.get("shipment", {}).get("id") or response.data.get("shipment_id") or None)
                except Exception:
                    shipment_id = None

                request_payload = dict(intent_data or {})
                # include original message if present in db_context
                if db_context and db_context.get("message"):
                    request_payload["message"] = db_context.get("message")

                save_ai_action_log(
                    user_id=user_id,
                    role=role,
                    action=response.action,
                    intent=response.intent,
                    tool_name=tool_name,
                    shipment_id=shipment_id,
                    status=response.status,
                    request_payload=request_payload,
                    response_payload=response.as_dict(),
                    error_message=response.error or None,
                    started_at=(execution_timing or {}).get("started_at"),
                    completed_at=(execution_timing or {}).get("completed_at"),
                    duration_ms=(execution_timing or {}).get("duration_ms"),
                    retry_count=(execution_timing or {}).get("retry_count", 0),
                    execution_status=(execution_timing or {}).get("execution_status") or response.status,
                )

            if save_ai_tool_metric is not None:
                save_ai_tool_metric(
                    user_id=user_id,
                    role=role,
                    workflow_type=response.workflow,
                    action=response.action,
                    tool_name=tool_name,
                    duration_ms=(execution_timing or {}).get("duration_ms"),
                    retry_count=(execution_timing or {}).get("retry_count", 0),
                    execution_status=(execution_timing or {}).get("execution_status") or response.status,
                    ai_latency_ms=(execution_timing or {}).get("duration_ms"),
                    websocket_latency_ms=db_context.get("websocket_latency_ms") or 0,
                    correlation_id=db_context.get("correlation_id"),
                    execution_id=db_context.get("execution_id"),
                    metadata_json={
                        "intent": response.intent,
                        "route": response.route,
                        "status": response.status,
                        "handled": response.handled,
                    },
                )
        except Exception:
            if hasattr(self.logger, "exception"):
                self.logger.exception("[ai][agent][persistence] failed to save ai_action_log")

        # Queue critical workflow failures for admin retry controls.
        try:
            if save_workflow_retry_job is not None and str(response.status or "").lower() in {"error", "failed", "timeout"}:
                if response.intent in {"BOOK_SHIPMENT", "MAKE_PAYMENT", "RECONCILE_PAYMENT", "GENERATE_INVOICE", "DELIVERY_CONFIRMATION", "DELAY_MANAGEMENT", "FAILED_SHIPMENT", "DRIVER_UPDATE"}:
                    save_workflow_retry_job(
                        user_id=str(user_state.get("user_id") or db_context.get("user_id") or "").strip() or None,
                        workflow_type=response.workflow or self._workflow_for_intent(response.intent),
                        action=response.action or response.intent,
                        route=response.route or f"{response.workflow}.recovery",
                        tool_name=tool_name,
                        payload_json={
                            "intent": response.intent,
                            "message": (db_context or {}).get("message"),
                            "data": response.data,
                            "context": {"role": role},
                        },
                        error_message=response.error or response.message,
                        execution_status=str((execution_timing or {}).get("execution_status") or response.status or "error"),
                        retry_count=int((execution_timing or {}).get("retry_count") or 0),
                        max_retries=3,
                        next_retry_at=None,
                        status="queued",
                    )
        except Exception:
            if hasattr(self.logger, "exception"):
                self.logger.exception("[ai][agent][retry_queue] failed to queue workflow retry")

        return response

    def _append_memory(self, user_state: Dict[str, Any], event: Dict[str, Any]) -> None:
        action_log = list(user_state.get("ai_action_log") or [])
        action_log.append(event)
        user_state["ai_action_log"] = _tail(action_log, 15)
        user_state["ai_last_action"] = event.get("action") or event.get("tool") or ""
        user_state["ai_last_tool"] = event.get("tool") or ""
        user_state["ai_last_card_type"] = event.get("card_type") or ""
        user_state["ai_last_intent"] = event.get("intent") or ""
        user_state["ai_last_error"] = event.get("error") or ""
        memory_window = list(user_state.get("ai_workflow_memory") or [])
        memory_window.append(event)
        user_state["ai_workflow_memory"] = _tail(memory_window, 8)

        # Persist workflow memory snapshot for replay/debugging when DB helper is available
        try:
            if save_workflow_memory is not None:
                user_id = str(user_state.get("user_id") or "").strip() or None
                workflow_type = str(event.get("workflow") or event.get("route") or "").strip() or "general"
                workflow_state = list(user_state.get("ai_workflow_memory") or [])
                current_step = str(user_state.get("stage") or user_state.get("current_step") or "").strip()
                context_json = {k: v for k, v in user_state.items() if k not in ("ai_action_log", "ai_workflow_memory")}
                save_workflow_memory(user_id=user_id, workflow_type=workflow_type, workflow_state=workflow_state, current_step=current_step, context_json=context_json)
        except Exception:
            if hasattr(self.logger, "exception"):
                self.logger.exception("[ai][agent][persistence] failed to save workflow memory")


__all__ = ["LogisticsAgent", "LogisticsAgentResponse"]
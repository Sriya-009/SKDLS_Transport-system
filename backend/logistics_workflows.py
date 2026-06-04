"""Enterprise logistics workflow orchestration.

This service layers business workflows over the existing shipment, booking,
payment, and notification helpers so the chatbot can trigger real operational
changes without duplicating persistence logic.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional


class LogisticsWorkflowService:
    def __init__(self, **dependencies: Callable[..., Any]) -> None:
        self.dependencies = dependencies

    def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        handler = self.dependencies.get(name)
        if handler is None:
            raise RuntimeError(f"Missing workflow dependency: {name}")
        return handler(*args, **kwargs)

    def _maybe_call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        handler = self.dependencies.get(name)
        if handler is None:
            return None
        return handler(*args, **kwargs)

    def _serialize_shipment(self, shipment_row: Optional[Mapping[str, Any]]) -> dict[str, Any]:
        serializer = self.dependencies.get("serialize_shipment_row")
        if shipment_row and serializer is not None:
            return dict(serializer(shipment_row) or {})
        return dict(shipment_row or {})

    def _serialize_booking(self, booking_row: Optional[Mapping[str, Any]]) -> dict[str, Any]:
        serializer = self.dependencies.get("serialize_booking_row")
        if booking_row and serializer is not None:
            return dict(serializer(booking_row) or {})
        return dict(booking_row or {})

    def _build_message(self, title: str, body: str) -> str:
        title_text = str(title or "").strip()
        body_text = str(body or "").strip()
        if title_text and body_text:
            return f"{title_text}\n{body_text}"
        return title_text or body_text

    def _notify(self, *, title: str, message: str, level: str = "info", data: Optional[dict[str, Any]] = None) -> None:
        self._maybe_call("broadcast_notification", title, message, level=level, data=data or {})

    def _broadcast_activity(self, domain: str, status: str, payload: Optional[dict[str, Any]], event_name: str) -> None:
        self._maybe_call("broadcast_activity", domain, status, payload or {}, event_name=event_name)

    def _broadcast_tracking(self, shipment_row: Optional[Mapping[str, Any]], source: str, status_note: str) -> None:
        self._maybe_call("broadcast_tracking_snapshot", shipment_row=shipment_row, source=source, event_name="tracking:update", status_note=status_note)

    def _log_event(self, shipment_id: Optional[int], event_type: str, *, title: str = "", message: str = "", severity: str = "info", source: str = "system", metadata: Optional[dict[str, Any]] = None) -> None:
        if shipment_id is None:
            return

        self._maybe_call(
            "insert_shipment_event_log",
            int(shipment_id),
            event_type,
            title=title,
            message=message,
            severity=severity,
            source=source,
            metadata=metadata or {},
        )

    def start_shipment_lifecycle(
        self,
        *,
        shipment_id: int,
        actor_role: str = "system",
        note: str = "Shipment created",
        location: str = "",
        session_id: str = "",
        assigned_driver: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        shipment_row = self._call("fetch_shipment_record_by_id", shipment_id)
        if not shipment_row:
            raise RuntimeError(f"Shipment {shipment_id} not found")

        shipment = self._serialize_shipment(shipment_row)
        if assigned_driver:
            shipment["assigned_driver_id"] = assigned_driver.get("id")

        self._call("insert_shipment_status_log", shipment_id, "pending", note=note, location=location, actor_role=actor_role, metadata={"session_id": session_id, "assigned_driver": dict(assigned_driver or {})})
        self._log_event(
            shipment_id,
            "shipment_created",
            title="Shipment created",
            message=note,
            severity="info",
            source="workflow.shipment_lifecycle",
            metadata={"session_id": session_id, "assigned_driver": dict(assigned_driver or {})},
        )
        self._notify(
            title="Shipment lifecycle started",
            message=self._build_message(f"Shipment #{shipment_id} is now active.", note),
            level="success",
            data={"shipment": shipment, "assigned_driver": dict(assigned_driver or {})},
        )

        if session_id:
            self._maybe_call(
                "booking_workflow_manager_save",
                session_id=session_id,
                user_id=str(shipment.get("user_id") or "default_user"),
                workflow="shipment_lifecycle",
                current_step="pending",
                draft_json=shipment,
                shipment_id=shipment_id,
                status="active",
                last_user_message="shipment_created",
                last_bot_reply="Shipment lifecycle started",
            )

        return {
            "shipment": shipment,
            "assigned_driver": dict(assigned_driver or {}) or None,
            "notification": True,
            "workflow": "shipment_lifecycle",
            "route": "workflows.shipment_lifecycle.started",
        }

    def dispatch_driver(
        self,
        *,
        shipment_id: int,
        driver: Optional[Mapping[str, Any]] = None,
        actor_role: str = "admin",
        note: str = "Driver dispatched",
        location: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        shipment_row = self._call("fetch_shipment_record_by_id", shipment_id)
        if not shipment_row:
            raise RuntimeError(f"Shipment {shipment_id} not found")

        shipment = self._serialize_shipment(shipment_row)
        updates: dict[str, Any] = {"shipment_status": "assigned"}
        if driver and driver.get("id") is not None:
            updates["assigned_driver_id"] = int(driver.get("id"))
            self._maybe_call("update_driver_record", int(driver.get("id")), {"status": "on_trip"})

        self._call("update_shipment_record", shipment_id, updates)
        self._call(
            "insert_shipment_status_log",
            shipment_id,
            "assigned",
            note=note,
            location=location or str(shipment.get("pickup_location") or ""),
            actor_role=actor_role,
            metadata={"driver": dict(driver or {})},
        )
        self._log_event(
            shipment_id,
            "driver_assigned",
            title="Driver assigned",
            message=note,
            severity="success",
            source="workflow.dispatch_driver",
            metadata={"driver": dict(driver or {})},
        )

        updated_row = self._call("fetch_shipment_record_by_id", shipment_id)
        self._notify(
            title="Driver dispatched",
            message=self._build_message(f"Shipment #{shipment_id} now has an assigned driver.", note),
            level="success",
            data={"shipment": self._serialize_shipment(updated_row), "driver": dict(driver or {})},
        )
        self._broadcast_tracking(updated_row, "dispatch.driver_assigned", "assigned")

        if session_id:
            self._maybe_call(
                "booking_workflow_manager_save",
                session_id=session_id,
                user_id=str(shipment.get("user_id") or "default_user"),
                workflow="driver_dispatch",
                current_step="assigned",
                draft_json=self._serialize_shipment(updated_row),
                shipment_id=shipment_id,
                status="active",
                last_user_message="driver_dispatched",
                last_bot_reply="Driver dispatched",
            )

        return {
            "shipment": self._serialize_shipment(updated_row),
            "driver": dict(driver or {}) or None,
            "workflow": "driver_dispatch",
            "route": "workflows.driver_dispatch.assigned",
            "notification": True,
        }

    def confirm_delivery(
        self,
        *,
        shipment_id: int,
        actor_role: str = "driver",
        note: str = "Delivery confirmed",
        location: str = "",
        proof: Optional[Mapping[str, Any]] = None,
        session_id: str = "",
    ) -> dict[str, Any]:
        shipment_row = self._call("fetch_shipment_record_by_id", shipment_id)
        if not shipment_row:
            raise RuntimeError(f"Shipment {shipment_id} not found")

        self._call("update_shipment_record", shipment_id, {"shipment_status": "delivered", "payment_status": str(shipment_row.get("payment_status") or "pending")})
        self._call(
            "insert_shipment_status_log",
            shipment_id,
            "delivered",
            note=note,
            location=location or str(shipment_row.get("drop_location") or ""),
            actor_role=actor_role,
            metadata={"proof": dict(proof or {})},
        )
        self._log_event(
            shipment_id,
            "delivery_confirmed",
            title="Delivery confirmed",
            message=note,
            severity="success",
            source="workflow.confirm_delivery",
            metadata={"proof": dict(proof or {})},
        )

        updated_row = self._call("fetch_shipment_record_by_id", shipment_id)
        self._notify(
            title="Delivery confirmed",
            message=self._build_message(f"Shipment #{shipment_id} was marked delivered.", note),
            level="success",
            data={"shipment": self._serialize_shipment(updated_row), "proof": dict(proof or {})},
        )
        self._broadcast_tracking(updated_row, "delivery.confirmation", "delivered")

        if session_id:
            self._maybe_call(
                "booking_workflow_manager_save",
                session_id=session_id,
                user_id=str((updated_row or shipment_row).get("user_id") or "default_user"),
                workflow="delivery_confirmation",
                current_step="delivered",
                draft_json=self._serialize_shipment(updated_row),
                shipment_id=shipment_id,
                status="confirmed",
                last_user_message="delivery_confirmed",
                last_bot_reply="Delivery confirmed",
            )

        return {
            "shipment": self._serialize_shipment(updated_row),
            "workflow": "delivery_confirmation",
            "route": "workflows.delivery_confirmation.completed",
            "notification": True,
        }

    def manage_delay(
        self,
        *,
        shipment_id: int,
        delay_minutes: int,
        reason: str = "",
        actor_role: str = "driver",
        location: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        shipment_row = self._call("fetch_shipment_record_by_id", shipment_id)
        if not shipment_row:
            raise RuntimeError(f"Shipment {shipment_id} not found")

        self._call("update_shipment_record", shipment_id, {"shipment_status": "delayed"})
        self._call(
            "insert_shipment_status_log",
            shipment_id,
            "delayed",
            note=reason or f"Shipment delayed by {delay_minutes} minutes",
            location=location or str(shipment_row.get("pickup_location") or ""),
            actor_role=actor_role,
            metadata={"delay_minutes": int(delay_minutes), "reason": reason},
        )
        self._log_event(
            shipment_id,
            "shipment_delayed",
            title="Delay recorded",
            message=reason or f"Shipment delayed by {delay_minutes} minutes",
            severity="warning",
            source="workflow.manage_delay",
            metadata={"delay_minutes": int(delay_minutes), "reason": reason},
        )

        updated_row = self._call("fetch_shipment_record_by_id", shipment_id)
        self._notify(
            title="Shipment delayed",
            message=self._build_message(f"Shipment #{shipment_id} is delayed by {delay_minutes} minutes.", reason or ""),
            level="warning",
            data={"shipment": self._serialize_shipment(updated_row), "delay_minutes": int(delay_minutes), "reason": reason},
        )
        self._broadcast_tracking(updated_row, "workflow.delay", "delayed")

        if session_id:
            self._maybe_call(
                "booking_workflow_manager_save",
                session_id=session_id,
                user_id=str((updated_row or shipment_row).get("user_id") or "default_user"),
                workflow="delay_management",
                current_step="delayed",
                draft_json=self._serialize_shipment(updated_row),
                shipment_id=shipment_id,
                status="active",
                last_user_message="shipment_delayed",
                last_bot_reply=f"Shipment delayed by {delay_minutes} minutes",
            )

        return {
            "shipment": self._serialize_shipment(updated_row),
            "workflow": "delay_management",
            "route": "workflows.delay_management.updated",
            "notification": True,
        }

    def handle_failed_shipment(
        self,
        *,
        shipment_id: int,
        reason: str,
        actor_role: str = "admin",
        location: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        shipment_row = self._call("fetch_shipment_record_by_id", shipment_id)
        if not shipment_row:
            raise RuntimeError(f"Shipment {shipment_id} not found")

        self._call("update_shipment_record", shipment_id, {"shipment_status": "failed"})
        self._call(
            "insert_shipment_status_log",
            shipment_id,
            "failed",
            note=reason or "Shipment failed",
            location=location or str(shipment_row.get("pickup_location") or ""),
            actor_role=actor_role,
            metadata={"reason": reason},
        )
        self._log_event(
            shipment_id,
            "shipment_failed",
            title="Shipment failed",
            message=reason or "Shipment failed",
            severity="critical",
            source="workflow.handle_failed_shipment",
            metadata={"reason": reason},
        )

        updated_row = self._call("fetch_shipment_record_by_id", shipment_id)
        self._notify(
            title="Shipment failed",
            message=self._build_message(f"Shipment #{shipment_id} was marked as failed.", reason or ""),
            level="critical",
            data={"shipment": self._serialize_shipment(updated_row), "reason": reason},
        )
        self._broadcast_tracking(updated_row, "workflow.failed", "failed")

        if session_id:
            self._maybe_call(
                "booking_workflow_manager_save",
                session_id=session_id,
                user_id=str((updated_row or shipment_row).get("user_id") or "default_user"),
                workflow="failed_shipment_handling",
                current_step="failed",
                draft_json=self._serialize_shipment(updated_row),
                shipment_id=shipment_id,
                status="failed",
                last_user_message="shipment_failed",
                last_bot_reply="Shipment marked failed",
            )

        return {
            "shipment": self._serialize_shipment(updated_row),
            "workflow": "failed_shipment_handling",
            "route": "workflows.failed_shipment.updated",
            "notification": True,
        }

    def generate_invoice(
        self,
        *,
        reference_type: str,
        reference_id: int,
    ) -> dict[str, Any]:
        reference = str(reference_type or "").strip().lower()
        if reference == "shipment":
            shipment_row = self._call("fetch_shipment_record_by_id", reference_id)
            if not shipment_row:
                raise RuntimeError(f"Shipment {reference_id} not found")
            invoice = self._call("build_shipment_invoice_payload", shipment_row, self._call("fetch_payment_records_for_shipment", reference_id))
            route = f"/api/shipments/{reference_id}/invoice"
            self._log_event(
                reference_id,
                "invoice_generated",
                title="Invoice generated",
                message=f"Shipment invoice {invoice.get('invoice_number')} generated.",
                severity="info",
                source="workflow.generate_invoice",
                metadata={"invoice": dict(invoice or {})},
            )
            return {
                "invoice": invoice,
                "shipment": self._serialize_shipment(shipment_row),
                "workflow": "invoice_generation",
                "route": "payments.shipment_invoice",
                "download_url": route,
            }

        booking_row = self._call("fetch_booking_record_by_id", reference_id)
        if not booking_row:
            raise RuntimeError(f"Booking {reference_id} not found")

        invoice = self._call("build_booking_invoice_payload", booking_row, self._call("fetch_payment_records_for_booking", reference_id))
        if booking_row.get("id") is not None:
            self._log_event(
                int(booking_row.get("id")),
                "invoice_generated",
                title="Invoice generated",
                message=f"Booking invoice {invoice.get('invoice_number')} generated.",
                severity="info",
                source="workflow.generate_invoice",
                metadata={"invoice": dict(invoice or {})},
            )
        return {
            "invoice": invoice,
            "booking": self._serialize_booking(booking_row),
            "workflow": "invoice_generation",
            "route": "payments.booking_invoice",
            "download_url": f"/bookings/{reference_id}/invoice",
        }

    def reconcile_payment(
        self,
        *,
        reference_type: str,
        reference_id: int,
        payment_type: str = "advance",
        session_id: str = "",
        actor_role: str = "system",
    ) -> dict[str, Any]:
        reference = str(reference_type or "").strip().lower()
        payment_type = str(payment_type or "advance").strip().lower()

        if reference == "shipment":
            shipment_row = self._call("fetch_shipment_record_by_id", reference_id)
            if not shipment_row:
                raise RuntimeError(f"Shipment {reference_id} not found")

            payment_rows = self._call("fetch_payment_records_for_shipment", reference_id)
            expected = int(self._call("resolve_shipment_payment_amount", shipment_row, payment_type, reference_id))
            paid = int(self._call("get_shipment_paid_amount", reference_id))
            outstanding = max(int(round(float(shipment_row.get("estimated_price") or 0))) - paid, 0)
            fully_paid = outstanding <= 0 and paid > 0

            if fully_paid:
                self._call("update_shipment_record", reference_id, {"payment_status": "paid", "shipment_status": str(shipment_row.get("shipment_status") or "confirmed")})
                self._call(
                    "insert_shipment_status_log",
                    reference_id,
                    "confirmed",
                    note="Payment reconciled and shipment confirmed",
                    location=str(shipment_row.get("pickup_location") or ""),
                    actor_role=actor_role,
                    metadata={"expected_amount": expected, "paid_amount": paid, "outstanding_amount": outstanding},
                )
                self._log_event(
                    reference_id,
                    "payment_reconciled",
                    title="Payment reconciled",
                    message=f"Shipment payment reconciled with outstanding ₹{outstanding:,}.",
                    severity="success",
                    source="workflow.reconcile_payment",
                    metadata={"expected_amount": expected, "paid_amount": paid, "outstanding_amount": outstanding},
                )

            updated_row = self._call("fetch_shipment_record_by_id", reference_id)
            return {
                "shipment": self._serialize_shipment(updated_row),
                "payments": [_ for _ in (self._call("fetch_payment_records_for_shipment", reference_id) or [])],
                "summary": {"expected_amount": expected, "paid_amount": paid, "outstanding_amount": outstanding, "fully_paid": fully_paid},
                "workflow": "payment_reconciliation",
                "route": "payments.shipment_reconciliation",
            }

        booking_row = self._call("fetch_booking_record_by_id", reference_id)
        if not booking_row:
            raise RuntimeError(f"Booking {reference_id} not found")

        payment_rows = self._call("fetch_payment_records_for_booking", reference_id)
        expected = int(self._call("resolve_booking_payment_amount", booking_row, payment_type, reference_id))
        paid = int(self._call("get_booking_paid_amount", reference_id))
        outstanding = max(int(round(float(booking_row.get("price") or 0))) - paid, 0)
        fully_paid = outstanding <= 0 and paid > 0

        if fully_paid:
            self._call("update_booking_payment_state", reference_id, "paid", booking_status="confirmed")
            self._call(
                "insert_booking_status_log",
                reference_id,
                "confirmed",
                note="Payment reconciled and booking confirmed",
                location=str(booking_row.get("pickup_location") or booking_row.get("source_location") or ""),
                actor_role=actor_role,
                metadata={"expected_amount": expected, "paid_amount": paid, "outstanding_amount": outstanding},
            )
            self._log_event(
                reference_id,
                "payment_reconciled",
                title="Payment reconciled",
                message=f"Booking payment reconciled with outstanding ₹{outstanding:,}.",
                severity="success",
                source="workflow.reconcile_payment",
                metadata={"expected_amount": expected, "paid_amount": paid, "outstanding_amount": outstanding},
            )

        updated_row = self._call("fetch_booking_record_by_id", reference_id)
        return {
            "booking": self._serialize_booking(updated_row),
            "payments": [_ for _ in (self._call("fetch_payment_records_for_booking", reference_id) or [])],
            "summary": {"expected_amount": expected, "paid_amount": paid, "outstanding_amount": outstanding, "fully_paid": fully_paid},
            "workflow": "payment_reconciliation",
            "route": "payments.booking_reconciliation",
        }

    def notify_customer(
        self,
        *,
        title: str,
        message: str,
        level: str = "info",
        booking_row: Optional[Mapping[str, Any]] = None,
        shipment_row: Optional[Mapping[str, Any]] = None,
        phone: str = "",
        session_id: str = "",
        workflow: str = "customer_notification",
        route: str = "notifications.customer",
    ) -> dict[str, Any]:
        payload = {
            "title": str(title or "").strip(),
            "message": str(message or "").strip(),
            "level": str(level or "info").strip() or "info",
            "booking": self._serialize_booking(booking_row),
            "shipment": self._serialize_shipment(shipment_row),
        }

        self._notify(title=payload["title"], message=payload["message"], level=payload["level"], data=payload)
        target_row = shipment_row or booking_row
        self._log_event(
            int((target_row or {}).get("id")) if target_row and target_row.get("id") is not None else None,
            "customer_notified",
            title=payload["title"],
            message=payload["message"],
            severity=payload["level"],
            source="workflow.notify_customer",
            metadata=payload,
        )

        whatsapp_sent = False
        phone_number = str(phone or (booking_row or {}).get("phone") or "").strip()
        whatsapp_enabled = self.dependencies.get("booking_whatsapp_opt_in")
        whatsapp_sender = self.dependencies.get("send_whatsapp_message")
        if phone_number and whatsapp_sender is not None:
            if booking_row is None or whatsapp_enabled is None or whatsapp_enabled(booking_row, "whatsapp_notifications_enabled"):
                try:
                    whatsapp_sender(phone_number, self._build_message(payload["title"], payload["message"]))
                    whatsapp_sent = True
                except Exception:
                    whatsapp_sent = False

        if session_id:
            self._maybe_call(
                "booking_workflow_manager_save",
                session_id=session_id,
                user_id=str((booking_row or shipment_row or {}).get("user_id") or "default_user"),
                workflow=workflow,
                current_step="notified",
                draft_json=self._serialize_booking(booking_row) or self._serialize_shipment(shipment_row),
                shipment_id=int((shipment_row or {}).get("id")) if shipment_row and shipment_row.get("id") is not None else None,
                status="active",
                last_user_message="customer_notification",
                last_bot_reply=payload["message"],
            )

        return {
            "notification": payload,
            "whatsapp_sent": whatsapp_sent,
            "workflow": workflow,
            "route": route,
        }

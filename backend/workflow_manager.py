import json
from threading import Lock

from db import get_booking_session, save_booking_session


class BookingWorkflowManager:
    def __init__(self):
        self._locks = {}
        self._registry_lock = Lock()

    def _get_lock(self, session_id):
        session_key = str(session_id or "").strip()
        with self._registry_lock:
            if session_key not in self._locks:
                self._locks[session_key] = Lock()
            return self._locks[session_key]

    def load(self, session_id):
        return get_booking_session(session_id)

    def save(self, session_id, user_id, workflow, current_step, draft_json=None, quote_json=None, payment_json=None, shipment_id=None, status="active", last_user_message="", last_bot_reply=""):
        save_booking_session(
            session_id=session_id,
            user_id=user_id,
            workflow=workflow,
            current_step=current_step,
            draft_json=draft_json,
            quote_json=quote_json,
            payment_json=payment_json,
            shipment_id=shipment_id,
            status=status,
            last_user_message=last_user_message,
            last_bot_reply=last_bot_reply,
        )

    def with_lock(self, session_id):
        return self._get_lock(session_id)

    def advance_stage(self, session_id, user_id, workflow, current_step, draft_json=None, quote_json=None, payment_json=None, shipment_id=None, status="active", last_user_message="", last_bot_reply=""):
        self.save(
            session_id=session_id,
            user_id=user_id,
            workflow=workflow,
            current_step=current_step,
            draft_json=draft_json,
            quote_json=quote_json,
            payment_json=payment_json,
            shipment_id=shipment_id,
            status=status,
            last_user_message=last_user_message,
            last_bot_reply=last_bot_reply,
        )

        return self.load(session_id)


booking_workflow_manager = BookingWorkflowManager()

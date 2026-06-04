import json
import os
from mysql.connector import connect, Error
from dotenv import load_dotenv
from logging_utils import logger, setup_logger, log_print

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
logger = setup_logger()
print = log_print


def _get_env(name, fallback=None):
    v = os.getenv(name)
    if v is None:
        return fallback
    return v.strip()


REQUIRED_DB_ENV_VARS = ("DB_HOST", "DB_USER", "DB_NAME")


def _get_int_env(name, fallback):
    try:
        return int(str(_get_env(name, fallback)).strip())
    except (TypeError, ValueError):
        return fallback


# Primary DB config uses only DB_* variables.
DB_CONFIG = {
    "host": _get_env("DB_HOST", "127.0.0.1"),
    "user": _get_env("DB_USER", "root"),
    "password": _get_env("DB_PASSWORD", ""),
    "database": _get_env("DB_NAME", "transport_system"),
    "port": _get_int_env("DB_PORT", 3306),
}


def validate_db_environment():
    """Raise a clear startup error when any required DB variable is missing."""
    missing = [
        name
        for name, value in (
            ("DB_HOST", DB_CONFIG.get("host")),
            ("DB_USER", DB_CONFIG.get("user")),
            ("DB_NAME", DB_CONFIG.get("database")),
        )
        if not value
    ]
    if missing:
        missing_list = ", ".join(missing)
        raise RuntimeError(
            f"Missing required database environment variables: {missing_list}. "
            "Set DB_HOST, DB_USER, DB_PASSWORD, DB_NAME, and DB_PORT before starting the backend."
        )

    return True


def get_db_connection():
    """Return a new MySQL connection using configured environment variables.

    Raises mysql.connector.Error on failure with additional context logged.
    """
    try:
        validate_db_environment()
        conn = connect(**DB_CONFIG)
        return conn
    except Error as e:
        msg = (
            f"Unable to connect to MySQL at {DB_CONFIG.get('host')} as {DB_CONFIG.get('user')}: {e}"
        )
        print(f"[db][error] {msg}")
        # Re-raise the original Error type so existing callers that catch Error keep working
        raise Error(msg) from e


def test_db_connection():
    """Quickly test the DB connection; returns True on success, False on failure."""
    try:
        conn = get_db_connection()
        conn.close()
        print("[db][info] DB connection test succeeded")
        return True
    except Error as e:
        print(f"[db][error] DB connection test failed: {e}")
        return False


def save_chat_message(session_id, user_message, bot_reply):
    """Save a chatbot interaction to `chat_history` table.

    Args:
        session_id (str): session or user identifier
        user_message (str): message sent by the user
        bot_reply (str): bot's reply text

    This function performs a safe commit/rollback and logs errors.
    """
    import traceback
    try:
        conn = get_db_connection()
        print(f"[chat_history][debug] connection.autocommit={conn.autocommit}")
        cursor = conn.cursor()
        try:
            payload = (session_id, user_message, bot_reply)
            print(f"[chat_history][payload] session_id={session_id!r} user_message_len={len(str(user_message))} bot_reply_len={len(str(bot_reply))}")
            print(f"[chat_history][payload_values] payload={payload}")
            cursor.execute(
                """
                INSERT INTO chat_history (session_id, user_message, bot_reply, created_at)
                VALUES (%s, %s, %s, NOW())
                """,
                payload,
            )
            print(f"[chat_history][debug] cursor.execute() completed")
            inserted_id = cursor.lastrowid
            print(f"[chat_history][inserted_id] row_id={inserted_id} session_id={session_id!r}")
            conn.commit()
            print(f"[chat_history][commit_success] session_id={session_id!r} row_id={inserted_id}")
        except Error as e:
            conn.rollback()
            print(f"[chat_history][error] Failed to save chat message: {e}")
            print(f"[chat_history][traceback] {traceback.format_exc()}")
            raise
        finally:
            cursor.close()
            conn.close()
    except Error as e:
        print(f"[chat_history][error] Could not open DB connection to save chat message: {e}")
        print(f"[chat_history][traceback] {traceback.format_exc()}")
        raise


def save_chat_history(session_id, user_message, bot_reply):
    """Compatibility wrapper for chat history persistence."""
    return save_chat_message(session_id, user_message, bot_reply)


def save_conversation_json(conversation_id, session_id, conversation_json):
    """Save the full conversation transcript as JSON."""
    import traceback

    def _normalize_messages(payload):
        if payload is None:
            return []

        if isinstance(payload, list):
            return payload

        if isinstance(payload, dict):
            return [payload]

        if isinstance(payload, bytes):
            try:
                payload = payload.decode("utf-8")
            except Exception:
                return []

        if isinstance(payload, str):
            text = payload.strip()
            if not text:
                return []
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                return []
            return _normalize_messages(parsed)

        return []

    def _append_without_duplicates(existing_messages, new_messages):
        existing_messages = _normalize_messages(existing_messages)
        new_messages = _normalize_messages(new_messages)

        if not existing_messages:
            return new_messages

        if not new_messages:
            return existing_messages

        max_overlap = min(len(existing_messages), len(new_messages))
        overlap = 0

        for size in range(max_overlap, 0, -1):
            if existing_messages[-size:] == new_messages[:size]:
                overlap = size
                break

        return existing_messages + new_messages[overlap:]

    try:
        conn = get_db_connection()
        print(f"[conversation][debug] connection.autocommit={conn.autocommit}")
        cursor = conn.cursor()
        try:
            existing_conversation_json = None
            if session_id:
                cursor.execute(
                    """
                    SELECT conversation_json
                    FROM conversations
                    WHERE session_id = %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (session_id,),
                )
                existing_row = cursor.fetchone()
                if existing_row:
                    existing_conversation_json = existing_row[0]

            merged_messages = _append_without_duplicates(existing_conversation_json, conversation_json)
            merged_conversation_json = json.dumps(merged_messages, ensure_ascii=False)
            payload = (conversation_id, session_id, merged_conversation_json)
            print(
                f"[conversation][saving] conversation_id={conversation_id!r} session_id={session_id!r} json_len={len(str(merged_conversation_json))}"
            )
            print(
                "[conversation][payload] INSERT INTO conversations (conversation_id, session_id, conversation_json, created_at) "
                f"VALUES (%s, %s, %s, NOW()) with params: {payload}"
            )
            cursor.execute(
                """
                INSERT INTO conversations (conversation_id, session_id, conversation_json, created_at)
                VALUES (%s, %s, %s, NOW())
                ON DUPLICATE KEY UPDATE
                    session_id = VALUES(session_id),
                    conversation_json = VALUES(conversation_json)
                """,
                payload,
            )
            print("[conversation][debug] cursor.execute() completed")
            inserted_id = cursor.lastrowid
            print(f"[conversation][inserted_id] row_id={inserted_id} conversation_id={conversation_id!r}")
            conn.commit()
            print(f"[conversation][saved] conversation_id={conversation_id!r} row_id={inserted_id}")
        except Error as e:
            conn.rollback()
            print(f"[conversation][error] Failed to save conversation JSON: {e}")
            print(f"[conversation][traceback] {traceback.format_exc()}")
            raise
        finally:
            cursor.close()
            conn.close()
    except Error as e:
        print(f"[conversation][error] Could not open DB connection to save conversation JSON: {e}")
        print(f"[conversation][traceback] {traceback.format_exc()}")
        raise


def _normalize_json_payload(payload):
    if payload in (None, "", b""):
        return {}

    if isinstance(payload, dict):
        return payload

    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except Exception:
            return {}

    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return {}

        try:
            parsed = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

        return parsed if isinstance(parsed, dict) else {}

    return {}


def save_booking_session(
    session_id,
    user_id,
    workflow,
    current_step,
    draft_json=None,
    quote_json=None,
    payment_json=None,
    shipment_id=None,
    status="active",
    last_user_message="",
    last_bot_reply="",
):
    """Persist the live booking workflow for recovery and payment handoff."""
    import traceback

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            payload = (
                str(session_id or "").strip(),
                str(user_id or "default_user").strip(),
                str(workflow or "shipment_booking").strip(),
                str(current_step or "collecting_pickup_location").strip(),
                json.dumps(_normalize_json_payload(draft_json), ensure_ascii=False),
                json.dumps(_normalize_json_payload(quote_json), ensure_ascii=False),
                json.dumps(_normalize_json_payload(payment_json), ensure_ascii=False),
                int(shipment_id) if shipment_id is not None and str(shipment_id).strip() != "" else None,
                str(status or "active").strip(),
                str(last_user_message or "").strip(),
                str(last_bot_reply or "").strip(),
            )

            cursor.execute(
                """
                INSERT INTO booking_sessions
                    (session_id, user_id, workflow, current_step, draft_json, quote_json, payment_json, shipment_id, status, last_user_message, last_bot_reply, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON DUPLICATE KEY UPDATE
                    user_id = VALUES(user_id),
                    workflow = VALUES(workflow),
                    current_step = VALUES(current_step),
                    draft_json = VALUES(draft_json),
                    quote_json = VALUES(quote_json),
                    payment_json = VALUES(payment_json),
                    shipment_id = VALUES(shipment_id),
                    status = VALUES(status),
                    last_user_message = VALUES(last_user_message),
                    last_bot_reply = VALUES(last_bot_reply),
                    updated_at = NOW()
                """,
                payload,
            )
            conn.commit()
        except Error as e:
            conn.rollback()
            print(f"[booking_session][error] Failed to save booking session: {e}")
            print(f"[booking_session][traceback] {traceback.format_exc()}")
            raise
        finally:
            cursor.close()
            conn.close()
    except Error as e:
        print(f"[booking_session][error] Could not open DB connection: {e}")
        print(f"[booking_session][traceback] {traceback.format_exc()}")
        raise


def get_booking_session(session_id):
    """Fetch a persisted booking workflow by session ID."""
    if not session_id:
        return None

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT *
            FROM booking_sessions
            WHERE session_id = %s
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (str(session_id).strip(),),
        )
        row = cursor.fetchone()
        if not row:
            return None

        for field in ("draft_json", "quote_json", "payment_json"):
            row[field] = _normalize_json_payload(row.get(field))

        return row
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and getattr(conn, 'is_connected', lambda: True)():
            try:
                conn.close()
            except Exception:
                pass


def save_ai_action_log(
    user_id,
    role,
    action,
    intent,
    tool_name,
    shipment_id=None,
    status="success",
    request_payload=None,
    response_payload=None,
    error_message=None,
    started_at=None,
    completed_at=None,
    duration_ms=None,
    retry_count=0,
    execution_status="success",
):
    """Persist an AI action execution to `ai_action_logs` table."""
    import traceback

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            payload = (
                str(user_id or "").strip(),
                str(role or "").strip(),
                str(action or "").strip(),
                str(intent or "").strip(),
                str(tool_name or "").strip(),
                int(shipment_id) if shipment_id is not None and str(shipment_id).strip() != "" else None,
                str(status or "").strip(),
                json.dumps(request_payload or {}, ensure_ascii=False),
                json.dumps(response_payload or {}, ensure_ascii=False),
                started_at,
                completed_at,
                int(duration_ms) if duration_ms is not None else None,
                int(retry_count) if retry_count is not None else 0,
                str(execution_status or "").strip(),
                str(error_message or "").strip(),
            )

            cursor.execute(
                """
                INSERT INTO ai_action_logs
                    (user_id, role, action, intent, tool_name, shipment_id, status, request_payload, response_payload, started_at, completed_at, duration_ms, retry_count, execution_status, error_message, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                payload,
            )
            conn.commit()
            return cursor.lastrowid
        except Error as e:
            conn.rollback()
            print(f"[ai_action_log][error] Failed to save action log: {e}")
            print(f"[ai_action_log][traceback] {traceback.format_exc()}")
            raise
        finally:
            cursor.close()
            conn.close()
    except Error as e:
        print(f"[ai_action_log][error] Could not open DB connection: {e}")
        raise


def get_ai_action_logs(page=1, per_page=50, filters=None):
    """Fetch AI action logs with simple pagination and filtering.

    Filters may include: user_id, shipment_id, action, status.
    Returns a dict: {count, page, per_page, items}
    """
    filters = filters or {}
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            where_clauses = []
            params = []
            if filters.get("user_id"):
                where_clauses.append("user_id = %s")
                params.append(str(filters.get("user_id")).strip())
            if filters.get("shipment_id") is not None:
                where_clauses.append("shipment_id = %s")
                params.append(int(filters.get("shipment_id")))
            if filters.get("action"):
                where_clauses.append("action = %s")
                params.append(str(filters.get("action")).strip())
            if filters.get("status"):
                where_clauses.append("status = %s")
                params.append(str(filters.get("status")).strip())

            where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

            offset = max(0, int(page - 1)) * int(per_page)

            cursor.execute(f"SELECT COUNT(*) as cnt FROM ai_action_logs {where_sql}", tuple(params) if params else None)
            total = cursor.fetchone().get("cnt") or 0

            cursor.execute(
                f"SELECT * FROM ai_action_logs {where_sql} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                tuple(params + [int(per_page), int(offset)]) if params else (int(per_page), int(offset)),
            )
            rows = cursor.fetchall()
            # normalize JSON fields
            for row in rows:
                try:
                    row["request_payload"] = json.loads(row.get("request_payload") or "{}")
                except Exception:
                    row["request_payload"] = {}
                try:
                    row["response_payload"] = json.loads(row.get("response_payload") or "{}")
                except Exception:
                    row["response_payload"] = {}

            return {"count": int(total or 0), "page": int(page), "per_page": int(per_page), "items": rows}
        finally:
            cursor.close()
            conn.close()
    except Error as e:
        print(f"[ai_action_log][error] Could not query action logs: {e}")
        raise


def _get_ai_action_log_metrics(filters=None):
    """Aggregate observability metrics across AI action logs."""
    filters = filters or {}
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            where_clauses = []
            params = []
            if filters.get("user_id"):
                where_clauses.append("user_id = %s")
                params.append(str(filters.get("user_id")).strip())
            if filters.get("shipment_id") is not None:
                where_clauses.append("shipment_id = %s")
                params.append(int(filters.get("shipment_id")))
            if filters.get("action"):
                where_clauses.append("action = %s")
                params.append(str(filters.get("action")).strip())
            if filters.get("status"):
                where_clauses.append("status = %s")
                params.append(str(filters.get("status")).strip())

            where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

            cursor.execute(
                f"""
                SELECT
                    tool_name,
                    COUNT(*) AS executions,
                    AVG(COALESCE(duration_ms, 0)) AS avg_duration_ms,
                    SUM(CASE WHEN execution_status IN ('error', 'failed') THEN 1 ELSE 0 END) AS failure_count,
                    SUM(CASE WHEN retry_count > 0 THEN 1 ELSE 0 END) AS retried_count,
                    MAX(COALESCE(duration_ms, 0)) AS max_duration_ms
                FROM ai_action_logs
                {where_sql}
                GROUP BY tool_name
                ORDER BY avg_duration_ms DESC
                """,
                tuple(params) if params else None,
            )
            tool_rows = cursor.fetchall() or []

            cursor.execute(
                f"""
                SELECT
                    COUNT(*) AS total_actions,
                    AVG(COALESCE(duration_ms, 0)) AS avg_duration_ms,
                    SUM(CASE WHEN execution_status IN ('error', 'failed') THEN 1 ELSE 0 END) AS failure_count,
                    SUM(CASE WHEN retry_count > 0 THEN 1 ELSE 0 END) AS retry_count,
                    MAX(COALESCE(duration_ms, 0)) AS slowest_ms,
                    AVG(CASE WHEN started_at IS NOT NULL AND completed_at IS NOT NULL THEN TIMESTAMPDIFF(MICROSECOND, started_at, completed_at) / 1000.0 ELSE NULL END) AS precise_avg_ms
                FROM ai_action_logs
                {where_sql}
                """,
                tuple(params) if params else None,
            )
            summary = cursor.fetchone() or {}

            slowest_tool = None
            if tool_rows:
                slowest_tool = max(tool_rows, key=lambda row: float(row.get("avg_duration_ms") or 0))

            return {
                "summary": {
                    "total_actions": int(summary.get("total_actions") or 0),
                    "avg_duration_ms": float(summary.get("avg_duration_ms") or 0),
                    "precise_avg_ms": float(summary.get("precise_avg_ms") or 0),
                    "failure_count": int(summary.get("failure_count") or 0),
                    "retry_count": int(summary.get("retry_count") or 0),
                    "slowest_ms": float(summary.get("slowest_ms") or 0),
                    "avg_ai_response_latency_ms": float(summary.get("precise_avg_ms") or summary.get("avg_duration_ms") or 0),
                    "avg_websocket_latency_ms": 0.0,
                    "workflow_success_rate": round(((int(summary.get("total_actions") or 0) - int(summary.get("failure_count") or 0)) / int(summary.get("total_actions") or 1)) * 100, 2),
                },
                "tools": tool_rows,
                "slowest_tool": slowest_tool,
            }
        finally:
            cursor.close()
            conn.close()
    except Error as e:
        print(f"[ai_action_log][error] Could not query tool metrics: {e}")
        raise


def save_workflow_memory(user_id, workflow_type, workflow_state=None, current_step=None, context_json=None):
    """Upsert workflow memory into `ai_workflow_memory` for replay/debugging."""
    import traceback

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            payload = (
                str(user_id or "").strip(),
                str(workflow_type or "").strip(),
                json.dumps(workflow_state or {}, ensure_ascii=False),
                str(current_step or "").strip(),
                json.dumps(context_json or {}, ensure_ascii=False),
            )

            cursor.execute(
                """
                INSERT INTO ai_workflow_memory (user_id, workflow_type, workflow_state, current_step, context_json, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON DUPLICATE KEY UPDATE
                    workflow_state = VALUES(workflow_state),
                    current_step = VALUES(current_step),
                    context_json = VALUES(context_json),
                    updated_at = NOW()
                """,
                payload,
            )
            conn.commit()
            return cursor.lastrowid
        except Error as e:
            conn.rollback()
            print(f"[workflow_memory][error] Failed to save workflow memory: {e}")
            print(f"[workflow_memory][traceback] {traceback.format_exc()}")
            raise
        finally:
            cursor.close()
            conn.close()
    except Error as e:
        print(f"[workflow_memory][error] Could not open DB connection: {e}")
        raise


def save_ai_conversation_memory(user_id, session_id, conversation_id, memory_json=None, summary="", last_intent="", last_route="", confidence=0):
    """Upsert durable conversational memory for AI chat sessions."""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO ai_conversation_memory
                (user_id, session_id, conversation_id, memory_json, summary, last_intent, last_route, confidence, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON DUPLICATE KEY UPDATE
                user_id = VALUES(user_id),
                memory_json = VALUES(memory_json),
                summary = VALUES(summary),
                last_intent = VALUES(last_intent),
                last_route = VALUES(last_route),
                confidence = VALUES(confidence),
                updated_at = NOW()
            """,
            (
                str(user_id or "").strip() or None,
                str(session_id or "").strip(),
                str(conversation_id or "").strip(),
                json.dumps(memory_json or {}, ensure_ascii=False),
                str(summary or "").strip(),
                str(last_intent or "").strip(),
                str(last_route or "").strip(),
                float(confidence or 0),
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()


def load_ai_conversation_memory(user_id=None, session_id=None, conversation_id=None, limit=10):
    """Load recent AI conversation memory rows by user, session, or conversation."""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        where = []
        params = []
        if user_id:
            where.append("user_id = %s")
            params.append(str(user_id).strip())
        if session_id:
            where.append("session_id = %s")
            params.append(str(session_id).strip())
        if conversation_id:
            where.append("conversation_id = %s")
            params.append(str(conversation_id).strip())
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        cursor.execute(
            f"SELECT * FROM ai_conversation_memory {where_sql} ORDER BY updated_at DESC LIMIT %s",
            tuple(params + [int(limit)]),
        )
        rows = cursor.fetchall() or []
        for row in rows:
            row["memory_json"] = _normalize_json_payload(row.get("memory_json"))
        return rows
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()


def save_ai_session_context(session_id, user_id=None, role="", context_json=None, active_workflow="", current_step="", status="active"):
    """Upsert the latest AI session context used for workflow continuation."""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO ai_session_context
                (session_id, user_id, role, context_json, active_workflow, current_step, status, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON DUPLICATE KEY UPDATE
                user_id = VALUES(user_id),
                role = VALUES(role),
                context_json = VALUES(context_json),
                active_workflow = VALUES(active_workflow),
                current_step = VALUES(current_step),
                status = VALUES(status),
                updated_at = NOW()
            """,
            (
                str(session_id or "").strip(),
                str(user_id or "").strip() or None,
                str(role or "").strip() or None,
                json.dumps(context_json or {}, ensure_ascii=False),
                str(active_workflow or "").strip() or None,
                str(current_step or "").strip() or None,
                str(status or "active").strip() or "active",
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()


def load_ai_session_context(session_id):
    """Load the latest AI session context for a chat session."""
    if not session_id:
        return None

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM ai_session_context WHERE session_id = %s LIMIT 1", (str(session_id).strip(),))
        row = cursor.fetchone()
        if row:
            row["context_json"] = _normalize_json_payload(row.get("context_json"))
        return row
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()


def load_workflow_memory(user_id, workflow_type=None):
    """Load workflow memory for a user. If workflow_type provided, filter by it."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            if workflow_type:
                cursor.execute(
                    "SELECT * FROM ai_workflow_memory WHERE user_id = %s AND workflow_type = %s ORDER BY updated_at DESC LIMIT 1",
                    (str(user_id).strip(), str(workflow_type).strip()),
                )
            else:
                cursor.execute(
                    "SELECT * FROM ai_workflow_memory WHERE user_id = %s ORDER BY updated_at DESC LIMIT 10",
                    (str(user_id).strip(),),
                )

            rows = cursor.fetchall()
            for row in rows:
                try:
                    row["workflow_state"] = json.loads(row.get("workflow_state") or "{}")
                except Exception:
                    row["workflow_state"] = {}
                try:
                    row["context_json"] = json.loads(row.get("context_json") or "{}")
                except Exception:
                    row["context_json"] = {}

            return rows
        finally:
            cursor.close()
            conn.close()
    except Error as e:
        print(f"[workflow_memory][error] Could not load workflow memory: {e}")
        raise


def clear_workflow_memory(user_id, workflow_type=None):
    """Clear persisted workflow memory for a user (or a specific workflow)."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            if workflow_type:
                cursor.execute("DELETE FROM ai_workflow_memory WHERE user_id = %s AND workflow_type = %s", (str(user_id).strip(), str(workflow_type).strip()))
            else:
                cursor.execute("DELETE FROM ai_workflow_memory WHERE user_id = %s", (str(user_id).strip(),))
            conn.commit()
            return cursor.rowcount
        finally:
            cursor.close()
            conn.close()
    except Error as e:
        print(f"[workflow_memory][error] Could not clear workflow memory: {e}")
        raise
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()


def save_workflow_retry_job(
    user_id,
    workflow_type,
    action,
    route,
    tool_name,
    payload_json=None,
    error_message=None,
    execution_status="error",
    retry_count=0,
    max_retries=3,
    next_retry_at=None,
    status="queued",
):
    """Insert or update a workflow retry job keyed by user/workflow/action/route."""
    import traceback

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            payload = (
                str(user_id or "").strip() or None,
                str(workflow_type or "general").strip() or "general",
                str(action or "UNKNOWN").strip() or "UNKNOWN",
                str(route or "assistant.recovery").strip() or "assistant.recovery",
                str(tool_name or "unknown_tool").strip() or "unknown_tool",
                json.dumps(payload_json or {}, ensure_ascii=False),
                str(error_message or "").strip() or None,
                str(execution_status or "error").strip() or "error",
                int(retry_count) if retry_count is not None else 0,
                int(max_retries) if max_retries is not None else 3,
                next_retry_at,
                str(status or "queued").strip() or "queued",
            )

            cursor.execute(
                """
                INSERT INTO ai_workflow_retry_queue
                    (user_id, workflow_type, action, route, tool_name, payload_json, error_message, execution_status, retry_count, max_retries, next_retry_at, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON DUPLICATE KEY UPDATE
                    payload_json = VALUES(payload_json),
                    error_message = VALUES(error_message),
                    execution_status = VALUES(execution_status),
                    retry_count = VALUES(retry_count),
                    max_retries = VALUES(max_retries),
                    next_retry_at = VALUES(next_retry_at),
                    status = VALUES(status),
                    updated_at = NOW()
                """,
                payload,
            )
            conn.commit()
            return cursor.lastrowid
        except Error as e:
            conn.rollback()
            print(f"[workflow_retry][error] Failed to save retry job: {e}")
            print(f"[workflow_retry][traceback] {traceback.format_exc()}")
            raise
        finally:
            cursor.close()
            conn.close()
    except Error as e:
        print(f"[workflow_retry][error] Could not open DB connection: {e}")
        raise


def get_workflow_retry_jobs(status=None, limit=50):
    """Fetch queued/retrying workflow recovery jobs for admin controls."""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        if status:
            cursor.execute(
                """
                SELECT *
                FROM ai_workflow_retry_queue
                WHERE status = %s
                ORDER BY updated_at DESC, id DESC
                LIMIT %s
                """,
                (str(status).strip(), int(limit)),
            )
        else:
            cursor.execute(
                """
                SELECT *
                FROM ai_workflow_retry_queue
                ORDER BY updated_at DESC, id DESC
                LIMIT %s
                """,
                (int(limit),),
            )

        rows = cursor.fetchall() or []
        for row in rows:
            try:
                row["payload_json"] = json.loads(row.get("payload_json") or "{}")
            except Exception:
                row["payload_json"] = {}
        return rows
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()


def update_workflow_retry_job(job_id, *, status=None, retry_count=None, error_message=None, next_retry_at=None, execution_status=None):
    """Update retry state and return the latest job row."""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        updates = []
        params = []
        if status is not None:
            updates.append("status = %s")
            params.append(str(status).strip() or "queued")
        if retry_count is not None:
            updates.append("retry_count = %s")
            params.append(int(retry_count))
        if error_message is not None:
            updates.append("error_message = %s")
            params.append(str(error_message).strip() or None)
        if next_retry_at is not None:
            updates.append("next_retry_at = %s")
            params.append(next_retry_at)
        if execution_status is not None:
            updates.append("execution_status = %s")
            params.append(str(execution_status).strip() or "error")

        updates.append("updated_at = NOW()")
        params.append(int(job_id))

        cursor.execute(
            f"UPDATE ai_workflow_retry_queue SET {', '.join(updates)} WHERE id = %s",
            tuple(params),
        )
        conn.commit()

        cursor.execute("SELECT * FROM ai_workflow_retry_queue WHERE id = %s LIMIT 1", (int(job_id),))
        row = cursor.fetchone()
        if row:
            try:
                row["payload_json"] = json.loads(row.get("payload_json") or "{}")
            except Exception:
                row["payload_json"] = {}
        return row
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()


def get_user_preferences(user_id):
    """Fetch a persisted preference profile for a user."""
    if user_id in (None, "", 0):
        return {}

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT prefs_json FROM user_preferences WHERE user_id = %s LIMIT 1", (str(user_id).strip(),))
        row = cursor.fetchone() or {}
        return _normalize_json_payload(row.get("prefs_json"))
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()


def save_user_preferences(user_id, prefs):
    """Upsert a user's preference profile into `user_preferences`."""
    import traceback

    if user_id in (None, "", 0):
        raise RuntimeError("Authentication required")

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO user_preferences (user_id, prefs_json, updated_at)
                VALUES (%s, %s, NOW())
                ON DUPLICATE KEY UPDATE
                    prefs_json = VALUES(prefs_json),
                    updated_at = NOW()
                """,
                (str(user_id).strip(), json.dumps(prefs or {}, ensure_ascii=False)),
            )
            conn.commit()
            return cursor.lastrowid
        except Error as e:
            conn.rollback()
            print(f"[user_preferences][error] Failed to save preferences: {e}")
            print(f"[user_preferences][traceback] {traceback.format_exc()}")
            raise
        finally:
            cursor.close()
            conn.close()
    except Error as e:
        print(f"[user_preferences][error] Could not open DB connection: {e}")
        raise


def delete_user_preferences(user_id):
    """Remove a stored preference profile for a user."""
    if user_id in (None, "", 0):
        return 0

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_preferences WHERE user_id = %s", (str(user_id).strip(),))
        conn.commit()
        return cursor.rowcount
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()


def validate_foreign_key_reference(table_name, column_name, value):
    """Validate that a referenced row exists in a known table."""
    allowed_tables = {
        "bookings": {"id"},
        "shipments": {"id"},
        "drivers": {"id"},
        "vehicles": {"id", "vehicle_code"},
        "users": {"id"},
    }

    if value in (None, "", 0):
        return False

    if table_name not in allowed_tables or column_name not in allowed_tables[table_name]:
        raise ValueError("Unsupported foreign key validation target")

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(f"SELECT 1 FROM {table_name} WHERE {column_name} = %s LIMIT 1", (value,))
        return cursor.fetchone() is not None
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()


def save_ai_tool_metric(
    user_id,
    role,
    workflow_type,
    action,
    tool_name,
    duration_ms,
    retry_count=0,
    execution_status="success",
    ai_latency_ms=None,
    websocket_latency_ms=None,
    correlation_id=None,
    execution_id=None,
    metadata_json=None,
):
    """Persist one AI tool execution metric row."""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO ai_tool_metrics
                (user_id, role, workflow_type, action, tool_name, duration_ms, retry_count, execution_status, ai_latency_ms, websocket_latency_ms, correlation_id, execution_id, metadata_json, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """,
            (
                str(user_id or "").strip() or None,
                str(role or "").strip() or None,
                str(workflow_type or "").strip() or None,
                str(action or "").strip() or None,
                str(tool_name or "").strip() or None,
                int(duration_ms) if duration_ms is not None else None,
                int(retry_count) if retry_count is not None else 0,
                str(execution_status or "success").strip() or "success",
                float(ai_latency_ms) if ai_latency_ms is not None else None,
                float(websocket_latency_ms) if websocket_latency_ms is not None else None,
                str(correlation_id or "").strip() or None,
                str(execution_id or "").strip() or None,
                json.dumps(metadata_json or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()


def get_ai_tool_metrics(filters=None):
    """Aggregate AI tool metrics from the ai_tool_metrics table."""
    filters = filters or {}
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        where_clauses = []
        params = []
        for key in ("user_id", "role", "workflow_type", "action", "tool_name", "execution_status"):
            if filters.get(key):
                where_clauses.append(f"{key} = %s")
                params.append(str(filters.get(key)).strip())

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        cursor.execute(
            f"""
            SELECT
                tool_name,
                COUNT(*) AS executions,
                AVG(COALESCE(duration_ms, 0)) AS avg_duration_ms,
                SUM(CASE WHEN execution_status IN ('error', 'failed', 'timeout') THEN 1 ELSE 0 END) AS failure_count,
                SUM(CASE WHEN retry_count > 0 THEN 1 ELSE 0 END) AS retried_count,
                MAX(COALESCE(duration_ms, 0)) AS max_duration_ms,
                AVG(COALESCE(ai_latency_ms, duration_ms, 0)) AS avg_ai_response_latency_ms,
                AVG(COALESCE(websocket_latency_ms, 0)) AS avg_websocket_latency_ms
            FROM ai_tool_metrics
            {where_sql}
            GROUP BY tool_name
            ORDER BY avg_duration_ms DESC
            """,
            tuple(params) if params else None,
        )
        tool_rows = cursor.fetchall() or []

        cursor.execute(
            f"""
            SELECT
                COUNT(*) AS total_actions,
                AVG(COALESCE(duration_ms, 0)) AS avg_duration_ms,
                AVG(COALESCE(ai_latency_ms, duration_ms, 0)) AS precise_avg_ms,
                SUM(CASE WHEN execution_status IN ('error', 'failed', 'timeout') THEN 1 ELSE 0 END) AS failure_count,
                SUM(CASE WHEN retry_count > 0 THEN 1 ELSE 0 END) AS retry_count,
                MAX(COALESCE(duration_ms, 0)) AS slowest_ms,
                AVG(COALESCE(websocket_latency_ms, 0)) AS avg_websocket_latency_ms
            FROM ai_tool_metrics
            {where_sql}
            """,
            tuple(params) if params else None,
        )
        summary = cursor.fetchone() or {}
        slowest_tool = max(tool_rows, key=lambda row: float(row.get("avg_duration_ms") or 0)) if tool_rows else None
        total_actions = int(summary.get("total_actions") or 0)
        failure_count = int(summary.get("failure_count") or 0)
        return {
            "summary": {
                "total_actions": total_actions,
                "avg_duration_ms": float(summary.get("avg_duration_ms") or 0),
                "precise_avg_ms": float(summary.get("precise_avg_ms") or 0),
                "failure_count": failure_count,
                "retry_count": int(summary.get("retry_count") or 0),
                "slowest_ms": float(summary.get("slowest_ms") or 0),
                "avg_ai_response_latency_ms": float(summary.get("precise_avg_ms") or summary.get("avg_duration_ms") or 0),
                "avg_websocket_latency_ms": float(summary.get("avg_websocket_latency_ms") or 0),
                "workflow_success_rate": round(((total_actions - failure_count) / total_actions) * 100, 2) if total_actions else 100.0,
            },
            "tools": tool_rows,
            "slowest_tool": slowest_tool,
        }
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()


def save_audit_log(user_id, role, action, entity_type, entity_id=None, status="success", details_json=None, correlation_id=None):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO audit_logs
                (user_id, role, action, entity_type, entity_id, status, details_json, correlation_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """,
            (
                str(user_id or "").strip() or None,
                str(role or "").strip() or None,
                str(action or "").strip() or None,
                str(entity_type or "").strip() or None,
                str(entity_id or "").strip() or None,
                str(status or "success").strip() or "success",
                json.dumps(details_json or {}, ensure_ascii=False),
                str(correlation_id or "").strip() or None,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()


def delete_audit_logs_older_than(days):
    """Delete audit log rows older than a retention window."""
    retention_days = max(0, int(days or 0))
    if retention_days <= 0:
        return 0

    from datetime import datetime, timedelta

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        cursor.execute("DELETE FROM audit_logs WHERE created_at < %s", (cutoff,))
        conn.commit()
        return cursor.rowcount
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()


def get_audit_logs(page=1, per_page=50, filters=None):
    filters = filters or {}
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        where_clauses = []
        params = []
        for key in ("user_id", "role", "action", "entity_type", "status", "correlation_id"):
            if filters.get(key):
                where_clauses.append(f"{key} = %s")
                params.append(str(filters.get(key)).strip())
        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        offset = max(0, int(page - 1)) * int(per_page)
        cursor.execute(f"SELECT COUNT(*) AS count FROM audit_logs {where_sql}", tuple(params) if params else None)
        total = int((cursor.fetchone() or {}).get("count") or 0)
        cursor.execute(
            f"SELECT * FROM audit_logs {where_sql} ORDER BY created_at DESC LIMIT %s OFFSET %s",
            tuple(params + [int(per_page), int(offset)]) if params else (int(per_page), int(offset)),
        )
        items = cursor.fetchall() or []
        for row in items:
            try:
                row["details_json"] = json.loads(row.get("details_json") or "{}")
            except Exception:
                row["details_json"] = {}
        return {"count": total, "page": int(page), "per_page": int(per_page), "items": items}
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()


def save_webhook_event(source, event_type, payload_json=None, headers_json=None, status="received", response_code=None, correlation_id=None):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO webhook_events
                (source, event_type, status, payload_json, headers_json, response_code, correlation_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            """,
            (
                str(source or "").strip() or None,
                str(event_type or "").strip() or None,
                str(status or "received").strip() or "received",
                json.dumps(payload_json or {}, ensure_ascii=False),
                json.dumps(headers_json or {}, ensure_ascii=False),
                int(response_code) if response_code is not None else None,
                str(correlation_id or "").strip() or None,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()


def update_webhook_event_status(event_id, status, response_code=None):
    """Update a stored webhook event's status after a retry or replay."""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "UPDATE webhook_events SET status = %s, response_code = %s WHERE id = %s",
            (str(status or "received").strip() or "received", int(response_code) if response_code is not None else None, int(event_id)),
        )
        conn.commit()

        cursor.execute("SELECT * FROM webhook_events WHERE id = %s LIMIT 1", (int(event_id),))
        row = cursor.fetchone()
        if row:
            try:
                row["payload_json"] = json.loads(row.get("payload_json") or "{}")
            except Exception:
                row["payload_json"] = {}
            try:
                row["headers_json"] = json.loads(row.get("headers_json") or "{}")
            except Exception:
                row["headers_json"] = {}
        return row
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()


def get_webhook_events(page=1, per_page=50, filters=None):
    filters = filters or {}
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        where_clauses = []
        params = []
        for key in ("source", "event_type", "status", "correlation_id"):
            if filters.get(key):
                where_clauses.append(f"{key} = %s")
                params.append(str(filters.get(key)).strip())
        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        offset = max(0, int(page - 1)) * int(per_page)
        cursor.execute(f"SELECT COUNT(*) AS count FROM webhook_events {where_sql}", tuple(params) if params else None)
        total = int((cursor.fetchone() or {}).get("count") or 0)
        cursor.execute(
            f"SELECT * FROM webhook_events {where_sql} ORDER BY created_at DESC LIMIT %s OFFSET %s",
            tuple(params + [int(per_page), int(offset)]) if params else (int(per_page), int(offset)),
        )
        items = cursor.fetchall() or []
        for row in items:
            try:
                row["payload_json"] = json.loads(row.get("payload_json") or "{}")
            except Exception:
                row["payload_json"] = {}
            try:
                row["headers_json"] = json.loads(row.get("headers_json") or "{}")
            except Exception:
                row["headers_json"] = {}
        return {"count": total, "page": int(page), "per_page": int(per_page), "items": items}
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()


def get_vehicle_by_code(vehicle_code):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM vehicles WHERE vehicle_code = %s LIMIT 1", (str(vehicle_code or "").strip(),))
        row = cursor.fetchone()
        return row
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()


def save_vehicle_record(vehicle):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        features_json = vehicle.get("features_json") or vehicle.get("features") or []
        if isinstance(features_json, list):
            features_json = json.dumps(features_json, ensure_ascii=False)
        cursor.execute(
            """
            INSERT INTO vehicles
                (vehicle_code, vehicle_name, truck_type, max_load_tons, rate_per_km, availability_status, current_booking_id, current_latitude, current_longitude, features_json, ai_notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                vehicle_name = VALUES(vehicle_name),
                truck_type = VALUES(truck_type),
                max_load_tons = VALUES(max_load_tons),
                rate_per_km = VALUES(rate_per_km),
                availability_status = VALUES(availability_status),
                current_booking_id = VALUES(current_booking_id),
                current_latitude = VALUES(current_latitude),
                current_longitude = VALUES(current_longitude),
                features_json = VALUES(features_json),
                ai_notes = VALUES(ai_notes),
                updated_at = NOW()
            """,
            (
                str(vehicle.get("vehicle_code") or "").strip(),
                str(vehicle.get("vehicle_name") or "").strip(),
                str(vehicle.get("truck_type") or "").strip(),
                float(vehicle.get("max_load_tons") or 0),
                float(vehicle.get("rate_per_km") or 0),
                str(vehicle.get("availability_status") or "available").strip() or "available",
                int(vehicle.get("current_booking_id")) if vehicle.get("current_booking_id") not in (None, "") else None,
                float(vehicle.get("current_latitude")) if vehicle.get("current_latitude") not in (None, "") else None,
                float(vehicle.get("current_longitude")) if vehicle.get("current_longitude") not in (None, "") else None,
                features_json,
                str(vehicle.get("ai_notes") or "").strip(),
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()


def delete_vehicle_record(vehicle_code):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM vehicles WHERE vehicle_code = %s", (str(vehicle_code or "").strip(),))
        conn.commit()
        return cursor.rowcount
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()


def initialize_schema():
    """Create required tables if they do not exist.

    Tables created:
    - bookings: transport booking records
    - payments: Razorpay payment records
    - payments supports both booking_id and shipment_id references
    - gps_logs: live truck location history
    - lorries_12_tyre, lorries_14_tyre, lorries_16_tyre: vehicle fleet tracking
    - vehicles: booking vehicle catalog and recommendation source
    - booking_status_logs: booking lifecycle history
    - shipment_event_logs: shipment journey event history
    - chat_history: chatbot conversation logs
    - conversations: JSON transcript snapshots

    Uses CREATE TABLE IF NOT EXISTS so existing data is never destroyed.
    """
    conn = None
    cursor = None
    try:
        validate_db_environment()
        conn = connect(**DB_CONFIG)
        cursor = conn.cursor()

        # Create bookings table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                customer_name VARCHAR(255),
                phone VARCHAR(32),
                pickup_location VARCHAR(255),
                drop_location VARCHAR(255),
                truck_type VARCHAR(50),
                load_weight DECIMAL(10, 2),
                price INT,
                booking_status VARCHAR(50) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id VARCHAR(255) NOT NULL,
                source_location VARCHAR(255),
                destination_location VARCHAR(255),
                exact_pickup_location VARCHAR(255),
                exact_delivery_location VARCHAR(255),
                tons INT,
                tyre_type VARCHAR(50),
                distance INT,
                token_amount INT,
                payment_status VARCHAR(50),
                lorry_number VARCHAR(100),
                whatsapp_notifications_enabled TINYINT(1) DEFAULT 1,
                whatsapp_booking_confirmation TINYINT(1) DEFAULT 1,
                whatsapp_payment_confirmation TINYINT(1) DEFAULT 1,
                whatsapp_live_location TINYINT(1) DEFAULT 1,
                whatsapp_delivery_updates TINYINT(1) DEFAULT 1,
                INDEX idx_user_id (user_id),
                INDEX idx_phone (phone),
                INDEX idx_booking_status (booking_status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                full_name VARCHAR(255) NOT NULL,
                email VARCHAR(255) NOT NULL,
                phone VARCHAR(32) NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                role VARCHAR(20) NOT NULL DEFAULT 'customer',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_users_email (email),
                UNIQUE KEY uq_users_phone (phone),
                INDEX idx_users_role (role),
                INDEX idx_users_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("SHOW COLUMNS FROM users")
        existing_user_columns = {row[0] for row in cursor.fetchall()}
        user_column_additions = [
            ("full_name", "VARCHAR(255) NOT NULL AFTER id"),
            ("email", "VARCHAR(255) NOT NULL AFTER full_name"),
            ("phone", "VARCHAR(32) NOT NULL AFTER email"),
            ("password_hash", "VARCHAR(255) NOT NULL AFTER phone"),
            ("role", "VARCHAR(20) NOT NULL DEFAULT 'customer' AFTER password_hash"),
            ("created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP AFTER role"),
        ]

        for column_name, column_definition in user_column_additions:
            if column_name not in existing_user_columns:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {column_name} {column_definition}")
                existing_user_columns.add(column_name)

        cursor.execute("SHOW COLUMNS FROM bookings")
        existing_booking_columns = {row[0] for row in cursor.fetchall()}
        booking_column_additions = [
            ("customer_name", "VARCHAR(255) NULL AFTER id"),
            ("phone", "VARCHAR(32) NULL AFTER customer_name"),
            ("pickup_location", "VARCHAR(255) NULL AFTER phone"),
            ("drop_location", "VARCHAR(255) NULL AFTER pickup_location"),
            ("truck_type", "VARCHAR(50) NULL AFTER drop_location"),
            ("load_weight", "DECIMAL(10, 2) NULL AFTER truck_type"),
            ("price", "INT NULL AFTER load_weight"),
            ("booking_status", "VARCHAR(50) DEFAULT 'pending' AFTER price"),
            ("user_id", "VARCHAR(255) NOT NULL DEFAULT 'web_booking' AFTER booking_status"),
            ("source_location", "VARCHAR(255) NULL AFTER user_id"),
            ("destination_location", "VARCHAR(255) NULL AFTER source_location"),
            ("exact_pickup_location", "VARCHAR(255) NULL AFTER destination_location"),
            ("exact_delivery_location", "VARCHAR(255) NULL AFTER exact_pickup_location"),
            ("tons", "INT NULL AFTER exact_delivery_location"),
            ("tyre_type", "VARCHAR(50) NULL AFTER tons"),
            ("distance", "INT NULL AFTER tyre_type"),
            ("token_amount", "INT NULL AFTER distance"),
            ("payment_status", "VARCHAR(50) NULL AFTER token_amount"),
            ("lorry_number", "VARCHAR(100) NULL AFTER payment_status"),
            ("whatsapp_notifications_enabled", "TINYINT(1) DEFAULT 1 AFTER lorry_number"),
            ("whatsapp_booking_confirmation", "TINYINT(1) DEFAULT 1 AFTER whatsapp_notifications_enabled"),
            ("whatsapp_payment_confirmation", "TINYINT(1) DEFAULT 1 AFTER whatsapp_booking_confirmation"),
            ("whatsapp_live_location", "TINYINT(1) DEFAULT 1 AFTER whatsapp_payment_confirmation"),
            ("whatsapp_delivery_updates", "TINYINT(1) DEFAULT 1 AFTER whatsapp_live_location"),
        ]

        for column_name, column_definition in booking_column_additions:
            if column_name not in existing_booking_columns:
                cursor.execute(f"ALTER TABLE bookings ADD COLUMN {column_name} {column_definition}")
                existing_booking_columns.add(column_name)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS shipments (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(255) NOT NULL,
                pickup_location VARCHAR(255) NOT NULL,
                drop_location VARCHAR(255) NOT NULL,
                cargo_type VARCHAR(100) NOT NULL,
                truck_type VARCHAR(50) NOT NULL,
                weight DECIMAL(10, 2) NOT NULL,
                distance_km DECIMAL(10, 2) NOT NULL DEFAULT 0,
                estimated_price INT NOT NULL DEFAULT 0,
                payment_status VARCHAR(50) DEFAULT 'pending',
                shipment_status VARCHAR(50) DEFAULT 'pending',
                assigned_driver_id BIGINT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_shipments_user_id (user_id),
                INDEX idx_shipments_payment_status (payment_status),
                INDEX idx_shipments_status (shipment_status),
                INDEX idx_shipments_driver_id (assigned_driver_id),
                INDEX idx_shipments_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("SHOW COLUMNS FROM shipments")
        existing_shipment_columns = {row[0] for row in cursor.fetchall()}
        shipment_column_additions = [
            ("user_id", "VARCHAR(255) NOT NULL AFTER id"),
            ("pickup_location", "VARCHAR(255) NOT NULL AFTER user_id"),
            ("drop_location", "VARCHAR(255) NOT NULL AFTER pickup_location"),
            ("cargo_type", "VARCHAR(100) NOT NULL AFTER drop_location"),
            ("truck_type", "VARCHAR(50) NOT NULL AFTER cargo_type"),
            ("weight", "DECIMAL(10, 2) NOT NULL AFTER truck_type"),
            ("distance_km", "DECIMAL(10, 2) NOT NULL DEFAULT 0 AFTER weight"),
            ("estimated_price", "INT NOT NULL DEFAULT 0 AFTER distance_km"),
            ("payment_status", "VARCHAR(50) DEFAULT 'pending' AFTER estimated_price"),
            ("shipment_status", "VARCHAR(50) DEFAULT 'pending' AFTER payment_status"),
            ("assigned_driver_id", "BIGINT NULL AFTER shipment_status"),
            ("created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP AFTER assigned_driver_id"),
        ]

        for column_name, column_definition in shipment_column_additions:
            if column_name not in existing_shipment_columns:
                cursor.execute(f"ALTER TABLE shipments ADD COLUMN {column_name} {column_definition}")
                existing_shipment_columns.add(column_name)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS vehicles (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                vehicle_code VARCHAR(100) NOT NULL,
                vehicle_name VARCHAR(255) NOT NULL,
                truck_type VARCHAR(50) NOT NULL,
                max_load_tons DECIMAL(10, 2) NOT NULL,
                rate_per_km DECIMAL(10, 2) NOT NULL DEFAULT 0,
                availability_status VARCHAR(50) DEFAULT 'available',
                current_booking_id BIGINT NULL,
                current_latitude DECIMAL(10, 8),
                current_longitude DECIMAL(11, 8),
                features_json LONGTEXT,
                ai_notes LONGTEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_vehicle_code (vehicle_code),
                INDEX idx_vehicle_truck_type (truck_type),
                INDEX idx_vehicle_status (availability_status),
                INDEX idx_vehicle_booking (current_booking_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("SHOW COLUMNS FROM vehicles")
        existing_vehicle_columns = {row[0] for row in cursor.fetchall()}
        vehicle_column_additions = [
            ("vehicle_code", "VARCHAR(100) NOT NULL AFTER id"),
            ("vehicle_name", "VARCHAR(255) NOT NULL AFTER vehicle_code"),
            ("truck_type", "VARCHAR(50) NOT NULL AFTER vehicle_name"),
            ("max_load_tons", "DECIMAL(10, 2) NOT NULL AFTER truck_type"),
            ("rate_per_km", "DECIMAL(10, 2) NOT NULL DEFAULT 0 AFTER max_load_tons"),
            ("availability_status", "VARCHAR(50) DEFAULT 'available' AFTER rate_per_km"),
            ("current_booking_id", "BIGINT NULL AFTER availability_status"),
            ("current_latitude", "DECIMAL(10, 8) NULL AFTER current_booking_id"),
            ("current_longitude", "DECIMAL(11, 8) NULL AFTER current_latitude"),
            ("features_json", "LONGTEXT NULL AFTER current_longitude"),
            ("ai_notes", "LONGTEXT NULL AFTER features_json"),
            ("created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP AFTER ai_notes"),
            ("updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP AFTER created_at"),
        ]

        for column_name, column_definition in vehicle_column_additions:
            if column_name not in existing_vehicle_columns:
                cursor.execute(f"ALTER TABLE vehicles ADD COLUMN {column_name} {column_definition}")
                existing_vehicle_columns.add(column_name)

        cursor.execute("SELECT COUNT(*) FROM vehicles")
        vehicle_count = cursor.fetchone()[0] or 0
        if vehicle_count == 0:
            sample_vehicles = [
                ("VEH-12-001", "Atlas Freight 12", "12 tyre", 25, 38, "available", None, 17.385, 78.4867, '["GPS tracking", "POD ready", "High priority dispatch"]', "Best for urban and regional freight under 25 tons"),
                ("VEH-12-002", "Atlas Freight 12B", "12 tyre", 24, 37, "available", None, 19.076, 72.8777, '["GPS tracking", "Razorpay verified", "Customer notification enabled"]', "Balanced cost and capacity for short-haul routes"),
                ("VEH-14-001", "Atlas Freight 14", "14 tyre", 30, 44, "available", None, 12.9716, 77.5946, '["Temperature-ready cargo bay", "Live status updates"]', "Recommended for medium-to-heavy consignments up to 30 tons"),
                ("VEH-14-002", "Atlas Freight 14B", "14 tyre", 30, 45, "maintenance", None, 13.0827, 80.2707, '["Priority dispatch", "Driver checklist"]', "Backup vehicle for scheduled fleet availability"),
                ("VEH-16-001", "Atlas Freight 16", "16 tyre", 35, 52, "available", None, 28.7041, 77.1025, '["Heavy load certified", "Long-haul optimized", "Live GPS"]', "Ideal for high-capacity and long-distance freight"),
                ("VEH-16-002", "Atlas Freight 16B", "16 tyre", 35, 54, "available", None, 22.5726, 88.3639, '["Heavy load certified", "Night dispatch ready"]', "Assigned when maximum payload and reliability are required"),
            ]
            cursor.executemany(
                """
                INSERT INTO vehicles
                    (vehicle_code, vehicle_name, truck_type, max_load_tons, rate_per_km, availability_status, current_booking_id, current_latitude, current_longitude, features_json, ai_notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                sample_vehicles,
            )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS booking_status_logs (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                booking_id BIGINT NOT NULL,
                status VARCHAR(50) NOT NULL,
                note TEXT,
                location VARCHAR(255),
                actor_role VARCHAR(50) DEFAULT 'system',
                metadata_json LONGTEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_booking_status_booking_id (booking_id),
                INDEX idx_booking_status_status (status),
                INDEX idx_booking_status_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("SHOW COLUMNS FROM booking_status_logs")
        existing_status_log_columns = {row[0] for row in cursor.fetchall()}
        status_log_column_additions = [
            ("booking_id", "BIGINT NOT NULL AFTER id"),
            ("status", "VARCHAR(50) NOT NULL AFTER booking_id"),
            ("note", "TEXT NULL AFTER status"),
            ("location", "VARCHAR(255) NULL AFTER note"),
            ("actor_role", "VARCHAR(50) DEFAULT 'system' AFTER location"),
            ("metadata_json", "LONGTEXT NULL AFTER actor_role"),
            ("created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP AFTER metadata_json"),
        ]

        for column_name, column_definition in status_log_column_additions:
            if column_name not in existing_status_log_columns:
                cursor.execute(f"ALTER TABLE booking_status_logs ADD COLUMN {column_name} {column_definition}")
                existing_status_log_columns.add(column_name)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS shipment_event_logs (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                shipment_id BIGINT NOT NULL,
                event_type VARCHAR(80) NOT NULL,
                title VARCHAR(255),
                message TEXT,
                severity VARCHAR(32) DEFAULT 'info',
                source VARCHAR(80) DEFAULT 'system',
                metadata_json LONGTEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_shipment_event_shipment_id (shipment_id),
                INDEX idx_shipment_event_type (event_type),
                INDEX idx_shipment_event_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("SHOW COLUMNS FROM shipment_event_logs")
        existing_shipment_event_columns = {row[0] for row in cursor.fetchall()}
        shipment_event_column_additions = [
            ("shipment_id", "BIGINT NOT NULL AFTER id"),
            ("event_type", "VARCHAR(80) NOT NULL AFTER shipment_id"),
            ("title", "VARCHAR(255) NULL AFTER event_type"),
            ("message", "TEXT NULL AFTER title"),
            ("severity", "VARCHAR(32) DEFAULT 'info' AFTER message"),
            ("source", "VARCHAR(80) DEFAULT 'system' AFTER severity"),
            ("metadata_json", "LONGTEXT NULL AFTER source"),
            ("created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP AFTER metadata_json"),
        ]

        for column_name, column_definition in shipment_event_column_additions:
            if column_name not in existing_shipment_event_columns:
                cursor.execute(f"ALTER TABLE shipment_event_logs ADD COLUMN {column_name} {column_definition}")
                existing_shipment_event_columns.add(column_name)

        # Create payments table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                booking_id BIGINT NULL,
                shipment_id BIGINT NULL,
                session_id VARCHAR(255) NULL,
                razorpay_order_id VARCHAR(255) NOT NULL,
                razorpay_payment_id VARCHAR(255),
                amount INT NOT NULL,
                payment_status VARCHAR(50) DEFAULT 'created',
                status VARCHAR(50) DEFAULT 'created',
                payment_type VARCHAR(50) DEFAULT 'advance',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_booking_id (booking_id),
                INDEX idx_shipment_id (shipment_id),
                INDEX idx_session_id (session_id),
                INDEX idx_razorpay_order_id (razorpay_order_id),
                INDEX idx_payment_status (payment_status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("SHOW COLUMNS FROM payments")
        existing_payment_columns = {row[0] for row in cursor.fetchall()}
        payment_column_additions = [
            ("booking_id", "BIGINT NULL AFTER id"),
            ("shipment_id", "BIGINT NULL AFTER booking_id"),
            ("session_id", "VARCHAR(255) NULL AFTER shipment_id"),
            ("razorpay_order_id", "VARCHAR(255) NOT NULL AFTER shipment_id"),
            ("razorpay_payment_id", "VARCHAR(255) NULL AFTER razorpay_order_id"),
            ("amount", "INT NOT NULL AFTER razorpay_payment_id"),
            ("payment_status", "VARCHAR(50) DEFAULT 'created' AFTER amount"),
            ("status", "VARCHAR(50) DEFAULT 'created' AFTER payment_status"),
            ("payment_type", "VARCHAR(50) DEFAULT 'advance' AFTER status"),
        ]

        for column_name, column_definition in payment_column_additions:
            if column_name not in existing_payment_columns:
                cursor.execute(f"ALTER TABLE payments ADD COLUMN {column_name} {column_definition}")
                existing_payment_columns.add(column_name)

        cursor.execute("SHOW COLUMNS FROM payments LIKE 'booking_id'")
        booking_id_column = cursor.fetchone()
        if booking_id_column and str(booking_id_column[1]).upper() == 'NO':
            cursor.execute("ALTER TABLE payments MODIFY booking_id BIGINT NULL")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS customer_wallet_ledger (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(255) NULL,
                booking_id BIGINT NULL,
                shipment_id BIGINT NULL,
                payment_id BIGINT NULL,
                entry_type VARCHAR(50) NOT NULL,
                amount INT NOT NULL,
                balance_after INT DEFAULT 0,
                description VARCHAR(255) NULL,
                metadata_json LONGTEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_wallet_user (user_id),
                INDEX idx_wallet_booking (booking_id),
                INDEX idx_wallet_shipment (shipment_id),
                INDEX idx_wallet_payment (payment_id),
                INDEX idx_wallet_created (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS payment_retries (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                payment_id BIGINT NULL,
                booking_id BIGINT NULL,
                shipment_id BIGINT NULL,
                old_razorpay_order_id VARCHAR(255) NULL,
                new_razorpay_order_id VARCHAR(255) NULL,
                retry_reason VARCHAR(255) NULL,
                retry_count INT DEFAULT 0,
                status VARCHAR(50) DEFAULT 'created',
                metadata_json LONGTEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_payment_retries_payment (payment_id),
                INDEX idx_payment_retries_booking (booking_id),
                INDEX idx_payment_retries_shipment (shipment_id),
                INDEX idx_payment_retries_status (status),
                INDEX idx_payment_retries_created (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS payment_refunds (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                payment_id BIGINT NULL,
                booking_id BIGINT NULL,
                shipment_id BIGINT NULL,
                razorpay_payment_id VARCHAR(255) NOT NULL,
                razorpay_refund_id VARCHAR(255) NULL,
                amount INT NOT NULL,
                status VARCHAR(50) DEFAULT 'initiated',
                reason VARCHAR(255) NULL,
                metadata_json LONGTEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_payment_refunds_payment (payment_id),
                INDEX idx_payment_refunds_booking (booking_id),
                INDEX idx_payment_refunds_shipment (shipment_id),
                INDEX idx_payment_refunds_razorpay_payment (razorpay_payment_id),
                INDEX idx_payment_refunds_status (status),
                INDEX idx_payment_refunds_created (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # Create gps_logs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS gps_logs (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                booking_id BIGINT NULL,
                shipment_id BIGINT NULL,
                driver_id BIGINT NULL,
                lorry_number VARCHAR(100) NOT NULL,
                latitude DECIMAL(10, 8) NOT NULL,
                longitude DECIMAL(11, 8) NOT NULL,
                speed_kmph DECIMAL(8, 2) NULL,
                heading_degrees DECIMAL(8, 2) NULL,
                source_location VARCHAR(255),
                destination_location VARCHAR(255),
                event_type VARCHAR(100) DEFAULT 'gps_ping',
                metadata_json LONGTEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_booking_id (booking_id),
                INDEX idx_gps_logs_shipment_id (shipment_id),
                INDEX idx_gps_logs_driver_id (driver_id),
                INDEX idx_lorry_number (lorry_number),
                INDEX idx_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("SHOW COLUMNS FROM gps_logs")
        existing_gps_log_columns = {row[0] for row in cursor.fetchall()}
        gps_log_column_additions = [
            ("booking_id", "BIGINT NULL AFTER id"),
            ("shipment_id", "BIGINT NULL AFTER booking_id"),
            ("driver_id", "BIGINT NULL AFTER shipment_id"),
            ("lorry_number", "VARCHAR(100) NOT NULL AFTER booking_id"),
            ("latitude", "DECIMAL(10, 8) NOT NULL AFTER lorry_number"),
            ("longitude", "DECIMAL(11, 8) NOT NULL AFTER latitude"),
            ("speed_kmph", "DECIMAL(8, 2) NULL AFTER longitude"),
            ("heading_degrees", "DECIMAL(8, 2) NULL AFTER speed_kmph"),
            ("source_location", "VARCHAR(255) NULL AFTER heading_degrees"),
            ("destination_location", "VARCHAR(255) NULL AFTER source_location"),
            ("event_type", "VARCHAR(100) DEFAULT 'gps_ping' AFTER destination_location"),
            ("metadata_json", "LONGTEXT NULL AFTER event_type"),
        ]

        for column_name, column_definition in gps_log_column_additions:
            if column_name not in existing_gps_log_columns:
                cursor.execute(f"ALTER TABLE gps_logs ADD COLUMN {column_name} {column_definition}")
                existing_gps_log_columns.add(column_name)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS driver_locations (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                driver_id BIGINT NULL,
                shipment_id BIGINT NULL,
                booking_id BIGINT NULL,
                lorry_number VARCHAR(100) NOT NULL,
                latitude DECIMAL(10, 8) NOT NULL,
                longitude DECIMAL(11, 8) NOT NULL,
                speed_kmph DECIMAL(8, 2) NULL,
                heading_degrees DECIMAL(8, 2) NULL,
                accuracy_meters DECIMAL(8, 2) NULL,
                battery_level DECIMAL(5, 2) NULL,
                heartbeat_status VARCHAR(50) DEFAULT 'online',
                source VARCHAR(100) DEFAULT 'gps',
                metadata_json LONGTEXT,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_driver_locations_driver (driver_id),
                INDEX idx_driver_locations_shipment (shipment_id),
                INDEX idx_driver_locations_booking (booking_id),
                INDEX idx_driver_locations_lorry (lorry_number),
                INDEX idx_driver_locations_recorded (recorded_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(255) NULL,
                driver_id BIGINT NULL,
                shipment_id BIGINT NULL,
                notification_type VARCHAR(100) NULL,
                title VARCHAR(255) NULL,
                message TEXT,
                status VARCHAR(50) DEFAULT 'unread',
                metadata_json LONGTEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                read_at TIMESTAMP NULL,
                INDEX idx_notifications_user (user_id),
                INDEX idx_notifications_driver (driver_id),
                INDEX idx_notifications_shipment (shipment_id),
                INDEX idx_notifications_status (status),
                INDEX idx_notifications_created (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # Create lorries_12_tyre table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lorries_12_tyre (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                vehicle_number VARCHAR(100) UNIQUE NOT NULL,
                latitude DECIMAL(10, 8),
                longitude DECIMAL(11, 8),
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                availability_status VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_vehicle_number (vehicle_number),
                INDEX idx_availability_status (availability_status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # Create lorries_14_tyre table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lorries_14_tyre (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                vehicle_number VARCHAR(100) UNIQUE NOT NULL,
                latitude DECIMAL(10, 8),
                longitude DECIMAL(11, 8),
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                availability_status VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_vehicle_number (vehicle_number),
                INDEX idx_availability_status (availability_status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # Create lorries_16_tyre table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lorries_16_tyre (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                vehicle_number VARCHAR(100) UNIQUE NOT NULL,
                latitude DECIMAL(10, 8),
                longitude DECIMAL(11, 8),
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                availability_status VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_vehicle_number (vehicle_number),
                INDEX idx_availability_status (availability_status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS drivers (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                driver_name VARCHAR(255) NOT NULL,
                phone VARCHAR(32),
                license_number VARCHAR(100) NOT NULL,
                assigned_truck VARCHAR(100),
                assigned_truck_type VARCHAR(50),
                status VARCHAR(50) DEFAULT 'available',
                rating DECIMAL(3, 2) DEFAULT 4.80,
                experience_years INT DEFAULT 0,
                next_available_at TIMESTAMP NULL,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_license_number (license_number),
                INDEX idx_status (status),
                INDEX idx_assigned_truck (assigned_truck)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("SHOW COLUMNS FROM drivers")
        existing_driver_columns = {row[0] for row in cursor.fetchall()}
        driver_column_additions = [
            ("driver_name", "VARCHAR(255) NULL AFTER id"),
            ("phone", "VARCHAR(32) NULL AFTER driver_name"),
            ("license_number", "VARCHAR(100) NULL AFTER phone"),
            ("assigned_truck", "VARCHAR(100) NULL AFTER license_number"),
            ("assigned_truck_type", "VARCHAR(50) NULL AFTER assigned_truck"),
            ("status", "VARCHAR(50) DEFAULT 'available' AFTER assigned_truck_type"),
            ("rating", "DECIMAL(3, 2) DEFAULT 4.80 AFTER status"),
            ("experience_years", "INT DEFAULT 0 AFTER rating"),
            ("next_available_at", "TIMESTAMP NULL AFTER experience_years"),
            ("last_updated", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP AFTER next_available_at"),
            ("created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP AFTER last_updated"),
        ]

        for column_name, column_definition in driver_column_additions:
            if column_name not in existing_driver_columns:
                cursor.execute(f"ALTER TABLE drivers ADD COLUMN {column_name} {column_definition}")
                existing_driver_columns.add(column_name)

        cursor.execute("SELECT COUNT(*) FROM drivers")
        driver_count = cursor.fetchone()[0] or 0
        if driver_count == 0:
            sample_drivers = [
                ("Ramesh Kumar", "+919100000101", "SKDLS-DRV-1001", "AP21BT1201", "12 tyre", "on_trip", 4.9, 12),
                ("Vikram Singh", "+919100000102", "SKDLS-DRV-1002", "TG09AB4508", "14 tyre", "available", 4.8, 9),
                ("Pradeep Reddy", "+919100000103", "SKDLS-DRV-1003", "KA05CD7732", "16 tyre", "available", 4.7, 15),
                ("Mohammed Hassan", "+919100000104", "SKDLS-DRV-1004", "MH12EF8890", "12 tyre", "on_trip", 4.9, 11),
                ("Anil Kumar", "+919100000105", "SKDLS-DRV-1005", "AP16GH3401", "14 tyre", "available", 4.8, 8),
                ("Suresh Patel", "+919100000106", "SKDLS-DRV-1006", "GJ01JK5607", "16 tyre", "maintenance", 4.6, 13),
                ("Rajesh Sharma", "+919100000107", "SKDLS-DRV-1007", "RJ14LM7788", "12 tyre", "available", 4.7, 10),
                ("Arjun Verma", "+919100000108", "SKDLS-DRV-1008", "KA41NP9091", "14 tyre", "on_leave", 4.8, 7),
            ]
            cursor.executemany(
                """
                INSERT INTO drivers
                    (driver_name, phone, license_number, assigned_truck, assigned_truck_type, status, rating, experience_years)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                sample_drivers,
            )

        # Ensure drivers table has password_hash and api_key fields for authentication
        cursor.execute("SHOW COLUMNS FROM drivers")
        existing_driver_columns = {row[0] for row in cursor.fetchall()}
        driver_column_additions = [
            ("password_hash", "VARCHAR(255) NULL AFTER driver_name"),
            ("api_key", "VARCHAR(255) NULL AFTER password_hash"),
        ]

        for column_name, column_definition in driver_column_additions:
            if column_name not in existing_driver_columns:
                cursor.execute(f"ALTER TABLE drivers ADD COLUMN {column_name} {column_definition}")
                existing_driver_columns.add(column_name)

        # Ensure users table has api_key field for alternative auth
        cursor.execute("SHOW COLUMNS FROM users")
        existing_user_columns = {row[0] for row in cursor.fetchall()}
        if "api_key" not in existing_user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN api_key VARCHAR(255) NULL AFTER password_hash")

        # Create chat_history table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(255) NOT NULL,
                user_message TEXT,
                bot_reply TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_session_id (session_id),
                INDEX idx_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # Create conversations table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                conversation_id VARCHAR(255) NOT NULL,
                session_id VARCHAR(255) NOT NULL,
                conversation_json LONGTEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_conversation_id (conversation_id),
                INDEX idx_session_id (session_id),
                INDEX idx_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS booking_sessions (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(255) NOT NULL,
                user_id VARCHAR(255) NOT NULL,
                workflow VARCHAR(80) NOT NULL DEFAULT 'shipment_booking',
                current_step VARCHAR(80) NOT NULL DEFAULT 'collecting_pickup_location',
                draft_json LONGTEXT NOT NULL,
                quote_json LONGTEXT,
                payment_json LONGTEXT,
                shipment_id BIGINT NULL,
                status VARCHAR(50) DEFAULT 'active',
                last_user_message TEXT,
                last_bot_reply TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_booking_sessions_session_id (session_id),
                INDEX idx_booking_sessions_user_id (user_id),
                INDEX idx_booking_sessions_status (status),
                INDEX idx_booking_sessions_updated_at (updated_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # AI action logs table for audit and replay
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ai_action_logs (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(255) NULL,
                role VARCHAR(50) NULL,
                action VARCHAR(255) NULL,
                intent VARCHAR(255) NULL,
                tool_name VARCHAR(255) NULL,
                shipment_id BIGINT NULL,
                status VARCHAR(50) DEFAULT 'success',
                request_payload LONGTEXT,
                response_payload LONGTEXT,
                started_at TIMESTAMP NULL,
                completed_at TIMESTAMP NULL,
                duration_ms INT NULL,
                retry_count INT DEFAULT 0,
                execution_status VARCHAR(50) DEFAULT 'success',
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_ai_action_user (user_id),
                INDEX idx_ai_action_shipment (shipment_id),
                INDEX idx_ai_action_action (action),
                INDEX idx_ai_action_status (status),
                INDEX idx_ai_action_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ai_conversation_memory (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(255) NULL,
                session_id VARCHAR(255) NOT NULL,
                conversation_id VARCHAR(255) NOT NULL,
                memory_json LONGTEXT,
                summary TEXT,
                last_intent VARCHAR(255) NULL,
                last_route VARCHAR(255) NULL,
                confidence DECIMAL(5, 4) DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_ai_conversation_memory_session (session_id, conversation_id),
                INDEX idx_ai_conversation_memory_user (user_id),
                INDEX idx_ai_conversation_memory_session (session_id),
                INDEX idx_ai_conversation_memory_updated_at (updated_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ai_session_context (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(255) NOT NULL,
                user_id VARCHAR(255) NULL,
                role VARCHAR(50) NULL,
                context_json LONGTEXT,
                active_workflow VARCHAR(255) NULL,
                current_step VARCHAR(255) NULL,
                status VARCHAR(50) DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_ai_session_context_session (session_id),
                INDEX idx_ai_session_context_user (user_id),
                INDEX idx_ai_session_context_status (status),
                INDEX idx_ai_session_context_updated_at (updated_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # AI workflow memory for saving in-progress workflow contexts
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ai_workflow_memory (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(255) NOT NULL,
                workflow_type VARCHAR(255) NOT NULL,
                workflow_state LONGTEXT,
                current_step VARCHAR(255),
                context_json LONGTEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_ai_workflow_user_type (user_id, workflow_type),
                INDEX idx_ai_workflow_user (user_id),
                INDEX idx_ai_workflow_type (workflow_type)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("SHOW COLUMNS FROM ai_workflow_memory")
        existing_workflow_memory_columns = {row[0] for row in cursor.fetchall()}
        workflow_memory_column_additions = [
            ("user_id", "VARCHAR(255) NOT NULL DEFAULT 'demo-admin' AFTER id"),
            ("workflow_type", "VARCHAR(255) NOT NULL DEFAULT 'general' AFTER user_id"),
            ("workflow_state", "LONGTEXT NULL AFTER workflow_type"),
            ("current_step", "VARCHAR(255) NULL AFTER workflow_state"),
            ("context_json", "LONGTEXT NULL AFTER current_step"),
            ("updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP AFTER context_json"),
        ]

        for column_name, column_definition in workflow_memory_column_additions:
            if column_name not in existing_workflow_memory_columns:
                cursor.execute(f"ALTER TABLE ai_workflow_memory ADD COLUMN {column_name} {column_definition}")
                existing_workflow_memory_columns.add(column_name)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ai_workflow_retry_queue (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(190) NULL,
                workflow_type VARCHAR(190) NOT NULL,
                action VARCHAR(190) NOT NULL,
                route VARCHAR(190) NOT NULL,
                tool_name VARCHAR(255) NOT NULL,
                payload_json LONGTEXT,
                error_message TEXT,
                execution_status VARCHAR(50) DEFAULT 'error',
                retry_count INT DEFAULT 0,
                max_retries INT DEFAULT 3,
                next_retry_at TIMESTAMP NULL,
                status VARCHAR(50) DEFAULT 'queued',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_retry_job (user_id, workflow_type, action, route),
                INDEX idx_retry_status (status),
                INDEX idx_retry_workflow_type (workflow_type),
                INDEX idx_retry_next_retry_at (next_retry_at),
                INDEX idx_retry_updated_at (updated_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ai_tool_metrics (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(255) NULL,
                role VARCHAR(50) NULL,
                workflow_type VARCHAR(255) NULL,
                action VARCHAR(255) NULL,
                tool_name VARCHAR(255) NULL,
                duration_ms INT NULL,
                retry_count INT DEFAULT 0,
                execution_status VARCHAR(50) DEFAULT 'success',
                ai_latency_ms DECIMAL(10, 2) NULL,
                websocket_latency_ms DECIMAL(10, 2) NULL,
                correlation_id VARCHAR(255) NULL,
                execution_id VARCHAR(255) NULL,
                metadata_json LONGTEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_ai_tool_metrics_tool (tool_name),
                INDEX idx_ai_tool_metrics_status (execution_status),
                INDEX idx_ai_tool_metrics_user (user_id),
                INDEX idx_ai_tool_metrics_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("SHOW COLUMNS FROM ai_tool_metrics")
        existing_tool_metric_columns = {row[0] for row in cursor.fetchall()}
        tool_metric_column_additions = [
            ("user_id", "VARCHAR(255) NULL AFTER id"),
            ("role", "VARCHAR(50) NULL AFTER user_id"),
            ("workflow_type", "VARCHAR(255) NULL AFTER role"),
            ("action", "VARCHAR(255) NULL AFTER workflow_type"),
            ("tool_name", "VARCHAR(255) NULL AFTER action"),
            ("duration_ms", "INT NULL AFTER tool_name"),
            ("retry_count", "INT DEFAULT 0 AFTER duration_ms"),
            ("execution_status", "VARCHAR(50) DEFAULT 'success' AFTER retry_count"),
            ("ai_latency_ms", "DECIMAL(10, 2) NULL AFTER execution_status"),
            ("websocket_latency_ms", "DECIMAL(10, 2) NULL AFTER ai_latency_ms"),
            ("correlation_id", "VARCHAR(255) NULL AFTER websocket_latency_ms"),
            ("execution_id", "VARCHAR(255) NULL AFTER correlation_id"),
            ("metadata_json", "LONGTEXT NULL AFTER execution_id"),
            ("created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP AFTER metadata_json"),
        ]

        for column_name, column_definition in tool_metric_column_additions:
            if column_name not in existing_tool_metric_columns:
                cursor.execute(f"ALTER TABLE ai_tool_metrics ADD COLUMN {column_name} {column_definition}")
                existing_tool_metric_columns.add(column_name)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(255) NULL,
                role VARCHAR(50) NULL,
                action VARCHAR(255) NULL,
                entity_type VARCHAR(255) NULL,
                entity_id VARCHAR(255) NULL,
                status VARCHAR(50) DEFAULT 'success',
                details_json LONGTEXT,
                correlation_id VARCHAR(255) NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_audit_logs_user (user_id),
                INDEX idx_audit_logs_entity (entity_type, entity_id),
                INDEX idx_audit_logs_action (action),
                INDEX idx_audit_logs_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("SHOW COLUMNS FROM audit_logs")
        existing_audit_log_columns = {row[0] for row in cursor.fetchall()}
        audit_log_column_additions = [
            ("user_id", "VARCHAR(255) NULL AFTER id"),
            ("role", "VARCHAR(50) NULL AFTER user_id"),
            ("action", "VARCHAR(255) NULL AFTER role"),
            ("entity_type", "VARCHAR(255) NULL AFTER action"),
            ("entity_id", "VARCHAR(255) NULL AFTER entity_type"),
            ("status", "VARCHAR(50) DEFAULT 'success' AFTER entity_id"),
            ("details_json", "LONGTEXT NULL AFTER status"),
            ("correlation_id", "VARCHAR(255) NULL AFTER details_json"),
            ("created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP AFTER correlation_id"),
        ]

        for column_name, column_definition in audit_log_column_additions:
            if column_name not in existing_audit_log_columns:
                cursor.execute(f"ALTER TABLE audit_logs ADD COLUMN {column_name} {column_definition}")
                existing_audit_log_columns.add(column_name)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS webhook_events (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                source VARCHAR(255) NULL,
                event_type VARCHAR(255) NULL,
                status VARCHAR(50) DEFAULT 'received',
                payload_json LONGTEXT,
                headers_json LONGTEXT,
                response_code INT NULL,
                correlation_id VARCHAR(255) NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_webhook_events_source (source),
                INDEX idx_webhook_events_type (event_type),
                INDEX idx_webhook_events_status (status),
                INDEX idx_webhook_events_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("SHOW COLUMNS FROM webhook_events")
        existing_webhook_event_columns = {row[0] for row in cursor.fetchall()}
        webhook_event_column_additions = [
            ("source", "VARCHAR(255) NULL AFTER id"),
            ("event_type", "VARCHAR(255) NULL AFTER source"),
            ("status", "VARCHAR(50) DEFAULT 'received' AFTER event_type"),
            ("payload_json", "LONGTEXT NULL AFTER status"),
            ("headers_json", "LONGTEXT NULL AFTER payload_json"),
            ("response_code", "INT NULL AFTER headers_json"),
            ("correlation_id", "VARCHAR(255) NULL AFTER response_code"),
            ("created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP AFTER correlation_id"),
        ]

        for column_name, column_definition in webhook_event_column_additions:
            if column_name not in existing_webhook_event_columns:
                cursor.execute(f"ALTER TABLE webhook_events ADD COLUMN {column_name} {column_definition}")
                existing_webhook_event_columns.add(column_name)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(255) NOT NULL UNIQUE,
                prefs_json LONGTEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_user_preferences_user (user_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("SHOW COLUMNS FROM user_preferences")
        existing_user_preference_columns = {row[0] for row in cursor.fetchall()}
        user_preference_column_additions = [
            ("user_id", "VARCHAR(255) NOT NULL DEFAULT 'demo-customer' AFTER id"),
            ("prefs_json", "LONGTEXT NULL AFTER user_id"),
            ("updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP AFTER prefs_json"),
        ]

        for column_name, column_definition in user_preference_column_additions:
            if column_name not in existing_user_preference_columns:
                cursor.execute(f"ALTER TABLE user_preferences ADD COLUMN {column_name} {column_definition}")
                existing_user_preference_columns.add(column_name)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_searches (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(255) NULL,
                query VARCHAR(255) NULL,
                metadata_json LONGTEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_user_searches_user (user_id),
                INDEX idx_user_searches_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("SHOW COLUMNS FROM user_searches")
        existing_user_search_columns = {row[0] for row in cursor.fetchall()}
        user_search_column_additions = [
            ("user_id", "VARCHAR(255) NULL AFTER id"),
            ("query", "VARCHAR(255) NULL AFTER user_id"),
            ("metadata_json", "LONGTEXT NULL AFTER query"),
            ("created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP AFTER metadata_json"),
        ]

        for column_name, column_definition in user_search_column_additions:
            if column_name not in existing_user_search_columns:
                cursor.execute(f"ALTER TABLE user_searches ADD COLUMN {column_name} {column_definition}")
                existing_user_search_columns.add(column_name)

        cursor.execute("SELECT COUNT(*) FROM ai_workflow_memory")
        workflow_memory_count = cursor.fetchone()[0] or 0
        if workflow_memory_count == 0:
            cursor.executemany(
                """
                INSERT INTO ai_workflow_memory (user_id, workflow_type, workflow_state, current_step, context_json, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                """,
                [
                    ("demo-admin", "booking", json.dumps({"step": "collecting_pickup_location", "status": "active"}, ensure_ascii=False), "collecting_pickup_location", json.dumps({"route": "Mumbai to Pune"}, ensure_ascii=False)),
                    ("demo-admin", "tracking", json.dumps({"step": "tracking_live", "shipment_id": 7001}, ensure_ascii=False), "tracking_live", json.dumps({"live": True}, ensure_ascii=False)),
                ],
            )

        cursor.execute("SELECT COUNT(*) FROM ai_tool_metrics")
        ai_tool_metric_count = cursor.fetchone()[0] or 0
        if ai_tool_metric_count == 0:
            cursor.executemany(
                """
                INSERT INTO ai_tool_metrics
                    (user_id, role, workflow_type, action, tool_name, duration_ms, retry_count, execution_status, ai_latency_ms, websocket_latency_ms, correlation_id, execution_id, metadata_json, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                [
                    ("demo-admin", "admin", "booking", "CREATE_SHIPMENT", "create_shipment", 120, 0, "success", 118.0, 0.0, "seed-ai-001", "seed-exec-001", json.dumps({"source": "seed"}, ensure_ascii=False)),
                    ("demo-admin", "admin", "tracking", "TRACK_SHIPMENT", "track_shipment", 92, 1, "success", 90.0, 0.0, "seed-ai-002", "seed-exec-002", json.dumps({"source": "seed"}, ensure_ascii=False)),
                ],
            )

        cursor.execute("SELECT COUNT(*) FROM audit_logs")
        audit_log_count = cursor.fetchone()[0] or 0
        if audit_log_count == 0:
            cursor.executemany(
                """
                INSERT INTO audit_logs (user_id, role, action, entity_type, entity_id, status, details_json, correlation_id, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                [
                    ("demo-admin", "admin", "CREATE", "vehicle", "VEH-12-001", "success", json.dumps({"seed": True}, ensure_ascii=False), "seed-audit-001"),
                    ("demo-admin", "admin", "UPDATE", "workflow_memory", "booking:demo-admin", "success", json.dumps({"seed": True}, ensure_ascii=False), "seed-audit-002"),
                ],
            )

        cursor.execute("SELECT COUNT(*) FROM webhook_events")
        webhook_event_count = cursor.fetchone()[0] or 0
        if webhook_event_count == 0:
            cursor.executemany(
                """
                INSERT INTO webhook_events (source, event_type, status, payload_json, headers_json, response_code, correlation_id, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                [
                    ("razorpay", "payment.captured", "received", json.dumps({"amount": 25000}, ensure_ascii=False), json.dumps({"x-webhook-source": "razorpay"}, ensure_ascii=False), 200, "seed-webhook-001"),
                    ("whatsapp", "message.delivered", "received", json.dumps({"message_id": "msg-seed-1"}, ensure_ascii=False), json.dumps({"x-webhook-source": "whatsapp"}, ensure_ascii=False), 200, "seed-webhook-002"),
                ],
            )

        cursor.execute("SELECT COUNT(*) FROM user_preferences")
        user_preferences_count = cursor.fetchone()[0] or 0
        if user_preferences_count == 0:
            try:
                cursor.execute(
                    """
                    INSERT INTO user_preferences (user_id, prefs_json, updated_at)
                    VALUES (%s, %s, NOW())
                    """,
                    ("demo-customer", json.dumps({
                        "preferred_truck_types": ["12 tyre", "14 tyre"],
                        "preferred_contact_method": "whatsapp",
                        "delivery_notes": "Call before delivery",
                        "dashboard_density": "comfortable",
                    }, ensure_ascii=False)),
                )
            except Error as seed_error:
                logger.warning(f"[db][schema] Skipping user_preferences seed: {seed_error}")

        conn.commit()
        print("[db][info] Schema initialization completed successfully")

    except Error as e:
        if conn:
            conn.rollback()
        print(f"[db][error] Failed to initialize schema: {e}")
        raise
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

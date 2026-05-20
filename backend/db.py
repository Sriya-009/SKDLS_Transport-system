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


REQUIRED_DB_ENV_VARS = ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME")


# Primary DB config uses only DB_* variables.
DB_CONFIG = {
    "host": _get_env("DB_HOST"),
    "user": _get_env("DB_USER"),
    "password": _get_env("DB_PASSWORD"),
    "database": _get_env("DB_NAME"),
}


def validate_db_environment():
    """Raise a clear startup error when any required DB variable is missing."""
    missing = [name for name in REQUIRED_DB_ENV_VARS if not _get_env(name)]
    if missing:
        missing_list = ", ".join(missing)
        raise RuntimeError(
            f"Missing required database environment variables: {missing_list}. "
            "Set DB_HOST, DB_USER, DB_PASSWORD, and DB_NAME before starting the backend."
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


def initialize_schema():
    """Create required tables if they do not exist.

    Tables created:
    - bookings: transport booking records
    - lorries_12_tyre, lorries_14_tyre, lorries_16_tyre: vehicle fleet tracking
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
                user_id VARCHAR(255) NOT NULL,
                source_location VARCHAR(255),
                destination_location VARCHAR(255),
                exact_pickup_location VARCHAR(255),
                exact_delivery_location VARCHAR(255),
                tons INT,
                tyre_type VARCHAR(50),
                distance INT,
                price INT,
                token_amount INT,
                payment_status VARCHAR(50),
                booking_status VARCHAR(50),
                lorry_number VARCHAR(100),
                customer_name VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_user_id (user_id),
                INDEX idx_booking_status (booking_status)
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

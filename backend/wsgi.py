import os
import sys


BASE_DIR = os.path.dirname(__file__)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from config import ProductionConfig, validate_production_environment
from db import initialize_schema, test_db_connection, validate_db_environment, DB_CONFIG
from factory import create_app
from logging_utils import logger
from extensions import socketio
from app import ALLOWED_CORS_ORIGINS, SOCKETIO_ASYNC_MODE, _log_ai_startup_state


app = create_app()


def main():
    try:
        validate_production_environment()
        validate_db_environment()
    except RuntimeError as error:
        logger.error(f"[startup][error] {error}")
        raise SystemExit(1)

    logger.info(
        f"[startup][database] host={DB_CONFIG.get('host')} user={DB_CONFIG.get('user')} database={DB_CONFIG.get('database')}"
    )

    try:
        initialize_schema()
    except Exception as error:
        logger.exception(f"[startup][error] Failed to initialize database schema: {error}")
        raise SystemExit(1)

    try:
        test_conn = test_db_connection()
        if test_conn:
            logger.info(
                f"[startup][database] DB connected host={DB_CONFIG.get('host')} database={DB_CONFIG.get('database')}"
            )
        else:
            logger.error("[startup][database] DB connection failed")
            raise SystemExit(1)
    except Exception as error:
        logger.exception(f"[startup][error] Failed to test database connection: {error}")
        raise SystemExit(1)

    _log_ai_startup_state()

    host = os.getenv("HOST", "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(os.getenv("PORT", "5000"))
    except ValueError:
        port = 5000
    logger.info(
        f"[startup][socketio] Socket.IO initialized async_mode={SOCKETIO_ASYNC_MODE} cors_origins={ALLOWED_CORS_ORIGINS}"
    )
    socketio.run(app, host=host, port=port, debug=False)


if __name__ == "__main__":
    main()

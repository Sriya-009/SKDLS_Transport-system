"""Seed demo data into MySQL database for Transport-system.

Usage:
  python backend/scripts/seed_demo_data.py --host localhost --port 3306 --user root --password secret --database transportdb

If environment variables are set (`DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`), they will be used by default.

This script reads `backend/seeds/demo_seed.sql` and executes all statements.
"""
import os
import argparse
import mysql.connector
from mysql.connector import errorcode

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SQL_PATH = os.path.join(os.path.dirname(THIS_DIR), 'seeds', 'demo_seed.sql')

def read_sql_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

def get_db_conn(host, port, user, password, database):
    return mysql.connector.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        autocommit=True
    )

def execute_sql_script(conn, script):
    cur = conn.cursor()
    try:
        for result in cur.execute(script, multi=True):
            # consume results to ensure execution
            try:
                _ = result.fetchall()
            except Exception:
                pass
        print('SQL script executed successfully.')
    finally:
        cur.close()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default=os.getenv('DB_HOST', 'localhost'))
    parser.add_argument('--port', type=int, default=int(os.getenv('DB_PORT', 3306)))
    parser.add_argument('--user', default=os.getenv('DB_USER', 'root'))
    parser.add_argument('--password', default=os.getenv('DB_PASSWORD', ''))
    parser.add_argument('--database', default=os.getenv('DB_NAME', 'transport'))
    args = parser.parse_args()

    if not os.path.exists(SQL_PATH):
        print(f'ERROR: SQL file not found at {SQL_PATH}')
        return

    script = read_sql_file(SQL_PATH)

    try:
        conn = get_db_conn(args.host, args.port, args.user, args.password, args.database)
    except mysql.connector.Error as err:
        if err.errno == errorcode.ER_BAD_DB_ERROR:
            print(f"Database '{args.database}' does not exist. Create it and retry.")
        else:
            print(f"Error connecting to DB: {err}")
        return

    try:
        execute_sql_script(conn, script)
    finally:
        conn.close()

if __name__ == '__main__':
    main()
"""Seed realistic Indian logistics demo data into core operational tables.

Usage:
    python scripts/seed_demo_data.py
    python scripts/seed_demo_data.py --sql-file scripts/seed_demo_data.sql --validate-only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _ensure_backend_on_path() -> Path:
    backend_root = Path(__file__).resolve().parents[1]
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    return backend_root


def run_seed(sql_file: Path, dry_run: bool = False) -> None:
    backend_root = _ensure_backend_on_path()
    from db import get_db_connection  # pylint: disable=import-error

    if not sql_file.is_absolute():
        sql_file = (backend_root / sql_file).resolve()

    if not sql_file.exists():
        raise FileNotFoundError(f"Seed SQL file not found: {sql_file}")

    sql_text = sql_file.read_text(encoding="utf-8")
    if not sql_text.strip():
        raise RuntimeError(f"Seed SQL file is empty: {sql_file}")

    connection = None
    cursor = None
    try:
        connection = get_db_connection()

        if dry_run:
            print("[seed][dry-run] SQL loaded successfully. No changes applied.")
            return

        cursor = connection.cursor()
        for _ in cursor.execute(sql_text, multi=True):
            pass
        connection.commit()
        print(f"[seed][ok] Seed SQL executed: {sql_file}")
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and getattr(connection, "is_connected", lambda: True)():
            connection.close()


def validate_seed_counts() -> dict[str, int]:
    _ensure_backend_on_path()
    from db import get_db_connection  # pylint: disable=import-error

    checks = {
        "users": "SELECT COUNT(*) FROM users WHERE email LIKE 'seed.user%@skdls.in'",
        "drivers": "SELECT COUNT(*) FROM drivers WHERE license_number LIKE 'DL-SEED-%'",
        "shipments": "SELECT COUNT(*) FROM shipments WHERE user_id LIKE 'seed_user_%'",
        "vehicles": "SELECT COUNT(*) FROM vehicles WHERE vehicle_code LIKE 'SEED-VEH-%'",
        "payments": "SELECT COUNT(*) FROM payments WHERE session_id LIKE 'seed_session_%'",
        "notifications": "SELECT COUNT(*) FROM notifications WHERE correlation_id LIKE 'seed-2026-notify-%'",
        "webhook_events": "SELECT COUNT(*) FROM webhook_events WHERE correlation_id LIKE 'seed-2026-webhook-%'",
        "ai_action_logs": "SELECT COUNT(*) FROM ai_action_logs WHERE user_id LIKE 'seed_user_%'",
        "ai_tool_metrics": "SELECT COUNT(*) FROM ai_tool_metrics WHERE correlation_id LIKE 'seed-2026-metric-%'",
    }

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        results: dict[str, int] = {}
        for table_name, query in checks.items():
            cursor.execute(query)
            count = cursor.fetchone()[0] or 0
            results[table_name] = int(count)
        return results
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and getattr(connection, "is_connected", lambda: True)():
            connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed realistic Indian logistics demo data.")
    parser.add_argument(
        "--sql-file",
        default="scripts/seed_demo_data.sql",
        help="Path to SQL seed file (absolute or relative to backend root).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load and parse SQL file without executing it.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Only validate seeded row counts and skip seeding.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.validate_only:
        run_seed(Path(args.sql_file), dry_run=args.dry_run)
        if args.dry_run:
            return 0

    counts = validate_seed_counts()
    print("[seed][counts]")
    min_required = 25
    failed = False

    for table_name, count in counts.items():
        status = "OK" if count >= min_required else "LOW"
        print(f"  - {table_name}: {count} ({status})")
        if count < min_required:
            failed = True

    if failed:
        print("[seed][error] One or more tables have fewer than 25 demo rows.")
        return 1

    print("[seed][ok] All required tables have at least 25 seeded rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

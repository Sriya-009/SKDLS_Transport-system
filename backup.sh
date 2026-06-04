#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/var/www/transport-system"
BACKUP_DIR="$APP_DIR/backups"
STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE_FILE="$BACKUP_DIR/transport-system-$STAMP.tar.gz"
DB_DUMP_FILE="$BACKUP_DIR/transport-system-$STAMP.sql"

mkdir -p "$BACKUP_DIR"

if [ -f "$APP_DIR/backend/.env" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$APP_DIR/backend/.env"
  set +a
fi

if [ -z "${DB_HOST:-}" ] || [ -z "${DB_USER:-}" ] || [ -z "${DB_PASSWORD:-}" ] || [ -z "${DB_NAME:-}" ]; then
  echo "Database variables are required for backups." >&2
  exit 1
fi

mysqldump \
  --host="$DB_HOST" \
  --user="$DB_USER" \
  --password="$DB_PASSWORD" \
  --databases "$DB_NAME" \
  --single-transaction \
  --routines \
  --triggers \
  --events \
  > "$DB_DUMP_FILE"

tar -czf "$ARCHIVE_FILE" \
  -C "$APP_DIR" \
  backend/.env \
  backend/gunicorn.conf.py \
  deployment \
  frontend/public/404.html \
  frontend/public/50x.html \
  frontend/public/maintenance.html

echo "Backup created: $ARCHIVE_FILE"
#!/usr/bin/env bash
set -euo pipefail

# Simple backup script for database and important files
OUT_DIR="/var/backups/transport-system"
mkdir -p "$OUT_DIR"

TIMESTAMP=$(date -u +"%Y%m%dT%H%M%SZ")

echo "Starting backup: $TIMESTAMP"

# MySQL dump
if [ -n "${DATABASE_HOST:-}" ]; then
  mysqldump -h "${DATABASE_HOST}" -u "${DATABASE_USER}" -p"${DATABASE_PASSWORD}" "${DATABASE_NAME}" > "$OUT_DIR/db-${TIMESTAMP}.sql"
  echo "Database dumped to $OUT_DIR/db-${TIMESTAMP}.sql"
fi

# Copy important configs
tar -czf "$OUT_DIR/configs-${TIMESTAMP}.tar.gz" deployment backend/*.conf || true
echo "Configs archived to $OUT_DIR/configs-${TIMESTAMP}.tar.gz"

# Retention: keep last 14 backups
ls -1t "$OUT_DIR" | tail -n +15 | xargs -r -I{} rm -f "$OUT_DIR/{}"

echo "Backup completed"

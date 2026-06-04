#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/var/www/transport-system"
HEALTH_URL="http://127.0.0.1:8000/api/health"

cd "$APP_DIR"

pm2 reload transport-system-backend --update-env || pm2 start deployment/pm2/ecosystem.config.cjs --update-env
pm2 status transport-system-backend
curl -fsS "$HEALTH_URL" >/dev/null

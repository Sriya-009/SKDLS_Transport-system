#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/var/www/transport-system"
BACKEND_DIR="$APP_DIR/backend"
VENV_DIR="$APP_DIR/.venv"
PM2_CONFIG="$APP_DIR/deployment/pm2/ecosystem.config.cjs"
HEALTH_URL="http://127.0.0.1:8000/api/health"

required_vars=(SECRET_KEY DB_HOST DB_USER DB_PASSWORD DB_NAME CORS_ORIGINS)

validate_env() {
  if [ -f "$BACKEND_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$BACKEND_DIR/.env"
    set +a
  fi

  for var in "${required_vars[@]}"; do
    if [ -z "${!var:-}" ]; then
      echo "Missing required environment variable: $var" >&2
      exit 1
    fi
  done
}

cd "$APP_DIR"

validate_env

git pull --ff-only

if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$BACKEND_DIR/requirements.txt"

npm install
VITE_API_BASE_URL=/api npm run build

cd "$BACKEND_DIR"
python -m py_compile app.py

pm2 start "$PM2_CONFIG" --update-env || pm2 reload "$PM2_CONFIG" --update-env
pm2 save
pm2 status transport-system-backend

curl -fsS "$HEALTH_URL" >/dev/null

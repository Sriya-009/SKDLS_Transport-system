#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/var/www/transport-system"
BACKEND_DIR="$APP_DIR/backend"
VENV_DIR="$APP_DIR/.venv"

cd "$APP_DIR"

git pull --ff-only

if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$BACKEND_DIR/requirements.txt"

cd "$BACKEND_DIR"
python -m py_compile app.py

sudo systemctl daemon-reload
sudo systemctl restart transport-system-backend
sudo systemctl status transport-system-backend --no-pager

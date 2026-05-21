#!/usr/bin/env bash
set -euo pipefail

sudo systemctl restart transport-system-backend
sudo systemctl status transport-system-backend --no-pager

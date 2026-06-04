Production hardening summary

1) Gunicorn
- Configuration in `backend/gunicorn.conf.py` controls workers, threads, timeouts, logging and max-requests.
- Systemd service `deployment/backend.service` uses the config file for consistent startup.

2) Nginx
- Config at `deployment/nginx/transport-system.conf` includes TLS placeholders, rate limiting, security headers, websocket proxying, and static asset caching.
- Replace `your-domain.com` and install certificates with Certbot.

3) HTTPS
- Recommend Certbot (Let's Encrypt). Example:

```bash
sudo apt update && sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com -d www.your-domain.com
```

4) Rate limiting
- Nginx implements `limit_req` and `limit_conn`. Backend uses `flask-limiter` via `API_RATE_LIMIT` env var.

5) Logging
- Application logging configured in `backend/logging_utils.py` (rotating files: application.log, error.log).
- Gunicorn writes access/error logs to `backend/logs` as configured in `gunicorn.conf.py`.

6) PM2
- Ecosystem file: `deployment/pm2/ecosystem.config.cjs` manages backend gunicorn process and frontend static serve fallback.

7) Docker
- `Dockerfile.backend`, `frontend/Dockerfile`, and `docker-compose.yml` provided for containerized deployment (local production stack incl. MySQL).

8) Health checks
- `/health/live` and `/health/ready` endpoints added to backend for liveness/readiness probes.

9) Environment validation
- `backend/validate_env.py` performs minimal required env var checks before startup.

10) Backups
- `deployment/backup.sh` dumps DB and archives configs; set proper credentials and cron job to run regularly.

11) Next steps
- Configure firewall, enable automatic security updates, monitor logs via a central logging system (ELK/CloudWatch), and add automated certificate renewal monitoring.

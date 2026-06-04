# Deployment Guide

## Production Topology

- Frontend: React/Vite build served by Nginx
- Backend: Flask app served by Gunicorn
- Supervisor: PM2 manages the backend process
- Database: MySQL or AWS RDS MySQL
- Hosting: AWS EC2

## Prerequisites

- Ubuntu or Amazon Linux EC2 instance
- Domain name pointed to the instance
- MySQL database available locally or through AWS RDS
- SSL certificate from Certbot

## Exact EC2 Commands

```bash
sudo apt update -y
sudo apt install -y git nginx python3 python3-pip python3-venv build-essential curl
sudo apt install -y certbot python3-certbot-nginx
sudo npm install -g pm2
sudo systemctl enable nginx
sudo systemctl start nginx
```

```bash
cd /var/www
sudo git clone https://github.com/YOUR_ORG/YOUR_REPO.git transport-system
sudo chown -R ubuntu:ubuntu /var/www/transport-system
cd /var/www/transport-system
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r backend/requirements.txt
npm install
```

## Exact Nginx Setup

Copy the bundled reverse proxy config to your server:

```bash
sudo cp deployment/nginx/transport-system.conf /etc/nginx/sites-available/transport-system.conf
sudo ln -sf /etc/nginx/sites-available/transport-system.conf /etc/nginx/sites-enabled/transport-system.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
```

The config serves the frontend build from `frontend/dist`, proxies `/api/` to Gunicorn on `127.0.0.1:8000`, proxies Socket.IO at `/socket.io/`, enables gzip, applies security headers, and serves branded 404/500/maintenance pages.

## Exact Gunicorn Commands

```bash
cd /var/www/transport-system/backend
gunicorn -c gunicorn.conf.py app:app
```

Recommended production environment variables are already reflected in `backend/gunicorn.conf.py` and `backend/.env.example`.

## Exact PM2 Commands

```bash
pm2 start deployment/pm2/ecosystem.config.cjs --update-env
pm2 save
pm2 status transport-system-backend
```

To reload after code changes:

```bash
pm2 reload transport-system-backend --update-env
```

## Frontend Build Optimization

```bash
cd /var/www/transport-system
VITE_API_BASE_URL=/api npm run build
sudo systemctl reload nginx
```

## SSL Setup Steps

```bash
sudo certbot --nginx -d your-domain.com -d www.your-domain.com
sudo certbot renew --dry-run
```

## Restart / Update Workflow

1. Run `./backup.sh` before applying deployment changes.
2. Pull the latest code or upload the release bundle.
3. Run `./deploy.sh` to validate env, build the frontend, and restart the backend process.
4. Use `./restart.sh` after config-only changes or to recover a running app.
5. Verify `curl http://127.0.0.1:8000/api/health` and `curl http://127.0.0.1:8000/api/ready` on the server.

## Production Checklist

- `backend/.env` contains production values and no placeholder secrets
- `frontend/public/404.html`, `50x.html`, and `maintenance.html` are present in the build output
- Nginx config tests cleanly with `nginx -t`
- Gunicorn starts on `127.0.0.1:8000`
- PM2 reports `transport-system-backend` as online
- `/api/health` returns `200`
- `/api/ready` returns `200`
- HTTPS certs are issued and renewed successfully
- `VITE_API_BASE_URL=/api npm run build` completes without errors

## Logs And Monitoring

- Gunicorn logs: `backend/logs/access.log` and `backend/logs/error.log`
- PM2 process state: `pm2 status transport-system-backend`
- Nginx logs: system defaults unless overridden
- Health checks: `/api/health` and `/api/ready`

## Environment Variables

Place the backend variables in `backend/.env` on the server.

### Backend

- `FLASK_ENV=production`
- `SECRET_KEY`
- `JWT_SECRET_KEY`
- `GEMINI_API_KEY`
- `GEMINI_MODEL_NAME`
- `DB_HOST`
- `DB_USER`
- `DB_PASSWORD`
- `DB_NAME`
- `DB_PORT`
- `CORS_ORIGINS`
- `FRONTEND_ORIGIN`
- `SESSION_COOKIE_SECURE=true`
- `SESSION_COOKIE_SAMESITE=Lax`
- `JWT_COOKIE_SECURE=true`
- `JWT_COOKIE_SAMESITE=Lax`
- `JWT_COOKIE_CSRF_PROTECT=true`
- `API_RATE_LIMIT=120 per minute`
- `RATELIMIT_STORAGE_URI=memory://`
- `MAX_CONTENT_LENGTH_BYTES=10485760`
- `GUNICORN_BIND=127.0.0.1:8000`
- `GUNICORN_WORKER_CLASS=gthread`
- `GUNICORN_WORKERS=3`
- `GUNICORN_THREADS=4`
- `GUNICORN_TIMEOUT=120`
- `GUNICORN_KEEPALIVE=5`
- `GUNICORN_GRACEFUL_TIMEOUT=30`
- `GUNICORN_MAX_REQUESTS=1000`
- `GUNICORN_MAX_REQUESTS_JITTER=100`
- `GUNICORN_WORKER_TMP_DIR=/tmp`
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_WHATSAPP_FROM_NUMBER`
- `SOCKETIO_ASYNC_MODE=threading`
- `HOST=127.0.0.1`
- `PORT=8000`

### Frontend

- `VITE_API_BASE_URL=/api`

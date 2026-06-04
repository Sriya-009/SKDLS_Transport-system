# AWS Production Deployment

## Target architecture

- React/Vite frontend built on EC2 and served by Nginx
- Flask backend running on EC2 with Gunicorn behind Nginx
- PM2 used to supervise the Gunicorn process
- AWS RDS MySQL as the database
- Twilio WhatsApp, Razorpay, and Gemini API accessed from the Flask backend

## Environment variables

Set these in `backend/.env` on EC2:

- `FLASK_ENV=production`
- `SECRET_KEY`
- `GEMINI_API_KEY`
- `GEMINI_MODEL_NAME`
- `DB_HOST`
- `DB_USER`
- `DB_PASSWORD`
- `DB_NAME`
- `DB_PORT`
- `CORS_ORIGINS`
- `SESSION_COOKIE_SECURE=true`
- `SESSION_COOKIE_SAMESITE=Lax`
- `API_RATE_LIMIT=120 per minute`
- `RATELIMIT_STORAGE_URI=memory://`
- `MAX_CONTENT_LENGTH_BYTES=10485760`
- `GUNICORN_BIND=127.0.0.1:8000`
- `GUNICORN_WORKERS=3`
- `GUNICORN_THREADS=4`
- `GUNICORN_TIMEOUT=120`
- `GUNICORN_KEEPALIVE=5`
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_WHATSAPP_FROM_NUMBER`

Set this in the frontend build environment:

- `VITE_API_BASE_URL=/api`

## EC2 setup

1. Launch an Amazon Linux 2023 or Ubuntu EC2 instance.
2. Open security groups for:
   - `22` from your IP for SSH
   - `80` from the internet for Nginx
   - `443` from the internet for HTTPS
   - `8000` only if you want to test Gunicorn directly; not required in production
3. Install system packages.

### Exact EC2 commands

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

## Backend deployment

1. Copy `deployment/nginx/transport-system.conf` to `/etc/nginx/sites-available/transport-system.conf`.
2. Enable the site and reload Nginx.
3. Start Gunicorn with PM2 using `deployment/pm2/ecosystem.config.cjs`.
4. Place `backend/.env` on the EC2 instance.

### Exact Nginx config

Use the file in [deployment/nginx/transport-system.conf](deployment/nginx/transport-system.conf).

### Exact PM2 config

Use the file in [deployment/pm2/ecosystem.config.cjs](deployment/pm2/ecosystem.config.cjs).

### Exact backend commands

```bash
pm2 start deployment/pm2/ecosystem.config.cjs --update-env
pm2 save
pm2 status transport-system-backend
```

```bash
sudo cp deployment/nginx/transport-system.conf /etc/nginx/sites-available/transport-system.conf
sudo ln -sf /etc/nginx/sites-available/transport-system.conf /etc/nginx/sites-enabled/transport-system.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
```

## Gunicorn

The backend is ready for:

```bash
cd /var/www/transport-system/backend
gunicorn -c gunicorn.conf.py app:app
```

`backend/gunicorn.conf.py` keeps the same worker and timeout values while routing logs to `backend/logs/`.

## Frontend deployment

1. Build the frontend with `VITE_API_BASE_URL=/api`.
2. Copy `frontend/dist/` into `/var/www/transport-system/frontend/dist` so Nginx can serve it.
3. Reload Nginx after each build.

### Exact build commands

```bash
cd /var/www/transport-system
npm install
npm run build
sudo systemctl reload nginx
```

## HTTPS

- Use Certbot with the Nginx plugin on the EC2 instance.

### Exact Certbot commands

```bash
sudo certbot --nginx -d your-domain.com -d www.your-domain.com
sudo certbot renew --dry-run
```

### Nginx SSL config

Use the file in [deployment/nginx/transport-system.conf](deployment/nginx/transport-system.conf). Replace the `your-domain.com` placeholders with your real domain before deploying.

## Logging

- Application logs rotate in `backend/logs/application.log`
- Error logs rotate in `backend/logs/error.log`
- Gunicorn access and error logs are configured in `backend/gunicorn.conf.py`
- Nginx access and error logs use the system defaults unless you override them in the server block

## PM2

PM2 keeps the backend alive and restarts it on failure.

### Exact PM2 commands

```bash
pm2 start deployment/pm2/ecosystem.config.cjs --update-env
pm2 save
pm2 startup systemd -u ubuntu --hp /home/ubuntu
```

To reload after code changes:

```bash
pm2 reload transport-system-backend --update-env
```

## Validation

Run these before shipping:

```bash
npm install
python -m py_compile backend/app.py backend/db.py backend/whatsapp_service.py
npm run build
```

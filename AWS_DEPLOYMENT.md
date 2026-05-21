# AWS Production Deployment

## Target architecture

- React/Vite frontend hosted in S3 and distributed by CloudFront
- Flask backend running on EC2 with Gunicorn behind Nginx
- AWS RDS MySQL as the database
- Gemini API accessed from the Flask backend via `GEMINI_API_KEY`

## Environment variables

Set these in `backend/.env` on EC2:

- `GEMINI_API_KEY`
- `DB_HOST`
- `DB_USER`
- `DB_PASSWORD`
- `DB_NAME`
- `DB_PORT`
- `CORS_ORIGINS`
- `SECRET_KEY`
- `SESSION_COOKIE_SECURE=true`

Set this in the frontend build environment:

- `VITE_API_BASE_URL=https://api.your-domain.com`

## EC2 setup

1. Launch an Amazon Linux 2023 or Ubuntu EC2 instance.
2. Attach an IAM role if you plan to deploy from EC2 to S3.
3. Open security groups for:
   - `22` from your IP for SSH
   - `80` from the internet for Nginx
   - `443` from the internet for HTTPS
   - `5000` only if you want to test Gunicorn directly; not required in production
4. Install system packages.

### Exact EC2 commands

```bash
sudo dnf update -y || sudo apt update -y
sudo dnf install -y git nginx python3 python3-pip python3-devel || sudo apt install -y git nginx python3 python3-pip python3-venv build-essential
sudo systemctl enable nginx
sudo systemctl start nginx
```

```bash
cd /var/www
sudo git clone https://github.com/YOUR_ORG/YOUR_REPO.git transport-system
sudo chown -R ec2-user:ec2-user /var/www/transport-system
cd /var/www/transport-system
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r backend/requirements.txt
```

## Backend deployment

1. Copy `deployment/backend.service` to `/etc/systemd/system/transport-system-backend.service`.
2. Copy `deployment/nginx/transport-system.conf` to `/etc/nginx/conf.d/transport-system.conf`.
3. Place `backend/.env` on the EC2 instance.
4. Start Gunicorn through systemd.

### Exact systemd service file

Use the file in [deployment/backend.service](deployment/backend.service).

### Exact Nginx config

Use the file in [deployment/nginx/transport-system.conf](deployment/nginx/transport-system.conf).

### Exact backend commands

```bash
sudo cp deployment/backend.service /etc/systemd/system/transport-system-backend.service
sudo systemctl daemon-reload
sudo systemctl enable transport-system-backend
sudo systemctl start transport-system-backend
sudo systemctl status transport-system-backend --no-pager
```

```bash
sudo cp deployment/nginx/transport-system.conf /etc/nginx/conf.d/transport-system.conf
sudo nginx -t
sudo systemctl restart nginx
```

## Gunicorn

The backend is ready for:

```bash
gunicorn app:app --workers 3 --timeout 120
```

`backend/gunicorn.conf.py` keeps the same worker and timeout values while routing logs to `backend/logs/`.

## Frontend deployment

1. Build the frontend with `VITE_API_BASE_URL` pointing at the backend domain.
2. Upload `frontend/dist/` to the S3 bucket configured for static website hosting or private origin access.
3. Put CloudFront in front of the bucket.

### Exact S3 deployment steps

```bash
cd frontend
npm install
VITE_API_BASE_URL=https://api.your-domain.com npm run build
aws s3 sync dist s3://your-frontend-bucket --delete
```

### CloudFront setup

1. Create an S3 bucket for the frontend build output.
2. Use Origin Access Control so the bucket stays private.
3. Create a CloudFront distribution with the S3 bucket as the origin.
4. Set the default root object to `index.html`.
5. Configure a custom error response for SPA routing to return `index.html` on `403` and `404`.

## HTTPS

- For CloudFront, use ACM in `us-east-1`.
- For the EC2 backend, either terminate TLS on Nginx with a certificate from Let’s Encrypt or place an ALB in front of EC2 with ACM.
- Update `CORS_ORIGINS` to the final frontend domain.

## Logging

- Application logs rotate in `backend/logs/application.log`
- Error logs rotate in `backend/logs/error.log`
- Gunicorn access and error logs are configured in `backend/gunicorn.conf.py`

## Validation

Run these before shipping:

```bash
python -m py_compile backend/app.py
npm run build
```

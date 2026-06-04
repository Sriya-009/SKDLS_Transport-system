# Setup Instructions

## Local Development

### 1. Install dependencies

```bash
npm install
cd frontend
npm install
cd ..
python -m pip install -r backend/requirements.txt
```

### 2. Configure environment variables

Create `backend/.env` and set the backend variables listed in the README and deployment guide.

Set the frontend base URL when needed:

```bash
VITE_API_BASE_URL=http://localhost:5000
```

### 3. Run the backend

```bash
cd backend
python app.py
```

If your environment uses Gunicorn locally:

```bash
cd backend
gunicorn -c gunicorn.conf.py app:app
```

### 4. Run the frontend

```bash
cd frontend
npm run dev
```

### 5. Build for production

```bash
cd frontend
VITE_API_BASE_URL=/api npm run build
```

## Validation Checklist

- Backend starts without import or syntax errors
- Frontend dev server loads the landing page
- Login, shipment booking, tracking, and admin routes remain reachable
- Razorpay, Socket.IO, and MySQL environment variables are configured
- Production build completes successfully

# Admin Management Dashboard

## Architecture

- **Backend**: Flask + Flask-JWT-Extended + Flask-SocketIO.
- **Database**: MySQL `drivers` and `users` tables now support `api_key` fields; `drivers` also stores `password_hash` for driver login.
- **Frontend**: React + Vite admin workspace with search, filters, CRUD modals, toasts, and copy-to-clipboard actions.

## Admin APIs

- `GET /api/admin/dashboard`
- `GET /api/admin/drivers`
- `POST /api/admin/drivers`
- `PUT /api/admin/drivers/:id`
- `DELETE /api/admin/drivers/:id`
- `POST /api/admin/drivers/:id/generate-api-key`
- `POST /api/admin/drivers/:id/reset-password`
- `GET /api/admin/users`

## Security

- All admin routes use JWT auth plus `require_role("admin")`.
- GPS ingest accepts either a driver JWT or a valid device API key.
- Socket.IO connections require JWT handshake auth or a valid API key.

## Run

```powershell
pip install -r backend/requirements.txt
npm install --prefix frontend
python backend/app.py
npm run dev --prefix frontend
```

## Notes

- Driver API keys are generated from the admin dashboard.
- Driver passwords can be reset with a manual password or a generated temporary password.
- The dashboard is styled to match the existing dark logistics control tower UI.

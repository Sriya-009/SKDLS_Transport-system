# Demo Commands

## Local Demo

```bash
npm run build
```

```bash
cd backend
python app.py
```

```bash
cd frontend
npm run dev
```

## Production Demo

```bash
pm2 start deployment/pm2/ecosystem.config.cjs --update-env
```

```bash
pm2 reload transport-system-backend --update-env
```

```bash
sudo systemctl reload nginx
```

## Useful App Routes

- `/` - premium landing page
- `/login` - login screen
- `/signup` - signup screen
- `/shipments` - booking workflow
- `/tracking` - live tracking view
- `/admin` - admin dashboard
- `/support` - AI assistant

## Demo Notes

- Enable demo data from the landing page or app shell if live backend data is unavailable.
- Keep the browser on the landing page first to showcase the product story.
- Use the booking flow, tracking route, and admin dashboard in that order for a strong demo narrative.

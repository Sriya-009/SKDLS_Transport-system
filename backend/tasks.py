import os

from celery_app import celery_app


@celery_app.task(name="tasks.capture_health_snapshot", autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def capture_health_snapshot():
    from app import _build_monitoring_dashboard_payload

    payload = _build_monitoring_dashboard_payload(days=1)
    return {
        "status": "success",
        "health": payload.get("health") or {},
        "summary": payload.get("summary") or {},
    }


@celery_app.task(name="tasks.scan_retry_queue", autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def scan_retry_queue():
    from db import get_workflow_retry_jobs

    jobs = get_workflow_retry_jobs(status="queued", limit=100)
    retrying = get_workflow_retry_jobs(status="retrying", limit=100)
    return {
        "status": "success",
        "queued": len(jobs),
        "retrying": len(retrying),
        "jobs": jobs[:20],
    }


@celery_app.task(name="tasks.purge_old_audit_logs", autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def purge_old_audit_logs():
    from db import delete_audit_logs_older_than

    retention_days = int(os.getenv("AUDIT_LOG_RETENTION_DAYS", "180"))
    deleted = delete_audit_logs_older_than(retention_days)
    return {"status": "success", "deleted": deleted, "retention_days": retention_days}


@celery_app.task(name="tasks.replay_webhook_event", autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 5})
def replay_webhook_event(event_id):
    from db import update_webhook_event_status

    row = update_webhook_event_status(event_id, "retrying", response_code=None)
    return {"status": "success" if row else "not_found", "event": row}

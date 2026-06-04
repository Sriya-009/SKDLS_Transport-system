import os

from celery import Celery


def make_celery_app():
    broker_url = os.getenv("CELERY_BROKER_URL") or os.getenv("REDIS_URL") or "redis://localhost:6379/0"
    result_backend = os.getenv("CELERY_RESULT_BACKEND") or os.getenv("REDIS_URL") or "redis://localhost:6379/1"
    celery = Celery("transport_system", broker=broker_url, backend=result_backend)
    celery.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone=os.getenv("CELERY_TIMEZONE", "UTC"),
        enable_utc=True,
        worker_prefetch_multiplier=int(os.getenv("CELERY_WORKER_PREFETCH_MULTIPLIER", "1")),
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_default_queue=os.getenv("CELERY_DEFAULT_QUEUE", "transport-default"),
        broker_connection_retry_on_startup=True,
        beat_schedule={
            "transport-health-snapshot": {
                "task": "tasks.capture_health_snapshot",
                "schedule": float(os.getenv("HEALTH_SNAPSHOT_INTERVAL_SECONDS", "60")),
            },
            "transport-retry-queue-scan": {
                "task": "tasks.scan_retry_queue",
                "schedule": float(os.getenv("RETRY_QUEUE_SCAN_INTERVAL_SECONDS", "120")),
            },
            "transport-audit-retention": {
                "task": "tasks.purge_old_audit_logs",
                "schedule": float(os.getenv("AUDIT_RETENTION_SCAN_INTERVAL_SECONDS", "86400")),
            },
        },
    )
    return celery


celery_app = make_celery_app()

from app import _auto_save_chat as legacy_auto_save_chat
from app import _monitoring_after_request as legacy_monitoring_after_request
from app import _monitoring_before_request as legacy_monitoring_before_request


def register_monitoring_hooks(app):
    app.before_request(legacy_monitoring_before_request)
    app.after_request(legacy_monitoring_after_request)
    app.after_request(legacy_auto_save_chat)

from flask import Blueprint

from app import admin_monitoring_dashboard as legacy_admin_monitoring_dashboard
from app import prometheus_metrics as legacy_prometheus_metrics
from middleware.auth import require_auth, require_role
from utils.blueprints import bind_route


monitoring_bp = Blueprint("monitoring_bp", __name__)

admin_guard = [require_role("admin"), require_auth]

bind_route(monitoring_bp, "/api/admin/monitoring", legacy_admin_monitoring_dashboard, "api_admin_monitoring_dashboard", ["GET"], admin_guard)
bind_route(monitoring_bp, "/admin/monitoring", legacy_admin_monitoring_dashboard, "admin_monitoring_dashboard", ["GET"], admin_guard)
bind_route(monitoring_bp, "/metrics", legacy_prometheus_metrics, "prometheus_metrics", ["GET"])

from flask import Blueprint

from app import health_check as legacy_health_check
from app import readiness_check as legacy_readiness_check
from app import webhook_event_ingest as legacy_webhook_event_ingest
from utils.blueprints import bind_route


system_bp = Blueprint("system_bp", __name__)

bind_route(system_bp, "/api/health", legacy_health_check, "api_health_check", ["GET"])
bind_route(system_bp, "/health", legacy_health_check, "health_check", ["GET"])
bind_route(system_bp, "/api/ready", legacy_readiness_check, "api_readiness_check", ["GET"])
bind_route(system_bp, "/ready", legacy_readiness_check, "readiness_check", ["GET"])
bind_route(system_bp, "/api/webhooks/events", legacy_webhook_event_ingest, "api_webhook_event_ingest", ["POST"])

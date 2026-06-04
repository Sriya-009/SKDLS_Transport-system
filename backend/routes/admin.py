from flask import Blueprint

from app import admin_dashboard as legacy_admin_dashboard
from app import admin_control_tower as legacy_admin_control_tower
from app import admin_drivers_create as legacy_admin_drivers_create
from app import admin_drivers_delete as legacy_admin_drivers_delete
from app import admin_drivers_list as legacy_admin_drivers_list
from app import admin_driver_generate_api_key as legacy_admin_driver_generate_api_key
from app import admin_driver_reset_password as legacy_admin_driver_reset_password
from app import admin_audit_logs as legacy_admin_audit_logs
from app import admin_audit_logs_delete as legacy_admin_audit_logs_delete
from app import admin_ai_action_log_get as legacy_admin_ai_action_log_get
from app import admin_ai_action_logs as legacy_admin_ai_action_logs
from app import admin_ai_metrics as legacy_admin_ai_metrics
from app import admin_ai_replay as legacy_admin_ai_replay
from app import admin_ai_workflow_retries as legacy_admin_ai_workflow_retries
from app import admin_ai_workflow_retry_update as legacy_admin_ai_workflow_retry_update
from app import admin_ai_workflows as legacy_admin_ai_workflows
from app import admin_vehicles_create as legacy_admin_vehicles_create
from app import admin_vehicles_delete as legacy_admin_vehicles_delete
from app import admin_vehicles_list as legacy_admin_vehicles_list
from app import admin_vehicles_update as legacy_admin_vehicles_update
from app import admin_webhook_events as legacy_admin_webhook_events
from app import admin_webhook_event_retry as legacy_admin_webhook_event_retry
from app import admin_shipments_list as legacy_admin_shipments_list
from app import admin_update_shipment_status as legacy_admin_update_shipment_status
from app import admin_users_list as legacy_admin_users_list
from app import update_admin_driver as legacy_update_admin_driver
from app import update_admin_truck as legacy_update_admin_truck
from middleware.auth import require_auth, require_role
from utils.blueprints import bind_route


admin_bp = Blueprint("admin_bp", __name__)

admin_guard = [require_role("admin"), require_auth]

bind_route(admin_bp, "/api/admin/dashboard", legacy_admin_dashboard, "api_admin_dashboard", ["GET"], admin_guard)
bind_route(admin_bp, "/admin/dashboard", legacy_admin_dashboard, "admin_dashboard", ["GET"], admin_guard)
bind_route(admin_bp, "/api/admin/control-tower", legacy_admin_control_tower, "api_admin_control_tower", ["GET"], admin_guard)
bind_route(admin_bp, "/admin/control-tower", legacy_admin_control_tower, "admin_control_tower", ["GET"], admin_guard)
bind_route(admin_bp, "/api/admin/drivers/<int:driver_id>", legacy_update_admin_driver, "api_update_admin_driver", ["PUT", "PATCH"], admin_guard)
bind_route(admin_bp, "/admin/drivers/<int:driver_id>", legacy_update_admin_driver, "update_admin_driver", ["PUT", "PATCH"], admin_guard)
bind_route(admin_bp, "/admin/trucks/<truck_type_code>/<vehicle_number>", legacy_update_admin_truck, "update_admin_truck", ["PATCH"], admin_guard)
bind_route(admin_bp, "/api/admin/drivers", legacy_admin_drivers_list, "api_admin_drivers_list", ["GET"], admin_guard)
bind_route(admin_bp, "/admin/drivers", legacy_admin_drivers_list, "admin_drivers_list", ["GET"], admin_guard)
bind_route(admin_bp, "/api/admin/drivers", legacy_admin_drivers_create, "api_admin_drivers_create", ["POST"], admin_guard)
bind_route(admin_bp, "/admin/drivers", legacy_admin_drivers_create, "admin_drivers_create", ["POST"], admin_guard)
bind_route(admin_bp, "/api/admin/drivers/<int:driver_id>", legacy_admin_drivers_delete, "api_admin_drivers_delete", ["DELETE"], admin_guard)
bind_route(admin_bp, "/admin/drivers/<int:driver_id>", legacy_admin_drivers_delete, "admin_drivers_delete", ["DELETE"], admin_guard)
bind_route(admin_bp, "/api/admin/drivers/<int:driver_id>/generate-api-key", legacy_admin_driver_generate_api_key, "api_admin_driver_generate_api_key", ["POST"], admin_guard)
bind_route(admin_bp, "/admin/drivers/<int:driver_id>/generate-api-key", legacy_admin_driver_generate_api_key, "admin_driver_generate_api_key", ["POST"], admin_guard)
bind_route(admin_bp, "/api/admin/drivers/<int:driver_id>/reset-password", legacy_admin_driver_reset_password, "api_admin_driver_reset_password", ["POST"], admin_guard)
bind_route(admin_bp, "/admin/drivers/<int:driver_id>/reset-password", legacy_admin_driver_reset_password, "admin_driver_reset_password", ["POST"], admin_guard)
bind_route(admin_bp, "/api/admin/users", legacy_admin_users_list, "api_admin_users_list", ["GET"], admin_guard)
bind_route(admin_bp, "/admin/users", legacy_admin_users_list, "admin_users_list", ["GET"], admin_guard)
bind_route(admin_bp, "/api/admin/ai/action-logs", legacy_admin_ai_action_logs, "api_admin_ai_action_logs", ["GET"], admin_guard)
bind_route(admin_bp, "/admin/ai/action-logs", legacy_admin_ai_action_logs, "admin_ai_action_logs", ["GET"], admin_guard)
bind_route(admin_bp, "/api/admin/ai/action-logs/<int:log_id>", legacy_admin_ai_action_log_get, "api_admin_ai_action_log_get", ["GET"], admin_guard)
bind_route(admin_bp, "/admin/ai/action-logs/<int:log_id>", legacy_admin_ai_action_log_get, "admin_ai_action_log_get", ["GET"], admin_guard)
bind_route(admin_bp, "/api/admin/ai/workflows", legacy_admin_ai_workflows, "api_admin_ai_workflows", ["GET"], admin_guard)
bind_route(admin_bp, "/admin/ai/workflows", legacy_admin_ai_workflows, "admin_ai_workflows", ["GET"], admin_guard)
bind_route(admin_bp, "/api/admin/ai/workflow-retries", legacy_admin_ai_workflow_retries, "api_admin_ai_workflow_retries", ["GET"], admin_guard)
bind_route(admin_bp, "/admin/ai/workflow-retries", legacy_admin_ai_workflow_retries, "admin_ai_workflow_retries", ["GET"], admin_guard)
bind_route(admin_bp, "/api/admin/ai/workflow-retries/<int:job_id>", legacy_admin_ai_workflow_retry_update, "api_admin_ai_workflow_retry_update", ["PATCH"], admin_guard)
bind_route(admin_bp, "/admin/ai/workflow-retries/<int:job_id>", legacy_admin_ai_workflow_retry_update, "admin_ai_workflow_retry_update", ["PATCH"], admin_guard)
bind_route(admin_bp, "/api/admin/ai/metrics", legacy_admin_ai_metrics, "api_admin_ai_metrics", ["GET"], admin_guard)
bind_route(admin_bp, "/admin/ai/metrics", legacy_admin_ai_metrics, "admin_ai_metrics", ["GET"], admin_guard)
bind_route(admin_bp, "/api/admin/ai/replay/<int:workflow_id>", legacy_admin_ai_replay, "api_admin_ai_replay", ["POST"], admin_guard)
bind_route(admin_bp, "/admin/ai/replay/<int:workflow_id>", legacy_admin_ai_replay, "admin_ai_replay", ["POST"], admin_guard)
bind_route(admin_bp, "/api/admin/vehicles", legacy_admin_vehicles_list, "api_admin_vehicles_list", ["GET"], admin_guard)
bind_route(admin_bp, "/admin/vehicles", legacy_admin_vehicles_list, "admin_vehicles_list", ["GET"], admin_guard)
bind_route(admin_bp, "/api/admin/vehicles", legacy_admin_vehicles_create, "api_admin_vehicles_create", ["POST"], admin_guard)
bind_route(admin_bp, "/admin/vehicles", legacy_admin_vehicles_create, "admin_vehicles_create", ["POST"], admin_guard)
bind_route(admin_bp, "/api/admin/vehicles/<string:vehicle_code>", legacy_admin_vehicles_update, "api_admin_vehicles_update", ["PUT", "PATCH"], admin_guard)
bind_route(admin_bp, "/admin/vehicles/<string:vehicle_code>", legacy_admin_vehicles_update, "admin_vehicles_update", ["PUT", "PATCH"], admin_guard)
bind_route(admin_bp, "/api/admin/vehicles/<string:vehicle_code>", legacy_admin_vehicles_delete, "api_admin_vehicles_delete", ["DELETE"], admin_guard)
bind_route(admin_bp, "/admin/vehicles/<string:vehicle_code>", legacy_admin_vehicles_delete, "admin_vehicles_delete", ["DELETE"], admin_guard)
bind_route(admin_bp, "/api/admin/audit-logs", legacy_admin_audit_logs, "api_admin_audit_logs", ["GET"], admin_guard)
bind_route(admin_bp, "/admin/audit-logs", legacy_admin_audit_logs, "admin_audit_logs", ["GET"], admin_guard)
bind_route(admin_bp, "/api/admin/audit-logs", legacy_admin_audit_logs_delete, "api_admin_audit_logs_delete", ["DELETE"], admin_guard)
bind_route(admin_bp, "/admin/audit-logs", legacy_admin_audit_logs_delete, "admin_audit_logs_delete", ["DELETE"], admin_guard)
bind_route(admin_bp, "/api/admin/webhooks/events", legacy_admin_webhook_events, "api_admin_webhook_events", ["GET"], admin_guard)
bind_route(admin_bp, "/admin/webhooks/events", legacy_admin_webhook_events, "admin_webhook_events", ["GET"], admin_guard)
bind_route(admin_bp, "/api/admin/webhooks/events/<int:event_id>/retry", legacy_admin_webhook_event_retry, "api_admin_webhook_event_retry", ["PATCH"], admin_guard)
bind_route(admin_bp, "/admin/webhooks/events/<int:event_id>/retry", legacy_admin_webhook_event_retry, "admin_webhook_event_retry", ["PATCH"], admin_guard)
bind_route(admin_bp, "/api/admin/shipments", legacy_admin_shipments_list, "api_admin_shipments_list", ["GET"], admin_guard)
bind_route(admin_bp, "/api/admin/shipments/<int:shipment_id>/status", legacy_admin_update_shipment_status, "api_admin_update_shipment_status", ["PUT", "PATCH"], admin_guard)

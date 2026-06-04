from flask import Blueprint
from flask_jwt_extended import jwt_required

from app import booking_history as legacy_booking_history
from app import booking_invoice as legacy_booking_invoice
from app import booking_timeline as legacy_booking_timeline
from app import create_booking as legacy_create_booking
from app import fare_estimate as legacy_fare_estimate
from app import get_booking as legacy_get_booking
from app import list_bookings as legacy_list_bookings
from app import update_booking_status as legacy_update_booking_status
from app import vehicle_recommendation as legacy_vehicle_recommendation
from extensions import limiter
from middleware.auth import require_roles
from utils.blueprints import bind_route


bookings_bp = Blueprint("bookings_bp", __name__)

bind_route(bookings_bp, "/api/bookings", legacy_create_booking, "api_create_booking", ["POST"], [limiter.limit("10 per minute")])
bind_route(bookings_bp, "/bookings", legacy_create_booking, "create_booking", ["POST"], [limiter.limit("10 per minute")])
bind_route(bookings_bp, "/api/bookings", legacy_list_bookings, "api_list_bookings", ["GET"])
bind_route(bookings_bp, "/bookings", legacy_list_bookings, "list_bookings", ["GET"])
bind_route(bookings_bp, "/api/bookings/<int:booking_id>", legacy_get_booking, "api_get_booking", ["GET"])
bind_route(bookings_bp, "/bookings/<int:booking_id>", legacy_get_booking, "get_booking", ["GET"])
bind_route(bookings_bp, "/api/bookings/history", legacy_booking_history, "api_booking_history", ["GET"], [jwt_required(optional=True)])
bind_route(bookings_bp, "/bookings/history", legacy_booking_history, "booking_history", ["GET"], [jwt_required(optional=True)])
bind_route(bookings_bp, "/api/bookings/<int:booking_id>/timeline", legacy_booking_timeline, "api_booking_timeline", ["GET"])
bind_route(bookings_bp, "/bookings/<int:booking_id>/timeline", legacy_booking_timeline, "booking_timeline", ["GET"])
bind_route(bookings_bp, "/api/vehicles/recommendation", legacy_vehicle_recommendation, "api_vehicle_recommendation", ["POST"])
bind_route(bookings_bp, "/vehicles/recommendation", legacy_vehicle_recommendation, "vehicle_recommendation", ["POST"])
bind_route(bookings_bp, "/api/bookings/<int:booking_id>/status", legacy_update_booking_status, "api_update_booking_status", ["PATCH"], [require_roles("admin", "driver")])
bind_route(bookings_bp, "/bookings/<int:booking_id>/status", legacy_update_booking_status, "update_booking_status", ["PATCH"], [require_roles("admin", "driver")])
bind_route(bookings_bp, "/fare-estimate", legacy_fare_estimate, "fare_estimate", ["POST"])
bind_route(bookings_bp, "/bookings/<booking_id>/invoice", legacy_booking_invoice, "booking_invoice", ["GET"])

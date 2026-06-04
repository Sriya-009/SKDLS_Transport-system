from flask import Blueprint

from app import payment_history as legacy_payment_history
from app import booking_invoice as legacy_booking_invoice
from app import create_payment_order as legacy_create_payment_order
from app import admin_payment_analytics as legacy_admin_payment_analytics
from app import admin_payment_anomalies as legacy_admin_payment_anomalies
from app import verify_payment as legacy_verify_payment
from app import payment_retry as legacy_payment_retry
from app import payment_wallet as legacy_payment_wallet
from app import payment_refund as legacy_payment_refund
from extensions import limiter
from middleware.auth import require_auth, require_role
from utils.blueprints import bind_route


payments_bp = Blueprint("payments_bp", __name__)

bind_route(payments_bp, "/payments/create-order", legacy_create_payment_order, "create_payment_order", ["POST"], [limiter.limit("15 per minute")])
bind_route(payments_bp, "/payments/verify", legacy_verify_payment, "verify_payment", ["POST"], [limiter.limit("15 per minute")])
bind_route(payments_bp, "/payments/history", legacy_payment_history, "payment_history", ["GET"])
bind_route(payments_bp, "/payments/retry", legacy_payment_retry, "payment_retry", ["POST"], [require_auth, limiter.limit("10 per minute")])
bind_route(payments_bp, "/payments/wallet", legacy_payment_wallet, "payment_wallet", ["GET"], [require_auth])
bind_route(payments_bp, "/payments/refund", legacy_payment_refund, "payment_refund", ["POST"], [require_role("admin"), require_auth])
bind_route(payments_bp, "/api/admin/payments/analytics", legacy_admin_payment_analytics, "api_admin_payment_analytics", ["GET"], [require_role("admin"), require_auth])
bind_route(payments_bp, "/admin/payments/analytics", legacy_admin_payment_analytics, "admin_payment_analytics", ["GET"], [require_role("admin"), require_auth])
bind_route(payments_bp, "/api/admin/payments/anomalies", legacy_admin_payment_anomalies, "api_admin_payment_anomalies", ["GET"], [require_role("admin"), require_auth])
bind_route(payments_bp, "/admin/payments/anomalies", legacy_admin_payment_anomalies, "admin_payment_anomalies", ["GET"], [require_role("admin"), require_auth])
bind_route(payments_bp, "/bookings/<booking_id>/invoice", legacy_booking_invoice, "booking_invoice_from_payments", ["GET"])

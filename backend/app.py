import os
import json
import re
import atexit
import traceback
import math
import uuid
import time
import random
import hmac
import hashlib
import secrets
from datetime import datetime
from collections import Counter, defaultdict, deque
from threading import Lock

from flask import Flask, request, jsonify, make_response, current_app
from flask_cors import CORS
from mysql.connector import Error
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    get_jwt,
    get_jwt_identity,
    jwt_required,
    verify_jwt_in_request,
    set_access_cookies,
    unset_jwt_cookies,
)
from flask_socketio import SocketIO
import google.generativeai as genai
import requests
from gps_simulator import GPSSimulator
from db import DB_CONFIG, get_db_connection, get_vehicle_by_code, test_db_connection, save_chat_history, save_conversation_json, validate_db_environment, initialize_schema
from ai_context import (
    get_admin_analytics_window,
    get_driver_context,
    get_latest_payment_for_user,
    get_payment_for_reference,
    get_shipment_tracking,
    get_user_context,
)
from workflow_manager import booking_workflow_manager
from config import ProductionConfig, validate_production_environment
from logging_utils import logger, setup_logger, log_print
from whatsapp_service import (
    build_booking_confirmation_message,
    build_delivery_update_message,
    build_live_location_message,
    build_payment_confirmation_message,
    is_whatsapp_configured,
    send_whatsapp_message,
)
from logistics_workflows import LogisticsWorkflowService

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

logger = setup_logger()
print = log_print


def _parse_cors_origins(value):
    origins = [item.strip() for item in str(value or '').split(',') if item.strip()]
    if origins:
        return origins

    return [
        'http://localhost:5173',
        'http://127.0.0.1:5173',
    ]


def _current_timestamp():
    return datetime.utcnow()


ALLOWED_CORS_ORIGINS = _parse_cors_origins(os.getenv('CORS_ORIGINS'))

app = Flask(__name__)
app.config.from_object(ProductionConfig)
app.config['ENV'] = os.getenv('FLASK_ENV', 'production')
app.config['DEBUG'] = False
app.config['MAX_CONTENT_LENGTH'] = int(os.getenv('MAX_CONTENT_LENGTH_BYTES', str(10 * 1024 * 1024)))
jwt = JWTManager(app)
socketio = SocketIO(
    app,
    cors_allowed_origins=ALLOWED_CORS_ORIGINS,
    async_mode=os.getenv('SOCKETIO_ASYNC_MODE', 'threading'),
    logger=False,
    engineio_logger=False,
)
DEFAULT_API_RATE_LIMIT = os.getenv('API_RATE_LIMIT', '120 per minute')
MONITORING_STARTED_AT = time.time()
MONITORING_LOCK = Lock()
MONITORING_SAMPLE_LIMIT = 500
MONITORING_ERROR_LIMIT = 25
MONITORING_REQUEST_SAMPLES = deque(maxlen=MONITORING_SAMPLE_LIMIT)
MONITORING_ERROR_EVENTS = deque(maxlen=MONITORING_ERROR_LIMIT)
MONITORING_METHOD_COUNTS = defaultdict(int)
MONITORING_STATUS_CLASS_COUNTS = defaultdict(int)
MONITORING_ROUTE_COUNTS = defaultdict(int)
MONITORING_ROUTE_LATENCY = defaultdict(float)
MONITORING_ROUTE_ERRORS = defaultdict(int)
MONITORING_WEBSOCKET_CONNECTIONS = 0
MONITORING_WEBSOCKET_CONNECTS_TOTAL = 0
MONITORING_WEBSOCKET_DISCONNECTS_TOTAL = 0
MONITORING_BUCKETS_SECONDS = [0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10]

# Active streaming tasks: stream_id -> {cancelled: bool, started_at: float}
ACTIVE_STREAMS = {}
STREAMS_LOCK = Lock()

CORS(
    app,
    resources={r"/*": {"origins": ALLOWED_CORS_ORIGINS}},
    supports_credentials=True,
    methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
    allow_headers=['Content-Type', 'Authorization', 'X-Requested-With', 'X-CSRF-TOKEN'],
)

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[DEFAULT_API_RATE_LIMIT],
    storage_uri=os.getenv('RATELIMIT_STORAGE_URI', 'memory://'),
)


# -----------------------------
# Health, logging, and security
# -----------------------------


@app.before_request
def log_request_info():
    try:
        request_id = request.headers.get('X-Request-Id') or request.headers.get('X-Correlation-Id')
        logger.info(f"[request] {request.method} {request.path} from {request.remote_addr} id={request_id}")
    except Exception:
        pass


@app.after_request
def set_security_headers(response):
    try:
        # Security headers
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault('X-XSS-Protection', '1; mode=block')
        response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains; preload')
        response.headers.setdefault('Permissions-Policy', 'geolocation=(self), microphone=(), camera=()')
    except Exception:
        pass
    return response


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    try:
        tb = traceback.format_exc()
        logger.exception(f"[unhandled_exception] {error}\n{tb}")
    except Exception:
        pass
    return jsonify({"status": "error", "message": "Internal server error"}), 500


@app.route('/health/live', methods=['GET'])
def health_live():
    return jsonify({'status': 'live'}), 200


@app.route('/health/ready', methods=['GET'])
def health_ready():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('SELECT 1')
        cur.close()
        conn.close()
        return jsonify({'status': 'ready'}), 200
    except Exception:
        logger.exception('[health][ready] database check failed')
        return jsonify({'status': 'error', 'message': 'db-unavailable'}), 503


def _normalize_auth_text(value):
    return re.sub(r"\s+", " ", str(value or "").strip())


def _normalize_email(value):
    email = _normalize_auth_text(value).lower()
    if not email or "@" not in email or "." not in email:
        raise ValueError("Enter a valid email address")
    return email


def _normalize_role(value):
    role = _normalize_auth_text(value).lower()
    if not role:
        return "customer"
    if role not in {"customer", "admin", "driver"}:
        raise ValueError("Role must be customer, admin, or driver")
    return role


def _hash_password(password):
    from werkzeug.security import generate_password_hash

    secret = str(password or "")
    if len(secret) < 8:
        raise ValueError("Password must be at least 8 characters long")
    return generate_password_hash(secret, method="pbkdf2:sha256", salt_length=16)


def _verify_password(password_hash, password):
    from werkzeug.security import check_password_hash

    return check_password_hash(str(password_hash or ""), str(password or ""))


def _serialize_user_row(row):
    if not row:
        return None

    return {
        "id": int(row["id"]),
        "full_name": str(row.get("full_name") or "").strip(),
        "email": str(row.get("email") or "").strip().lower(),
        "phone": str(row.get("phone") or "").strip(),
        "role": str(row.get("role") or "customer").strip() or "customer",
        "created_at": row.get("created_at").isoformat(sep=" ", timespec="seconds") if row.get("created_at") else None,
    }


def _get_user_by_email(email):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM users WHERE email = %s LIMIT 1",
            (str(email).strip().lower(),),
        )
        return cursor.fetchone()
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def _get_user_by_id(user_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM users WHERE id = %s LIMIT 1",
            (int(user_id),),
        )
        return cursor.fetchone()
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def _create_user(full_name, email, phone, password, role="customer"):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO users (full_name, email, phone, password_hash, role)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                _normalize_auth_text(full_name),
                _normalize_email(email),
                _normalize_phone_number(phone),
                _hash_password(password),
                _normalize_role(role),
            ),
        )
        connection.commit()
        return cursor.lastrowid
    except Error as error:
        if connection is not None:
            connection.rollback()
        raise RuntimeError(f"Database error while creating user: {error}") from error
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def _build_access_token_for_user(user_row):
    identity = str(user_row["id"])
    additional_claims = {
        "role": str(user_row.get("role") or "customer").strip() or "customer",
        "email": str(user_row.get("email") or "").strip().lower(),
        "full_name": str(user_row.get("full_name") or "").strip(),
    }
    return create_access_token(identity=identity, additional_claims=additional_claims)


def _auth_response_payload(user_row):
    return {
        "user": _serialize_user_row(user_row),
    }


def _build_booking_reference(booking_id, created_at=None):
    created_value = created_at
    if hasattr(created_value, "strftime"):
        prefix = created_value.strftime("BK-%Y%m%d")
    else:
        prefix = time.strftime("BK-%Y%m%d")

    return f"{prefix}-{int(booking_id):05d}"


def _normalize_booking_status(value, fallback="pending"):
    status = _normalize_auth_text(value).lower()
    allowed_statuses = {
        "pending",
        "pending_payment",
        "quoted",
        "confirmed",
        "assigned",
        "loading",
        "in_transit",
        "completed",
        "cancelled",
        "rescheduled",
    }

    if not status:
        return fallback

    return status if status in allowed_statuses else fallback


def _serialize_vehicle_row(row):
    if not row:
        return None

    features = row.get("features_json")
    if isinstance(features, str) and features.strip():
        try:
            features = json.loads(features)
        except Exception:
            features = [features.strip()]
    elif not isinstance(features, list):
        features = []

    return {
        "id": int(row["id"]),
        "vehicle_code": str(row.get("vehicle_code") or "").strip(),
        "vehicle_name": str(row.get("vehicle_name") or "").strip(),
        "truck_type": str(row.get("truck_type") or "").strip(),
        "max_load_tons": float(row["max_load_tons"]) if row.get("max_load_tons") is not None else None,
        "rate_per_km": float(row["rate_per_km"]) if row.get("rate_per_km") is not None else None,
        "availability_status": str(row.get("availability_status") or "available").strip() or "available",
        "current_booking_id": int(row["current_booking_id"]) if row.get("current_booking_id") is not None else None,
        "current_latitude": float(row["current_latitude"]) if row.get("current_latitude") is not None else None,
        "current_longitude": float(row["current_longitude"]) if row.get("current_longitude") is not None else None,
        "features": features,
        "ai_notes": str(row.get("ai_notes") or "").strip(),
        "created_at": row.get("created_at").isoformat(sep=" ", timespec="seconds") if row.get("created_at") else None,
        "updated_at": row.get("updated_at").isoformat(sep=" ", timespec="seconds") if row.get("updated_at") else None,
    }


def _fetch_vehicle_records():
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(f"SELECT * FROM {VEHICLES_TABLE} ORDER BY availability_status ASC, max_load_tons ASC, rate_per_km ASC, id ASC")
        return cursor.fetchall()
    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def _generate_vehicle_recommendation_reason(booking_payload, selected_vehicle, candidate_rows):
    if not selected_vehicle:
        return "No matching vehicle is currently available."

    candidate_summary = [
        {
            "vehicle_code": row.get("vehicle_code"),
            "vehicle_name": row.get("vehicle_name"),
            "truck_type": row.get("truck_type"),
            "max_load_tons": row.get("max_load_tons"),
            "rate_per_km": row.get("rate_per_km"),
            "availability_status": row.get("availability_status"),
        }
        for row in candidate_rows[:5]
    ]

    if GEMINI_API_KEY:
        try:
            model = _get_gemini_model()
            if model:
                prompt = f"""Recommend the best logistics vehicle for this booking.

Booking request:
{json.dumps(booking_payload, ensure_ascii=False)}

Candidate vehicles:
{json.dumps(candidate_summary, ensure_ascii=False)}

Selected vehicle:
{json.dumps(_serialize_vehicle_row(selected_vehicle), ensure_ascii=False)}

Return a concise 1-2 sentence explanation of why the selected vehicle is best. Mention capacity and routing fit. No markdown."""
                response = model.generate_content(
                    prompt,
                    generation_config=genai.types.GenerationConfig(max_output_tokens=120, temperature=0.4),
                    request_options={"timeout": GEMINI_TIMEOUT_SECONDS},
                )
                reply = str(getattr(response, "text", "")).strip()
                if reply:
                    return reply
        except Exception as error:
            logger.warning(f"[vehicles][recommendation][ai_fallback] {error}")

    return (
        f"{selected_vehicle['vehicle_name']} is the best available match for {booking_payload.get('truck_type') or 'this load'} "
        f"because it can safely carry up to {selected_vehicle.get('max_load_tons')} tons and is currently available."
    )


def _recommend_vehicle_for_booking(booking_payload):
    truck_type = _normalize_form_truck_type(booking_payload.get("truck_type") or booking_payload.get("truckType"))
    if not truck_type:
        raise ValueError("Select a valid truck type")

    try:
        load_weight = float(booking_payload.get("load_weight") or booking_payload.get("loadWeight"))
    except (TypeError, ValueError):
        raise ValueError("Load weight must be a number")

    vehicles = _fetch_vehicle_records()
    matching_vehicles = [
        row for row in vehicles
        if str(row.get("truck_type") or "").strip() == truck_type
        and _normalize_auth_text(row.get("availability_status")).lower() in {"available", "ready", "idle"}
        and float(row.get("max_load_tons") or 0) >= load_weight
    ]

    if not matching_vehicles:
        matching_vehicles = [
            row for row in vehicles
            if str(row.get("truck_type") or "").strip() == truck_type
            and float(row.get("max_load_tons") or 0) >= load_weight
        ]

    if not matching_vehicles:
        return {
            "recommended_vehicle": None,
            "alternatives": [],
            "reasoning": "No matching vehicle is currently available for this shipment.",
            "truck_type": truck_type,
            "load_weight": round(load_weight, 2),
            "available": False,
        }

    sorted_candidates = sorted(
        matching_vehicles,
        key=lambda row: (
            0 if _normalize_auth_text(row.get("availability_status")).lower() in {"available", "ready", "idle"} else 1,
            float(row.get("max_load_tons") or 0),
            float(row.get("rate_per_km") or 0),
            int(row.get("id") or 0),
        ),
    )
    selected_vehicle = sorted_candidates[0]
    return {
        "recommended_vehicle": _serialize_vehicle_row(selected_vehicle),
        "alternatives": [_serialize_vehicle_row(row) for row in sorted_candidates[1:4]],
        "reasoning": _generate_vehicle_recommendation_reason(
            {
                "truck_type": truck_type,
                "load_weight": round(load_weight, 2),
                "pickup_location": _normalize_auth_text(booking_payload.get("pickup_location") or booking_payload.get("pickupLocation")),
                "drop_location": _normalize_auth_text(booking_payload.get("drop_location") or booking_payload.get("dropLocation")),
            },
            selected_vehicle,
            sorted_candidates,
        ),
        "truck_type": truck_type,
        "load_weight": round(load_weight, 2),
        "available": True,
    }


def _insert_booking_status_log(booking_id, status, note="", location="", actor_role="system", metadata=None):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            f"""
            INSERT INTO {BOOKING_STATUS_LOGS_TABLE}
                (booking_id, status, note, location, actor_role, metadata_json)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                int(booking_id),
                _normalize_booking_status(status),
                str(note or "").strip(),
                str(location or "").strip(),
                str(actor_role or "system").strip() or "system",
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        connection.commit()
        return cursor.lastrowid
    except Error as error:
        if connection is not None:
            connection.rollback()
        raise RuntimeError(f"Database error while storing booking status log: {error}") from error
    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def _fetch_booking_status_logs(booking_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            f"SELECT * FROM {BOOKING_STATUS_LOGS_TABLE} WHERE booking_id = %s ORDER BY created_at ASC, id ASC",
            (int(booking_id),),
        )
        return cursor.fetchall()
    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def _serialize_booking_status_log_row(row):
    if not row:
        return None

    metadata = row.get("metadata_json")
    if isinstance(metadata, str) and metadata.strip():
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {"raw": metadata}
    elif not isinstance(metadata, dict):
        metadata = {}

    return {
        "id": int(row["id"]),
        "booking_id": int(row["booking_id"]),
        "status": str(row.get("status") or "pending").strip() or "pending",
        "note": str(row.get("note") or "").strip(),
        "location": str(row.get("location") or "").strip(),
        "actor_role": str(row.get("actor_role") or "system").strip() or "system",
        "metadata": metadata,
        "created_at": row.get("created_at").isoformat(sep=" ", timespec="seconds") if row.get("created_at") else None,
    }


def _fetch_booking_timeline(booking_id):
    return [_serialize_booking_status_log_row(row) for row in _fetch_booking_status_logs(booking_id)]


def _require_roles(*allowed_roles):
    def decorator(view_func):
        @jwt_required()
        def wrapped(*args, **kwargs):
            claims = get_jwt()
            role = str(claims.get("role") or "customer").strip().lower()
            if role not in {item.lower() for item in allowed_roles}:
                return jsonify({"status": "error", "message": "Forbidden"}), 403
            return view_func(*args, **kwargs)

        wrapped.__name__ = view_func.__name__
        return wrapped

    return decorator


def require_auth(view_func):
    """Decorator to require any authenticated user (JWT)."""
    def wrapped(*args, **kwargs):
        try:
            verify_jwt_in_request()
        except Exception:
            return jsonify({"status": "error", "message": "Missing or invalid authentication token"}), 401
        return view_func(*args, **kwargs)

    wrapped.__name__ = view_func.__name__
    return wrapped


def require_role(*allowed_roles):
    """Decorator to require a specific role (e.g., 'driver', 'admin')."""
    def decorator(view_func):
        def wrapped(*args, **kwargs):
            try:
                verify_jwt_in_request()
            except Exception:
                return jsonify({"status": "error", "message": "Missing or invalid authentication token"}), 401

            claims = get_jwt()
            role = str(claims.get("role") or "").strip().lower()
            if role not in {r.lower() for r in allowed_roles}:
                return jsonify({"status": "error", "message": "Forbidden"}), 403

            return view_func(*args, **kwargs)

        wrapped.__name__ = view_func.__name__
        return wrapped

    return decorator


def _validate_api_key(api_key):
    """Check drivers and users tables for a matching api_key. Returns row dict or None."""
    if not api_key:
        return None

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM drivers WHERE api_key = %s LIMIT 1", (str(api_key),))
        row = cursor.fetchone()
        if row:
            return {"type": "driver", "row": row}

        cursor.execute("SELECT * FROM users WHERE api_key = %s LIMIT 1", (str(api_key),))
        row = cursor.fetchone()
        if row:
            return {"type": "user", "row": row}

        return None
    except Exception:
        return None
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()

TABLE_MAP = {
    "12": "lorries_12_tyre",
    "14": "lorries_14_tyre",
    "16": "lorries_16_tyre",
}

BOOKINGS_TABLE = "bookings"
SHIPMENTS_TABLE = "shipments"
PAYMENTS_TABLE = "payments"
DRIVERS_TABLE = "drivers"
VEHICLES_TABLE = "vehicles"
BOOKING_STATUS_LOGS_TABLE = "booking_status_logs"
SHIPMENT_STATUS_LOGS_TABLE = "shipment_status_logs"

ENABLE_GPS_SIMULATOR = os.getenv("ENABLE_GPS_SIMULATOR", "0").lower() in {"1", "true", "yes", "on"}

# Initialize GPS Simulator only for explicit non-production replay/testing.
gps_simulator = GPSSimulator(DB_CONFIG, TABLE_MAP) if ENABLE_GPS_SIMULATOR else None

# Ensure simulator stops gracefully on app exit when it is enabled.
if gps_simulator is not None:
    atexit.register(gps_simulator.stop)

REQUIRED_SCHEMA_COLUMNS = {
    BOOKINGS_TABLE: {
        "id",
        "customer_name",
        "phone",
        "pickup_location",
        "drop_location",
        "truck_type",
        "load_weight",
        "price",
        "booking_status",
        "created_at",
        "user_id",
        "source_location",
        "destination_location",
        "exact_pickup_location",
        "exact_delivery_location",
        "tons",
        "tyre_type",
        "distance",
        "price",
        "token_amount",
        "payment_status",
        "lorry_number",
        "whatsapp_notifications_enabled",
        "whatsapp_booking_confirmation",
        "whatsapp_payment_confirmation",
        "whatsapp_live_location",
        "whatsapp_delivery_updates",
    },
    PAYMENTS_TABLE: {
        "id",
        "booking_id",
        "shipment_id",
        "razorpay_order_id",
        "razorpay_payment_id",
        "amount",
        "payment_status",
        "status",
        "payment_type",
        "created_at",
    },
    SHIPMENTS_TABLE: {
        "id",
        "user_id",
        "pickup_location",
        "drop_location",
        "cargo_type",
        "truck_type",
        "weight",
        "distance_km",
        "estimated_price",
        "payment_status",
        "shipment_status",
        "assigned_driver_id",
        "created_at",
    },
    SHIPMENT_STATUS_LOGS_TABLE: {
        "id",
        "shipment_id",
        "status",
        "note",
        "location",
        "actor_role",
        "metadata_json",
        "created_at",
    },
    DRIVERS_TABLE: {
        "id",
        "driver_name",
        "phone",
        "license_number",
        "assigned_truck",
        "assigned_truck_type",
        "status",
        "rating",
        "experience_years",
        "next_available_at",
        "last_updated",
        "created_at",
    },
    "gps_logs": {
        "id",
        "booking_id",
        "lorry_number",
        "latitude",
        "longitude",
        "source_location",
        "destination_location",
        "created_at",
    },
    "conversations": {
        "id",
        "conversation_id",
        "session_id",
        "conversation_json",
        "created_at",
    },
    TABLE_MAP["14"]: {"vehicle_number", "availability_status", "latitude", "longitude", "last_updated"},
    TABLE_MAP["16"]: {"vehicle_number", "availability_status", "latitude", "longitude", "last_updated"},
}

user_data = {}

GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "").strip()
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip()
EMPTY_BOOKING_JSON = {"source": "", "destination": "", "tons": "", "truck_type": ""}
BOOKING_FIELDS = ("source", "destination", "tons")
BOOKING_STATES = {
    "collecting_source",
    "collecting_destination",
    "collecting_tyre_type",
    "collecting_exact_pickup",
    "collecting_exact_delivery",
    "awaiting_payment_confirmation",
    "awaiting_booking_confirmation",
}

BOOKING_FORM_TRUCK_TYPES = {
    "12 tyre": {"label": "12 tyre", "max_load_weight": 25, "base_price": 10000, "rate_per_ton": 450},
    "14 tyre": {"label": "14 tyre", "max_load_weight": 30, "base_price": 14000, "rate_per_ton": 550},
    "16 tyre": {"label": "16 tyre", "max_load_weight": 35, "base_price": 18000, "rate_per_ton": 700},
}

RAZORPAY_API_BASE_URL = "https://api.razorpay.com/v1"
RAZORPAY_CURRENCY = "INR"
RAZORPAY_ADVANCE_RATIO = 0.8
GST_RATE = 0.18

PRICING_CONFIG = {
    "12": {
        "label": "12 tyre",
        "slabs": (
            {"min_km": 0, "max_km": 500, "base": 10000, "rate": 40},
            {"min_km": 500, "max_km": 1000, "base": 15000, "rate": 40},
            {"min_km": 1000, "max_km": 1500, "base": 18000, "rate": 40},
            {"min_km": 1500, "max_km": None, "base": 25000, "rate": 50},
        ),
    },
    "14": {
        "label": "14 tyre",
        "slabs": (
            {"min_km": 0, "max_km": 500, "base": 15000, "rate": 45},
            {"min_km": 500, "max_km": 1000, "base": 18000, "rate": 40},
            {"min_km": 1000, "max_km": 1500, "base": 20000, "rate": 40},
            {"min_km": 1500, "max_km": None, "base": 27000, "rate": 50},
        ),
    },
    "16": {
        "label": "16 tyre",
        "slabs": (
            {"min_km": 0, "max_km": 500, "base": 18000, "rate": 50},
            {"min_km": 500, "max_km": 1000, "base": 20000, "rate": 45},
            {"min_km": 1000, "max_km": 1500, "base": 23000, "rate": 45},
            {"min_km": 1500, "max_km": None, "base": 29000, "rate": 55},
        ),
    },
}

TRUCK_SPEEDS_KMPH = {
    "12": 58,
    "14": 54,
    "16": 48,
}

try:
    import genai  # type: ignore
except Exception:
    genai = None

if genai is not None and GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
    except Exception:
        pass


# ============================================================================
# HYBRID CONVERSATIONAL AI INFRASTRUCTURE
# ============================================================================

# Intent cache: key = (stage, normalized_message_hash), TTL = 5 min
INTENT_CACHE = {}
CACHE_TTL_SECONDS = 300
GEMINI_MODEL = None  # Reused instance for performance
GEMINI_TIMEOUT_SECONDS = 10

def _get_gemini_model():
    """Reuse Gemini model instance for performance."""
    global GEMINI_MODEL
    if GEMINI_MODEL is None and GEMINI_API_KEY and genai is not None:
        try:
            GEMINI_MODEL = genai.GenerativeModel(GEMINI_MODEL_NAME)
        except Exception:
            GEMINI_MODEL = None
    return GEMINI_MODEL

# System prompt for conversational AI
SYSTEM_PROMPT = """You are SKDLS Transportations AI assistant.
You help users book trucks naturally like ChatGPT.
Be concise, professional, conversational, and logistics-focused.
Avoid robotic replies.
Preserve booking context and answer naturally.
Keep replies to 1-2 sentences for chat UI."""

def _normalize_message_for_cache(message):
    """Normalize message for cache key."""
    return re.sub(r"\s+", " ", str(message or "").strip().lower())[:50]

def _get_cache_key(stage, message):
    """Generate cache key from stage and message."""
    normalized = _normalize_message_for_cache(message)
    return f"{stage}:{normalized}"

def _get_cached_intent(cache_key):
    """Get cached intent if still valid (< 5 min old)."""
    if cache_key not in INTENT_CACHE:
        return None
    
    entry = INTENT_CACHE[cache_key]
    age_seconds = time.time() - entry["timestamp"]
    
    if age_seconds > CACHE_TTL_SECONDS:
        del INTENT_CACHE[cache_key]
        return None
    
    print(f"[gemini][cache_hit] cache_key={cache_key!r} age_sec={age_seconds:.1f}")
    return entry["data"]

def _set_cached_intent(cache_key, intent_data):
    """Cache intent result."""
    INTENT_CACHE[cache_key] = {
        "data": intent_data,
        "timestamp": time.time(),
    }

def _should_use_fast_path(message, stage):
    """Check if message can be handled without Gemini (fast path)."""
    message_lower = str(message or "").strip().lower()
    
    # Fast path: Direct yes/no
    if message_lower in {"yes", "y", "yep", "yeah", "yup", "ok", "okay", "sure", "no", "n", "nope", "nah"}:
        print(f"[gemini][fast_path] type=direct_confirmation")
        return True
    
    # Fast path: Numbers only (tons)
    if re.fullmatch(r"\d+", message_lower):
        print(f"[gemini][fast_path] type=numeric_input")
        return True
    
    # Fast path: Known city names
    if message_lower in KNOWN_CITY_HINTS:
        print(f"[gemini][fast_path] type=known_city")
        return True
    
    # Fast path: Tyre values
    if message_lower in {"12", "14", "16", "12 tyre", "14 tyre", "16 tyre"}:
        print(f"[gemini][fast_path] type=tyre_value")
        return True
    
    # Fast path: Reset intents
    if message_lower in {"restart", "reset", "start over", "hi", "hello", "hey"}:
        print(f"[gemini][fast_path] type=reset_or_greeting")
        return True
    
    return False


# Known city hints for single-word acceptance (lowercase)
KNOWN_CITY_HINTS = {
    "vadodara",
    "vijayawada",
    "hyderabad",
    "bangalore",
    "mumbai",
    "delhi",
    "visakhapatnam",
    "pune",
    "surat",
    "jaipur",
    "chennai",
    "kolkata",
    "ahmedabad",
}

CITY_ALIASES = {
    "vij": "Vijayawada",
    "hyd": "Hyderabad",
    "blr": "Bangalore",
    "vizag": "Visakhapatnam",
    "mum": "Mumbai",
    "del": "Delhi",
}

PUBLIC_CHAT_INTENTS = {
    "BOOK_SHIPMENT",
    "TRACK_SHIPMENT",
    "GET_PRICE_ESTIMATE",
    "MAKE_PAYMENT",
    "RECONCILE_PAYMENT",
    "GENERATE_INVOICE",
    "DELIVERY_CONFIRMATION",
    "DELAY_MANAGEMENT",
    "FAILED_SHIPMENT",
    "CUSTOMER_NOTIFICATION",
    "GET_ANALYTICS",
    "DRIVER_UPDATE",
    "CUSTOMER_SUPPORT",
    "UNRELATED",
}

INTENT_WORKFLOWS = {
    "BOOK_SHIPMENT": "booking",
    "TRACK_SHIPMENT": "tracking",
    "GET_PRICE_ESTIMATE": "pricing",
    "MAKE_PAYMENT": "payments",
    "RECONCILE_PAYMENT": "payment_reconciliation",
    "GENERATE_INVOICE": "invoice_generation",
    "DELIVERY_CONFIRMATION": "delivery_confirmation",
    "DELAY_MANAGEMENT": "delay_management",
    "FAILED_SHIPMENT": "failed_shipment_handling",
    "CUSTOMER_NOTIFICATION": "customer_notification",
    "GET_ANALYTICS": "analytics",
    "DRIVER_UPDATE": "driver_management",
    "CUSTOMER_SUPPORT": "support",
    "UNRELATED": "fallback",
}

ROLE_CHAT_CAPABILITIES = {
    "customer": {
        "label": "Customer",
        "allowed_intents": {"BOOK_SHIPMENT", "TRACK_SHIPMENT", "GET_PRICE_ESTIMATE", "MAKE_PAYMENT", "RECONCILE_PAYMENT", "GENERATE_INVOICE", "CUSTOMER_NOTIFICATION", "CUSTOMER_SUPPORT"},
        "quick_actions": [
            "Book a shipment",
            "Track my shipment",
            "Get price estimate",
            "Make a payment",
            "Generate invoice",
            "Shipment support",
        ],
        "assistant_focus": "booking, tracking, payments, and shipment support",
    },
    "admin": {
        "label": "Admin",
        "allowed_intents": {"GET_ANALYTICS", "TRACK_SHIPMENT", "DRIVER_UPDATE", "DELIVERY_CONFIRMATION", "DELAY_MANAGEMENT", "FAILED_SHIPMENT", "RECONCILE_PAYMENT", "GENERATE_INVOICE", "CUSTOMER_NOTIFICATION", "CUSTOMER_SUPPORT"},
        "quick_actions": [
            "Show analytics",
            "Check delayed shipments",
            "Review revenue reports",
            "Manage drivers",
            "Reconcile payment",
        ],
        "assistant_focus": "analytics, delayed shipments, revenue reports, payment reconciliation, and driver management",
    },
    "driver": {
        "label": "Driver",
        "allowed_intents": {"TRACK_SHIPMENT", "DRIVER_UPDATE", "DELIVERY_CONFIRMATION", "DELAY_MANAGEMENT", "FAILED_SHIPMENT", "CUSTOMER_NOTIFICATION", "CUSTOMER_SUPPORT"},
        "quick_actions": [
            "View assigned shipments",
            "Start trip",
            "Update shipment status",
            "Upload proof of delivery",
            "Report delay",
        ],
        "assistant_focus": "assigned shipments, trip updates, shipment status, proof of delivery, and delay reporting",
    },
}


def _get_role_chat_policy(role):
    normalized_role = str(role or "").strip().lower()
    if normalized_role == "super_admin":
        normalized_role = "admin"
    return ROLE_CHAT_CAPABILITIES.get(normalized_role, ROLE_CHAT_CAPABILITIES["customer"])


def _role_allows_intent(role, intent):
    policy = _get_role_chat_policy(role)
    return _normalize_public_intent(intent) in policy.get("allowed_intents", set())


def _normalize_public_intent(intent_value):
    intent = str(intent_value or "").strip().upper()
    if intent in PUBLIC_CHAT_INTENTS:
        return intent
    return "UNRELATED"


def _public_intent_to_internal(intent_value, current_stage="idle"):
    intent = _normalize_public_intent(intent_value)
    if intent == "BOOK_SHIPMENT":
        return "booking_question"
    if intent == "TRACK_SHIPMENT":
        return "tracking_question"
    if intent == "GET_PRICE_ESTIMATE":
        return "pricing_question"
    if intent == "MAKE_PAYMENT":
        return "continue_booking" if str(current_stage or "").strip() == "awaiting_payment_confirmation" else "booking_question"
    if intent in {"GET_ANALYTICS", "DRIVER_UPDATE", "RECONCILE_PAYMENT", "GENERATE_INVOICE", "DELIVERY_CONFIRMATION", "DELAY_MANAGEMENT", "FAILED_SHIPMENT", "CUSTOMER_NOTIFICATION", "CUSTOMER_SUPPORT"}:
        return "info_query"
    return "unrelated"


def _extract_reference_id(message):
    text = str(message or "")
    match = re.search(r"\b(?:shipment|booking|order|payment)?\s*#?\s*(\d{2,})\b", text, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))

    match = re.search(r"\b(\d{2,})\b", text)
    if match:
        return int(match.group(1))

    return None


def _resolve_chat_response_type(intent, route, data=None):
    intent = _normalize_public_intent(intent)
    route = str(route or "").strip().lower()
    data = data or {}

    if intent == "GET_ANALYTICS" or route.startswith("analytics"):
        return "analytics_card"
    if intent == "DRIVER_UPDATE" or route.startswith("driver_management"):
        return "driver_assignment_card"
    if intent in {"MAKE_PAYMENT", "RECONCILE_PAYMENT", "GENERATE_INVOICE"} or route.startswith("payments"):
        return "payment_card"
    if intent == "TRACK_SHIPMENT" or route.startswith("tracking"):
        if any(key in data for key in ("eta_hours", "eta_minutes", "eta_label", "estimated_eta")):
            return "eta_status_card"
        return "tracking_card"
    if intent == "GET_PRICE_ESTIMATE" or route.startswith("pricing"):
        return "eta_status_card"
    if intent in {"DELIVERY_CONFIRMATION", "DELAY_MANAGEMENT", "FAILED_SHIPMENT", "CUSTOMER_NOTIFICATION"} or route.startswith("workflows"):
        return "tracking_card"
    if route.startswith("booking") or intent == "BOOK_SHIPMENT":
        return "booking_summary_card"
    return "text_reply"


def _build_chat_intent_payload(intent, confidence, reply, workflow, route, data=None, suggestions=None, status="success", http_status=200, response_type=None, action=None, card_type=None, message=None):
    resolved_type = response_type or _resolve_chat_response_type(intent, route, data)
    resolved_message = str(message if message is not None else reply or "").strip()
    resolved_action = _normalize_public_intent(action or intent)
    resolved_card_type = str(card_type or resolved_type or "text_reply").strip() or "text_reply"
    payload = {
        "status": status,
        "intent": _normalize_public_intent(intent),
        "confidence": round(float(confidence or 0), 2),
        "workflow": workflow,
        "route": route,
        "message": resolved_message,
        "reply": resolved_message,
        "action": resolved_action,
        "card_type": resolved_card_type,
        "type": resolved_type,
    }

    if data is not None:
        payload["data"] = data

    if suggestions:
        payload["suggestions"] = suggestions

    return jsonify(payload), http_status


def _log_detected_chat_intent(user_id, current_stage, intent, confidence, route, note=""):
    print(
        f"[chat][intent] user={user_id!r} stage={current_stage!r} intent={_normalize_public_intent(intent)} "
        f"confidence={float(confidence or 0):.2f} route={route} note={note}"
    )


def _detect_user_intent(message, current_stage, booking_context=None, db_context=None):
    """Detect user intent using Gemini and return a structured public intent payload.

    Returns:
        {
            "intent": "BOOK_SHIPMENT|TRACK_SHIPMENT|GET_PRICE_ESTIMATE|MAKE_PAYMENT|RECONCILE_PAYMENT|GENERATE_INVOICE|DELIVERY_CONFIRMATION|DELAY_MANAGEMENT|FAILED_SHIPMENT|CUSTOMER_NOTIFICATION|GET_ANALYTICS|DRIVER_UPDATE|CUSTOMER_SUPPORT|UNRELATED",
            "confidence": 0.0-1.0,
            "reply": "conversational reply",
            "reasoning": "brief explanation"
        }
    """
    booking_context = booking_context or {}
    db_context = db_context or {}
    role = str(db_context.get("role") or "customer").strip().lower() or "customer"
    role_policy = _get_role_chat_policy(role)
    
    # STEP 1: Check cache
    cache_key = _get_cache_key(current_stage, message)
    cached_result = _get_cached_intent(cache_key)
    if cached_result is not None:
        return cached_result
    
    # STEP 2: Fast-path for simple inputs (no Gemini needed)
    if _should_use_fast_path(message, current_stage):
        print(f"[gemini][fast_path] skipping Gemini for deterministic flow")
        return {"intent": "UNRELATED", "confidence": 1.0, "reply": "", "reasoning": "fast_path"}
    
    # STEP 3: Use configured LLM provider for intent classification + reply in one call
    try:
        # Build context string for LLM
        context_parts = []
        context_parts.append(f"Role: {role_policy.get('label', role.title())}")
        context_parts.append(f"Allowed capabilities: {', '.join(sorted(role_policy.get('allowed_intents', [])))}")
        context_parts.append(f"Role focus: {role_policy.get('assistant_focus', 'customer support')}")
        if booking_context.get("source"):
            context_parts.append(f"Source: {booking_context['source']}")
        if booking_context.get("destination"):
            context_parts.append(f"Destination: {booking_context['destination']}")
        if booking_context.get("tons"):
            context_parts.append(f"Load: {booking_context['tons']} tons")
        if booking_context.get("tyre_type"):
            context_parts.append(f"Truck: {booking_context['tyre_type']}")
        if booking_context.get("distance"):
            context_parts.append(f"Distance: {booking_context['distance']} km")
        if booking_context.get("price"):
            context_parts.append(f"Fare: ₹{booking_context['price']}")
        
        context_str = " | ".join(context_parts) if context_parts else "Starting new booking"
        db_context_str = _context_block_for_prompt(db_context)

        prompt = f"""User Stage: {current_stage}
Booking Context: {context_str}
    User Role: {role_policy.get('label', role.title())}
    Allowed Actions: {', '.join(sorted(role_policy.get('allowed_intents', [])))}
    Role Guidance: Stay strictly within {role_policy.get('assistant_focus', 'customer support')}.
User Message: {message}
    Database Context: {db_context_str or 'none'}

Classify the user's intent and provide a brief, natural conversational reply.

RESPOND ONLY WITH VALID JSON (no markdown, no explanation):
{{
    "intent": "BOOK_SHIPMENT OR TRACK_SHIPMENT OR GET_PRICE_ESTIMATE OR MAKE_PAYMENT OR RECONCILE_PAYMENT OR GENERATE_INVOICE OR DELIVERY_CONFIRMATION OR DELAY_MANAGEMENT OR FAILED_SHIPMENT OR CUSTOMER_NOTIFICATION OR GET_ANALYTICS OR DRIVER_UPDATE OR CUSTOMER_SUPPORT OR UNRELATED",
    "confidence": 0.85,
    "reply": "brief natural response (1-2 sentences max)"
}}

RULES:
- BOOK_SHIPMENT: user wants to start or continue a shipment booking
- TRACK_SHIPMENT: user wants live shipment or booking tracking
- GET_PRICE_ESTIMATE: user wants a quote, estimate, fare, or pricing breakdown
- MAKE_PAYMENT: user wants to pay or generate a payment workflow
- RECONCILE_PAYMENT: user wants to reconcile, verify, or settle payments against live records
- GENERATE_INVOICE: user wants an invoice, receipt, or billing document
- DELIVERY_CONFIRMATION: user wants to confirm delivery or proof of delivery
- DELAY_MANAGEMENT: user wants to log, monitor, or explain a shipment delay
- FAILED_SHIPMENT: user wants to report or handle a failed shipment
- CUSTOMER_NOTIFICATION: user wants customer alerts or operational notifications sent
- GET_ANALYTICS: user wants dashboard, revenue, fleet, or operational analytics
- DRIVER_UPDATE: user wants to update a driver record or driver status
- CUSTOMER_SUPPORT: user wants support, clarification, or a human-style assistant response
- UNRELATED: everything else

Reply MUST be conversational, friendly, and context-aware. DO NOT make the user re-enter data.""".strip()

        # Call LLM provider (Gemini / OpenAI / fallback)
        from ai.llm_provider import LLMProvider

        provider = LLMProvider(logger=logger)
        try:
            reply_text = provider.generate(prompt, max_tokens=180, temperature=0.45, timeout=10, retries=2, stream=False)
            if isinstance(reply_text, (list, tuple)):
                reply_text = "".join(reply_text)
            reply_text = str(reply_text or "").strip()
        except Exception as inner_exc:
            logger.exception(f"[llm][intent] provider error: {inner_exc}")
            return {"intent": "UNRELATED", "confidence": 0, "reply": "", "reasoning": "provider_error"}

        # Parse JSON response from LLM
        try:
            result = json.loads(reply_text)
            intent = _normalize_public_intent(result.get("intent", "UNRELATED"))
            confidence = float(result.get("confidence", 0))
            reply = str(result.get("reply", "")).strip()
            reasoning = str(result.get("reasoning", "")).strip()
            
            if confidence < 0.7:
                print(f"[gemini][fallback] low_confidence conf={confidence:.2f}")
                intent = "UNRELATED"
                confidence = 0.0
                reply = ""
                reasoning = "low_confidence"

            if confidence > 1:
                confidence = 1.0
            if confidence < 0:
                confidence = 0.0

            result = {"intent": intent, "confidence": confidence, "reply": reply, "reasoning": reasoning}
            
            print(f"[gemini][intent] intent={intent} confidence={confidence:.2f} stage={current_stage}")
            
            # Cache result
            _set_cached_intent(cache_key, result)
            return result

        except json.JSONDecodeError as e:
            logger.warning(f"[llm][intent] parse_error {e} response={reply_text!r}")
            return {"intent": "UNRELATED", "confidence": 0, "reply": "", "reasoning": "parse_error"}
    except Exception as e:
        logger.exception(f"[llm][intent] exception {e}")
        return {"intent": "UNRELATED", "confidence": 0, "reply": "", "reasoning": "exception"}


LOGISTICS_AI_AGENT = None
LOGISTICS_WORKFLOW_SERVICE = None


def _ai_build_response(message, action, card_type, data=None, status="success", http_status=200, workflow="fallback", route="", suggestions=None, intent=None, confidence=0.0, handled=True, error=""):
    payload = {
        "status": status,
        "message": str(message or "").strip(),
        "reply": str(message or "").strip(),
        "action": _normalize_public_intent(intent or action),
        "card_type": str(card_type or "text_reply").strip() or "text_reply",
        "data": data or {},
        "workflow": workflow,
        "route": route,
        "suggestions": suggestions or [],
        "confidence": round(float(confidence or 0.0), 2),
        "handled": handled,
        "http_status": int(http_status or 200),
    }

    if error:
        payload["error"] = str(error)

    return payload


def _tail(values, limit=10):
    items = list(values or [])
    if len(items) <= limit:
        return items
    return items[-limit:]


def _ai_extract_reference_context(message, user_state=None):
    user_state = user_state or {}
    reference_id = _extract_reference_id(message)
    shipment_id = user_state.get("shipment_id")
    booking_id = user_state.get("booking_id")

    if reference_id is None:
        reference_id = shipment_id or booking_id

    return {
        "reference_id": reference_id,
        "shipment_id": shipment_id,
        "booking_id": booking_id,
    }


def _ai_extract_booking_details(message, user_state=None):
    user_state = user_state or {}
    booking_state = user_state.get("booking") if isinstance(user_state.get("booking"), dict) else {}
    extracted = get_gemini_response(message)

    combined = {}
    for field in BOOKING_FIELDS:
        combined[field] = str(extracted.get(field) or booking_state.get(field) or "").strip()

    if not combined.get("truck_type") and combined.get("tons"):
        try:
            combined["truck_type"] = _canonical_truck_type_label(_truck_type_from_tons(combined["tons"]))
        except Exception:
            combined["truck_type"] = ""

    return combined


def _ai_customer_support_handler(context):
    message = str(context.get("message") or "").strip()
    role_policy = context.get("role_policy") or _get_role_chat_policy(context.get("role"))
    reply = get_chat_reply(
        f"You are SKDLS Transportations support. Answer clearly, briefly, and with logistics context: {message}"
    )
    return _ai_build_response(
        reply,
        action="CUSTOMER_SUPPORT",
        card_type="text_reply",
        workflow="support",
        route="support.general",
        suggestions=list(role_policy.get("quick_actions", []))[:4],
        intent="CUSTOMER_SUPPORT",
        confidence=context.get("intent_result", {}).get("confidence", 0),
    )


def _ai_calculate_eta_handler(context):
    message = str(context.get("message") or "").strip()
    user_state = context.get("user_state") or {}
    role_policy = context.get("role_policy") or _get_role_chat_policy(context.get("role"))
    booking_details = _ai_extract_booking_details(message, user_state)

    source = booking_details.get("source") or booking_details.get("pickup_location")
    destination = booking_details.get("destination") or booking_details.get("drop_location")
    tons = booking_details.get("tons")
    truck_type = booking_details.get("truck_type")

    if not source or not destination or not tons:
        return _ai_build_response(
            "Share pickup, drop, and load weight to calculate ETA and pricing.",
            action="GET_PRICE_ESTIMATE",
            card_type="eta_status_card",
            data={"source": source or "", "destination": destination or "", "tons": tons or ""},
            status="needs_input",
            workflow="pricing",
            route="pricing.request_details",
            suggestions=["Share pickup location", "Share drop location", "Share load weight in tons"],
            intent="GET_PRICE_ESTIMATE",
            confidence=context.get("intent_result", {}).get("confidence", 0),
        )

    truck_code = ""
    try:
        truck_code = _normalize_truck_type(truck_type or "", tons)
    except Exception:
        try:
            truck_code = _truck_type_from_tons(tons)
        except Exception:
            truck_code = ""

    if not truck_code:
        return _ai_build_response(
            "I need the load weight to determine the right truck type.",
            action="GET_PRICE_ESTIMATE",
            card_type="eta_status_card",
            data={"source": source, "destination": destination, "tons": tons},
            status="needs_input",
            workflow="pricing",
            route="pricing.request_weight",
            suggestions=["Share load weight in tons"],
            intent="GET_PRICE_ESTIMATE",
            confidence=context.get("intent_result", {}).get("confidence", 0),
        )

    distance_km, estimated_price = _estimate_distance_and_price(source, destination, truck_code)
    eta_hours = _estimate_eta_hours(distance_km, truck_code)
    truck_type_label = _canonical_truck_type_label(truck_code)

    return _ai_build_response(
        _build_fare_estimation_reply(source, destination, tons, truck_type_label, distance_km, estimated_price, eta_hours),
        action="GET_PRICE_ESTIMATE",
        card_type="eta_status_card",
        data={
            "pickup_location": source,
            "drop_location": destination,
            "load_weight": float(tons),
            "truck_type": truck_type_label,
            "distance": int(round(float(distance_km))),
            "estimated_fare": int(round(float(estimated_price))),
            "eta_hours": eta_hours,
        },
        workflow="pricing",
        route="pricing.estimate",
        suggestions=list(role_policy.get("quick_actions", []))[:4],
        intent="GET_PRICE_ESTIMATE",
        confidence=context.get("intent_result", {}).get("confidence", 0),
    )


def _ai_create_shipment_handler(context):
    message = str(context.get("message") or "").strip()
    user_state = context.get("user_state") or {}
    auth_context = context.get("auth_context") or {}
    role_policy = context.get("role_policy") or _get_role_chat_policy(context.get("role"))
    booking_details = _ai_extract_booking_details(message, user_state)

    source = booking_details.get("source")
    destination = booking_details.get("destination")
    tons = booking_details.get("tons")
    if not source or not destination or not tons:
        return _ai_build_response(
            "Share pickup, drop, and load weight so I can create the shipment.",
            action="BOOK_SHIPMENT",
            card_type="booking_summary_card",
            data={"booking": booking_details},
            status="needs_input",
            workflow="booking",
            route="booking.request_details",
            suggestions=["Share pickup location", "Share drop location", "Share weight in tons"],
            intent="BOOK_SHIPMENT",
            confidence=context.get("intent_result", {}).get("confidence", 0),
        )

    truck_code = ""
    try:
        truck_code = _normalize_truck_type(booking_details.get("truck_type") or "", tons)
    except Exception:
        truck_code = _truck_type_from_tons(tons)

    if not truck_code:
        return _ai_build_response(
            "I need the load weight to recommend the right truck type before I create the shipment.",
            action="BOOK_SHIPMENT",
            card_type="booking_summary_card",
            data={"booking": booking_details},
            status="needs_input",
            workflow="booking",
            route="booking.request_weight",
            suggestions=["Share load weight in tons"],
            intent="BOOK_SHIPMENT",
            confidence=context.get("intent_result", {}).get("confidence", 0),
        )

    user_id = str(auth_context.get("identity") or user_state.get("user_id") or "guest").strip() or "guest"
    truck_type_label = _canonical_truck_type_label(truck_code)
    pickup_coords = _get_coordinates(source)
    drop_coords = _get_coordinates(destination)
    route_info = _get_route_geometry(pickup_coords[0], pickup_coords[1], drop_coords[0], drop_coords[1])
    distance_km = float(route_info.get("distance_km") or 0)
    weight_value = float(tons)

    shipment_payload = {
        "pickup_location": source,
        "drop_location": destination,
        "cargo_type": booking_details.get("cargo_type") or "General cargo",
        "truck_type": truck_type_label,
        "weight": weight_value,
        "distance_km": round(distance_km, 2),
        "estimated_price": _get_shipment_pricing(distance_km, truck_code, weight_value)["estimated_price"],
        "payment_status": "pending",
        "shipment_status": "pending",
    }

    assigned_driver = _find_nearest_available_driver(pickup_coords[0], pickup_coords[1], truck_code)
    if assigned_driver:
        shipment_payload["assigned_driver_id"] = assigned_driver.get("id")

    shipment_id = _insert_shipment_record(shipment_payload, user_id)
    shipment_row = _fetch_shipment_record_by_id(shipment_id)
    shipment_payload_serialized = _serialize_shipment_row(shipment_row) if shipment_row else {"id": shipment_id, **shipment_payload}

    _insert_shipment_status_log(
        shipment_id,
        "pending",
        note="Shipment created by AI agent",
        location=source,
        actor_role=str((auth_context.get("role") or "customer")).strip() or "customer",
        metadata={
            "route_coordinates": route_info.get("coordinates", []),
            "assigned_driver": assigned_driver,
        },
    )

    user_state["shipment_id"] = shipment_id
    user_state["booking_mode"] = False
    _set_stage(user_state, "idle")

    return _ai_build_response(
        f"Shipment #{shipment_id} has been created and is ready for tracking.",
        action="BOOK_SHIPMENT",
        card_type="booking_summary_card",
        data={
            "shipment": shipment_payload_serialized,
            "route_coordinates": route_info.get("coordinates", []),
            "assigned_driver": _serialize_driver_row(assigned_driver) if assigned_driver else None,
        },
        workflow="booking",
        route="shipments.create",
        suggestions=["Track shipment", "Create payment", "Generate invoice"],
        intent="BOOK_SHIPMENT",
        confidence=context.get("intent_result", {}).get("confidence", 0),
    )


def _ai_track_shipment_handler(context):
    message = str(context.get("message") or "").strip()
    user_state = context.get("user_state") or {}
    role_policy = context.get("role_policy") or _get_role_chat_policy(context.get("role"))
    role = str(context.get("role") or "customer").strip().lower() or "customer"
    reference = _ai_extract_reference_context(message, user_state)
    reference_id = reference.get("reference_id")

    shipment_row = _fetch_shipment_record_by_id(reference_id) if reference_id is not None else None
    booking_row = _fetch_booking_record_by_id(reference_id) if reference_id is not None and shipment_row is None else None

    if shipment_row:
        operational = _build_shipment_operational_context(shipment_row, role=role, include_invoice=True)
        payload = operational.get("data") if operational else {}
        return _ai_build_response(
            operational.get("reply") if operational else f"Tracking opened for shipment #{shipment_row.get('id')}. Status: {shipment_row.get('shipment_status') or 'pending' }.",
            action="TRACK_SHIPMENT",
            card_type="tracking_card",
            data=payload,
            workflow="tracking",
            route="tracking.shipment_lookup",
            suggestions=(operational.get("actions") if operational else list(role_policy.get("quick_actions", []))[:4]),
            intent="TRACK_SHIPMENT",
            confidence=context.get("intent_result", {}).get("confidence", 0),
        )

    if booking_row:
        operational = _build_booking_operational_context(booking_row, role=role, include_invoice=True)
        return _ai_build_response(
            operational.get("reply") if operational else f"Booking #{booking_row.get('id')} is {booking_row.get('booking_status') or 'pending'}. Create the shipment to unlock live tracking.",
            action="TRACK_SHIPMENT",
            card_type="booking_summary_card",
            data=operational.get("data") if operational else {"booking": _serialize_booking_row(booking_row)},
            workflow="tracking",
            route="tracking.booking_lookup",
            suggestions=(operational.get("actions") if operational else ["Create shipment", "Share shipment ID"]),
            intent="TRACK_SHIPMENT",
            confidence=context.get("intent_result", {}).get("confidence", 0),
        )

    return _ai_build_response(
        "Share your shipment ID or booking reference and I will open live tracking.",
        action="TRACK_SHIPMENT",
        card_type="tracking_card",
        data={"reference_id": reference_id},
        workflow="tracking",
        route="tracking.request_reference",
        suggestions=["Share shipment ID", "Share booking reference"],
        intent="TRACK_SHIPMENT",
        confidence=context.get("intent_result", {}).get("confidence", 0),
    )


def _ai_assign_driver_handler(context):
    message = str(context.get("message") or "").strip().lower()
    user_state = context.get("user_state") or {}
    role_policy = context.get("role_policy") or _get_role_chat_policy(context.get("role"))
    role = str(context.get("role") or "admin").strip().lower() or "admin"
    reference = _ai_extract_reference_context(message, user_state)
    reference_id = reference.get("reference_id")

    if reference_id is None:
        return _ai_build_response(
            "Share the shipment reference and optional driver ID to assign a driver.",
            action="DRIVER_UPDATE",
            card_type="driver_assignment_card",
            data={},
            status="needs_input",
            workflow="driver_management",
            route="driver_management.request_reference",
            suggestions=["Share shipment ID", "Share driver ID"],
            intent="DRIVER_UPDATE",
            confidence=context.get("intent_result", {}).get("confidence", 0),
        )

    shipment_row = _fetch_shipment_record_by_id(reference_id)
    if not shipment_row:
        return _ai_build_response(
            f"Shipment #{reference_id} was not found.",
            action="DRIVER_UPDATE",
            card_type="driver_assignment_card",
            data={"shipment_id": reference_id},
            status="error",
            http_status=404,
            workflow="driver_management",
            route="driver_management.not_found",
            suggestions=["Share shipment ID"],
            intent="DRIVER_UPDATE",
            confidence=context.get("intent_result", {}).get("confidence", 0),
        )

    shipment_payload = _serialize_shipment_row(shipment_row) or {}
    pickup_location = str(shipment_payload.get("pickup_location") or shipment_row.get("pickup_location") or "").strip()
    drop_location = str(shipment_payload.get("drop_location") or shipment_row.get("drop_location") or "").strip()
    truck_type = str(shipment_payload.get("truck_type") or shipment_row.get("truck_type") or "12 tyre").strip()
    truck_code = _normalize_truck_type(truck_type, shipment_row.get("weight") or shipment_payload.get("weight") or 0)

    try:
        pickup_coords = _get_coordinates(pickup_location)
    except Exception:
        pickup_coords = (0.0, 0.0)

    assigned_driver = _find_nearest_available_driver(pickup_coords[0], pickup_coords[1], truck_code)
    if not assigned_driver:
        return _ai_build_response(
            "I could not find an available driver for this shipment right now.",
            action="DRIVER_UPDATE",
            card_type="driver_assignment_card",
            data={"shipment": shipment_payload},
            status="error",
            http_status=404,
            workflow="driver_management",
            route="driver_management.no_driver_available",
            suggestions=list(role_policy.get("quick_actions", []))[:4],
            intent="DRIVER_UPDATE",
            confidence=context.get("intent_result", {}).get("confidence", 0),
        )

    workflow_service = _get_logistics_workflow_service()
    workflow_result = workflow_service.dispatch_driver(
        shipment_id=reference_id,
        driver=assigned_driver,
        actor_role=str(context.get("role") or "admin").strip() or "admin",
        note=f"Driver assigned by AI: {assigned_driver.get('driver_name') or assigned_driver.get('name') or 'assigned driver'}",
        location=pickup_location,
        session_id=user_state.get("session_id") or "",
    )
    payload = {
        "shipment": workflow_result.get("shipment") or shipment_payload,
        "driver": workflow_result.get("driver") or _serialize_driver_row(assigned_driver),
    }

    operational = _build_shipment_operational_context(_fetch_shipment_record_by_id(reference_id), role=role, include_invoice=True)
    if operational:
        payload.update(operational.get("data") or {})

    return _ai_build_response(
        operational.get("reply") if operational else f"Driver assigned to shipment #{reference_id}.",
        action="DRIVER_UPDATE",
        card_type="driver_assignment_card",
        data=payload,
        workflow="driver_management",
        route="driver_management.assigned",
        suggestions=(operational.get("actions") if operational else ["Update shipment status", "Track shipment"]),
        intent="DRIVER_UPDATE",
        confidence=context.get("intent_result", {}).get("confidence", 0),
    )


def _ai_create_payment_handler(context):
    message = str(context.get("message") or "").strip()
    user_state = context.get("user_state") or {}
    role_policy = context.get("role_policy") or _get_role_chat_policy(context.get("role"))
    role = str(context.get("role") or "customer").strip().lower() or "customer"
    reference = _ai_extract_reference_context(message, user_state)
    reference_id = reference.get("reference_id")
    payment_type = "full" if any(keyword in message.lower() for keyword in {"full", "pay all", "complete"}) else "advance"

    if any(keyword in message.lower() for keyword in {"reconcile", "reconciliation", "verify", "settle", "settlement", "sync"}):
        if reference_id is None:
            return _ai_build_response(
                "Share the booking or shipment reference so I can reconcile the payment.",
                action="RECONCILE_PAYMENT",
                card_type="payment_card",
                data={"reference_id": reference_id},
                status="needs_input",
                workflow="payment_reconciliation",
                route="payments.reconcile.request_reference",
                suggestions=["Share booking reference", "Share shipment reference"],
                intent="RECONCILE_PAYMENT",
                confidence=context.get("intent_result", {}).get("confidence", 0),
            )

        service = _get_logistics_workflow_service()
        reference_type = "shipment" if _fetch_shipment_record_by_id(reference_id) else "booking"
        result = service.reconcile_payment(
            reference_type=reference_type,
            reference_id=reference_id,
            payment_type=payment_type,
            session_id=user_state.get("session_id") or "",
            actor_role=str(context.get("role") or "system").strip() or "system",
        )
        summary = result.get("summary") or {}
        operational = _build_shipment_operational_context(result.get("shipment") and _fetch_shipment_record_by_id(reference_id), role=role, payment_rows=result.get("payments") or [], include_invoice=True) if reference_type == "shipment" else _build_booking_operational_context(_fetch_booking_record_by_id(reference_id), role=role, include_invoice=True)
        message_text = operational.get("reply") if operational else (
            f"Payment reconciliation completed for {reference_type} #{reference_id}. "
            f"Paid {_format_currency_amount(summary.get('paid_amount') or 0)}, outstanding {_format_currency_amount(summary.get('outstanding_amount') or 0)}."
        )
        return _ai_build_response(
            message_text,
            action="RECONCILE_PAYMENT",
            card_type="payment_card",
            data=result,
            workflow="payment_reconciliation",
            route=result.get("route") or "payments.reconcile.completed",
            suggestions=(operational.get("actions") if operational else ["Generate invoice", "Track shipment"]),
            intent="RECONCILE_PAYMENT",
            confidence=context.get("intent_result", {}).get("confidence", 0),
        )

    booking_row = _fetch_booking_record_by_id(reference_id) if reference_id is not None else None
    shipment_row = _fetch_shipment_record_by_id(reference_id) if reference_id is not None and booking_row is None else None

    if booking_row:
        amount_due = _resolve_payment_amount(booking_row, payment_type, reference_id)
        if amount_due <= 0:
            return _ai_build_response(
                f"No payment is due for booking #{reference_id}.",
                action="MAKE_PAYMENT",
                card_type="payment_card",
                data={"booking_id": reference_id},
                workflow="payments",
                route="payments.no_balance",
                suggestions=list(role_policy.get("quick_actions", []))[:4],
                intent="MAKE_PAYMENT",
                confidence=context.get("intent_result", {}).get("confidence", 0),
            )

        order_data = _create_razorpay_order(amount_due, reference_id)
        _insert_payment_record(reference_id, order_data["id"], amount_due, "created", payment_type=payment_type, session_id=user_state.get("session_id"))
        operational = _build_booking_operational_context(booking_row, role=role, include_invoice=True)
        return _ai_build_response(
            operational.get("reply") if operational else f"Payment link created for booking #{reference_id}. Amount due: {_format_currency_amount(amount_due)}.",
            action="MAKE_PAYMENT",
            card_type="payment_card",
            data={
                "booking_id": reference_id,
                "amount_due": int(round(float(amount_due))),
                "amount": int(order_data.get("amount", amount_due * 100)),
                "currency": order_data.get("currency", RAZORPAY_CURRENCY),
                "key_id": RAZORPAY_KEY_ID,
                "order_id": order_data["id"],
                "actions": operational.get("actions") if operational else [],
                "payment_summary": operational.get("data", {}).get("payment_summary") if operational else {},
                "invoice": operational.get("data", {}).get("invoice") if operational else None,
            },
            workflow="payments",
            route="payments.order_created",
            suggestions=(operational.get("actions") if operational else ["Open payment checkout", "Generate invoice"]),
            intent="MAKE_PAYMENT",
            confidence=context.get("intent_result", {}).get("confidence", 0),
        )

    if shipment_row:
        amount_due = _resolve_shipment_payment_amount(shipment_row, payment_type, reference_id)
        if amount_due <= 0:
            return _ai_build_response(
                f"No payment is due for shipment #{reference_id}.",
                action="MAKE_PAYMENT",
                card_type="payment_card",
                data={"shipment_id": reference_id},
                workflow="payments",
                route="payments.no_balance",
                suggestions=list(role_policy.get("quick_actions", []))[:4],
                intent="MAKE_PAYMENT",
                confidence=context.get("intent_result", {}).get("confidence", 0),
            )

        order_data = _create_razorpay_order(amount_due, reference_id)
        _insert_payment_record(None, order_data["id"], amount_due, "created", payment_type=payment_type, shipment_id=reference_id, session_id=user_state.get("session_id"))
        operational = _build_shipment_operational_context(shipment_row, role=role, include_invoice=True)
        return _ai_build_response(
            operational.get("reply") if operational else f"Payment link created for shipment #{reference_id}. Amount due: {_format_currency_amount(amount_due)}.",
            action="MAKE_PAYMENT",
            card_type="payment_card",
            data={
                "shipment_id": reference_id,
                "amount_due": int(round(float(amount_due))),
                "amount": int(order_data.get("amount", amount_due * 100)),
                "currency": order_data.get("currency", RAZORPAY_CURRENCY),
                "key_id": RAZORPAY_KEY_ID,
                "order_id": order_data["id"],
                "actions": operational.get("actions") if operational else [],
                "payment_summary": operational.get("data", {}).get("payment_summary") if operational else {},
                "invoice": operational.get("data", {}).get("invoice") if operational else None,
            },
            workflow="payments",
            route="payments.order_created",
            suggestions=(operational.get("actions") if operational else ["Open payment checkout", "Generate invoice"]),
            intent="MAKE_PAYMENT",
            confidence=context.get("intent_result", {}).get("confidence", 0),
        )

    return _ai_build_response(
        "Share your booking or shipment reference and I will prepare the payment workflow.",
        action="MAKE_PAYMENT",
        card_type="payment_card",
        data={"reference_id": reference_id},
        workflow="payments",
        route="payments.request_reference",
        suggestions=["Share booking reference", "Share shipment reference"],
        intent="MAKE_PAYMENT",
        confidence=context.get("intent_result", {}).get("confidence", 0),
    )


def _ai_generate_invoice_handler(context):
    message = str(context.get("message") or "").strip()
    user_state = context.get("user_state") or {}
    role_policy = context.get("role_policy") or _get_role_chat_policy(context.get("role"))
    role = str(context.get("role") or "customer").strip().lower() or "customer"
    reference = _ai_extract_reference_context(message, user_state)
    reference_id = reference.get("reference_id")

    if reference_id is None:
        return _ai_build_response(
            "Share a booking or shipment reference and I will generate the invoice.",
            action="GENERATE_INVOICE",
            card_type="invoice_card",
            data={"reference_id": reference_id},
            status="needs_input",
            workflow="invoice_generation",
            route="payments.invoice.request_reference",
            suggestions=["Share booking reference", "Share shipment reference"],
            intent="GENERATE_INVOICE",
            confidence=context.get("intent_result", {}).get("confidence", 0),
        )

    shipment_row = _fetch_shipment_record_by_id(reference_id) if reference_id is not None else None
    booking_row = _fetch_booking_record_by_id(reference_id) if reference_id is not None and shipment_row is None else None

    service = _get_logistics_workflow_service()

    if shipment_row:
        result = service.generate_invoice(reference_type="shipment", reference_id=reference_id)
        invoice_payload = result.get("invoice") or _build_shipment_invoice_payload(shipment_row, _fetch_payment_records_for_shipment(reference_id))
        operational = _build_shipment_operational_context(shipment_row, role=role, include_invoice=True)
        return _ai_build_response(
            operational.get("reply") if operational else f"Shipment invoice {invoice_payload.get('invoice_number')} is ready.",
            action="GENERATE_INVOICE",
            card_type="invoice_card",
            data={**result, "invoice": invoice_payload, "shipment": _serialize_shipment_row(shipment_row)},
            workflow="invoice_generation",
            route=result.get("route") or "payments.shipment_invoice",
            suggestions=(operational.get("actions") if operational else ["Download invoice", "Track shipment"]),
            intent="GENERATE_INVOICE",
            confidence=context.get("intent_result", {}).get("confidence", 0),
        )

    if booking_row:
        result = service.generate_invoice(reference_type="booking", reference_id=reference_id)
        invoice_payload = result.get("invoice") or _build_invoice_payload(booking_row, _fetch_payment_records_for_booking(reference_id))
        operational = _build_booking_operational_context(booking_row, role=role, include_invoice=True)
        return _ai_build_response(
            operational.get("reply") if operational else f"Invoice {invoice_payload.get('invoice_number')} is ready.",
            action="GENERATE_INVOICE",
            card_type="invoice_card",
            data={**result, "invoice": invoice_payload, "booking": _serialize_booking_row(booking_row)},
            workflow="invoice_generation",
            route=result.get("route") or "payments.booking_invoice",
            suggestions=(operational.get("actions") if operational else ["Download invoice", "Open payment history"]),
            intent="GENERATE_INVOICE",
            confidence=context.get("intent_result", {}).get("confidence", 0),
        )

    return _ai_build_response(
        "Share a booking or shipment reference and I will generate the invoice.",
        action="GENERATE_INVOICE",
        card_type="invoice_card",
        data={"reference_id": reference_id},
        workflow="invoice_generation",
        route="payments.request_reference",
        suggestions=["Share booking reference", "Share shipment reference"],
        intent="GENERATE_INVOICE",
        confidence=context.get("intent_result", {}).get("confidence", 0),
    )


def _ai_get_analytics_handler(context):
    role_policy = context.get("role_policy") or _get_role_chat_policy(context.get("role"))
    dashboard = _build_admin_dashboard_payload(days=30)
    summary = dashboard.get("summary", {})
    return _ai_build_response(
        f"Analytics ready: {summary.get('total_bookings', 0)} bookings, {summary.get('active_shipments', 0)} active shipments, ₹{int(summary.get('revenue_collected', 0)):,} collected revenue.",
        action="GET_ANALYTICS",
        card_type="analytics_card",
        data=dashboard,
        workflow="analytics",
        route="analytics.dashboard_summary",
        suggestions=list(role_policy.get("quick_actions", []))[:4],
        intent="GET_ANALYTICS",
        confidence=context.get("intent_result", {}).get("confidence", 0),
    )


def _ai_update_shipment_status_handler(context):
    message = str(context.get("message") or "").strip().lower()
    user_state = context.get("user_state") or {}
    role_policy = context.get("role_policy") or _get_role_chat_policy(context.get("role"))
    role = str(context.get("role") or "driver").strip().lower() or "driver"
    reference = _ai_extract_reference_context(message, user_state)
    reference_id = reference.get("reference_id")

    shipment_row = _fetch_shipment_record_by_id(reference_id) if reference_id is not None else None
    if not shipment_row:
        return _ai_build_response(
            "Share the shipment reference you want me to update.",
            action="DRIVER_UPDATE",
            card_type="tracking_card",
            data={"reference_id": reference_id},
            status="needs_input",
            workflow="driver_management",
            route="driver_management.request_reference",
            suggestions=["Share shipment ID", "Share the new status"],
            intent="DRIVER_UPDATE",
            confidence=context.get("intent_result", {}).get("confidence", 0),
        )

    service = _get_logistics_workflow_service()

    if any(keyword in message for keyword in {"delayed", "delay", "late", "stuck", "held"}):
        delay_match = re.search(r"(\d{1,4})\s*(?:min|mins|minute|minutes|hr|hrs|hour|hours)", message)
        delay_minutes = int(delay_match.group(1)) if delay_match else 60
        if any(keyword in message for keyword in {"hour", "hours", "hr", "hrs"}):
            delay_minutes *= 60
        result = service.manage_delay(
            shipment_id=reference_id,
            delay_minutes=delay_minutes,
            reason=message,
            actor_role=str(context.get("role") or "driver").strip() or "driver",
            session_id=user_state.get("session_id") or "",
        )
        operational = _build_shipment_operational_context(_fetch_shipment_record_by_id(reference_id), role=role, include_invoice=True)
        return _workflow_response(
            "DELAY_MANAGEMENT",
            {**result, "message": operational.get("reply") if operational else f"Shipment #{reference_id} is delayed by {delay_minutes} minutes.", "card_type": "delay_alert_card", "data": operational.get("data") if operational else result.get("shipment")},
            default_message=operational.get("reply") if operational else f"Shipment #{reference_id} is delayed by {delay_minutes} minutes.",
            default_route=result.get("route") or "workflows.delay_management.updated",
            default_workflow="delay_management",
            card_type="delay_alert_card",
            suggestions=(operational.get("actions") if operational else ["Notify customer", "Track shipment"]),
        )

    if any(keyword in message for keyword in {"failed", "cancel", "cancelled", "unable", "rejected"}):
        result = service.handle_failed_shipment(
            shipment_id=reference_id,
            reason=message,
            actor_role=str(context.get("role") or "admin").strip() or "admin",
            session_id=user_state.get("session_id") or "",
        )
        operational = _build_shipment_operational_context(_fetch_shipment_record_by_id(reference_id), role=role, include_invoice=True)
        return _workflow_response(
            "FAILED_SHIPMENT",
            {**result, "message": operational.get("reply") if operational else f"Shipment #{reference_id} has been marked as failed.", "card_type": "tracking_card", "data": operational.get("data") if operational else result.get("shipment")},
            default_message=operational.get("reply") if operational else f"Shipment #{reference_id} has been marked as failed.",
            default_route=result.get("route") or "workflows.failed_shipment.updated",
            default_workflow="failed_shipment_handling",
            card_type="tracking_card",
            suggestions=(operational.get("actions") if operational else ["Reassign driver", "Notify customer"]),
        )

    if any(keyword in message for keyword in {"delivered", "completed", "proof of delivery", "pod"}):
        result = service.confirm_delivery(
            shipment_id=reference_id,
            actor_role=str(context.get("role") or "driver").strip() or "driver",
            note=message or "Delivery confirmed through chatbot workflow",
            session_id=user_state.get("session_id") or "",
        )
        operational = _build_shipment_operational_context(_fetch_shipment_record_by_id(reference_id), role=role, include_invoice=True)
        return _workflow_response(
            "DELIVERY_CONFIRMATION",
            {**result, "message": operational.get("reply") if operational else f"Shipment #{reference_id} has been marked delivered.", "card_type": "delivery_confirmation_card", "data": operational.get("data") if operational else result.get("shipment")},
            default_message=operational.get("reply") if operational else f"Shipment #{reference_id} has been marked delivered.",
            default_route=result.get("route") or "workflows.delivery_confirmation.completed",
            default_workflow="delivery_confirmation",
            card_type="delivery_confirmation_card",
            suggestions=(operational.get("actions") if operational else ["Generate invoice", "Notify customer"]),
        )

    if any(keyword in message for keyword in {"notify customer", "customer notification", "send update"}):
        result = service.notify_customer(
            title="Shipment update",
            message=message or "Your shipment has an operational update.",
            level="info",
            shipment_row=shipment_row,
            session_id=user_state.get("session_id") or "",
        )
        operational = _build_shipment_operational_context(shipment_row, role=str(context.get("role") or "customer").strip().lower() or "customer", include_invoice=True)
        return _workflow_response(
            "CUSTOMER_NOTIFICATION",
            {**result, "message": operational.get("reply") if operational else f"Customer notification sent for shipment #{reference_id}.", "card_type": "text_reply", "data": operational.get("data") if operational else {}},
            default_message=operational.get("reply") if operational else f"Customer notification sent for shipment #{reference_id}.",
            default_route=result.get("route") or "notifications.customer.sent",
            default_workflow="customer_notification",
            card_type="text_reply",
            suggestions=(operational.get("actions") if operational else ["Track shipment", "Open timeline"]),
        )

    next_status = None
    for candidate in ["delivered", "completed", "in transit", "out for delivery", "loaded", "assigned", "picked up", "cancelled"]:
        if candidate in message:
            next_status = candidate.replace(" ", "_")
            break

    if not next_status:
        next_status = _normalize_shipment_status(shipment_row.get("shipment_status") or "pending")

    _update_shipment_record(reference_id, {"shipment_status": next_status})
    _insert_shipment_status_log(
        reference_id,
        next_status,
        note="Status updated by AI agent",
        location=str(shipment_row.get("pickup_location") or "").strip(),
        actor_role=str(context.get("role") or "driver").strip() or "driver",
        metadata={"updated_by": context.get("auth_context", {}).get("identity") or user_state.get("user_id") or "system"},
    )

    updated_row = _fetch_shipment_record_by_id(reference_id)
    operational = _build_shipment_operational_context(updated_row or shipment_row, role=role, include_invoice=True)
    return _ai_build_response(
        operational.get("reply") if operational else f"Shipment #{reference_id} status updated to {next_status}.",
        action="DRIVER_UPDATE",
        card_type="tracking_card",
        data=operational.get("data") if operational else {"shipment": _serialize_shipment_row(updated_row) if updated_row else _serialize_shipment_row(shipment_row)},
        workflow="driver_management",
        route="driver_management.updated",
        suggestions=(operational.get("actions") if operational else list(role_policy.get("quick_actions", []))[:4]),
        intent="DRIVER_UPDATE",
        confidence=context.get("intent_result", {}).get("confidence", 0),
    )


def _get_logistics_ai_agent():
    global LOGISTICS_AI_AGENT

    if LOGISTICS_AI_AGENT is not None:
        return LOGISTICS_AI_AGENT

    from ai.logistics_agent import LogisticsAgent

    tool_registry = {
        "create_shipment": _ai_create_shipment_handler,
        "track_shipment": _ai_track_shipment_handler,
        "calculate_eta": _ai_calculate_eta_handler,
        "assign_driver": _ai_assign_driver_handler,
        "create_payment": _ai_create_payment_handler,
        "generate_invoice": _ai_generate_invoice_handler,
        "reconcile_payment": _ai_reconcile_payment_handler,
        "confirm_delivery": _ai_confirm_delivery_handler,
        "manage_delay": _ai_manage_delay_handler,
        "handle_failed_shipment": _ai_handle_failed_shipment_handler,
        "notify_customer": _ai_notify_customer_handler,
        "get_analytics": _ai_get_analytics_handler,
        "update_shipment_status": _ai_update_shipment_status_handler,
        "customer_support": _ai_customer_support_handler,
    }

    LOGISTICS_AI_AGENT = LogisticsAgent(
        tool_registry=tool_registry,
        intent_resolver=_detect_user_intent,
        role_policy_resolver=_get_role_chat_policy,
        logger=logger,
    )

    return LOGISTICS_AI_AGENT


def _get_logistics_workflow_service():
    global LOGISTICS_WORKFLOW_SERVICE

    if LOGISTICS_WORKFLOW_SERVICE is not None:
        return LOGISTICS_WORKFLOW_SERVICE

    LOGISTICS_WORKFLOW_SERVICE = LogisticsWorkflowService(
        fetch_booking_record_by_id=_fetch_booking_record_by_id,
        fetch_shipment_record_by_id=_fetch_shipment_record_by_id,
        fetch_payment_records_for_booking=_fetch_payment_records_for_booking,
        fetch_payment_records_for_shipment=_fetch_payment_records_for_shipment,
        serialize_booking_row=_serialize_booking_row,
        serialize_shipment_row=_serialize_shipment_row,
        update_shipment_record=_update_shipment_record,
        update_driver_record=_update_driver_record,
        insert_shipment_status_log=_insert_shipment_status_log,
        insert_booking_status_log=_insert_booking_status_log,
        update_booking_payment_state=_update_booking_payment_state,
        build_booking_invoice_payload=_build_invoice_payload,
        build_shipment_invoice_payload=_build_shipment_invoice_payload,
        resolve_booking_payment_amount=_resolve_payment_amount,
        resolve_shipment_payment_amount=_resolve_shipment_payment_amount,
        get_booking_paid_amount=_get_booking_paid_amount,
        get_shipment_paid_amount=_get_shipment_paid_amount,
        insert_shipment_event_log=_insert_shipment_event_log,
        send_whatsapp_message=send_whatsapp_message,
        booking_whatsapp_opt_in=_booking_whatsapp_opt_in,
        broadcast_activity=_broadcast_realtime_activity,
        broadcast_notification=_broadcast_realtime_notification,
        broadcast_tracking_snapshot=_broadcast_tracking_snapshot,
        booking_workflow_manager_save=lambda **kwargs: booking_workflow_manager.save(**kwargs),
        logger=logger,
    )

    return LOGISTICS_WORKFLOW_SERVICE


from ai.workflow_engine import WorkflowEngine

# Initialize Workflow Engine with dependencies and tool handlers
def _get_workflow_engine():
    global WORKFLOW_ENGINE
    try:
        WORKFLOW_ENGINE
    except NameError:
        WORKFLOW_ENGINE = None

    if WORKFLOW_ENGINE is not None:
        return WORKFLOW_ENGINE

    workflow_service = _get_logistics_workflow_service()
    # tool handlers available in app.py as functions prefixed with _ai_
    tool_registry = {}
    for name in [
        '_ai_create_shipment_handler',
        '_ai_create_payment_handler',
        '_ai_generate_invoice_handler',
        '_ai_assign_driver_handler',
        '_ai_confirm_delivery_handler',
        '_ai_manage_delay_handler',
        '_ai_handle_failed_shipment_handler',
        '_ai_reconcile_payment_handler',
        '_ai_notify_customer_handler',
    ]:
        if name in globals():
            tool_registry[name.replace('_ai_', '').replace('_handler','')] = globals()[name]

    WORKFLOW_ENGINE = WorkflowEngine(
        workflow_service=workflow_service,
        tool_handlers=tool_registry,
        emit_event=_emit_stream_event,
        logger=logger,
        db_save_memory=lambda **kwargs: __import__('db').save_workflow_memory(**kwargs),
        db_save_action=lambda **kwargs: __import__('db').save_ai_action_log(**kwargs),
        db_save_metric=lambda **kwargs: __import__('db').save_ai_tool_metric(**kwargs),
    )
    return WORKFLOW_ENGINE


def _emit_stream_event(event_name, payload):
    try:
        socketio.emit(event_name, payload)
    except Exception:
        logger.exception(f"[socketio][{event_name}][warning] failed to emit")


def _stream_llm_worker(stream_id, message, user_id=None, session_id=None, timeout_seconds=60, provider_name=None):
    """Background task to stream LLM responses and emit socket events.

    Emits: ai:stream:start, ai:stream:chunk, ai:stream:end
    """
    from ai.llm_provider import LLMProvider

    start_ts = time.time()
    provider = LLMProvider(logger=logger)
    metadata = {"stream_id": stream_id, "user_id": user_id, "session_id": session_id}

    # Notify clients streaming has started
    _emit_stream_event("ai:stream:start", {**metadata, "status": "started"})

    full_text_parts = []
    success = False

    try:
        user_key = str(user_id or session_id or "default_user").strip() or "default_user"
        user_state = _get_chat_state(user_key)
        user_state["user_id"] = user_key
        auth_context = {"identity": user_key, "role": user_state.get("auth_role") or "customer"}
        db_context = _build_chat_db_context(message, user_state, auth_context)
        intent_result = _detect_user_intent(message, user_state.get("stage", "idle"), user_state.get("booking"), db_context=db_context)
        public_intent = _normalize_public_intent(intent_result.get("intent"))

        if public_intent != "UNRELATED":
            ai_response = _get_logistics_ai_agent().handle(message, user_state, auth_context, db_context=db_context, intent_result=intent_result)
            structured_payload = ai_response.as_dict() if hasattr(ai_response, "as_dict") else dict(ai_response or {})
            final_text = str(structured_payload.get("reply") or structured_payload.get("message") or "").strip()
            for chunk in _chunk_stream_text(final_text):
                with STREAMS_LOCK:
                    if ACTIVE_STREAMS.get(stream_id, {}).get("cancelled"):
                        _emit_stream_event("ai:stream:end", {**metadata, "status": "cancelled", "text": ""})
                        return
                full_text_parts.append(chunk)
                _emit_stream_event("ai:stream:chunk", {**metadata, "chunk": chunk})
            metadata.update(structured_payload)
        else:
            gen = provider.generate(message, max_tokens=1024, temperature=0.4, timeout=timeout_seconds, retries=2, stream=True, provider=provider_name)

            # generator may be a generator or a string; handle both
            chunks = [gen] if isinstance(gen, str) else gen
            for chunk in chunks:
                with STREAMS_LOCK:
                    if ACTIVE_STREAMS.get(stream_id, {}).get("cancelled"):
                        _emit_stream_event("ai:stream:end", {**metadata, "status": "cancelled", "text": ""})
                        return

                text = str(chunk or "")
                if not text:
                    continue
                full_text_parts.append(text)
                _emit_stream_event("ai:stream:chunk", {**metadata, "chunk": text})

        success = True
    except Exception as exc:
        logger.exception(f"[ai][stream][error] {exc}")
        _emit_stream_event("ai:stream:end", {**metadata, "status": "error", "error": str(exc), "text": ""})
        success = False
    finally:
        duration_ms = int((time.time() - start_ts) * 1000)
        final_text = "".join(full_text_parts).strip()
        if success:
            _emit_stream_event("ai:stream:end", {**metadata, "status": "completed", "text": final_text, "duration_ms": duration_ms})

        if final_text:
            try:
                user_key = str(user_id or session_id or "default_user").strip() or "default_user"
                state = _get_chat_state(user_key)
                state["user_id"] = user_key
                sid = str(session_id or state.get("session_id") or "").strip()
                sid = _get_or_create_chat_session_id({"session_id": sid}, user_state=state, user_id=user_key)
                cid = _get_or_create_conversation_id(state, sid, user_id=user_key)
                conversation = _append_conversation_turn(state, message, final_text)
                conversation_json = state.get("conversation_json") or json.dumps(conversation, ensure_ascii=False)
                response_memory = {
                    "status": "completed" if success else "error",
                    "intent": metadata.get("intent") or state.get("ai_last_intent") or "UNRELATED",
                    "action": metadata.get("action") or state.get("ai_last_action") or "",
                    "workflow": metadata.get("workflow") or "stream",
                    "route": metadata.get("route") or "stream.assistant",
                    "card_type": metadata.get("card_type") or metadata.get("type") or "",
                    "confidence": metadata.get("confidence") or 0,
                    "stream_id": stream_id,
                }

                save_chat_history(sid, message, final_text)
                save_conversation_json(cid, sid, conversation_json)

                from db import save_ai_conversation_memory, save_ai_session_context, save_workflow_memory
                save_ai_conversation_memory(
                    user_id=user_key,
                    session_id=sid,
                    conversation_id=cid,
                    memory_json={**response_memory, "conversation": _tail(conversation, 12)},
                    summary=final_text[:500],
                    last_intent=response_memory["intent"],
                    last_route=response_memory["route"],
                    confidence=response_memory["confidence"],
                )
                save_ai_session_context(
                    session_id=sid,
                    user_id=user_key,
                    role=state.get("auth_role") or "customer",
                    context_json={
                        "stage": state.get("stage") or "idle",
                        "booking": state.get("booking") if isinstance(state.get("booking"), dict) else {},
                        "ai_action_log": _tail(state.get("ai_action_log") or [], 15),
                        "stream": response_memory,
                    },
                    active_workflow=response_memory["workflow"],
                    current_step=state.get("stage") or response_memory["route"],
                    status="active" if success else "error",
                )
                save_workflow_memory(
                    user_id=user_key,
                    workflow_type=response_memory["workflow"],
                    workflow_state=_tail(state.get("ai_workflow_memory") or [], 10) or response_memory,
                    current_step=state.get("stage") or response_memory["route"],
                    context_json={"session_id": sid, "conversation_id": cid, "stream_id": stream_id},
                )
            except Exception:
                logger.exception("[ai][stream][memory] failed to save stream memory")

        # record metric
        try:
            from db import save_ai_tool_metric as _save_metric
            try:
                _save_metric(user_id=user_id or None, role="system", workflow_type="stream", action="llm_stream", tool_name=provider_name or "auto", duration_ms=duration_ms, retry_count=0, execution_status=("success" if success else "error"), ai_latency_ms=duration_ms)
            except Exception:
                logger.exception("[ai][stream][metric] failed to save metric")
        except Exception:
            pass
        # cleanup
        with STREAMS_LOCK:
            try:
                ACTIVE_STREAMS.pop(stream_id, None)
            except Exception:
                pass


def _chunk_stream_text(text, chunk_size=28):
    words = str(text or "").split(" ")
    if not words:
        return []

    chunks = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) >= chunk_size and current:
            chunks.append(current + " ")
            current = word
        else:
            current = candidate

    if current:
        chunks.append(current)
    return chunks


@app.route('/api/ai/workflow/start', methods=['POST'])
def api_start_workflow():
    try:
        data = request.get_json() or {}
        workflow = str(data.get('workflow') or '').strip()
        steps = data.get('steps') or []
        ctx = data.get('context') or {}
        user_id = str(data.get('user_id') or '').strip() or None

        if not workflow or not isinstance(steps, list) or len(steps) == 0:
            return jsonify({'status': 'error', 'message': 'workflow and non-empty steps required'}), 400

        engine = _get_workflow_engine()
        execution_id = uuid.uuid4().hex
        ctx = {**ctx, "requested_execution_id": execution_id}
        socketio.start_background_task(lambda: engine.execute_workflow(workflow_name=workflow, steps=steps, initial_context=ctx, user_id=user_id))
        return jsonify({'status': 'started', 'execution_id': execution_id}), 202
    except Exception as error:
        logger.exception(f"[api][workflow][start][error] {error}")
        return jsonify({'status': 'error', 'message': str(error)}), 500


@app.route('/api/ai/workflow/<string:execution_id>/retry', methods=['POST'])
def api_retry_workflow_step(execution_id):
    try:
        data = request.get_json() or {}
        step = int(data.get('step') or 0)
        _emit_stream_event('ai:workflow:retry_requested', {'execution_id': execution_id, 'step': step})
        return jsonify({'status': 'queued', 'execution_id': execution_id, 'step': step}), 202
    except Exception as error:
        logger.exception(f"[api][workflow][retry][error] {error}")
        return jsonify({'status': 'error', 'message': str(error)}), 500


@app.route('/chat/stream', methods=['POST'])
def chat_stream_start():
    try:
        data = request.get_json() or {}
        message = str(data.get('message') or '').strip()
        user_id = str(data.get('user_id') or '').strip() or None
        session_id = str(data.get('session_id') or '').strip() or None
        provider_name = data.get('provider')

        if not message:
            return jsonify({'status': 'error', 'message': 'message is required'}), 400

        stream_id = uuid.uuid4().hex
        with STREAMS_LOCK:
            ACTIVE_STREAMS[stream_id] = {'cancelled': False, 'started_at': time.time()}

        # start background task
        socketio.start_background_task(_stream_llm_worker, stream_id, message, user_id, session_id, int(os.getenv('LLM_STREAM_TIMEOUT', '60')), provider_name)

        return jsonify({'status': 'started', 'stream_id': stream_id}), 202
    except Exception as error:
        logger.exception(f"[chat][stream][start][error] {error}")
        return jsonify({'status': 'error', 'message': str(error)}), 500


@app.route('/chat/stream/cancel', methods=['POST'])
def chat_stream_cancel():
    try:
        data = request.get_json() or {}
        stream_id = str(data.get('stream_id') or '').strip()
        if not stream_id:
            return jsonify({'status': 'error', 'message': 'stream_id is required'}), 400

        with STREAMS_LOCK:
            if stream_id in ACTIVE_STREAMS:
                ACTIVE_STREAMS[stream_id]['cancelled'] = True
                return jsonify({'status': 'cancelled', 'stream_id': stream_id}), 200
            else:
                return jsonify({'status': 'not_found', 'stream_id': stream_id}), 404
    except Exception as error:
        logger.exception(f"[chat][stream][cancel][error] {error}")
        return jsonify({'status': 'error', 'message': str(error)}), 500


def _workflow_response(intent, result, *, default_message, default_route, default_workflow, card_type="tracking_card", status="success", http_status=200, suggestions=None):
    payload = dict(result or {})
    message = str(payload.get("message") or default_message or "").strip()
    workflow = str(payload.get("workflow") or default_workflow or "fallback").strip() or default_workflow
    route = str(payload.get("route") or default_route or "").strip() or default_route
    data = dict(payload)
    data.pop("message", None)
    data.pop("workflow", None)
    data.pop("route", None)
    return _ai_build_response(
        message,
        action=intent,
        card_type=str(payload.get("card_type") or card_type or "text_reply").strip() or "text_reply",
        data=data,
        status=str(payload.get("status") or status or "success").strip() or "success",
        http_status=int(payload.get("http_status") or http_status or 200),
        workflow=workflow,
        route=route,
        suggestions=payload.get("suggestions") or suggestions or [],
        intent=intent,
        confidence=payload.get("confidence", 0),
        handled=bool(payload.get("handled", True)),
        error=str(payload.get("error") or ""),
    )


def _ai_reconcile_payment_handler(context):
    message = str(context.get("message") or "").strip()
    user_state = context.get("user_state") or {}
    role_policy = context.get("role_policy") or _get_role_chat_policy(context.get("role"))
    reference = _ai_extract_reference_context(message, user_state)
    reference_id = reference.get("reference_id")

    if reference_id is None:
        return _ai_build_response(
            "Share the booking or shipment reference so I can reconcile the payment.",
            action="RECONCILE_PAYMENT",
            card_type="payment_card",
            data={"reference_id": reference_id},
            status="needs_input",
            workflow="payment_reconciliation",
            route="payments.reconcile.request_reference",
            suggestions=["Share booking reference", "Share shipment reference"],
            intent="RECONCILE_PAYMENT",
            confidence=context.get("intent_result", {}).get("confidence", 0),
        )

    service = _get_logistics_workflow_service()
    reference_type = "shipment" if _fetch_shipment_record_by_id(reference_id) else "booking"
    result = service.reconcile_payment(
        reference_type=reference_type,
        reference_id=reference_id,
        payment_type="full" if any(keyword in message.lower() for keyword in {"full", "settle", "reconcile", "verify", "complete"}) else "advance",
        session_id=user_state.get("session_id") or "",
        actor_role=str(context.get("role") or "system").strip() or "system",
    )
    summary = result.get("summary") or {}
    message_text = (
        f"Payment reconciliation completed for {reference_type} #{reference_id}. "
        f"Paid ₹{int(summary.get('paid_amount') or 0):,}, outstanding ₹{int(summary.get('outstanding_amount') or 0):,}."
    )
    return _workflow_response(
        "RECONCILE_PAYMENT",
        {**result, "message": message_text, "card_type": "payment_card", "suggestions": ["Generate invoice", "Track shipment"]},
        default_message=message_text,
        default_route=result.get("route") or "payments.reconcile.completed",
        default_workflow="payment_reconciliation",
        card_type="payment_card",
        suggestions=["Generate invoice", "Track shipment"],
    )


def _ai_confirm_delivery_handler(context):
    message = str(context.get("message") or "").strip()
    user_state = context.get("user_state") or {}
    reference = _ai_extract_reference_context(message, user_state)
    reference_id = reference.get("reference_id")

    if reference_id is None:
        return _ai_build_response(
            "Share the shipment reference so I can confirm delivery.",
            action="DELIVERY_CONFIRMATION",
            card_type="delivery_confirmation_card",
            data={"reference_id": reference_id},
            status="needs_input",
            workflow="delivery_confirmation",
            route="workflows.delivery_confirmation.request_reference",
            suggestions=["Share shipment reference", "Share proof of delivery"],
            intent="DELIVERY_CONFIRMATION",
            confidence=context.get("intent_result", {}).get("confidence", 0),
        )

    service = _get_logistics_workflow_service()
    result = service.confirm_delivery(
        shipment_id=reference_id,
        actor_role=str(context.get("role") or "driver").strip() or "driver",
        note="Delivery confirmed through chatbot workflow",
        session_id=user_state.get("session_id") or "",
    )
    message_text = f"Shipment #{reference_id} has been marked delivered."
    return _workflow_response(
        "DELIVERY_CONFIRMATION",
        {**result, "message": message_text, "card_type": "delivery_confirmation_card", "suggestions": ["Generate invoice", "Notify customer"]},
        default_message=message_text,
        default_route=result.get("route") or "workflows.delivery_confirmation.completed",
        default_workflow="delivery_confirmation",
        card_type="delivery_confirmation_card",
        suggestions=["Generate invoice", "Notify customer"],
    )


def _ai_manage_delay_handler(context):
    message = str(context.get("message") or "").strip()
    user_state = context.get("user_state") or {}
    reference = _ai_extract_reference_context(message, user_state)
    reference_id = reference.get("reference_id")

    if reference_id is None:
        return _ai_build_response(
            "Share the shipment reference and delay duration so I can log the delay.",
            action="DELAY_MANAGEMENT",
            card_type="delay_alert_card",
            data={"reference_id": reference_id},
            status="needs_input",
            workflow="delay_management",
            route="workflows.delay_management.request_reference",
            suggestions=["Share shipment reference", "Share delay duration"],
            intent="DELAY_MANAGEMENT",
            confidence=context.get("intent_result", {}).get("confidence", 0),
        )

    delay_match = re.search(r"(\d{1,4})\s*(?:min|mins|minute|minutes|hr|hrs|hour|hours)", message.lower())
    delay_minutes = int(delay_match.group(1)) if delay_match else 60
    if "hour" in message.lower() or "hr" in message.lower():
        delay_minutes *= 60

    reason = message
    service = _get_logistics_workflow_service()
    result = service.manage_delay(
        shipment_id=reference_id,
        delay_minutes=delay_minutes,
        reason=reason,
        actor_role=str(context.get("role") or "driver").strip() or "driver",
        session_id=user_state.get("session_id") or "",
    )
    message_text = f"Shipment #{reference_id} is now marked delayed by {delay_minutes} minutes."
    return _workflow_response(
        "DELAY_MANAGEMENT",
        {**result, "message": message_text, "card_type": "delay_alert_card", "suggestions": ["Notify customer", "Track shipment"]},
        default_message=message_text,
        default_route=result.get("route") or "workflows.delay_management.updated",
        default_workflow="delay_management",
        card_type="delay_alert_card",
        suggestions=["Notify customer", "Track shipment"],
    )


def _ai_handle_failed_shipment_handler(context):
    message = str(context.get("message") or "").strip()
    user_state = context.get("user_state") or {}
    reference = _ai_extract_reference_context(message, user_state)
    reference_id = reference.get("reference_id")

    if reference_id is None:
        return _ai_build_response(
            "Share the shipment reference so I can record the failure.",
            action="FAILED_SHIPMENT",
            card_type="tracking_card",
            data={"reference_id": reference_id},
            status="needs_input",
            workflow="failed_shipment_handling",
            route="workflows.failed_shipment.request_reference",
            suggestions=["Share shipment reference", "Share failure reason"],
            intent="FAILED_SHIPMENT",
            confidence=context.get("intent_result", {}).get("confidence", 0),
        )

    service = _get_logistics_workflow_service()
    result = service.handle_failed_shipment(
        shipment_id=reference_id,
        reason=message,
        actor_role=str(context.get("role") or "admin").strip() or "admin",
        session_id=user_state.get("session_id") or "",
    )
    message_text = f"Shipment #{reference_id} has been marked as failed and customer notification was triggered."
    return _workflow_response(
        "FAILED_SHIPMENT",
        {**result, "message": message_text, "card_type": "tracking_card", "suggestions": ["Reassign driver", "Generate invoice"]},
        default_message=message_text,
        default_route=result.get("route") or "workflows.failed_shipment.updated",
        default_workflow="failed_shipment_handling",
        card_type="tracking_card",
        suggestions=["Reassign driver", "Generate invoice"],
    )


def _ai_notify_customer_handler(context):
    message = str(context.get("message") or "").strip()
    user_state = context.get("user_state") or {}
    reference = _ai_extract_reference_context(message, user_state)
    reference_id = reference.get("reference_id")

    if reference_id is None:
        return _ai_build_response(
            "Share the booking or shipment reference so I can notify the customer.",
            action="CUSTOMER_NOTIFICATION",
            card_type="text_reply",
            data={"reference_id": reference_id},
            status="needs_input",
            workflow="customer_notification",
            route="notifications.customer.request_reference",
            suggestions=["Share booking reference", "Share shipment reference"],
            intent="CUSTOMER_NOTIFICATION",
            confidence=context.get("intent_result", {}).get("confidence", 0),
        )

    booking_row = _fetch_booking_record_by_id(reference_id)
    shipment_row = None if booking_row else _fetch_shipment_record_by_id(reference_id)
    service = _get_logistics_workflow_service()
    result = service.notify_customer(
        title="Logistics update",
        message=message or "Your shipment has a new operational update.",
        level="info",
        booking_row=booking_row,
        shipment_row=shipment_row,
        session_id=user_state.get("session_id") or "",
    )
    message_text = f"Customer notification sent for reference #{reference_id}."
    return _workflow_response(
        "CUSTOMER_NOTIFICATION",
        {**result, "message": message_text, "card_type": "text_reply", "suggestions": ["Track shipment", "Open timeline"]},
        default_message=message_text,
        default_route=result.get("route") or "notifications.customer.sent",
        default_workflow="customer_notification",
        card_type="text_reply",
        suggestions=["Track shipment", "Open timeline"],
    )


def _log_missing_schema_columns(table_name, required_columns, actual_columns):
    missing_columns = sorted(set(required_columns) - set(actual_columns))
    if missing_columns:
        print(f"[schema][warning] {table_name} missing columns: {', '.join(missing_columns)}")
    return missing_columns


def validate_database_schema():
    schema_report = {}

    for table_name, required_columns in REQUIRED_SCHEMA_COLUMNS.items():
        try:
            actual_columns = _get_table_columns(table_name)
            missing_columns = _log_missing_schema_columns(table_name, required_columns, actual_columns)
            schema_report[table_name] = {
                "present": sorted(actual_columns),
                "missing": missing_columns,
            }
        except Exception as error:
            print(f"[schema][error] Unable to validate {table_name}: {error}")
            schema_report[table_name] = {
                "present": [],
                "missing": sorted(required_columns),
                "error": str(error),
            }

    return schema_report



def is_available_row(row):
    value = row.get("availability_status")

    if value is None:
        return False

    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return value == 1

    if isinstance(value, str):
        return value.strip().lower() in {"available", "yes", "true", "1"}

    return False


def fetch_available_lorries(table_name):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(f"SELECT * FROM {table_name}")
        rows = cursor.fetchall()
        return [row for row in rows if is_available_row(row)]
    except Error as error:
        raise RuntimeError(f"Database error while reading {table_name}: {error}") from error
    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def fetch_available_12_tyre_lorries():
    return fetch_available_lorries(TABLE_MAP["12"])


def fetch_available_14_tyre_lorries():
    return fetch_available_lorries(TABLE_MAP["14"])


def fetch_available_16_tyre_lorries():
    return fetch_available_lorries(TABLE_MAP["16"])


def _get_table_columns(table_name):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(f"SHOW COLUMNS FROM {table_name}")
        return {row[0] for row in cursor.fetchall()}
    except Error as error:
        raise RuntimeError(f"Database error while reading schema for {table_name}: {error}") from error
    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def _find_lorry_row(lorry_number):
    connection = None
    cursor = None
    target = str(lorry_number or "").strip()

    if not target:
        raise ValueError("Lorry number is required")

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        for table_name in TABLE_MAP.values():
            cursor.execute(
                f"SELECT * FROM {table_name} WHERE vehicle_number = %s LIMIT 1",
                (target,),
            )
            row = cursor.fetchone()
            if row:
                return table_name, row

        return None, None
    except Error as error:
        raise RuntimeError(f"Database error while locating lorry {target}: {error}") from error
    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def _extract_tracking_payload(table_name, row, lorry_number):
    columns = _get_table_columns(table_name)

    latitude = row.get("latitude") if "latitude" in columns else None
    longitude = row.get("longitude") if "longitude" in columns else None
    last_updated = row.get("last_updated") if "last_updated" in columns else None

    if latitude is None or longitude is None:
        return None

    try:
        latitude_value = float(latitude)
        longitude_value = float(longitude)
    except (TypeError, ValueError):
        return None

    if not (latitude_value == latitude_value and longitude_value == longitude_value):
        return None

    vehicle_number = str(row.get("vehicle_number") or lorry_number)

    return {
        "lorry_number": vehicle_number,
        "latitude": latitude_value,
        "longitude": longitude_value,
        "last_updated": str(last_updated) if last_updated is not None else "",
    }


def get_lorry_tracking_data(lorry_number):
    table_name, row = _find_lorry_row(lorry_number)

    if not table_name or not row:
        raise RuntimeError(f"Lorry {lorry_number} not found")

    tracking_payload = _extract_tracking_payload(table_name, row, lorry_number)
    if tracking_payload is None:
        return {
            "lorry_number": str(lorry_number),
            "message": "GPS data is unavailable for this lorry right now.",
        }

    return tracking_payload


def _get_booking_fleet_rows():
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            f"""
            SELECT *
            FROM {BOOKINGS_TABLE}
            WHERE lorry_number IS NOT NULL
              AND TRIM(lorry_number) <> ''
              AND (booking_status IS NULL OR booking_status NOT IN ('completed', 'cancelled'))
            ORDER BY created_at DESC, id DESC
            """
        )
        return cursor.fetchall()
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def _get_route_geometry(start_latitude, start_longitude, destination_latitude, destination_longitude):
    try:
        url = (
            "https://router.project-osrm.org/route/v1/driving/"
            f"{float(start_longitude)},{float(start_latitude)};{float(destination_longitude)},{float(destination_latitude)}"
        )
        response = requests.get(
            url,
            params={"overview": "full", "geometries": "geojson", "steps": "false"},
            timeout=15,
        )

        if response.ok:
            data = response.json()
            route = data.get("routes", [{}])[0]
            geometry = route.get("geometry", {})
            coords = geometry.get("coordinates", [])
            if coords:
                route_coordinates = [[float(lat), float(lng)] for lng, lat in coords if lat == lat and lng == lng]
                route_distance = route.get("distance")
                if isinstance(route_distance, (int, float)) and route_distance == route_distance:
                    return {
                        "distance_km": float(route_distance) / 1000.0,
                        "coordinates": route_coordinates,
                        "source": "osrm",
                    }
    except Exception as error:
        logger.error(f"[gps][route] OSRM route geometry failed: {error}")

    fallback_coordinates = [
        [float(start_latitude), float(start_longitude)],
        [float(destination_latitude), float(destination_longitude)],
    ]
    fallback_distance = _calculate_straight_line_distance_km(
        start_latitude,
        start_longitude,
        destination_latitude,
        destination_longitude,
    )

    return {
        "distance_km": float(fallback_distance),
        "coordinates": fallback_coordinates,
        "source": "fallback",
    }


def _build_fleet_tracking_payload(booking_row):
    lorry_number = str(booking_row.get("lorry_number") or "").strip()
    if not lorry_number:
        return None

    tracking_data = None
    try:
        tracking_data = get_lorry_tracking_data(lorry_number)
    except RuntimeError:
        tracking_data = None

    if not tracking_data or tracking_data.get("message"):
        return {
            "booking_id": int(booking_row.get("id")),
            "lorry_number": lorry_number,
            "truck_type": str(booking_row.get("tyre_type") or booking_row.get("truck_type") or ""),
            "booking_status": str(booking_row.get("booking_status") or "pending"),
            "pickup_location": str(booking_row.get("pickup_location") or booking_row.get("source_location") or "").strip(),
            "drop_location": str(booking_row.get("drop_location") or booking_row.get("destination_location") or "").strip(),
            "latitude": None,
            "longitude": None,
            "last_updated": "",
            "distance_km": None,
            "eta_hours": None,
            "route_coordinates": [],
            "gps_available": False,
        }

    destination_location = str(booking_row.get("drop_location") or booking_row.get("destination_location") or "").strip()
    truck_type_code = _normalize_truck_type(booking_row.get("tyre_type") or booking_row.get("truck_type"), booking_row.get("tons"))
    truck_label = _canonical_truck_type_label(truck_type_code) if truck_type_code else str(booking_row.get("tyre_type") or booking_row.get("truck_type") or "")

    destination_coordinates = None
    if destination_location:
        try:
            destination_coordinates = _get_coordinates(destination_location)
        except Exception:
            destination_coordinates = None

    route_coordinates = []
    distance_km = None
    eta_hours = None

    if destination_coordinates and _is_lat_lng_tuple(destination_coordinates):
        route_info = _get_route_geometry(
            tracking_data["latitude"],
            tracking_data["longitude"],
            destination_coordinates[0],
            destination_coordinates[1],
        )
        distance_km = float(route_info["distance_km"])
        route_coordinates = route_info["coordinates"]
        eta_hours = _estimate_eta_hours(distance_km, truck_type_code or "12")

    if eta_hours is None and booking_row.get("price") is not None:
        try:
            eta_hours = _estimate_eta_hours(float(booking_row.get("distance") or 0), truck_type_code or "12")
        except Exception:
            eta_hours = None

    return {
        "booking_id": int(booking_row.get("id")),
        "lorry_number": lorry_number,
        "truck_type": truck_label,
        "booking_status": str(booking_row.get("booking_status") or "pending"),
        "pickup_location": str(booking_row.get("pickup_location") or booking_row.get("source_location") or "").strip(),
        "drop_location": destination_location,
        "latitude": float(tracking_data["latitude"]),
        "longitude": float(tracking_data["longitude"]),
        "last_updated": str(tracking_data.get("last_updated") or ""),
        "distance_km": round(float(distance_km), 1) if distance_km is not None else None,
        "eta_hours": eta_hours,
        "route_coordinates": route_coordinates,
        "gps_available": True,
    }


def _broadcast_live_fleet_update(updates=None):
    try:
        if isinstance(updates, list) and updates:
            # Emit lightweight per-truck updates and also aggregate
            trucks = []
            for u in updates:
                try:
                    payload = {
                        "booking_id": u.get("booking_id"),
                        "lorry_number": u.get("lorry_number"),
                        "latitude": u.get("latitude"),
                        "longitude": u.get("longitude"),
                        "source_location": u.get("source_location"),
                        "destination_location": u.get("destination_location"),
                        "last_updated": u.get("timestamp"),
                        "traffic_slowdown": u.get("traffic_slowdown", 0.0),
                    }
                    trucks.append(payload)
                    # Per-truck socket
                    socketio.emit("truck:location", payload)
                except Exception:
                    continue

            # Aggregate fleet update
            socketio.emit("fleet:update", {"status": "success", "count": len(trucks), "trucks": trucks})
            return

        # Fallback: read fleet rows and compute full payload
        fleet_rows = _get_booking_fleet_rows()
        trucks = []

        for row in fleet_rows:
            truck_payload = _build_fleet_tracking_payload(row)
            if truck_payload is not None:
                trucks.append(truck_payload)

        socketio.emit(
            "fleet:update",
            {
                "status": "success",
                "count": len(trucks),
                "trucks": trucks,
            },
        )
    except Exception as error:
        logger.warning(f"[socketio][fleet:update][warning] {error}")


def _emit_socket_event(event_name, payload):
    try:
        socketio.emit(event_name, payload)
        return True
    except Exception as error:
        logger.warning(f"[socketio][{event_name}][warning] {error}")
        return False


def _broadcast_chat_update(payload):
    chat_payload = {
        "status": str(payload.get("status") or "success").strip() or "success",
        "message": str(payload.get("message") or payload.get("reply") or "").strip(),
        "reply": str(payload.get("reply") or payload.get("message") or "").strip(),
        "action": str(payload.get("action") or payload.get("intent") or "TRACK_SHIPMENT").strip() or "TRACK_SHIPMENT",
        "card_type": str(payload.get("card_type") or "tracking_card").strip() or "tracking_card",
        "workflow": str(payload.get("workflow") or "tracking").strip() or "tracking",
        "route": str(payload.get("route") or "tracking.update").strip() or "tracking.update",
        "intent": str(payload.get("intent") or payload.get("action") or "TRACK_SHIPMENT").strip() or "TRACK_SHIPMENT",
        "data": payload.get("data") or {},
        "suggestions": payload.get("suggestions") or [],
        "source": str(payload.get("source") or "system").strip() or "system",
        "timestamp": _current_timestamp().isoformat(sep=" ", timespec="seconds"),
    }
    _emit_socket_event("chat:update", chat_payload)
    return chat_payload


def _broadcast_tracking_snapshot(shipment_row=None, booking_row=None, source="system", event_name="tracking:update", status_note=""):
    snapshot = None
    if shipment_row:
        snapshot = _build_shipment_live_tracking_payload(shipment_row)
    elif booking_row:
        snapshot = _build_fleet_tracking_payload(booking_row)

    payload = {
        "status": "success",
        "source": source,
        "event": event_name,
        "shipment": _serialize_shipment_row(shipment_row) if shipment_row else None,
        "booking": _serialize_booking_row(booking_row) if booking_row else None,
        "snapshot": snapshot,
        "status_note": status_note,
        "timestamp": _current_timestamp().isoformat(sep=" ", timespec="seconds"),
    }

    if snapshot:
        payload.update(snapshot)

    try:
        target_row = shipment_row or booking_row
        if target_row and target_row.get("id") is not None:
            _insert_shipment_event_log(
                int(target_row.get("id")),
                str(event_name or "tracking_update").replace(":", "_").replace("/", "_"),
                title=str(status_note or event_name or "Tracking update").replace("_", " ").title(),
                message=str(status_note or "Tracking update").strip(),
                severity="info",
                source=str(source or "system").strip() or "system",
                metadata=payload,
            )
    except Exception as error:
        logger.warning(f"[event_log][tracking_snapshot][warning] {error}")

    _emit_socket_event(event_name, payload)
    try:
        role = "customer"
        if booking_row is None and shipment_row is not None:
            role = "customer"
        if snapshot and snapshot.get("shipment"):
            operational = _build_shipment_operational_context(shipment_row, role=role, include_invoice=True)
            if operational:
                _broadcast_chat_update(
                    {
                        "status": "success",
                        "message": operational.get("reply") or status_note or "Tracking update received.",
                        "reply": operational.get("reply") or status_note or "Tracking update received.",
                        "action": "TRACK_SHIPMENT",
                        "card_type": "tracking_card",
                        "workflow": "tracking",
                        "route": event_name,
                        "intent": "TRACK_SHIPMENT",
                        "data": operational.get("data") or {},
                        "suggestions": ["Track shipment", "Download invoice", "Contact driver"],
                        "source": source,
                    }
                )
    except Exception as error:
        logger.warning(f"[socketio][chat:update][warning] {error}")


def _broadcast_realtime_activity(entity_type, action, payload, event_name=None):
    event_name = event_name or f"{entity_type}:update"
    realtime_payload = {
        "status": "success",
        "entity_type": entity_type,
        "action": action,
        "timestamp": _current_timestamp().isoformat(sep=" ", timespec="seconds"),
        "data": payload,
    }
    _emit_socket_event(event_name, realtime_payload)


def _broadcast_realtime_notification(title, message, level="info", data=None):
    payload = {
        "status": "success",
        "title": str(title or "").strip(),
        "message": str(message or "").strip(),
        "level": str(level or "info").strip(),
        "data": data or {},
        "timestamp": _current_timestamp().isoformat(sep=" ", timespec="seconds"),
    }
    _emit_socket_event("notification:update", payload)
    try:
        notification_data = data or {}
        shipment = notification_data.get("shipment") if isinstance(notification_data, dict) else None
        booking = notification_data.get("booking") if isinstance(notification_data, dict) else None
        if shipment and shipment.get("id") is not None:
            operational = _build_shipment_operational_context(_fetch_shipment_record_by_id(int(shipment.get("id"))), role="customer", include_invoice=True)
            if operational:
                _broadcast_chat_update(
                    {
                        "status": "success",
                        "message": operational.get("reply") or payload["message"],
                        "reply": operational.get("reply") or payload["message"],
                        "action": "TRACK_SHIPMENT",
                        "card_type": "tracking_card",
                        "workflow": "tracking",
                        "route": "notifications.customer",
                        "intent": "TRACK_SHIPMENT",
                        "data": operational.get("data") or {},
                        "suggestions": ["Track shipment", "Download invoice", "Contact driver"],
                        "source": "notification.update",
                    }
                )
        elif booking and booking.get("id") is not None:
            operational = _build_booking_operational_context(_fetch_booking_record_by_id(int(booking.get("id"))), role="customer", include_invoice=True)
            if operational:
                _broadcast_chat_update(
                    {
                        "status": "success",
                        "message": operational.get("reply") or payload["message"],
                        "reply": operational.get("reply") or payload["message"],
                        "action": "MAKE_PAYMENT",
                        "card_type": "payment_card",
                        "workflow": "payments",
                        "route": "notifications.customer",
                        "intent": "MAKE_PAYMENT",
                        "data": operational.get("data") or {},
                        "suggestions": ["Pay invoice", "Download invoice", "Track shipment"],
                        "source": "notification.update",
                    }
                )
    except Exception as error:
        logger.warning(f"[socketio][chat:update][warning] {error}")


def _broadcast_monitoring_alert(message, level="warning", data=None):
    _emit_socket_event(
        "monitoring:alert",
        {
            "status": "success",
            "level": str(level or "warning").strip(),
            "message": str(message or "").strip(),
            "data": data or {},
            "timestamp": _current_timestamp().isoformat(sep=" ", timespec="seconds"),
        },
    )


def _find_booking_rows_by_lorry_number(lorry_number):
    target = str(lorry_number or "").strip()
    if not target:
        return []

    try:
        return [row for row in _fetch_booking_records() if str(row.get("lorry_number") or "").strip() == target]
    except Exception:
        return []


def _get_gps_logs_for_lorry(lorry_number, limit=25):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT lorry_number, latitude, longitude, source_location, destination_location, created_at
            FROM gps_logs
            WHERE lorry_number = %s
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            (str(lorry_number), int(limit)),
        )
        rows = cursor.fetchall()
        logs = []
        for row in rows:
            try:
                logs.append({
                    "lorry_number": str(row.get("lorry_number") or lorry_number),
                    "latitude": float(row.get("latitude")),
                    "longitude": float(row.get("longitude")),
                    "source_location": str(row.get("source_location") or "").strip(),
                    "destination_location": str(row.get("destination_location") or "").strip(),
                    "created_at": row.get("created_at").isoformat(sep=" ", timespec="seconds") if row.get("created_at") else None,
                })
            except Exception:
                continue
        return logs
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


if gps_simulator is not None:
    gps_simulator.set_update_callback(_broadcast_live_fleet_update)


@socketio.on("connect")
def handle_tracking_connect():
    # Require authenticated socket connections (JWT or API key)
    global MONITORING_WEBSOCKET_CONNECTIONS, MONITORING_WEBSOCKET_CONNECTS_TOTAL
    try:
        auth_header = request.headers.get("Authorization") or ""
        api_key = request.headers.get("X-API-KEY") or request.args.get("api_key")

        if auth_header.startswith("Bearer "):
            try:
                verify_jwt_in_request()
                with MONITORING_LOCK:
                    MONITORING_WEBSOCKET_CONNECTIONS += 1
                    MONITORING_WEBSOCKET_CONNECTS_TOTAL += 1
                return {"status": "connected"}
            except Exception:
                raise ConnectionRefusedError("unauthorized")

        if api_key:
            key_row = _validate_api_key(api_key)
            if key_row:
                with MONITORING_LOCK:
                    MONITORING_WEBSOCKET_CONNECTIONS += 1
                    MONITORING_WEBSOCKET_CONNECTS_TOTAL += 1
                return {"status": "connected"}

        # no valid auth
        raise ConnectionRefusedError("unauthorized")
    except ConnectionRefusedError:
        raise
    except Exception:
        raise ConnectionRefusedError("unauthorized")


@socketio.on("disconnect")
def handle_tracking_disconnect():
    global MONITORING_WEBSOCKET_CONNECTIONS, MONITORING_WEBSOCKET_DISCONNECTS_TOTAL
    with MONITORING_LOCK:
        MONITORING_WEBSOCKET_CONNECTIONS = max(0, MONITORING_WEBSOCKET_CONNECTIONS - 1)
        MONITORING_WEBSOCKET_DISCONNECTS_TOTAL += 1


def _normalize_truck_type(truck_type, tons=None):
    if tons is not None:
        tons_code = _truck_type_from_tons(tons)
        if tons_code:
            return tons_code
        raise ValueError("Unable to determine tyre type")

    value = str(truck_type or "").strip().lower()

    if value in {"12", "14", "16"}:
        return value

    if "12" in value:
        return "12"

    if "14" in value:
        return "14"

    if "16" in value:
        return "16"

    raise ValueError("Unable to determine tyre type")


def _get_recommended_tyre_type(tons):
    """Map load weight (tons) to recommended tyre type with logging.
    
    Business rules:
    - 20–25 tons → 12 tyre
    - 25–30 tons → 14 tyre
    - 30–35 tons → 16 tyre
    
    Returns: "12", "14", or "16" (tyre code), or "" if out of range
    """
    try:
        tons_value = int(float(tons))
    except (TypeError, ValueError):
        print(f"[booking][tyre_recommendation] invalid_input tons={tons!r} result=none")
        return ""

    if 20 <= tons_value < 25:
        print(f"[booking][tyre_recommendation] tons={tons_value} result=12_tyre")
        return "12"
    elif 25 <= tons_value < 30:
        print(f"[booking][tyre_recommendation] tons={tons_value} result=14_tyre")
        return "14"
    elif 30 <= tons_value <= 35:
        print(f"[booking][tyre_recommendation] tons={tons_value} result=16_tyre")
        return "16"
    else:
        print(f"[booking][tyre_recommendation] tons={tons_value} result=out_of_range")
        return ""


def _truck_type_from_tons(tons):
    """Wrapper for backward compatibility. Uses the correct business rules."""
    return _get_recommended_tyre_type(tons)


def _canonical_truck_type_label(truck_type_code):
    code = str(truck_type_code or "").strip()
    if code in {"12", "14", "16"}:
        return f"{code} tyre"
    raise ValueError("Unsupported tyre type")


def _extract_numeric_value(value):
    text = str(value or "").strip()
    if not text:
        return ""

    match = re.search(r"\d+(?:\.\d+)?", text.replace(",", ""))
    if not match:
        return ""

    number = float(match.group(0))
    if not number.is_integer():
        return ""

    return str(int(number))


def _normalize_booking_text(value):
    text = str(value or "").strip()
    return text if text else ""


def _fallback_booking_details(message):
    text = str(message or "").strip()
    if not text:
        return dict(EMPTY_BOOKING_JSON)

    patterns = [
        r"from\s+(?P<source>.+?)\s+to\s+(?P<destination>.+?)\s+for\s+(?P<tons>\d+(?:\.\d+)?)\s*tons?",
        r"(?P<tons>\d+(?:\.\d+)?)\s*tons?\s+from\s+(?P<source>.+?)\s+to\s+(?P<destination>.+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue

        source = _normalize_booking_text(match.group("source"))
        destination = _normalize_booking_text(match.group("destination"))
        tons = _extract_numeric_value(match.group("tons"))

        if source and destination and tons:
            truck_type_code = _truck_type_from_tons(tons)
            return {
                "source": source,
                "destination": destination,
                "tons": tons,
                "truck_type": _canonical_truck_type_label(truck_type_code) if truck_type_code else "",
            }

    return dict(EMPTY_BOOKING_JSON)


def _get_available_lorry_for_tyre_type(truck_type, tons=None):
    normalized_tyre_type = _normalize_truck_type(truck_type, tons)
    table_name = TABLE_MAP.get(normalized_tyre_type)

    if table_name is None:
        raise ValueError("Unsupported tyre type")

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(f"SELECT * FROM {table_name}")

        for row in cursor.fetchall():
            if not is_available_row(row):
                continue

            lorry_number = (
                row.get("lorry_number")
                or row.get("vehicle_number")
                or row.get("registration_number")
                or row.get("number")
            )

            if not lorry_number:
                continue

            return {
                "table_name": table_name,
                "lorry_number": str(lorry_number),
            }

        raise RuntimeError(f"No available lorry found for tyre type {normalized_tyre_type}")
    except Error as error:
        raise RuntimeError(f"Database error while selecting lorry from {table_name}: {error}") from error
    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def _build_conversational_booking_summary(booking_details, distance_km, price, tyre_type_label):
    """Generate smart conversational booking summary using Gemini."""
    total_price = int(round(float(price)))
    token_amount = int(round(total_price * 0.8))
    source = str(booking_details.get("source") or booking_details.get("exact_pickup") or "").strip()
    destination = str(booking_details.get("destination") or booking_details.get("exact_delivery") or "").strip()
    
    if not source or not destination:
        # Fallback to standard summary if missing details
        return (
            f"📦 **Shipment Summary**\n\n"
            f"Distance: {int(round(distance_km))} km\n"
            f"Truck Type: {tyre_type_label}\n"
            f"Fare: ₹{total_price}\n"
            f"Advance Token (80%): ₹{token_amount}\n\n"
            f"Ready to proceed to payment?"
        )
    
    # Try to generate conversational summary with Gemini
    try:
        if not GEMINI_API_KEY:
            return ""
        
        model = _get_gemini_model()
        if not model:
            return ""
        
        prompt = f"""Generate a brief, friendly booking summary for this transport shipment.
Be conversational, not robotic.

From: {source}
To: {destination}
Distance: {int(round(distance_km))} km
Truck: {tyre_type_label}
Fare: ₹{total_price}
Advance (80%): ₹{token_amount}

Return ONLY the summary text (no JSON, no markdown formatting). Keep it 2-3 sentences.""".strip()
        
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(max_output_tokens=80, temperature=0.6),
            request_options={"timeout": GEMINI_TIMEOUT_SECONDS},
        )
        
        summary = str(getattr(response, "text", "")).strip()
        if summary:
            return summary
    except TimeoutError as e:
        print(f"[gemini][timeout] booking_summary error={e}")
    except Exception as e:
        logger.exception(f"[booking][summary] Gemini generation failed: {e}")
    
    return ""


def _generate_driver_truck_assignment():
    """Generate simulated driver/truck assignment after booking confirmation."""
    # Simulated driver names and truck numbers
    driver_names = [
        "Ramesh Kumar", "Vikram Singh", "Pradeep Reddy", "Mohammed Hassan",
        "Anil Kumar", "Suresh Patel", "Rajesh Sharma", "Arjun Verma",
        "Naveen Kumar", "Deepak Yadav",
    ]
    
    truck_prefixes = {
        "AP": "Andhra Pradesh",
        "TG": "Telangana", 
        "KA": "Karnataka",
        "MH": "Maharashtra",
    }
    
    import random
    
    driver_name = random.choice(driver_names)
    prefix = random.choice(list(truck_prefixes.keys()))
    truck_number = f"{prefix}{random.randint(10, 99)}{chr(random.randint(65, 90))}{random.randint(1000, 9999)}"
    eta_mins = random.randint(30, 90)
    
    return {
        "driver_name": driver_name,
        "truck_number": truck_number,
        "eta_mins": eta_mins,
        "status": "Confirmed",
        "message": f"Driver {driver_name} ({truck_number}) will arrive in ~{eta_mins} mins. Live tracking enabled."
    }



def _build_booking_insert_payload(user_id, booking_details, distance_km, price, lorry_number, token_amount=None):
    total_price = int(round(float(price)))
    token_value = int(round(float(token_amount if token_amount is not None else total_price * 0.8)))
    
    tons = int(float(str(booking_details.get("tons", "") or 0).strip() or 0))
    tyre_code = _get_recommended_tyre_type(tons)
    tyre_label = _canonical_truck_type_label(tyre_code) if tyre_code else str(booking_details.get("truck_type") or booking_details.get("tyre_type") or "").strip()
    
    print(f"[booking][tyre_recommendation] db_persistence user={user_id} tons={tons} tyre_code={tyre_code} tyre_label={tyre_label} lorry={lorry_number}")

    return {
        "user_id": str(user_id or "default_user").strip(),
        "source_location": str(booking_details.get("source") or booking_details.get("source_location") or "").strip(),
        "destination_location": str(booking_details.get("destination") or booking_details.get("destination_location") or "").strip(),
        "exact_pickup_location": str(booking_details.get("exact_pickup") or booking_details.get("exact_pickup_location") or "").strip(),
        "exact_delivery_location": str(booking_details.get("exact_delivery") or booking_details.get("exact_delivery_location") or "").strip(),
        "tons": tons,
        "tyre_type": tyre_label,
        "distance": int(round(float(distance_km))),
        "distance_km": int(round(float(distance_km))),
        "price": total_price,
        "token_amount": token_value,
        "payment_status": "paid",
        "booking_status": "confirmed",
        "lorry_number": str(lorry_number or "").strip(),
    }


def _insert_booking(user_id, booking_details, distance_km, price, lorry_number, customer_name="", token_amount=None):
    connection = None
    cursor = None

    try:
        payload = _build_booking_insert_payload(user_id, booking_details, distance_km, price, lorry_number, token_amount)
        connection = get_db_connection()
        cursor = connection.cursor()

        table_columns = _get_table_columns(BOOKINGS_TABLE)
        insert_columns = []
        insert_values = []

        for column_name, column_value in payload.items():
            if column_name in table_columns:
                insert_columns.append(column_name)
                insert_values.append(column_value)

        if customer_name and "customer_name" in table_columns:
            insert_columns.append("customer_name")
            insert_values.append(customer_name)

        missing_required = sorted(set(REQUIRED_SCHEMA_COLUMNS[BOOKINGS_TABLE]) - table_columns)
        if missing_required:
            print(f"[schema][warning] {BOOKINGS_TABLE} missing columns at insert time: {', '.join(missing_required)}")

        if not insert_columns:
            raise RuntimeError(f"No compatible columns available in {BOOKINGS_TABLE}")

        columns_sql = ", ".join(insert_columns)
        placeholders_sql = ", ".join(["%s"] * len(insert_columns))
        cursor.execute(
            f"""
            INSERT INTO {BOOKINGS_TABLE}
                ({columns_sql})
            VALUES ({placeholders_sql})
            """,
            insert_values,
        )
        connection.commit()
    except Error as error:
        if connection is not None:
            connection.rollback()
        raise RuntimeError(f"Database error while creating booking: {error}") from error
    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def _normalize_form_truck_type(truck_type):
    normalized = re.sub(r"\s+", " ", str(truck_type or "").strip().lower())

    if normalized in {"12", "12 tyre", "12 tyres", "12-tyre", "12 tyre lorry"}:
        return "12 tyre"
    if normalized in {"14", "14 tyre", "14 tyres", "14-tyre", "14 tyre lorry"}:
        return "14 tyre"
    if normalized in {"16", "16 tyre", "16 tyres", "16-tyre", "16 tyre lorry"}:
        return "16 tyre"

    return ""


def _calculate_form_booking_price(truck_type, load_weight):
    pricing = BOOKING_FORM_TRUCK_TYPES.get(truck_type)
    if not pricing:
        raise ValueError("Unsupported truck type")

    return int(round(pricing["base_price"] + (float(load_weight) * pricing["rate_per_ton"])))


def _normalize_phone_number(phone):
    cleaned = re.sub(r"[\s()\-]", "", str(phone or "").strip())
    if cleaned.startswith("+"):
        digits = cleaned[1:]
    else:
        digits = cleaned

    if not digits.isdigit() or not (7 <= len(digits) <= 15):
        raise ValueError("Enter a valid phone number with 7 to 15 digits")

    return f"+{digits}" if cleaned.startswith("+") else digits


def _parse_bool(value, default=False):
    if value is None:
        return bool(default)

    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return value != 0

    normalized = str(value).strip().lower()
    if not normalized:
        return bool(default)

    return normalized in {"1", "true", "yes", "y", "on", "enabled"}


def _normalize_booking_field(value, field_name):
    cleaned = re.sub(r"\s+", " ", str(value or "").strip())
    if len(cleaned) < 3:
        raise ValueError(f"{field_name} must be at least 3 characters long")

    if len(cleaned) > 255:
        raise ValueError(f"{field_name} must be 255 characters or fewer")

    return cleaned


def _serialize_booking_row(row):
    if not row:
        return None

    booking_reference = _build_booking_reference(row["id"], row.get("created_at"))
    truck_type = str(row.get("truck_type") or row.get("tyre_type") or "").strip()
    pickup_location = str(row.get("pickup_location") or row.get("source_location") or "").strip()
    drop_location = str(row.get("drop_location") or row.get("destination_location") or "").strip()
    load_weight = row.get("load_weight")
    price = row.get("price")

    return {
        "id": int(row["id"]),
        "booking_reference": booking_reference,
        "customer_name": str(row.get("customer_name") or "").strip(),
        "phone": str(row.get("phone") or "").strip(),
        "pickup_location": pickup_location,
        "drop_location": drop_location,
        "truck_type": truck_type,
        "load_weight": float(load_weight) if load_weight is not None else None,
        "price": int(round(float(price))) if price is not None else None,
        "booking_status": str(row.get("booking_status") or "").strip() or "pending",
        "created_at": row.get("created_at").isoformat(sep=" ", timespec="seconds") if row.get("created_at") else None,
        "payment_status": str(row.get("payment_status") or "").strip() or "pending",
        "lorry_number": str(row.get("lorry_number") or "").strip(),
        "source_location": str(row.get("source_location") or "").strip(),
        "destination_location": str(row.get("destination_location") or "").strip(),
        "exact_pickup_location": str(row.get("exact_pickup_location") or "").strip(),
        "exact_delivery_location": str(row.get("exact_delivery_location") or "").strip(),
        "tons": int(row["tons"]) if row.get("tons") is not None else None,
        "tyre_type": str(row.get("tyre_type") or "").strip(),
        "distance": int(row["distance"]) if row.get("distance") is not None else None,
        "token_amount": int(row["token_amount"]) if row.get("token_amount") is not None else None,
        "whatsapp_notifications_enabled": bool(row.get("whatsapp_notifications_enabled", 1)),
        "whatsapp_booking_confirmation": bool(row.get("whatsapp_booking_confirmation", 1)),
        "whatsapp_payment_confirmation": bool(row.get("whatsapp_payment_confirmation", 1)),
        "whatsapp_live_location": bool(row.get("whatsapp_live_location", 1)),
        "whatsapp_delivery_updates": bool(row.get("whatsapp_delivery_updates", 1)),
    }


def _normalize_shipment_status(value, fallback="pending"):
    status = _normalize_auth_text(value).lower()
    allowed_statuses = {"pending", "confirmed", "assigned", "in_transit", "delivered", "cancelled", "delayed", "failed"}

    if not status:
        return fallback

    return status if status in allowed_statuses else fallback


def _serialize_shipment_row(row):
    if not row:
        return None

    return {
        "id": int(row["id"]),
        "user_id": str(row.get("user_id") or "").strip(),
        "pickup_location": str(row.get("pickup_location") or "").strip(),
        "drop_location": str(row.get("drop_location") or "").strip(),
        "cargo_type": str(row.get("cargo_type") or "").strip(),
        "truck_type": str(row.get("truck_type") or "").strip(),
        "weight": float(row["weight"]) if row.get("weight") is not None else None,
        "distance_km": float(row["distance_km"]) if row.get("distance_km") is not None else 0,
        "estimated_price": int(row["estimated_price"]) if row.get("estimated_price") is not None else 0,
        "payment_status": str(row.get("payment_status") or "pending").strip() or "pending",
        "shipment_status": str(row.get("shipment_status") or "pending").strip() or "pending",
        "assigned_driver_id": int(row["assigned_driver_id"]) if row.get("assigned_driver_id") is not None else None,
        "created_at": row.get("created_at").isoformat(sep=" ", timespec="seconds") if row.get("created_at") else None,
        "booking_reference": f"SHP-{int(row['id']):06d}",
    }


def _get_shipment_pricing(distance_km, truck_type, weight):
    truck_code = _normalize_truck_type(truck_type, weight)
    truck_multiplier = {"12": 1.0, "14": 1.18, "16": 1.35}.get(truck_code, 1.0)
    cargo_weight = float(weight or 0)
    weight_multiplier = 1.0 + max(0.0, (cargo_weight - 10.0) * 0.015)
    distance_value = float(distance_km or 0)
    base_distance_rate = {"12": 38, "14": 44, "16": 52}.get(truck_code, 38)
    estimated_price = (distance_value * base_distance_rate * truck_multiplier * weight_multiplier) + 2500
    return {
        "truck_code": truck_code,
        "estimated_price": int(round(estimated_price)),
    }


def _fetch_shipment_record_by_id(shipment_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(f"SELECT * FROM {SHIPMENTS_TABLE} WHERE id = %s LIMIT 1", (int(shipment_id),))
        return cursor.fetchone()
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def _fetch_shipment_records_for_user(user_id, limit=None):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        query = f"SELECT * FROM {SHIPMENTS_TABLE} WHERE user_id = %s ORDER BY created_at DESC, id DESC"
        params = [str(user_id)]
        if limit is not None:
            query += " LIMIT %s"
            params.append(max(1, min(int(limit or 0), 200)))

        cursor.execute(query, tuple(params))
        return cursor.fetchall()
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def _fetch_shipment_records(limit=None):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        query = f"SELECT * FROM {SHIPMENTS_TABLE} ORDER BY created_at DESC, id DESC"
        params = []
        if limit is not None:
            query += " LIMIT %s"
            params.append(max(1, min(int(limit or 0), 200)))

        cursor.execute(query, tuple(params))
        return cursor.fetchall()
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def _fetch_shipment_records_for_driver(driver_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            f"SELECT * FROM {SHIPMENTS_TABLE} WHERE assigned_driver_id = %s ORDER BY created_at DESC, id DESC",
            (int(driver_id),),
        )
        return cursor.fetchall()
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


SEARCH_ROUTE_PRESETS = [
    {"pickup_location": "Hyderabad", "drop_location": "Bangalore"},
    {"pickup_location": "Chennai", "drop_location": "Pune"},
    {"pickup_location": "Mumbai", "drop_location": "Delhi"},
]


def _normalize_search_location(value):
    return re.sub(r"\s+", " ", str(value or "").strip())


def _normalize_search_route_label(pickup_location, drop_location):
    pickup = _normalize_search_location(pickup_location)
    drop = _normalize_search_location(drop_location)
    if not pickup or not drop:
        return ""
    return f"{pickup} → {drop}"


def _route_search_key(pickup_location, drop_location):
    pickup = _normalize_search_location(pickup_location).lower()
    drop = _normalize_search_location(drop_location).lower()
    return f"{pickup}||{drop}"


def _route_sort_timestamp(row):
    created_at = row.get("created_at") if isinstance(row, dict) else None
    if hasattr(created_at, "timestamp"):
        try:
            return float(created_at.timestamp())
        except Exception:
            return 0.0
    return 0.0


def _coerce_route_truck_type(row):
    truck_type = _normalize_search_location((row or {}).get("truck_type") or (row or {}).get("tyre_type"))
    if truck_type:
        normalized_form = _normalize_form_truck_type(truck_type)
        if normalized_form:
            return normalized_form

    truck_code = _normalize_truck_type(truck_type or "", (row or {}).get("weight") or (row or {}).get("load_weight") or 0)
    return _canonical_truck_type_label(truck_code)


def _build_route_record(row, source):
    if not row:
        return None

    pickup_location = _normalize_search_location(
        row.get("pickup_location") or row.get("source_location") or row.get("exact_pickup_location")
    )
    drop_location = _normalize_search_location(
        row.get("drop_location") or row.get("destination_location") or row.get("exact_delivery_location")
    )

    if not pickup_location or not drop_location:
        return None

    truck_type = _coerce_route_truck_type(row)
    weight_value = row.get("weight") or row.get("load_weight") or row.get("tons") or 0
    distance_value = row.get("distance_km") or row.get("distance")
    estimated_price_value = row.get("estimated_price") or row.get("price") or 0
    eta_hours = None

    try:
        distance_number = float(distance_value) if distance_value is not None else None
        if distance_number is not None:
            truck_code = truck_type.split()[0] if truck_type else _normalize_truck_type("", weight_value)
            eta_hours = _estimate_eta_hours(distance_number, truck_code)
    except Exception:
        eta_hours = None

    return {
        "id": int(row.get("id") or 0),
        "route_key": _route_search_key(pickup_location, drop_location),
        "pickup_location": pickup_location,
        "drop_location": drop_location,
        "route_label": _normalize_search_route_label(pickup_location, drop_location),
        "truck_type": truck_type,
        "estimated_price": int(round(float(estimated_price_value))) if estimated_price_value is not None else None,
        "eta_hours": round(float(eta_hours), 2) if eta_hours is not None else None,
        "distance_km": round(float(distance_value), 2) if distance_value is not None else None,
        "weight": round(float(weight_value), 2) if weight_value is not None else None,
        "created_at": row.get("created_at").isoformat(sep=" ", timespec="seconds") if row.get("created_at") else None,
        "created_at_raw": row.get("created_at"),
        "source": source,
        "status": str(row.get("booking_status") or row.get("shipment_status") or "pending").strip() or "pending",
        "reference": str(row.get("booking_reference") or row.get("reference") or row.get("shipment_reference") or "").strip(),
    }


def _build_route_catalog(user_identifier=None, phone=None, limit=120):
    booking_rows = _fetch_booking_records_for_user(user_identifier=user_identifier, phone=phone, limit=limit) if (user_identifier or phone) else _fetch_booking_records()[:limit]
    shipment_rows = _fetch_shipment_records_for_user(user_identifier, limit=limit) if user_identifier else (_fetch_shipment_records(limit=limit) if not phone else [])

    route_groups = {}
    pickup_counter = Counter()
    drop_counter = Counter()
    route_counter = Counter()
    history_rows = []

    for source, rows in (("booking", booking_rows), ("shipment", shipment_rows)):
        for row in rows or []:
            record = _build_route_record(row, source)
            if not record:
                continue

            history_rows.append(record)
            route_counter[record["route_key"]] += 1
            pickup_counter[record["pickup_location"]] += 1
            drop_counter[record["drop_location"]] += 1

            group = route_groups.setdefault(record["route_key"], [])
            group.append(record)

    route_cards = []
    for route_key, rows in route_groups.items():
        sorted_rows = sorted(rows, key=_route_sort_timestamp, reverse=True)
        latest = sorted_rows[0]
        total_price = sum(int(item.get("estimated_price") or 0) for item in rows)
        total_eta = [item.get("eta_hours") for item in rows if item.get("eta_hours") is not None]
        truck_counts = Counter(item.get("truck_type") for item in rows if item.get("truck_type"))
        route_cards.append(
            {
                "route_key": route_key,
                "route_label": latest["route_label"],
                "pickup_location": latest["pickup_location"],
                "drop_location": latest["drop_location"],
                "count": len(rows),
                "estimated_price": int(round(total_price / len(rows))) if rows else None,
                "eta_hours": round(sum(total_eta) / len(total_eta), 2) if total_eta else latest.get("eta_hours"),
                "suggested_truck_type": truck_counts.most_common(1)[0][0] if truck_counts else latest.get("truck_type") or "12 tyre",
                "last_seen": latest.get("created_at"),
                "last_seen_raw": latest.get("created_at_raw"),
                "source": latest.get("source") or "booking",
            }
        )

    route_cards.sort(key=lambda item: (item.get("count", 0), item.get("last_seen_raw") or datetime.min), reverse=True)

    def _location_items(counter, field_name):
        items = []
        for location, count in counter.most_common():
            if not location:
                continue
            items.append({"label": location, "value": location, "field": field_name, "count": count})
        return items

    return {
        "history_rows": history_rows,
        "route_cards": route_cards,
        "pickup_locations": _location_items(pickup_counter, "pickup"),
        "drop_locations": _location_items(drop_counter, "drop"),
        "route_counter": route_counter,
    }


def _build_live_route_estimate(pickup_location, drop_location, load_weight=None, truck_type=None):
    pickup = _normalize_search_location(pickup_location)
    drop = _normalize_search_location(drop_location)
    if not pickup or not drop:
        return None

    truck_label = _normalize_form_truck_type(truck_type) or _canonical_truck_type_label(_normalize_truck_type(truck_type or "", load_weight or 0))
    truck_code = truck_label.split()[0] if truck_label else _normalize_truck_type(truck_type or "", load_weight or 0)

    try:
        distance_km, estimated_price = _estimate_distance_and_price(pickup, drop, truck_code)
        eta_hours = _estimate_eta_hours(distance_km, truck_code)
    except Exception as error:
        return {
            "pickup_location": pickup,
            "drop_location": drop,
            "truck_type": truck_label or _canonical_truck_type_label(truck_code),
            "load_weight": float(load_weight) if load_weight not in (None, "") else None,
            "estimated_price": None,
            "eta_hours": None,
            "distance_km": None,
            "suggested_truck_type": truck_label or _canonical_truck_type_label(truck_code),
            "reply": str(error),
        }

    return {
        "pickup_location": pickup,
        "drop_location": drop,
        "truck_type": truck_label,
        "load_weight": float(load_weight) if load_weight not in (None, "") else None,
        "estimated_price": int(round(float(estimated_price))),
        "eta_hours": round(float(eta_hours), 2) if eta_hours is not None else None,
        "distance_km": round(float(distance_km), 2),
        "suggested_truck_type": truck_label,
        "reply": _build_fare_estimation_reply(
            pickup,
            drop,
            float(load_weight) if load_weight not in (None, "") else 0,
            truck_label,
            distance_km,
            estimated_price,
            eta_hours,
        ),
    }


def _match_route_suggestions(catalog, query, field, limit=8):
    query_text = _normalize_search_location(query).lower()
    field_name = _normalize_search_location(field).lower() or "pickup"
    suggestions = []

    def _score(value, route_label, source_text, kind):
        if not query_text:
            return 1.0

        haystack = " ".join([value, route_label, source_text]).lower()
        score = 0.0
        if haystack.startswith(query_text):
            score += 4.0
        if query_text in haystack:
            score += 2.0
        if kind == "route":
            score += 0.5
        if field_name == "pickup" and value.lower().startswith(query_text):
            score += 1.0
        if field_name == "drop" and value.lower().startswith(query_text):
            score += 1.0
        return score

    for location in catalog["pickup_locations"]:
        if query_text and query_text not in location["label"].lower() and field_name == "drop":
            continue
        if query_text and query_text not in location["label"].lower() and query_text not in location["value"].lower():
            continue
        suggestions.append({**location, "kind": "location", "route_label": "", "score": _score(location["value"], "", location["field"], "location")})

    for location in catalog["drop_locations"]:
        if query_text and query_text not in location["label"].lower() and field_name == "pickup":
            continue
        if query_text and query_text not in location["label"].lower() and query_text not in location["value"].lower():
            continue
        suggestions.append({**location, "kind": "location", "route_label": "", "score": _score(location["value"], "", location["field"], "location")})

    for route in catalog["route_cards"]:
        route_label = route["route_label"]
        if query_text and query_text not in route_label.lower() and query_text not in route["pickup_location"].lower() and query_text not in route["drop_location"].lower():
            continue
        suggestions.append(
            {
                **route,
                "kind": "route",
                "label": route_label,
                "value": route_label,
                "score": _score(route_label, route_label, route.get("source") or "history", "route"),
            }
        )

    if not query_text:
        suggestions.extend(
            {
                **route,
                "kind": "route",
                "label": route["route_label"],
                "value": route["route_label"],
                "score": 1.0,
            }
            for route in catalog["route_cards"][:limit]
        )

    suggestions.sort(key=lambda item: (-float(item.get("score") or 0), -int(item.get("count") or 0), item.get("label") or item.get("value") or ""))
    deduped = []
    seen = set()
    for item in suggestions:
        key = (item.get("kind"), _normalize_search_location(item.get("value") or item.get("label")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append({k: v for k, v in item.items() if k != "score"})
        if len(deduped) >= limit:
            break

    return deduped


def _insert_shipment_record(payload, user_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            f"""
            INSERT INTO {SHIPMENTS_TABLE}
                (user_id, pickup_location, drop_location, cargo_type, truck_type, weight, distance_km, estimated_price, payment_status, shipment_status, assigned_driver_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(user_id),
                str(payload.get("pickup_location") or "").strip(),
                str(payload.get("drop_location") or "").strip(),
                str(payload.get("cargo_type") or "").strip(),
                str(payload.get("truck_type") or "").strip(),
                float(payload.get("weight") or 0),
                float(payload.get("distance_km") or 0),
                int(payload.get("estimated_price") or 0),
                str(payload.get("payment_status") or "pending").strip() or "pending",
                str(payload.get("shipment_status") or "pending").strip() or "pending",
                payload.get("assigned_driver_id"),
            ),
        )
        connection.commit()
        shipment_id = cursor.lastrowid
        try:
            shipment_row = _fetch_shipment_record_by_id(shipment_id)
            if shipment_row:
                _broadcast_realtime_activity("shipment", "created", {"shipment": _serialize_shipment_row(shipment_row)}, event_name="shipment:activity")
                _broadcast_tracking_snapshot(shipment_row=shipment_row, source="shipment.create", event_name="tracking:update", status_note="Shipment created")
        except Exception as error:
            logger.warning(f"[socketio][shipment:create][warning] {error}")
        return shipment_id
    except Error as error:
        if connection is not None:
            connection.rollback()
        raise RuntimeError(f"Database error while creating shipment: {error}") from error
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def _update_shipment_record(shipment_id, updates):
    connection = None
    cursor = None

    allowed_fields = {
        "pickup_location": "pickup_location",
        "drop_location": "drop_location",
        "cargo_type": "cargo_type",
        "truck_type": "truck_type",
        "weight": "weight",
        "distance_km": "distance_km",
        "estimated_price": "estimated_price",
        "payment_status": "payment_status",
        "shipment_status": "shipment_status",
        "assigned_driver_id": "assigned_driver_id",
    }

    columns = []
    params = []
    for key, column in allowed_fields.items():
        if key not in updates:
            continue
        columns.append(f"{column} = %s")
        params.append(updates[key])

    if not columns:
        raise ValueError("No valid shipment fields provided")

    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        params.append(int(shipment_id))
        cursor.execute(f"UPDATE {SHIPMENTS_TABLE} SET {', '.join(columns)} WHERE id = %s", params)
        connection.commit()
        updated_rows = cursor.rowcount
        try:
            shipment_row = _fetch_shipment_record_by_id(shipment_id)
            serialized = _serialize_shipment_row(shipment_row) if shipment_row else {"id": int(shipment_id), **updates}
            _broadcast_realtime_activity("shipment", "updated", {"shipment": serialized, "updates": updates}, event_name="shipment:update")
            status_value = str(updates.get("shipment_status") or "").strip().lower()
            if status_value in {"confirmed", "assigned", "loading", "in_transit", "out_for_delivery", "delivered", "completed", "delayed", "failed"}:
                _broadcast_tracking_snapshot(shipment_row=shipment_row, source="shipment.status", event_name="eta:update", status_note=status_value or "updated")
                if status_value in {"delayed", "failed"}:
                    _broadcast_realtime_notification(
                        "Shipment exception",
                        f"Shipment #{shipment_id} status changed to {status_value}.",
                        "warning" if status_value == "delayed" else "critical",
                        {"shipment": serialized, "status": status_value},
                    )
                if status_value in {"delivered", "completed"}:
                    _broadcast_realtime_activity("delivery", "confirmed", {"shipment": serialized}, event_name="delivery:confirmed")
        except Exception as error:
            logger.warning(f"[socketio][shipment:update][warning] {error}")
        return updated_rows
    except Error as error:
        if connection is not None:
            connection.rollback()
        raise RuntimeError(f"Database error while updating shipment {shipment_id}: {error}") from error
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def _shipment_timeline_table():
    return SHIPMENT_STATUS_LOGS_TABLE


def _insert_shipment_status_log(shipment_id, status, note="", location="", actor_role="system", metadata=None):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            f"""
            INSERT INTO {SHIPMENT_STATUS_LOGS_TABLE}
                (shipment_id, status, note, location, actor_role, metadata_json)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                int(shipment_id),
                _normalize_shipment_status(status),
                str(note or "").strip(),
                str(location or "").strip(),
                str(actor_role or "system").strip() or "system",
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        connection.commit()
        status_log_id = cursor.lastrowid
        try:
            shipment_row = _fetch_shipment_record_by_id(shipment_id)
            serialized_shipment = _serialize_shipment_row(shipment_row) if shipment_row else {"id": int(shipment_id)}
            normalized_status = _normalize_shipment_status(status)
            status_payload = {
                "shipment_id": int(shipment_id),
                "status": normalized_status,
                "note": str(note or "").strip(),
                "location": str(location or "").strip(),
                "actor_role": str(actor_role or "system").strip() or "system",
                "metadata": metadata or {},
                "shipment": serialized_shipment,
            }
            _insert_shipment_event_log(
                shipment_id,
                f"shipment_status_{normalized_status}",
                title=normalized_status.replace("_", " ").title(),
                message=str(note or normalized_status).strip(),
                severity="warning" if normalized_status in {"delayed", "failed"} else "info",
                source="status_log",
                metadata=status_payload,
            )
            _broadcast_realtime_activity("shipment", normalized_status, status_payload, event_name="shipment:activity")
            _broadcast_tracking_snapshot(shipment_row=shipment_row, source="shipment.status_log", event_name="tracking:update", status_note=normalized_status)
            if normalized_status in {"delivered", "completed", "confirmed"}:
                _broadcast_realtime_activity("delivery", "confirmed", status_payload, event_name="delivery:confirmed")
        except Exception as error:
            logger.warning(f"[socketio][shipment:status_log][warning] {error}")
        return status_log_id
    except Error as error:
        if connection is not None:
            connection.rollback()
        raise RuntimeError(f"Database error while storing shipment status log: {error}") from error
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def _insert_shipment_event_log(shipment_id, event_type, title="", message="", severity="info", source="system", metadata=None):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO shipment_event_logs
                (shipment_id, event_type, title, message, severity, source, metadata_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                int(shipment_id),
                str(event_type or "shipment_event").strip() or "shipment_event",
                str(title or "").strip(),
                str(message or "").strip(),
                str(severity or "info").strip() or "info",
                str(source or "system").strip() or "system",
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        connection.commit()
        return cursor.lastrowid
    except Error as error:
        if connection is not None:
            connection.rollback()
        raise RuntimeError(f"Database error while storing shipment event log: {error}") from error
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def _fetch_shipment_status_logs(shipment_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            f"SELECT * FROM {SHIPMENT_STATUS_LOGS_TABLE} WHERE shipment_id = %s ORDER BY created_at ASC, id ASC",
            (int(shipment_id),),
        )
        return cursor.fetchall()
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def _ensure_user_pref_tables():
    """Create user_preferences and user_searches tables if they don't exist."""
    return True


def _get_user_preferences(user_id):
    from db import get_user_preferences

    if not user_id:
        return {}

    return get_user_preferences(user_id)


def _set_user_preferences(user_id, prefs):
    if not user_id:
        raise RuntimeError('Authentication required')
    from db import save_user_preferences

    try:
        save_user_preferences(user_id, prefs)
        return True
    except Error as error:
        raise RuntimeError(f"Database error while saving preferences: {error}") from error


def _delete_user_preferences(user_id):
    if not user_id:
        raise RuntimeError('Authentication required')

    from db import delete_user_preferences

    return delete_user_preferences(user_id)


def _log_user_search(user_id, query, metadata=None):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        _ensure_user_pref_tables()
        cursor.execute("INSERT INTO user_searches (user_id, query, metadata_json) VALUES (%s, %s, %s)", (int(user_id) if user_id else None, str(query or ''), json.dumps(metadata or {})))
        connection.commit()
        return cursor.lastrowid
    except Error as error:
        if connection is not None:
            connection.rollback()
        raise RuntimeError(f"Database error while logging search: {error}") from error
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def _fetch_user_searches(user_id=None, limit=10):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        _ensure_user_pref_tables()
        if user_id:
            cursor.execute("SELECT id, query, metadata_json, created_at FROM user_searches WHERE user_id = %s ORDER BY created_at DESC LIMIT %s", (int(user_id), int(limit)))
        else:
            cursor.execute("SELECT id, query, metadata_json, created_at FROM user_searches ORDER BY created_at DESC LIMIT %s", (int(limit),))
        rows = cursor.fetchall()
        for r in rows:
            try:
                r['metadata'] = json.loads(r.get('metadata_json') or '{}')
            except Exception:
                r['metadata'] = {}
        return rows
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def _fetch_user_recommendations(user_id=None, limit=6):
    # Simple recommendations: combine recent searches and frequent routes from shipments
    recommendations = []
    try:
        recent = _fetch_user_searches(user_id=user_id, limit=12)
        seen = set()
        for r in recent:
            q = (r.get('query') or '').strip()
            if not q or q in seen:
                continue
            seen.add(q)
            recommendations.append({
                'label': q,
                'value': q,
                'source': 'recent_search',
            })
            if len(recommendations) >= limit:
                break

        if len(recommendations) < limit:
            # fallback to top routes from shipments
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT pickup_location, drop_location, COUNT(*) as cnt FROM shipments GROUP BY pickup_location, drop_location ORDER BY cnt DESC LIMIT %s", (limit,))
            rows = cursor.fetchall() if cursor else []
            for r in rows:
                label = f"{r.get('pickup_location') or ''} → {r.get('drop_location') or ''}".strip()
                if label and label not in seen:
                    seen.add(label)
                    recommendations.append({
                        'label': label,
                        'value': label,
                        'count': r.get('cnt') or 0,
                        'source': 'popular_route',
                    })
                if len(recommendations) >= limit:
                    break
    except Exception:
        pass

    return recommendations[:limit]


def _serialize_shipment_event_row(row):
    if not row:
        return None

    metadata = row.get("metadata_json")
    if isinstance(metadata, str) and metadata.strip():
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {"raw": metadata}
    elif not isinstance(metadata, dict):
        metadata = {}

    return {
        "id": int(row["id"]),
        "shipment_id": int(row["shipment_id"]),
        "event_type": str(row.get("event_type") or "shipment_event").strip() or "shipment_event",
        "title": str(row.get("title") or "").strip(),
        "message": str(row.get("message") or "").strip(),
        "severity": str(row.get("severity") or "info").strip() or "info",
        "source": str(row.get("source") or "system").strip() or "system",
        "metadata": metadata,
        "created_at": row.get("created_at").isoformat(sep=" ", timespec="seconds") if row.get("created_at") else None,
    }
    
def _build_shipment_activity_feed_event(shipment_row, event_row=None):
    shipment = _serialize_shipment_row(shipment_row)
    if not shipment:
        return None

    driver = None
    if shipment_row.get("assigned_driver_id") is not None:
        try:
            driver = _serialize_driver_row(_get_driver_record_by_id(shipment_row.get("assigned_driver_id")))
        except Exception:
            driver = None

    live_payload = _build_shipment_live_tracking_payload(shipment_row) or {}
    eta_hours = live_payload.get("eta_hours") if live_payload.get("eta_hours") is not None else shipment.get("eta_hours")
    latest_location = live_payload.get("live_location") or {}
    route_summary = f"{shipment.get('pickup_location') or 'Pickup'} → {shipment.get('drop_location') or 'Drop'}"
    timestamp_value = None
    if event_row and event_row.get("created_at"):
      timestamp_value = event_row.get("created_at").isoformat(sep=" ", timespec="seconds")
    elif shipment_row.get("created_at"):
      timestamp_value = shipment_row.get("created_at").isoformat(sep=" ", timespec="seconds")

    shipment_reference = f"SHP{shipment.get('id')}"
    driver_reference = f"DRV{driver.get('id')}" if driver and driver.get("id") is not None else ""
    event_type = str((event_row or {}).get("event_type") or shipment.get("shipment_status") or "shipment_update").strip() or "shipment_update"
    event_title = str((event_row or {}).get("title") or event_type.replace("_", " ").title()).strip()
    event_message = str((event_row or {}).get("message") or (event_row or {}).get("title") or shipment.get("shipment_status") or "Shipment update").strip()

    return {
        "id": int((event_row or {}).get("id") or shipment.get("id") or 0),
        "shipment_id": shipment.get("id"),
        "shipment_reference": shipment_reference,
        "driver_reference": driver_reference,
        "driver": driver,
        "title": event_title,
        "message": event_message,
        "event_type": event_type,
        "severity": str((event_row or {}).get("severity") or "info").strip() or "info",
        "source": str((event_row or {}).get("source") or "system").strip() or "system",
        "timestamp": timestamp_value,
        "shipment_status": str(shipment.get("shipment_status") or "pending").strip() or "pending",
        "payment_status": str(shipment.get("payment_status") or "pending").strip() or "pending",
        "eta_hours": eta_hours,
        "eta_phrase": _format_eta_phrase(eta_hours),
        "route_summary": route_summary,
        "latest_location": latest_location,
        "shipment": shipment,
        "timeline": live_payload.get("timeline") or [],
        "status_badge": event_title,
        "kind": event_type.replace("shipment_", "").replace("_", " ").strip() or "shipment update",
        "actions": _build_chat_action_cards(
            shipment_id=shipment.get("id"),
            driver=driver,
            download_url=f"/api/shipments/{shipment.get('id')}/invoice?download=1",
            payment_data={"order_id": None, "download_url": f"/api/shipments/{shipment.get('id')}/invoice?download=1"},
            role="customer",
        ),
    }


def _build_shipment_activity_feed_payload(limit=12, shipment_id=None, user_identifier=None):
    try:
        shipment_limit = max(1, min(int(limit or 12), 30))
    except Exception:
        shipment_limit = 12

    if shipment_id is not None:
        target_shipments = []
        shipment_row = _fetch_shipment_record_by_id(shipment_id)
        if shipment_row:
            target_shipments.append(shipment_row)
    elif user_identifier:
        target_shipments = _fetch_shipment_records_for_user(user_identifier, limit=shipment_limit * 3)
    else:
        target_shipments = _fetch_shipment_records(limit=shipment_limit * 3)

    shipments = [_serialize_shipment_row(row) for row in target_shipments if row]
    active_statuses = {"confirmed", "assigned", "loading", "in_transit", "out_for_delivery", "delayed"}
    active_shipments = [shipment for shipment in shipments if str(shipment.get("shipment_status") or "").strip().lower() in active_statuses][:shipment_limit]
    recent_shipments = shipments[:shipment_limit]

    feed_events = []
    for row in target_shipments[:shipment_limit]:
        shipment_events = _fetch_shipment_event_logs(int(row.get("id")))
        if shipment_events:
            feed_events.extend(shipment_events[:3])

    if not feed_events and shipments:
        for shipment in shipments[:shipment_limit]:
            feed_events.append({
                "id": shipment.get("id"),
                "shipment_id": shipment.get("id"),
                "event_type": str(shipment.get("shipment_status") or "shipment_update"),
                "title": str(shipment.get("shipment_status") or "Shipment update").replace("_", " ").title(),
                "message": f"Shipment {shipment.get('booking_reference') or shipment.get('id')} is {shipment.get('shipment_status') or 'pending'}.",
                "severity": "info",
                "source": "shipment.snapshot",
                "metadata": {"shipment": shipment},
                "created_at": shipment.get("created_at"),
            })

    feed_events = sorted(feed_events, key=lambda item: str(item.get("created_at") or ""), reverse=True)[:shipment_limit]

    summary_counts = {
        "active_shipments": len(active_shipments),
        "recent_shipments": len(recent_shipments),
        "payment_updates": len([event for event in feed_events if "payment" in str(event.get("event_type") or "").lower()]),
        "driver_updates": len([event for event in feed_events if "driver" in str(event.get("event_type") or "").lower()]),
        "gps_updates": len([event for event in feed_events if any(key in str(event.get("event_type") or "").lower() for key in ["gps", "tracking", "eta"]) ]),
        "delivery_updates": len([event for event in feed_events if any(key in str(event.get("event_type") or "").lower() for key in ["delivery", "delivered", "completed"]) ]),
        "invoice_updates": len([event for event in feed_events if "invoice" in str(event.get("event_type") or "").lower()]),
        "delay_alerts": len([event for event in feed_events if any(key in str(event.get("event_type") or "").lower() for key in ["delay", "delayed", "failed"]) ]),
    }

    return {
        "summary": summary_counts,
        "active_shipments": [
            {
                **shipment,
                "activity": _build_shipment_activity_feed_event(_fetch_shipment_record_by_id(shipment.get("id")))
            }
            for shipment in active_shipments
            if shipment.get("id") is not None
        ],
        "recent_shipments": [
            {
                **shipment,
                "activity": _build_shipment_activity_feed_event(_fetch_shipment_record_by_id(shipment.get("id")))
            }
            for shipment in recent_shipments
            if shipment.get("id") is not None
        ],
        "feed": [
            {
                **event,
                "shipment": event.get("shipment") or (_serialize_shipment_row(_fetch_shipment_record_by_id(event.get("shipment_id"))) if event.get("shipment_id") is not None else None),
                "event_card": _build_shipment_activity_feed_event(_fetch_shipment_record_by_id(event.get("shipment_id"))) if event.get("shipment_id") is not None else None,
            }
            for event in feed_events
        ],
    }


def _format_eta_phrase(eta_hours):
    if eta_hours is None:
        return "ETA unavailable"

    try:
        value = float(eta_hours)
    except Exception:
        return "ETA unavailable"

    if value <= 0:
        return "arriving soon"
    if value < 1:
        return "less than 1 hour"

    rounded = round(value, 1)
    if float(rounded).is_integer():
        rounded = int(rounded)
    suffix = "hour" if float(rounded) == 1 else "hours"
    return f"{rounded} {suffix}"


def _format_currency_amount(value):
    try:
        amount = int(round(float(value)))
    except Exception:
        amount = 0
    return f"₹{amount:,}"


def _build_chat_action_cards(*, shipment_id=None, driver=None, download_url="", payment_data=None, role="customer"):
    actions = []
    shipment_ref = f"SHP{int(shipment_id)}" if shipment_id is not None else ""
    driver_phone = str((driver or {}).get("phone") or "").strip()

    if shipment_id is not None:
        actions.append(
            {
                "key": "track_shipment",
                "label": "Track shipment",
                "kind": "send",
                "prompt": f"Track shipment {shipment_ref}",
            }
        )

    if payment_data and (payment_data.get("order_id") or payment_data.get("download_url")):
        actions.append(
            {
                "key": "pay_invoice",
                "label": "Pay invoice",
                "kind": "payment",
            }
        )

    if shipment_id is not None and driver_phone:
        actions.append(
            {
                "key": "contact_driver",
                "label": "Contact driver",
                "kind": "send",
                "prompt": f"Contact the driver for shipment {shipment_ref}",
            }
        )

    if download_url:
        actions.append(
            {
                "key": "download_invoice",
                "label": "Download invoice",
                "kind": "open_url",
                "url": download_url,
            }
        )

    if role in {"admin", "super_admin"} and shipment_id is not None:
        actions.append(
            {
                "key": "notify_customer",
                "label": "Notify customer",
                "kind": "send",
                "prompt": f"Notify customer about shipment {shipment_ref}",
            }
        )

    return actions[:4]


def _build_shipment_operational_context(shipment_row, *, role="customer", payment_rows=None, include_invoice=True):
    shipment = _serialize_shipment_row(shipment_row)
    if not shipment:
        return None

    live_payload = _build_shipment_live_tracking_payload(shipment_row) or {}
    shipment_id = shipment.get("id")
    events = _fetch_shipment_event_logs(shipment_id)
    timeline = live_payload.get("timeline") or []
    latest_event = events[0] if events else None
    driver = None
    if shipment_row.get("assigned_driver_id") is not None:
        try:
            driver = _serialize_driver_row(_get_driver_record_by_id(shipment_row.get("assigned_driver_id")))
        except Exception:
            driver = None

    payments = [_serialize_payment_row(row) for row in (payment_rows if payment_rows is not None else _fetch_payment_records_for_shipment(shipment_id))]
    latest_payment = next((payment for payment in payments if payment), None)
    paid_total = sum(int(payment.get("amount") or 0) for payment in payments if str(payment.get("payment_status") or "").lower() == "paid")
    estimated_total = int(round(float(shipment_row.get("estimated_price") or shipment.get("estimated_price") or 0)))
    outstanding_total = max(estimated_total - paid_total, 0)
    payment_summary = {
        "latest_payment": latest_payment,
        "payments": payments,
        "paid_total": paid_total,
        "estimated_total": estimated_total,
        "outstanding_total": outstanding_total,
        "status": "paid" if outstanding_total <= 0 and paid_total > 0 else str(shipment_row.get("payment_status") or shipment.get("payment_status") or "pending"),
        "summary": f"Paid {_format_currency_amount(paid_total)} of {_format_currency_amount(estimated_total)}. Outstanding {_format_currency_amount(outstanding_total)}.",
    }

    route_phrase = f"{shipment.get('pickup_location') or 'Pickup'} → {shipment.get('drop_location') or 'Drop'}"
    location_value = ""
    if live_payload.get("live_location"):
        live_location = live_payload.get("live_location") or {}
        location_value = str(live_location.get("display_name") or live_location.get("address") or live_location.get("location") or live_location.get("source_location") or "").strip()
        if not location_value and live_location.get("latitude") is not None and live_location.get("longitude") is not None:
            location_value = f"{float(live_location.get('latitude')):.4f}, {float(live_location.get('longitude')):.4f}"

    event_summary = latest_event.get("title") or latest_event.get("message") or "No recent shipment events" if latest_event else "No recent shipment events"
    eta_hours = live_payload.get("eta_hours") if live_payload.get("eta_hours") is not None else shipment.get("eta_hours")
    eta_phrase = _format_eta_phrase(eta_hours)
    status_text = str(shipment.get("shipment_status") or live_payload.get("summary", {}).get("status") or "pending").strip() or "pending"
    shipment_ref = f"SHP{shipment_id}"

    if role == "driver":
        reply = (
            f"Shipment {shipment_ref} is {status_text}. {route_phrase}. ETA {eta_phrase}. "
            f"Latest event: {event_summary}."
        )
    elif role in {"admin", "super_admin"}:
        reply = (
            f"Shipment {shipment_ref} is {status_text} on {route_phrase}. ETA {eta_phrase}. "
            f"Payments: {payment_summary['summary']}. Latest event: {event_summary}."
        )
    else:
        reply = (
            f"Shipment {shipment_ref} is {status_text} near {location_value or shipment.get('drop_location') or shipment.get('pickup_location') or 'the planned route'}. "
            f"ETA {eta_phrase}. {driver.get('driver_name') if driver else 'Driver not assigned yet'}."
        )

    invoice_payload = None
    if include_invoice:
        try:
            invoice_payload = _build_shipment_invoice_payload(shipment_row, (payment_rows if payment_rows is not None else _fetch_payment_records_for_shipment(shipment_id)))
        except Exception:
            invoice_payload = None

    actions = _build_chat_action_cards(
        shipment_id=shipment_id,
        driver=driver,
        download_url=f"/api/shipments/{shipment_id}/invoice?download=1" if include_invoice else "",
        payment_data={**payment_summary, "order_id": (latest_payment or {}).get("razorpay_order_id")},
        role=role,
    )

    return {
        "reply": reply,
        "data": {
            "shipment": shipment,
            "live_tracking": live_payload,
            "timeline": timeline,
            "shipment_events": events,
            "latest_event": latest_event,
            "payment_summary": payment_summary,
            "invoice": invoice_payload,
            "driver": driver,
            "route_summary": route_phrase,
            "eta_hours": eta_hours,
            "eta_phrase": eta_phrase,
            "status_text": status_text,
            "actions": actions,
        },
        "actions": actions,
        "download_url": f"/api/shipments/{shipment_id}/invoice?download=1" if include_invoice else "",
    }


def _build_booking_operational_context(booking_row, *, role="customer", include_invoice=True):
    booking = _serialize_booking_row(booking_row)
    if not booking:
        return None

    booking_id = booking.get("id")
    payment_rows = _fetch_payment_records_for_booking(booking_id)
    payments = [_serialize_payment_row(row) for row in payment_rows]
    latest_payment = next((payment for payment in payments if payment), None)
    paid_total = sum(int(payment.get("amount") or 0) for payment in payments if str(payment.get("payment_status") or "").lower() == "paid")
    estimated_total = int(round(float(booking_row.get("price") or booking.get("price") or 0)))
    outstanding_total = max(estimated_total - paid_total, 0)
    payment_summary = {
        "latest_payment": latest_payment,
        "payments": payments,
        "paid_total": paid_total,
        "estimated_total": estimated_total,
        "outstanding_total": outstanding_total,
        "status": "paid" if outstanding_total <= 0 and paid_total > 0 else str(booking_row.get("payment_status") or booking.get("payment_status") or "pending"),
        "summary": f"Paid {_format_currency_amount(paid_total)} of {_format_currency_amount(estimated_total)}. Outstanding {_format_currency_amount(outstanding_total)}.",
    }

    route_phrase = f"{booking.get('source_location') or booking.get('pickup_location') or 'Pickup'} → {booking.get('destination_location') or booking.get('drop_location') or 'Drop'}"
    eta_phrase = _format_eta_phrase(booking.get("eta_hours"))
    reply = (
        f"Booking #{booking_id} is {str(booking.get('booking_status') or 'pending').strip() or 'pending'} on {route_phrase}. "
        f"ETA {eta_phrase}. {payment_summary['summary']}"
    )

    invoice_payload = None
    if include_invoice:
        try:
            invoice_payload = _build_invoice_payload(booking_row, payment_rows)
        except Exception:
            invoice_payload = None

    actions = _build_chat_action_cards(
        shipment_id=booking_id,
        download_url=f"/bookings/{booking_id}/invoice?download=1" if include_invoice else "",
        payment_data={**payment_summary, "order_id": (latest_payment or {}).get("razorpay_order_id")},
        role=role,
    )

    return {
        "reply": reply,
        "data": {
            "booking": booking,
            "payment_summary": payment_summary,
            "invoice": invoice_payload,
            "route_summary": route_phrase,
            "eta_phrase": eta_phrase,
            "actions": actions,
        },
        "actions": actions,
        "download_url": f"/bookings/{booking_id}/invoice?download=1" if include_invoice else "",
    }


def _fetch_shipment_event_logs(shipment_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT *
            FROM shipment_event_logs
            WHERE shipment_id = %s
            ORDER BY created_at DESC, id DESC
            """,
            (int(shipment_id),),
        )
        return [_serialize_shipment_event_row(row) for row in cursor.fetchall()]
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def _serialize_shipment_status_row(row):
    if not row:
        return None

    metadata = row.get("metadata_json")
    if isinstance(metadata, str) and metadata.strip():
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {"raw": metadata}
    elif not isinstance(metadata, dict):
        metadata = {}

    return {
        "id": int(row["id"]),
        "shipment_id": int(row["shipment_id"]),
        "status": str(row.get("status") or "pending").strip() or "pending",
        "note": str(row.get("note") or "").strip(),
        "location": str(row.get("location") or "").strip(),
        "actor_role": str(row.get("actor_role") or "system").strip() or "system",
        "metadata": metadata,
        "created_at": row.get("created_at").isoformat(sep=" ", timespec="seconds") if row.get("created_at") else None,
    }


def _fetch_shipment_timeline(shipment_id):
    return [_serialize_shipment_status_row(row) for row in _fetch_shipment_status_logs(shipment_id)]


def _build_shipment_invoice_payload(shipment_row, payment_rows):
    shipment = _serialize_shipment_row(shipment_row)
    payments = [_serialize_payment_row(row) for row in payment_rows or []]
    total_paid = sum(int(payment.get("amount") or 0) for payment in payments if str(payment.get("payment_status") or "").lower() == "paid")
    invoice_number = f"SHP-INV-{int(shipment_row.get('id')):06d}"

    return {
        "invoice_number": invoice_number,
        "issued_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "shipment": shipment,
        "payments": payments,
        "summary": {
            "subtotal": int(shipment_row.get("estimated_price") or 0),
            "paid_total": total_paid,
            "balance_due": max(0, int(shipment_row.get("estimated_price") or 0) - total_paid),
            "cargo_type": shipment_row.get("cargo_type"),
            "truck_type": shipment_row.get("truck_type"),
            "distance_km": float(shipment_row.get("distance_km") or 0),
            "weight": float(shipment_row.get("weight") or 0),
        },
    }


def _render_shipment_invoice_html(invoice_payload):
    shipment = invoice_payload.get("shipment") or {}
    summary = invoice_payload.get("summary") or {}
    payments = invoice_payload.get("payments") or []

    payment_rows = "".join(
        f"<tr><td>{idx + 1}</td><td>{str(payment.get('payment_type') or '')}</td><td>{str(payment.get('razorpay_order_id') or '-')}</td><td>{str(payment.get('razorpay_payment_id') or '-')}</td><td>₹{int(payment.get('amount') or 0):,}</td><td>{str(payment.get('payment_status') or '')}</td></tr>"
        for idx, payment in enumerate(payments)
    ) or "<tr><td colspan='6'>No payments recorded.</td></tr>"

    return f"""
    <!doctype html>
    <html>
    <head>
      <meta charset=\"utf-8\" />
      <title>Shipment Invoice {invoice_payload.get('invoice_number')}</title>
      <style>
        body {{ font-family: Arial, sans-serif; margin: 24px; background: #0f172a; color: #f8fafc; }}
        .sheet {{ max-width: 920px; margin: 0 auto; background: #111827; border: 1px solid #334155; border-radius: 18px; padding: 28px; }}
        .top {{ display: flex; justify-content: space-between; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; }}
        h1, h2, h3, p {{ margin: 0; }}
        .muted {{ color: #94a3b8; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 18px; }}
        th, td {{ text-align: left; padding: 12px 10px; border-bottom: 1px solid #334155; }}
        th {{ color: #cbd5e1; font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; }}
        .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 18px; }}
        .card {{ background: #0b1220; border: 1px solid #1f2937; border-radius: 16px; padding: 14px; }}
      </style>
    </head>
    <body>
      <div class=\"sheet\">
        <div class=\"top\">
          <div>
            <h1>Shipment Invoice</h1>
            <p class=\"muted\">{invoice_payload.get('invoice_number')}</p>
          </div>
          <div class=\"card\">
            <p><strong>Issued:</strong> {invoice_payload.get('issued_at')}</p>
            <p><strong>Status:</strong> {str(shipment.get('shipment_status') or 'pending')}</p>
          </div>
        </div>
        <div class=\"grid\">
          <div class=\"card\"><strong>Pickup</strong><p>{shipment.get('pickup_location')}</p></div>
          <div class=\"card\"><strong>Drop</strong><p>{shipment.get('drop_location')}</p></div>
          <div class=\"card\"><strong>Cargo</strong><p>{summary.get('cargo_type')}</p></div>
          <div class=\"card\"><strong>Truck</strong><p>{summary.get('truck_type')}</p></div>
          <div class=\"card\"><strong>Distance</strong><p>{summary.get('distance_km')} km</p></div>
          <div class=\"card\"><strong>Weight</strong><p>{summary.get('weight')} tons</p></div>
        </div>
        <table>
          <thead><tr><th>#</th><th>Type</th><th>Order ID</th><th>Payment ID</th><th>Amount</th><th>Status</th></tr></thead>
          <tbody>{payment_rows}</tbody>
        </table>
        <div class=\"grid\" style=\"margin-top: 18px;\">
          <div class=\"card\"><strong>Subtotal</strong><p>₹{int(summary.get('subtotal') or 0):,}</p></div>
          <div class=\"card\"><strong>Paid</strong><p>₹{int(summary.get('paid_total') or 0):,}</p></div>
          <div class=\"card\"><strong>Balance due</strong><p>₹{int(summary.get('balance_due') or 0):,}</p></div>
          <div class=\"card\"><strong>Shipment ID</strong><p>#{int(shipment.get('id') or 0)}</p></div>
        </div>
      </div>
    </body>
    </html>
    """



def _serialize_payment_row(row):
    if not row:
        return None

    return {
        "id": int(row["id"]),
        "booking_id": int(row["booking_id"]) if row.get("booking_id") is not None else None,
        "shipment_id": int(row["shipment_id"]) if row.get("shipment_id") is not None else None,
        "razorpay_order_id": str(row.get("razorpay_order_id") or "").strip(),
        "razorpay_payment_id": str(row.get("razorpay_payment_id") or "").strip(),
        "amount": int(row["amount"]) if row.get("amount") is not None else 0,
        "payment_status": str(row.get("payment_status") or row.get("status") or "").strip() or "created",
        "status": str(row.get("status") or row.get("payment_status") or "").strip() or "created",
        "payment_type": str(row.get("payment_type") or "advance").strip() or "advance",
        "created_at": row.get("created_at").isoformat(sep=" ", timespec="seconds") if row.get("created_at") else None,
    }


def _serialize_driver_row(row):
    if not row:
        return None

    return {
        "id": int(row["id"]),
        "driver_name": str(row.get("driver_name") or "").strip(),
        "phone": str(row.get("phone") or "").strip(),
        "license_number": str(row.get("license_number") or "").strip(),
        "assigned_truck": str(row.get("assigned_truck") or "").strip(),
        "assigned_truck_type": str(row.get("assigned_truck_type") or "").strip(),
        "status": str(row.get("status") or "available").strip() or "available",
        "rating": float(row["rating"]) if row.get("rating") is not None else 0.0,
        "experience_years": int(row["experience_years"]) if row.get("experience_years") is not None else 0,
        "next_available_at": row.get("next_available_at").isoformat(sep=" ", timespec="seconds") if row.get("next_available_at") else None,
        "last_updated": row.get("last_updated").isoformat(sep=" ", timespec="seconds") if row.get("last_updated") else None,
        "last_active": row.get("last_updated").isoformat(sep=" ", timespec="seconds") if row.get("last_updated") else None,
        "role": "driver",
        "api_key": str(row.get("api_key") or "").strip(),
        "created_at": row.get("created_at").isoformat(sep=" ", timespec="seconds") if row.get("created_at") else None,
    }


def _serialize_admin_user_row(row):
    if not row:
        return None

    return {
        "id": int(row["id"]),
        "full_name": str(row.get("full_name") or "").strip(),
        "email": str(row.get("email") or "").strip().lower(),
        "phone": str(row.get("phone") or "").strip(),
        "role": str(row.get("role") or "customer").strip() or "customer",
        "api_key": str(row.get("api_key") or "").strip(),
        "created_at": row.get("created_at").isoformat(sep=" ", timespec="seconds") if row.get("created_at") else None,
    }


def _generate_secure_api_key(prefix="tk"):
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def _mask_secret(value, start=6, end=4):
    text = str(value or "").strip()
    if len(text) <= start + end:
        return text
    return f"{text[:start]}...{text[-end:]}"


def _fetch_admin_user_records():
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users ORDER BY created_at DESC, id DESC")
        return cursor.fetchall()
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def _get_driver_record_by_id(driver_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(f"SELECT * FROM {DRIVERS_TABLE} WHERE id = %s LIMIT 1", (int(driver_id),))
        return cursor.fetchone()
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def _create_driver_record(payload):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            f"""
            INSERT INTO {DRIVERS_TABLE}
                (driver_name, phone, license_number, assigned_truck, assigned_truck_type, status, rating, experience_years, password_hash, api_key, next_available_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(payload.get("driver_name") or "").strip(),
                str(payload.get("phone") or "").strip(),
                str(payload.get("license_number") or "").strip(),
                str(payload.get("assigned_truck") or "").strip(),
                str(payload.get("assigned_truck_type") or "").strip(),
                str(payload.get("status") or "available").strip() or "available",
                float(payload.get("rating") or 0),
                int(payload.get("experience_years") or 0),
                _hash_password(str(payload.get("password") or "")),
                _generate_secure_api_key("drv"),
                payload.get("next_available_at"),
            ),
        )
        connection.commit()
        event_id = cursor.lastrowid
        try:
            shipment_row = _fetch_shipment_record_by_id(shipment_id)
            event_row = {
                "id": event_id,
                "shipment_id": int(shipment_id),
                "event_type": str(event_type or "shipment_event").strip() or "shipment_event",
                "title": str(title or "").strip(),
                "message": str(message or "").strip(),
                "severity": str(severity or "info").strip() or "info",
                "source": str(source or "system").strip() or "system",
                "created_at": _current_timestamp(),
            }
            activity_payload = _build_shipment_activity_feed_event(shipment_row, event_row) if shipment_row else None
            if activity_payload:
                _broadcast_realtime_activity("shipment", "activity", activity_payload, event_name="shipment:activity")
        except Exception as error:
            logger.warning(f"[socketio][shipment:event][warning] {error}")
        return event_id
    except Error as error:
        if connection is not None:
            connection.rollback()
        raise RuntimeError(f"Database error while creating driver: {error}") from error
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def _update_driver_auth_fields(driver_id, updates):
    connection = None
    cursor = None

    fields = []
    params = []

    if "password_hash" in updates:
        fields.append("password_hash = %s")
        params.append(updates["password_hash"])
    if "api_key" in updates:
        fields.append("api_key = %s")
        params.append(updates["api_key"])

    if not fields:
        raise ValueError("No auth fields to update")

    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        params.append(int(driver_id))
        cursor.execute(f"UPDATE {DRIVERS_TABLE} SET {', '.join(fields)} WHERE id = %s", params)
        connection.commit()
        updated_rows = cursor.rowcount
        try:
            if updated_rows:
                driver_row = _get_driver_record_by_id(driver_id)
                if driver_row:
                    _broadcast_realtime_activity("driver", "updated", {"driver": _serialize_driver_row(driver_row), "updates": updates}, event_name="driver:update")
        except Exception as error:
            logger.warning(f"[socketio][driver:update][warning] {error}")
        return updated_rows
    except Error as error:
        if connection is not None:
            connection.rollback()
        raise RuntimeError(f"Database error while updating driver auth fields: {error}") from error
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def _serialize_truck_row(row, truck_type):
    if not row:
        return None

    return {
        "vehicle_number": str(row.get("vehicle_number") or "").strip(),
        "truck_type": str(truck_type or "").strip(),
        "availability_status": str(row.get("availability_status") or "").strip() or "unknown",
        "latitude": float(row["latitude"]) if row.get("latitude") is not None else None,
        "longitude": float(row["longitude"]) if row.get("longitude") is not None else None,
        "last_updated": row.get("last_updated").isoformat(sep=" ", timespec="seconds") if row.get("last_updated") else None,
        "created_at": row.get("created_at").isoformat(sep=" ", timespec="seconds") if row.get("created_at") else None,
    }


def _fetch_payment_records():
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(f"SELECT * FROM {PAYMENTS_TABLE} ORDER BY created_at DESC, id DESC")
        return cursor.fetchall()
    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def _fetch_payment_records_for_booking(booking_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            f"SELECT * FROM {PAYMENTS_TABLE} WHERE booking_id = %s ORDER BY created_at DESC, id DESC",
            (int(booking_id),),
        )
        return cursor.fetchall()
    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def _fetch_payment_records_for_shipment(shipment_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            f"SELECT * FROM {PAYMENTS_TABLE} WHERE shipment_id = %s ORDER BY created_at DESC, id DESC",
            (int(shipment_id),),
        )
        return cursor.fetchall()
    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def _fetch_driver_records():
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(f"SELECT * FROM {DRIVERS_TABLE} ORDER BY FIELD(status, 'on_trip', 'available', 'maintenance', 'on_leave'), driver_name ASC")
        return cursor.fetchall()
    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def _fetch_truck_inventory():
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        trucks = []

        for truck_type_code, table_name in TABLE_MAP.items():
            cursor.execute(f"SELECT * FROM {table_name} ORDER BY last_updated DESC, id DESC")
            for row in cursor.fetchall():
                truck = _serialize_truck_row(row, f"{truck_type_code} tyre")
                if truck is not None:
                    truck["source_table"] = table_name
                    truck["is_live"] = bool(truck.get("latitude") is not None and truck.get("longitude") is not None)
                    trucks.append(truck)

        return trucks
    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def _build_booking_admin_record(booking_row, payment_map):
    booking = _serialize_booking_row(booking_row)
    if booking is None:
        return None

    payment_row = payment_map.get(int(booking["id"]))
    payment_status = str(payment_row.get("payment_status") or booking.get("payment_status") or "pending").strip() if payment_row else booking.get("payment_status") or "pending"
    amount = int(payment_row.get("amount")) if payment_row and payment_row.get("amount") is not None else int(booking.get("token_amount") or 0)

    active_statuses = {"pending", "confirmed", "assigned", "loading", "in_transit", "out_for_delivery"}

    booking["payment_status"] = payment_status
    booking["payment_amount"] = amount
    booking["is_active"] = str(booking.get("booking_status") or "").strip().lower() in active_statuses
    booking["payment_record"] = _serialize_payment_row(payment_row)
    booking["shipment_stage"] = "active" if booking["is_active"] else str(booking.get("booking_status") or "pending")
    return booking


def _get_revenue_series(days=30):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            f"""
            SELECT DATE(created_at) AS day, COALESCE(SUM(amount), 0) AS revenue
            FROM {PAYMENTS_TABLE}
            WHERE payment_status = 'paid' AND created_at >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
            GROUP BY DATE(created_at)
            ORDER BY day ASC
            """,
            (int(days),),
        )
        rows = cursor.fetchall()
        return [
            {
                "day": str(row.get("day")),
                "revenue": int(row.get("revenue") or 0),
            }
            for row in rows
        ]
    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def _get_booking_status_series(bookings):
    counts = {}
    for booking in bookings:
        status = str(booking.get("booking_status") or "pending").strip().lower() or "pending"
        counts[status] = counts.get(status, 0) + 1

    return [{"status": status, "count": count} for status, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def _get_payment_status_series(payments):
    counts = {}
    for payment in payments:
        status = str(payment.get("payment_status") or "created").strip().lower() or "created"
        counts[status] = counts.get(status, 0) + 1

    return [{"status": status, "count": count} for status, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def _monitoring_status_class(status_code):
    try:
        return f"{int(status_code) // 100}xx"
    except Exception:
        return "5xx"


def _format_monitoring_uptime(seconds):
    total_seconds = max(int(seconds or 0), 0)
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds_left = divmod(remainder, 60)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or parts:
        parts.append(f"{hours}h")
    if minutes or parts:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds_left}s")
    return " ".join(parts)


def _percentile(values, percentile):
    if not values:
        return 0.0

    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]

    position = (len(ordered) - 1) * (float(percentile) / 100.0)
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return ordered[int(position)]

    lower_value = ordered[lower_index]
    upper_value = ordered[upper_index]
    return lower_value + ((upper_value - lower_value) * (position - lower_index))


def _read_recent_error_logs(limit=10):
    logs_path = os.path.join(os.path.dirname(__file__), "logs", "error.log")
    if not os.path.exists(logs_path):
        return []

    try:
        with open(logs_path, "r", encoding="utf-8", errors="replace") as log_file:
            lines = [line.rstrip() for line in log_file.readlines() if "| ERROR |" in line or "| CRITICAL |" in line]
        return lines[-int(limit):]
    except Exception:
        return []


def _snapshot_monitoring_requests(days=30):
    cutoff = time.time() - (max(int(days), 1) * 86400)

    with MONITORING_LOCK:
        samples = [dict(sample) for sample in MONITORING_REQUEST_SAMPLES if sample["timestamp"] >= cutoff]
        websocket_connections = MONITORING_WEBSOCKET_CONNECTIONS
        websocket_connects_total = MONITORING_WEBSOCKET_CONNECTS_TOTAL
        websocket_disconnects_total = MONITORING_WEBSOCKET_DISCONNECTS_TOTAL

    for sample in samples:
        sample["status_class"] = _monitoring_status_class(sample.get("status_code", 500))

    durations = [float(sample.get("duration_ms") or 0.0) for sample in samples]
    errors = [sample for sample in samples if int(sample.get("status_code") or 0) >= 500]
    requests_last_minute = [sample for sample in samples if sample["timestamp"] >= (time.time() - 60)]

    method_counts = defaultdict(int)
    status_class_counts = defaultdict(int)
    route_counts = defaultdict(int)
    route_latency_totals = defaultdict(float)
    route_error_counts = defaultdict(int)
    minute_buckets = defaultdict(lambda: {"count": 0, "latency_total": 0.0, "error_count": 0})

    for sample in samples:
        method_counts[str(sample.get("method") or "GET").upper()] += 1
        status_class = sample.get("status_class") or _monitoring_status_class(sample.get("status_code"))
        status_class_counts[status_class] += 1

        route = str(sample.get("route") or sample.get("path") or "unknown")
        route_counts[route] += 1
        route_latency_totals[route] += float(sample.get("duration_ms") or 0.0)
        if int(sample.get("status_code") or 0) >= 500:
            route_error_counts[route] += 1

        bucket_key = time.strftime("%Y-%m-%d %H:%M", time.localtime(sample["timestamp"]))
        bucket = minute_buckets[bucket_key]
        bucket["count"] += 1
        bucket["latency_total"] += float(sample.get("duration_ms") or 0.0)
        if int(sample.get("status_code") or 0) >= 500:
            bucket["error_count"] += 1

    route_series = []
    for route, count in sorted(route_counts.items(), key=lambda item: (-item[1], item[0]))[:8]:
        route_series.append({
            "route": route,
            "count": count,
            "avg_latency_ms": round(route_latency_totals[route] / count, 2) if count else 0.0,
            "error_count": route_error_counts[route],
        })

    timeline_series = []
    for bucket_key, bucket in sorted(minute_buckets.items(), key=lambda item: item[0])[-30:]:
        timeline_series.append({
            "bucket": bucket_key,
            "request_count": bucket["count"],
            "avg_latency_ms": round(bucket["latency_total"] / bucket["count"], 2) if bucket["count"] else 0.0,
            "error_count": bucket["error_count"],
        })

    recent_request_rows = [
        {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(sample["timestamp"])),
            "method": sample.get("method"),
            "route": sample.get("route"),
            "status_code": sample.get("status_code"),
            "duration_ms": round(float(sample.get("duration_ms") or 0.0), 2),
            "status_class": sample.get("status_class"),
        }
        for sample in sorted(samples, key=lambda item: item["timestamp"])[-20:]
    ]

    return {
        "summary": {
            "uptime_seconds": round(time.time() - MONITORING_STARTED_AT, 2),
            "uptime_human": _format_monitoring_uptime(time.time() - MONITORING_STARTED_AT),
            "total_requests": len(samples),
            "requests_last_minute": len(requests_last_minute),
            "avg_latency_ms": round(sum(durations) / len(durations), 2) if durations else 0.0,
            "p95_latency_ms": round(_percentile(durations, 95), 2) if durations else 0.0,
            "error_count": len(errors),
            "error_rate_percent": round((len(errors) / len(samples)) * 100, 2) if samples else 0.0,
            "websocket_connections": websocket_connections,
            "websocket_connects_total": websocket_connects_total,
            "websocket_disconnects_total": websocket_disconnects_total,
        },
        "method_series": [{"method": method, "count": count} for method, count in sorted(method_counts.items(), key=lambda item: (-item[1], item[0]))],
        "status_class_series": [{"status_class": status_class, "count": count} for status_class, count in sorted(status_class_counts.items(), key=lambda item: (-item[1], item[0]))],
        "route_series": route_series,
        "latency_series": timeline_series,
        "recent_requests": recent_request_rows,
        "recent_errors": [
            {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(sample["timestamp"])),
                "method": sample.get("method"),
                "route": sample.get("route"),
                "status_code": sample.get("status_code"),
                "duration_ms": round(float(sample.get("duration_ms") or 0.0), 2),
                "message": sample.get("message") or "Server error",
            }
            for sample in sorted(errors, key=lambda item: item["timestamp"])[-10:]
        ],
        "log_errors": _read_recent_error_logs(limit=8),
    }


def _render_prometheus_metrics(snapshot, db_status="unknown"):
    summary = snapshot.get("summary", {})
    route_series = snapshot.get("route_series", [])
    method_series = snapshot.get("method_series", [])
    status_class_series = snapshot.get("status_class_series", [])
    latency_series = snapshot.get("latency_series", [])
    recent_requests = snapshot.get("recent_requests", [])

    lines = [
        "# HELP transport_app_uptime_seconds Seconds since the application monitoring state started.",
        "# TYPE transport_app_uptime_seconds gauge",
        f"transport_app_uptime_seconds {summary.get('uptime_seconds', 0)}",
        "# HELP transport_app_requests_total Total HTTP requests captured by the monitoring layer.",
        "# TYPE transport_app_requests_total counter",
    ]

    for row in method_series:
        method = str(row.get("method") or "GET")
        lines.append(f'transport_app_requests_total{{method="{method}"}} {int(row.get("count") or 0)}')

    lines.extend([
        "# HELP transport_app_request_duration_seconds Request latency histogram in seconds.",
        "# TYPE transport_app_request_duration_seconds histogram",
    ])

    all_durations = [float(row.get("duration_ms") or 0.0) / 1000.0 for row in recent_requests]
    for bucket in MONITORING_BUCKETS_SECONDS:
        count = sum(1 for duration in all_durations if duration <= bucket)
        lines.append(f'transport_app_request_duration_seconds_bucket{{le="{bucket}"}} {count}')
    lines.append(f'transport_app_request_duration_seconds_bucket{{le="+Inf"}} {len(all_durations)}')
    lines.append(f'transport_app_request_duration_seconds_sum {sum(all_durations)}')
    lines.append(f'transport_app_request_duration_seconds_count {len(all_durations)}')

    lines.extend([
        "# HELP transport_app_http_status_total Total requests grouped by status class.",
        "# TYPE transport_app_http_status_total counter",
    ])
    for row in status_class_series:
        status_class = str(row.get("status_class") or "5xx")
        lines.append(f'transport_app_http_status_total{{status_class="{status_class}"}} {int(row.get("count") or 0)}')

    lines.extend([
        "# HELP transport_app_route_requests_total Total requests per observed route.",
        "# TYPE transport_app_route_requests_total counter",
    ])
    for row in route_series:
        route = str(row.get("route") or "/")
        route_label = route.replace('"', "\\\"")
        lines.append(f'transport_app_route_requests_total{{route="{route_label}"}} {int(row.get("count") or 0)}')

    lines.extend([
        "# HELP transport_app_websocket_connections Current active websocket connections.",
        "# TYPE transport_app_websocket_connections gauge",
        f"transport_app_websocket_connections {summary.get('websocket_connections', 0)}",
        "# HELP transport_app_error_events_total Total 5xx request events captured in the monitoring window.",
        "# TYPE transport_app_error_events_total counter",
        f"transport_app_error_events_total {summary.get('error_count', 0)}",
        "# HELP transport_app_db_connection_status Database connection health gauge.",
        "# TYPE transport_app_db_connection_status gauge",
        f'transport_app_db_connection_status{{state="{db_status}"}} 1',
    ])

    if latency_series:
        lines.append("# HELP transport_app_request_latency_latest_ms Latest request latency per captured minute.")
        lines.append("# TYPE transport_app_request_latency_latest_ms gauge")
        for row in latency_series:
            bucket = str(row.get("bucket") or "unknown")
            lines.append(f'transport_app_request_latency_latest_ms{{bucket="{bucket}"}} {float(row.get("avg_latency_ms") or 0.0)}')

    return "\n".join(lines) + "\n"


def _build_monitoring_dashboard_payload(days=30):
    snapshot = _snapshot_monitoring_requests(days=days)

    try:
        db_status = "ok" if test_db_connection() else "unreachable"
    except Exception:
        db_status = "unreachable"

    readiness = "ready" if db_status == "ok" else "degraded"
    snapshot["health"] = {
        "service": "skdls-transport-ai",
        "environment": os.getenv("FLASK_ENV", "production"),
        "database": db_status,
        "readiness": readiness,
        "log_errors": len(snapshot.get("log_errors") or []),
    }
    snapshot["prometheus_metrics"] = _render_prometheus_metrics(snapshot, db_status=db_status)
    return snapshot


def _build_admin_dashboard_payload(status_filter=None, payment_filter=None, days=30):
    booking_rows = _fetch_booking_records()
    payment_rows = _fetch_payment_records()
    driver_rows = _fetch_driver_records()
    truck_rows = _fetch_truck_inventory()

    payment_map = {}
    paid_revenue = 0
    pending_payments = 0

    for payment_row in payment_rows:
        booking_id = payment_row.get("booking_id")
        if booking_id is None:
            continue
        booking_key = int(booking_id)
        if booking_key not in payment_map:
            payment_map[booking_key] = payment_row
        if str(payment_row.get("payment_status") or "").strip().lower() == "paid":
            paid_revenue += int(payment_row.get("amount") or 0)

    bookings = []
    for row in booking_rows:
        admin_booking = _build_booking_admin_record(row, payment_map)
        if admin_booking is None:
            continue

        status_value = str(admin_booking.get("booking_status") or "pending").strip().lower()
        payment_value = str(admin_booking.get("payment_status") or "pending").strip().lower()

        if status_filter and status_filter != "all" and status_value != status_filter:
            continue

        if payment_filter and payment_filter != "all" and payment_value != payment_filter:
            continue

        bookings.append(admin_booking)

    active_shipments = [booking for booking in bookings if booking.get("is_active")]
    shipment_statuses = {"pending", "confirmed", "assigned", "loading", "in_transit", "out_for_delivery", "delayed"}
    delayed_shipments = len([booking for booking in booking_rows if str(booking.get("booking_status") or "").strip().lower() == "delayed"])
    failed_shipments = len([booking for booking in booking_rows if str(booking.get("booking_status") or "").strip().lower() == "failed"])

    dashboard_summary = {
        "total_bookings": len(booking_rows),
        "filtered_bookings": len(bookings),
        "active_shipments": len([booking for booking in booking_rows if str(booking.get("booking_status") or "").strip().lower() in shipment_statuses]),
        "completed_shipments": len([booking for booking in booking_rows if str(booking.get("booking_status") or "").strip().lower() == "completed"]),
        "delayed_shipments": delayed_shipments,
        "failed_shipments": failed_shipments,
        "revenue_collected": paid_revenue,
        "pending_payments": len([booking for booking in booking_rows if str(booking.get("payment_status") or "").strip().lower() != "paid"]),
        "live_trucks": len([truck for truck in truck_rows if truck.get("is_live")]),
        "available_trucks": len([truck for truck in truck_rows if str(truck.get("availability_status") or "").strip().lower() in {"available", "yes", "true", "1"}]),
        "drivers_on_duty": len([driver for driver in driver_rows if str(driver.get("status") or "").strip().lower() in {"available", "on_trip"}]),
    }

    recent_shipments = active_shipments[:12]
    recent_bookings = bookings[:20]
    revenue_series = _get_revenue_series(days=days)

    return {
        "summary": dashboard_summary,
        "filters": {
            "booking_status": status_filter or "all",
            "payment_status": payment_filter or "all",
            "days": int(days),
        },
        "bookings": recent_bookings,
        "active_shipments": recent_shipments,
        "drivers": [_serialize_driver_row(row) for row in driver_rows],
        "trucks": truck_rows,
        "analytics": {
            "revenue_series": revenue_series,
            "booking_status_series": _get_booking_status_series(booking_rows),
            "payment_status_series": _get_payment_status_series(payment_rows),
        },
        "payment_records": [_serialize_payment_row(row) for row in payment_rows[:25]],
    }


def _update_driver_record(driver_id, updates):
    connection = None
    cursor = None

    allowed_fields = {
        "driver_name": "driver_name",
        "status": "status",
        "assigned_truck": "assigned_truck",
        "assigned_truck_type": "assigned_truck_type",
        "phone": "phone",
        "license_number": "license_number",
        "rating": "rating",
        "experience_years": "experience_years",
        "next_available_at": "next_available_at",
    }

    columns = []
    params = []
    for key, column in allowed_fields.items():
        if key not in updates:
            continue
        value = updates[key]
        columns.append(f"{column} = %s")
        params.append(value)

    if not columns:
        raise ValueError("No valid driver fields provided")

    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        params.append(int(driver_id))
        cursor.execute(
            f"UPDATE {DRIVERS_TABLE} SET {', '.join(columns)} WHERE id = %s",
            params,
        )
        connection.commit()
        return cursor.rowcount
    except Error as error:
        if connection is not None:
            connection.rollback()
        raise RuntimeError(f"Database error while updating driver {driver_id}: {error}") from error
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def _update_truck_availability(truck_type_code, vehicle_number, availability_status):
    table_name = TABLE_MAP.get(str(truck_type_code).strip())
    if table_name is None:
        raise ValueError("Unsupported truck type")

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            f"UPDATE {table_name} SET availability_status = %s WHERE vehicle_number = %s",
            (str(availability_status or "available").strip(), str(vehicle_number).strip()),
        )
        connection.commit()
        return cursor.rowcount
    except Error as error:
        if connection is not None:
            connection.rollback()
        raise RuntimeError(f"Database error while updating truck {vehicle_number}: {error}") from error
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def _find_nearest_available_driver(pickup_latitude, pickup_longitude, truck_type):
    """Best-effort driver assignment without a separate GPS driver location table.

    The current schema does not track driver coordinates, so we rank by status and
    truck type compatibility and return the first eligible driver.
    """
    try:
        target_type = _normalize_truck_type(truck_type)
    except Exception:
        target_type = str(truck_type or "").strip()

    drivers = _fetch_driver_records()
    compatible = []
    for driver in drivers:
        status = str(driver.get("status") or "").strip().lower()
        assigned_type = str(driver.get("assigned_truck_type") or "").strip().lower()
        if status not in {"available", "on_trip"}:
            continue
        if target_type and assigned_type and target_type not in assigned_type:
            continue
        compatible.append(driver)

    if compatible:
        return compatible[0]

    return next((driver for driver in drivers if str(driver.get("status") or "").strip().lower() == "available"), None)


def _prepare_booking_insert_data(payload, authenticated_user_id=None):
    customer_name = _normalize_booking_field(payload.get("customer_name") or payload.get("customerName"), "Customer name")
    phone = _normalize_phone_number(payload.get("phone") or payload.get("phoneNumber"))
    pickup_location = _normalize_booking_field(payload.get("pickup_location") or payload.get("pickupLocation"), "Pickup location")
    drop_location = _normalize_booking_field(payload.get("drop_location") or payload.get("dropLocation"), "Drop location")
    truck_type = _normalize_form_truck_type(payload.get("truck_type") or payload.get("truckType"))

    if not truck_type:
        raise ValueError("Select a valid truck type")

    if pickup_location.lower() == drop_location.lower():
        raise ValueError("Pickup and drop locations must be different")

    try:
        load_weight = float(payload.get("load_weight") or payload.get("loadWeight"))
    except (TypeError, ValueError):
        raise ValueError("Load weight must be a number")

    if load_weight <= 0:
        raise ValueError("Load weight must be greater than zero")

    truck_limits = BOOKING_FORM_TRUCK_TYPES[truck_type]
    if load_weight > truck_limits["max_load_weight"]:
        raise ValueError(f"{truck_limits['label']} supports up to {truck_limits['max_load_weight']} tons")

    estimated_price = _calculate_form_booking_price(truck_type, load_weight)

    notification_options = payload.get("notification_settings") or {}

    user_identifier = str(authenticated_user_id or payload.get("user_id") or phone or "web_booking").strip() or "web_booking"

    return {
        "customer_name": customer_name,
        "phone": phone,
        "pickup_location": pickup_location,
        "drop_location": drop_location,
        "truck_type": truck_type,
        "load_weight": round(load_weight, 2),
        "price": estimated_price,
        "booking_status": "pending",
        "user_id": user_identifier,
        "source_location": pickup_location,
        "destination_location": drop_location,
        "exact_pickup_location": pickup_location,
        "exact_delivery_location": drop_location,
        "tons": int(round(load_weight)),
        "tyre_type": truck_type,
        "distance": 0,
        "token_amount": 0,
        "payment_status": "pending",
        "lorry_number": "",
        "whatsapp_notifications_enabled": int(_parse_bool(payload.get("whatsapp_notifications_enabled"), True)),
        "whatsapp_booking_confirmation": int(_parse_bool(notification_options.get("booking_confirmation") if isinstance(notification_options, dict) else payload.get("whatsapp_booking_confirmation"), True)),
        "whatsapp_payment_confirmation": int(_parse_bool(notification_options.get("payment_confirmation") if isinstance(notification_options, dict) else payload.get("whatsapp_payment_confirmation"), True)),
        "whatsapp_live_location": int(_parse_bool(notification_options.get("live_location") if isinstance(notification_options, dict) else payload.get("whatsapp_live_location"), True)),
        "whatsapp_delivery_updates": int(_parse_bool(notification_options.get("delivery_updates") if isinstance(notification_options, dict) else payload.get("whatsapp_delivery_updates"), True)),
    }


def _insert_booking_form_record(booking_data):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        table_columns = _get_table_columns(BOOKINGS_TABLE)

        insert_columns = []
        insert_values = []

        for column_name, column_value in booking_data.items():
            if column_name in table_columns:
                insert_columns.append(column_name)
                insert_values.append(column_value)

        if not insert_columns:
            raise RuntimeError(f"No compatible columns available in {BOOKINGS_TABLE}")

        columns_sql = ", ".join(insert_columns)
        placeholders_sql = ", ".join(["%s"] * len(insert_columns))

        cursor.execute(
            f"""
            INSERT INTO {BOOKINGS_TABLE}
                ({columns_sql})
            VALUES ({placeholders_sql})
            """,
            insert_values,
        )
        connection.commit()
        return cursor.lastrowid
    except Error as error:
        if connection is not None:
            connection.rollback()
        raise RuntimeError(f"Database error while creating booking: {error}") from error
    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def _fetch_booking_record_by_id(booking_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            f"SELECT * FROM {BOOKINGS_TABLE} WHERE id = %s LIMIT 1",
            (int(booking_id),),
        )
        return cursor.fetchone()
    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def _fetch_booking_records():
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(f"SELECT * FROM {BOOKINGS_TABLE} ORDER BY created_at DESC, id DESC")
        return cursor.fetchall()
    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def _fetch_booking_records_for_user(user_identifier=None, phone=None, limit=30):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        limit_value = max(1, min(int(limit or 30), 100))

        if user_identifier:
            cursor.execute(
                f"SELECT * FROM {BOOKINGS_TABLE} WHERE user_id = %s ORDER BY created_at DESC, id DESC LIMIT %s",
                (str(user_identifier).strip(), limit_value),
            )
        elif phone:
            cursor.execute(
                f"SELECT * FROM {BOOKINGS_TABLE} WHERE phone = %s ORDER BY created_at DESC, id DESC LIMIT %s",
                (str(phone).strip(), limit_value),
            )
        else:
            cursor.execute(
                f"SELECT * FROM {BOOKINGS_TABLE} ORDER BY created_at DESC, id DESC LIMIT %s",
                (limit_value,),
            )

        return cursor.fetchall()
    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def _fetch_payment_record_by_order_id(razorpay_order_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            f"SELECT * FROM {PAYMENTS_TABLE} WHERE razorpay_order_id = %s LIMIT 1",
            (str(razorpay_order_id),),
        )
        return cursor.fetchone()
    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def _insert_payment_record(
    booking_id,
    razorpay_order_id,
    amount,
    payment_status="created",
    razorpay_payment_id=None,
    payment_type="advance",
    shipment_id=None,
    session_id=None,
):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        insert_columns = []
        insert_values = []

        if booking_id is not None:
            insert_columns.append("booking_id")
            insert_values.append(int(booking_id))

        if shipment_id is not None:
            insert_columns.append("shipment_id")
            insert_values.append(int(shipment_id))

        if session_id is not None:
            insert_columns.append("session_id")
            insert_values.append(str(session_id))

        if not insert_columns:
            raise ValueError("booking_id or shipment_id is required")

        insert_columns.extend(["razorpay_order_id", "razorpay_payment_id", "amount", "payment_status", "status", "payment_type"])
        insert_values.extend(
            [
                str(razorpay_order_id),
                razorpay_payment_id,
                int(round(float(amount))),
                str(payment_status or "created"),
                str(payment_status or "created"),
                str(payment_type or "advance"),
            ]
        )

        cursor.execute(
            f"""
            INSERT INTO {PAYMENTS_TABLE}
                ({', '.join(insert_columns)})
            VALUES ({', '.join(['%s'] * len(insert_columns))})
            """,
            insert_values,
        )
        connection.commit()
        payment_id = cursor.lastrowid
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(f"SELECT * FROM {PAYMENTS_TABLE} WHERE id = %s LIMIT 1", (int(payment_id),))
                payment_row = cursor.fetchone()
            finally:
                cursor.close()
                conn.close()
            if payment_row:
                shipment_reference = payment_row.get("shipment_id") or payment_row.get("booking_id")
                if shipment_reference is not None:
                    try:
                        _insert_shipment_event_log(
                            int(shipment_reference),
                            "payment_created",
                            title="Payment created",
                            message=f"Payment order {payment_row.get('razorpay_order_id')} created.",
                            severity="info",
                            source="payment.create",
                            metadata={"payment": _serialize_payment_row(payment_row)},
                        )
                    except Exception:
                        logger.warning("[event_log][payment_created][warning] Failed to store shipment event log")
                _broadcast_realtime_activity("payment", str(payment_status or "created"), {"payment": _serialize_payment_row(payment_row)}, event_name="payment:update")
                try:
                    operational = None
                    if payment_row.get("shipment_id") is not None:
                        shipment_row = _fetch_shipment_record_by_id(int(payment_row.get("shipment_id")))
                        if shipment_row:
                            operational = _build_shipment_operational_context(shipment_row, role="customer", payment_rows=[payment_row], include_invoice=True)
                    elif payment_row.get("booking_id") is not None:
                        booking_row = _fetch_booking_record_by_id(int(payment_row.get("booking_id")))
                        if booking_row:
                            operational = _build_booking_operational_context(booking_row, role="customer", include_invoice=True)

                    if operational:
                        _broadcast_chat_update(
                            {
                                "status": "success",
                                "message": operational.get("reply") or f"Payment order {payment_row.get('razorpay_order_id')} created.",
                                "reply": operational.get("reply") or f"Payment order {payment_row.get('razorpay_order_id')} created.",
                                "action": "MAKE_PAYMENT",
                                "card_type": "payment_card",
                                "workflow": "payments",
                                "route": "payments.order_created",
                                "intent": "MAKE_PAYMENT",
                                "data": operational.get("data") or {},
                                "suggestions": ["Pay invoice", "Download invoice", "Track shipment"],
                                "source": "payment.create",
                            }
                        )
                except Exception as error:
                    logger.warning(f"[socketio][chat:update][warning] {error}")
        except Exception as error:
            logger.warning(f"[socketio][payment:create][warning] {error}")
        return payment_id
    except Error as error:
        if connection is not None:
            connection.rollback()
        raise RuntimeError(f"Database error while storing payment record: {error}") from error
    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def _update_payment_record(razorpay_order_id, razorpay_payment_id=None, payment_status=None, payment_type=None):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        update_parts = []
        params = []

        if razorpay_payment_id is not None:
            update_parts.append("razorpay_payment_id = %s")
            params.append(str(razorpay_payment_id))

        if payment_status is not None:
            update_parts.append("payment_status = %s")
            params.append(str(payment_status))

            update_parts.append("status = %s")
            params.append(str(payment_status))

        if payment_type is not None:
            update_parts.append("payment_type = %s")
            params.append(str(payment_type))

        if not update_parts:
            return 0

        params.append(str(razorpay_order_id))
        cursor.execute(
            f"""
            UPDATE {PAYMENTS_TABLE}
            SET {', '.join(update_parts)}
            WHERE razorpay_order_id = %s
            """,
            params,
        )
        connection.commit()
        updated_rows = cursor.rowcount
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(f"SELECT * FROM {PAYMENTS_TABLE} WHERE razorpay_order_id = %s LIMIT 1", (str(razorpay_order_id),))
                payment_row = cursor.fetchone()
            finally:
                cursor.close()
                conn.close()
            if payment_row:
                _broadcast_realtime_activity("payment", str(payment_status or payment_row.get("payment_status") or "updated"), {"payment": _serialize_payment_row(payment_row)}, event_name="payment:update")
        except Exception as error:
            logger.warning(f"[socketio][payment:update][warning] {error}")
        return updated_rows
    except Error as error:
        if connection is not None:
            connection.rollback()
        raise RuntimeError(f"Database error while updating payment record: {error}") from error
    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def _update_booking_payment_state(booking_id, payment_status, booking_status=None):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        update_parts = ["payment_status = %s"]
        params = [str(payment_status)]

        if booking_status is not None:
            update_parts.append("booking_status = %s")
            params.append(str(booking_status))

        params.append(int(booking_id))

        cursor.execute(
            f"""
            UPDATE {BOOKINGS_TABLE}
            SET {', '.join(update_parts)}
            WHERE id = %s
            """,
            params,
        )
        connection.commit()
        return cursor.rowcount
    except Error as error:
        if connection is not None:
            connection.rollback()
        raise RuntimeError(f"Database error while updating booking payment state: {error}") from error
    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def _get_booking_advance_amount(booking_row):
    price_value = booking_row.get("price")
    if price_value is None:
        raise ValueError("Booking price is unavailable")

    return int(round(float(price_value) * RAZORPAY_ADVANCE_RATIO))


def _get_booking_paid_amount(booking_id):
    payments = _fetch_payment_records_for_booking(booking_id)
    total = 0

    for payment_row in payments or []:
        if str(payment_row.get("payment_status") or payment_row.get("status") or "").strip().lower() != "paid":
            continue

        total += int(round(float(payment_row.get("amount") or 0)))

    return total


def _resolve_payment_amount(booking_row, payment_type, booking_id):
    total_price = int(round(float(booking_row.get("price") or 0)))
    paid_amount = _get_booking_paid_amount(booking_id)
    payment_kind = str(payment_type or "advance").strip().lower()

    if payment_kind == "full":
        return max(total_price - paid_amount, 0)

    advance_amount = _get_booking_advance_amount(booking_row)
    return max(advance_amount - paid_amount, 0)


def _get_shipment_paid_amount(shipment_id):
    return _get_booking_paid_amount(shipment_id)


def _resolve_shipment_payment_amount(shipment_row, payment_type, shipment_id):
    total_price = int(round(float(shipment_row.get("estimated_price") or 0)))
    paid_amount = _get_shipment_paid_amount(shipment_id)
    payment_kind = str(payment_type or "advance").strip().lower()

    if payment_kind == "full":
        return max(total_price - paid_amount, 0)

    advance_amount = int(round(total_price * RAZORPAY_ADVANCE_RATIO))
    return max(advance_amount - paid_amount, 0)


def _build_invoice_payload(booking_row, payment_rows):
    total_amount = int(round(float(booking_row.get("price") or 0)))
    subtotal_amount = int(round(total_amount / (1 + GST_RATE))) if total_amount else 0
    gst_amount = max(total_amount - subtotal_amount, 0)
    paid_amount = sum(int(round(float(row.get("amount") or 0))) for row in payment_rows or [] if str(row.get("payment_status") or row.get("status") or "").strip().lower() == "paid")
    outstanding_amount = max(total_amount - paid_amount, 0)
    invoice_number = f"INV-{int(booking_row.get('id')):06d}"

    return {
        "invoice_number": invoice_number,
        "booking": _serialize_booking_row(booking_row),
        "payments": [_serialize_payment_row(row) for row in payment_rows or []],
        "summary": {
            "subtotal_amount": subtotal_amount,
            "gst_rate": int(round(GST_RATE * 100)),
            "gst_amount": gst_amount,
            "total_amount": total_amount,
            "paid_amount": paid_amount,
            "outstanding_amount": outstanding_amount,
        },
        "issued_at": _current_timestamp().isoformat(sep=" ", timespec="seconds"),
    }


def _render_invoice_html(invoice_payload):
        booking = invoice_payload.get("booking") or {}
        summary = invoice_payload.get("summary") or {}
        payments = invoice_payload.get("payments") or []

        payment_rows_html = "".join(
                (
                        "<tr>"
                        f"<td>{str(payment.get('payment_type') or 'advance').title()}</td>"
                        f"<td>{str(payment.get('razorpay_payment_id') or payment.get('razorpay_order_id') or '-')}</td>"
                        f"<td>₹{int(payment.get('amount') or 0):,}</td>"
                        f"<td>{str(payment.get('payment_status') or 'created').title()}</td>"
                        "</tr>"
                )
                for payment in payments
        ) or "<tr><td colspan='4'>No payments recorded yet.</td></tr>"

        return f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>GST Invoice {invoice_payload.get('invoice_number')}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 0; padding: 32px; background: #0f172a; color: #e2e8f0; }}
        .sheet {{ max-width: 920px; margin: 0 auto; background: #111827; border: 1px solid #334155; border-radius: 18px; padding: 28px; }}
        .top {{ display: flex; justify-content: space-between; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; }}
        h1, h2 {{ margin: 0 0 8px; }}
        .muted {{ color: #94a3b8; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 20px 0; }}
        .card {{ background: rgba(15, 23, 42, 0.8); border: 1px solid #334155; border-radius: 14px; padding: 14px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 18px; }}
        th, td {{ text-align: left; padding: 12px 10px; border-bottom: 1px solid #334155; }}
        th {{ color: #fdba74; }}
    </style>
</head>
<body>
    <div class="sheet">
        <div class="top">
            <div>
                <h1>SKDLS Transportations</h1>
                <div class="muted">GST Invoice {invoice_payload.get('invoice_number')}</div>
            </div>
            <div>
                <div><strong>Booking:</strong> {booking.get('booking_reference') or f"Booking #{booking.get('id')}"}</div>
                <div><strong>Issued:</strong> {invoice_payload.get('issued_at')}</div>
            </div>
        </div>

        <div class="grid">
            <div class="card"><strong>Customer</strong><div>{booking.get('customer_name') or '-'}</div></div>
            <div class="card"><strong>Route</strong><div>{booking.get('pickup_location') or '-'} to {booking.get('drop_location') or '-'}</div></div>
            <div class="card"><strong>Truck</strong><div>{booking.get('truck_type') or '-'}</div></div>
            <div class="card"><strong>Booking status</strong><div>{booking.get('booking_status') or '-'}</div></div>
        </div>

        <div class="grid">
            <div class="card"><strong>Subtotal</strong><div>₹{int(summary.get('subtotal_amount') or 0):,}</div></div>
            <div class="card"><strong>GST {int(summary.get('gst_rate') or 0)}%</strong><div>₹{int(summary.get('gst_amount') or 0):,}</div></div>
            <div class="card"><strong>Total</strong><div>₹{int(summary.get('total_amount') or 0):,}</div></div>
            <div class="card"><strong>Outstanding</strong><div>₹{int(summary.get('outstanding_amount') or 0):,}</div></div>
        </div>

        <h2>Payment history</h2>
        <table>
            <thead>
                <tr>
                    <th>Type</th>
                    <th>Reference</th>
                    <th>Amount</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
                {payment_rows_html}
            </tbody>
        </table>
    </div>
</body>
</html>"""


def _booking_whatsapp_opt_in(booking_row, field_name):
    if not booking_row:
        return False

    if not _parse_bool(booking_row.get("whatsapp_notifications_enabled"), True):
        return False

    return _parse_bool(booking_row.get(field_name), True)


def _send_booking_whatsapp_confirmation(booking_row):
    if not _booking_whatsapp_opt_in(booking_row, "whatsapp_booking_confirmation"):
        return False

    if not is_whatsapp_configured():
        logger.info("[whatsapp][booking_confirmation] Twilio not configured, skipping send")
        return False

    try:
        send_whatsapp_message(booking_row.get("phone"), build_booking_confirmation_message(booking_row))
        logger.info(f"[whatsapp][booking_confirmation] sent booking_id={booking_row.get('id')}")
        return True
    except Exception as error:
        logger.warning(f"[whatsapp][booking_confirmation][warning] booking_id={booking_row.get('id')} error={error}")
        return False


def _send_payment_whatsapp_confirmation(booking_row, payment_record):
    if not _booking_whatsapp_opt_in(booking_row, "whatsapp_payment_confirmation"):
        return False

    if not is_whatsapp_configured():
        logger.info("[whatsapp][payment_confirmation] Twilio not configured, skipping send")
        return False

    payload = dict(payment_record or {})
    payload.setdefault("amount", booking_row.get("token_amount") or booking_row.get("price") or 0)

    try:
        send_whatsapp_message(booking_row.get("phone"), build_payment_confirmation_message(booking_row, payload))
        logger.info(f"[whatsapp][payment_confirmation] sent booking_id={booking_row.get('id')}")
        return True
    except Exception as error:
        logger.warning(f"[whatsapp][payment_confirmation][warning] booking_id={booking_row.get('id')} error={error}")
        return False


def _send_live_location_whatsapp(booking_row, tracking_data):
    if not _booking_whatsapp_opt_in(booking_row, "whatsapp_live_location"):
        return False

    if not is_whatsapp_configured():
        logger.info("[whatsapp][live_location] Twilio not configured, skipping send")
        return False

    try:
        send_whatsapp_message(booking_row.get("phone"), build_live_location_message(booking_row, tracking_data))
        logger.info(f"[whatsapp][live_location] sent booking_id={booking_row.get('id')}")
        return True
    except Exception as error:
        logger.warning(f"[whatsapp][live_location][warning] booking_id={booking_row.get('id')} error={error}")
        return False


def _send_delivery_whatsapp_update(booking_row, status_text, details=None):
    if not _booking_whatsapp_opt_in(booking_row, "whatsapp_delivery_updates"):
        return False

    if not is_whatsapp_configured():
        logger.info("[whatsapp][delivery_update] Twilio not configured, skipping send")
        return False

    try:
        send_whatsapp_message(booking_row.get("phone"), build_delivery_update_message(booking_row, status_text, details))
        logger.info(f"[whatsapp][delivery_update] sent booking_id={booking_row.get('id')}")
        return True
    except Exception as error:
        logger.warning(f"[whatsapp][delivery_update][warning] booking_id={booking_row.get('id')} error={error}")
        return False


def _create_razorpay_order(amount_rupees, booking_id):
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        raise RuntimeError("Razorpay credentials are not configured")

    amount_paise = int(round(float(amount_rupees) * 100))
    payload = {
        "amount": amount_paise,
        "currency": RAZORPAY_CURRENCY,
        "receipt": f"booking_{booking_id}",
        "payment_capture": 1,
        "notes": {
            "booking_id": str(booking_id),
            "source": "SKDLS Transportations",
        },
    }

    response = requests.post(
        f"{RAZORPAY_API_BASE_URL}/orders",
        auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
        json=payload,
        timeout=20,
    )

    if not response.ok:
        raise RuntimeError(f"Razorpay order creation failed: {response.text[:300]}")

    data = response.json()
    order_id = data.get("id")
    if not order_id:
        raise RuntimeError("Razorpay order response did not include an order id")

    return data


def _verify_razorpay_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature):
    if not RAZORPAY_KEY_SECRET:
        raise RuntimeError("Razorpay secret is not configured")

    message = f"{razorpay_order_id}|{razorpay_payment_id}".encode("utf-8")
    expected_signature = hmac.new(
        RAZORPAY_KEY_SECRET.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected_signature, str(razorpay_signature or ""))


def _verify_razorpay_webhook_signature(payload_bytes, razorpay_signature):
    """Validate Razorpay webhook payload using the webhook secret and HMAC SHA256.

    payload_bytes should be the raw request body (bytes).
    """
    if not RAZORPAY_WEBHOOK_SECRET:
        raise RuntimeError("Razorpay webhook secret is not configured")

    if isinstance(payload_bytes, str):
        payload_bytes = payload_bytes.encode("utf-8")

    expected = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, str(razorpay_signature or ""))


def _get_pricing_profile(truck_type):
    code = str(truck_type or "12").strip()
    return PRICING_CONFIG.get(code, PRICING_CONFIG["12"])


def calculate_price(distance_km, truck_type):
    """Return total freight cost (rounded int) for given distance (km) and truck type."""
    try:
        d = float(distance_km)
    except (TypeError, ValueError):
        raise ValueError("Invalid distance")

    pricing_profile = _get_pricing_profile(truck_type)

    for slab in pricing_profile["slabs"]:
        max_km = slab["max_km"]
        if d >= slab["min_km"] and (max_km is None or d < max_km):
            price = slab["base"] + (d * slab["rate"])
            return int(round(price))

    raise ValueError("No pricing slab configured for this truck type")


def _estimate_eta_hours(distance_km, truck_type):
    try:
        distance_value = float(distance_km)
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid distance for ETA calculation") from error

    truck_code = str(truck_type or "12").strip()
    average_speed = TRUCK_SPEEDS_KMPH.get(truck_code, TRUCK_SPEEDS_KMPH["12"])
    driving_hours = distance_value / float(average_speed)
    loading_delay_hours = 0.75
    eta_hours = max(1.0, driving_hours + loading_delay_hours)
    return round(eta_hours, 1)


def _build_fare_estimation_reply(source, destination, load_weight, truck_type_label, distance_km, fare, eta_hours):
    source_text = str(source or "").strip()
    destination_text = str(destination or "").strip()
    weight_text = int(round(float(load_weight))) if load_weight is not None else ""
    fare_text = f"₹{int(round(float(fare))):,}"
    eta_text = f"{eta_hours:g} hours"

    return (
        f"The estimated fare from {source_text} to {destination_text} for {weight_text} tons is {fare_text} "
        f"and ETA is {eta_text}."
    )


def is_positive_confirmation(message):
    """Detect positive confirmation intent from user message.
    
    Recognizes: yes, yep, yeah, yup, ok, okay, sure, continue, proceed, book, confirm
    """
    text = str(message or "").strip().lower()
    positive_keywords = {
        "yes", "yep", "yeah", "yup", "y",
        "ok", "okay",
        "sure", "definitely", "absolutely",
        "continue", "proceed", "go", "let's go",
        "book", "confirm", "confirmed",
        "please", "do it", "yes please"
    }
    
    # Check for exact matches or keywords in the text
    return any(keyword in text for keyword in positive_keywords)


def is_negative_confirmation(message):
    """Detect negative confirmation intent from user message.
    
    Recognizes: no, nope, cancel, stop, later
    """
    text = str(message or "").strip().lower()
    negative_keywords = {
        "no", "nope", "n",
        "cancel", "cancelled",
        "stop", "stopped",
        "later", "not now", "not right now",
        "skip", "pass", "decline"
    }
    
    # Check for exact matches or keywords in the text
    return any(keyword in text for keyword in negative_keywords)


def _prepare_location_for_geocoding(location_text):
    cleaned = re.sub(r"\s+", " ", str(location_text or "").strip())
    return cleaned


_GEO_ALIAS_MAP = {
    "vij": "Vijayawada",
    "hyd": "Hyderabad",
    "blr": "Bangalore",
    "vizag": "Visakhapatnam",
}

_GEO_STRIP_PATTERNS = (
    r"\bnear\b",
    r"\bopposite\b",
    r"\bbeside\b",
    r"\btransport hub\b",
    r"\bwarehouse zone\b",
    r"\bindustrial area\b",
)


def _normalize_whitespace(text):
    return re.sub(r"\s+", " ", str(text or "").strip())


def _apply_address_aliases(text):
    normalized = _normalize_whitespace(text)
    for alias, replacement in _GEO_ALIAS_MAP.items():
        normalized = re.sub(rf"\b{re.escape(alias)}\b", replacement, normalized, flags=re.IGNORECASE)
    return _normalize_whitespace(normalized)


def _strip_unnecessary_words(text):
    cleaned = _normalize_whitespace(text)
    for pattern in _GEO_STRIP_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[,/;:()\[\]{}]+", ",", cleaned)
    cleaned = re.sub(r"\s*,\s*", ", ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,-")
    return cleaned


def _extract_city_from_address(text):
    cleaned = _normalize_whitespace(text)
    if not cleaned:
        return ""

    parts = [part.strip(" ,-") for part in re.split(r"[,|-]", cleaned) if part.strip(" ,-")]
    if not parts:
        return cleaned

    last_part = parts[-1]
    last_part = re.sub(r"\b(?:district|dist\.?|state|india)\b", "", last_part, flags=re.IGNORECASE)
    last_part = _normalize_whitespace(last_part)


def _build_geocode_candidates(cleaned_address):
    cleaned_address = _normalize_whitespace(cleaned_address)
    if not cleaned_address:
        return []

    parts = [part.strip(" ,-") for part in cleaned_address.split(",") if part.strip(" ,-")]
    city = _extract_city_from_address(cleaned_address)
    landmark = parts[0] if parts else cleaned_address

    candidates = []
    for candidate in (
        city,
        f"{landmark}, {city}" if landmark and city and landmark.lower() != city.lower() else "",
        cleaned_address,
    ):
        candidate = _normalize_whitespace(candidate)
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def normalize_location_with_gemini(location_text):
    """Use Gemini to normalize informal pickup/delivery text for geocoding.

    Keeps industrial area, estate, phase, sector, road and landmark details
    while converting informal phrasing into a cleaner geocoding-friendly form.
    """
    original_text = _prepare_location_for_geocoding(location_text)

    if not original_text:
        return ""

    if not GEMINI_API_KEY:
        print(f"[geocoding][debug] Gemini not configured, using original: {original_text}")
        return original_text

    try:
        model = _get_gemini_model()
        if not model:
            return original_text
        prompt = f"""Normalize this pickup or delivery location into a concise geocoding-friendly address.

Return ONLY the normalized location as plain text.
Rules:
- Keep only landmark + city + state when possible.
- Support industrial area style addresses such as factories, estates, SIPCOT areas, industrial parks, layouts, and warehouse locations.
- Expand short aliases when possible.
- Remove filler words and make the address easier to geocode.
- If you cannot improve it, return the original text.

Original: {original_text}"""

        response = model.generate_content(
            prompt,
            request_options={"timeout": GEMINI_TIMEOUT_SECONDS},
        )
        normalized = _prepare_location_for_geocoding(getattr(response, "text", ""))
        normalized = _apply_address_aliases(normalized)
        normalized = _strip_unnecessary_words(normalized)

        if normalized:
            print(f"[geocoding][debug] Gemini normalized '{original_text}' -> '{normalized}'")
            return normalized

        print(f"[geocoding][debug] Gemini returned empty, using original: {original_text}")
        return original_text
    except TimeoutError as error:
        print(f"[gemini][timeout] geocoding error={error} original={original_text!r}")
        return original_text
    except Exception as error:
        print(f"[geocoding][debug] Gemini normalization failed for '{original_text}': {error}")
        return original_text


def _extract_nominatim_coordinates(data, query_label):
    if not isinstance(data, list) or not data:
        print(f"[geocoding][debug] Nominatim returned no results for query='{query_label}'")
        return None

    first_result = data[0]
    if not isinstance(first_result, dict):
        print(f"[geocoding][debug] Nominatim returned malformed result for query='{query_label}': {first_result!r}")
        return None

    latitude_value = first_result.get("lat")
    longitude_value = first_result.get("lon")
    display_name = str(first_result.get("display_name") or "")

    if latitude_value is None or longitude_value is None:
        print(f"[geocoding][debug] Nominatim result missing lat/lon for query='{query_label}' payload={first_result!r}")
        return None

    try:
        latitude = float(latitude_value)
        longitude = float(longitude_value)
    except (TypeError, ValueError):
        print(f"[geocoding][debug] Nominatim lat/lon conversion failed for query='{query_label}' payload={first_result!r}")
        return None

    if not (latitude == latitude and longitude == longitude):
        print(f"[geocoding][debug] Nominatim returned non-finite coordinates for query='{query_label}' payload={first_result!r}")
        return None

    print(f"[geocoding][success] query='{query_label}' display_name='{display_name}' lat={latitude} lon={longitude}")
    return latitude, longitude


def _is_lat_lng_tuple(value):
    return isinstance(value, tuple) and len(value) == 2


def _geocode_with_candidates(label, candidates):
    for candidate in candidates:
        for attempt in range(1, 4):
            try:
                print(f"[geocoding][debug] {label} attempt={attempt}/3 query='{candidate}'")
                response = requests.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={"q": candidate, "format": "jsonv2", "limit": 1, "addressdetails": 1},
                    headers={"User-Agent": "SKDLS Transportations"},
                    timeout=15,
                )
                body_preview = response.text[:300] if response.text else ""
                print(f"[geocoding][debug] {label} status={response.status_code} body_preview={body_preview!r}")

                if not response.ok:
                    last_error = f"HTTP {response.status_code} for '{candidate}'"
                    continue

                try:
                    data = response.json()
                except ValueError as error:
                    last_error = f"Invalid JSON for '{candidate}': {error}"
                    print(f"[geocoding][debug] {label} {last_error}")
                    continue

                coordinates = _extract_nominatim_coordinates(data, candidate)
                if coordinates is not None:
                    print(f"[geocoding][success] {label} geocoded with '{candidate}' -> {coordinates}")
                    return coordinates

                print(f"[geocoding][debug] {label} no usable coordinates for '{candidate}'")
            except requests.RequestException as error:
                print(f"[geocoding][debug] {label} request error for '{candidate}': {error}")
            except Exception as error:
                print(f"[geocoding][debug] {label} unexpected error for '{candidate}': {error}")

    return None


def _get_coordinates(location):
    original_location = _prepare_location_for_geocoding(location)
    if not original_location:
        raise RuntimeError("Pickup or delivery address is required")

    normalized_location = normalize_location_with_gemini(original_location)
    cleaned_address = _strip_unnecessary_words(_apply_address_aliases(normalized_location or original_location))
    cleaned_address = _normalize_whitespace(cleaned_address)
    city_only = _extract_city_from_address(cleaned_address)
    landmark = _normalize_whitespace(cleaned_address.split(",", 1)[0]) if cleaned_address else ""
    landmark_city = _normalize_whitespace(f"{landmark}, {city_only}") if landmark and city_only and landmark.lower() != city_only.lower() else ""

    print(
        f"[geocoding][debug] original='{original_location}' normalized='{normalized_location}' cleaned='{cleaned_address}' city_only='{city_only}' landmark_city='{landmark_city}'"
    )

    retry_stages = [
        ("city_only", city_only),
        ("landmark_city", landmark_city),
        ("cleaned_address", cleaned_address),
    ]

    for stage_name, query in retry_stages:
        if not query:
            continue
        print(f"[geocoding][debug] fallback_stage_used='{stage_name}' query='{query}'")
        coordinates = _geocode_with_candidates(stage_name, [query])
        if _is_lat_lng_tuple(coordinates):
            print(f"[geocoding][debug] geocoding_result stage='{stage_name}' coordinates={coordinates}")
            return coordinates

    print(f"[geocoding][debug] geocoding_result stage='failed' original='{original_location}'")
    return None


def _calculate_straight_line_distance_km(source_latitude, source_longitude, destination_latitude, destination_longitude):
    try:
        source_latitude = float(source_latitude)
        source_longitude = float(source_longitude)
        destination_latitude = float(destination_latitude)
        destination_longitude = float(destination_longitude)
    except (TypeError, ValueError) as error:
        raise RuntimeError("Missing latitude/longitude for straight-line distance fallback") from error

    values = (source_latitude, source_longitude, destination_latitude, destination_longitude)
    if any(value != value for value in values):
        raise RuntimeError("Invalid latitude/longitude for straight-line distance fallback")

    radius_km = 6371.0
    phi1 = math.radians(source_latitude)
    phi2 = math.radians(destination_latitude)
    delta_phi = math.radians(destination_latitude - source_latitude)
    delta_lambda = math.radians(destination_longitude - source_longitude)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    distance_km = radius_km * c
    logger.info(f"[distance][fallback] straight_line_distance_km={distance_km:.2f}")
    return distance_km


def _estimate_distance_and_price(source, destination, tyre_type_code=None):
    logger.info(f"[distance][start] Estimating distance and price for: {source} -> {destination}")

    source_coordinates = _get_coordinates(source)
    destination_coordinates = _get_coordinates(destination)
    
    logger.info(f"[distance][source_coords] {source_coordinates!r}")
    logger.info(f"[distance][destination_coords] {destination_coordinates!r}")

    if not _is_lat_lng_tuple(source_coordinates) or not _is_lat_lng_tuple(destination_coordinates):
        raise ValueError(
            "Coordinates are malformed. Please provide clearer pickup and delivery addresses with a recognizable city or landmark."
        )

    source_latitude, source_longitude = source_coordinates
    destination_latitude, destination_longitude = destination_coordinates

    route_distance_km = None
    osrm_used = False
    try:
        url = (
            "https://router.project-osrm.org/route/v1/driving/"
            f"{source_longitude},{source_latitude};{destination_longitude},{destination_latitude}"
        )
        logger.info(f"[distance][osrm] request url={url}")

        response = requests.get(url, params={"overview": "false"}, timeout=15)
        body_preview = response.text[:300] if response.text else ""
        logger.info(f"[distance][osrm] status={response.status_code} body_preview={body_preview!r}")

        if response.ok:
            try:
                data = response.json()
            except ValueError as error:
                raise RuntimeError(f"Routing API returned invalid JSON: {error}") from error

            route_distance = data.get("routes", [{}])[0].get("distance")
            if isinstance(route_distance, (int, float)) and route_distance == route_distance:
                route_distance_km = float(route_distance) / 1000.0
                osrm_used = True
                logger.info(f"[distance][osrm_distance] distance_m={route_distance} distance_km={route_distance_km:.2f}")
            else:
                raise RuntimeError(f"Routing API returned invalid route data: {data}")
        else:
            raise RuntimeError(f"Routing API request failed with HTTP {response.status_code}")
    except Exception as error:
        logger.error(f"[distance][error] OSRM route lookup failed, using straight-line fallback: {error}")
        route_distance_km = _calculate_straight_line_distance_km(
            source_latitude,
            source_longitude,
            destination_latitude,
            destination_longitude,
        )
        logger.info(f"[distance][osrm_distance] osrm_unavailable straight_line_km={route_distance_km:.2f}")

    logger.info(f"[distance][result] raw_distance_km={route_distance_km:.2f}")

    # Prefer OSRM road distance when available.
    if osrm_used:
        final_distance = float(route_distance_km)
    else:
        # Fallback: use straight-line distance but avoid aggressive inflation.
        # Apply a small conservative correction factor (<=5%).
        correction_factor = 1.02
        corrected = route_distance_km * correction_factor

        # If distance is large, cap correction within ±5% of the straight-line estimate.
        if route_distance_km > 1000:
            lower = 0.95 * route_distance_km
            upper = 1.05 * route_distance_km
            final_distance = max(lower, min(upper, corrected))
        else:
            final_distance = corrected

    final_distance_km = max(1, round(float(final_distance)))
    logger.info(f"[distance][final_distance] final_distance_km={final_distance_km} source='{source}' destination='{destination}'")

    price = calculate_price(final_distance_km, tyre_type_code)
    logger.info(f"[distance][price] Calculated price: {price} for {final_distance_km}km ({tyre_type_code} tyre)")
    return final_distance_km, price


def get_chat_reply(message):
    if not GEMINI_API_KEY:
        return "Gemini is not configured yet. Please add GEMINI_API_KEY to backend/.env."

    try:
        model = _get_gemini_model()
        if not model:
            return "Sorry, the chatbot is temporarily unavailable. Please try again in a moment."

        response = model.generate_content(
            message,
            request_options={"timeout": GEMINI_TIMEOUT_SECONDS},
        )
        reply = getattr(response, "text", "")

        if not reply:
            return "Sorry, I could not generate a response right now. Please try again."

        return reply.strip()
    except TimeoutError as error:
        print(f"[gemini][timeout] chat_reply error={error}")
        return "Sorry, the chatbot is temporarily unavailable. Please try again in a moment."
    except Exception:
        logger.exception("[chat][exception] Gemini response generation failed")
        return "Sorry, the chatbot is temporarily unavailable. Please try again in a moment."


def _generate_contextual_reply(message, current_stage, booking_details, db_context=None):
    """Generate a natural conversational reply from Gemini while preserving booking context.
    
    Used for answering questions during booking stages without breaking the flow.
    Returns a conversational reply string.
    """
    if not GEMINI_API_KEY:
        print(f"[gemini][conversation] disabled stage={current_stage}")
        return ""
    
    try:
        role = str((db_context or {}).get("role") or "customer").strip().lower() or "customer"
        role_policy = _get_role_chat_policy(role)
        source = str(booking_details.get("source", "") or "").strip()
        destination = str(booking_details.get("destination", "") or "").strip()
        tons = str(booking_details.get("tons", "") or "").strip()
        
        context_str = f"Source: {source}, Destination: {destination}, Load: {tons} tons" if any([source, destination, tons]) else "Starting new booking"
        db_context_block = _context_block_for_prompt(db_context)
        
        model = _get_gemini_model()
        if not model:
            return ""
        prompt = f"""You are SKDLS Transportations' helpful chatbot. Respond naturally and briefly to this user question during their truck booking process.

    User role: {role_policy.get('label', role.title())}
    Allowed capabilities: {', '.join(sorted(role_policy.get('allowed_intents', [])))}
    Role focus: {role_policy.get('assistant_focus', 'customer support')}
Current booking: {context_str}
Current stage: {current_stage}
User message: {message}
Database context: {db_context_block or 'none'}

Respond in 1-2 sentences. Be helpful and friendly but keep it short. Do NOT ask them to restart or validate fields - just answer their question.""".strip()

        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(max_output_tokens=100, temperature=0.7),
            request_options={"timeout": GEMINI_TIMEOUT_SECONDS},
        )
        reply = str(getattr(response, "text", "")).strip()
        
        if reply:
            print(f"[gemini][conversation] reply_generated stage={current_stage} len={len(reply)}")
            return reply
        
        print(f"[gemini][conversation] empty_reply stage={current_stage}")
        return ""
    except TimeoutError as error:
        print(f"[gemini][timeout] conversation stage={current_stage} error={error}")
        return ""
    except Exception as error:
        logger.exception(f"[gemini][conversation] exception {error}")
        return ""


def _clean_json_text(text):
    cleaned = str(text or "").strip()
    cleaned = cleaned.replace("```json", "").replace("```", "").strip()
    cleaned = cleaned.replace("\ufeff", "").strip()
    return cleaned


def _parse_gemini_json(raw_text, fallback_payload, log_prefix):
    cleaned = _clean_json_text(raw_text)

    if not cleaned:
        print(f"{log_prefix} empty_response")
        return fallback_payload

    try:
        return json.loads(cleaned)
    except Exception:
        try:
            match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
            if match:
                return json.loads(match.group(0))
        except Exception:
            pass

        print(f"{log_prefix} raw={cleaned!r}")
        return fallback_payload


def _validate_booking_data(payload):
    if not isinstance(payload, dict):
        raise ValueError("Booking payload must be a JSON object")

    source = _normalize_booking_text(payload.get("source", ""))
    destination = _normalize_booking_text(payload.get("destination", ""))
    tons_value = _extract_numeric_value(payload.get("tons", ""))

    truck_type_code = _truck_type_from_tons(tons_value)
    
    if tons_value and truck_type_code:
        print(f"[booking][tyre_recommendation] gemini_validation tons={tons_value} tyre_code={truck_type_code}")

    return {
        "source": source,
        "destination": destination,
        "tons": tons_value,
        "truck_type": _canonical_truck_type_label(truck_type_code) if truck_type_code else "",
    }


def get_gemini_response(message):
    fallback_booking = _fallback_booking_details(message)
    if not GEMINI_API_KEY:
        return fallback_booking

    try:
        model = _get_gemini_model()
        if not model:
            return fallback_booking
        prompt = f"""
    Extract the transport booking details from the user's complete message.
    Return ONLY valid JSON with exactly these keys:
    {{
      "source": "",
      "destination": "",
      "tons": "",
      "truck_type": ""
    }}

    Rules:
    - Return JSON only. No markdown, no code fences, no explanation.
    - source and destination must be city names or place names as plain text.
    - tons must be the load weight as a plain numeric string.
    - truck_type is optional and can be left empty because it is derived from tons.
    - If any required field is missing, return empty strings for that field.

    User message: {message}
    """.strip()
        response = model.generate_content(
            prompt,
            request_options={"timeout": GEMINI_TIMEOUT_SECONDS},
        )
        reply = _clean_json_text(getattr(response, "text", ""))

        if not reply:
            return fallback_booking

        parsed = _parse_gemini_json(reply, {}, "[gemini][parse_error]")
        if not isinstance(parsed, dict):
            return fallback_booking
        booking_data = _validate_booking_data(parsed)

        if any(str(booking_data.get(field, "") or "").strip() for field in ("source", "destination", "tons")):
            return booking_data

        return fallback_booking
    except TimeoutError:
        print("[gemini][timeout] booking_extraction")
        return fallback_booking
    except (ValueError, TypeError, json.JSONDecodeError):
        return fallback_booking
    except Exception:
        logger.exception("[chat][exception] Gemini booking extraction failed")
        return fallback_booking


def _is_complete_booking(booking_details):
    return all(
        str(booking_details.get(field, "") or "").strip()
        for field in BOOKING_FIELDS
    )


def _merge_booking_details(existing_details, extracted_details):
    merged = dict(EMPTY_BOOKING_JSON)

    for field in BOOKING_FIELDS:
        existing_value = str((existing_details or {}).get(field, "") or "").strip()
        extracted_value = str((extracted_details or {}).get(field, "") or "").strip()

        merged[field] = extracted_value or existing_value

    return merged


def _missing_booking_fields(booking_details):
    # Return keys for missing fields so logic can decide next steps precisely
    missing = []
    if not str(booking_details.get("source", "") or "").strip():
        missing.append("source")
    if not str(booking_details.get("destination", "") or "").strip():
        missing.append("destination")
    if not str(booking_details.get("tons", "") or "").strip():
        missing.append("tons")
    return missing


def _missing_booking_prompt(missing_fields):
    if not missing_fields:
        return ""

    # Map internal keys to human friendly labels
    label_map = {
        "source": "source city",
        "destination": "destination city",
        "tons": "tons/load weight",
    }

    human = [label_map.get(key, key) for key in missing_fields]

    if len(human) == 1:
        return f"Please provide the {human[0]}."
    if len(human) == 2:
        return f"Please provide the {human[0]} and {human[1]}."
    return f"Please provide the {', '.join(human[:-1])}, and {human[-1]}."


def _set_stage(user_state, new_stage):
    prev = user_state.get("stage", "<none>")
    user_state["stage"] = new_stage
    print(f"[chat][state] {user_state.get('user_id','?')} {prev} -> {new_stage}")


def _log_chat_state(user_state, note=""):
    print(
        f"[chat][state] user={user_state.get('user_id','?')} stage={user_state.get('stage','idle')} "
        f"booking_mode={bool(user_state.get('booking_mode'))} onboarding_shown={bool(user_state.get('onboarding_shown'))} "
        f"active_intent={user_state.get('active_intent','')} session_id={user_state.get('session_id','')} note={note}"
    )


def _set_active_intent(user_state, intent, source=""):
    previous_intent = str(user_state.get("active_intent") or "")
    user_state["active_intent"] = intent
    print(f"[chat][intent] user={user_state.get('user_id','?')} {previous_intent or '<none>'} -> {intent} source={source}")


def _set_booking_mode(user_state, enabled=True, source=""):
    previous_mode = bool(user_state.get("booking_mode"))
    user_state["booking_mode"] = enabled
    if enabled:
        user_state["active_intent"] = "booking"
    print(f"[chat][booking_mode] user={user_state.get('user_id','?')} {previous_mode} -> {enabled} source={source}")


def _clean_greeting_reply():
    return "Welcome to SKDLS Transportations. Are you looking to book a truck today?"


def _is_reset_intent(message):
    text = str(message or "").strip().lower()
    return text in {"hi", "hello", "hey", "restart", "reset", "start over"}


def _get_or_create_chat_session_id(payload, user_state=None, user_id=None):
    session_id = str((payload or {}).get("session_id") or "").strip()

    if session_id:
        if user_state is not None:
            user_state["session_id"] = session_id
        return session_id

    if user_state is not None:
        existing_session_id = str(user_state.get("session_id") or "").strip()
        if existing_session_id:
            return existing_session_id

    generated_session_id = f"session_{uuid.uuid4().hex}"
    if user_state is not None:
        user_state["session_id"] = generated_session_id
    logger.info(f"[chat] Session started session_id={generated_session_id!r} user={user_id!r}")
    return generated_session_id


def _get_or_create_conversation_id(user_state, session_id, user_id=None):
    conversation_id = str((user_state or {}).get("conversation_id") or "").strip()

    if conversation_id:
        return conversation_id

    if session_id:
        if user_state is not None:
            user_state["conversation_id"] = session_id
        print(f"[conversation][debug] derived conversation_id={session_id!r} for user={user_id!r}")
        return session_id

    generated_conversation_id = f"conversation_{uuid.uuid4().hex}"
    if user_state is not None:
        user_state["conversation_id"] = generated_conversation_id
    print(f"[conversation][debug] generated conversation_id={generated_conversation_id!r} for user={user_id!r}")
    return generated_conversation_id


def _append_conversation_turn(user_state, user_message, bot_reply):
    conversation = user_state.get("conversation_messages")
    if not isinstance(conversation, list):
        conversation = []
    conversation.append({"role": "user", "content": str(user_message or "")})
    conversation.append({"role": "assistant", "content": str(bot_reply or "")})
    user_state["conversation_messages"] = conversation
    user_state["conversation_json"] = json.dumps(conversation, ensure_ascii=False)
    return conversation


def _extract_bot_reply_for_history(response_obj):
    payload = None

    target = response_obj[0] if isinstance(response_obj, tuple) else response_obj
    if hasattr(target, "get_json"):
        try:
            payload = target.get_json(silent=True)
        except Exception:
            payload = None

    if isinstance(payload, dict):
        for key in ("reply", "error", "message", "response"):
            if payload.get(key) is not None:
                return str(payload.get(key))
        return json.dumps(payload, ensure_ascii=False)

    if hasattr(target, "get_data"):
        try:
            return str(target.get_data(as_text=True) or "")
        except Exception:
            return ""

    return ""


def _extract_response_payload_for_memory(response_obj):
    target = response_obj[0] if isinstance(response_obj, tuple) else response_obj

    if hasattr(target, "get_json"):
        try:
            payload = target.get_json(silent=True)
            return dict(payload) if isinstance(payload, dict) else {}
        except Exception:
            return {}

    if hasattr(target, "get_data"):
        try:
            text = str(target.get_data(as_text=True) or "").strip()
            payload = json.loads(text) if text else {}
            return dict(payload) if isinstance(payload, dict) else {}
        except Exception:
            return {}

    return dict(target) if isinstance(target, dict) else {}


def _save_chat_history_before_return(user_state, user_message, response_obj, return_path):
    user_state = user_state or {}
    user_id = str(user_state.get("user_id") or "default_user")
    session_id = _get_or_create_chat_session_id({"session_id": user_state.get("session_id", "")}, user_state=user_state, user_id=user_id)
    conversation_id = _get_or_create_conversation_id(user_state, session_id, user_id=user_id)
    bot_reply = _extract_bot_reply_for_history(response_obj)
    response_payload = _extract_response_payload_for_memory(response_obj)
    conversation = _append_conversation_turn(user_state, user_message, bot_reply)
    conversation_json = user_state.get("conversation_json") or json.dumps(conversation, ensure_ascii=False)
    workflow_type = str(response_payload.get("workflow") or ("shipment_booking" if user_state.get("booking_mode") or user_state.get("stage") in BOOKING_STATES else "assistant")).strip() or "assistant"
    current_step = str(user_state.get("stage") or response_payload.get("route") or "idle").strip() or "idle"

    print(f"[chat][return_path] {return_path}")
    print(f"[chat][session_id] {session_id}")
    print(f"[chat][user_message] {str(user_message or '')}")
    print(f"[chat][bot_reply] {bot_reply}")

    try:
        save_chat_history(session_id, str(user_message or ""), bot_reply)
    except Exception as error:
        print(f"[chat_history][error] route-level save failed: {error}")

    try:
        save_conversation_json(conversation_id, session_id, conversation_json)
    except Exception as error:
        print(f"[conversation][error] route-level save failed: {error}")

    try:
        from db import save_ai_conversation_memory, save_ai_session_context, save_workflow_memory

        memory_window = {
            "conversation": _tail(conversation, 12),
            "last_user_message": str(user_message or ""),
            "last_bot_reply": bot_reply,
            "intent": response_payload.get("intent") or user_state.get("ai_last_intent") or "",
            "action": response_payload.get("action") or user_state.get("ai_last_action") or "",
            "workflow": workflow_type,
            "route": response_payload.get("route") or "",
            "card_type": response_payload.get("card_type") or response_payload.get("type") or user_state.get("ai_last_card_type") or "",
            "confidence": response_payload.get("confidence") or 0,
            "return_path": return_path,
        }
        save_ai_conversation_memory(
            user_id=user_id,
            session_id=session_id,
            conversation_id=conversation_id,
            memory_json=memory_window,
            summary=bot_reply[:500],
            last_intent=memory_window["intent"],
            last_route=memory_window["route"],
            confidence=memory_window["confidence"],
        )
        save_ai_session_context(
            session_id=session_id,
            user_id=user_id,
            role=user_state.get("auth_role") or "customer",
            context_json={
                "stage": current_step,
                "booking": user_state.get("booking") if isinstance(user_state.get("booking"), dict) else {},
                "quote": user_state.get("quote", {}),
                "payment": user_state.get("payment", {}),
                "ai_action_log": _tail(user_state.get("ai_action_log") or [], 15),
                "response": response_payload,
            },
            active_workflow=workflow_type,
            current_step=current_step,
            status="active" if user_state.get("booking_mode") or user_state.get("stage") in BOOKING_STATES else "idle",
        )
        save_workflow_memory(
            user_id=user_id,
            workflow_type=workflow_type,
            workflow_state=_tail(user_state.get("ai_workflow_memory") or [], 10) or memory_window,
            current_step=current_step,
            context_json={
                "session_id": session_id,
                "conversation_id": conversation_id,
                "return_path": return_path,
                "intent": memory_window["intent"],
                "route": memory_window["route"],
            },
        )
    except Exception as error:
        print(f"[ai_memory][error] route-level save failed: {error}")

    try:
        booking_state = user_state.get("booking") if isinstance(user_state.get("booking"), dict) else {}
        booking_workflow_manager.save(
            session_id=session_id,
            user_id=user_id,
            workflow=workflow_type,
            current_step=current_step,
            draft_json={
                **(booking_state or {}),
                "stage": current_step,
                "booking_mode": bool(user_state.get("booking_mode")),
                "requested_field": user_state.get("requested_field", ""),
                "quote": user_state.get("quote", {}),
                "ai_last_action": user_state.get("ai_last_action", ""),
                "ai_last_tool": user_state.get("ai_last_tool", ""),
                "ai_last_card_type": user_state.get("ai_last_card_type", ""),
                "ai_last_intent": user_state.get("ai_last_intent", ""),
                "ai_last_error": user_state.get("ai_last_error", ""),
                "ai_action_log": _tail(user_state.get("ai_action_log") or [], 10),
            },
            quote_json=user_state.get("quote", {}),
            payment_json=user_state.get("payment", {}),
            shipment_id=user_state.get("shipment_id"),
            status="active" if user_state.get("booking_mode") or user_state.get("stage") in BOOKING_STATES else "idle",
            last_user_message=str(user_message or ""),
            last_bot_reply=bot_reply,
        )
    except Exception as error:
        print(f"[booking_session][error] route-level save failed: {error}")

    return response_obj


def reset_session(user_id=None, user_state=None):
    """Clear any cached chat/booking state for a fresh conversation."""
    state = user_state or (user_data.get(user_id) if user_id else None)

    if state is not None:
        state["stage"] = "idle"
        state["booking"] = dict(EMPTY_BOOKING_JSON)
        state["requested_field"] = ""
        state["quote"] = {}
        state["onboarding_shown"] = False
        state["session_id"] = ""
        state["conversation_id"] = ""
        state["conversation_messages"] = []
        state["conversation_json"] = ""
        state.pop("exact_pickup", None)
        state.pop("exact_delivery", None)
        state.pop("pickup_location", None)
        state.pop("drop_location", None)
        state.pop("source_location", None)
        state.pop("destination_location", None)
        _set_booking_mode(state, False, source="reset_session")
        _set_active_intent(state, "", source="reset_session")

    if user_id is not None:
        user_data.pop(user_id, None)

    print(f"[chat][state] reset_session user={user_id or (state or {}).get('user_id', '?')}")


def _start_new_booking(user_state):
    # Clear stale booking details and start fresh
    user_state["booking"] = dict(EMPTY_BOOKING_JSON)
    user_state["requested_field"] = ""
    user_state["quote"] = {}
    user_state["onboarding_shown"] = True
    _set_booking_mode(user_state, True, source="start_new_booking")
    _set_active_intent(user_state, "booking", source="start_new_booking")
    _set_stage(user_state, "collecting_source")
    user_state["requested_field"] = "source"
    print(f"[chat][start] starting new booking for {user_state.get('user_id','?')}")


def _get_chat_state(user_id):
    state = user_data.setdefault(user_id, {})
    state.setdefault("stage", "idle")
    state.setdefault("booking", dict(EMPTY_BOOKING_JSON))
    state.setdefault("requested_field", "")
    state.setdefault("quote", {})
    state.setdefault("booking_mode", False)
    state.setdefault("active_intent", "")
    state.setdefault("onboarding_shown", False)
    state.setdefault("session_id", "")
    state.setdefault("conversation_id", "")
    state.setdefault("conversation_messages", [])
    state.setdefault("conversation_json", "")
    return state


def _get_optional_auth_context():
    claims = {}
    identity = ""

    try:
        verify_jwt_in_request(optional=True)
        claims = get_jwt() or {}
        identity = str(get_jwt_identity() or "").strip()
    except Exception:
        claims = {}
        identity = ""

    role = str((claims or {}).get("role") or "").strip().lower()
    return {
        "claims": claims,
        "identity": identity,
        "role": role,
    }


def _trim_prompt_context(value, depth=0, max_depth=2, max_items=4):
    sensitive_keys = {
        "api_key",
        "email",
        "license_number",
        "phone",
        "password",
        "password_hash",
        "razorpay_order_id",
        "razorpay_payment_id",
    }

    if value is None:
        return None

    if isinstance(value, dict):
        trimmed = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                trimmed["..."] = "truncated"
                break
            if key in sensitive_keys and item:
                trimmed[key] = _mask_secret(item)
            elif depth >= max_depth and isinstance(item, (dict, list)):
                trimmed[key] = "truncated"
            else:
                trimmed[key] = _trim_prompt_context(item, depth + 1, max_depth=max_depth, max_items=max_items)
        return trimmed

    if isinstance(value, list):
        items = value[:max_items]
        trimmed_list = []
        for item in items:
            if depth >= max_depth and isinstance(item, (dict, list)):
                trimmed_list.append("truncated")
            else:
                trimmed_list.append(_trim_prompt_context(item, depth + 1, max_depth=max_depth, max_items=max_items))
        if len(value) > max_items:
            trimmed_list.append("truncated")
        return trimmed_list

    if isinstance(value, (datetime,)):
        return value.isoformat(sep=" ", timespec="seconds")

    return value


def _build_chat_db_context(raw_message, user_state, auth_context):
    user_state = user_state or {}
    auth_context = auth_context or {}
    role = str(auth_context.get("role") or "").strip().lower() or "customer"
    role_policy = _get_role_chat_policy(role)
    resolved_user_id = str(auth_context.get("identity") or user_state.get("user_id") or "").strip()
    session_id = str(user_state.get("session_id") or "").strip()

    context = {
        "role": role,
        "role_label": role_policy.get("label", role.title()),
        "role_focus": role_policy.get("assistant_focus", "customer support"),
        "allowed_intents": sorted(role_policy.get("allowed_intents", set())),
        "quick_actions": list(role_policy.get("quick_actions", [])),
        "user_id": resolved_user_id,
        "session_id": session_id,
        "message": str(raw_message or "")[:240],
        "auth": {
            "identity": resolved_user_id,
            "role": role,
            "claims": _trim_prompt_context(auth_context.get("claims") or {}),
        },
    }

    try:
        user_context = get_user_context(resolved_user_id or user_state.get("user_id") or "", session_id=session_id)
        if user_context:
            latest_shipment = user_context.get("latest_shipment") or {}
            latest_payment = user_context.get("latest_payment") or get_latest_payment_for_user(resolved_user_id or user_state.get("user_id") or "")
            active_payments = user_context.get("active_payments") or []
            context["customer"] = user_context
            context["latest_shipment"] = latest_shipment
            context["latest_payment"] = latest_payment
            context["active_payments"] = active_payments
            context["recent_shipments"] = user_context.get("recent_shipments") or []
            context["open_shipments"] = user_context.get("open_shipments") or []
            context["payment_history"] = user_context.get("payment_history") or []
            preference_memory = user_context.get("preference_memory") or {}
            context["preference_memory"] = preference_memory
            context["preferred_route"] = preference_memory.get("preferred_route") or ""
            context["preferred_vehicle_type"] = preference_memory.get("preferred_vehicle_type") or ""
            context["preferred_payment_method"] = preference_memory.get("preferred_payment_method") or ""
            context["recent_conversation"] = preference_memory.get("recent_conversation") or []

            if latest_shipment.get("id") is not None:
                live_tracking = get_shipment_tracking(latest_shipment.get("id"))
                if live_tracking:
                    context["tracking"] = live_tracking
                    context["latest_tracking"] = live_tracking

        if role in {"admin", "super_admin"}:
            analytics_window = get_admin_analytics_window(days=1)
            context["analytics"] = analytics_window
            context["admin"] = analytics_window
            context["monitoring"] = _build_monitoring_dashboard_payload(days=1)
            context["active_shipments"] = (analytics_window.get("recent_shipments") or [])[:10]
        elif role == "driver":
            driver_id = resolved_user_id or user_state.get("driver_id") or user_state.get("user_id")
            driver_context = get_driver_context(driver_id)
            context["driver"] = driver_context
            context["assigned_trips"] = driver_context.get("assigned_shipments") or []
            context["gps_logs"] = driver_context.get("gps_logs") or []
            latest_assigned = (driver_context.get("assigned_shipments") or [None])[0]
            if latest_assigned and latest_assigned.get("id") is not None:
                context["latest_tracking"] = get_shipment_tracking(latest_assigned.get("id"))

        reference_id = _extract_reference_id(raw_message)
        if reference_id is not None:
            context["reference_id"] = reference_id
            try:
                shipment_tracking = get_shipment_tracking(reference_id)
                if shipment_tracking:
                    context["reference_tracking"] = shipment_tracking
            except Exception:
                context["reference_tracking_error"] = True

        if role in {"admin", "super_admin"}:
            context["admin_context"] = {
                "analytics": context.get("analytics") or {},
                "monitoring": context.get("monitoring") or {},
                "recent_shipments": (context.get("analytics") or {}).get("recent_shipments") or [],
                "recent_payments": (context.get("analytics") or {}).get("recent_payments") or [],
            }
    except Exception as error:
        logger.warning(f"[chat][db_context][warning] {error}")

    return context


def _context_block_for_prompt(db_context):
    if not db_context:
        return ""

    try:
        trimmed = _trim_prompt_context(db_context)
        return json.dumps(trimmed, ensure_ascii=False)
    except Exception:
        return ""


def _looks_like_tracking_query(message):
    text = str(message or "").strip().lower()
    return any(keyword in text for keyword in {
        "where is my shipment",
        "track my shipment",
        "track shipment",
        "shipment status",
        "live location",
        "where is my order",
        "latest shipment",
    })


def _looks_like_payment_query(message):
    text = str(message or "").strip().lower()
    return any(keyword in text for keyword in {
        "latest payment",
        "payment status",
        "show my payment",
        "pending payment",
        "payment due",
        "payment receipt",
    })


def _looks_like_driver_query(message):
    text = str(message or "").strip().lower()
    return any(keyword in text for keyword in {
        "assigned shipment",
        "assigned shipments",
        "my shipment",
        "start trip",
        "trip status",
        "update shipment status",
        "shipment status",
        "proof of delivery",
        "upload pod",
        "pod",
        "driver update",
    })


def _looks_like_admin_analytics_query(message):
    text = str(message or "").strip().lower()
    return any(keyword in text for keyword in {
        "how many delayed shipments",
        "delayed shipments",
        "today revenue",
        "revenue today",
        "total shipments",
        "active shipments",
        "pending payments",
        "fleet status",
        "dashboard summary",
    })


def _build_structured_db_reply(raw_message, user_state, auth_context, db_context=None):
    db_context = db_context or _build_chat_db_context(raw_message, user_state, auth_context)
    role = str(auth_context.get("role") or "customer").strip().lower()
    role_policy = _get_role_chat_policy(role)
    reference_id = _extract_reference_id(raw_message)
    customer_context = db_context.get("customer") or {}
    driver_context = db_context.get("driver") or {}
    analytics = db_context.get("analytics") or {}

    if _looks_like_driver_query(raw_message) and role != "driver" and role not in {"admin", "super_admin"}:
        return _build_chat_intent_payload(
            "DRIVER_UPDATE",
            0.9,
            "Driver operations are available to authenticated drivers only.",
            INTENT_WORKFLOWS["DRIVER_UPDATE"],
            "driver.access_restricted",
            suggestions=role_policy.get("quick_actions", []),
            status="forbidden",
            http_status=403,
        )

    if _looks_like_payment_query(raw_message) and role != "customer":
        return _build_chat_intent_payload(
            "MAKE_PAYMENT",
            0.9,
            "Payment workflows are available to customers only.",
            INTENT_WORKFLOWS["MAKE_PAYMENT"],
            "payments.access_restricted",
            suggestions=role_policy.get("quick_actions", []),
            status="forbidden",
            http_status=403,
        )

    if _looks_like_admin_analytics_query(raw_message) and role not in {"admin", "super_admin"}:
        return _build_chat_intent_payload(
            "GET_ANALYTICS",
            0.9,
            "Analytics is restricted to admin users.",
            INTENT_WORKFLOWS["GET_ANALYTICS"],
            "analytics.access_restricted",
            suggestions=role_policy.get("quick_actions", []),
            status="forbidden",
            http_status=403,
        )

    if _looks_like_admin_analytics_query(raw_message) and role in {"admin", "super_admin"}:
        summary = analytics.get("summary") or {}
        reply = (
            f"Today’s snapshot: {int(summary.get('total_shipments', 0))} shipments, "
            f"{int(summary.get('active_shipments', 0))} active, "
            f"{int(summary.get('delayed_shipments', 0))} delayed, and ₹{int(summary.get('revenue_collected', 0)):,} collected."
        )
        return _build_chat_intent_payload(
            "GET_ANALYTICS",
            1.0,
            reply,
            INTENT_WORKFLOWS["GET_ANALYTICS"],
            "analytics.database_snapshot",
            data=analytics,
        )

    if role == "driver" and (_looks_like_driver_query(raw_message) or _looks_like_tracking_query(raw_message)):
        assigned_shipments = driver_context.get("assigned_shipments") or []
        gps_logs = driver_context.get("gps_logs") or []
        driver = driver_context.get("driver") or {}

        reply = (
            f"Driver profile loaded for {driver.get('driver_name') or 'your account'} with {len(assigned_shipments)} assigned shipment(s)."
        )
        if assigned_shipments:
            latest_shipment = assigned_shipments[0]
            reply += f" Latest assignment #{latest_shipment.get('id')} is {latest_shipment.get('shipment_status') or 'pending'}."

        return _build_chat_intent_payload(
            "DRIVER_UPDATE",
            0.98,
            reply,
            INTENT_WORKFLOWS["DRIVER_UPDATE"],
            "driver.assigned_shipments",
            data={
                "driver": driver,
                "assigned_shipments": assigned_shipments,
                "gps_logs": gps_logs,
                "quick_actions": role_policy.get("quick_actions", []),
            },
            suggestions=role_policy.get("quick_actions", []),
            response_type="driver_assignment_card",
        )

    if _looks_like_tracking_query(raw_message):
        tracking_payload = None
        shipment_row = None
        if reference_id is not None:
            shipment_row = _fetch_shipment_record_by_id(reference_id)
            tracking_payload = get_shipment_tracking(reference_id)
        elif customer_context.get("latest_shipment"):
            shipment_row = _fetch_shipment_record_by_id(customer_context["latest_shipment"].get("id"))
            tracking_payload = get_shipment_tracking(customer_context["latest_shipment"].get("id"))

        if tracking_payload and tracking_payload.get("shipment"):
            live_tracking = _build_shipment_live_tracking_payload(shipment_row) if shipment_row else None
            shipment = tracking_payload.get("shipment") or {}
            latest_location = tracking_payload.get("latest_location") or {}
            live_location = (live_tracking or {}).get("live_location") or latest_location
            route_coordinates = (live_tracking or {}).get("route_coordinates") or []
            eta_hours = (live_tracking or {}).get("eta_hours")
            route_source = (live_tracking or {}).get("route_source") or "gps"
            route_distance = (live_tracking or {}).get("distance_km")
            summary = (live_tracking or {}).get("summary") or {}
            reply = (
                f"Shipment #{shipment.get('id')} is currently {shipment.get('shipment_status') or 'pending'} "
                f"from {shipment.get('pickup_location') or 'the pickup point'} to {shipment.get('drop_location') or 'the drop point'}."
            )
            if live_location.get("latitude") is not None and live_location.get("longitude") is not None:
                reply += f" Live GPS: {live_location.get('latitude')}, {live_location.get('longitude')}."
            if eta_hours is not None:
                reply += f" ETA: about {round(float(eta_hours), 1)} hours."
            return _build_chat_intent_payload(
                "TRACK_SHIPMENT",
                1.0,
                reply,
                INTENT_WORKFLOWS["TRACK_SHIPMENT"],
                "tracking.database_snapshot",
                data={
                    **tracking_payload,
                    **(live_tracking or {}),
                    "live_location": live_location,
                    "latest_location": live_location,
                    "route_coordinates": route_coordinates,
                    "route_source": route_source,
                    "distance_km": route_distance,
                    "eta_hours": eta_hours,
                    "summary": {
                        **summary,
                        "route_status": shipment.get("shipment_status") or "pending",
                    },
                },
                response_type="tracking_card",
            )

        latest_shipment = customer_context.get("latest_shipment") or {}
        if latest_shipment:
            reply = (
                f"I found shipment #{latest_shipment.get('id')} in your account, but I need the shipment reference or an assigned driver to show live tracking."
            )
            return _build_chat_intent_payload(
                "TRACK_SHIPMENT",
                0.92,
                reply,
                INTENT_WORKFLOWS["TRACK_SHIPMENT"],
                "tracking.needs_reference",
                data={"shipment": latest_shipment},
            )

        return _build_chat_intent_payload(
            "TRACK_SHIPMENT",
            0.85,
            "I could not find a shipment linked to your account yet. Share a shipment ID or booking reference.",
            INTENT_WORKFLOWS["TRACK_SHIPMENT"],
            "tracking.no_match",
            suggestions=["Share shipment ID", "Share booking reference"],
        )

    if _looks_like_payment_query(raw_message):
        latest_payment = customer_context.get("latest_payment")
        active_payments = customer_context.get("active_payments") or []

        payment_record = None
        if reference_id is not None:
            payment_record = get_payment_for_reference(reference_id)
        elif latest_payment:
            payment_record = latest_payment

        if payment_record:
            status = str(payment_record.get("payment_status") or payment_record.get("status") or "created").strip()
            reply = f"Your latest payment is {status} for ₹{int(payment_record.get('amount') or 0):,}."
            return _build_chat_intent_payload(
                "MAKE_PAYMENT",
                1.0,
                reply,
                INTENT_WORKFLOWS["MAKE_PAYMENT"],
                "payments.database_snapshot",
                data={"latest_payment": payment_record, "active_payments": active_payments},
            )

        if active_payments:
            reply = f"I found {len(active_payments)} pending payment record(s) linked to your account."
            return _build_chat_intent_payload(
                "MAKE_PAYMENT",
                0.9,
                reply,
                INTENT_WORKFLOWS["MAKE_PAYMENT"],
                "payments.pending_snapshot",
                data={"active_payments": active_payments},
            )

        return _build_chat_intent_payload(
            "MAKE_PAYMENT",
            0.8,
            "I could not find any recent payments for your account. Share a booking or shipment reference.",
            INTENT_WORKFLOWS["MAKE_PAYMENT"],
            "payments.no_match",
            suggestions=["Share booking reference", "Share shipment reference"],
        )

    if role == "driver" and driver_context.get("driver"):
        driver = driver_context.get("driver") or {}
        reply = f"Driver profile loaded for {driver.get('driver_name') or 'this driver'} with status {driver.get('status') or 'unknown'}."
        return _build_chat_intent_payload(
            "DRIVER_UPDATE",
            0.86,
            reply,
            INTENT_WORKFLOWS["DRIVER_UPDATE"],
            "driver.database_snapshot",
            data=driver_context,
        )

    return None


def _request_prompt_for_field(field_name):
    prompts = {
        "source": "Please provide the source city.",
        "destination": "Please provide the destination city.",
        "tons": "Please provide the tons/load weight.",
        "tyre_type": "Please specify tyre type: 12 tyre, 14 tyre, or 16 tyre.",
    }

    return prompts.get(field_name, "Please provide the missing booking detail.")


def _validate_location_relevance(value):
    text = str(value or "").strip()
    if len(text) < 3:
        print(f"[validation][irrelevant_input] too short: {text!r}")
        return False

    normalized_city = text
    alias_key = text.lower().strip()
    alias_match = CITY_ALIASES.get(alias_key)
    if alias_match:
        print(f"[validation][alias_match] alias={text!r} normalized={alias_match!r}")
        normalized_city = alias_match

    print(f"[validation][normalized_city] original={text!r} normalized={normalized_city!r}")

    low = normalized_city.lower()

    # reject direct question words or question sentences
    if any(w in low for w in ("how", "why", "what", "who")) or "?" in text:
        print(f"[validation][irrelevant_input] contains question words: {text!r}")
        return False

    # reject pure conversational inputs
    conversational = {"hi", "hello", "yes", "no", "ok", "okay", "thanks", "thank", "y", "n", "hey", "sure", "bike", "car"}
    if low.strip() in conversational:
        print(f"[validation][irrelevant_input] conversational keyword: {text!r}")
        return False

    # reject obvious non-location tokens (language names, emotions) - basic blacklist
    obvious_non_location = {"english", "hindi", "french", "spanish", "happy", "sad", "angry", "telugu"}
    if low.strip() in obvious_non_location:
        print(f"[validation][irrelevant_input] non-location token: {text!r}")
        return False

    # allowed character set for place names
    if not re.fullmatch(r"[A-Za-z\s\-\.'()&,]+", text):
        print(f"[validation][irrelevant_input] contains invalid chars: {text!r}")
        return False

    normalized = low.strip()

    # Known city hints
    try:
        if normalized in KNOWN_CITY_HINTS:
            print(f"[validation][city_match] known city matched: {normalized!r}")
            return True
    except Exception:
        pass

    # scoring keywords indicating transport/location relevance
    indicators = {
        "nagar", "city", "road", "colony", "industrial", "hub", "warehouse",
        "circle", "street", "area", "vijayawada", "hyderabad", "bangalore", "mumbai"
    }
    score = sum(1 for kw in indicators if kw in low)
    if score > 0:
        print(f"[validation][passed] location indicators matched: {text!r}")
        return True

    # heuristics: accept if it looks like a proper place name
    tokens = [t for t in re.split(r"\s+", text) if t]
    known_suffixes = ("nagar", "abad", "pur", "pura", "gaon", "ville", "city")

    # single-word heuristics: accept if alphabetic, length>=3 and capitalized or known city
    if len(tokens) == 1:
        token = tokens[0]
        if re.fullmatch(r"[A-Za-z]+", token) and len(token) >= 3:
            if token[0].isupper() or token.istitle():
                print(f"[validation][heuristic_match] single-word proper noun: {text!r}")
                return True
            if token.lower() in KNOWN_CITY_HINTS:
                print(f"[validation][city_match] known city matched (single-word): {token.lower()!r}")
                return True

    if len(tokens) >= 2:
        # multi-word names are likely place names
        print(f"[validation][passed] multi-word place: {text!r}")
        return True

    if any(normalized.endswith(suf) for suf in known_suffixes):
        print(f"[validation][passed] suffix matched: {text!r}")
        return True

    print(f"[validation][irrelevant_input] no location relevance: {text!r}")
    return False


def _validate_city(value):
    # Backwards-compatible wrapper: use semantic relevance validator
    return _validate_location_relevance(value)


def _validate_tons(value):
    text = str(value or "").strip()
    match = re.search(r"\d+", text.replace(",", ""))
    if not match:
        print(f"[validation][failed] tons not numeric: {text!r}")
        return False
    try:
        num = int(match.group(0))
    except Exception:
        print(f"[validation][failed] tons parse error: {text!r}")
        return False
    if not (1 <= num <= 100):
        print(f"[validation][failed] tons out of range: {num}")
        return False
    print(f"[validation][passed] tons validated: {num}")
    return True


def _validate_location_text(value):
    text = str(value or "").strip()
    if len(text) < 5:
        print(f"[validation][failed] location too short: {text!r}")
        return False
    if not re.search(r"[A-Za-z0-9]", text):
        print(f"[validation][failed] location lacks readable text: {text!r}")
        return False
    print(f"[validation][passed] location validated: {text!r}")
    return True


def _normalize_tyre_input(value):
    text = str(value or "").strip().lower()
    if not text:
        return None
    # accept forms like '12 tyre', '12', '12-tyre'
    m = re.search(r"\b(12|14|16)\b", text)
    if not m:
        return None
    code = m.group(1)
    return code


def _validate_tyre_type(value):
    code = _normalize_tyre_input(value)
    if not code:
        print(f"[validation][failed] tyre_type invalid: {value!r}")
        return False
    print(f"[validation][passed] tyre_type validated: {code}")
    return True


def _increment_validation_attempt(user_state, field_key):
    attempts = user_state.setdefault("validation_attempts", {})
    count = int(attempts.get(field_key, 0)) + 1
    attempts[field_key] = count
    print(f"[validation][attempt] field={field_key} user={user_state.get('user_id')} count={count}")
    return count


def _reset_validation_attempts_for_field(user_state, field_key):
    attempts = user_state.get("validation_attempts", {})
    if field_key in attempts:
        attempts.pop(field_key, None)


def _clear_booking_field_on_max_retry(user_state, field_key):
    # Clear specific booking field and reset stage
    booking = user_state.setdefault("booking", dict(EMPTY_BOOKING_JSON))
    if field_key in booking:
        booking[field_key] = ""
    user_state["booking"] = booking
    user_state["requested_field"] = ""
    user_state["stage"] = "idle"
    _reset_validation_attempts_for_field(user_state, field_key)
    print(f"[validation][max_retry] field={field_key} user={user_state.get('user_id')} - booking reset")


def _build_payment_summary(distance_km, truck_type_label, price_value, booking_details=None):
    total_price = int(round(float(price_value)))
    token_amount = int(round(total_price * 0.8))

    print(f"[booking][tyre_recommendation] summary_prepared truck_type={truck_type_label} distance={int(round(float(distance_km)))}km price={total_price}")

    if booking_details:
        conversational_summary = _build_conversational_booking_summary(
            booking_details, distance_km, price_value, truck_type_label
        )
        if conversational_summary:
            return f"{conversational_summary}\n\nWould you like to continue to payment?"

    return (
        f"Estimated Distance: {int(round(float(distance_km)))} km\n"
        f"Recommended Truck: {truck_type_label}\n"
        f"Estimated Freight Cost: ₹{total_price}\n"
        f"Advance Token Amount (80%): ₹{token_amount}\n\n"
        "Would you like to continue to payment?"
    )


def _build_shipment_live_tracking_payload(shipment_row):
    shipment = _serialize_shipment_row(shipment_row)
    if not shipment:
        return None

    live_location = None
    lorry_number = str(shipment.get("lorry_number") or "").strip()
    if lorry_number:
        try:
            live_location = get_lorry_tracking_data(lorry_number)
        except RuntimeError:
            live_location = None

    pickup_location = str(shipment.get("pickup_location") or "").strip()
    drop_location = str(shipment.get("drop_location") or "").strip()
    truck_type_code = _normalize_truck_type(shipment.get("truck_type"), shipment.get("weight"))
    route_coordinates = []
    distance_km = None
    eta_hours = None
    route_source = "planned"

    origin_coordinates = None
    destination_coordinates = None

    if pickup_location:
        try:
            origin_coordinates = _get_coordinates(pickup_location)
        except Exception:
            origin_coordinates = None

    if drop_location:
        try:
            destination_coordinates = _get_coordinates(drop_location)
        except Exception:
            destination_coordinates = None

    if live_location and live_location.get("latitude") is not None and live_location.get("longitude") is not None and destination_coordinates and _is_lat_lng_tuple(destination_coordinates):
        route_info = _get_route_geometry(
            live_location["latitude"],
            live_location["longitude"],
            destination_coordinates[0],
            destination_coordinates[1],
        )
        distance_km = float(route_info.get("distance_km") or 0)
        route_coordinates = route_info.get("coordinates") or []
        eta_hours = _estimate_eta_hours(distance_km, truck_type_code or "12")
        route_source = route_info.get("source") or "live"
    elif origin_coordinates and destination_coordinates and _is_lat_lng_tuple(origin_coordinates) and _is_lat_lng_tuple(destination_coordinates):
        route_info = _get_route_geometry(
            origin_coordinates[0],
            origin_coordinates[1],
            destination_coordinates[0],
            destination_coordinates[1],
        )
        distance_km = float(route_info.get("distance_km") or 0)
        route_coordinates = route_info.get("coordinates") or []
        eta_hours = _estimate_eta_hours(distance_km, truck_type_code or "12")
        route_source = route_info.get("source") or "planned"

    if eta_hours is None and shipment.get("distance_km") is not None:
        try:
            eta_hours = _estimate_eta_hours(float(shipment.get("distance_km") or 0), truck_type_code or "12")
        except Exception:
            eta_hours = None

    return {
        "shipment": shipment,
        "live_location": live_location,
        "latest_location": live_location,
        "route_coordinates": route_coordinates,
        "route_source": route_source,
        "distance_km": round(float(distance_km), 1) if distance_km is not None else shipment.get("distance_km"),
        "eta_hours": eta_hours,
        "gps_available": bool(live_location and live_location.get("latitude") is not None and live_location.get("longitude") is not None),
        "timeline": _fetch_shipment_timeline(shipment.get("id")),
        "summary": {
            "status": shipment.get("shipment_status") or "pending",
            "pickup_location": pickup_location,
            "drop_location": drop_location,
            "truck_type": shipment.get("truck_type") or "",
            "last_updated": live_location.get("last_updated") if live_location else shipment.get("created_at"),
        },
    }


def _advance_request_flow(user_state):
    booking = user_state.get("booking", dict(EMPTY_BOOKING_JSON))
    missing_fields = _missing_booking_fields(booking)

    if missing_fields:
        next_field = missing_fields[0]
        user_state["requested_field"] = next_field
        user_state["stage"] = "collecting_source" if next_field == "source" else "collecting_destination"
        return jsonify({"reply": _request_prompt_for_field(next_field)})

    user_state["requested_field"] = ""
    user_state["stage"] = "collecting_exact_pickup"
    return jsonify({"reply": "Please provide the exact pickup location."})


def _handle_booking_stage(user_state, raw_message, data, db_context=None):
    stage = str(user_state.get("stage", "idle") or "idle")
    booking = user_state.setdefault("booking", dict(EMPTY_BOOKING_JSON))
    _log_chat_state(user_state, note=f"booking_stage:{stage}")
    
    # Log booking stage entry
    print(f"[booking][stage] user={user_state.get('user_id')} stage={stage} message_len={len(raw_message)}")

    short_answers = {"yes", "y", "no", "n", "ok", "okay", "continue", "proceed", "cancel", "stop"}

    # STEP 1: Use Gemini to detect user intent with full booking context
    booking_context = {
        "source": booking.get("source") or "",
        "destination": booking.get("destination") or "",
        "tons": booking.get("tons") or "",
        "tyre_type": booking.get("truck_type") or "",
        "distance": user_state.get("quote", {}).get("distance") or "",
        "price": user_state.get("quote", {}).get("price") or "",
        "stage": stage,
    }
    intent_result = _detect_user_intent(raw_message, stage, booking_context, db_context=db_context)
    intent = _public_intent_to_internal(intent_result.get("intent"), stage)
    public_intent = _normalize_public_intent(intent_result.get("intent"))
    
    # STEP 2: Handle explicit cancellations during payment confirmation
    if stage == "awaiting_payment_confirmation":
        if intent == "cancel_booking" or is_negative_confirmation(raw_message):
            print(f"[booking][stage] user={user_state.get('user_id')} stage={stage} action=cancel_explicit")
            user_data.pop(user_state.get("user_id", "default_user"), None)
            return jsonify({"reply": "Booking cancelled. You can start a new transport request anytime."})
        
        if intent == "continue_booking" or is_positive_confirmation(raw_message):
            print(f"[booking][stage] user={user_state.get('user_id')} stage={stage} action=payment_confirmed")
            quote = user_state.get("quote", {})
            booking["truck_type"] = quote.get("truck_type") or _canonical_truck_type_label(_truck_type_from_tons(booking.get("tons", "")))
            booking["exact_pickup"] = str(booking.get("exact_pickup", "") or "").strip()
            booking["exact_delivery"] = str(booking.get("exact_delivery", "") or "").strip()

            final_data = {
                "distance": quote.get("distance"),
                "price": quote.get("price"),
            }

            try:
                response = _handle_direct_booking(user_state, raw_message, final_data, booking)
                if isinstance(response, tuple):
                    return response

                user_state["stage"] = "booking_confirmed"
                return response
            except Exception as error:
                logger.exception(f"[booking][error] Booking could not be completed: {error}")
                return jsonify({"reply": f"Booking could not be completed: {error}", "error": str(error)}), 500
        
        # Intent is an info/question query during payment confirmation - answer conversationally
        if intent in {"info_query", "pricing_question", "booking_question", "tracking_question"} or public_intent in {"GET_PRICE_ESTIMATE", "TRACK_SHIPMENT", "CUSTOMER_SUPPORT"}:
            contextual_reply = _generate_contextual_reply(raw_message, stage, booking, db_context=db_context)
            if contextual_reply:
                print(f"[booking][stage] user={user_state.get('user_id')} stage={stage} action=contextual_reply intent={intent}")
                return jsonify({"reply": contextual_reply})
        
        # Otherwise ask for yes/no clarification
        return jsonify({"reply": "Please reply with yes to continue to payment or no to cancel."})

    # If we're asking for exact pickup/delivery, short yes/no replies are invalid (unless they want to cancel)
    if stage in {"collecting_exact_pickup", "collecting_exact_delivery"}:
        if intent == "cancel_booking":
            print(f"[booking][stage] user={user_state.get('user_id')} stage={stage} action=cancel_explicit")
            reset_session(user_state=user_state)
            return jsonify({"reply": "Booking cancelled. You can start a new transport request anytime."})
        
        if raw_message.strip().lower() in short_answers and intent not in {"info_query", "pricing_question", "booking_question", "tracking_question"}:
            return jsonify({"reply": "Please provide an address or place name for the location (not a yes/no)."})

    if stage == "collecting_destination":
        requested_field = str(user_state.get("requested_field", "") or "")

        if requested_field == "tons":
            if not _validate_tons(raw_message):
                count = _increment_validation_attempt(user_state, "tons")
                if count >= 5:
                    _clear_booking_field_on_max_retry(user_state, "tons")
                    return jsonify({"reply": "Too many invalid attempts for tons. Booking has been reset. You can start again."})

                if intent in {"info_query", "pricing_question", "booking_question", "tracking_question"} or public_intent in {"GET_PRICE_ESTIMATE", "TRACK_SHIPMENT", "CUSTOMER_SUPPORT"}:
                    contextual_reply = _generate_contextual_reply(raw_message, stage, booking, db_context=db_context)
                    if contextual_reply:
                        print(f"[booking][stage] user={user_state.get('user_id')} stage={stage} action=contextual_reply intent={intent}")
                        return jsonify({"reply": contextual_reply})

                if count >= 3:
                    return jsonify({"reply": "Invalid tons. Example: 12 (must be between 1 and 100). You can type 'restart' to start over."})
                return jsonify({"reply": "Invalid tons. Please enter a numeric tons/load weight between 1 and 100."})

            booking["tons"] = str(int(re.search(r"\d+", raw_message.replace(",", "")).group(0)))
            _reset_validation_attempts_for_field(user_state, "tons")
        else:
            if not _validate_city(raw_message):
                count = _increment_validation_attempt(user_state, "destination")
                if count >= 5:
                    _clear_booking_field_on_max_retry(user_state, "destination")
                    return jsonify({"reply": "Too many invalid attempts for destination city. Booking has been reset. You can start again."})

                if intent in {"info_query", "pricing_question", "booking_question", "tracking_question"} or public_intent in {"GET_PRICE_ESTIMATE", "TRACK_SHIPMENT", "CUSTOMER_SUPPORT"}:
                    contextual_reply = _generate_contextual_reply(raw_message, stage, booking, db_context=db_context)
                    if contextual_reply:
                        print(f"[booking][stage] user={user_state.get('user_id')} stage={stage} action=contextual_reply intent={intent}")
                        return jsonify({"reply": contextual_reply})

                if count >= 3:
                    return jsonify({"reply": "Invalid destination city. Example: 'Bangalore'. You can type 'restart' to start over."})
                return jsonify({"reply": "Invalid destination city. Please provide only alphabetic city or location name (e.g., 'Bangalore')."})

            booking["destination"] = _normalize_booking_text(raw_message)
            _reset_validation_attempts_for_field(user_state, "destination")

            if not str(booking.get("tons", "") or "").strip():
                user_state["booking"] = booking
                user_state["requested_field"] = "tons"
                return jsonify({"reply": _request_prompt_for_field("tons")})

        user_state["booking"] = booking
        return _advance_request_flow(user_state)

    if stage == "collecting_exact_pickup":
        if not _validate_location_text(raw_message):
            count = _increment_validation_attempt(user_state, "exact_pickup")
            if count >= 5:
                _clear_booking_field_on_max_retry(user_state, "exact_pickup")
                return jsonify({"reply": "Too many invalid attempts for pickup location. Booking has been reset. You can start again."})
            
            # If it's a question, answer it conversationally
            if intent in {"info_query", "pricing_question", "booking_question", "tracking_question"} or public_intent in {"GET_PRICE_ESTIMATE", "TRACK_SHIPMENT", "CUSTOMER_SUPPORT"}:
                contextual_reply = _generate_contextual_reply(raw_message, stage, booking, db_context=db_context)
                if contextual_reply:
                    print(f"[booking][stage] user={user_state.get('user_id')} stage={stage} action=contextual_reply intent={intent}")
                    return jsonify({"reply": contextual_reply})
            
            if count >= 3:
                return jsonify({"reply": "Please provide a more detailed pickup location. Example: 'Near ABC Warehouse, Road 10'. You can type 'restart' to start over."})
            return jsonify({"reply": "Please provide a more detailed pickup location (at least 5 characters)."})

        booking["exact_pickup"] = _normalize_booking_text(raw_message)
        user_state["booking"] = booking
        _set_stage(user_state, "collecting_exact_delivery")
        _reset_validation_attempts_for_field(user_state, "exact_pickup")
        print(f"[validation][passed] exact_pickup set for user={user_state.get('user_id')}")
        return jsonify({"reply": "Please provide the exact delivery location."})

    if stage == "collecting_exact_delivery":
        if not _validate_location_text(raw_message):
            count = _increment_validation_attempt(user_state, "exact_delivery")
            if count >= 5:
                _clear_booking_field_on_max_retry(user_state, "exact_delivery")
                return jsonify({"reply": "Too many invalid attempts for delivery location. Booking has been reset. You can start again."})
            
            # If it's a question, answer it conversationally
            if intent in {"info_query", "pricing_question", "booking_question", "tracking_question"}:
                contextual_reply = _generate_contextual_reply(raw_message, stage, booking, db_context=db_context)
                if contextual_reply:
                    print(f"[booking][stage] user={user_state.get('user_id')} stage={stage} action=contextual_reply intent={intent}")
                    return jsonify({"reply": contextual_reply})
            
            if count >= 3:
                return jsonify({"reply": "Please provide a more detailed delivery location. Example: 'Opposite XYZ Warehouse, Sector 7'. You can type 'restart' to start over."})
            return jsonify({"reply": "Please provide a more detailed delivery location (at least 5 characters)."})

        booking["exact_delivery"] = _normalize_booking_text(raw_message)
        user_state["booking"] = booking
        _reset_validation_attempts_for_field(user_state, "exact_delivery")

        exact_pickup = str(booking.get("exact_pickup", "") or "").strip()
        exact_delivery = str(booking.get("exact_delivery", "") or "").strip()
        tons_text = str(booking.get("tons", "") or "").strip()

        if not exact_pickup or not exact_delivery or not tons_text:
            reset_session(user_state=user_state)
            return jsonify({"error": "Exact locations and tons are required"}), 400

        try:
            truck_type_code = _truck_type_from_tons(tons_text)
            if not truck_type_code:
                reset_session(user_state=user_state)
                return jsonify({"error": "No lorry available for given tons"}), 400

            truck_type_label = _canonical_truck_type_label(truck_type_code)
            print(f"[booking][stage] user={user_state.get('user_id')} stage={stage} action=price_calculated truck_type={truck_type_label}")
            distance_int, price_int = _estimate_distance_and_price(exact_pickup, exact_delivery, truck_type_code)
        except Exception as error:
            logger.exception(f"[distance][error] Distance could not be calculated: {error}")
            reset_session(user_state=user_state)
            status_code = 400 if "Coordinates are malformed" in str(error) else 500
            return jsonify({"reply": f"Distance could not be calculated: {error}", "error": str(error)}), status_code

        user_state["quote"] = {
            "distance": int(round(distance_int)),
            "price": int(round(price_int)),
            "truck_type": truck_type_label,
            "token_amount": int(round(price_int * 0.8)),
        }
        _set_stage(user_state, "awaiting_payment_confirmation")

        return jsonify({
            "reply": _build_payment_summary(distance_int, truck_type_label, price_int, booking),
            "type": "booking_summary_card",
            "distance": int(round(distance_int)),
            "price": int(round(price_int)),
            "lorryType": truck_type_label,
            "token_amount": int(round(price_int * 0.8)),
            "data": {
                "source": source_location,
                "destination": destination_location,
                "distance": int(round(distance_int)),
                "price": int(round(price_int)),
                "truck_type": truck_type_label,
                "token_amount": int(round(price_int * 0.8)),
                "booking": booking,
            },
        })

    return None


def _handle_direct_booking(user_state, raw_message, data, booking_details):
    try:
        user_id = str((user_state or {}).get("user_id") or "default_user")
        session_id = str((user_state or {}).get("session_id") or "").strip()
        source_location = str(
            booking_details.get("exact_pickup")
            or booking_details.get("source")
            or ""
        ).strip()
        destination_location = str(
            booking_details.get("exact_delivery")
            or booking_details.get("destination")
            or ""
        ).strip()
        tons_text = str(booking_details.get("tons", "") or "").strip()
        if not source_location or not destination_location or not tons_text:
            raise ValueError("Booking payload is incomplete")

        tons_value = float(tons_text)
        if not tons_value.is_integer():
            raise ValueError("Tons must be an integer")

        tons_int = int(tons_value)
        truck_type = _normalize_truck_type("", tons_int)

        try:
            distance_value = data.get("distance")
            price_value = data.get("price")

            if distance_value is None or price_value is None:
                distance_int, price_int = _estimate_distance_and_price(source_location, destination_location, truck_type)
            else:
                distance_int = int(round(float(distance_value)))
                price_int = int(round(float(price_value)))

            if not (distance_int == distance_int) or not (price_int == price_int):
                raise ValueError("invalid number")
        except Exception:
            distance_int, price_int = _estimate_distance_and_price(source_location, destination_location, truck_type)

        selected_lorry = _get_available_lorry_for_tyre_type(truck_type, tons_int)
        assignment = _generate_driver_truck_assignment()

        _insert_booking(
            user_id,
            {
                **booking_details,
                "source_location": source_location,
                "destination_location": destination_location,
                "tyre_type": _canonical_truck_type_label(truck_type),
                "exact_pickup_location": booking_details.get("exact_pickup") or source_location,
                "exact_delivery_location": booking_details.get("exact_delivery") or destination_location,
            },
            distance_int,
            price_int,
            selected_lorry["lorry_number"],
            token_amount=int(round(price_int * 0.8)),
        )

        if isinstance(user_state, dict):
            user_state["stage"] = "booking_confirmed"
            user_state["booking_mode"] = False
            user_state["shipment_id"] = user_state.get("shipment_id")
            try:
                booking_workflow_manager.save(
                    session_id=session_id or user_state.get("session_id") or f"session_{user_id}",
                    user_id=user_id,
                    workflow="shipment_booking",
                    current_step="booking_confirmed",
                    draft_json={
                        **booking_details,
                        "source_location": source_location,
                        "destination_location": destination_location,
                        "tyre_type": _canonical_truck_type_label(truck_type),
                        "exact_pickup_location": booking_details.get("exact_pickup") or source_location,
                        "exact_delivery_location": booking_details.get("exact_delivery") or destination_location,
                        "distance": distance_int,
                        "price": price_int,
                        "lorry_number": selected_lorry["lorry_number"],
                    },
                    quote_json={
                        "distance": distance_int,
                        "price": price_int,
                        "truck_type": _canonical_truck_type_label(truck_type),
                    },
                    payment_json=user_state.get("payment", {}),
                    status="confirmed",
                    last_user_message=str(raw_message or ""),
                    last_bot_reply="Booking confirmed",
                )
            except Exception as error:
                print(f"[booking_session][error] Could not persist confirmed booking: {error}")

        user_data.pop(user_id, None)

        return jsonify(
            {
                "status": "confirmed",
                "type": "booking_summary_card",
                "lorryType": _canonical_truck_type_label(truck_type),
                "distance": distance_int,
                "price": price_int,
                "lorry_number": selected_lorry["lorry_number"],
                "reply": _build_booking_confirmation(selected_lorry["lorry_number"], assignment=assignment),
                "data": {
                    "booking": {
                        **booking_details,
                        "source_location": source_location,
                        "destination_location": destination_location,
                        "truck_type": _canonical_truck_type_label(truck_type),
                        "distance": distance_int,
                        "price": price_int,
                        "lorry_number": selected_lorry["lorry_number"],
                    },
                    "assignment": selected_lorry,
                    "driver": assignment,
                },
            }
        )
    except (RuntimeError, ValueError) as error:
        message = str(error)
        reset_session(user_id=user_id)
        status_code = 400 if "Coordinates are malformed" in message else 500
        return jsonify({"error": message, "reply": message}), status_code


def _build_booking_confirmation(lorry_number, assignment=None):
    print(f"[booking][tyre_recommendation] booking_confirmed lorry={lorry_number}")
    
    # Generate driver/truck assignment
    assignment = assignment or _generate_driver_truck_assignment()
    
    confirmation_msg = f"✅ Booking confirmed!\n\n"
    confirmation_msg += f"Assigned Truck: {lorry_number}\n"
    confirmation_msg += f"Driver: {assignment['driver_name']}\n"
    confirmation_msg += f"Truck: {assignment['truck_number']}\n"
    confirmation_msg += f"ETA: ~{assignment['eta_mins']} mins\n\n"
    confirmation_msg += f"{assignment['message']}"
    
    return confirmation_msg


@app.route("/lorries/12", methods=["GET"])
def get_12_tyre_lorries():
    try:
        return jsonify({"status": "success", "data": fetch_available_12_tyre_lorries()})
    except RuntimeError as error:
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/lorries/14", methods=["GET"])
def get_14_tyre_lorries():
    try:
        return jsonify({"status": "success", "data": fetch_available_14_tyre_lorries()})
    except RuntimeError as error:
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/lorries/16", methods=["GET"])
def get_16_tyre_lorries():
    try:
        return jsonify({"status": "success", "data": fetch_available_16_tyre_lorries()})
    except RuntimeError as error:
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/track/<lorry_number>", methods=["GET"])
def track_lorry(lorry_number):
    try:
        tracking_data = get_lorry_tracking_data(lorry_number)

        if tracking_data.get("message"):
            return jsonify({"status": "unavailable", **tracking_data})

        return jsonify({"status": "success", **tracking_data})
    except ValueError as error:
        return jsonify({"status": "error", "message": str(error)}), 400
    except RuntimeError as error:
        return jsonify({"status": "error", "message": str(error)}), 404


@app.route("/gps/fleet", methods=["GET"])
@require_auth
def gps_fleet():
    try:
        fleet_rows = _get_booking_fleet_rows()
        trucks = []
        for row in fleet_rows:
            truck_payload = _build_fleet_tracking_payload(row)
            if truck_payload is not None:
                trucks.append(truck_payload)

        return jsonify(
            {
                "status": "success",
                "count": len(trucks),
                "trucks": trucks,
            }
        )
    except RuntimeError as error:
        logger.exception(f"[gps][fleet][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/gps/trucks/<lorry_number>", methods=["GET"])
@require_auth
def gps_truck(lorry_number):
    try:
        fleet_rows = _get_booking_fleet_rows()
        for row in fleet_rows:
            if str(row.get("lorry_number") or "").strip() != str(lorry_number).strip():
                continue

            truck_payload = _build_fleet_tracking_payload(row)
            if truck_payload is None:
                break

            return jsonify({"status": "success", "truck": truck_payload})

        return jsonify({"status": "error", "message": "Truck not found"}), 404
    except RuntimeError as error:
        logger.exception(f"[gps][truck][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/gps/logs/<lorry_number>", methods=["GET"])
@require_auth
def gps_logs(lorry_number):
    try:
        limit = request.args.get("limit", 25)
        try:
            limit_value = max(1, min(int(limit), 100))
        except (TypeError, ValueError):
            limit_value = 25

        logs = _get_gps_logs_for_lorry(lorry_number, limit=limit_value)
        return jsonify({"status": "success", "count": len(logs), "logs": logs})
    except RuntimeError as error:
        logger.exception(f"[gps][logs][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/gps/ingest", methods=["POST"])
def gps_ingest():
    """Ingest a single GPS point from devices or third-party GPS APIs.

    Expected JSON body:
    {
        "lorry_number": "VEH-12-001",
        "latitude": 17.385,
        "longitude": 78.4867,
        "booking_id": 123,            # optional
        "source_location": "Hyderabad", # optional
        "destination_location": "Bangalore" # optional
    }

    The endpoint stores the point in `gps_logs`, updates vehicle current position,
    and emits a live socket update to connected dashboards.
    """
    try:
        payload = request.get_json(silent=True) or {}

        # Authentication: allow either a JWT for a driver or an API key (for devices)
        api_key_header = request.headers.get("X-API-KEY") or request.args.get("api_key") or payload.get("api_key")
        jwt_ok = False
        try:
            # If an Authorization header with Bearer token is present, verify it
            auth_header = request.headers.get("Authorization") or ""
            if auth_header.startswith("Bearer "):
                verify_jwt_in_request()
                claims = get_jwt()
                if str(claims.get("role") or "").lower() == "driver":
                    jwt_ok = True
        except Exception:
            # invalid token
            return jsonify({"status": "error", "message": "Invalid authentication token"}), 401

        if not jwt_ok:
            # fallback to API key validation
            key_row = _validate_api_key(api_key_header)
            if not key_row:
                return jsonify({"status": "error", "message": "Missing or invalid API key"}), 401
        lorry_number = str(payload.get("lorry_number") or payload.get("vehicle_number") or "").strip()
        if not lorry_number:
            return jsonify({"status": "error", "message": "Missing lorry_number"}), 400

        try:
            latitude = float(payload.get("latitude"))
            longitude = float(payload.get("longitude"))
        except Exception:
            return jsonify({"status": "error", "message": "Invalid latitude/longitude"}), 400

        booking_id = payload.get("booking_id")
        source_loc = str(payload.get("source_location") or payload.get("source") or "").strip()
        dest_loc = str(payload.get("destination_location") or payload.get("destination") or "").strip()

        # Persist GPS point
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO gps_logs (booking_id, lorry_number, latitude, longitude, source_location, destination_location, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
                """,
                (
                    int(booking_id) if booking_id is not None else None,
                    lorry_number,
                    latitude,
                    longitude,
                    source_loc or None,
                    dest_loc or None,
                ),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()

        # Update vehicles table (best-effort) so fleet queries reflect latest position
        try:
            conn2 = get_db_connection()
            cur2 = conn2.cursor()
            try:
                cur2.execute(
                    "UPDATE vehicles SET current_latitude = %s, current_longitude = %s, updated_at = NOW() WHERE vehicle_code = %s",
                    (latitude, longitude, lorry_number),
                )
                conn2.commit()
            finally:
                cur2.close()
                conn2.close()
        except Exception:
            # Non-fatal if vehicles table doesn't contain this lorry
            pass

        # Emit a lightweight per-truck update and broadcast full fleet update
        try:
            socketio.emit("truck:location", {"lorry_number": lorry_number, "latitude": latitude, "longitude": longitude})
        except Exception:
            logger.exception("[socketio][truck:location] emit failed")

        tracked_bookings = _find_booking_rows_by_lorry_number(lorry_number)
        for booking_row in tracked_bookings:
            try:
                _broadcast_tracking_snapshot(booking_row=booking_row, source="gps.ingest", event_name="tracking:update", status_note="Live GPS movement")
                _broadcast_realtime_activity(
                    "tracking",
                    "movement",
                    {
                        "booking": _serialize_booking_row(booking_row),
                        "lorry_number": lorry_number,
                        "latitude": latitude,
                        "longitude": longitude,
                    },
                    event_name="shipment:activity",
                )
            except Exception as error:
                logger.warning(f"[socketio][gps:tracking][warning] {error}")

        # Trigger full fleet update (reads DB and computes routes/ETAs)
        try:
            _broadcast_live_fleet_update()
        except Exception:
            logger.exception("[gps][ingest] failed to broadcast fleet update")

        return jsonify({"status": "success", "message": "GPS point ingested"})
    except Exception as error:
        logger.exception(f"[gps][ingest][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/gps/playback/<int:booking_id>", methods=["GET"])
@require_auth
def gps_playback(booking_id):
    try:
        start = request.args.get("start")
        end = request.args.get("end")
        limit = request.args.get("limit", 100)
        try:
            limit_value = max(1, min(int(limit), 1000))
        except Exception:
            limit_value = 100

        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        try:
            sql = "SELECT * FROM gps_logs WHERE booking_id = %s"
            params = [int(booking_id)]
            if start:
                sql += " AND created_at >= %s"
                params.append(start)
            if end:
                sql += " AND created_at <= %s"
                params.append(end)
            sql += " ORDER BY created_at ASC LIMIT %s"
            params.append(limit_value)
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        finally:
            cur.close()
            conn.close()

        logs = []
        for row in rows:
            try:
                logs.append({
                    "latitude": float(row.get("latitude")),
                    "longitude": float(row.get("longitude")),
                    "created_at": row.get("created_at").isoformat(sep=" ", timespec="seconds") if row.get("created_at") else None,
                })
            except Exception:
                continue

        return jsonify({"status": "success", "count": len(logs), "logs": logs})
    except Exception as error:
        logger.exception(f"[gps][playback][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/gps/eta/<int:booking_id>", methods=["GET"])
@require_auth
def gps_eta(booking_id):
    try:
        # Find latest gps log for booking
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT * FROM gps_logs WHERE booking_id = %s ORDER BY created_at DESC LIMIT 1", (int(booking_id),))
            row = cur.fetchone()
        finally:
            cur.close()
            conn.close()

        if not row:
            return jsonify({"status": "error", "message": "No GPS logs found for booking"}), 404

        # Determine destination coordinates from booking
        conn2 = get_db_connection()
        cur2 = conn2.cursor(dictionary=True)
        try:
            cur2.execute("SELECT destination_location, source_location, lorry_number FROM bookings WHERE id = %s LIMIT 1", (int(booking_id),))
            booking_row = cur2.fetchone()
        finally:
            cur2.close()
            conn2.close()

        if not booking_row:
            return jsonify({"status": "error", "message": "Booking not found"}), 404

        dest = booking_row.get("destination_location") or booking_row.get("drop_location") or booking_row.get("destination")
        if not dest:
            return jsonify({"status": "error", "message": "No destination for booking"}), 400

        # Use route geometry to compute remaining distance
        lat = float(row.get("latitude"))
        lon = float(row.get("longitude"))
        dest_lat, dest_lon = _get_coordinates(dest)
        if dest_lat is None:
            return jsonify({"status": "error", "message": "Unable to geocode destination"}), 400

        route = _get_route_geometry(lat, lon, dest_lat, dest_lon)
        distance_km = float(route.get("distance_km") or 0)

        # Estimate ETA using truck type from booking if available
        truck_type = booking_row.get("tyre_type") or booking_row.get("truck_type") or "12"
        eta_hours = _estimate_eta_hours(distance_km, truck_type)

        return jsonify({"status": "success", "distance_km": distance_km, "eta_hours": eta_hours, "route": route})
    except Exception as error:
        logger.exception(f"[gps][eta][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500
    except Exception as error:
        logger.exception(f"[gps][ingest][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/auth/register", methods=["POST"])
@app.route("/auth/register", methods=["POST"])
def auth_register():
    try:
        payload = request.get_json(silent=True) or {}
        full_name = _normalize_auth_text(payload.get("full_name") or payload.get("fullName"))
        email = _normalize_email(payload.get("email"))
        phone = _normalize_phone_number(payload.get("phone"))
        password = str(payload.get("password") or "")
        role = _normalize_role(payload.get("role"))

        if len(full_name) < 3:
            raise ValueError("Full name must be at least 3 characters long")

        if _get_user_by_email(email):
            return jsonify({"status": "error", "message": "Email is already registered"}), 409

        created_id = _create_user(full_name, email, phone, password, role)
        user_row = _get_user_by_id(created_id)
        if not user_row:
            return jsonify({"status": "error", "message": "Unable to create user"}), 500

        access_token = _build_access_token_for_user(user_row)
        response = jsonify(
            {
                "status": "success",
                "message": "Account created successfully",
                **_auth_response_payload(user_row),
            }
        )
        set_access_cookies(response, access_token)
        return response, 201
    except ValueError as error:
        return jsonify({"status": "error", "message": str(error)}), 400
    except RuntimeError as error:
        logger.exception(f"[auth][register][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/auth/driver/login", methods=["POST"])
def driver_login():
    try:
        payload = request.get_json(silent=True) or {}
        phone = _normalize_phone_number(payload.get("phone") or payload.get("mobile") or payload.get("phone_number"))
        password = str(payload.get("password") or "")

        if not phone or not password:
            return jsonify({"status": "error", "message": "Phone and password are required"}), 400

        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT * FROM drivers WHERE phone = %s LIMIT 1", (phone,))
            driver = cur.fetchone()
        finally:
            cur.close()
            conn.close()

        if not driver:
            return jsonify({"status": "error", "message": "Driver not found"}), 404

        stored_hash = driver.get("password_hash")
        if not stored_hash:
            return jsonify({"status": "error", "message": "Driver account has no password set"}), 403

        if not _verify_password(stored_hash, password):
            return jsonify({"status": "error", "message": "Invalid credentials"}), 401

        access_token = create_access_token(identity=str(driver.get("id")), additional_claims={"role": "driver", "phone": driver.get("phone")})

        return jsonify({"status": "success", "access_token": access_token, "driver": {"id": int(driver.get("id")), "driver_name": driver.get("driver_name"), "phone": driver.get("phone")}})
    except Exception as error:
        logger.exception(f"[auth][driver][login][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/auth/login", methods=["POST"])
@app.route("/auth/login", methods=["POST"])
def auth_login():
    try:
        payload = request.get_json(silent=True) or {}
        email = _normalize_email(payload.get("email"))
        password = str(payload.get("password") or "")

        user_row = _get_user_by_email(email)
        if not user_row or not _verify_password(user_row.get("password_hash"), password):
            return jsonify({"status": "error", "message": "Invalid email or password"}), 401

        access_token = _build_access_token_for_user(user_row)
        response = jsonify(
            {
                "status": "success",
                "message": "Login successful",
                **_auth_response_payload(user_row),
            }
        )
        set_access_cookies(response, access_token)
        return response
    except ValueError as error:
        return jsonify({"status": "error", "message": str(error)}), 400
    except RuntimeError as error:
        logger.exception(f"[auth][login][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/auth/profile", methods=["GET"])
@app.route("/auth/profile", methods=["GET"])
@jwt_required()
def auth_profile():
    try:
        user_id = get_jwt_identity()
        user_row = _get_user_by_id(user_id)
        if not user_row:
            return jsonify({"status": "error", "message": "User not found"}), 404

        return jsonify({"status": "success", **_auth_response_payload(user_row)})
    except RuntimeError as error:
        logger.exception(f"[auth][profile][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/auth/logout", methods=["POST"])
@app.route("/auth/logout", methods=["POST"])
@jwt_required()
def auth_logout():
    response = jsonify({"status": "success", "message": "Logged out successfully"})
    unset_jwt_cookies(response)
    return response


@limiter.limit("20 per minute")
@app.route("/notifications/whatsapp/live-location", methods=["POST"])
def notify_whatsapp_live_location():
    try:
        payload = request.get_json(silent=True) or {}
        booking_id = payload.get("booking_id") or payload.get("bookingId")
        lorry_number = payload.get("lorry_number") or payload.get("lorryNumber")

        booking_row = None
        if booking_id:
            booking_row = _fetch_booking_record_by_id(booking_id)

        if booking_row is None and lorry_number:
            booking_rows = _fetch_booking_records()
            booking_row = next(
                (row for row in booking_rows if str(row.get("lorry_number") or "").strip() == str(lorry_number).strip()),
                None,
            )

        if not booking_row:
            return jsonify({"status": "error", "message": "Booking not found"}), 404

        truck_number = str(lorry_number or booking_row.get("lorry_number") or "").strip()
        if not truck_number:
            return jsonify({"status": "error", "message": "No truck assigned for live location alerts"}), 400

        tracking_data = None
        try:
            tracking_data = get_lorry_tracking_data(truck_number)
        except Exception:
            tracking_data = None

        if not tracking_data or tracking_data.get("message"):
            return jsonify({"status": "error", "message": "Live GPS data is unavailable for this truck"}), 404

        sent = _send_live_location_whatsapp(booking_row, tracking_data)
        return jsonify({"status": "success", "sent": sent, "tracking": tracking_data})
    except RuntimeError as error:
        logger.exception(f"[whatsapp][live_location][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@limiter.limit("20 per minute")
@app.route("/notifications/whatsapp/delivery-update", methods=["POST"])
def notify_whatsapp_delivery_update():
    try:
        payload = request.get_json(silent=True) or {}
        booking_id = payload.get("booking_id") or payload.get("bookingId")
        status_text = str(payload.get("status") or payload.get("booking_status") or "Delivery update").strip()
        details = payload.get("details") or payload.get("message")

        if not booking_id:
            raise ValueError("booking_id is required")

        booking_row = _fetch_booking_record_by_id(booking_id)
        if not booking_row:
            return jsonify({"status": "error", "message": "Booking not found"}), 404

        sent = _send_delivery_whatsapp_update(booking_row, status_text, details)
        return jsonify({"status": "success", "sent": sent})
    except ValueError as error:
        return jsonify({"status": "error", "message": str(error)}), 400
    except RuntimeError as error:
        logger.exception(f"[whatsapp][delivery_update][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/admin/dashboard", methods=["GET"])
@app.route("/admin/dashboard", methods=["GET"])
@require_auth
@require_role("admin")
def admin_dashboard():
    try:
        booking_status = str(request.args.get("booking_status") or request.args.get("status") or "all").strip().lower() or "all"
        payment_status = str(request.args.get("payment_status") or "all").strip().lower() or "all"

        try:
            days = max(7, min(int(request.args.get("days", 30)), 90))
        except (TypeError, ValueError):
            days = 30

        payload = _build_admin_dashboard_payload(
            status_filter=booking_status,
            payment_filter=payment_status,
            days=days,
        )

        return jsonify({"status": "success", **payload})
    except RuntimeError as error:
        logger.exception(f"[admin][dashboard][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/admin/monitoring", methods=["GET"])
@app.route("/admin/monitoring", methods=["GET"])
@require_auth
@require_role("admin")
def admin_monitoring_dashboard():
    try:
        try:
            days = max(1, min(int(request.args.get("days", 30)), 90))
        except (TypeError, ValueError):
            days = 30

        payload = _build_monitoring_dashboard_payload(days=days)
        return jsonify({"status": "success", **payload})
    except RuntimeError as error:
        logger.exception(f"[admin][monitoring][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/metrics", methods=["GET"])
def prometheus_metrics():
    try:
        payload = _build_monitoring_dashboard_payload(days=30)
        response = make_response(payload.get("prometheus_metrics", ""), 200)
        response.headers["Content-Type"] = "text/plain; version=0.0.4; charset=utf-8"
        return response
    except RuntimeError as error:
        logger.exception(f"[metrics][error] {error}")
        return make_response("", 500)


@app.route("/api/admin/drivers/<int:driver_id>", methods=["PUT", "PATCH"])
@app.route("/admin/drivers/<int:driver_id>", methods=["PUT", "PATCH"])
@require_auth
@require_role("admin")
def update_admin_driver(driver_id):
    try:
        payload = request.get_json(silent=True) or {}
        updates = {}

        for key in ("status", "assigned_truck", "assigned_truck_type", "phone", "license_number"):
            if key in payload and payload.get(key) is not None:
                updates[key] = str(payload.get(key)).strip()

        if payload.get("rating") is not None:
            updates["rating"] = float(payload.get("rating"))

        if payload.get("experience_years") is not None:
            updates["experience_years"] = int(payload.get("experience_years"))

        if payload.get("next_available_at") is not None:
            updates["next_available_at"] = payload.get("next_available_at")

        _update_driver_record(driver_id, updates)

        driver_rows = _fetch_driver_records()
        updated_driver = next((row for row in driver_rows if int(row.get("id")) == int(driver_id)), None)
        if not updated_driver:
            return jsonify({"status": "error", "message": "Driver not found"}), 404

        return jsonify({"status": "success", "driver": _serialize_driver_row(updated_driver)})
    except ValueError as error:
        return jsonify({"status": "error", "message": str(error)}), 400
    except RuntimeError as error:
        logger.exception(f"[admin][driver_update][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/admin/trucks/<truck_type_code>/<vehicle_number>", methods=["PATCH"])
@require_auth
@require_role("admin")
def update_admin_truck(truck_type_code, vehicle_number):
    try:
        payload = request.get_json(silent=True) or {}
        availability_status = str(payload.get("availability_status") or payload.get("status") or "available").strip() or "available"
        _update_truck_availability(truck_type_code, vehicle_number, availability_status)

        table_name = TABLE_MAP.get(str(truck_type_code).strip())
        connection = None
        cursor = None
        try:
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                f"SELECT * FROM {table_name} WHERE vehicle_number = %s LIMIT 1",
                (str(vehicle_number).strip(),),
            )
            truck_row = cursor.fetchone()
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None and connection.is_connected():
                connection.close()

        if not truck_row:
            return jsonify({"status": "error", "message": "Truck not found"}), 404

        return jsonify({"status": "success", "truck": _serialize_truck_row(truck_row, f"{truck_type_code} tyre")})
    except ValueError as error:
        return jsonify({"status": "error", "message": str(error)}), 400
    except RuntimeError as error:
        logger.exception(f"[admin][truck_update][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/admin/drivers", methods=["GET"])
@app.route("/admin/drivers", methods=["GET"])
@require_auth
@require_role("admin")
def admin_drivers_list():
    try:
        drivers = [_serialize_driver_row(row) for row in _fetch_driver_records()]
        return jsonify({"status": "success", "count": len(drivers), "drivers": drivers})
    except RuntimeError as error:
        logger.exception(f"[admin][drivers][list][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/admin/drivers", methods=["POST"])
@app.route("/admin/drivers", methods=["POST"])
@require_auth
@require_role("admin")
def admin_drivers_create():
    try:
        payload = request.get_json(silent=True) or {}
        driver_name = _normalize_auth_text(payload.get("driver_name") or payload.get("name"))
        phone = _normalize_phone_number(payload.get("phone"))
        license_number = _normalize_auth_text(payload.get("license_number") or payload.get("licenseNumber"))
        password = str(payload.get("password") or "")

        if len(driver_name) < 3:
            raise ValueError("Driver name is required")
        if len(phone) < 8:
            raise ValueError("Driver phone is required")
        if len(license_number) < 4:
            raise ValueError("License number is required")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters long")

        created_id = _create_driver_record({
            "driver_name": driver_name,
            "phone": phone,
            "license_number": license_number,
            "assigned_truck": payload.get("assigned_truck") or payload.get("assignedTruck") or "",
            "assigned_truck_type": payload.get("assigned_truck_type") or payload.get("assignedTruckType") or "",
            "status": payload.get("status") or "available",
            "rating": payload.get("rating") or 0,
            "experience_years": payload.get("experience_years") or 0,
            "password": password,
            "next_available_at": payload.get("next_available_at"),
        })

        driver_row = _get_driver_record_by_id(created_id)
        return jsonify({"status": "success", "driver": _serialize_driver_row(driver_row)}), 201
    except ValueError as error:
        return jsonify({"status": "error", "message": str(error)}), 400
    except RuntimeError as error:
        logger.exception(f"[admin][drivers][create][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/admin/drivers/<int:driver_id>", methods=["DELETE"])
@app.route("/admin/drivers/<int:driver_id>", methods=["DELETE"])
@require_auth
@require_role("admin")
def admin_drivers_delete(driver_id):
    try:
        connection = None
        cursor = None
        try:
            connection = get_db_connection()
            cursor = connection.cursor()
            cursor.execute(f"DELETE FROM {DRIVERS_TABLE} WHERE id = %s", (int(driver_id),))
            connection.commit()
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None and connection.is_connected():
                connection.close()

        return jsonify({"status": "success", "message": "Driver deleted"})
    except RuntimeError as error:
        logger.exception(f"[admin][drivers][delete][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/admin/drivers/<int:driver_id>/generate-api-key", methods=["POST"])
@app.route("/admin/drivers/<int:driver_id>/generate-api-key", methods=["POST"])
@require_auth
@require_role("admin")
def admin_driver_generate_api_key(driver_id):
    try:
        api_key = _generate_secure_api_key("drv")
        _update_driver_auth_fields(driver_id, {"api_key": api_key})
        driver_row = _get_driver_record_by_id(driver_id)
        if not driver_row:
            return jsonify({"status": "error", "message": "Driver not found"}), 404

        return jsonify({"status": "success", "message": "API key generated", "driver": _serialize_driver_row(driver_row), "api_key": api_key})
    except RuntimeError as error:
        logger.exception(f"[admin][drivers][api_key][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/admin/drivers/<int:driver_id>/reset-password", methods=["POST"])
@app.route("/admin/drivers/<int:driver_id>/reset-password", methods=["POST"])
@require_auth
@require_role("admin")
def admin_driver_reset_password(driver_id):
    try:
        payload = request.get_json(silent=True) or {}
        new_password = str(payload.get("new_password") or payload.get("password") or "").strip()
        generated = False

        if not new_password:
            new_password = secrets.token_urlsafe(10)
            generated = True

        if len(new_password) < 8:
            raise ValueError("Password must be at least 8 characters long")

        _update_driver_auth_fields(driver_id, {"password_hash": _hash_password(new_password)})
        driver_row = _get_driver_record_by_id(driver_id)
        if not driver_row:
            return jsonify({"status": "error", "message": "Driver not found"}), 404

        return jsonify({"status": "success", "message": "Password reset", "driver": _serialize_driver_row(driver_row), "temporary_password": new_password if generated else None})
    except ValueError as error:
        return jsonify({"status": "error", "message": str(error)}), 400
    except RuntimeError as error:
        logger.exception(f"[admin][drivers][reset_password][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/admin/users", methods=["GET"])
@app.route("/admin/users", methods=["GET"])
@require_auth
@require_role("admin")
def admin_users_list():
    try:
        users = [_serialize_admin_user_row(row) for row in _fetch_admin_user_records()]
        return jsonify({"status": "success", "count": len(users), "users": users})
    except RuntimeError as error:
        logger.exception(f"[admin][users][list][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/admin/vehicles", methods=["GET"])
@app.route("/admin/vehicles", methods=["GET"])
@require_auth
@require_role("admin")
def admin_vehicles_list():
    try:
        vehicles = [_serialize_vehicle_row(row) for row in _fetch_vehicle_records()]
        return jsonify({"status": "success", "count": len(vehicles), "vehicles": vehicles})
    except RuntimeError as error:
        logger.exception(f"[admin][vehicles][list][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/admin/vehicles", methods=["POST"])
@app.route("/admin/vehicles", methods=["POST"])
@require_auth
@require_role("admin")
def admin_vehicles_create():
    try:
        payload = request.get_json(silent=True) or {}
        vehicle_code = str(payload.get("vehicle_code") or payload.get("vehicleCode") or "").strip()
        vehicle_name = str(payload.get("vehicle_name") or payload.get("vehicleName") or "").strip()
        truck_type = str(payload.get("truck_type") or payload.get("truckType") or "").strip()
        if not vehicle_code or not vehicle_name or not truck_type:
            return jsonify({"status": "error", "message": "vehicle_code, vehicle_name, and truck_type are required"}), 400

        current_booking_id = payload.get("current_booking_id") or payload.get("currentBookingId")
        if current_booking_id not in (None, ""):
            from db import validate_foreign_key_reference
            if not validate_foreign_key_reference("bookings", "id", int(current_booking_id)):
                return jsonify({"status": "error", "message": "Referenced booking does not exist"}), 400

        from db import save_audit_log, save_vehicle_record

        save_vehicle_record({
            "vehicle_code": vehicle_code,
            "vehicle_name": vehicle_name,
            "truck_type": truck_type,
            "max_load_tons": payload.get("max_load_tons") or payload.get("maxLoadTons") or 0,
            "rate_per_km": payload.get("rate_per_km") or payload.get("ratePerKm") or 0,
            "availability_status": payload.get("availability_status") or payload.get("availabilityStatus") or "available",
            "current_booking_id": current_booking_id,
            "current_latitude": payload.get("current_latitude") or payload.get("currentLatitude"),
            "current_longitude": payload.get("current_longitude") or payload.get("currentLongitude"),
            "features_json": payload.get("features_json") or payload.get("features") or [],
            "ai_notes": payload.get("ai_notes") or payload.get("aiNotes") or "",
        })

        save_audit_log(
            user_id=get_jwt_identity(),
            role=str(get_jwt().get("role") or "admin"),
            action="CREATE",
            entity_type="vehicle",
            entity_id=vehicle_code,
            details_json=payload,
            correlation_id=request.headers.get("X-Correlation-Id") or request.headers.get("X-Request-Id"),
        )

        created = _serialize_vehicle_row(get_vehicle_by_code(vehicle_code))
        return jsonify({"status": "success", "vehicle": created}), 201
    except ValueError as error:
        return jsonify({"status": "error", "message": str(error)}), 400
    except RuntimeError as error:
        logger.exception(f"[admin][vehicles][create][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/admin/vehicles/<string:vehicle_code>", methods=["PUT", "PATCH"])
@app.route("/admin/vehicles/<string:vehicle_code>", methods=["PUT", "PATCH"])
@require_auth
@require_role("admin")
def admin_vehicles_update(vehicle_code):
    try:
        payload = request.get_json(silent=True) or {}
        existing = get_vehicle_by_code(vehicle_code)
        if not existing:
            return jsonify({"status": "error", "message": "Vehicle not found"}), 404

        current_booking_id = payload.get("current_booking_id") or payload.get("currentBookingId")
        if current_booking_id not in (None, ""):
            from db import validate_foreign_key_reference
            if not validate_foreign_key_reference("bookings", "id", int(current_booking_id)):
                return jsonify({"status": "error", "message": "Referenced booking does not exist"}), 400

        from db import save_audit_log, save_vehicle_record

        next_vehicle = {
            "vehicle_code": vehicle_code,
            "vehicle_name": payload.get("vehicle_name") or payload.get("vehicleName") or existing.get("vehicle_name"),
            "truck_type": payload.get("truck_type") or payload.get("truckType") or existing.get("truck_type"),
            "max_load_tons": payload.get("max_load_tons") or payload.get("maxLoadTons") or existing.get("max_load_tons"),
            "rate_per_km": payload.get("rate_per_km") or payload.get("ratePerKm") or existing.get("rate_per_km"),
            "availability_status": payload.get("availability_status") or payload.get("availabilityStatus") or existing.get("availability_status"),
            "current_booking_id": current_booking_id if current_booking_id not in (None, "") else existing.get("current_booking_id"),
            "current_latitude": payload.get("current_latitude") or payload.get("currentLatitude") or existing.get("current_latitude"),
            "current_longitude": payload.get("current_longitude") or payload.get("currentLongitude") or existing.get("current_longitude"),
            "features_json": payload.get("features_json") or payload.get("features") or existing.get("features"),
            "ai_notes": payload.get("ai_notes") or payload.get("aiNotes") or existing.get("ai_notes"),
        }
        save_vehicle_record(next_vehicle)
        save_audit_log(
            user_id=get_jwt_identity(),
            role=str(get_jwt().get("role") or "admin"),
            action="UPDATE",
            entity_type="vehicle",
            entity_id=vehicle_code,
            details_json=payload,
            correlation_id=request.headers.get("X-Correlation-Id") or request.headers.get("X-Request-Id"),
        )
        updated = _serialize_vehicle_row(get_vehicle_by_code(vehicle_code))
        return jsonify({"status": "success", "vehicle": updated})
    except ValueError as error:
        return jsonify({"status": "error", "message": str(error)}), 400
    except RuntimeError as error:
        logger.exception(f"[admin][vehicles][update][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/admin/vehicles/<string:vehicle_code>", methods=["DELETE"])
@app.route("/admin/vehicles/<string:vehicle_code>", methods=["DELETE"])
@require_auth
@require_role("admin")
def admin_vehicles_delete(vehicle_code):
    try:
        from db import delete_vehicle_record, save_audit_log

        deleted = delete_vehicle_record(vehicle_code)
        if not deleted:
            return jsonify({"status": "error", "message": "Vehicle not found"}), 404

        save_audit_log(
            user_id=get_jwt_identity(),
            role=str(get_jwt().get("role") or "admin"),
            action="DELETE",
            entity_type="vehicle",
            entity_id=vehicle_code,
            details_json={"vehicle_code": vehicle_code},
            correlation_id=request.headers.get("X-Correlation-Id") or request.headers.get("X-Request-Id"),
        )
        return jsonify({"status": "success", "deleted": deleted})
    except RuntimeError as error:
        logger.exception(f"[admin][vehicles][delete][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/admin/audit-logs", methods=["GET"])
@require_auth
@require_role("admin")
def admin_audit_logs():
    try:
        try:
            page = max(1, int(request.args.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            per_page = max(1, min(200, int(request.args.get("per_page", 50))))
        except (TypeError, ValueError):
            per_page = 50

        filters = {}
        for key in ("user_id", "role", "action", "entity_type", "status", "correlation_id"):
            if request.args.get(key):
                filters[key] = request.args.get(key)

        from db import get_audit_logs

        result = get_audit_logs(page=page, per_page=per_page, filters=filters)
        return jsonify({"status": "success", **result})
    except RuntimeError as error:
        logger.exception(f"[admin][audit_logs][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/admin/audit-logs", methods=["DELETE"])
@require_auth
@require_role("admin")
def admin_audit_logs_delete():
    try:
        try:
            older_than_days = max(1, min(3650, int(request.args.get("older_than_days", request.args.get("days", 30)))))
        except (TypeError, ValueError):
            older_than_days = 30

        from db import delete_audit_logs_older_than, save_audit_log

        deleted = delete_audit_logs_older_than(older_than_days)
        save_audit_log(
            user_id=get_jwt_identity(),
            role=str(get_jwt().get("role") or "admin"),
            action="DELETE",
            entity_type="audit_log_retention",
            entity_id=str(older_than_days),
            details_json={"older_than_days": older_than_days, "deleted": deleted},
            correlation_id=request.headers.get("X-Correlation-Id") or request.headers.get("X-Request-Id"),
        )
        return jsonify({"status": "success", "deleted": deleted, "older_than_days": older_than_days})
    except RuntimeError as error:
        logger.exception(f"[admin][audit_logs][delete][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/admin/webhooks/events", methods=["GET"])
@require_auth
@require_role("admin")
def admin_webhook_events():
    try:
        try:
            page = max(1, int(request.args.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            per_page = max(1, min(200, int(request.args.get("per_page", 50))))
        except (TypeError, ValueError):
            per_page = 50

        filters = {}
        for key in ("source", "event_type", "status", "correlation_id"):
            if request.args.get(key):
                filters[key] = request.args.get(key)

        from db import get_webhook_events

        result = get_webhook_events(page=page, per_page=per_page, filters=filters)
        return jsonify({"status": "success", **result})
    except RuntimeError as error:
        logger.exception(f"[admin][webhooks][events][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/admin/webhooks/events/<int:event_id>/retry", methods=["PATCH"])
@require_auth
@require_role("admin")
def admin_webhook_event_retry(event_id):
    try:
        payload = request.get_json(silent=True) or {}
        response_code = payload.get("response_code") or payload.get("responseCode")

        from db import update_webhook_event_status, save_audit_log

        updated = update_webhook_event_status(event_id, payload.get("status") or "retrying", response_code=response_code)
        if not updated:
            return jsonify({"status": "error", "message": "Webhook event not found"}), 404

        save_audit_log(
            user_id=get_jwt_identity(),
            role=str(get_jwt().get("role") or "admin"),
            action="RETRY",
            entity_type="webhook_event",
            entity_id=str(event_id),
            details_json={"response_code": response_code, "status": payload.get("status") or "retrying"},
            correlation_id=request.headers.get("X-Correlation-Id") or request.headers.get("X-Request-Id"),
        )
        return jsonify({"status": "success", "event": updated})
    except RuntimeError as error:
        logger.exception(f"[admin][webhooks][retry][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/webhooks/events", methods=["POST"])
def webhook_event_ingest():
    try:
        payload = request.get_json(silent=True) or {}
        event_type = str(payload.get("event_type") or payload.get("eventType") or request.headers.get("X-Webhook-Event") or "generic_event").strip() or "generic_event"
        source = str(payload.get("source") or request.headers.get("X-Webhook-Source") or "unknown").strip() or "unknown"
        correlation_id = request.headers.get("X-Correlation-Id") or request.headers.get("X-Request-Id")
        verify_jwt_in_request(optional=True)
        user_identity = get_jwt_identity()
        jwt_data = get_jwt() if user_identity is not None else {}

        from db import save_audit_log, save_webhook_event

        event_id = save_webhook_event(
            source=source,
            event_type=event_type,
            payload_json=payload,
            headers_json=dict(request.headers),
            status="received",
            response_code=200,
            correlation_id=correlation_id,
        )
        save_audit_log(
            user_id=user_identity,
            role=str(jwt_data.get("role") or "system"),
            action="WEBHOOK_RECEIVE",
            entity_type="webhook_event",
            entity_id=event_id,
            details_json=payload,
            correlation_id=correlation_id,
        )
        return jsonify({"status": "success", "event_id": event_id, "source": source, "event_type": event_type})
    except RuntimeError as error:
        logger.exception(f"[webhook][events][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/webhooks/razorpay", methods=["POST"])
def razorpay_webhook():
    raw = request.get_data() or b""
    signature = request.headers.get("X-Razorpay-Signature") or request.headers.get("x-razorpay-signature")
    try:
        if not signature or not _verify_razorpay_webhook_signature(raw, signature):
            current_app.logger.warning("razorpay webhook signature validation failed")
            return jsonify({"status": "error", "message": "invalid signature"}), 400
    except Exception:
        current_app.logger.exception("error validating razorpay webhook signature")
        return jsonify({"status": "error", "message": "signature validation error"}), 400

    try:
        event = request.get_json(force=True)
    except Exception:
        current_app.logger.exception("failed parsing razorpay webhook json")
        return jsonify({"status": "error", "message": "invalid json"}), 400

    try:
        from db import save_webhook_event

        save_webhook_event(
            source="razorpay",
            event_type=event.get("event", "unknown"),
            payload_json=event,
            headers_json=dict(request.headers),
            status="received",
        )
    except Exception:
        current_app.logger.exception("failed saving razorpay webhook event")

    try:
        ev = event.get("event")
        payload = event.get("payload") or {}

        if ev == "payment.captured":
            payment_obj = payload.get("payment", {}).get("entity", {})
            order_id = payment_obj.get("order_id")
            payment_id = payment_obj.get("id")
            amount = int(payment_obj.get("amount") or 0)
            if order_id:
                try:
                    _update_payment_record(order_id, razorpay_payment_id=payment_id, payment_status="captured")
                except Exception:
                    current_app.logger.exception("failed updating payment record for captured event")
            else:
                # attempt to find by payment id and update
                try:
                    conn = get_db_connection()
                    cur = conn.cursor(dictionary=True)
                    cur.execute(f"SELECT * FROM {PAYMENTS_TABLE} WHERE razorpay_payment_id = %s LIMIT 1", (str(payment_id),))
                    rec = cur.fetchone()
                    cur.close()
                    conn.close()
                    if rec:
                        _update_payment_record(rec.get("razorpay_order_id"), razorpay_payment_id=payment_id, payment_status="captured")
                except Exception:
                    current_app.logger.exception("failed locating payment record by payment id for capture")

        elif ev == "payment.failed":
            payment_obj = payload.get("payment", {}).get("entity", {})
            order_id = payment_obj.get("order_id")
            payment_id = payment_obj.get("id")
            if order_id:
                try:
                    _update_payment_record(order_id, razorpay_payment_id=payment_id, payment_status="failed")
                except Exception:
                    current_app.logger.exception("failed updating payment record for failed event")
            else:
                try:
                    conn = get_db_connection()
                    cur = conn.cursor(dictionary=True)
                    cur.execute(f"SELECT * FROM {PAYMENTS_TABLE} WHERE razorpay_payment_id = %s LIMIT 1", (str(payment_id),))
                    rec = cur.fetchone()
                    cur.close()
                    conn.close()
                    if rec:
                        _update_payment_record(rec.get("razorpay_order_id"), razorpay_payment_id=payment_id, payment_status="failed")
                except Exception:
                    current_app.logger.exception("failed locating payment record by payment id for failure")

        elif ev and ev.startswith("refund"):
            refund_obj = payload.get("refund", {}).get("entity", {})
            payment_id = refund_obj.get("payment_id")
            try:
                conn = get_db_connection()
                cur = conn.cursor(dictionary=True)
                cur.execute(f"SELECT * FROM {PAYMENTS_TABLE} WHERE razorpay_payment_id = %s LIMIT 1", (str(payment_id),))
                rec = cur.fetchone()
                cur.close()
                conn.close()
                if rec:
                    _update_payment_record(rec.get("razorpay_order_id"), razorpay_payment_id=payment_id, payment_status="refunded")
            except Exception:
                current_app.logger.exception("failed updating payment record for refund event")

    except Exception:
        current_app.logger.exception("error handling razorpay webhook event")

    return jsonify({"status": "success"}), 200


@app.route("/payments/refund", methods=["POST"])
@require_auth
@require_role("admin")
def payment_refund():
    payload = request.get_json(silent=True) or {}
    razorpay_payment_id = payload.get("razorpay_payment_id") or payload.get("payment_id")
    amount = payload.get("amount")
    reason = payload.get("reason")

    if not razorpay_payment_id:
        return jsonify({"status": "error", "message": "razorpay_payment_id is required"}), 400

    try:
        url = f"{RAZORPAY_API_BASE_URL}/payments/{razorpay_payment_id}/refund"
        body = {}
        if amount:
            body["amount"] = int(amount)
        if reason:
            body["notes"] = {"reason": str(reason)}

        resp = requests.post(url, auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET), json=body, timeout=30)
        if resp.status_code not in (200, 201):
            current_app.logger.warning("razorpay refund api error %s %s", resp.status_code, resp.text)
            return jsonify({"status": "error", "message": "refund failed", "detail": resp.text}), 500

        refund_obj = resp.json()

        # try to update local payment record
        try:
            conn = get_db_connection()
            cur = conn.cursor(dictionary=True)
            cur.execute(f"SELECT * FROM {PAYMENTS_TABLE} WHERE razorpay_payment_id = %s LIMIT 1", (str(razorpay_payment_id),))
            rec = cur.fetchone()
            cur.close()
            conn.close()
            if rec:
                _update_payment_record(rec.get("razorpay_order_id"), razorpay_payment_id=razorpay_payment_id, payment_status="refund_initiated")
        except Exception:
            current_app.logger.exception("failed updating payment record after refund api call")

        return jsonify({"status": "success", "refund": refund_obj}), 200
    except Exception:
        current_app.logger.exception("error initiating refund")
        return jsonify({"status": "error", "message": "internal error"}), 500


@app.route("/api/admin/ai/action-logs", methods=["GET"])
@require_auth
@require_role("admin")
def admin_ai_action_logs():
    try:
        try:
            page = max(1, int(request.args.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            per_page = max(1, min(200, int(request.args.get("per_page", 50))))
        except (TypeError, ValueError):
            per_page = 50

        filters = {}
        if request.args.get("user_id"):
            filters["user_id"] = request.args.get("user_id")
        if request.args.get("shipment_id"):
            try:
                filters["shipment_id"] = int(request.args.get("shipment_id"))
            except Exception:
                pass
        if request.args.get("action"):
            filters["action"] = request.args.get("action")
        if request.args.get("status"):
            filters["status"] = request.args.get("status")

        from db import get_ai_action_logs

        result = get_ai_action_logs(page=page, per_page=per_page, filters=filters)
        return jsonify({"status": "success", **result})
    except Exception as error:
        logger.exception(f"[admin][ai][action_logs][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/admin/ai/action-logs/<int:log_id>", methods=["GET"])
@require_auth
@require_role("admin")
def admin_ai_action_log_get(log_id):
    try:
        from db import get_ai_action_logs

        result = get_ai_action_logs(page=1, per_page=1, filters={})
        # Best-effort: fetch single log by id
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM ai_action_logs WHERE id = %s", (int(log_id),))
            row = cursor.fetchone()
            if not row:
                return jsonify({"status": "error", "message": "Log not found"}), 404
            try:
                row["request_payload"] = json.loads(row.get("request_payload") or "{}")
            except Exception:
                row["request_payload"] = {}
            try:
                row["response_payload"] = json.loads(row.get("response_payload") or "{}")
            except Exception:
                row["response_payload"] = {}
            return jsonify({"status": "success", "log": row})
        finally:
            cursor.close()
            conn.close()
    except Exception as error:
        logger.exception(f"[admin][ai][action_log_get][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/admin/ai/workflows", methods=["GET"])
@require_auth
@require_role("admin")
def admin_ai_workflows():
    try:
        user_id = request.args.get("user_id")
        workflow_type = request.args.get("workflow_type")
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            if user_id:
                if workflow_type:
                    cursor.execute(
                        "SELECT * FROM ai_workflow_memory WHERE user_id = %s AND workflow_type = %s ORDER BY updated_at DESC LIMIT 10",
                        (str(user_id).strip(), str(workflow_type).strip()),
                    )
                else:
                    cursor.execute(
                        "SELECT * FROM ai_workflow_memory WHERE user_id = %s ORDER BY updated_at DESC LIMIT 10",
                        (str(user_id).strip(),),
                    )
            elif workflow_type:
                cursor.execute(
                    "SELECT * FROM ai_workflow_memory WHERE workflow_type = %s ORDER BY updated_at DESC LIMIT 10",
                    (str(workflow_type).strip(),),
                )
            else:
                cursor.execute("SELECT * FROM ai_workflow_memory ORDER BY updated_at DESC LIMIT 10")

            rows = cursor.fetchall() or []
            for row in rows:
                try:
                    row["workflow_state"] = json.loads(row.get("workflow_state") or "{}")
                except Exception:
                    row["workflow_state"] = {}
                try:
                    row["context_json"] = json.loads(row.get("context_json") or "{}")
                except Exception:
                    row["context_json"] = {}
        finally:
            cursor.close()
            conn.close()

        return jsonify({"status": "success", "count": len(rows), "workflows": rows})
    except Exception as error:
        logger.exception(f"[admin][ai][workflows][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/admin/ai/workflow-retries", methods=["GET"])
@require_auth
@require_role("admin")
def admin_ai_workflow_retries():
    try:
        from db import get_workflow_retry_jobs

        status = request.args.get("status")
        try:
            limit = max(1, min(int(request.args.get("limit", 100)), 500))
        except Exception:
            limit = 100

        jobs = get_workflow_retry_jobs(status=status, limit=limit)
        return jsonify({"status": "success", "count": len(jobs), "jobs": jobs})
    except Exception as error:
        logger.exception(f"[admin][ai][workflow_retries][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/admin/ai/workflow-retries/<int:job_id>", methods=["PATCH"])
@require_auth
@require_role("admin")
def admin_ai_workflow_retry_update(job_id):
    try:
        from db import update_workflow_retry_job, save_audit_log

        payload = request.get_json(silent=True) or {}
        row = update_workflow_retry_job(
            job_id,
            status=payload.get("status"),
            retry_count=payload.get("retry_count"),
            error_message=payload.get("error_message"),
            execution_status=payload.get("execution_status"),
        )
        if not row:
            return jsonify({"status": "error", "message": "Retry job not found"}), 404

        jwt_data = get_jwt() or {}
        save_audit_log(
            user_id=get_jwt_identity(),
            role=str(jwt_data.get("role") or "admin"),
            action="WORKFLOW_RETRY_UPDATE",
            entity_type="ai_workflow_retry_queue",
            entity_id=job_id,
            details_json={
                "status": row.get("status"),
                "retry_count": row.get("retry_count"),
                "execution_status": row.get("execution_status"),
            },
        )
        return jsonify({"status": "success", "job": row})
    except Exception as error:
        logger.exception(f"[admin][ai][workflow_retry_update][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/admin/ai/metrics", methods=["GET"])
@require_auth
@require_role("admin")
def admin_ai_metrics():
    try:
        from db import get_ai_tool_metrics

        filters = {}
        if request.args.get("user_id"):
            filters["user_id"] = request.args.get("user_id")
        if request.args.get("shipment_id"):
            try:
                filters["shipment_id"] = int(request.args.get("shipment_id"))
            except Exception:
                pass
        if request.args.get("action"):
            filters["action"] = request.args.get("action")
        if request.args.get("status"):
            filters["status"] = request.args.get("status")

        metrics = get_ai_tool_metrics(filters=filters)
        return jsonify({"status": "success", **metrics})
    except Exception as error:
        logger.exception(f"[admin][ai][metrics][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/admin/ai/replay/<int:workflow_id>", methods=["POST"])
@require_auth
@require_role("admin")
def admin_ai_replay(workflow_id):
    """Replay a saved AI workflow memory and stream progress via socket events.

    Emits socket events: `ai:replay:progress` and `ai:replay:complete` with `replay_run_id`.
    """
    try:
        import uuid
        from db import get_ai_action_logs

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM ai_workflow_memory WHERE id = %s", (int(workflow_id),))
            wf = cursor.fetchone()
            if not wf:
                return jsonify({"status": "error", "message": "workflow not found"}), 404

            user_id = wf.get("user_id")
            workflow_type = wf.get("workflow_type")
            updated_at = wf.get("updated_at")

            # find action logs for this user up to the workflow updated_at timestamp
            cursor.execute(
                "SELECT * FROM ai_action_logs WHERE user_id = %s AND created_at <= %s ORDER BY created_at ASC",
                (user_id, updated_at),
            )
            rows = cursor.fetchall() or []

            timeline = []
            total_duration_ms = 0
            failures = 0
            replay_run_id = str(uuid.uuid4())

            for idx, row in enumerate(rows):
                ts = row.get("created_at")
                next_ts = rows[idx + 1].get("created_at") if idx + 1 < len(rows) else updated_at or ts
                try:
                    duration_ms = int(((next_ts - ts).total_seconds()) * 1000) if (ts and next_ts) else 0
                except Exception:
                    duration_ms = 0

                entry = {
                    "step": idx + 1,
                    "action": row.get("action"),
                    "status": row.get("status") or "success",
                    "execution_status": row.get("execution_status") or row.get("status") or "success",
                    "timestamp": ts.isoformat(sep=" ", timespec="seconds") if ts else None,
                    "started_at": row.get("started_at").isoformat(sep=" ", timespec="seconds") if row.get("started_at") else None,
                    "completed_at": row.get("completed_at").isoformat(sep=" ", timespec="seconds") if row.get("completed_at") else None,
                    "duration_ms": duration_ms,
                    "retry_count": int(row.get("retry_count") or 0),
                    "tool": row.get("tool_name"),
                    "intent": row.get("intent"),
                    "request_payload": json.loads(row.get("request_payload") or "{}") if row.get("request_payload") else {},
                    "response_payload": json.loads(row.get("response_payload") or "{}") if row.get("response_payload") else {},
                    "error_message": row.get("error_message"),
                }

                if entry["status"] != "success":
                    failures += 1

                total_duration_ms += max(0, int(duration_ms))
                timeline.append(entry)

                # stream progress event
                _emit_socket_event("ai:replay:progress", {"replay_run_id": replay_run_id, "workflow_id": workflow_id, "entry": entry})

            metrics = {
                "total_duration_ms": total_duration_ms,
                "action_count": len(timeline),
                "failures": failures,
                "success_rate": round(((len(timeline) - failures) / len(timeline)) * 100, 2) if timeline else 100.0,
            }

            replay_payload = {"workflow_id": workflow_id, "user_id": user_id, "workflow_type": workflow_type, "timeline": timeline, "metrics": metrics, "replay_run_id": replay_run_id}

            # emit completion
            _emit_socket_event("ai:replay:complete", {"replay_run_id": replay_run_id, "workflow_id": workflow_id, "metrics": metrics, "timeline_length": len(timeline)})

            # persist audit for replay operation
            try:
                from db import save_ai_action_log
                save_ai_action_log(user_id=user_id, role=(get_jwt_identity() or "admin"), action="REPLAY", intent="REPLAY", tool_name="replay_engine", shipment_id=None, status="completed", request_payload={"workflow_id": workflow_id}, response_payload=replay_payload)
            except Exception:
                logger.exception("[admin][ai][replay] failed to save replay audit")

            return jsonify({"status": "success", **replay_payload})
        finally:
            cursor.close()
            conn.close()
    except Exception as error:
        logger.exception(f"[admin][ai][replay][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@limiter.limit("10 per minute")
@app.route("/api/bookings", methods=["POST"])
@app.route("/bookings", methods=["POST"])
def create_booking():
    try:
        payload = request.get_json(silent=True) or {}
        verify_jwt_in_request(optional=True)
        authenticated_user_id = get_jwt_identity()
        booking_data = _prepare_booking_insert_data(payload, authenticated_user_id)
        vehicle_recommendation = _recommend_vehicle_for_booking(payload)
        booking_id = _insert_booking_form_record(booking_data)
        booking_row = _fetch_booking_record_by_id(booking_id)
        booking = _serialize_booking_row(booking_row) or {"id": booking_id, **booking_data}

        _insert_booking_status_log(
            booking_id,
            booking.get("booking_status") or "pending",
            note="Booking created",
            location=booking.get("pickup_location") or booking.get("source_location") or "",
            actor_role="customer" if authenticated_user_id else "guest",
            metadata={
                "booking_reference": booking.get("booking_reference"),
                "recommended_vehicle": vehicle_recommendation.get("recommended_vehicle"),
                "alternatives": vehicle_recommendation.get("alternatives", []),
            },
        )

        _send_booking_whatsapp_confirmation(booking_row or booking)

        return jsonify(
            {
                "status": "success",
                "message": "Booking created successfully",
                "booking_id": booking_id,
                "booking": booking,
                "vehicle_recommendation": vehicle_recommendation,
                "timeline": _fetch_booking_timeline(booking_id),
            }
        ), 201
    except ValueError as error:
        return jsonify({"status": "error", "message": str(error)}), 400
    except RuntimeError as error:
        logger.exception(f"[bookings][create][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/bookings", methods=["GET"])
@app.route("/bookings", methods=["GET"])
def list_bookings():
    try:
        booking_rows = _fetch_booking_records()
        bookings = [_serialize_booking_row(row) for row in booking_rows]

        return jsonify(
            {
                "status": "success",
                "count": len(bookings),
                "bookings": bookings,
            }
        )
    except RuntimeError as error:
        logger.exception(f"[bookings][list][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/bookings/<int:booking_id>", methods=["GET"])
@app.route("/bookings/<int:booking_id>", methods=["GET"])
def get_booking(booking_id):
    try:
        booking_row = _fetch_booking_record_by_id(booking_id)
        if not booking_row:
            return jsonify({"status": "error", "message": "Booking not found"}), 404

        return jsonify(
            {
                "status": "success",
                "booking": _serialize_booking_row(booking_row),
            }
        )
    except RuntimeError as error:
        logger.exception(f"[bookings][get][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/bookings/history", methods=["GET"])
@app.route("/bookings/history", methods=["GET"])
@jwt_required(optional=True)
def booking_history():
    try:
        user_id = get_jwt_identity()
        phone = str(request.args.get("phone") or "").strip()

        if not user_id and not phone:
            return jsonify({"status": "error", "message": "Login or provide a phone number to view booking history"}), 400

        try:
            limit = max(1, min(int(request.args.get("limit", 20)), 50))
        except (TypeError, ValueError):
            limit = 20

        booking_rows = _fetch_booking_records_for_user(user_id, phone, limit=limit)
        bookings = [_serialize_booking_row(row) for row in booking_rows]

        return jsonify(
            {
                "status": "success",
                "count": len(bookings),
                "bookings": bookings,
            }
        )
    except RuntimeError as error:
        logger.exception(f"[bookings][history][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/bookings/<int:booking_id>/timeline", methods=["GET"])
@app.route("/bookings/<int:booking_id>/timeline", methods=["GET"])
def booking_timeline(booking_id):
    try:
        booking_row = _fetch_booking_record_by_id(booking_id)
        if not booking_row:
            return jsonify({"status": "error", "message": "Booking not found"}), 404

        timeline = _fetch_booking_timeline(booking_id)
        if not timeline:
            timeline = [
                {
                    "id": 0,
                    "booking_id": int(booking_id),
                    "status": str(booking_row.get("booking_status") or "pending"),
                    "note": "No status updates yet",
                    "location": "",
                    "actor_role": "system",
                    "metadata": {},
                    "created_at": booking_row.get("created_at").isoformat(sep=" ", timespec="seconds") if booking_row.get("created_at") else None,
                }
            ]

        return jsonify(
            {
                "status": "success",
                "booking": _serialize_booking_row(booking_row),
                "timeline": timeline,
            }
        )
    except RuntimeError as error:
        logger.exception(f"[bookings][timeline][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/vehicles/recommendation", methods=["POST"])
@app.route("/vehicles/recommendation", methods=["POST"])
def vehicle_recommendation():
    try:
        payload = request.get_json(silent=True) or {}
        recommendation = _recommend_vehicle_for_booking(payload)
        return jsonify({"status": "success", **recommendation})
    except ValueError as error:
        return jsonify({"status": "error", "message": str(error)}), 400
    except RuntimeError as error:
        logger.exception(f"[vehicles][recommendation][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/bookings/<int:booking_id>/status", methods=["PATCH"])
@app.route("/bookings/<int:booking_id>/status", methods=["PATCH"])
@_require_roles("admin", "driver")
def update_booking_status(booking_id):
    try:
        payload = request.get_json(silent=True) or {}
        booking_row = _fetch_booking_record_by_id(booking_id)
        if not booking_row:
            return jsonify({"status": "error", "message": "Booking not found"}), 404

        next_status = _normalize_booking_status(payload.get("status") or payload.get("booking_status"), booking_row.get("booking_status") or "pending")
        note = str(payload.get("note") or payload.get("message") or "Status updated").strip()
        location = str(payload.get("location") or payload.get("current_location") or "").strip()
        lorry_number = str(payload.get("lorry_number") or payload.get("vehicle_code") or booking_row.get("lorry_number") or "").strip()

        connection = None
        cursor = None
        try:
            connection = get_db_connection()
            cursor = connection.cursor()
            update_columns = ["booking_status = %s"]
            params = [next_status]

            if lorry_number:
                update_columns.append("lorry_number = %s")
                params.append(lorry_number)

            params.append(int(booking_id))
            cursor.execute(
                f"UPDATE {BOOKINGS_TABLE} SET {', '.join(update_columns)} WHERE id = %s",
                params,
            )
            connection.commit()
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None and connection.is_connected():
                connection.close()

        actor_role = str(get_jwt().get("role") or "system").strip() or "system"
        _insert_booking_status_log(
            booking_id,
            next_status,
            note=note,
            location=location,
            actor_role=actor_role,
            metadata={
                "lorry_number": lorry_number,
                "updated_by": get_jwt().get("email") or get_jwt_identity(),
            },
        )

        updated_booking = _fetch_booking_record_by_id(booking_id)
        return jsonify(
            {
                "status": "success",
                "booking": _serialize_booking_row(updated_booking),
                "timeline": _fetch_booking_timeline(booking_id),
            }
        )
    except ValueError as error:
        return jsonify({"status": "error", "message": str(error)}), 400
    except RuntimeError as error:
        logger.exception(f"[bookings][status][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@limiter.limit("15 per minute")
@app.route("/payments/create-order", methods=["POST"])
def create_payment_order():
    try:
        payload = request.get_json(silent=True) or {}
        booking_id = payload.get("booking_id") or payload.get("bookingId")
        shipment_id = payload.get("shipment_id") or payload.get("shipmentId")
        session_id = payload.get("session_id") or payload.get("sessionId")
        payment_type = str(payload.get("payment_type") or payload.get("paymentType") or "advance").strip().lower()

        target_type = None
        target_row = None
        target_id = None

        if booking_id:
            target_type = "booking"
            target_id = int(booking_id)
            target_row = _fetch_booking_record_by_id(target_id)
            if not target_row:
                return jsonify({"status": "error", "message": "Booking not found"}), 404
            amount_due = _resolve_payment_amount(target_row, payment_type, target_id)
        elif shipment_id:
            target_type = "shipment"
            target_id = int(shipment_id)
            target_row = _fetch_shipment_record_by_id(target_id)
            if not target_row:
                return jsonify({"status": "error", "message": "Shipment not found"}), 404
            amount_due = _resolve_shipment_payment_amount(target_row, payment_type, target_id)
        else:
            raise ValueError("booking_id or shipment_id is required")

        if amount_due <= 0:
            raise ValueError("No amount is due for this payment type")

        order_data = _create_razorpay_order(amount_due, target_id)
        _insert_payment_record(
            int(booking_id) if booking_id else None,
            order_data["id"],
            amount_due,
            "created",
            payment_type=payment_type,
            shipment_id=int(shipment_id) if shipment_id else None,
            session_id=session_id,
        )

        return jsonify(
            {
                "status": "success",
                "entity_type": target_type,
                "booking_id": int(booking_id) if booking_id else None,
                "shipment_id": int(shipment_id) if shipment_id else None,
                "session_id": session_id,
                "booking_status": str(target_row.get("booking_status") or "pending") if target_type == "booking" else None,
                "shipment_status": str(target_row.get("shipment_status") or "pending") if target_type == "shipment" else None,
                "payment_type": payment_type,
                "advance_amount": _get_booking_advance_amount(target_row) if target_type == "booking" else int(round(float(target_row.get("estimated_price") or 0) * RAZORPAY_ADVANCE_RATIO)),
                "amount_due": amount_due,
                "amount": int(order_data.get("amount", amount_due * 100)),
                "currency": order_data.get("currency", RAZORPAY_CURRENCY),
                "key_id": RAZORPAY_KEY_ID,
                "order_id": order_data["id"],
                "receipt": order_data.get("receipt"),
            }
        )
    except ValueError as error:
        return jsonify({"status": "error", "message": str(error)}), 400
    except RuntimeError as error:
        logger.exception(f"[payments][create_order][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@limiter.limit("15 per minute")
@app.route("/payments/verify", methods=["POST"])
def verify_payment():
    try:
        payload = request.get_json(silent=True) or {}
        booking_id = payload.get("booking_id") or payload.get("bookingId")
        shipment_id = payload.get("shipment_id") or payload.get("shipmentId")
        session_id = payload.get("session_id") or payload.get("sessionId")
        razorpay_order_id = payload.get("razorpay_order_id") or payload.get("razorpayOrderId")
        razorpay_payment_id = payload.get("razorpay_payment_id") or payload.get("razorpayPaymentId")
        razorpay_signature = payload.get("razorpay_signature") or payload.get("razorpaySignature")

        if not all([razorpay_order_id, razorpay_payment_id, razorpay_signature]):
            raise ValueError("razorpay_order_id, razorpay_payment_id, and razorpay_signature are required")
        if not booking_id and not shipment_id:
            raise ValueError("booking_id or shipment_id is required")

        if not _verify_razorpay_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature):
            return jsonify({"status": "error", "message": "Invalid payment signature"}), 400

        booking_row = None
        shipment_row = None
        if booking_id:
            booking_row = _fetch_booking_record_by_id(booking_id)
            if not booking_row:
                return jsonify({"status": "error", "message": "Booking not found"}), 404
        if shipment_id:
            shipment_row = _fetch_shipment_record_by_id(shipment_id)
            if not shipment_row:
                return jsonify({"status": "error", "message": "Shipment not found"}), 404

        payment_record = _fetch_payment_record_by_order_id(razorpay_order_id)
        payment_type = str(
            payload.get("payment_type")
            or payload.get("paymentType")
            or (payment_record.get("payment_type") if payment_record else "advance")
        ).strip().lower()
        if booking_row is not None:
            payment_amount = payment_record.get("amount") if payment_record else _resolve_payment_amount(booking_row, payment_type, booking_id)
        else:
            payment_amount = payment_record.get("amount") if payment_record else _resolve_shipment_payment_amount(shipment_row, payment_type, shipment_id)

        if payment_record:
            _update_payment_record(
                razorpay_order_id,
                razorpay_payment_id=razorpay_payment_id,
                payment_status="paid",
                payment_type=payment_type,
            )
        else:
            _insert_payment_record(
                int(booking_id) if booking_id else None,
                razorpay_order_id,
                payment_amount,
                "paid",
                razorpay_payment_id,
                payment_type=payment_type,
                shipment_id=int(shipment_id) if shipment_id else None,
                session_id=session_id,
            )

        shipment_reference = int(shipment_id) if shipment_id else None
        if shipment_reference is not None:
            try:
                _insert_shipment_event_log(
                    shipment_reference,
                    "payment_verified",
                    title="Payment verified",
                    message=f"Payment {razorpay_payment_id} marked paid.",
                    severity="success",
                    source="payment.verify",
                    metadata={
                        "razorpay_order_id": razorpay_order_id,
                        "razorpay_payment_id": razorpay_payment_id,
                        "payment_amount": int(round(float(payment_amount))),
                        "payment_type": payment_type,
                    },
                )
            except Exception:
                logger.warning("[event_log][payment_verified][warning] Failed to store shipment event log")

        updated_booking = None
        updated_shipment = None
        if booking_row is not None:
            _update_booking_payment_state(int(booking_id), "paid", booking_status="confirmed")
            _insert_booking_status_log(
                booking_id,
                "confirmed",
                note="Payment verified and booking confirmed",
                location=str(booking_row.get("pickup_location") or booking_row.get("source_location") or "").strip(),
                actor_role="system",
                metadata={
                    "razorpay_order_id": razorpay_order_id,
                    "razorpay_payment_id": razorpay_payment_id,
                    "payment_amount": int(round(float(payment_amount))),
                    "payment_type": payment_type,
                },
            )
            updated_booking = _fetch_booking_record_by_id(booking_id)
        else:
            _update_shipment_record(int(shipment_id), {"payment_status": "paid", "shipment_status": "confirmed"})
            _insert_shipment_status_log(
                shipment_id,
                "confirmed",
                note="Payment verified and shipment confirmed",
                location=str(shipment_row.get("pickup_location") or "").strip(),
                actor_role="system",
                metadata={
                    "razorpay_order_id": razorpay_order_id,
                    "razorpay_payment_id": razorpay_payment_id,
                    "payment_amount": int(round(float(payment_amount))),
                    "payment_type": payment_type,
                },
            )
            updated_shipment = _fetch_shipment_record_by_id(shipment_id)

        try:
            operational = None
            if updated_shipment is not None:
                operational = _build_shipment_operational_context(updated_shipment, role="customer", include_invoice=True)
            elif updated_booking is not None:
                operational = _build_booking_operational_context(updated_booking, role="customer", include_invoice=True)
            elif shipment_row is not None:
                operational = _build_shipment_operational_context(shipment_row, role="customer", include_invoice=True)
            elif booking_row is not None:
                operational = _build_booking_operational_context(booking_row, role="customer", include_invoice=True)

            if operational:
                _broadcast_chat_update(
                    {
                        "status": "success",
                        "message": operational.get("reply") or "Payment verified successfully.",
                        "reply": operational.get("reply") or "Payment verified successfully.",
                        "action": "RECONCILE_PAYMENT",
                        "card_type": "payment_card",
                        "workflow": "payment_reconciliation",
                        "route": "payments.verify",
                        "intent": "RECONCILE_PAYMENT",
                        "data": operational.get("data") or {},
                        "suggestions": ["Download invoice", "Track shipment", "Contact driver"],
                        "source": "payment.verify",
                    }
                )
        except Exception as error:
            logger.warning(f"[socketio][chat:update][warning] {error}")
        updated_payment_record = {
            "booking_id": int(booking_id) if booking_id else None,
            "shipment_id": int(shipment_id) if shipment_id else None,
            "session_id": session_id,
            "razorpay_order_id": razorpay_order_id,
            "razorpay_payment_id": razorpay_payment_id,
            "amount": int(round(float(payment_amount))),
            "payment_status": "paid",
            "payment_type": payment_type,
        }

        if session_id:
            try:
                booking_workflow_manager.save(
                    session_id=session_id,
                    user_id=str((booking_row or shipment_row or {}).get("user_id") or "default_user"),
                    workflow="shipment_booking" if booking_id else "shipment_payment",
                    current_step="confirmed",
                    draft_json=booking_row or shipment_row or {},
                    quote_json={"payment_amount": int(round(float(payment_amount)))},
                    payment_json=updated_payment_record,
                    shipment_id=int(shipment_id) if shipment_id else None,
                    status="confirmed",
                    last_user_message="payment_verified",
                    last_bot_reply="Payment verified successfully",
                )
            except Exception as error:
                print(f"[booking_session][error] Could not persist verified payment: {error}")

        _send_payment_whatsapp_confirmation(updated_booking or updated_shipment or booking_row or shipment_row, updated_payment_record)

        return jsonify(
            {
                "status": "success",
                "message": "Payment verified successfully",
                "booking": _serialize_booking_row(updated_booking) if updated_booking else None,
                "shipment": _serialize_shipment_row(updated_shipment) if updated_shipment else None,
                "payment": {
                    "booking_id": int(booking_id) if booking_id else None,
                    "shipment_id": int(shipment_id) if shipment_id else None,
                    "razorpay_order_id": razorpay_order_id,
                    "razorpay_payment_id": razorpay_payment_id,
                    "amount": int(round(float(payment_amount))),
                    "payment_status": "paid",
                    "payment_type": payment_type,
                },
            }
        )
    except ValueError as error:
        return jsonify({"status": "error", "message": str(error)}), 400
    except RuntimeError as error:
        logger.exception(f"[payments][verify][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/payments/history", methods=["GET"])
def payment_history():
    try:
        booking_id = request.args.get("booking_id") or request.args.get("bookingId")
        shipment_id = request.args.get("shipment_id") or request.args.get("shipmentId")
        if booking_id:
            payment_rows = _fetch_payment_records_for_booking(booking_id)
        elif shipment_id:
            payment_rows = _fetch_payment_records_for_shipment(shipment_id)
        else:
            payment_rows = _fetch_payment_records()

        return jsonify(
            {
                "status": "success",
                "payments": [_serialize_payment_row(row) for row in payment_rows or []],
            }
        )
    except RuntimeError as error:
        logger.exception(f"[payments][history][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/bookings/<booking_id>/invoice", methods=["GET"])
def booking_invoice(booking_id):
    try:
        booking_row = _fetch_booking_record_by_id(booking_id)
        if not booking_row:
            return jsonify({"status": "error", "message": "Booking not found"}), 404

        invoice_payload = _build_invoice_payload(booking_row, _fetch_payment_records_for_booking(booking_id))

        if request.args.get("download") == "1":
            response = make_response(_render_invoice_html(invoice_payload))
            response.headers["Content-Type"] = "text/html; charset=utf-8"
            response.headers["Content-Disposition"] = f'attachment; filename="{invoice_payload["invoice_number"]}.html"'
            return response

        return jsonify({"status": "success", "invoice": invoice_payload})
    except RuntimeError as error:
        logger.exception(f"[payments][invoice][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/shipments", methods=["POST"])
@app.route("/shipments", methods=["POST"])
@jwt_required(optional=True)
def create_shipment():
    try:
        payload = request.get_json(silent=True) or {}
        user_id = get_jwt_identity() or str(payload.get("user_id") or payload.get("userId") or "guest")

        pickup_location = _normalize_booking_field(payload.get("pickup_location") or payload.get("pickupLocation"), "Pickup location")
        drop_location = _normalize_booking_field(payload.get("drop_location") or payload.get("dropLocation"), "Drop location")
        cargo_type = _normalize_booking_field(payload.get("cargo_type") or payload.get("cargoType") or "General cargo", "Cargo type")
        truck_type = _normalize_truck_type(payload.get("truck_type") or payload.get("truckType") or payload.get("truck_type_code") or "12", payload.get("weight") or payload.get("load_weight"))

        try:
            weight = float(payload.get("weight") or payload.get("load_weight") or payload.get("cargo_weight") or payload.get("tons") or payload.get("loadWeight"))
        except (TypeError, ValueError):
            raise ValueError("Weight must be a number")

        if weight <= 0:
            raise ValueError("Weight must be greater than zero")

        pickup_coords = _get_coordinates(pickup_location)
        drop_coords = _get_coordinates(drop_location)
        route_info = _get_route_geometry(pickup_coords[0], pickup_coords[1], drop_coords[0], drop_coords[1])
        distance_km = float(route_info["distance_km"])
        pricing = _get_shipment_pricing(distance_km, truck_type, weight)

        assigned_driver = _find_nearest_available_driver(pickup_coords[0], pickup_coords[1], truck_type)
        shipment_payload = {
            "pickup_location": pickup_location,
            "drop_location": drop_location,
            "cargo_type": cargo_type,
            "truck_type": f"{truck_type} tyre",
            "weight": weight,
            "distance_km": round(distance_km, 2),
            "estimated_price": pricing["estimated_price"],
            "payment_status": "pending",
            "shipment_status": "pending",
            "assigned_driver_id": assigned_driver.get("id") if assigned_driver else None,
        }

        shipment_id = _insert_shipment_record(shipment_payload, user_id)
        shipment_row = _fetch_shipment_record_by_id(shipment_id)

        _insert_shipment_status_log(
            shipment_id,
            "pending",
            note="Shipment created",
            location=pickup_location,
            actor_role="customer" if get_jwt_identity() else "guest",
            metadata={
                "route_coordinates": route_info.get("coordinates", []),
                "assigned_driver": assigned_driver,
                "pricing": pricing,
            },
        )

        return jsonify(
            {
                "status": "success",
                "message": "Shipment created successfully",
                "shipment": _serialize_shipment_row(shipment_row),
                "route_coordinates": route_info.get("coordinates", []),
                "estimated_price": pricing["estimated_price"],
                "distance_km": round(distance_km, 2),
                "assigned_driver": _serialize_driver_row(assigned_driver) if assigned_driver else None,
            }
        ), 201
    except ValueError as error:
        return jsonify({"status": "error", "message": str(error)}), 400
    except RuntimeError as error:
        logger.exception(f"[shipments][create][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/shipments/my", methods=["GET"])
@jwt_required()
def my_shipments():
    try:
        user_id = get_jwt_identity()
        shipment_rows = _fetch_shipment_records_for_user(user_id)
        shipments = [_serialize_shipment_row(row) for row in shipment_rows]
        return jsonify({"status": "success", "count": len(shipments), "shipments": shipments})
    except RuntimeError as error:
        logger.exception(f"[shipments][my][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/shipments/<int:shipment_id>", methods=["GET"])
@jwt_required()
def get_shipment(shipment_id):
    try:
        shipment_row = _fetch_shipment_record_by_id(shipment_id)
        if not shipment_row:
            return jsonify({"status": "error", "message": "Shipment not found"}), 404

        return jsonify(
            {
                "status": "success",
                "shipment": _serialize_shipment_row(shipment_row),
                "timeline": _fetch_shipment_timeline(shipment_id),
            }
        )
    except RuntimeError as error:
        logger.exception(f"[shipments][get][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/shipments/<int:shipment_id>/timeline", methods=["GET"])
@jwt_required()
def shipment_timeline(shipment_id):
    try:
        shipment_row = _fetch_shipment_record_by_id(shipment_id)
        if not shipment_row:
            return jsonify({"status": "error", "message": "Shipment not found"}), 404

        timeline = _fetch_shipment_timeline(shipment_id)
        if not timeline:
            timeline = [
                {
                    "id": 0,
                    "shipment_id": int(shipment_id),
                    "status": str(shipment_row.get("shipment_status") or "pending"),
                    "note": "No status updates yet",
                    "location": "",
                    "actor_role": "system",
                    "metadata": {},
                    "created_at": shipment_row.get("created_at").isoformat(sep=" ", timespec="seconds") if shipment_row.get("created_at") else None,
                }
            ]

        return jsonify({"status": "success", "shipment": _serialize_shipment_row(shipment_row), "timeline": timeline})
    except RuntimeError as error:
        logger.exception(f"[shipments][timeline][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/shipments/<int:shipment_id>/events", methods=["GET"])
@jwt_required()
def shipment_events(shipment_id):
    try:
        shipment_row = _fetch_shipment_record_by_id(shipment_id)
        if not shipment_row:
            return jsonify({"status": "error", "message": "Shipment not found"}), 404

        events = _fetch_shipment_event_logs(shipment_id)
        return jsonify({"status": "success", "shipment": _serialize_shipment_row(shipment_row), "events": events})
    except RuntimeError as error:
        logger.exception(f"[shipments][events][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500
    
@app.route("/api/shipments/activity-feed", methods=["GET"])
@jwt_required(optional=True)
def shipment_activity_feed():
    try:
        user_id = get_jwt_identity()
        booking_id = request.args.get("booking_id") or request.args.get("shipment_id")
        limit = max(1, min(int(request.args.get("limit", 12)), 30))

        feed_payload = _build_shipment_activity_feed_payload(
            limit=limit,
            shipment_id=int(booking_id) if booking_id not in (None, "") else None,
            user_identifier=str(user_id or "").strip() or None,
        )

        return jsonify({
            "status": "success",
            "feed": feed_payload.get("feed") or [],
            "active_shipments": feed_payload.get("active_shipments") or [],
            "recent_shipments": feed_payload.get("recent_shipments") or [],
            "summary": feed_payload.get("summary") or {},
        })
    except RuntimeError as error:
        logger.exception(f"[shipments][activity_feed][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/users/preferences", methods=["GET", "PUT", "DELETE"])
@jwt_required()
def user_preferences():
    try:
        user_id = get_jwt_identity()
        from db import validate_foreign_key_reference

        try:
            user_reference = int(user_id)
        except (TypeError, ValueError):
            user_reference = None

        if user_reference is not None and not validate_foreign_key_reference("users", "id", user_reference):
            return jsonify({"status": "error", "message": "User not found"}), 404

        if request.method == 'GET':
            prefs = _get_user_preferences(user_id)
            return jsonify({"status": "success", "preferences": prefs})

        if request.method == 'DELETE':
            deleted = _delete_user_preferences(user_id)
            return jsonify({"status": "success", "deleted": deleted, "preferences": {}})

        # PUT: save preferences
        payload = request.get_json() or {}
        _set_user_preferences(user_id, payload)
        return jsonify({"status": "success", "preferences": payload})
    except RuntimeError as error:
        logger.exception(f"[users][preferences][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/users/searches", methods=["GET", "POST"])
@jwt_required(optional=True)
def user_searches():
    try:
        user_id = get_jwt_identity()
        if request.method == 'POST':
            payload = request.get_json() or {}
            query = payload.get('query') or payload.get('q') or ''
            metadata = payload.get('metadata') or {}
            _log_user_search(user_id, query, metadata)
            return jsonify({"status": "success"})

        limit = max(1, min(int(request.args.get('limit', 10)), 50))
        rows = _fetch_user_searches(user_id=user_id, limit=limit)
        return jsonify({"status": "success", "searches": rows})
    except RuntimeError as error:
        logger.exception(f"[users][searches][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/users/recommendations", methods=["GET"])
@jwt_required(optional=True)
def user_recommendations():
    try:
        user_id = get_jwt_identity()
        limit = max(1, min(int(request.args.get('limit', 6)), 30))
        recs = _fetch_user_recommendations(user_id=user_id, limit=limit)
        return jsonify({"status": "success", "recommendations": recs})
    except RuntimeError as error:
        logger.exception(f"[users][recommendations][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/shipments/<int:shipment_id>/invoice", methods=["GET"])
@jwt_required()
def shipment_invoice(shipment_id):
    try:
        shipment_row = _fetch_shipment_record_by_id(shipment_id)
        if not shipment_row:
            return jsonify({"status": "error", "message": "Shipment not found"}), 404

        invoice_payload = _build_shipment_invoice_payload(shipment_row, _fetch_payment_records_for_shipment(shipment_id))

        if request.args.get("download") == "1":
            response = make_response(_render_shipment_invoice_html(invoice_payload))
            response.headers["Content-Type"] = "text/html; charset=utf-8"
            response.headers["Content-Disposition"] = f'attachment; filename="{invoice_payload["invoice_number"]}.html"'
            return response

        return jsonify({"status": "success", "invoice": invoice_payload})
    except RuntimeError as error:
        logger.exception(f"[shipments][invoice][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/admin/shipments", methods=["GET"])
@require_auth
@require_role("admin")
def admin_shipments_list():
    try:
        shipments = [_serialize_shipment_row(row) for row in _fetch_shipment_records()]
        return jsonify({"status": "success", "count": len(shipments), "shipments": shipments})
    except RuntimeError as error:
        logger.exception(f"[admin][shipments][list][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500


@app.route("/api/admin/shipments/<int:shipment_id>/status", methods=["PUT", "PATCH"])
@require_auth
@require_role("admin")
def admin_update_shipment_status(shipment_id):
    try:
        payload = request.get_json(silent=True) or {}
        shipment_row = _fetch_shipment_record_by_id(shipment_id)
        if not shipment_row:
            return jsonify({"status": "error", "message": "Shipment not found"}), 404

        next_status = _normalize_shipment_status(payload.get("shipment_status") or payload.get("status"), shipment_row.get("shipment_status") or "pending")
        assigned_driver_id = payload.get("assigned_driver_id")
        updates = {"shipment_status": next_status}

        if assigned_driver_id is not None and str(assigned_driver_id).strip() != "":
            updates["assigned_driver_id"] = int(assigned_driver_id)

        _update_shipment_record(shipment_id, updates)
        _insert_shipment_status_log(
            shipment_id,
            next_status,
            note=str(payload.get("note") or payload.get("message") or "Status updated").strip(),
            location=str(payload.get("location") or "").strip(),
            actor_role="admin",
            metadata={
                "assigned_driver_id": updates.get("assigned_driver_id"),
                "updated_by": get_jwt().get("email") or get_jwt_identity(),
            },
        )

        updated_row = _fetch_shipment_record_by_id(shipment_id)
        return jsonify({"status": "success", "shipment": _serialize_shipment_row(updated_row), "timeline": _fetch_shipment_timeline(shipment_id)})
    except ValueError as error:
        return jsonify({"status": "error", "message": str(error)}), 400
    except RuntimeError as error:
        logger.exception(f"[admin][shipments][status][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500



@app.route("/fare-estimate", methods=["POST"])
def fare_estimate():
    try:
        payload = request.get_json(silent=True) or {}

        pickup_location = _normalize_booking_field(
            payload.get("pickup_location") or payload.get("pickupLocation"),
            "Pickup location",
        )
        drop_location = _normalize_booking_field(
            payload.get("drop_location") or payload.get("dropLocation"),
            "Drop location",
        )
        truck_type = _normalize_form_truck_type(payload.get("truck_type") or payload.get("truckType"))

        if not truck_type:
            raise ValueError("Select a valid truck type")

        try:
            load_weight = float(payload.get("load_weight") or payload.get("loadWeight"))
        except (TypeError, ValueError):
            raise ValueError("Load weight must be a number")

        if load_weight <= 0:
            raise ValueError("Load weight must be greater than zero")

        truck_limits = BOOKING_FORM_TRUCK_TYPES[truck_type]
        if load_weight > truck_limits["max_load_weight"]:
            raise ValueError(
                f"{truck_limits['label']} supports up to {truck_limits['max_load_weight']} tons"
            )

        truck_type_code = truck_type.split()[0]
        distance_km, _ = _estimate_distance_and_price(pickup_location, drop_location, truck_type_code)
        estimated_fare = calculate_price(distance_km, truck_type_code)
        eta_hours = _estimate_eta_hours(distance_km, truck_type_code)
        truck_type_label = _canonical_truck_type_label(truck_type_code)
        reply = _build_fare_estimation_reply(
            pickup_location,
            drop_location,
            load_weight,
            truck_type_label,
            distance_km,
            estimated_fare,
            eta_hours,
        )

        return jsonify(
            {
                "status": "success",
                "pickup_location": pickup_location,
                "drop_location": drop_location,
                "truck_type": truck_type_label,
                "load_weight": round(load_weight, 2),
                "distance": int(round(float(distance_km))),
                "estimated_fare": int(round(float(estimated_fare))),
                "eta_hours": eta_hours,
                "reply": reply,
                "message": reply,
            }
        )
    except ValueError as error:
        return jsonify({"status": "error", "message": str(error)}), 400
    except Exception as error:
        logger.exception(f"[fare-estimate][error] {error}")
        return jsonify({"status": "error", "message": str(error)}), 500

@limiter.limit("30 per minute")
@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json() or {}
        raw_message = str(data.get("message", "")).strip()
        message = raw_message.lower()

        auth_context = _get_optional_auth_context()
        user_id = str(auth_context.get("identity") or data.get("user_id", "default_user")).strip() or "default_user"

        user_state = _get_chat_state(user_id)
        user_state["user_id"] = user_id
        user_state["auth_role"] = auth_context.get("role") or ""
        user_state["auth_identity"] = auth_context.get("identity") or ""
        _log_chat_state(user_state, note="chat_entry")
        db_context = _build_chat_db_context(raw_message, user_state, auth_context)

        if not raw_message:
            return _save_chat_history_before_return(user_state, raw_message, (jsonify({"error": "Message is required"}), 400), "chat.empty_message")

        print(f"[chat][req] user={user_id} message={raw_message!r} stage={user_data.get(user_id, {}).get('stage')}")

        if _is_reset_intent(raw_message):
            reset_session(user_id=user_id, user_state=user_state)
            _set_stage(user_state, "awaiting_booking_confirmation")
            user_state["onboarding_shown"] = True
            _set_active_intent(user_state, "welcome", source="reset_intent")
            print(f"[chat][debug] reset intent detected: {raw_message!r}")
            return _save_chat_history_before_return(user_state, raw_message, jsonify({"reply": _clean_greeting_reply()}), "chat.reset_intent")

        if user_state.get("booking_mode") or user_state.get("stage") in BOOKING_STATES:
            current_stage = user_state.get("stage", "idle")
            print(f"[booking][stage] user={user_id} entering_stage={current_stage} message_preview={raw_message[:50]}")
            stage_response = _handle_booking_stage(user_state, raw_message, data, db_context=db_context)
            if stage_response is not None:
                return _save_chat_history_before_return(user_state, raw_message, stage_response, "chat.booking_stage")

        # Greeting / booking starter handling
        greetings = {"hi", "hello", "hey", "good morning", "good afternoon", "good evening", "restart", "reset", "start over"}
        booking_starters = {"need a truck", "need truck", "book transport", "book a truck", "need a lorry", "need lorry"}

        # more robust greeting detection
        if any(g in message for g in greetings) and not user_state.get("onboarding_shown") and not user_state.get("booking_mode"):
            reset_session(user_state=user_state)
            _set_stage(user_state, "awaiting_booking_confirmation")
            user_state["onboarding_shown"] = True
            _set_active_intent(user_state, "welcome", source="fresh_greeting")
            print(f"[chat][debug] greeting detected: {raw_message!r}")
            return _save_chat_history_before_return(user_state, raw_message, jsonify({"reply": _clean_greeting_reply()}), "chat.greeting_fresh")

        if any(g in message for g in greetings):
            print(f"[chat][debug] greeting ignored during active session: {raw_message!r}")

        if any(message.startswith(starter) or starter in message for starter in booking_starters):
            # direct booking starter -> start fresh
            if not user_state.get("booking_mode"):
                reset_session(user_state=user_state)
                _start_new_booking(user_state)
                _set_active_intent(user_state, "booking", source="booking_starter")
                return _save_chat_history_before_return(user_state, raw_message, jsonify({"reply": _request_prompt_for_field("source")}), "chat.booking_starter")

        if user_state.get("booking_mode") and user_state.get("stage") not in BOOKING_STATES:
            _set_active_intent(user_state, "booking", source="booking_mode_lock")
            _log_chat_state(user_state, note="booking_mode_locked")
            return _save_chat_history_before_return(user_state, raw_message, _advance_request_flow(user_state), "chat.booking_mode_lock")

        structured_response = _build_structured_db_reply(raw_message, user_state, auth_context, db_context=db_context)
        if structured_response is not None:
            return _save_chat_history_before_return(user_state, raw_message, structured_response, "chat.structured_db_reply")

        booking_context = {
            "source": user_state.get("booking", {}).get("source") or "",
            "destination": user_state.get("booking", {}).get("destination") or "",
            "tons": user_state.get("booking", {}).get("tons") or "",
            "tyre_type": user_state.get("booking", {}).get("truck_type") or user_state.get("booking", {}).get("tyre_type") or "",
            "distance": user_state.get("quote", {}).get("distance") or "",
            "price": user_state.get("quote", {}).get("price") or "",
            "stage": user_state.get("stage", "idle"),
        }
        intent_result = _detect_user_intent(raw_message, user_state.get("stage", "idle"), booking_context, db_context=db_context)
        public_intent = _normalize_public_intent(intent_result.get("intent"))
        _log_detected_chat_intent(user_id, user_state.get("stage", "idle"), public_intent, intent_result.get("confidence", 0), INTENT_WORKFLOWS.get(public_intent, "fallback"), note=str(intent_result.get("reasoning") or ""))

        if public_intent != "UNRELATED":
            ai_agent = _get_logistics_ai_agent()
            ai_response = ai_agent.handle(raw_message, user_state, auth_context, db_context=db_context, intent_result=intent_result)
            ai_payload = ai_response.as_dict() if hasattr(ai_response, "as_dict") else dict(ai_response or {})

            if ai_payload.get("handled", True):
                return _save_chat_history_before_return(
                    user_state,
                    raw_message,
                    _build_chat_intent_payload(
                        ai_payload.get("intent") or public_intent,
                        ai_payload.get("confidence", intent_result.get("confidence", 0)),
                        ai_payload.get("message") or ai_payload.get("reply") or "",
                        ai_payload.get("workflow") or INTENT_WORKFLOWS.get(public_intent, "fallback"),
                        ai_payload.get("route") or INTENT_WORKFLOWS.get(public_intent, "fallback"),
                        data=ai_payload.get("data") or {},
                        suggestions=ai_payload.get("suggestions") or [],
                        status=ai_payload.get("status") or "success",
                        http_status=ai_payload.get("http_status") or 200,
                        response_type=ai_payload.get("card_type") or ai_payload.get("type") or _resolve_chat_response_type(public_intent, ai_payload.get("route"), ai_payload.get("data")),
                        action=ai_payload.get("action") or ai_payload.get("intent") or public_intent,
                        card_type=ai_payload.get("card_type") or ai_payload.get("type") or _resolve_chat_response_type(public_intent, ai_payload.get("route"), ai_payload.get("data")),
                        message=ai_payload.get("message") or ai_payload.get("reply") or "",
                    ),
                    f"chat.ai_agent.{ai_payload.get('action') or public_intent}",
                )

        if public_intent == "GET_ANALYTICS":
            try:
                verify_jwt_in_request(optional=True)
                claims = get_jwt()
            except Exception:
                claims = {}

            role = str((claims or {}).get("role") or "").strip().lower()
            if role not in {"admin", "super_admin"}:
                return _save_chat_history_before_return(
                    user_state,
                    raw_message,
                    _build_chat_intent_payload(
                        "GET_ANALYTICS",
                        intent_result.get("confidence", 0),
                        "Analytics is available from the admin dashboard for authorized staff only.",
                        INTENT_WORKFLOWS["GET_ANALYTICS"],
                        "analytics.access_restricted",
                        suggestions=["Open admin dashboard"],
                        status="forbidden",
                        http_status=403,
                    ),
                    "chat.intent_analytics_forbidden",
                )

            dashboard = _build_admin_dashboard_payload(days=30)
            summary = dashboard.get("summary", {})
            reply = (
                f"Analytics ready: {summary.get('total_bookings', 0)} bookings, "
                f"{summary.get('active_shipments', 0)} active shipments, "
                f"₹{int(summary.get('revenue_collected', 0)):,} collected revenue."
            )
            return _save_chat_history_before_return(
                user_state,
                raw_message,
                _build_chat_intent_payload(
                    "GET_ANALYTICS",
                    intent_result.get("confidence", 0),
                    reply,
                    INTENT_WORKFLOWS["GET_ANALYTICS"],
                    "analytics.dashboard_summary",
                    data=dashboard,
                ),
                "chat.intent_analytics",
            )

        if public_intent == "DRIVER_UPDATE":
            try:
                verify_jwt_in_request(optional=True)
                claims = get_jwt()
            except Exception:
                claims = {}

            role = str((claims or {}).get("role") or "").strip().lower()
            if role not in {"admin", "super_admin", "driver"}:
                return _save_chat_history_before_return(
                    user_state,
                    raw_message,
                    _build_chat_intent_payload(
                        "DRIVER_UPDATE",
                        intent_result.get("confidence", 0),
                        "Driver management updates are available to admin users only.",
                        INTENT_WORKFLOWS["DRIVER_UPDATE"],
                        "driver_management.access_restricted",
                        suggestions=["Open admin driver management"],
                        status="forbidden",
                        http_status=403,
                    ),
                    "chat.intent_driver_forbidden",
                )

            if role == "driver":
                driver_context = get_driver_context(auth_context.get("identity") or user_state.get("user_id"))
                assigned_shipments = driver_context.get("assigned_shipments") or []
                reply = f"You have {len(assigned_shipments)} assigned shipment(s) available in your driver workspace."
                if assigned_shipments:
                    latest_shipment = assigned_shipments[0]
                    reply += f" Latest shipment #{latest_shipment.get('id')} is {latest_shipment.get('shipment_status') or 'pending'}."

                return _save_chat_history_before_return(
                    user_state,
                    raw_message,
                    _build_chat_intent_payload(
                        "DRIVER_UPDATE",
                        intent_result.get("confidence", 0),
                        reply,
                        INTENT_WORKFLOWS["DRIVER_UPDATE"],
                        "driver.assigned_shipments",
                        data={
                            "driver": driver_context.get("driver"),
                            "assigned_shipments": assigned_shipments,
                            "gps_logs": driver_context.get("gps_logs") or [],
                            "quick_actions": _get_role_chat_policy("driver").get("quick_actions", []),
                        },
                        suggestions=_get_role_chat_policy("driver").get("quick_actions", []),
                        response_type="driver_assignment_card",
                    ),
                    "chat.intent_driver_assigned_shipments",
                )

            driver_id = _extract_reference_id(raw_message)
            if driver_id is None:
                return _save_chat_history_before_return(
                    user_state,
                    raw_message,
                    _build_chat_intent_payload(
                        "DRIVER_UPDATE",
                        intent_result.get("confidence", 0),
                        "Share the driver ID and the status or field you want to update.",
                        INTENT_WORKFLOWS["DRIVER_UPDATE"],
                        "driver_management.request_reference",
                        suggestions=["Share driver ID", "Share desired status"],
                    ),
                    "chat.intent_driver_request",
                )

            driver_row = _get_driver_record_by_id(driver_id)
            if not driver_row:
                return _save_chat_history_before_return(
                    user_state,
                    raw_message,
                    _build_chat_intent_payload(
                        "DRIVER_UPDATE",
                        intent_result.get("confidence", 0),
                        f"Driver #{driver_id} was not found.",
                        INTENT_WORKFLOWS["DRIVER_UPDATE"],
                        "driver_management.not_found",
                    ),
                    "chat.intent_driver_not_found",
                )

            message_lower = raw_message.lower()
            next_status = None
            if any(keyword in message_lower for keyword in {"available", "ready", "online"}):
                next_status = "available"
            elif any(keyword in message_lower for keyword in {"busy", "inactive", "offline", "unavailable"}):
                next_status = "inactive"

            if next_status:
                _update_driver_record(driver_id, {"status": next_status})
                driver_row = _get_driver_record_by_id(driver_id)

            driver_payload = _serialize_driver_row(driver_row)
            reply = f"Driver #{driver_id} is currently {driver_payload.get('status') or 'unknown'}."
            if next_status:
                reply = f"Driver #{driver_id} status updated to {next_status}."

            return _save_chat_history_before_return(
                user_state,
                raw_message,
                _build_chat_intent_payload(
                    "DRIVER_UPDATE",
                    intent_result.get("confidence", 0),
                    reply,
                    INTENT_WORKFLOWS["DRIVER_UPDATE"],
                    "driver_management.updated" if next_status else "driver_management.summary",
                    data={"driver": driver_payload},
                ),
                "chat.intent_driver_update",
            )

        if public_intent == "TRACK_SHIPMENT":
            reference_id = _extract_reference_id(raw_message)
            shipment_row = _fetch_shipment_record_by_id(reference_id) if reference_id is not None else None
            booking_row = _fetch_booking_record_by_id(reference_id) if reference_id is not None and shipment_row is None else None

            if shipment_row:
                shipment_payload = _serialize_shipment_row(shipment_row)
                return _save_chat_history_before_return(
                    user_state,
                    raw_message,
                    _build_chat_intent_payload(
                        "TRACK_SHIPMENT",
                        intent_result.get("confidence", 0),
                        f"Tracking opened for shipment #{shipment_payload.get('id')}. Status: {shipment_payload.get('shipment_status') or 'pending' }.",
                        INTENT_WORKFLOWS["TRACK_SHIPMENT"],
                        "tracking.shipment_lookup",
                        data={"shipment": shipment_payload, "timeline": _fetch_shipment_timeline(reference_id)},
                    ),
                    "chat.intent_tracking_shipment",
                )

            if booking_row:
                booking_payload = _serialize_booking_row(booking_row)
                return _save_chat_history_before_return(
                    user_state,
                    raw_message,
                    _build_chat_intent_payload(
                        "TRACK_SHIPMENT",
                        intent_result.get("confidence", 0),
                        f"Booking #{booking_payload.get('id')} is {booking_payload.get('booking_status') or 'pending'}. Create the shipment to unlock live tracking.",
                        INTENT_WORKFLOWS["TRACK_SHIPMENT"],
                        "tracking.booking_lookup",
                        data={"booking": booking_payload},
                    ),
                    "chat.intent_tracking_booking",
                )

            return _save_chat_history_before_return(
                user_state,
                raw_message,
                _build_chat_intent_payload(
                    "TRACK_SHIPMENT",
                    intent_result.get("confidence", 0),
                    "Share your shipment ID or booking reference and I will open live tracking.",
                    INTENT_WORKFLOWS["TRACK_SHIPMENT"],
                    "tracking.request_reference",
                    suggestions=["Share shipment ID", "Share booking reference"],
                ),
                "chat.intent_tracking_request",
            )

        if public_intent == "GET_PRICE_ESTIMATE":
            try:
                verify_jwt_in_request(optional=True)
                claims = get_jwt()
            except Exception:
                claims = {}

            role = str((claims or {}).get("role") or "").strip().lower()
            if role not in {"customer"}:
                return _save_chat_history_before_return(
                    user_state,
                    raw_message,
                    _build_chat_intent_payload(
                        "GET_PRICE_ESTIMATE",
                        intent_result.get("confidence", 0),
                        "Price estimates are available from the customer booking flow.",
                        INTENT_WORKFLOWS["GET_PRICE_ESTIMATE"],
                        "pricing.access_restricted",
                        suggestions=_get_role_chat_policy(role).get("quick_actions", []),
                        status="forbidden",
                        http_status=403,
                    ),
                    "chat.intent_price_forbidden",
                )

            extracted_booking = get_gemini_response(raw_message)
            source = str(extracted_booking.get("source") or "").strip()
            destination = str(extracted_booking.get("destination") or "").strip()
            tons_text = str(extracted_booking.get("tons") or "").strip()
            truck_type_code = _truck_type_from_tons(tons_text)

            if source and destination and tons_text and truck_type_code:
                truck_type_label = _canonical_truck_type_label(truck_type_code)
                distance_km, estimated_price = _estimate_distance_and_price(source, destination, truck_type_code)
                eta_hours = _estimate_eta_hours(distance_km, truck_type_code)
                reply = _build_fare_estimation_reply(source, destination, tons_text, truck_type_label, distance_km, estimated_price, eta_hours)
                return _save_chat_history_before_return(
                    user_state,
                    raw_message,
                    _build_chat_intent_payload(
                        "GET_PRICE_ESTIMATE",
                        intent_result.get("confidence", 0),
                        reply,
                        INTENT_WORKFLOWS["GET_PRICE_ESTIMATE"],
                        "pricing.estimate",
                        data={
                            "pickup_location": source,
                            "drop_location": destination,
                            "load_weight": float(tons_text),
                            "truck_type": truck_type_label,
                            "distance": int(round(float(distance_km))),
                            "estimated_fare": int(round(float(estimated_price))),
                            "eta_hours": eta_hours,
                        },
                    ),
                    "chat.intent_price_estimate",
                )

            return _save_chat_history_before_return(
                user_state,
                raw_message,
                _build_chat_intent_payload(
                    "GET_PRICE_ESTIMATE",
                    intent_result.get("confidence", 0),
                    "Share pickup, drop, and load weight to get an instant price estimate.",
                    INTENT_WORKFLOWS["GET_PRICE_ESTIMATE"],
                    "pricing.request_details",
                    suggestions=["Share pickup location", "Share drop location", "Share weight in tons"],
                ),
                "chat.intent_price_prompt",
            )

        if public_intent == "MAKE_PAYMENT":
            try:
                verify_jwt_in_request(optional=True)
                claims = get_jwt()
            except Exception:
                claims = {}

            role = str((claims or {}).get("role") or "").strip().lower()
            if role not in {"customer"}:
                return _save_chat_history_before_return(
                    user_state,
                    raw_message,
                    _build_chat_intent_payload(
                        "MAKE_PAYMENT",
                        intent_result.get("confidence", 0),
                        "Payment workflows are available to customers only.",
                        INTENT_WORKFLOWS["MAKE_PAYMENT"],
                        "payments.access_restricted",
                        suggestions=_get_role_chat_policy(role).get("quick_actions", []),
                        status="forbidden",
                        http_status=403,
                    ),
                    "chat.intent_payment_forbidden",
                )

            reference_id = _extract_reference_id(raw_message)
            booking_row = _fetch_booking_record_by_id(reference_id) if reference_id is not None else None
            shipment_row = _fetch_shipment_record_by_id(reference_id) if reference_id is not None and booking_row is None else None
            payment_type = "advance"

            if booking_row:
                amount_due = _resolve_payment_amount(booking_row, payment_type, reference_id)
                if amount_due > 0:
                    order_data = _create_razorpay_order(amount_due, reference_id)
                    _insert_payment_record(reference_id, order_data["id"], amount_due, "created", payment_type=payment_type, session_id=user_state.get("session_id"))
                    return _save_chat_history_before_return(
                        user_state,
                        raw_message,
                        _build_chat_intent_payload(
                            "MAKE_PAYMENT",
                            intent_result.get("confidence", 0),
                            f"Payment link created for booking #{reference_id}. Amount due: ₹{int(round(float(amount_due))):,}.",
                            INTENT_WORKFLOWS["MAKE_PAYMENT"],
                            "payments.order_created",
                            data={
                                "booking_id": reference_id,
                                "amount_due": int(round(float(amount_due))),
                                "amount": int(order_data.get("amount", amount_due * 100)),
                                "currency": order_data.get("currency", RAZORPAY_CURRENCY),
                                "key_id": RAZORPAY_KEY_ID,
                                "order_id": order_data["id"],
                            },
                        ),
                        "chat.intent_payment_booking",
                    )

            if shipment_row:
                amount_due = _resolve_shipment_payment_amount(shipment_row, payment_type, reference_id)
                if amount_due > 0:
                    order_data = _create_razorpay_order(amount_due, reference_id)
                    _insert_payment_record(None, order_data["id"], amount_due, "created", payment_type=payment_type, shipment_id=reference_id, session_id=user_state.get("session_id"))
                    return _save_chat_history_before_return(
                        user_state,
                        raw_message,
                        _build_chat_intent_payload(
                            "MAKE_PAYMENT",
                            intent_result.get("confidence", 0),
                            f"Payment link created for shipment #{reference_id}. Amount due: ₹{int(round(float(amount_due))):,}.",
                            INTENT_WORKFLOWS["MAKE_PAYMENT"],
                            "payments.order_created",
                            data={
                                "shipment_id": reference_id,
                                "amount_due": int(round(float(amount_due))),
                                "amount": int(order_data.get("amount", amount_due * 100)),
                                "currency": order_data.get("currency", RAZORPAY_CURRENCY),
                                "key_id": RAZORPAY_KEY_ID,
                                "order_id": order_data["id"],
                            },
                        ),
                        "chat.intent_payment_shipment",
                    )

            return _save_chat_history_before_return(
                user_state,
                raw_message,
                _build_chat_intent_payload(
                    "MAKE_PAYMENT",
                    intent_result.get("confidence", 0),
                    "Share your booking or shipment reference and I will prepare the payment workflow.",
                    INTENT_WORKFLOWS["MAKE_PAYMENT"],
                    "payments.request_reference",
                    suggestions=["Share booking reference", "Share shipment reference"],
                ),
                "chat.intent_payment_request",
            )

        if public_intent == "CUSTOMER_SUPPORT":
            support_reply = get_chat_reply(
                f"You are SKDLS Transportations support. Answer clearly and briefly: {raw_message}"
            )
            return _save_chat_history_before_return(
                user_state,
                raw_message,
                _build_chat_intent_payload(
                    "CUSTOMER_SUPPORT",
                    intent_result.get("confidence", 0),
                    support_reply,
                    INTENT_WORKFLOWS["CUSTOMER_SUPPORT"],
                    "support.general",
                    suggestions=["Ask about booking", "Ask about tracking", "Ask about payment"],
                ),
                "chat.intent_support",
            )

        extracted_booking = get_gemini_response(raw_message)
        print(f"[chat] extracted booking JSON: {extracted_booking}")
        booking_fields_present = any(
            str(extracted_booking.get(field, "") or "").strip()
            for field in BOOKING_FIELDS
        )

        if booking_fields_present:
            print("[chat] selected branch: booking-request")
            _set_booking_mode(user_state, True, source="gemini_extraction")
            user_state["onboarding_shown"] = True
            _set_active_intent(user_state, "booking", source="gemini_extraction")

            stored_booking = user_state.get("booking", dict(EMPTY_BOOKING_JSON))
            booking_details = _merge_booking_details(stored_booking, extracted_booking)
            user_state["booking"] = booking_details

            # If Gemini provided a tyre type, validate it immediately and prompt if invalid
            provided_tyre = booking_details.get("tyre_type") or booking_details.get("truck_type")
            if provided_tyre and not _validate_tyre_type(provided_tyre):
                user_state["requested_field"] = "tyre_type"
                user_state["stage"] = "collecting_tyre_type"
                return _save_chat_history_before_return(user_state, raw_message, jsonify({"reply": _request_prompt_for_field("tyre_type")}), "chat.collecting_tyre_type_prompt")

            _log_chat_state(user_state, note="booking_request_detected")
            return _save_chat_history_before_return(user_state, raw_message, _advance_request_flow(user_state), "chat.gemini_booking_request")

        print("[chat] selected branch: conversation")

        if user_state.get("booking_mode"):
            _set_active_intent(user_state, "booking", source="booking_mode_continue")

        if user_state.get("stage") == "idle" and any(keyword in message for keyword in ["truck", "lorry"]):
            user_state["stage"] = "collecting_source"
            user_state["requested_field"] = "source"
            _set_booking_mode(user_state, True, source="generic_transport_keyword")
            user_state["onboarding_shown"] = True
            _set_active_intent(user_state, "booking", source="generic_transport_keyword")
            return _save_chat_history_before_return(user_state, raw_message, jsonify({"reply": "Please provide the source city."}), "chat.generic_transport_keyword")

        if user_state.get("stage") == "idle" and any(keyword in message for keyword in ["car", "bike", "motorcycle", "van", "bus", "auto", "scooter"]):
            _set_active_intent(user_state, "general", source="unsupported_vehicle")
            return _save_chat_history_before_return(user_state, raw_message, jsonify({"reply": "Sorry, we only provide truck/lorry transportation services"}), "chat.unsupported_vehicle")

        # Fallback: Use Gemini intent detection for general conversations
        intent_result = _detect_user_intent(raw_message, "idle", db_context=db_context)
        intent = intent_result.get("intent", "unrelated")
        print(f"[gemini][intent] fallback_general user={user_id} intent={intent}")
        
        _set_active_intent(user_state, "general", source="chat_reply")
        return _save_chat_history_before_return(user_state, raw_message, jsonify({
            "reply": get_chat_reply(
                f"You are SKDLS Transportations' chatbot. Respond helpfully to this user message: {raw_message}"
            )
        }), "chat.generic_reply")
    except Exception:
        logger.exception("[chat][fatal]")
        fallback_data = request.get_json(silent=True) or {}
        fallback_user_id = str(fallback_data.get("user_id", "default_user"))
        fallback_state = user_data.get(fallback_user_id, {"user_id": fallback_user_id})
        fallback_message = str(fallback_data.get("message", "") or "")
        return _save_chat_history_before_return(
            fallback_state,
            fallback_message,
            (jsonify({"reply": "Sorry, something went wrong. Please try again."}), 200),
            "chat.fatal",
        )


@app.before_request
def _monitoring_before_request():
    request._monitoring_started_at = time.perf_counter()


@app.after_request
def _monitoring_after_request(response):
    started_at = getattr(request, "_monitoring_started_at", None)
    if started_at is not None:
        duration_ms = (time.perf_counter() - started_at) * 1000.0
        route_label = getattr(getattr(request, "url_rule", None), "rule", None) or request.endpoint or request.path or "unknown"
        method = str(request.method or "GET").upper()
        status_code = int(getattr(response, "status_code", 500) or 500)

        sample = {
            "timestamp": time.time(),
            "method": method,
            "route": route_label,
            "path": request.path,
            "status_code": status_code,
            "duration_ms": round(duration_ms, 2),
            "status_class": _monitoring_status_class(status_code),
        }

        with MONITORING_LOCK:
            MONITORING_REQUEST_SAMPLES.append(sample)
            MONITORING_METHOD_COUNTS[method] += 1
            MONITORING_STATUS_CLASS_COUNTS[sample["status_class"]] += 1
            MONITORING_ROUTE_COUNTS[route_label] += 1
            MONITORING_ROUTE_LATENCY[route_label] += duration_ms
            if status_code >= 500:
                MONITORING_ROUTE_ERRORS[route_label] += 1
                MONITORING_ERROR_EVENTS.append(sample)
                _broadcast_monitoring_alert(
                    f"{method} {route_label} returned {status_code}",
                    level="critical",
                    data=sample,
                )

        response.headers["X-Response-Time-ms"] = f"{duration_ms:.2f}"

    return response


@app.after_request
def _auto_save_chat(response):
    """Chat history is saved explicitly in `/chat` before every return."""
    return response


@app.route("/chat/welcome", methods=["GET"])
def chat_welcome():
    try:
        return jsonify({"reply": "Welcome to SKDLS Transportations. Are you looking to book a truck today?"})
    except Exception:
        logger.exception("[chat][exception] Error building welcome response")
        return jsonify({"reply": "Welcome. How can I help?"}), 200


@app.route("/api/health", methods=["GET"])
@app.route("/health", methods=["GET"])
def health_check():
    return jsonify(
        {
            "status": "ok",
            "service": "skdls-transport-ai",
            "environment": os.getenv("FLASK_ENV", "production"),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        }
    ), 200


@app.route("/api/ready", methods=["GET"])
@app.route("/ready", methods=["GET"])
def readiness_check():
    try:
        validate_db_environment()
        if not test_db_connection():
            return jsonify({"status": "degraded", "service": "skdls-transport-ai", "database": "unreachable"}), 503

        return jsonify(
            {
                "status": "ready",
                "service": "skdls-transport-ai",
                "database": "ok",
                "environment": os.getenv("FLASK_ENV", "production"),
            }
        ), 200
    except Exception as error:
        logger.exception(f"[health][readiness][error] {error}")
        return jsonify({"status": "unhealthy", "service": "skdls-transport-ai", "error": str(error)}), 503


if __name__ == "__main__":
    try:
        validate_production_environment()
        validate_db_environment()
    except RuntimeError as error:
        logger.error(f"[startup][error] {error}")
        raise SystemExit(1)

    logger.info(f"[startup][database] host={DB_CONFIG.get('host')} user={DB_CONFIG.get('user')} database={DB_CONFIG.get('database')}")

    # Initialize database schema (creates tables if they don't exist)
    try:
        initialize_schema()
    except Exception as error:
        logger.exception(f"[startup][error] Failed to initialize database schema: {error}")
        raise SystemExit(1)

    # Test DB connection early and warn if unavailable
    try:
        test_conn = get_db_connection()
        test_cursor = test_conn.cursor()
        logger.info(f"[database][startup] Connection autocommit state: {test_conn.autocommit}")
        logger.info("[database][startup] cursor.execute() reached for SELECT DATABASE()")
        test_cursor.execute("SELECT DATABASE()")
        db_name = test_cursor.fetchone()[0]
        logger.info(f"[database][startup] active_database={db_name}")
        if str(db_name or "").strip() != "transport_system":
            logger.error(f"[startup][error] Expected transport_system schema but found {db_name!r}")
            raise SystemExit(1)
        logger.info("[database][startup] cursor.execute() reached for SELECT 1")
        test_cursor.execute("SELECT 1")
        test_cursor.fetchone()
        test_cursor.execute("SELECT VERSION()")
        version = test_cursor.fetchone()[0]
        logger.info(f"[database][startup] mysql_version={version}")
        test_cursor.close()
        test_conn.close()
        logger.info("[startup][database] DB connection verified")
    except Exception as error:
        logger.exception(f"[startup][error] Database connection verification failed: {error}")
        raise SystemExit(1)

    if not test_db_connection():
        logger.warning("[startup][warning] Database appears unreachable. App will continue, but DB operations may fail.")

    if ENABLE_GPS_SIMULATOR and gps_simulator is not None:
        logger.info("[gps] Starting GPS simulator")
        gps_simulator.start()
    else:
        logger.info("[gps] GPS simulator disabled; using live GPS ingest only")
    validate_database_schema()
    logger.info("[startup] Flask application starting")
    socketio.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        debug=False,
        allow_unsafe_werkzeug=True,
    )

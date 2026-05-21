import os
import json
import re
import atexit
import traceback
import math
import uuid
import time
import random

from flask import Flask, request, jsonify
from flask_cors import CORS
from mysql.connector import Error
from dotenv import load_dotenv
import google.generativeai as genai
import requests
from gps_simulator import GPSSimulator
from db import DB_CONFIG, get_db_connection, test_db_connection, save_chat_history, save_conversation_json, validate_db_environment, initialize_schema
from config import ProductionConfig
from logging_utils import logger, setup_logger, log_print

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

logger = setup_logger()
print = log_print

app = Flask(__name__)
app.config.from_object(ProductionConfig)
app.config['ENV'] = os.getenv('FLASK_ENV', 'production')
app.config['DEBUG'] = False
CORS(app)

TABLE_MAP = {
    "12": "lorries_12_tyre",
    "14": "lorries_14_tyre",
    "16": "lorries_16_tyre",
}

# Initialize GPS Simulator
gps_simulator = GPSSimulator(DB_CONFIG, TABLE_MAP)

# Ensure simulator stops gracefully on app exit
atexit.register(gps_simulator.stop)

BOOKINGS_TABLE = "bookings"

REQUIRED_SCHEMA_COLUMNS = {
    BOOKINGS_TABLE: {
        "id",
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
        "booking_status",
        "lorry_number",
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

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    genai.configure(api_key="")


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
    if GEMINI_MODEL is None and GEMINI_API_KEY:
        GEMINI_MODEL = genai.GenerativeModel(GEMINI_MODEL_NAME)
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


def _detect_user_intent(message, current_stage, booking_context=None):
    """Detect user intent using Gemini. Returns intent + conversational reply in one call.
    
    Implements caching and fast-path logic to optimize response time.
    
    Args:
        message: User's message
        current_stage: Current booking stage (e.g. "collecting_source")
        booking_context: Dict with source, destination, tons, tyre, distance, price, stage
    
    Returns:
        {
            "intent": "continue_booking|cancel_booking|pricing_question|booking_question|tracking_question|info_query|greeting|unrelated",
            "confidence": 0.0-1.0,
            "reply": "conversational reply"
        }
    """
    booking_context = booking_context or {}
    
    # STEP 1: Check cache
    cache_key = _get_cache_key(current_stage, message)
    cached_result = _get_cached_intent(cache_key)
    if cached_result is not None:
        return cached_result
    
    # STEP 2: Fast-path for simple inputs (no Gemini needed)
    if _should_use_fast_path(message, current_stage):
        print(f"[gemini][fast_path] skipping Gemini for deterministic flow")
        return {"intent": "unrelated", "confidence": 1.0, "reply": ""}
    
    # STEP 3: Use Gemini for intent classification + reply in one call
    if not GEMINI_API_KEY:
        print(f"[gemini][intent] Gemini disabled")
        return {"intent": "unrelated", "confidence": 0, "reply": ""}
    
    try:
        model = _get_gemini_model()
        if not model:
            return {"intent": "unrelated", "confidence": 0, "reply": ""}
        
        # Build context string for Gemini
        context_parts = []
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
        
        prompt = f"""User Stage: {current_stage}
Booking Context: {context_str}
User Message: {message}

Classify the user's intent and provide a brief, natural conversational reply.

RESPOND ONLY WITH VALID JSON (no markdown, no explanation):
{{
  "intent": "continue_booking OR cancel_booking OR pricing_question OR booking_question OR tracking_question OR info_query OR greeting OR unrelated",
  "confidence": 0.85,
  "reply": "brief natural response (1-2 sentences max)"
}}

RULES:
- continue_booking: explicit yes/okay/proceed/continue/let's go/book it
- cancel_booking: explicit cancel/stop/decline/no thanks/not interested/never mind
- pricing_question: cost/price/rate/fee/charge/expensive/discount/affordable
- booking_question: process/fields/requirements/insurance/truck details/driver
- tracking_question: track/location/driver/arrival/ETA/where/when
- info_query: other how/what/when/why/where questions
- greeting: hi/hello/hey/good morning
- unrelated: off-topic, jokes, random chat

Reply MUST be conversational, friendly, and context-aware. DO NOT make the user re-enter data.""".strip()

        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=120,
                temperature=0.7,
            )
        )
        
        reply_text = getattr(response, "text", "").strip()
        
        # Parse JSON response
        try:
            result = json.loads(reply_text)
            intent = str(result.get("intent", "unrelated")).strip().lower()
            confidence = float(result.get("confidence", 0))
            reply = str(result.get("reply", "")).strip()
            
            # Validate intent
            valid_intents = {
                "continue_booking", "cancel_booking", "pricing_question", 
                "booking_question", "tracking_question", "info_query", 
                "greeting", "unrelated"
            }
            if intent not in valid_intents:
                intent = "unrelated"
            
            # Safety: Use fallback if low confidence
            if confidence < 0.7:
                print(f"[gemini][fallback] low_confidence conf={confidence:.2f}")
                result = {"intent": "unrelated", "confidence": 0, "reply": ""}
            
            print(f"[gemini][intent] intent={intent} confidence={confidence:.2f} stage={current_stage}")
            
            # Cache result
            _set_cached_intent(cache_key, result)
            return result
            
        except json.JSONDecodeError as e:
            print(f"[gemini][intent] parse_error {e} response={reply_text!r}")
            return {"intent": "unrelated", "confidence": 0, "reply": ""}
    
    except Exception as e:
        logger.exception(f"[gemini][intent] exception {e}")
        return {"intent": "unrelated", "confidence": 0, "reply": ""}


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
    return last_part or cleaned


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


def _detect_user_intent(message, current_stage, booking_context=None):
    """Detect user intent using Gemini. Returns structured JSON with intent classification.
    
    Intent types:
    - continue_booking: User wants to proceed with booking (yes, proceed, etc)
    - cancel_booking: User explicitly wants to cancel (no, cancel, stop, etc)
    - info_query: User asking for information (how, what, when, why, where)
    - pricing_question: Questions about cost, price, rates, fees
    - tracking_question: Questions about vehicle tracking, driver, arrival
    - booking_question: Questions about booking process, fields, requirements
    - greeting: Hi, hello, general greeting
    - unrelated: Complete off-topic conversation
    """
    if not GEMINI_API_KEY:
        print(f"[gemini][intent] disabled message={message!r} current_stage={current_stage}")
        return {"intent": "unrelated", "confidence": 0}
    
    try:
        model = _get_gemini_model()
        if not model:
            return {"intent": "unrelated", "confidence": 0, "reasoning": "Gemini unavailable"}

        booking_context = booking_context or {}
        prompt = f"""Classify the user's message intent. Current booking stage: {current_stage}

User message: {message}
Booking context: {json.dumps(booking_context, ensure_ascii=False)}

Return ONLY valid JSON with these exact keys:
{{
  "intent": "continue_booking|cancel_booking|info_query|pricing_question|tracking_question|booking_question|greeting|unrelated",
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation"
}}

Rules:
- continue_booking: explicit yes, okay, proceed, continue, proceed to payment, let's go, book it, confirm
- cancel_booking: explicit no, cancel, stop, decline, not interested, never mind, not right now (if stage is awaiting confirmation)
- pricing_question: cost, price, rate, charge, fee, expensive, affordable, discount
- tracking_question: track, driver, location, status, where is, when will arrive, ETA
- booking_question: fields required, how does booking work, requirements
- info_query: other how/what/when/why/where questions
- greeting: hi, hello, hey, good morning, etc
- unrelated: anything else like jokes, random chat, etc

Return JSON only. No markdown or explanation.""".strip()

        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(max_output_tokens=150, temperature=0.2),
            request_options={"timeout": GEMINI_TIMEOUT_SECONDS},
        )
        reply = _clean_json_text(getattr(response, "text", ""))

        if not reply:
            print(f"[gemini][intent] empty_response message={message!r}")
            return {"intent": "unrelated", "confidence": 0, "reasoning": "empty_response"}

        parsed = _parse_gemini_json(reply, {"intent": "unrelated", "confidence": 0, "reasoning": "parse_error"}, "[gemini][parse_error]")
        if not isinstance(parsed, dict):
            return {"intent": "unrelated", "confidence": 0, "reasoning": "parse_error"}

        intent = str(parsed.get("intent", "unrelated")).strip()
        confidence = float(parsed.get("confidence", 0))
        reasoning = str(parsed.get("reasoning", "")).strip()
        
        # Validate intent is in allowed list
        valid_intents = {"continue_booking", "cancel_booking", "info_query", "pricing_question", "tracking_question", "booking_question", "greeting", "unrelated"}
        if intent not in valid_intents:
            intent = "unrelated"
        
        print(f"[gemini][intent] intent={intent} confidence={confidence:.2f} stage={current_stage} reasoning={reasoning!r}")
        return {"intent": intent, "confidence": confidence, "reasoning": reasoning}
    except TimeoutError as error:
        print(f"[gemini][timeout] intent stage={current_stage} message={message!r} error={error}")
        return {"intent": "unrelated", "confidence": 0, "reasoning": "timeout"}
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        print(f"[gemini][parse_error] intent message={message!r} error={error}")
        return {"intent": "unrelated", "confidence": 0, "reasoning": "parse_error"}
    except Exception as error:
        logger.exception(f"[gemini][intent] exception {error}")
        return {"intent": "unrelated", "confidence": 0, "reasoning": "exception"}


def _generate_contextual_reply(message, current_stage, booking_details):
    """Generate a natural conversational reply from Gemini while preserving booking context.
    
    Used for answering questions during booking stages without breaking the flow.
    Returns a conversational reply string.
    """
    if not GEMINI_API_KEY:
        print(f"[gemini][conversation] disabled stage={current_stage}")
        return ""
    
    try:
        source = str(booking_details.get("source", "") or "").strip()
        destination = str(booking_details.get("destination", "") or "").strip()
        tons = str(booking_details.get("tons", "") or "").strip()
        
        context_str = f"Source: {source}, Destination: {destination}, Load: {tons} tons" if any([source, destination, tons]) else "Starting new booking"
        
        model = _get_gemini_model()
        if not model:
            return ""
        prompt = f"""You are SKDLS Transportations' helpful chatbot. Respond naturally and briefly to this user question during their truck booking process.

Current booking: {context_str}
Current stage: {current_stage}
User message: {message}

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


def _save_chat_history_before_return(user_state, user_message, response_obj, return_path):
    user_state = user_state or {}
    user_id = str(user_state.get("user_id") or "default_user")
    session_id = _get_or_create_chat_session_id({"session_id": user_state.get("session_id", "")}, user_state=user_state, user_id=user_id)
    conversation_id = _get_or_create_conversation_id(user_state, session_id, user_id=user_id)
    bot_reply = _extract_bot_reply_for_history(response_obj)
    conversation = _append_conversation_turn(user_state, user_message, bot_reply)
    conversation_json = user_state.get("conversation_json") or json.dumps(conversation, ensure_ascii=False)

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

    # Try to generate conversational summary if booking details provided
    if booking_details:
        conversational_summary = _build_conversational_booking_summary(
            booking_details, distance_km, price_value, truck_type_label
        )
        if conversational_summary:
            return f"{conversational_summary}\n\nWould you like to continue to payment?"
    
    # Fallback to standard format
    return (
        f"Estimated Distance: {int(round(float(distance_km)))} km\n"
        f"Recommended Truck: {truck_type_label}\n"
        f"Estimated Freight Cost: ₹{total_price}\n"
        f"Advance Token Amount (80%): ₹{token_amount}\n\n"
        "Would you like to continue to payment?"
    )


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


def _handle_booking_stage(user_state, raw_message, data):
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
    intent_result = _detect_user_intent(raw_message, stage, booking_context)
    intent = intent_result.get("intent", "unrelated")
    
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
                response = _handle_direct_booking(user_state.get("user_id", "default_user"), raw_message, final_data, booking)
                if isinstance(response, tuple):
                    return response

                user_state["stage"] = "booking_confirmed"
                return response
            except Exception as error:
                logger.exception(f"[booking][error] Booking could not be completed: {error}")
                return jsonify({"reply": f"Booking could not be completed: {error}", "error": str(error)}), 500
        
        # Intent is an info/question query during payment confirmation - answer conversationally
        if intent in {"info_query", "pricing_question", "booking_question", "tracking_question"}:
            contextual_reply = _generate_contextual_reply(raw_message, stage, booking)
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
        
        # If it's a question during location collection, answer it
        if intent in {"info_query", "pricing_question", "booking_question", "tracking_question"}:
            contextual_reply = _generate_contextual_reply(raw_message, stage, booking)
            if contextual_reply:
                print(f"[booking][stage] user={user_state.get('user_id')} stage={stage} action=contextual_reply intent={intent}")
                return jsonify({"reply": contextual_reply})

    # Handle explicit cancellation at any stage
    if intent == "cancel_booking":
        print(f"[booking][stage] user={user_state.get('user_id')} stage={stage} action=cancel_explicit")
        reset_session(user_state=user_state)
        return jsonify({"reply": "Booking cancelled. You can start a new transport request anytime."})

    if stage == "collecting_tyre_type":
        # accept inputs like '12', '12 tyre', '14', '16-tyre'
        if not _validate_tyre_type(raw_message):
            count = _increment_validation_attempt(user_state, "tyre_type")
            if count >= 5:
                _clear_booking_field_on_max_retry(user_state, "tyre_type")
                return jsonify({"reply": "Too many invalid attempts for tyre type. Booking has been reset. You can start again."})
            if count >= 3:
                return jsonify({"reply": "Invalid tyre type. Example: 12 tyre. You can type 'restart' to start over."})
            return jsonify({"reply": "Invalid tyre type. Please reply with one of: 12 tyre, 14 tyre, or 16 tyre."})

        code = _normalize_tyre_input(raw_message)
        booking["truck_type"] = code
        booking["tyre_type"] = f"{code} tyre"
        user_state["booking"] = booking
        _reset_validation_attempts_for_field(user_state, "tyre_type")
        print(f"[validation][passed] tyre_type set: {booking['tyre_type']}")
        return _advance_request_flow(user_state)

    # awaiting booking confirmation (after a greeting)
    if stage == "awaiting_booking_confirmation":
        if intent == "cancel_booking" or is_negative_confirmation(raw_message):
            reset_session(user_state=user_state)
            return jsonify({"reply": _clean_greeting_reply()})
        if intent == "continue_booking" or is_positive_confirmation(raw_message):
            _start_new_booking(user_state)
            return jsonify({"reply": _request_prompt_for_field("source")})
        # Not a clear yes/no, assume they want to proceed
        _start_new_booking(user_state)
        return jsonify({"reply": _request_prompt_for_field("source")})

    if stage == "collecting_source":
        if not _validate_city(raw_message):
            count = _increment_validation_attempt(user_state, "source")
            if count >= 5:
                _clear_booking_field_on_max_retry(user_state, "source")
                return jsonify({"reply": "Too many invalid attempts for source city. Booking has been reset. You can start again."})
            
            # If it's a question, answer it conversationally
            if intent in {"info_query", "pricing_question", "booking_question", "tracking_question"}:
                contextual_reply = _generate_contextual_reply(raw_message, stage, booking)
                if contextual_reply:
                    print(f"[booking][stage] user={user_state.get('user_id')} stage={stage} action=contextual_reply intent={intent}")
                    return jsonify({"reply": contextual_reply})
            
            if count >= 3:
                return jsonify({"reply": "Invalid source city. Example: 'Hyderabad'. You can type 'restart' to start over."})
            return jsonify({"reply": "Invalid source city. Please provide only alphabetic city or location name (e.g., 'Hyderabad')."})

        booking["source"] = _normalize_booking_text(raw_message)
        user_state["booking"] = booking
        _reset_validation_attempts_for_field(user_state, "source")
        print(f"[validation][passed] source set: {booking['source']!r}")
        return _advance_request_flow(user_state)

    if stage == "collecting_destination":
        requested_field = str(user_state.get("requested_field", "") or "")

        if requested_field == "tons":
            if not _validate_tons(raw_message):
                count = _increment_validation_attempt(user_state, "tons")
                if count >= 5:
                    _clear_booking_field_on_max_retry(user_state, "tons")
                    return jsonify({"reply": "Too many invalid attempts for tons. Booking has been reset. You can start again."})
                
                # If it's a question, answer it conversationally
                if intent in {"info_query", "pricing_question", "booking_question", "tracking_question"}:
                    contextual_reply = _generate_contextual_reply(raw_message, stage, booking)
                    if contextual_reply:
                        print(f"[booking][stage] user={user_state.get('user_id')} stage={stage} action=contextual_reply intent={intent}")
                        return jsonify({"reply": contextual_reply})
                
                if count >= 3:
                    return jsonify({"reply": "Invalid tons. Example: 12 (must be between 1 and 100). You can type 'restart' to start over."})
                return jsonify({"reply": "Invalid tons. Please enter a numeric tons/load weight between 1 and 100."})

            # store normalized integer string
            booking["tons"] = str(int(re.search(r"\d+", raw_message.replace(",", "")).group(0)))
            _reset_validation_attempts_for_field(user_state, "tons")
        else:
            if not _validate_city(raw_message):
                count = _increment_validation_attempt(user_state, "destination")
                if count >= 5:
                    _clear_booking_field_on_max_retry(user_state, "destination")
                    return jsonify({"reply": "Too many invalid attempts for destination city. Booking has been reset. You can start again."})
                
                # If it's a question, answer it conversationally
                if intent in {"info_query", "pricing_question", "booking_question", "tracking_question"}:
                    contextual_reply = _generate_contextual_reply(raw_message, stage, booking)
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
            if intent in {"info_query", "pricing_question", "booking_question", "tracking_question"}:
                contextual_reply = _generate_contextual_reply(raw_message, stage, booking)
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
                contextual_reply = _generate_contextual_reply(raw_message, stage, booking)
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
            "distance": int(round(distance_int)),
            "price": int(round(price_int)),
            "lorryType": truck_type_label,
            "token_amount": int(round(price_int * 0.8)),
        })

    return None


def _handle_direct_booking(user_id, raw_message, data, booking_details):
    try:
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

        user_data.pop(user_id, None)

        return jsonify(
            {
                "status": "confirmed",
                "lorryType": _canonical_truck_type_label(truck_type),
                "distance": distance_int,
                "price": price_int,
                "lorry_number": selected_lorry["lorry_number"],
                "reply": _build_booking_confirmation(selected_lorry["lorry_number"]),
            }
        )
    except (RuntimeError, ValueError) as error:
        message = str(error)
        reset_session(user_id=user_id)
        status_code = 400 if "Coordinates are malformed" in message else 500
        return jsonify({"error": message, "reply": message}), status_code


def _build_booking_confirmation(lorry_number):
    print(f"[booking][tyre_recommendation] booking_confirmed lorry={lorry_number}")
    
    # Generate driver/truck assignment
    assignment = _generate_driver_truck_assignment()
    
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

@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json() or {}
        user_id = data.get("user_id", "default_user")  # Accept user_id from client
        raw_message = str(data.get("message", "")).strip()
        message = raw_message.lower()

        user_state = _get_chat_state(user_id)
        user_state["user_id"] = user_id
        _log_chat_state(user_state, note="chat_entry")

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
            stage_response = _handle_booking_stage(user_state, raw_message, data)
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
        intent_result = _detect_user_intent(raw_message, "idle")
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


if __name__ == "__main__":
    try:
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

    logger.info("[gps] Starting GPS simulator")
    validate_database_schema()
    gps_simulator.start()
    logger.info("[startup] Flask application starting")
    app.run(debug=False)
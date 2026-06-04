import json
from datetime import datetime, timedelta

from mysql.connector import Error

from db import get_db_connection, get_user_preferences


def _parse_json_field(value):
    if value in (None, "", b""):
        return {}

    if isinstance(value, dict):
        return value

    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except Exception:
            return {}

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    return {}


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
        "payment_status": str(row.get("payment_status") or row.get("status") or "created").strip() or "created",
        "status": str(row.get("status") or row.get("payment_status") or "created").strip() or "created",
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
        "created_at": row.get("created_at").isoformat(sep=" ", timespec="seconds") if row.get("created_at") else None,
    }


def _serialize_status_log_row(row):
    if not row:
        return None

    metadata = _parse_json_field(row.get("metadata_json"))
    return {
        "id": int(row["id"]),
        "status": str(row.get("status") or "pending").strip() or "pending",
        "note": str(row.get("note") or "").strip(),
        "location": str(row.get("location") or "").strip(),
        "actor_role": str(row.get("actor_role") or "system").strip() or "system",
        "metadata": metadata,
        "created_at": row.get("created_at").isoformat(sep=" ", timespec="seconds") if row.get("created_at") else None,
    }


def _query_one(sql, params=()):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql, params)
        return cursor.fetchone()
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()


def _query_all(sql, params=()):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()


def get_latest_shipment_for_user(user_id):
    if not user_id:
        return None

    row = _query_one(
        "SELECT * FROM shipments WHERE user_id = %s ORDER BY created_at DESC, id DESC LIMIT 1",
        (str(user_id).strip(),),
    )
    return _serialize_shipment_row(row)


def get_active_payments_for_user(user_id, limit=5):
    if not user_id:
        return []

    rows = _query_all(
        """
        SELECT p.*
        FROM payments p
        LEFT JOIN shipments s ON p.shipment_id = s.id
        LEFT JOIN bookings b ON p.booking_id = b.id
        WHERE s.user_id = %s OR b.user_id = %s
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT %s
        """,
        (str(user_id).strip(), str(user_id).strip(), int(limit)),
    )
    active = []
    for row in rows:
        payment_status = str(row.get("payment_status") or row.get("status") or "").strip().lower()
        if payment_status in {"paid", "captured"}:
            continue
        active.append(_serialize_payment_row(row))
    return active


def get_latest_payment_for_user(user_id):
    if not user_id:
        return None

    row = _query_one(
        """
        SELECT p.*
        FROM payments p
        LEFT JOIN shipments s ON p.shipment_id = s.id
        LEFT JOIN bookings b ON p.booking_id = b.id
        WHERE s.user_id = %s OR b.user_id = %s
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT 1
        """,
        (str(user_id).strip(), str(user_id).strip()),
    )
    return _serialize_payment_row(row)


def get_payment_for_reference(reference_id):
    if reference_id in (None, ""):
        return None

    try:
        reference_value = int(reference_id)
    except Exception:
        return None

    row = _query_one(
        """
        SELECT *
        FROM payments
        WHERE shipment_id = %s OR booking_id = %s
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (reference_value, reference_value),
    )
    return _serialize_payment_row(row)


def get_user_context(user_id, session_id=None):
    latest_shipment = get_latest_shipment_for_user(user_id)
    active_payments = get_active_payments_for_user(user_id, limit=5)
    latest_payment = get_latest_payment_for_user(user_id)
    preference_memory = get_user_preferences(user_id) if user_id else {}

    recent_shipments = []
    if user_id:
        rows = _query_all(
            "SELECT * FROM shipments WHERE user_id = %s ORDER BY created_at DESC, id DESC LIMIT 5",
            (str(user_id).strip(),),
        )
        recent_shipments = [_serialize_shipment_row(row) for row in rows]

    payment_history = []
    if user_id:
        payment_rows = _query_all(
            """
            SELECT p.*
            FROM payments p
            LEFT JOIN shipments s ON p.shipment_id = s.id
            LEFT JOIN bookings b ON p.booking_id = b.id
            WHERE s.user_id = %s OR b.user_id = %s
            ORDER BY p.created_at DESC, p.id DESC
            LIMIT 8
            """,
            (str(user_id).strip(), str(user_id).strip()),
        )
        payment_history = [_serialize_payment_row(row) for row in payment_rows]

    route_counter = {}
    vehicle_counter = {}
    for shipment in recent_shipments:
        pickup = str(shipment.get("pickup_location") or "").strip()
        drop = str(shipment.get("drop_location") or "").strip()
        truck_type = str(shipment.get("truck_type") or "").strip()
        route_key = f"{pickup} -> {drop}" if pickup or drop else ""
        if route_key:
            route_counter[route_key] = int(route_counter.get(route_key, 0)) + 1
        if truck_type:
            vehicle_counter[truck_type] = int(vehicle_counter.get(truck_type, 0)) + 1

    preferred_route = max(route_counter, key=route_counter.get) if route_counter else ""
    preferred_vehicle_type = max(vehicle_counter, key=vehicle_counter.get) if vehicle_counter else ""

    recent_conversation = []
    if session_id:
        rows = _query_all(
            """
            SELECT user_message, bot_reply, created_at
            FROM chat_history
            WHERE session_id = %s
            ORDER BY created_at DESC, id DESC
            LIMIT 6
            """,
            (str(session_id).strip(),),
        )
        recent_conversation = [
            {
                "user_message": str(row.get("user_message") or "").strip(),
                "bot_reply": str(row.get("bot_reply") or "").strip(),
                "created_at": row.get("created_at").isoformat(sep=" ", timespec="seconds") if row.get("created_at") else None,
            }
            for row in rows
        ]

    return {
        "scope": "customer",
        "user_id": str(user_id or "").strip(),
        "session_id": str(session_id or "").strip(),
        "latest_shipment": latest_shipment,
        "latest_payment": latest_payment,
        "active_payments": active_payments,
        "recent_shipments": recent_shipments,
        "open_shipments": [
            shipment for shipment in recent_shipments
            if shipment and shipment.get("shipment_status") not in {"delivered", "cancelled"}
        ],
        "payment_history": payment_history,
        "preference_memory": {
            "profile": preference_memory,
            "preferred_route": preference_memory.get("preferred_route") or preferred_route,
            "preferred_vehicle_type": preference_memory.get("preferred_vehicle_type") or preference_memory.get("preferred_truck_type") or preferred_vehicle_type,
            "preferred_payment_method": preference_memory.get("preferred_payment_method") or preference_memory.get("payment_method"),
            "recent_conversation": recent_conversation,
        },
    }


def get_driver_context(driver_id):
    if driver_id in (None, ""):
        return {"scope": "driver", "driver": None, "assigned_shipments": [], "gps_logs": []}

    try:
        driver_id_value = int(driver_id)
    except Exception:
        return {"scope": "driver", "driver": None, "assigned_shipments": [], "gps_logs": []}

    driver_row = _query_one("SELECT * FROM drivers WHERE id = %s LIMIT 1", (driver_id_value,))
    driver = _serialize_driver_row(driver_row)
    assigned_shipments = []
    gps_logs = []

    if driver_row:
        shipment_rows = _query_all(
            "SELECT * FROM shipments WHERE assigned_driver_id = %s ORDER BY created_at DESC, id DESC LIMIT 5",
            (driver_id_value,),
        )
        assigned_shipments = [_serialize_shipment_row(row) for row in shipment_rows]

        truck_number = str(driver_row.get("assigned_truck") or "").strip()
        if truck_number:
            gps_rows = _query_all(
                """
                SELECT lorry_number, latitude, longitude, source_location, destination_location, created_at
                FROM gps_logs
                WHERE lorry_number = %s
                ORDER BY created_at DESC, id DESC
                LIMIT 10
                """,
                (truck_number,),
            )
            gps_logs = [
                {
                    "lorry_number": str(row.get("lorry_number") or truck_number).strip(),
                    "latitude": float(row.get("latitude")) if row.get("latitude") is not None else None,
                    "longitude": float(row.get("longitude")) if row.get("longitude") is not None else None,
                    "source_location": str(row.get("source_location") or "").strip(),
                    "destination_location": str(row.get("destination_location") or "").strip(),
                    "created_at": row.get("created_at").isoformat(sep=" ", timespec="seconds") if row.get("created_at") else None,
                }
                for row in gps_rows
            ]

    return {
        "scope": "driver",
        "driver": driver,
        "assigned_shipments": assigned_shipments,
        "gps_logs": gps_logs,
        "latest_shipment": assigned_shipments[0] if assigned_shipments else None,
    }


def get_shipment_tracking(shipment_id):
    if shipment_id in (None, ""):
        return None

    shipment_row = _query_one("SELECT * FROM shipments WHERE id = %s LIMIT 1", (int(shipment_id),))
    shipment = _serialize_shipment_row(shipment_row)
    if not shipment_row:
        return None

    driver = None
    gps_logs = []
    latest_location = None

    if shipment_row.get("assigned_driver_id") is not None:
        driver_row = _query_one("SELECT * FROM drivers WHERE id = %s LIMIT 1", (int(shipment_row.get("assigned_driver_id")),))
        driver = _serialize_driver_row(driver_row)
        if driver_row:
            truck_number = str(driver_row.get("assigned_truck") or "").strip()
            if truck_number:
                gps_rows = _query_all(
                    """
                    SELECT lorry_number, latitude, longitude, source_location, destination_location, created_at
                    FROM gps_logs
                    WHERE lorry_number = %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT 10
                    """,
                    (truck_number,),
                )
                gps_logs = [
                    {
                        "lorry_number": str(row.get("lorry_number") or truck_number).strip(),
                        "latitude": float(row.get("latitude")) if row.get("latitude") is not None else None,
                        "longitude": float(row.get("longitude")) if row.get("longitude") is not None else None,
                        "source_location": str(row.get("source_location") or "").strip(),
                        "destination_location": str(row.get("destination_location") or "").strip(),
                        "created_at": row.get("created_at").isoformat(sep=" ", timespec="seconds") if row.get("created_at") else None,
                    }
                    for row in gps_rows
                ]
                if gps_logs:
                    latest_location = gps_logs[0]

    timeline_rows = _query_all(
        "SELECT * FROM shipment_status_logs WHERE shipment_id = %s ORDER BY created_at ASC, id ASC",
        (int(shipment_id),),
    )
    timeline = [_serialize_status_log_row(row) for row in timeline_rows]

    return {
        "shipment": shipment,
        "driver": driver,
        "gps_logs": gps_logs,
        "latest_location": latest_location,
        "timeline": timeline,
    }


def get_admin_analytics_window(days=1):
    window_days = max(int(days or 1), 1)
    cutoff = datetime.utcnow() - timedelta(days=window_days)

    summary_row = _query_one(
        """
        SELECT
            COUNT(*) AS total_shipments,
            SUM(CASE WHEN shipment_status IN ('pending', 'confirmed', 'assigned', 'in_transit') THEN 1 ELSE 0 END) AS active_shipments,
            SUM(CASE WHEN shipment_status = 'delivered' THEN 1 ELSE 0 END) AS completed_shipments,
            SUM(CASE WHEN payment_status NOT IN ('paid', 'captured') THEN 1 ELSE 0 END) AS pending_payments,
            COALESCE(SUM(CASE WHEN payment_status IN ('paid', 'captured') THEN estimated_price ELSE 0 END), 0) AS revenue_collected,
            COALESCE(AVG(estimated_price), 0) AS average_order_value
        FROM shipments
        WHERE created_at >= %s
        """,
        (cutoff,),
    ) or {}

    delayed_row = _query_one(
        """
        SELECT COUNT(*) AS delayed_shipments
        FROM shipments
        WHERE created_at >= %s
          AND shipment_status IN ('pending', 'confirmed', 'assigned', 'in_transit')
          AND TIMESTAMPDIFF(HOUR, created_at, NOW()) >= 6
        """,
        (cutoff,),
    ) or {}

    revenue_rows = _query_all(
        """
        SELECT DATE(created_at) AS day, COALESCE(SUM(amount), 0) AS revenue
        FROM payments
        WHERE created_at >= %s AND payment_status IN ('paid', 'captured')
        GROUP BY DATE(created_at)
        ORDER BY day ASC
        """,
        (cutoff,),
    )

    recent_shipments_rows = _query_all(
        "SELECT * FROM shipments WHERE created_at >= %s ORDER BY created_at DESC, id DESC LIMIT 20",
        (cutoff,),
    )

    recent_payments_rows = _query_all(
        "SELECT * FROM payments WHERE created_at >= %s ORDER BY created_at DESC, id DESC LIMIT 20",
        (cutoff,),
    )

    return {
        "scope": "admin",
        "window_days": window_days,
        "summary": {
            "total_shipments": int(summary_row.get("total_shipments") or 0),
            "active_shipments": int(summary_row.get("active_shipments") or 0),
            "completed_shipments": int(summary_row.get("completed_shipments") or 0),
            "pending_payments": int(summary_row.get("pending_payments") or 0),
            "revenue_collected": int(summary_row.get("revenue_collected") or 0),
            "average_order_value": float(summary_row.get("average_order_value") or 0),
            "delayed_shipments": int(delayed_row.get("delayed_shipments") or 0),
        },
        "revenue_series": [
            {
                "day": str(row.get("day")),
                "revenue": int(row.get("revenue") or 0),
            }
            for row in revenue_rows
        ],
        "recent_shipments": [_serialize_shipment_row(row) for row in recent_shipments_rows],
        "recent_payments": [_serialize_payment_row(row) for row in recent_payments_rows],
    }
-- Demo seed dataset for Indian logistics operations.
-- Seeds at least 25 rows for each requested table:
-- users, drivers, shipments, vehicles, payments, notifications,
-- webhook_events, ai_action_logs, ai_tool_metrics.

START TRANSACTION;

CREATE TABLE IF NOT EXISTS notifications (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id VARCHAR(255) NULL,
    shipment_id BIGINT NULL,
    notification_channel VARCHAR(50) NOT NULL,
    notification_type VARCHAR(100) NOT NULL,
    title VARCHAR(255) NOT NULL,
    message TEXT,
    status VARCHAR(50) DEFAULT 'queued',
    correlation_id VARCHAR(255) NULL,
    sent_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_notifications_user (user_id),
    INDEX idx_notifications_shipment (shipment_id),
    INDEX idx_notifications_status (status),
    INDEX idx_notifications_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DROP TEMPORARY TABLE IF EXISTS seed_numbers;
CREATE TEMPORARY TABLE seed_numbers (
    n INT PRIMARY KEY
) ENGINE=MEMORY;

INSERT INTO seed_numbers (n)
VALUES
(1),(2),(3),(4),(5),(6),(7),(8),(9),(10),
(11),(12),(13),(14),(15),(16),(17),(18),(19),(20),
(21),(22),(23),(24),(25);

DELETE FROM ai_tool_metrics WHERE correlation_id LIKE 'seed-2026-metric-%';
DELETE FROM ai_action_logs WHERE user_id LIKE 'seed_user_%';
DELETE FROM webhook_events WHERE correlation_id LIKE 'seed-2026-webhook-%';
DELETE FROM notifications WHERE correlation_id LIKE 'seed-2026-notify-%';
DELETE FROM payments WHERE session_id LIKE 'seed_session_%';
DELETE FROM shipments WHERE user_id LIKE 'seed_user_%';
DELETE FROM drivers WHERE license_number LIKE 'DL-SEED-%';
DELETE FROM vehicles WHERE vehicle_code LIKE 'SEED-VEH-%';
DELETE FROM users WHERE email LIKE 'seed.user%@skdls.in';

INSERT INTO users (full_name, email, phone, password_hash, role, created_at)
SELECT
    CASE ((n - 1) % 5)
        WHEN 0 THEN CONCAT('Amit ', ELT(((n - 1) % 5) + 1, 'Sharma', 'Verma', 'Singh', 'Patel', 'Yadav'))
        WHEN 1 THEN CONCAT('Priya ', ELT(((n - 1) % 5) + 1, 'Reddy', 'Nair', 'Kaur', 'Joshi', 'Mehta'))
        WHEN 2 THEN CONCAT('Rahul ', ELT(((n - 1) % 5) + 1, 'Kumar', 'Gupta', 'Mishra', 'Das', 'Pandey'))
        WHEN 3 THEN CONCAT('Sneha ', ELT(((n - 1) % 5) + 1, 'Iyer', 'Bose', 'Kulkarni', 'Saxena', 'Chawla'))
        ELSE CONCAT('Arjun ', ELT(((n - 1) % 5) + 1, 'Kapoor', 'Thakur', 'Menon', 'Jain', 'Chopra'))
    END AS full_name,
    CONCAT('seed.user', LPAD(n, 2, '0'), '@skdls.in') AS email,
    CONCAT('+91', LPAD(9001000000 + n, 10, '0')) AS phone,
    '$pbkdf2-sha256$29000$seed$uElyW7demoHashForSeedOnly',
    CASE
        WHEN n IN (5, 10, 15, 20, 25) THEN 'driver'
        WHEN n IN (4, 8, 12, 16, 24) THEN 'admin'
        ELSE 'customer'
    END AS role,
    DATE_SUB(NOW(), INTERVAL (40 - n) DAY) AS created_at
FROM seed_numbers;

INSERT INTO drivers (
    driver_name,
    phone,
    license_number,
    assigned_truck,
    assigned_truck_type,
    status,
    rating,
    experience_years,
    next_available_at,
    created_at
)
SELECT
    CONCAT(
        ELT(((n - 1) % 8) + 1, 'Ravi', 'Sandeep', 'Manoj', 'Imran', 'Nitin', 'Akash', 'Vijay', 'Salman'),
        ' ',
        ELT(((n - 1) % 8) + 1, 'Kumar', 'Singh', 'Patil', 'Shaikh', 'Rao', 'Tiwari', 'Ali', 'Pawar')
    ) AS driver_name,
    CONCAT('+91', LPAD(9012000000 + n, 10, '0')) AS phone,
    CONCAT('DL-SEED-', LPAD(n, 4, '0')) AS license_number,
    CONCAT(
        ELT(((n - 1) % 8) + 1, 'MH12', 'KA05', 'DL01', 'GJ01', 'TN10', 'WB24', 'RJ14', 'UP32'),
        ELT(((n - 1) % 8) + 1, 'AB', 'CD', 'EF', 'GH', 'JK', 'LM', 'NP', 'QR'),
        LPAD(5000 + n, 4, '0')
    ) AS assigned_truck,
    CASE (n % 3)
        WHEN 1 THEN '12 tyre'
        WHEN 2 THEN '14 tyre'
        ELSE '16 tyre'
    END AS assigned_truck_type,
    CASE (n % 5)
        WHEN 0 THEN 'maintenance'
        WHEN 1 THEN 'available'
        WHEN 2 THEN 'on_trip'
        WHEN 3 THEN 'on_leave'
        ELSE 'available'
    END AS status,
    ROUND(4.2 + ((n % 8) * 0.1), 2) AS rating,
    3 + (n % 14) AS experience_years,
    CASE
        WHEN (n % 5) IN (0, 2) THEN DATE_ADD(NOW(), INTERVAL (n % 6) HOUR)
        ELSE NULL
    END AS next_available_at,
    DATE_SUB(NOW(), INTERVAL (30 - n) DAY) AS created_at
FROM seed_numbers;

INSERT INTO vehicles (
    vehicle_code,
    vehicle_name,
    truck_type,
    max_load_tons,
    rate_per_km,
    availability_status,
    current_booking_id,
    current_latitude,
    current_longitude,
    features_json,
    ai_notes,
    created_at,
    updated_at
)
SELECT
    CONCAT('SEED-VEH-', LPAD(n, 3, '0')) AS vehicle_code,
    CONCAT(
        ELT((n % 5) + 1, 'Ashok Leyland Hauler', 'Tata Ultra Freight', 'BharatBenz Cargo', 'Eicher Pro Fleet', 'Mahindra Blazo Move'),
        ' ',
        LPAD(n, 2, '0')
    ) AS vehicle_name,
    CASE (n % 3)
        WHEN 1 THEN '12 tyre'
        WHEN 2 THEN '14 tyre'
        ELSE '16 tyre'
    END AS truck_type,
    CASE (n % 3)
        WHEN 1 THEN 24 + (n % 2)
        WHEN 2 THEN 30 + (n % 2)
        ELSE 34 + (n % 2)
    END AS max_load_tons,
    36 + (n % 12) AS rate_per_km,
    CASE (n % 5)
        WHEN 0 THEN 'maintenance'
        WHEN 1 THEN 'available'
        WHEN 2 THEN 'on_trip'
        WHEN 3 THEN 'offline'
        ELSE 'available'
    END AS availability_status,
    NULL AS current_booking_id,
    CASE (n % 5)
        WHEN 0 THEN 19.0760 + (n / 1000.0)
        WHEN 1 THEN 28.7041 + (n / 1000.0)
        WHEN 2 THEN 12.9716 + (n / 1000.0)
        WHEN 3 THEN 22.5726 + (n / 1000.0)
        ELSE 17.3850 + (n / 1000.0)
    END AS current_latitude,
    CASE (n % 5)
        WHEN 0 THEN 72.8777 + (n / 1000.0)
        WHEN 1 THEN 77.1025 + (n / 1000.0)
        WHEN 2 THEN 77.5946 + (n / 1000.0)
        WHEN 3 THEN 88.3639 + (n / 1000.0)
        ELSE 78.4867 + (n / 1000.0)
    END AS current_longitude,
    JSON_ARRAY('GPS tracking', 'FASTag enabled', 'ePOD ready', 'Fuel monitored') AS features_json,
    CONCAT('Seed AI recommendation lane ', n, ': prioritize high-value consignments with live ETA updates.') AS ai_notes,
    DATE_SUB(NOW(), INTERVAL (35 - n) DAY) AS created_at,
    DATE_SUB(NOW(), INTERVAL (10 - (n % 10)) HOUR) AS updated_at
FROM seed_numbers;

INSERT INTO shipments (
    user_id,
    pickup_location,
    drop_location,
    cargo_type,
    truck_type,
    weight,
    distance_km,
    estimated_price,
    payment_status,
    shipment_status,
    assigned_driver_id,
    created_at
)
SELECT
    CONCAT('seed_user_', LPAD(n, 2, '0')) AS user_id,
    ELT(((n - 1) % 10) + 1,
        'Bhiwandi, Maharashtra',
        'Sanathnagar, Hyderabad',
        'Peenya, Bengaluru',
        'Chakan, Pune',
        'Vatva, Ahmedabad',
        'Sriperumbudur, Chennai',
        'Dhulagarh, Kolkata',
        'Okhla, New Delhi',
        'Sitapura, Jaipur',
        'Mundra Port, Gujarat'
    ) AS pickup_location,
    ELT(((n + 2) % 10) + 1,
        'Nagpur, Maharashtra',
        'Vijayawada, Andhra Pradesh',
        'Hosur, Tamil Nadu',
        'Indore, Madhya Pradesh',
        'Surat, Gujarat',
        'Coimbatore, Tamil Nadu',
        'Bhubaneswar, Odisha',
        'Lucknow, Uttar Pradesh',
        'Udaipur, Rajasthan',
        'Noida, Uttar Pradesh'
    ) AS drop_location,
    ELT(((n - 1) % 7) + 1,
        'FMCG pallets',
        'Industrial machinery',
        'Pharmaceutical cartons',
        'Textile bales',
        'Automotive parts',
        'Consumer electronics',
        'Agri produce sacks'
    ) AS cargo_type,
    CASE (n % 3)
        WHEN 1 THEN '12 tyre'
        WHEN 2 THEN '14 tyre'
        ELSE '16 tyre'
    END AS truck_type,
    ROUND(12 + (n * 0.9), 2) AS weight,
    ROUND(180 + (n * 15), 2) AS distance_km,
    ROUND((180 + (n * 15)) * (38 + (n % 8))) AS estimated_price,
    CASE (n % 5)
        WHEN 0 THEN 'failed'
        WHEN 1 THEN 'paid'
        WHEN 2 THEN 'pending'
        WHEN 3 THEN 'paid'
        ELSE 'pending'
    END AS payment_status,
    CASE (n % 8)
        WHEN 0 THEN 'cancelled'
        WHEN 1 THEN 'pending'
        WHEN 2 THEN 'confirmed'
        WHEN 3 THEN 'assigned'
        WHEN 4 THEN 'in_transit'
        WHEN 5 THEN 'delivered'
        WHEN 6 THEN 'delayed'
        ELSE 'failed'
    END AS shipment_status,
    (
        SELECT d.id
        FROM drivers d
        WHERE d.license_number = CONCAT('DL-SEED-', LPAD(((n - 1) % 25) + 1, 4, '0'))
        LIMIT 1
    ) AS assigned_driver_id,
    DATE_SUB(NOW(), INTERVAL (26 - n) HOUR) AS created_at
FROM seed_numbers;

INSERT INTO payments (
    booking_id,
    shipment_id,
    session_id,
    razorpay_order_id,
    razorpay_payment_id,
    amount,
    payment_status,
    status,
    payment_type,
    created_at
)
SELECT
    NULL AS booking_id,
    (
        SELECT s.id
        FROM shipments s
        WHERE s.user_id = CONCAT('seed_user_', LPAD(n, 2, '0'))
        ORDER BY s.id DESC
        LIMIT 1
    ) AS shipment_id,
    CONCAT('seed_session_', LPAD(n, 2, '0')) AS session_id,
    CONCAT('order_seed_', LPAD(n, 4, '0')) AS razorpay_order_id,
    CASE
        WHEN (n % 5) IN (1, 3, 4) THEN CONCAT('pay_seed_', LPAD(n, 4, '0'))
        ELSE NULL
    END AS razorpay_payment_id,
    12000 + (n * 850) AS amount,
    CASE (n % 5)
        WHEN 0 THEN 'failed'
        WHEN 1 THEN 'paid'
        WHEN 2 THEN 'pending'
        WHEN 3 THEN 'refunded'
        ELSE 'created'
    END AS payment_status,
    CASE (n % 5)
        WHEN 0 THEN 'failed'
        WHEN 1 THEN 'completed'
        WHEN 2 THEN 'pending'
        WHEN 3 THEN 'refunded'
        ELSE 'created'
    END AS status,
    CASE
        WHEN (n % 2) = 0 THEN 'advance'
        ELSE 'full'
    END AS payment_type,
    DATE_SUB(NOW(), INTERVAL (30 - n) HOUR) AS created_at
FROM seed_numbers;

INSERT INTO notifications (
    user_id,
    shipment_id,
    notification_channel,
    notification_type,
    title,
    message,
    status,
    correlation_id,
    sent_at,
    created_at
)
SELECT
    CONCAT('seed_user_', LPAD(n, 2, '0')) AS user_id,
    (
        SELECT s.id
        FROM shipments s
        WHERE s.user_id = CONCAT('seed_user_', LPAD(n, 2, '0'))
        ORDER BY s.id DESC
        LIMIT 1
    ) AS shipment_id,
    ELT(((n - 1) % 4) + 1, 'whatsapp', 'sms', 'push', 'email') AS notification_channel,
    ELT(((n - 1) % 6) + 1, 'tracking_update', 'eta_update', 'delivery_update', 'payment_update', 'delay_alert', 'driver_assigned') AS notification_type,
    ELT(((n - 1) % 6) + 1,
        'Live tracking ping',
        'ETA revised for shipment',
        'Delivery status changed',
        'Payment status update',
        'Delay advisory',
        'Driver assigned successfully'
    ) AS title,
    CONCAT('Shipment ', LPAD(n, 2, '0'), ' on ', ELT(((n - 1) % 5) + 1, 'Mumbai-Pune', 'Delhi-Jaipur', 'Chennai-Bengaluru', 'Ahmedabad-Surat', 'Kolkata-Bhubaneswar'), ' lane: event ', ELT(((n - 1) % 6) + 1, 'tracking', 'eta', 'delivery', 'payment', 'delay', 'driver assignment')) AS message,
    CASE (n % 4)
        WHEN 0 THEN 'failed'
        WHEN 1 THEN 'sent'
        WHEN 2 THEN 'queued'
        ELSE 'sent'
    END AS status,
    CONCAT('seed-2026-notify-', LPAD(n, 3, '0')) AS correlation_id,
    CASE
        WHEN (n % 4) = 2 THEN NULL
        ELSE DATE_SUB(NOW(), INTERVAL (26 - n) MINUTE)
    END AS sent_at,
    DATE_SUB(NOW(), INTERVAL (32 - n) MINUTE) AS created_at
FROM seed_numbers;

INSERT INTO webhook_events (
    source,
    event_type,
    status,
    payload_json,
    headers_json,
    response_code,
    correlation_id,
    created_at
)
SELECT
    ELT(((n - 1) % 5) + 1, 'razorpay', 'twilio_whatsapp', 'gps_provider', 'customer_app', 'fleet_telematics') AS source,
    ELT(((n - 1) % 7) + 1,
        'payment.captured',
        'payment.failed',
        'message.delivered',
        'tracking.ping',
        'delivery.completed',
        'driver.location',
        'shipment.delay'
    ) AS event_type,
    CASE (n % 4)
        WHEN 0 THEN 'failed'
        WHEN 1 THEN 'received'
        WHEN 2 THEN 'processed'
        ELSE 'retrying'
    END AS status,
    JSON_OBJECT(
        'shipment_reference', CONCAT('seed_user_', LPAD(n, 2, '0')),
        'lane', ELT(((n - 1) % 4) + 1, 'Mumbai-Pune', 'Delhi-Lucknow', 'Hyderabad-Vijayawada', 'Ahmedabad-Indore'),
        'event_no', n,
        'tracking', (n % 2) = 0
    ) AS payload_json,
    JSON_OBJECT(
        'x-webhook-source', ELT(((n - 1) % 5) + 1, 'razorpay', 'twilio', 'gps', 'customer-app', 'fleet'),
        'x-correlation-id', CONCAT('seed-2026-webhook-', LPAD(n, 3, '0')),
        'x-region', 'india-west'
    ) AS headers_json,
    CASE (n % 4)
        WHEN 0 THEN 500
        WHEN 1 THEN 200
        WHEN 2 THEN 202
        ELSE 429
    END AS response_code,
    CONCAT('seed-2026-webhook-', LPAD(n, 3, '0')) AS correlation_id,
    DATE_SUB(NOW(), INTERVAL (25 - n) MINUTE) AS created_at
FROM seed_numbers;

INSERT INTO ai_action_logs (
    user_id,
    role,
    action,
    intent,
    tool_name,
    shipment_id,
    status,
    request_payload,
    response_payload,
    started_at,
    completed_at,
    duration_ms,
    retry_count,
    execution_status,
    error_message,
    created_at
)
SELECT
    CONCAT('seed_user_', LPAD(n, 2, '0')) AS user_id,
    CASE
        WHEN (n % 5) = 0 THEN 'admin'
        WHEN (n % 3) = 0 THEN 'driver'
        ELSE 'customer'
    END AS role,
    ELT(((n - 1) % 8) + 1,
        'CREATE_SHIPMENT',
        'GET_PRICE_ESTIMATE',
        'ASSIGN_DRIVER',
        'TRACK_SHIPMENT',
        'MAKE_PAYMENT',
        'CUSTOMER_NOTIFICATION',
        'DELIVERY_CONFIRMATION',
        'RECONCILE_PAYMENT'
    ) AS action,
    ELT(((n - 1) % 8) + 1,
        'BOOK_SHIPMENT',
        'GET_PRICE_ESTIMATE',
        'DRIVER_UPDATE',
        'TRACK_SHIPMENT',
        'MAKE_PAYMENT',
        'CUSTOMER_SUPPORT',
        'DELIVERY_CONFIRMATION',
        'RECONCILE_PAYMENT'
    ) AS intent,
    ELT(((n - 1) % 8) + 1,
        'shipment_builder',
        'price_engine',
        'dispatch_allocator',
        'tracking_hub',
        'payment_verifier',
        'notification_gateway',
        'delivery_proof_service',
        'finance_reconciler'
    ) AS tool_name,
    (
        SELECT s.id
        FROM shipments s
        WHERE s.user_id = CONCAT('seed_user_', LPAD(n, 2, '0'))
        ORDER BY s.id DESC
        LIMIT 1
    ) AS shipment_id,
    CASE (n % 4)
        WHEN 0 THEN 'failed'
        ELSE 'success'
    END AS status,
    JSON_OBJECT('seed', true, 'step', n, 'channel', ELT(((n - 1) % 3) + 1, 'chat', 'api', 'admin_dashboard')) AS request_payload,
    JSON_OBJECT('seed', true, 'result', CASE WHEN (n % 4) = 0 THEN 'error' ELSE 'ok' END, 'tracking_event', (n % 2) = 0) AS response_payload,
    DATE_SUB(NOW(), INTERVAL (60 - n) MINUTE) AS started_at,
    DATE_SUB(NOW(), INTERVAL (60 - n) MINUTE) + INTERVAL (120 + (n * 7)) MICROSECOND AS completed_at,
    120 + (n * 7) AS duration_ms,
    CASE
        WHEN (n % 4) = 0 THEN 1 + (n % 2)
        ELSE 0
    END AS retry_count,
    CASE
        WHEN (n % 6) = 0 THEN 'timeout'
        WHEN (n % 4) = 0 THEN 'failed'
        ELSE 'success'
    END AS execution_status,
    CASE
        WHEN (n % 6) = 0 THEN 'Upstream model timeout while generating route ETA'
        WHEN (n % 4) = 0 THEN 'Tool execution failed due to partner API response 500'
        ELSE NULL
    END AS error_message,
    DATE_SUB(NOW(), INTERVAL (60 - n) MINUTE) AS created_at
FROM seed_numbers;

INSERT INTO ai_tool_metrics (
    user_id,
    role,
    workflow_type,
    action,
    tool_name,
    duration_ms,
    retry_count,
    execution_status,
    ai_latency_ms,
    websocket_latency_ms,
    correlation_id,
    execution_id,
    metadata_json,
    created_at
)
SELECT
    CONCAT('seed_user_', LPAD(n, 2, '0')) AS user_id,
    CASE
        WHEN (n % 5) = 0 THEN 'admin'
        WHEN (n % 3) = 0 THEN 'driver'
        ELSE 'customer'
    END AS role,
    ELT(((n - 1) % 5) + 1, 'booking', 'tracking', 'payments', 'delivery', 'notifications') AS workflow_type,
    ELT(((n - 1) % 8) + 1,
        'CREATE_SHIPMENT',
        'GET_PRICE_ESTIMATE',
        'ASSIGN_DRIVER',
        'TRACK_SHIPMENT',
        'MAKE_PAYMENT',
        'CUSTOMER_NOTIFICATION',
        'DELIVERY_CONFIRMATION',
        'RECONCILE_PAYMENT'
    ) AS action,
    ELT(((n - 1) % 8) + 1,
        'shipment_builder',
        'price_engine',
        'dispatch_allocator',
        'tracking_hub',
        'payment_verifier',
        'notification_gateway',
        'delivery_proof_service',
        'finance_reconciler'
    ) AS tool_name,
    100 + (n * 9) AS duration_ms,
    CASE
        WHEN (n % 5) = 0 THEN 2
        WHEN (n % 4) = 0 THEN 1
        ELSE 0
    END AS retry_count,
    CASE
        WHEN (n % 7) = 0 THEN 'timeout'
        WHEN (n % 5) = 0 THEN 'failed'
        ELSE 'success'
    END AS execution_status,
    ROUND((100 + (n * 9)) * 0.9, 2) AS ai_latency_ms,
    ROUND(8 + (n % 11), 2) AS websocket_latency_ms,
    CONCAT('seed-2026-metric-', LPAD(n, 3, '0')) AS correlation_id,
    CONCAT('exec-seed-', LPAD(n, 3, '0')) AS execution_id,
    JSON_OBJECT(
        'seed', true,
        'region', 'india-west',
        'lane', ELT(((n - 1) % 4) + 1, 'Mumbai-Pune', 'Delhi-Lucknow', 'Chennai-Bengaluru', 'Ahmedabad-Surat'),
        'delivery_event', (n % 2) = 0,
        'tracking_event', (n % 3) = 0
    ) AS metadata_json,
    DATE_SUB(NOW(), INTERVAL (90 - n) MINUTE) AS created_at
FROM seed_numbers;

COMMIT;

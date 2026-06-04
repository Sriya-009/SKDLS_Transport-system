-- Migration: Enterprise payments ledger, retries, refunds, and control-tower support

CREATE TABLE IF NOT EXISTS customer_wallet_ledger (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id VARCHAR(255) NULL,
    booking_id BIGINT NULL,
    shipment_id BIGINT NULL,
    payment_id BIGINT NULL,
    entry_type VARCHAR(50) NOT NULL,
    amount INT NOT NULL,
    balance_after INT DEFAULT 0,
    description VARCHAR(255) NULL,
    metadata_json LONGTEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_wallet_user (user_id),
    INDEX idx_wallet_booking (booking_id),
    INDEX idx_wallet_shipment (shipment_id),
    INDEX idx_wallet_payment (payment_id),
    INDEX idx_wallet_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS payment_retries (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    payment_id BIGINT NULL,
    booking_id BIGINT NULL,
    shipment_id BIGINT NULL,
    old_razorpay_order_id VARCHAR(255) NULL,
    new_razorpay_order_id VARCHAR(255) NULL,
    retry_reason VARCHAR(255) NULL,
    retry_count INT DEFAULT 0,
    status VARCHAR(50) DEFAULT 'created',
    metadata_json LONGTEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_payment_retries_payment (payment_id),
    INDEX idx_payment_retries_booking (booking_id),
    INDEX idx_payment_retries_shipment (shipment_id),
    INDEX idx_payment_retries_status (status),
    INDEX idx_payment_retries_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS payment_refunds (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    payment_id BIGINT NULL,
    booking_id BIGINT NULL,
    shipment_id BIGINT NULL,
    razorpay_payment_id VARCHAR(255) NOT NULL,
    razorpay_refund_id VARCHAR(255) NULL,
    amount INT NOT NULL,
    status VARCHAR(50) DEFAULT 'initiated',
    reason VARCHAR(255) NULL,
    metadata_json LONGTEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_payment_refunds_payment (payment_id),
    INDEX idx_payment_refunds_booking (booking_id),
    INDEX idx_payment_refunds_shipment (shipment_id),
    INDEX idx_payment_refunds_razorpay_payment (razorpay_payment_id),
    INDEX idx_payment_refunds_status (status),
    INDEX idx_payment_refunds_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

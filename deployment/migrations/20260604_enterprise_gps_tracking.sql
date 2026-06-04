-- Migration: Enterprise GPS tracking, driver heartbeat, and fleet monitoring

DROP PROCEDURE IF EXISTS add_column_if_missing;
DROP PROCEDURE IF EXISTS add_index_if_missing;

DELIMITER $$

CREATE PROCEDURE add_column_if_missing(
    IN target_table VARCHAR(64),
    IN target_column VARCHAR(64),
    IN column_definition TEXT
)
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = target_table
          AND COLUMN_NAME = target_column
    ) THEN
        SET @ddl = CONCAT('ALTER TABLE `', target_table, '` ADD COLUMN `', target_column, '` ', column_definition);
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$

CREATE PROCEDURE add_index_if_missing(
    IN target_table VARCHAR(64),
    IN target_index VARCHAR(64),
    IN index_definition TEXT
)
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = target_table
          AND INDEX_NAME = target_index
    ) THEN
        SET @ddl = CONCAT('ALTER TABLE `', target_table, '` ADD INDEX `', target_index, '` ', index_definition);
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$

DELIMITER ;

CALL add_column_if_missing('gps_logs', 'shipment_id', 'BIGINT NULL AFTER `booking_id`');
CALL add_column_if_missing('gps_logs', 'driver_id', 'BIGINT NULL AFTER `shipment_id`');
CALL add_column_if_missing('gps_logs', 'speed_kmph', 'DECIMAL(8, 2) NULL AFTER `longitude`');
CALL add_column_if_missing('gps_logs', 'heading_degrees', 'DECIMAL(8, 2) NULL AFTER `speed_kmph`');
CALL add_column_if_missing('gps_logs', 'event_type', 'VARCHAR(100) DEFAULT ''gps_ping'' AFTER `destination_location`');
CALL add_column_if_missing('gps_logs', 'metadata_json', 'LONGTEXT NULL AFTER `event_type`');

CALL add_index_if_missing('gps_logs', 'idx_gps_logs_shipment_id', '(`shipment_id`)');
CALL add_index_if_missing('gps_logs', 'idx_gps_logs_driver_id', '(`driver_id`)');

CREATE TABLE IF NOT EXISTS driver_locations (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    driver_id BIGINT NULL,
    shipment_id BIGINT NULL,
    booking_id BIGINT NULL,
    lorry_number VARCHAR(100) NOT NULL,
    latitude DECIMAL(10, 8) NOT NULL,
    longitude DECIMAL(11, 8) NOT NULL,
    speed_kmph DECIMAL(8, 2) NULL,
    heading_degrees DECIMAL(8, 2) NULL,
    accuracy_meters DECIMAL(8, 2) NULL,
    battery_level DECIMAL(5, 2) NULL,
    heartbeat_status VARCHAR(50) DEFAULT 'online',
    source VARCHAR(100) DEFAULT 'gps',
    metadata_json LONGTEXT,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_driver_locations_driver (driver_id),
    INDEX idx_driver_locations_shipment (shipment_id),
    INDEX idx_driver_locations_booking (booking_id),
    INDEX idx_driver_locations_lorry (lorry_number),
    INDEX idx_driver_locations_recorded (recorded_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS notifications (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id VARCHAR(255) NULL,
    driver_id BIGINT NULL,
    shipment_id BIGINT NULL,
    notification_type VARCHAR(100) NULL,
    title VARCHAR(255) NULL,
    message TEXT,
    status VARCHAR(50) DEFAULT 'unread',
    metadata_json LONGTEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    read_at TIMESTAMP NULL,
    INDEX idx_notifications_user (user_id),
    INDEX idx_notifications_driver (driver_id),
    INDEX idx_notifications_shipment (shipment_id),
    INDEX idx_notifications_status (status),
    INDEX idx_notifications_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DROP PROCEDURE IF EXISTS add_column_if_missing;
DROP PROCEDURE IF EXISTS add_index_if_missing;

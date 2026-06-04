-- Migration: Add AI action logs and workflow memory tables

CREATE TABLE IF NOT EXISTS ai_action_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id VARCHAR(255) NULL,
    role VARCHAR(50) NULL,
    action VARCHAR(255) NULL,
    intent VARCHAR(255) NULL,
    tool_name VARCHAR(255) NULL,
    shipment_id BIGINT NULL,
    status VARCHAR(50) DEFAULT 'success',
    request_payload LONGTEXT,
    response_payload LONGTEXT,
    started_at TIMESTAMP NULL,
    completed_at TIMESTAMP NULL,
    duration_ms INT NULL,
    retry_count INT DEFAULT 0,
    execution_status VARCHAR(50) DEFAULT 'success',
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_ai_action_user (user_id),
    INDEX idx_ai_action_shipment (shipment_id),
    INDEX idx_ai_action_action (action),
    INDEX idx_ai_action_status (status),
    INDEX idx_ai_action_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ai_conversation_memory (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id VARCHAR(255) NULL,
    session_id VARCHAR(255) NOT NULL,
    conversation_id VARCHAR(255) NOT NULL,
    memory_json LONGTEXT,
    summary TEXT,
    last_intent VARCHAR(255) NULL,
    last_route VARCHAR(255) NULL,
    confidence DECIMAL(5, 4) DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_ai_conversation_memory_session (session_id, conversation_id),
    INDEX idx_ai_conversation_memory_user (user_id),
    INDEX idx_ai_conversation_memory_session (session_id),
    INDEX idx_ai_conversation_memory_updated_at (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ai_session_context (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(255) NOT NULL,
    user_id VARCHAR(255) NULL,
    role VARCHAR(50) NULL,
    context_json LONGTEXT,
    active_workflow VARCHAR(255) NULL,
    current_step VARCHAR(255) NULL,
    status VARCHAR(50) DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_ai_session_context_session (session_id),
    INDEX idx_ai_session_context_user (user_id),
    INDEX idx_ai_session_context_status (status),
    INDEX idx_ai_session_context_updated_at (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ai_workflow_memory (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    workflow_type VARCHAR(255) NOT NULL,
    workflow_state LONGTEXT,
    current_step VARCHAR(255),
    context_json LONGTEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_ai_workflow_user_type (user_id, workflow_type),
    INDEX idx_ai_workflow_user (user_id),
    INDEX idx_ai_workflow_type (workflow_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ai_tool_metrics (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id VARCHAR(255) NULL,
    role VARCHAR(50) NULL,
    workflow_type VARCHAR(255) NULL,
    action VARCHAR(255) NULL,
    tool_name VARCHAR(255) NULL,
    duration_ms INT NULL,
    retry_count INT DEFAULT 0,
    execution_status VARCHAR(50) DEFAULT 'success',
    ai_latency_ms DECIMAL(10, 2) NULL,
    websocket_latency_ms DECIMAL(10, 2) NULL,
    correlation_id VARCHAR(255) NULL,
    execution_id VARCHAR(255) NULL,
    metadata_json LONGTEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_ai_tool_metrics_tool (tool_name),
    INDEX idx_ai_tool_metrics_status (execution_status),
    INDEX idx_ai_tool_metrics_user (user_id),
    INDEX idx_ai_tool_metrics_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS webhook_events (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    source VARCHAR(255) NULL,
    event_type VARCHAR(255) NULL,
    status VARCHAR(50) DEFAULT 'received',
    payload_json LONGTEXT,
    headers_json LONGTEXT,
    response_code INT NULL,
    correlation_id VARCHAR(255) NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_webhook_events_source (source),
    INDEX idx_webhook_events_type (event_type),
    INDEX idx_webhook_events_status (status),
    INDEX idx_webhook_events_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS audit_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id VARCHAR(255) NULL,
    role VARCHAR(50) NULL,
    action VARCHAR(255) NULL,
    entity_type VARCHAR(255) NULL,
    entity_id VARCHAR(255) NULL,
    status VARCHAR(50) DEFAULT 'success',
    details_json LONGTEXT,
    correlation_id VARCHAR(255) NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_audit_logs_user (user_id),
    INDEX idx_audit_logs_entity (entity_type, entity_id),
    INDEX idx_audit_logs_action (action),
    INDEX idx_audit_logs_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

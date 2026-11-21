CREATE TABLE sensor_event (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    sensor_serial VARCHAR(64) NOT NULL,
    event_code VARCHAR(64) NOT NULL,
    vehicle_vin VARCHAR(64) NOT NULL,
    reading DECIMAL(10,2) NOT NULL,
    event_time TIMESTAMP NOT NULL
);
CREATE TABLE diagnostic_log (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    log_code VARCHAR(64) NOT NULL,
    tenant_id VARCHAR(64) NOT NULL,
    trouble_codes VARCHAR(255) NOT NULL,
    mechanic_comment VARCHAR(255)
);

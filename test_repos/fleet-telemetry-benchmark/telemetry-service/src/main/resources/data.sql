INSERT INTO sensor_event (sensor_serial, event_code, vehicle_vin, reading, event_time)
VALUES ('SN-7710', 'EV-1002', '1HGCR2F83HA001234', 85.50, CURRENT_TIMESTAMP);
INSERT INTO diagnostic_log (log_code, tenant_id, trouble_codes, mechanic_comment)
VALUES ('LOG-401', 'FLEET-88', 'P0300, P0171', 'Engine misfire detected on cylinder 1');

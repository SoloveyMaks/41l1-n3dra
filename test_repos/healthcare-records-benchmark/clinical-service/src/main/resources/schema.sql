CREATE TABLE lab_report (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    specimen_id VARCHAR(64) NOT NULL,
    report_code VARCHAR(64) NOT NULL,
    patient_id VARCHAR(64) NOT NULL,
    biomarkers VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL
);
CREATE TABLE medical_history (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    record_code VARCHAR(64) NOT NULL,
    patient_id VARCHAR(64) NOT NULL,
    diagnoses VARCHAR(255) NOT NULL,
    physician_note VARCHAR(255)
);

CREATE TABLE transaction_record (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    reference_tag VARCHAR(64) NOT NULL,
    transaction_code VARCHAR(64) NOT NULL,
    merchant_id VARCHAR(64) NOT NULL,
    amount DECIMAL(15,2) NOT NULL,
    status VARCHAR(32) NOT NULL
);
CREATE TABLE wallet_statement (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    statement_code VARCHAR(64) NOT NULL,
    merchant_id VARCHAR(64) NOT NULL,
    balance DECIMAL(15,2) NOT NULL,
    audit_note VARCHAR(255)
);

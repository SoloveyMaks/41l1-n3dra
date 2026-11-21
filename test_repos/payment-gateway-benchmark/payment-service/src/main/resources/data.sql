INSERT INTO transaction_record (reference_tag, transaction_code, merchant_id, amount, status)
VALUES ('REF-1001', 'TX-7701', 'M-991', 1500.00, 'COMPLETED');
INSERT INTO wallet_statement (statement_code, merchant_id, balance, audit_note)
VALUES ('STMT-901', 'M-991', 45000.50, 'Monthly settlement validated');

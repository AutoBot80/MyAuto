-- Insert Hero MotoCorp (oem_id = 1) service schedule into oem_service_schedule.
-- Run after oem_ref and oem_service_schedule exist: psql -U postgres -d auto_ai -f DDL/seed_oem_service_schedule.sql

INSERT INTO oem_service_schedule (oem_id, service_num, service_type, days_from_billing, active_flag, reminder_type) VALUES (1, 1, 'Free', 60, 'Y', 'SMS');
INSERT INTO oem_service_schedule (oem_id, service_num, service_type, days_from_billing, active_flag, reminder_type) VALUES (1, 2, 'Free', 160, 'Y', 'SMS');
INSERT INTO oem_service_schedule (oem_id, service_num, service_type, days_from_billing, active_flag, reminder_type) VALUES (1, 3, 'Free', 260, 'Y', 'SMS');
INSERT INTO oem_service_schedule (oem_id, service_num, service_type, days_from_billing, active_flag, reminder_type) VALUES (1, 4, 'Free', 360, 'Y', 'SMS');
INSERT INTO oem_service_schedule (oem_id, service_num, service_type, days_from_billing, active_flag, reminder_type) VALUES (1, 5, 'Free', 460, 'Y', 'SMS');
INSERT INTO oem_service_schedule (oem_id, service_num, service_type, days_from_billing, active_flag, reminder_type) VALUES (1, 6, 'Paid', 640, 'Y', 'SMS');
INSERT INTO oem_service_schedule (oem_id, service_num, service_type, days_from_billing, active_flag, reminder_type) VALUES (1, 7, 'Paid', 820, 'Y', 'SMS');
INSERT INTO oem_service_schedule (oem_id, service_num, service_type, days_from_billing, active_flag, reminder_type) VALUES (1, 8, 'Paid', 1000, 'Y', 'SMS');
INSERT INTO oem_service_schedule (oem_id, service_num, service_type, days_from_billing, active_flag, reminder_type) VALUES (1, 9, 'Paid', 1180, 'Y', 'SMS');

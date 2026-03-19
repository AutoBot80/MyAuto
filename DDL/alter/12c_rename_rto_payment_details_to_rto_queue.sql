DO $$
BEGIN
    IF to_regclass('public.rto_payment_details') IS NOT NULL
       AND to_regclass('public.rto_queue') IS NULL THEN
        ALTER TABLE rto_payment_details RENAME TO rto_queue;
    END IF;
END $$;

COMMENT ON TABLE rto_queue IS 'RTO queue rows created after Fill Forms; application_id stores the queue/reference id until a final Vahan application id exists';

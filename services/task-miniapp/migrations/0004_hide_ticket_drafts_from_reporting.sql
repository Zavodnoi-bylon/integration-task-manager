DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'activity_reader') THEN
        REVOKE SELECT ON ticketing.ticket_drafts FROM activity_reader;
    END IF;
END $$;

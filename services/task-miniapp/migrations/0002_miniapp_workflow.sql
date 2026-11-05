-- Mini App workflow extension for the canonical ticketing schema.
-- Requires canonical migration 0001_ticketing_schema.sql.

INSERT INTO ticketing.ticket_statuses (
    status_code, display_name, is_terminal, sort_order
) VALUES ('review', 'На проверке', false, 15)
ON CONFLICT (status_code) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    is_terminal = EXCLUDED.is_terminal,
    sort_order = EXCLUDED.sort_order;

ALTER TABLE ticketing.tickets
    ADD COLUMN IF NOT EXISTS review_cycle integer NOT NULL DEFAULT 0
        CHECK (review_cycle >= 0),
    ADD COLUMN IF NOT EXISTS review_started_at timestamptz,
    ADD COLUMN IF NOT EXISTS first_review_at timestamptz,
    ADD COLUMN IF NOT EXISTS accepted_at timestamptz;

CREATE TABLE IF NOT EXISTS ticketing.miniapp_sessions (
    session_hash char(64) PRIMARY KEY,
    csrf_hash char(64) NOT NULL,
    profile_key text NOT NULL REFERENCES ticketing.bot_profiles(profile_key),
    user_id bigint NOT NULL REFERENCES ticketing.telegram_users(user_id),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    rotated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (profile_key, user_id)
);
CREATE INDEX IF NOT EXISTS miniapp_sessions_expiry_idx
    ON ticketing.miniapp_sessions (expires_at);

CREATE TABLE IF NOT EXISTS ticketing.miniapp_idempotency (
    namespace text PRIMARY KEY,
    profile_key text NOT NULL REFERENCES ticketing.bot_profiles(profile_key),
    actor_user_id bigint NOT NULL REFERENCES ticketing.telegram_users(user_id),
    operation text NOT NULL CHECK (length(btrim(operation)) > 0),
    request_uuid uuid NOT NULL,
    payload_hash char(64) NOT NULL,
    state text NOT NULL CHECK (state IN ('processing', 'completed', 'failed')),
    lease_token uuid,
    lease_expires_at timestamptz,
    response jsonb,
    last_error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    UNIQUE (profile_key, actor_user_id, operation, request_uuid),
    CHECK (jsonb_typeof(response) = 'object' OR response IS NULL),
    CHECK (
        (state = 'processing' AND lease_token IS NOT NULL
            AND lease_expires_at IS NOT NULL AND response IS NULL)
        OR (state = 'completed' AND lease_token IS NULL
            AND lease_expires_at IS NULL AND response IS NOT NULL)
        OR (state = 'failed' AND lease_token IS NULL
            AND lease_expires_at IS NULL AND response IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS miniapp_idempotency_lease_idx
    ON ticketing.miniapp_idempotency (lease_expires_at)
    WHERE state = 'processing';

ALTER TABLE ticketing.outbox
    ADD COLUMN IF NOT EXISTS locked_at timestamptz,
    ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz,
    ADD COLUMN IF NOT EXISTS review_cycle integer,
    ADD COLUMN IF NOT EXISTS required_status text;

ALTER TABLE ticketing.outbox DROP CONSTRAINT IF EXISTS outbox_required_status_fkey;
ALTER TABLE ticketing.outbox ADD CONSTRAINT outbox_required_status_fkey
    FOREIGN KEY (required_status)
    REFERENCES ticketing.ticket_statuses(status_code);

CREATE INDEX IF NOT EXISTS outbox_delivery_idx
    ON ticketing.outbox (available_at, outbox_id)
    WHERE status IN ('pending', 'failed', 'sending');

CREATE OR REPLACE VIEW reporting.ticket_facts AS
SELECT
    t.ticket_id,
    t.profile_key,
    p.display_name AS profile_name,
    requester.telegram_user_id AS requester_telegram_id,
    requester.username AS requester_username,
    assigned.telegram_user_id AS assigned_telegram_id,
    assigned.username AS assigned_username,
    t.status_code,
    s.display_name AS status_name,
    t.title,
    t.description,
    t.created_at,
    t.updated_at,
    t.closed_at,
    CASE WHEN t.closed_at IS NOT NULL
         THEN EXTRACT(epoch FROM t.closed_at - t.created_at)::bigint END
         AS resolution_seconds,
    (SELECT count(*) FROM ticketing.ticket_events e
     WHERE e.ticket_id = t.ticket_id
       AND e.metadata->>'workflow_action' = 'ticket_reopened') AS reopen_count,
    (SELECT count(*) FROM ticketing.ticket_messages m
     WHERE m.ticket_id = t.ticket_id AND m.visibility = 'public')
         AS public_message_count,
    (SELECT count(*) FROM ticketing.ticket_messages m
     WHERE m.ticket_id = t.ticket_id AND m.visibility = 'internal')
         AS internal_note_count,
    t.ticket_type_code AS ticket_type,
    tt.display_name AS ticket_type_name,
    t.review_cycle,
    t.review_started_at,
    t.first_review_at,
    t.accepted_at,
    CASE WHEN t.first_review_at IS NOT NULL
         THEN EXTRACT(epoch FROM t.first_review_at - t.created_at)::bigint END
         AS time_to_first_review_seconds,
    CASE WHEN t.accepted_at IS NOT NULL
         THEN EXTRACT(epoch FROM t.accepted_at - t.created_at)::bigint END
         AS time_to_accept_seconds,
    (SELECT count(*) FROM ticketing.ticket_events e
     WHERE e.ticket_id = t.ticket_id
       AND e.metadata->>'workflow_action' = 'general_returned') AS review_return_count
FROM ticketing.tickets t
JOIN ticketing.bot_profiles p USING (profile_key)
JOIN ticketing.ticket_statuses s USING (status_code)
JOIN ticketing.ticket_types tt ON tt.type_code = t.ticket_type_code
JOIN ticketing.telegram_users requester ON requester.user_id = t.requester_user_id
LEFT JOIN ticketing.telegram_users assigned ON assigned.user_id = t.assigned_user_id;

CREATE OR REPLACE VIEW reporting.ticket_daily_flow AS
SELECT
    (e.occurred_at AT TIME ZONE 'Europe/Moscow')::date AS activity_date,
    e.profile_key,
    count(*) FILTER (WHERE e.event_type = 'created') AS created_count,
    count(*) FILTER (WHERE e.event_type = 'status_changed'
                       AND e.to_status = 'completed') AS completed_count,
    count(*) FILTER (WHERE e.event_type = 'status_changed'
                       AND e.to_status = 'open'
                       AND e.from_status = 'completed') AS reopened_count,
    count(*) FILTER (WHERE e.event_type = 'public_message') AS public_message_count,
    count(*) FILTER (WHERE e.event_type = 'internal_note') AS internal_note_count,
    count(*) FILTER (WHERE e.metadata->>'workflow_action'
                       = 'general_submitted_for_review') AS review_submitted_count,
    count(*) FILTER (WHERE e.metadata->>'workflow_action'
                       = 'general_accepted') AS review_accepted_count,
    count(*) FILTER (WHERE e.metadata->>'workflow_action'
                       = 'general_returned') AS review_returned_count
FROM ticketing.ticket_events e
GROUP BY 1, 2;

CREATE OR REPLACE VIEW reporting.ticket_current_backlog AS
SELECT profile_key, assigned_telegram_id, assigned_username,
       count(*) AS open_count,
       min(created_at) AS oldest_opened_at,
       max(updated_at) AS latest_activity_at
FROM reporting.ticket_facts
WHERE status_code = 'open'
GROUP BY profile_key, assigned_telegram_id, assigned_username;

CREATE OR REPLACE VIEW reporting.ticket_review_backlog AS
SELECT profile_key, assigned_telegram_id, assigned_username,
       count(*) AS review_count,
       min(review_started_at) AS oldest_review_started_at,
       max(updated_at) AS latest_activity_at
FROM reporting.ticket_facts
WHERE status_code = 'review'
GROUP BY profile_key, assigned_telegram_id, assigned_username;

CREATE OR REPLACE VIEW reporting.ticket_review_cycles AS
SELECT
    e.ticket_id,
    e.profile_key,
    (e.metadata->>'review_cycle')::integer AS review_cycle,
    min(e.occurred_at) FILTER (
        WHERE e.metadata->>'workflow_action' = 'general_submitted_for_review'
    ) AS submitted_at,
    min(e.occurred_at) FILTER (
        WHERE e.metadata->>'workflow_action' = 'general_accepted'
    ) AS accepted_at,
    min(e.occurred_at) FILTER (
        WHERE e.metadata->>'workflow_action' = 'general_returned'
    ) AS returned_at,
    count(*) FILTER (
        WHERE e.metadata->>'workflow_action' = 'general_returned'
    ) AS return_count
FROM ticketing.ticket_events e
WHERE e.metadata ? 'review_cycle'
GROUP BY e.ticket_id, e.profile_key, (e.metadata->>'review_cycle')::integer;

GRANT SELECT, INSERT, UPDATE, DELETE ON ticketing.miniapp_sessions,
    ticketing.miniapp_idempotency TO ticketing_writer;
GRANT INSERT, UPDATE ON ticketing.telegram_users, ticketing.memberships,
    ticketing.tickets, ticketing.ticket_messages, ticketing.ticket_events
    TO ticketing_writer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ticketing TO ticketing_writer;
GRANT SELECT ON reporting.ticket_review_cycles,
    reporting.ticket_review_backlog TO ticketing_writer;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'activity_reader') THEN
        GRANT SELECT ON reporting.ticket_review_cycles,
            reporting.ticket_review_backlog TO activity_reader;
    END IF;
END $$;

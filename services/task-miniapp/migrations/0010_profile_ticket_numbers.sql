-- Profile-scoped public ticket numbers. Internal ticket_id values remain stable.
LOCK TABLE ticketing.tickets IN SHARE ROW EXCLUSIVE MODE;

ALTER TABLE ticketing.tickets
    ADD COLUMN ticket_number bigint;

WITH numbered AS (
    SELECT
        ticket_id,
        CASE
            WHEN profile_key = 'kalisto' THEN row_number() OVER (
                PARTITION BY profile_key ORDER BY created_at, ticket_id
            )::bigint
            ELSE ticket_id
        END AS ticket_number
    FROM ticketing.tickets
)
UPDATE ticketing.tickets AS ticket
SET ticket_number = numbered.ticket_number
FROM numbered
WHERE numbered.ticket_id = ticket.ticket_id;

ALTER TABLE ticketing.tickets
    ALTER COLUMN ticket_number SET NOT NULL,
    ADD CONSTRAINT tickets_ticket_number_positive_ck CHECK (ticket_number > 0),
    ADD CONSTRAINT tickets_profile_ticket_number_key
        UNIQUE (profile_key, ticket_number);

CREATE TABLE ticketing.profile_ticket_counters (
    profile_key text PRIMARY KEY
        REFERENCES ticketing.bot_profiles(profile_key),
    last_number bigint NOT NULL CHECK (last_number >= 0),
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO ticketing.profile_ticket_counters (profile_key, last_number)
SELECT profile.profile_key, COALESCE(max(ticket.ticket_number), 0)
FROM ticketing.bot_profiles AS profile
LEFT JOIN ticketing.tickets AS ticket USING (profile_key)
GROUP BY profile.profile_key;

CREATE OR REPLACE FUNCTION ticketing.next_ticket_number(p_profile_key text)
RETURNS bigint
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, ticketing
AS $$
    INSERT INTO ticketing.profile_ticket_counters (
        profile_key, last_number, updated_at
    ) VALUES (
        p_profile_key, 1, now()
    )
    ON CONFLICT (profile_key) DO UPDATE SET
        last_number = ticketing.profile_ticket_counters.last_number + 1,
        updated_at = now()
    RETURNING last_number
$$;

CREATE OR REPLACE FUNCTION ticketing.create_ticket(
    p_profile_key text,
    p_requester_telegram_id bigint,
    p_assigned_telegram_id bigint,
    p_description text,
    p_created_at timestamptz,
    p_request_key text,
    p_source text DEFAULT 'telegram',
    p_legacy_sqlite_id bigint DEFAULT NULL,
    p_metadata jsonb DEFAULT '{}'::jsonb,
    p_ticket_type_code text DEFAULT 'general'
) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, ticketing
AS $$
DECLARE
    v_ticket_id bigint;
    v_ticket_number bigint;
    v_requester_id bigint;
    v_requester_role text;
    v_assigned_id bigint;
BEGIN
    SELECT t.ticket_id INTO v_ticket_id
    FROM ticketing.tickets t
    WHERE t.profile_key = p_profile_key AND t.request_key = p_request_key;
    IF v_ticket_id IS NOT NULL THEN RETURN v_ticket_id; END IF;

    SELECT u.user_id, m.role INTO STRICT v_requester_id, v_requester_role
    FROM ticketing.telegram_users u
    JOIN ticketing.memberships m USING (user_id)
    WHERE u.telegram_user_id = p_requester_telegram_id
      AND m.profile_key = p_profile_key AND m.active;

    IF p_assigned_telegram_id IS NOT NULL THEN
        SELECT u.user_id INTO STRICT v_assigned_id
        FROM ticketing.telegram_users u
        JOIN ticketing.memberships m USING (user_id)
        WHERE u.telegram_user_id = p_assigned_telegram_id
          AND m.profile_key = p_profile_key AND m.active
          AND m.role IN ('admin', 'technician');
    END IF;

    v_ticket_number := ticketing.next_ticket_number(p_profile_key);
    INSERT INTO ticketing.tickets (
        profile_key, ticket_number, requester_user_id, assigned_user_id,
        ticket_type_code, description, request_key, source, legacy_sqlite_id,
        created_at, updated_at, metadata
    ) VALUES (
        p_profile_key, v_ticket_number, v_requester_id, v_assigned_id,
        p_ticket_type_code, p_description, p_request_key, p_source,
        p_legacy_sqlite_id, p_created_at, p_created_at,
        COALESCE(p_metadata, '{}'::jsonb)
    ) RETURNING ticket_id INTO v_ticket_id;

    INSERT INTO ticketing.ticket_events (
        ticket_id, profile_key, actor_user_id, actor_role, event_type, to_status,
        idempotency_key, occurred_at
    ) VALUES (
        v_ticket_id, p_profile_key, v_requester_id, v_requester_role, 'created', NULL,
        'ticket:' || p_request_key || ':created', p_created_at
    );
    RETURN v_ticket_id;
END $$;

UPDATE ticketing.miniapp_idempotency AS idempotency
SET response = idempotency.response || jsonb_build_object(
    'ticket_number', ticket.ticket_number
)
FROM ticketing.tickets AS ticket
WHERE idempotency.state = 'completed'
  AND idempotency.response ? 'ticket_id'
  AND (idempotency.response->>'ticket_id')::bigint = ticket.ticket_id
  AND idempotency.profile_key = ticket.profile_key;

UPDATE ticketing.outbox AS notification
SET payload = notification.payload || jsonb_build_object(
    'ticket_number', ticket.ticket_number
)
FROM ticketing.tickets AS ticket
WHERE notification.ticket_id = ticket.ticket_id
  AND notification.profile_key = ticket.profile_key;

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
       AND e.metadata->>'workflow_action' = 'general_returned') AS review_return_count,
    t.ticket_number
FROM ticketing.tickets t
JOIN ticketing.bot_profiles p USING (profile_key)
JOIN ticketing.ticket_statuses s USING (status_code)
JOIN ticketing.ticket_types tt ON tt.type_code = t.ticket_type_code
JOIN ticketing.telegram_users requester ON requester.user_id = t.requester_user_id
LEFT JOIN ticketing.telegram_users assigned ON assigned.user_id = t.assigned_user_id;

REVOKE ALL ON ticketing.profile_ticket_counters FROM PUBLIC;
REVOKE ALL ON FUNCTION ticketing.next_ticket_number(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ticketing.next_ticket_number(text) TO ticketing_writer;
GRANT EXECUTE ON FUNCTION ticketing.create_ticket(
    text,bigint,bigint,text,timestamptz,text,text,bigint,jsonb,text
) TO ticketing_writer;

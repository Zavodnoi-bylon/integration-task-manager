-- Canonical Telegram ticketing schema.
-- Applied transactionally by scripts/migrate_postgres.py.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ticketing_writer') THEN
        CREATE ROLE ticketing_writer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
END $$;

CREATE SCHEMA IF NOT EXISTS ticketing;
CREATE SCHEMA IF NOT EXISTS reporting;
REVOKE CREATE ON SCHEMA ticketing, reporting FROM PUBLIC;

CREATE TABLE IF NOT EXISTS ticketing.bot_profiles (
    profile_key text PRIMARY KEY CHECK (profile_key ~ '^[a-z][a-z0-9_-]{1,31}$'),
    display_name text NOT NULL CHECK (length(btrim(display_name)) > 0),
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO ticketing.bot_profiles (profile_key, display_name) VALUES
    ('kalisto', 'Kalisto'),
    ('mephisto', 'Mephisto')
ON CONFLICT (profile_key) DO UPDATE SET display_name = EXCLUDED.display_name;

CREATE TABLE IF NOT EXISTS ticketing.ticket_statuses (
    status_code text PRIMARY KEY CHECK (status_code ~ '^[a-z][a-z0-9_]{1,31}$'),
    display_name text NOT NULL CHECK (length(btrim(display_name)) > 0),
    is_terminal boolean NOT NULL,
    sort_order smallint NOT NULL UNIQUE
);
INSERT INTO ticketing.ticket_statuses VALUES
    ('open', 'В работе', false, 10),
    ('completed', 'Завершена', true, 20),
    ('cancelled', 'Отменена', true, 30)
ON CONFLICT (status_code) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    is_terminal = EXCLUDED.is_terminal,
    sort_order = EXCLUDED.sort_order;

CREATE TABLE IF NOT EXISTS ticketing.ticket_types (
    type_code text PRIMARY KEY CHECK (type_code ~ '^[a-z][a-z0-9_]{1,31}$'),
    display_name text NOT NULL CHECK (length(btrim(display_name)) > 0),
    active boolean NOT NULL DEFAULT true,
    sort_order smallint NOT NULL UNIQUE
);
INSERT INTO ticketing.ticket_types VALUES
    ('general', 'Другая задача', true, 10),
    ('destination', 'Destination', true, 20)
ON CONFLICT (type_code) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    active = EXCLUDED.active,
    sort_order = EXCLUDED.sort_order;

CREATE TABLE IF NOT EXISTS ticketing.event_types (
    event_type text PRIMARY KEY CHECK (event_type ~ '^[a-z][a-z0-9_]{1,63}$'),
    display_name text NOT NULL CHECK (length(btrim(display_name)) > 0)
);
INSERT INTO ticketing.event_types VALUES
    ('created', 'Создание'),
    ('status_changed', 'Изменение статуса'),
    ('assigned', 'Назначение исполнителя'),
    ('public_message', 'Публичное сообщение'),
    ('internal_note', 'Внутренняя заметка')
ON CONFLICT (event_type) DO UPDATE SET display_name = EXCLUDED.display_name;

CREATE TABLE IF NOT EXISTS ticketing.telegram_users (
    user_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    telegram_user_id bigint NOT NULL UNIQUE CHECK (telegram_user_id > 0),
    username text,
    display_name text NOT NULL CHECK (length(btrim(display_name)) > 0),
    first_seen_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (last_seen_at >= first_seen_at)
);

CREATE TABLE IF NOT EXISTS ticketing.memberships (
    profile_key text NOT NULL REFERENCES ticketing.bot_profiles(profile_key),
    user_id bigint NOT NULL REFERENCES ticketing.telegram_users(user_id),
    role text NOT NULL CHECK (role IN ('admin', 'technician', 'client')),
    active boolean NOT NULL DEFAULT true,
    joined_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (profile_key, user_id)
);
CREATE INDEX IF NOT EXISTS memberships_profile_role_idx
    ON ticketing.memberships (profile_key, role, active, user_id);

CREATE TABLE IF NOT EXISTS ticketing.tickets (
    ticket_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    profile_key text NOT NULL REFERENCES ticketing.bot_profiles(profile_key),
    requester_user_id bigint NOT NULL,
    assigned_user_id bigint,
    status_code text NOT NULL DEFAULT 'open'
        REFERENCES ticketing.ticket_statuses(status_code),
    ticket_type_code text NOT NULL DEFAULT 'general'
        REFERENCES ticketing.ticket_types(type_code),
    title text,
    description text NOT NULL CHECK (length(btrim(description)) > 0),
    request_key text NOT NULL,
    source text NOT NULL DEFAULT 'telegram' CHECK (source IN ('telegram', 'ui', 'import')),
    legacy_sqlite_id bigint,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    closed_at timestamptz,
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (ticket_id, profile_key),
    UNIQUE (profile_key, request_key),
    UNIQUE (profile_key, legacy_sqlite_id),
    FOREIGN KEY (profile_key, requester_user_id)
        REFERENCES ticketing.memberships(profile_key, user_id),
    FOREIGN KEY (profile_key, assigned_user_id)
        REFERENCES ticketing.memberships(profile_key, user_id),
    CHECK (updated_at >= created_at),
    CHECK (closed_at IS NULL OR closed_at >= created_at),
    CHECK (jsonb_typeof(metadata) = 'object')
);
ALTER TABLE ticketing.tickets
    ADD COLUMN IF NOT EXISTS ticket_type_code text DEFAULT 'general'
        REFERENCES ticketing.ticket_types(type_code);
UPDATE ticketing.tickets SET ticket_type_code = 'general'
WHERE ticket_type_code IS NULL;
ALTER TABLE ticketing.tickets
    ALTER COLUMN ticket_type_code SET DEFAULT 'general',
    ALTER COLUMN ticket_type_code SET NOT NULL;
CREATE INDEX IF NOT EXISTS tickets_profile_status_created_idx
    ON ticketing.tickets (profile_key, status_code, created_at DESC, ticket_id DESC);
CREATE INDEX IF NOT EXISTS tickets_requester_status_idx
    ON ticketing.tickets (profile_key, requester_user_id, status_code, ticket_id DESC);
CREATE INDEX IF NOT EXISTS tickets_assigned_status_idx
    ON ticketing.tickets (profile_key, assigned_user_id, status_code, ticket_id DESC)
    WHERE assigned_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS tickets_profile_type_status_created_idx
    ON ticketing.tickets (
        profile_key, ticket_type_code, status_code, created_at DESC, ticket_id DESC
    );

CREATE TABLE IF NOT EXISTS ticketing.ticket_messages (
    message_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticket_id bigint NOT NULL,
    profile_key text NOT NULL,
    author_user_id bigint NOT NULL REFERENCES ticketing.telegram_users(user_id),
    author_role text NOT NULL CHECK (author_role IN ('admin', 'technician', 'client')),
    visibility text NOT NULL CHECK (visibility IN ('public', 'internal')),
    body text NOT NULL CHECK (length(btrim(body)) > 0),
    idempotency_key text NOT NULL,
    reply_to_message_id bigint,
    telegram_chat_id bigint,
    telegram_message_id bigint,
    sent_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (message_id, ticket_id),
    UNIQUE (profile_key, idempotency_key),
    FOREIGN KEY (ticket_id, profile_key)
        REFERENCES ticketing.tickets(ticket_id, profile_key),
    FOREIGN KEY (reply_to_message_id, ticket_id)
        REFERENCES ticketing.ticket_messages(message_id, ticket_id),
    CHECK (jsonb_typeof(metadata) = 'object')
);
CREATE INDEX IF NOT EXISTS ticket_messages_ticket_time_idx
    ON ticketing.ticket_messages (ticket_id, sent_at, message_id);
CREATE INDEX IF NOT EXISTS ticket_messages_public_idx
    ON ticketing.ticket_messages (ticket_id, sent_at, message_id)
    WHERE visibility = 'public';

CREATE TABLE IF NOT EXISTS ticketing.ticket_attachments (
    attachment_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    message_id bigint NOT NULL REFERENCES ticketing.ticket_messages(message_id),
    kind text NOT NULL CHECK (kind IN ('document', 'photo', 'video', 'audio', 'voice', 'other')),
    telegram_file_id text NOT NULL,
    telegram_file_unique_id text,
    file_name text,
    mime_type text,
    size_bytes bigint CHECK (size_bytes IS NULL OR size_bytes >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (message_id, telegram_file_id)
);
CREATE INDEX IF NOT EXISTS ticket_attachments_message_idx
    ON ticketing.ticket_attachments (message_id, attachment_id);

CREATE TABLE IF NOT EXISTS ticketing.ticket_events (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticket_id bigint NOT NULL,
    profile_key text NOT NULL,
    actor_user_id bigint REFERENCES ticketing.telegram_users(user_id),
    actor_role text CHECK (actor_role IN ('admin', 'technician', 'client', 'system')),
    event_type text NOT NULL REFERENCES ticketing.event_types(event_type),
    from_status text REFERENCES ticketing.ticket_statuses(status_code),
    to_status text REFERENCES ticketing.ticket_statuses(status_code),
    message_id bigint,
    idempotency_key text NOT NULL,
    occurred_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (profile_key, idempotency_key),
    FOREIGN KEY (ticket_id, profile_key)
        REFERENCES ticketing.tickets(ticket_id, profile_key),
    FOREIGN KEY (message_id, ticket_id)
        REFERENCES ticketing.ticket_messages(message_id, ticket_id),
    CHECK ((event_type = 'status_changed') = (to_status IS NOT NULL)),
    CHECK (jsonb_typeof(metadata) = 'object')
);
CREATE INDEX IF NOT EXISTS ticket_events_ticket_time_idx
    ON ticketing.ticket_events (ticket_id, occurred_at, event_id);
CREATE INDEX IF NOT EXISTS ticket_events_type_time_idx
    ON ticketing.ticket_events (profile_key, event_type, occurred_at, event_id);

CREATE TABLE IF NOT EXISTS ticketing.telegram_updates (
    profile_key text NOT NULL REFERENCES ticketing.bot_profiles(profile_key),
    update_id bigint NOT NULL,
    status text NOT NULL CHECK (status IN ('received', 'processed', 'failed')),
    received_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    error_code text,
    PRIMARY KEY (profile_key, update_id),
    CHECK ((status = 'processed') = (processed_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS ticketing.outbox (
    outbox_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    profile_key text NOT NULL REFERENCES ticketing.bot_profiles(profile_key),
    ticket_id bigint,
    event_id bigint REFERENCES ticketing.ticket_events(event_id),
    recipient_user_id bigint NOT NULL REFERENCES ticketing.telegram_users(user_id),
    notification_type text NOT NULL,
    deduplication_key text NOT NULL,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sending', 'sent', 'failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz,
    last_error_code text,
    UNIQUE (profile_key, deduplication_key),
    FOREIGN KEY (ticket_id, profile_key)
        REFERENCES ticketing.tickets(ticket_id, profile_key)
);
CREATE INDEX IF NOT EXISTS outbox_pending_idx
    ON ticketing.outbox (available_at, outbox_id)
    WHERE status IN ('pending', 'failed');

CREATE OR REPLACE FUNCTION ticketing.register_member(
    p_profile_key text,
    p_telegram_user_id bigint,
    p_username text,
    p_display_name text,
    p_role text,
    p_seen_at timestamptz
) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, ticketing
AS $$
DECLARE v_user_id bigint;
BEGIN
    IF p_role NOT IN ('admin', 'technician', 'client') THEN
        RAISE EXCEPTION 'invalid_membership_role';
    END IF;
    INSERT INTO ticketing.telegram_users (
        telegram_user_id, username, display_name, first_seen_at, last_seen_at
    ) VALUES (
        p_telegram_user_id, NULLIF(p_username, ''), p_display_name, p_seen_at, p_seen_at
    )
    ON CONFLICT (telegram_user_id) DO UPDATE SET
        username = EXCLUDED.username,
        display_name = EXCLUDED.display_name,
        last_seen_at = GREATEST(ticketing.telegram_users.last_seen_at, EXCLUDED.last_seen_at)
    RETURNING user_id INTO v_user_id;

    INSERT INTO ticketing.memberships (
        profile_key, user_id, role, joined_at, updated_at
    ) VALUES (
        p_profile_key, v_user_id, p_role, p_seen_at, now()
    )
    ON CONFLICT (profile_key, user_id) DO UPDATE SET
        role = EXCLUDED.role,
        active = true,
        updated_at = now();
    RETURN v_user_id;
END $$;

CREATE OR REPLACE FUNCTION ticketing.sync_configured_roles(
    p_profile_key text,
    p_admin_telegram_ids bigint[],
    p_technician_telegram_ids bigint[]
) RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, ticketing
AS $$
DECLARE v_changed integer;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM ticketing.bot_profiles WHERE profile_key = p_profile_key
    ) THEN
        RAISE EXCEPTION 'unknown_bot_profile';
    END IF;

    UPDATE ticketing.memberships membership
    SET role = CASE
            WHEN user_account.telegram_user_id = ANY(
                COALESCE(p_admin_telegram_ids, ARRAY[]::bigint[])
            ) THEN 'admin'
            WHEN user_account.telegram_user_id = ANY(
                COALESCE(p_technician_telegram_ids, ARRAY[]::bigint[])
            ) THEN 'technician'
            ELSE 'client'
        END,
        active = true,
        updated_at = now()
    FROM ticketing.telegram_users user_account
    WHERE membership.profile_key = p_profile_key
      AND membership.user_id = user_account.user_id
      AND (
        NOT membership.active
        OR membership.role IS DISTINCT FROM CASE
            WHEN user_account.telegram_user_id = ANY(
                COALESCE(p_admin_telegram_ids, ARRAY[]::bigint[])
            ) THEN 'admin'
            WHEN user_account.telegram_user_id = ANY(
                COALESCE(p_technician_telegram_ids, ARRAY[]::bigint[])
            ) THEN 'technician'
            ELSE 'client'
        END
      );
    GET DIAGNOSTICS v_changed = ROW_COUNT;
    RETURN v_changed;
END $$;

DROP FUNCTION IF EXISTS ticketing.create_ticket(
    text, bigint, bigint, text, timestamptz, text, text, bigint
);
DROP FUNCTION IF EXISTS ticketing.create_ticket(
    text, bigint, bigint, text, timestamptz, text, text, bigint, jsonb
);
DROP FUNCTION IF EXISTS ticketing.create_ticket(
    text, bigint, bigint, text, timestamptz, text, text, bigint, jsonb, text
);
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

    INSERT INTO ticketing.tickets (
        profile_key, requester_user_id, assigned_user_id, ticket_type_code, description,
        request_key, source, legacy_sqlite_id, created_at, updated_at, metadata
    ) VALUES (
        p_profile_key, v_requester_id, v_assigned_id, p_ticket_type_code, p_description,
        p_request_key, p_source, p_legacy_sqlite_id, p_created_at, p_created_at,
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

CREATE OR REPLACE FUNCTION ticketing.add_message(
    p_profile_key text,
    p_ticket_id bigint,
    p_actor_telegram_id bigint,
    p_visibility text,
    p_body text,
    p_sent_at timestamptz,
    p_idempotency_key text,
    p_telegram_chat_id bigint DEFAULT NULL,
    p_telegram_message_id bigint DEFAULT NULL
) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, ticketing
AS $$
DECLARE
    v_message_id bigint;
    v_actor_id bigint;
    v_actor_role text;
    v_requester_id bigint;
    v_status text;
BEGIN
    SELECT m.message_id INTO v_message_id
    FROM ticketing.ticket_messages m
    WHERE m.profile_key = p_profile_key AND m.idempotency_key = p_idempotency_key;
    IF v_message_id IS NOT NULL THEN RETURN v_message_id; END IF;

    SELECT u.user_id, m.role INTO STRICT v_actor_id, v_actor_role
    FROM ticketing.telegram_users u
    JOIN ticketing.memberships m USING (user_id)
    WHERE u.telegram_user_id = p_actor_telegram_id
      AND m.profile_key = p_profile_key AND m.active;
    SELECT requester_user_id, status_code INTO STRICT v_requester_id, v_status
    FROM ticketing.tickets
    WHERE ticket_id = p_ticket_id AND profile_key = p_profile_key
    FOR UPDATE;

    IF v_status <> 'open' THEN RAISE EXCEPTION 'ticket_is_not_open'; END IF;
    IF v_actor_role = 'client' AND v_actor_id <> v_requester_id THEN
        RAISE EXCEPTION 'ticket_access_denied';
    END IF;
    IF p_visibility = 'internal' AND v_actor_role = 'client' THEN
        RAISE EXCEPTION 'internal_note_access_denied';
    END IF;
    IF p_visibility NOT IN ('public', 'internal') THEN
        RAISE EXCEPTION 'invalid_message_visibility';
    END IF;

    INSERT INTO ticketing.ticket_messages (
        ticket_id, profile_key, author_user_id, author_role, visibility, body,
        idempotency_key, telegram_chat_id, telegram_message_id, sent_at
    ) VALUES (
        p_ticket_id, p_profile_key, v_actor_id, v_actor_role, p_visibility, p_body,
        p_idempotency_key, p_telegram_chat_id, p_telegram_message_id, p_sent_at
    ) RETURNING message_id INTO v_message_id;

    INSERT INTO ticketing.ticket_events (
        ticket_id, profile_key, actor_user_id, actor_role, event_type, message_id,
        idempotency_key, occurred_at
    ) VALUES (
        p_ticket_id, p_profile_key, v_actor_id, v_actor_role,
        CASE WHEN p_visibility = 'public' THEN 'public_message' ELSE 'internal_note' END,
        v_message_id, 'message:' || p_idempotency_key, p_sent_at
    );
    UPDATE ticketing.tickets SET
        updated_at = GREATEST(updated_at, p_sent_at), version = version + 1
    WHERE ticket_id = p_ticket_id;
    RETURN v_message_id;
END $$;

CREATE OR REPLACE FUNCTION ticketing.change_status(
    p_profile_key text,
    p_ticket_id bigint,
    p_actor_telegram_id bigint,
    p_to_status text,
    p_occurred_at timestamptz,
    p_idempotency_key text
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, ticketing
AS $$
DECLARE
    v_actor_id bigint;
    v_actor_role text;
    v_requester_id bigint;
    v_from_status text;
BEGIN
    IF EXISTS (
        SELECT 1 FROM ticketing.ticket_events
        WHERE profile_key = p_profile_key AND idempotency_key = p_idempotency_key
    ) THEN RETURN false; END IF;

    SELECT u.user_id, m.role INTO STRICT v_actor_id, v_actor_role
    FROM ticketing.telegram_users u
    JOIN ticketing.memberships m USING (user_id)
    WHERE u.telegram_user_id = p_actor_telegram_id
      AND m.profile_key = p_profile_key AND m.active;
    SELECT requester_user_id, status_code INTO STRICT v_requester_id, v_from_status
    FROM ticketing.tickets
    WHERE ticket_id = p_ticket_id AND profile_key = p_profile_key
    FOR UPDATE;

    IF v_from_status = p_to_status THEN RETURN false; END IF;
    IF v_actor_role = 'client' AND v_actor_id <> v_requester_id THEN
        RAISE EXCEPTION 'ticket_access_denied';
    END IF;
    IF p_to_status = 'completed' AND v_actor_role NOT IN ('admin', 'technician') THEN
        RAISE EXCEPTION 'complete_access_denied';
    END IF;
    IF p_to_status = 'open' AND v_from_status = 'completed'
       AND v_actor_id <> v_requester_id AND v_actor_role NOT IN ('admin', 'technician') THEN
        RAISE EXCEPTION 'reopen_access_denied';
    END IF;
    IF p_to_status = 'open' AND v_from_status NOT IN ('completed') THEN
        RAISE EXCEPTION 'invalid_reopen_transition';
    END IF;
    IF p_to_status NOT IN ('open', 'completed', 'cancelled') THEN
        RAISE EXCEPTION 'invalid_ticket_status';
    END IF;

    UPDATE ticketing.tickets SET
        status_code = p_to_status,
        updated_at = GREATEST(updated_at, p_occurred_at),
        closed_at = CASE WHEN p_to_status IN ('completed', 'cancelled')
                         THEN p_occurred_at ELSE NULL END,
        version = version + 1
    WHERE ticket_id = p_ticket_id;
    INSERT INTO ticketing.ticket_events (
        ticket_id, profile_key, actor_user_id, actor_role, event_type,
        from_status, to_status, idempotency_key, occurred_at
    ) VALUES (
        p_ticket_id, p_profile_key, v_actor_id, v_actor_role, 'status_changed',
        v_from_status, p_to_status, p_idempotency_key, p_occurred_at
    );
    RETURN true;
END $$;

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
         THEN EXTRACT(epoch FROM t.closed_at - t.created_at)::bigint END AS resolution_seconds,
    (SELECT count(*) FROM ticketing.ticket_events e
     WHERE e.ticket_id = t.ticket_id AND e.event_type = 'status_changed'
       AND e.from_status = 'completed' AND e.to_status = 'open') AS reopen_count,
    (SELECT count(*) FROM ticketing.ticket_messages m
     WHERE m.ticket_id = t.ticket_id AND m.visibility = 'public') AS public_message_count,
    (SELECT count(*) FROM ticketing.ticket_messages m
     WHERE m.ticket_id = t.ticket_id AND m.visibility = 'internal') AS internal_note_count,
    t.ticket_type_code AS ticket_type,
    tt.display_name AS ticket_type_name
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
    count(*) FILTER (WHERE e.event_type = 'status_changed' AND e.to_status = 'completed')
        AS completed_count,
    count(*) FILTER (WHERE e.event_type = 'status_changed' AND e.to_status = 'open'
                       AND e.from_status = 'completed') AS reopened_count,
    count(*) FILTER (WHERE e.event_type = 'public_message') AS public_message_count,
    count(*) FILTER (WHERE e.event_type = 'internal_note') AS internal_note_count
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

CREATE OR REPLACE VIEW reporting.ticket_conversation AS
SELECT
    m.ticket_id,
    m.profile_key,
    m.message_id,
    m.visibility,
    author.telegram_user_id AS author_telegram_id,
    author.username AS author_username,
    m.author_role,
    m.body,
    m.sent_at,
    m.recorded_at
FROM ticketing.ticket_messages m
JOIN ticketing.telegram_users author ON author.user_id = m.author_user_id;

REVOKE ALL ON ALL TABLES IN SCHEMA ticketing FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA ticketing FROM PUBLIC;
GRANT USAGE ON SCHEMA ticketing TO ticketing_writer;
GRANT SELECT ON ALL TABLES IN SCHEMA ticketing TO ticketing_writer;
GRANT INSERT, UPDATE ON ticketing.telegram_updates, ticketing.outbox TO ticketing_writer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ticketing TO ticketing_writer;
GRANT EXECUTE ON FUNCTION ticketing.register_member(text,bigint,text,text,text,timestamptz)
    TO ticketing_writer;
GRANT EXECUTE ON FUNCTION ticketing.sync_configured_roles(text,bigint[],bigint[])
    TO ticketing_writer;
GRANT EXECUTE ON FUNCTION ticketing.create_ticket(text,bigint,bigint,text,timestamptz,text,text,bigint,jsonb,text)
    TO ticketing_writer;
GRANT EXECUTE ON FUNCTION ticketing.add_message(text,bigint,bigint,text,text,timestamptz,text,bigint,bigint)
    TO ticketing_writer;
GRANT EXECUTE ON FUNCTION ticketing.change_status(text,bigint,bigint,text,timestamptz,text)
    TO ticketing_writer;
GRANT USAGE ON SCHEMA reporting TO ticketing_writer;
GRANT SELECT ON reporting.ticket_facts, reporting.ticket_daily_flow,
    reporting.ticket_current_backlog, reporting.ticket_conversation TO ticketing_writer;

DO $$
BEGIN
    -- Team Activity Explorer owns this optional reader role. A standalone bot
    -- database must still be migratable when the dashboard is absent.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'activity_reader') THEN
        GRANT USAGE ON SCHEMA ticketing, reporting TO activity_reader;
        GRANT SELECT ON ticketing.bot_profiles, ticketing.ticket_statuses,
            ticketing.ticket_types, ticketing.telegram_users, ticketing.memberships,
            ticketing.tickets, ticketing.ticket_messages, ticketing.ticket_attachments,
            ticketing.ticket_events TO activity_reader;
        GRANT SELECT ON reporting.ticket_facts, reporting.ticket_daily_flow,
            reporting.ticket_current_backlog, reporting.ticket_conversation
            TO activity_reader;
    END IF;
END $$;

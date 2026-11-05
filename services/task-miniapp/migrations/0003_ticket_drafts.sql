CREATE TABLE IF NOT EXISTS ticketing.ticket_draft_intake (
    profile_key text NOT NULL REFERENCES ticketing.bot_profiles(profile_key),
    author_user_id bigint NOT NULL REFERENCES ticketing.telegram_users(user_id),
    chat_id bigint NOT NULL,
    telegram_message_id bigint NOT NULL,
    body text NOT NULL CHECK (length(btrim(body)) > 0),
    received_at timestamptz NOT NULL,
    PRIMARY KEY (profile_key, author_user_id, chat_id)
);

CREATE TABLE IF NOT EXISTS ticketing.ticket_drafts (
    draft_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    profile_key text NOT NULL REFERENCES ticketing.bot_profiles(profile_key),
    author_user_id bigint NOT NULL REFERENCES ticketing.telegram_users(user_id),
    status text NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'submitted', 'cancelled')),
    source_body text NOT NULL CHECK (length(btrim(source_body)) > 0),
    client_comment text NOT NULL DEFAULT '',
    edited_body text NOT NULL CHECK (length(btrim(edited_body)) > 0),
    advertiser_kind text CHECK (advertiser_kind IN ('catalog', 'new')),
    advertiser_key text,
    advertiser_name text,
    cap integer CHECK (cap > 0 AND cap <= 1000000),
    source_chat_id bigint NOT NULL,
    source_message_id bigint NOT NULL,
    forward_origin_type text,
    forward_origin_name text,
    forward_origin_date timestamptz,
    ticket_id bigint,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    submitted_at timestamptz,
    cancelled_at timestamptz,
    UNIQUE (profile_key, source_chat_id, source_message_id),
    FOREIGN KEY (ticket_id, profile_key)
        REFERENCES ticketing.tickets(ticket_id, profile_key),
    CHECK (
        (status = 'draft' AND ticket_id IS NULL AND submitted_at IS NULL
            AND cancelled_at IS NULL)
        OR (status = 'submitted' AND ticket_id IS NOT NULL
            AND submitted_at IS NOT NULL AND cancelled_at IS NULL)
        OR (status = 'cancelled' AND ticket_id IS NULL
            AND submitted_at IS NULL AND cancelled_at IS NOT NULL)
    ),
    CHECK (
        advertiser_kind IS NULL
        OR (advertiser_kind = 'catalog' AND advertiser_key IS NOT NULL
            AND advertiser_name IS NULL)
        OR (advertiser_kind = 'new' AND advertiser_name IS NOT NULL
            AND advertiser_key IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS ticket_drafts_author_idx
    ON ticketing.ticket_drafts (profile_key, author_user_id, updated_at DESC)
    WHERE status = 'draft';

CREATE OR REPLACE FUNCTION ticketing.save_draft_intake(
    p_profile_key text,
    p_author_telegram_id bigint,
    p_chat_id bigint,
    p_telegram_message_id bigint,
    p_body text,
    p_received_at timestamptz
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, ticketing
AS $$
DECLARE v_author_id bigint;
BEGIN
    SELECT u.user_id INTO STRICT v_author_id
    FROM ticketing.telegram_users u
    JOIN ticketing.memberships m USING (user_id)
    WHERE u.telegram_user_id = p_author_telegram_id
      AND m.profile_key = p_profile_key AND m.active;

    INSERT INTO ticketing.ticket_draft_intake (
        profile_key, author_user_id, chat_id, telegram_message_id, body, received_at
    ) VALUES (
        p_profile_key, v_author_id, p_chat_id, p_telegram_message_id,
        btrim(p_body), p_received_at
    )
    ON CONFLICT (profile_key, author_user_id, chat_id) DO UPDATE SET
        telegram_message_id = EXCLUDED.telegram_message_id,
        body = EXCLUDED.body,
        received_at = EXCLUDED.received_at;
END $$;

CREATE OR REPLACE FUNCTION ticketing.create_forwarded_draft(
    p_profile_key text,
    p_author_telegram_id bigint,
    p_source_chat_id bigint,
    p_source_message_id bigint,
    p_source_body text,
    p_forward_origin_type text,
    p_forward_origin_name text,
    p_forward_origin_date timestamptz,
    p_created_at timestamptz
) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, ticketing
AS $$
DECLARE
    v_author_id bigint;
    v_comment text := '';
    v_draft_id bigint;
BEGIN
    SELECT u.user_id INTO STRICT v_author_id
    FROM ticketing.telegram_users u
    JOIN ticketing.memberships m USING (user_id)
    WHERE u.telegram_user_id = p_author_telegram_id
      AND m.profile_key = p_profile_key AND m.active;

    SELECT body INTO v_comment
    FROM ticketing.ticket_draft_intake
    WHERE profile_key = p_profile_key
      AND author_user_id = v_author_id
      AND chat_id = p_source_chat_id
      AND received_at BETWEEN p_created_at - interval '10 minutes' AND p_created_at
    FOR UPDATE;

    DELETE FROM ticketing.ticket_draft_intake
    WHERE profile_key = p_profile_key
      AND author_user_id = v_author_id
      AND chat_id = p_source_chat_id;

    INSERT INTO ticketing.ticket_drafts (
        profile_key, author_user_id, source_body, client_comment, edited_body,
        source_chat_id, source_message_id, forward_origin_type,
        forward_origin_name, forward_origin_date, created_at, updated_at
    ) VALUES (
        p_profile_key, v_author_id, btrim(p_source_body), COALESCE(v_comment, ''),
        btrim(p_source_body), p_source_chat_id, p_source_message_id,
        p_forward_origin_type, p_forward_origin_name, p_forward_origin_date,
        p_created_at, p_created_at
    )
    ON CONFLICT (profile_key, source_chat_id, source_message_id) DO UPDATE SET
        source_message_id = EXCLUDED.source_message_id
    RETURNING draft_id INTO v_draft_id;
    RETURN v_draft_id;
END $$;

REVOKE ALL ON ticketing.ticket_draft_intake, ticketing.ticket_drafts FROM PUBLIC;
REVOKE ALL ON FUNCTION ticketing.save_draft_intake(text,bigint,bigint,bigint,text,timestamptz)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION ticketing.create_forwarded_draft(
    text,bigint,bigint,bigint,text,text,text,timestamptz,timestamptz
) FROM PUBLIC;

GRANT SELECT, INSERT, UPDATE, DELETE ON
    ticketing.ticket_draft_intake, ticketing.ticket_drafts TO ticketing_writer;
GRANT USAGE, SELECT ON SEQUENCE ticketing.ticket_drafts_draft_id_seq
    TO ticketing_writer;
GRANT EXECUTE ON FUNCTION ticketing.save_draft_intake(
    text,bigint,bigint,bigint,text,timestamptz
) TO ticketing_writer;
GRANT EXECUTE ON FUNCTION ticketing.create_forwarded_draft(
    text,bigint,bigint,bigint,text,text,text,timestamptz,timestamptz
) TO ticketing_writer;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'activity_reader') THEN
        GRANT SELECT ON ticketing.ticket_drafts TO activity_reader;
    END IF;
END $$;

CREATE TABLE ticketing.forward_batches (
    batch_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    profile_key text NOT NULL REFERENCES ticketing.bot_profiles(profile_key),
    author_user_id bigint NOT NULL REFERENCES ticketing.telegram_users(user_id),
    chat_id bigint NOT NULL,
    state text NOT NULL DEFAULT 'collecting'
        CHECK (state IN ('collecting', 'ready', 'closed')),
    received_at timestamptz NOT NULL,
    due_at timestamptz NOT NULL,
    window_seconds integer NOT NULL CHECK (window_seconds BETWEEN 1 AND 300),
    draft_id bigint UNIQUE REFERENCES ticketing.ticket_drafts(draft_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    closed_at timestamptz,
    CHECK (due_at = received_at + make_interval(secs => window_seconds)),
    CHECK (
        (state IN ('collecting', 'ready') AND draft_id IS NULL AND closed_at IS NULL)
        OR (state = 'closed' AND draft_id IS NOT NULL AND closed_at IS NOT NULL)
    )
);

ALTER TABLE ticketing.ticket_draft_intake
    ADD COLUMN consumed_at timestamptz;

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
        profile_key, author_user_id, chat_id, telegram_message_id, body,
        received_at, consumed_at
    ) VALUES (
        p_profile_key, v_author_id, p_chat_id, p_telegram_message_id,
        btrim(p_body), p_received_at, NULL
    )
    ON CONFLICT (profile_key, author_user_id, chat_id) DO UPDATE SET
        telegram_message_id = EXCLUDED.telegram_message_id,
        body = EXCLUDED.body,
        received_at = EXCLUDED.received_at,
        consumed_at = NULL;
END $$;

CREATE UNIQUE INDEX forward_batches_collecting_idx
    ON ticketing.forward_batches(profile_key, author_user_id, chat_id)
    WHERE state = 'collecting';
CREATE INDEX forward_batches_due_idx
    ON ticketing.forward_batches(profile_key, due_at, batch_id)
    WHERE state IN ('collecting', 'ready');

CREATE TABLE ticketing.forward_batch_messages (
    batch_message_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id bigint NOT NULL REFERENCES ticketing.forward_batches(batch_id),
    profile_key text NOT NULL REFERENCES ticketing.bot_profiles(profile_key),
    chat_id bigint NOT NULL,
    telegram_message_id bigint NOT NULL,
    body text NOT NULL CHECK (length(btrim(body)) > 0),
    received_at timestamptz NOT NULL,
    forward_origin_type text,
    forward_origin_name text,
    forward_origin_date timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (profile_key, chat_id, telegram_message_id),
    UNIQUE (batch_id, telegram_message_id)
);

CREATE INDEX forward_batch_messages_order_idx
    ON ticketing.forward_batch_messages(batch_id, telegram_message_id);

GRANT SELECT, INSERT, UPDATE ON ticketing.forward_batches,
    ticketing.forward_batch_messages TO ticketing_writer;
GRANT USAGE, SELECT ON SEQUENCE ticketing.forward_batches_batch_id_seq,
    ticketing.forward_batch_messages_batch_message_id_seq TO ticketing_writer;

REVOKE ALL ON ticketing.forward_batches,
    ticketing.forward_batch_messages FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'activity_reader') THEN
        REVOKE ALL ON ticketing.forward_batches,
            ticketing.forward_batch_messages FROM activity_reader;
    END IF;
END $$;

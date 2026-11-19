ALTER TABLE ticketing.memberships
    DROP CONSTRAINT IF EXISTS memberships_role_check;
ALTER TABLE ticketing.memberships
    ADD CONSTRAINT memberships_role_check
    CHECK (role IN ('admin', 'technician', 'supervisor', 'client'));

ALTER TABLE ticketing.ticket_messages
    DROP CONSTRAINT IF EXISTS ticket_messages_author_role_check;
ALTER TABLE ticketing.ticket_messages
    ADD CONSTRAINT ticket_messages_author_role_check
    CHECK (author_role IN ('admin', 'technician', 'supervisor', 'client'));

ALTER TABLE ticketing.ticket_events
    DROP CONSTRAINT IF EXISTS ticket_events_actor_role_check;
ALTER TABLE ticketing.ticket_events
    ADD CONSTRAINT ticket_events_actor_role_check
    CHECK (actor_role IN ('admin', 'technician', 'supervisor', 'client', 'system'));

CREATE TABLE ticketing.supervisor_subordinates (
    profile_key text NOT NULL,
    supervisor_user_id bigint NOT NULL,
    subordinate_user_id bigint NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (profile_key, supervisor_user_id, subordinate_user_id),
    FOREIGN KEY (profile_key, supervisor_user_id)
        REFERENCES ticketing.memberships(profile_key, user_id),
    FOREIGN KEY (profile_key, subordinate_user_id)
        REFERENCES ticketing.memberships(profile_key, user_id),
    CHECK (supervisor_user_id <> subordinate_user_id)
);

CREATE INDEX supervisor_subordinates_lookup_idx
    ON ticketing.supervisor_subordinates(
        profile_key, supervisor_user_id, active, subordinate_user_id
    );

CREATE OR REPLACE FUNCTION ticketing.validate_supervisor_subordinate()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, ticketing
AS $$
DECLARE v_role text;
BEGIN
    SELECT role INTO STRICT v_role
    FROM ticketing.memberships
    WHERE profile_key = NEW.profile_key
      AND user_id = NEW.supervisor_user_id
      AND active;
    IF v_role <> 'supervisor' THEN
        RAISE EXCEPTION 'supervisor_membership_required';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM ticketing.memberships
        WHERE profile_key = NEW.profile_key
          AND user_id = NEW.subordinate_user_id
          AND active
    ) THEN
        RAISE EXCEPTION 'active_subordinate_membership_required';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;

CREATE TRIGGER supervisor_subordinates_validate
BEFORE INSERT OR UPDATE ON ticketing.supervisor_subordinates
FOR EACH ROW EXECUTE FUNCTION ticketing.validate_supervisor_subordinate();

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
    IF p_role NOT IN ('admin', 'technician', 'supervisor', 'client') THEN
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
        role = CASE
            WHEN EXCLUDED.role IN ('admin', 'technician') THEN EXCLUDED.role
            WHEN ticketing.memberships.role = 'supervisor' THEN 'supervisor'
            ELSE EXCLUDED.role
        END,
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
        membership.role <> 'supervisor'
        OR user_account.telegram_user_id = ANY(
            COALESCE(p_admin_telegram_ids, ARRAY[]::bigint[])
        )
        OR user_account.telegram_user_id = ANY(
            COALESCE(p_technician_telegram_ids, ARRAY[]::bigint[])
        )
      )
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

GRANT SELECT ON ticketing.supervisor_subordinates TO ticketing_writer;
REVOKE ALL ON ticketing.supervisor_subordinates FROM PUBLIC;

BEGIN;

CREATE SCHEMA IF NOT EXISTS aio_alerts;
REVOKE ALL ON SCHEMA aio_alerts FROM PUBLIC;

CREATE TABLE IF NOT EXISTS aio_alerts.monitor_state (
    tenant_uuid text PRIMARY KEY,
    tenant_name text NOT NULL,
    checkpoint_at timestamptz NOT NULL,
    initialized_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    last_success_at timestamptz,
    last_error_code text
);

CREATE TABLE IF NOT EXISTS aio_alerts.alert_events (
    tenant_uuid text NOT NULL,
    tenant_name text NOT NULL,
    conversion_uuid text NOT NULL,
    visit_uuid text NOT NULL,
    conversion_at timestamptz NOT NULL,
    destination_uuid text NOT NULL,
    destination_name text NOT NULL,
    advertiser_uuid text,
    advertiser_name text,
    revenue numeric NOT NULL,
    currency text,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','sending','sent','failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL DEFAULT now(),
    last_attempt_at timestamptz,
    sent_at timestamptz,
    telegram_message_id bigint,
    last_error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_uuid, conversion_uuid)
);

CREATE INDEX IF NOT EXISTS aio_alert_events_delivery_idx
    ON aio_alerts.alert_events (available_at, created_at)
    WHERE status IN ('pending','failed','sending');

GRANT USAGE ON SCHEMA aio_alerts TO :"app_role";
GRANT SELECT, INSERT, UPDATE, DELETE
    ON ALL TABLES IN SCHEMA aio_alerts TO :"app_role";
ALTER DEFAULT PRIVILEGES IN SCHEMA aio_alerts
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_role";

COMMIT;

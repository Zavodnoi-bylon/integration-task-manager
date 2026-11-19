CREATE TABLE ticketing.advertiser_catalog (
    profile_key text NOT NULL
        REFERENCES ticketing.bot_profiles(profile_key) ON DELETE CASCADE,
    catalog_key text NOT NULL CHECK (catalog_key ~ '^[a-f0-9]{12}$'),
    aio_uuid uuid,
    name text NOT NULL CHECK (length(btrim(name)) > 0),
    normalized_name text GENERATED ALWAYS AS (lower(btrim(name))) STORED,
    url_parameters text NOT NULL DEFAULT '',
    technician_note text,
    priority integer NOT NULL DEFAULT 0 CHECK (priority >= 0),
    external_id_policy jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(external_id_policy) = 'array'),
    source text NOT NULL CHECK (source IN ('seed', 'aio')),
    aio_created_at timestamptz,
    aio_updated_at timestamptz,
    aio_archived boolean NOT NULL DEFAULT false,
    last_seen_at timestamptz,
    synced_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (profile_key, catalog_key),
    UNIQUE (profile_key, normalized_name)
);

CREATE UNIQUE INDEX advertiser_catalog_aio_uuid_unique
    ON ticketing.advertiser_catalog(profile_key, aio_uuid)
    WHERE aio_uuid IS NOT NULL;

CREATE INDEX advertiser_catalog_listing
    ON ticketing.advertiser_catalog(profile_key, aio_archived, priority DESC, normalized_name);

CREATE TABLE ticketing.aio_advertiser_sync_state (
    source_key text PRIMARY KEY CHECK (length(btrim(source_key)) > 0),
    latest_created_at timestamptz,
    last_incremental_at timestamptz,
    last_full_at timestamptz,
    last_success_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);

REVOKE ALL ON ticketing.advertiser_catalog FROM PUBLIC;
REVOKE ALL ON ticketing.aio_advertiser_sync_state FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON ticketing.advertiser_catalog TO ticketing_writer;
GRANT SELECT, INSERT, UPDATE ON ticketing.aio_advertiser_sync_state TO ticketing_writer;

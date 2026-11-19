CREATE TABLE ticketing.aio_advertiser_refresh_requests (
    profile_key text PRIMARY KEY
        REFERENCES ticketing.bot_profiles(profile_key) ON DELETE CASCADE,
    requested_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    completed_at timestamptz,
    outcome text CHECK (outcome IN ('succeeded', 'failed')),
    CHECK (started_at IS NULL OR started_at >= requested_at),
    CHECK (completed_at IS NULL OR started_at IS NOT NULL),
    CHECK (completed_at IS NULL OR completed_at >= started_at),
    CHECK (completed_at IS NULL OR outcome IS NOT NULL),
    CHECK (completed_at IS NOT NULL OR outcome IS NULL)
);

REVOKE ALL ON ticketing.aio_advertiser_refresh_requests FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE
    ON ticketing.aio_advertiser_refresh_requests TO ticketing_writer;

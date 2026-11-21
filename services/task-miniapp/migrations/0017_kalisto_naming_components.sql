ALTER TABLE ticketing.ticket_drafts
    ADD COLUMN offer_name text,
    ADD COLUMN platform text,
    ADD COLUMN approach text;

ALTER TABLE ticketing.ticket_drafts
    ADD CONSTRAINT ticket_drafts_naming_components_ck CHECK (
        (offer_name IS NULL OR length(btrim(offer_name)) > 0)
        AND (platform IS NULL OR length(btrim(platform)) > 0)
        AND (approach IS NULL OR length(btrim(approach)) > 0)
    ),
    DROP CONSTRAINT ticket_drafts_general_fields_ck,
    ADD CONSTRAINT ticket_drafts_general_fields_ck CHECK (
        ticket_type IS DISTINCT FROM 'general'
        OR (
            advertiser_kind IS NULL AND advertiser_key IS NULL
            AND advertiser_name IS NULL AND cap IS NULL AND NOT cap_unlimited
            AND naming IS NULL AND offer_name IS NULL AND platform IS NULL
            AND approach IS NULL AND geo IS NULL AND rate IS NULL
            AND currency IS NULL AND landing_url IS NULL
            AND clickid_subid = '' AND additional = ''
        )
    );

ALTER TABLE ticketing.destination_requests
    ADD COLUMN offer_name text,
    ADD COLUMN platform text,
    ADD COLUMN approach text,
    ADD CONSTRAINT destination_requests_naming_components_ck CHECK (
        (
            offer_name IS NULL
            AND platform IS NULL
            AND approach IS NULL
        )
        OR (
            offer_name IS NOT NULL
            AND platform IS NOT NULL
            AND approach IS NOT NULL
            AND length(btrim(offer_name)) > 0
            AND length(btrim(platform)) > 0
            AND length(btrim(approach)) > 0
        )
    );

COMMENT ON COLUMN ticketing.destination_requests.offer_name IS
    'Structured Kalisto naming component; NULL only for legacy requests.';
COMMENT ON COLUMN ticketing.destination_requests.platform IS
    'Structured Kalisto naming component; NULL only for legacy requests.';
COMMENT ON COLUMN ticketing.destination_requests.approach IS
    'Structured Kalisto naming component; NULL only for legacy requests.';

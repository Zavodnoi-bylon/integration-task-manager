ALTER TABLE ticketing.ticket_drafts
    ADD COLUMN ticket_type text
        CHECK (ticket_type IN ('general', 'destination')),
    ADD COLUMN naming text,
    ADD COLUMN geo text,
    ADD COLUMN rate numeric(18, 4),
    ADD COLUMN currency text,
    ADD COLUMN landing_url text,
    ADD COLUMN clickid_subid text NOT NULL DEFAULT '',
    ADD COLUMN additional text NOT NULL DEFAULT '';

ALTER TABLE ticketing.ticket_drafts
    ADD CONSTRAINT ticket_drafts_structured_values_ck CHECK (
        (naming IS NULL OR length(btrim(naming)) > 0)
        AND (geo IS NULL OR length(btrim(geo)) > 0)
        AND (rate IS NULL OR rate > 0)
        AND (currency IS NULL OR length(btrim(currency)) > 0)
        AND (landing_url IS NULL OR length(btrim(landing_url)) > 0)
    ),
    ADD CONSTRAINT ticket_drafts_general_fields_ck CHECK (
        ticket_type IS DISTINCT FROM 'general'
        OR (
            advertiser_kind IS NULL AND advertiser_key IS NULL
            AND advertiser_name IS NULL AND cap IS NULL AND NOT cap_unlimited
            AND naming IS NULL AND geo IS NULL AND rate IS NULL
            AND currency IS NULL AND landing_url IS NULL
            AND clickid_subid = '' AND additional = ''
        )
    );

CREATE TABLE ticketing.destination_requests (
    ticket_id bigint PRIMARY KEY,
    profile_key text NOT NULL,
    advertiser_key text NOT NULL CHECK (length(btrim(advertiser_key)) > 0),
    advertiser_name text NOT NULL CHECK (length(btrim(advertiser_name)) > 0),
    naming text NOT NULL CHECK (length(btrim(naming)) > 0),
    geo text NOT NULL CHECK (length(btrim(geo)) > 0),
    rate numeric(18, 4) NOT NULL CHECK (rate > 0),
    currency text NOT NULL CHECK (length(btrim(currency)) > 0),
    cap integer CHECK (cap > 0 AND cap <= 1000000),
    cap_unlimited boolean NOT NULL DEFAULT false,
    landing_url text NOT NULL CHECK (length(btrim(landing_url)) > 0),
    clickid_subid text NOT NULL DEFAULT '',
    additional text NOT NULL DEFAULT '',
    source_body text NOT NULL CHECK (length(btrim(source_body)) > 0),
    processing_status text NOT NULL
        CHECK (processing_status IN ('validated', 'fallback')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ticket_id, profile_key),
    FOREIGN KEY (ticket_id, profile_key)
        REFERENCES ticketing.tickets(ticket_id, profile_key),
    CHECK (profile_key = 'kalisto'),
    CHECK ((cap IS NOT NULL) <> cap_unlimited)
);

CREATE TABLE ticketing.destination_results (
    result_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticket_id bigint NOT NULL,
    profile_key text NOT NULL,
    result_order integer NOT NULL CHECK (result_order > 0),
    naming text NOT NULL CHECK (length(btrim(naming)) > 0),
    destination_url text NOT NULL CHECK (length(btrim(destination_url)) > 0),
    warnings text[] NOT NULL DEFAULT ARRAY[]::text[],
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ticket_id, result_order),
    FOREIGN KEY (ticket_id, profile_key)
        REFERENCES ticketing.tickets(ticket_id, profile_key)
);

CREATE INDEX destination_results_ticket_order_idx
    ON ticketing.destination_results(ticket_id, result_order);

GRANT SELECT, INSERT ON ticketing.destination_requests,
    ticketing.destination_results TO ticketing_writer;
GRANT USAGE, SELECT ON SEQUENCE ticketing.destination_results_result_id_seq
    TO ticketing_writer;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'activity_reader') THEN
        GRANT SELECT ON ticketing.destination_requests,
            ticketing.destination_results TO activity_reader;
    END IF;
END $$;

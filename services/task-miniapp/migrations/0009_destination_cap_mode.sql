ALTER TABLE ticketing.ticket_drafts
    ADD COLUMN cap_mode text
        CHECK (cap_mode IN ('unlim', 'daily', 'monthly', 'lifetime'));

ALTER TABLE ticketing.ticket_drafts
    ADD CONSTRAINT ticket_drafts_cap_mode_consistency_ck CHECK (
        cap_mode IS NULL
        OR (cap_mode = 'unlim' AND cap_unlimited AND cap IS NULL)
        OR (
            cap_mode IN ('daily', 'monthly', 'lifetime')
            AND NOT cap_unlimited
        )
    ),
    ADD CONSTRAINT ticket_drafts_general_cap_mode_ck CHECK (
        ticket_type IS DISTINCT FROM 'general' OR cap_mode IS NULL
    );

ALTER TABLE ticketing.destination_requests
    ADD COLUMN cap_mode text
        CHECK (cap_mode IN ('unlim', 'daily', 'monthly', 'lifetime'));

ALTER TABLE ticketing.destination_requests
    ADD CONSTRAINT destination_requests_cap_mode_consistency_ck CHECK (
        cap_mode IS NULL
        OR (cap_mode = 'unlim' AND cap_unlimited AND cap IS NULL)
        OR (
            cap_mode IN ('daily', 'monthly', 'lifetime')
            AND NOT cap_unlimited
            AND cap IS NOT NULL
        )
    );

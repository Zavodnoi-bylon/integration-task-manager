ALTER TABLE ticketing.tickets
    ADD COLUMN new_advertiser boolean NOT NULL DEFAULT false,
    ADD CONSTRAINT tickets_new_advertiser_ck CHECK (
        NOT new_advertiser OR ticket_type_code = 'destination'
    );

ALTER TABLE ticketing.destination_requests
    DROP CONSTRAINT destination_requests_processing_status_check,
    ADD CONSTRAINT destination_requests_processing_status_check CHECK (
        processing_status IN ('validated', 'fallback', 'passthrough')
    );

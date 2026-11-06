ALTER TABLE ticketing.ticket_drafts
    ADD COLUMN cap_unlimited boolean NOT NULL DEFAULT false;

ALTER TABLE ticketing.ticket_drafts
    ADD CONSTRAINT ticket_drafts_cap_mode_ck
    CHECK (NOT cap_unlimited OR cap IS NULL);

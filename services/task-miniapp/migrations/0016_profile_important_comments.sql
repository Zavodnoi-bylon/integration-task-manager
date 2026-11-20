ALTER TABLE ticketing.tickets
    DROP CONSTRAINT tickets_important_comment_ck,
    ADD CONSTRAINT tickets_important_comment_ck CHECK (
        important_comment IS NULL
        OR (
            ticket_type_code = 'destination'
            AND length(btrim(important_comment)) BETWEEN 1 AND 2000
        )
    );

ALTER TABLE ticketing.ticket_drafts
    DROP CONSTRAINT ticket_drafts_important_comment_ck,
    ADD CONSTRAINT ticket_drafts_important_comment_ck CHECK (
        important_comment IS NULL
        OR (
            ticket_type = 'destination'
            AND length(btrim(important_comment)) BETWEEN 1 AND 2000
        )
    );

ALTER INDEX ticketing.tickets_mephisto_comment_history_idx
    RENAME TO tickets_comment_history_idx;

ALTER TABLE ticketing.tickets
    ADD COLUMN important_comment text,
    ADD CONSTRAINT tickets_important_comment_ck CHECK (
        important_comment IS NULL
        OR (
            profile_key = 'mephisto'
            AND ticket_type_code = 'destination'
            AND length(btrim(important_comment)) BETWEEN 1 AND 2000
        )
    );

ALTER TABLE ticketing.ticket_drafts
    ADD COLUMN important_comment text,
    ADD CONSTRAINT ticket_drafts_important_comment_ck CHECK (
        important_comment IS NULL
        OR (
            profile_key = 'mephisto'
            AND ticket_type = 'destination'
            AND length(btrim(important_comment)) BETWEEN 1 AND 2000
        )
    );

CREATE INDEX tickets_mephisto_comment_history_idx
    ON ticketing.tickets (
        profile_key, requester_user_id, created_at DESC, ticket_id DESC
    )
    WHERE important_comment IS NOT NULL;

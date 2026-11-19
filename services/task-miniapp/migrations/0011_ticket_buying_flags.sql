-- Business-only current-state marker. Row existence means "issued to buying".
-- Deliberately stores no actor, timestamp or history and emits no ticket event.
CREATE TABLE ticketing.ticket_buying_flags (
    profile_key text NOT NULL CHECK (profile_key = 'kalisto'),
    ticket_id bigint NOT NULL,
    PRIMARY KEY (profile_key, ticket_id),
    FOREIGN KEY (ticket_id, profile_key)
        REFERENCES ticketing.tickets(ticket_id, profile_key)
        ON DELETE CASCADE
);

REVOKE ALL ON ticketing.ticket_buying_flags FROM PUBLIC;
GRANT SELECT, INSERT, DELETE ON ticketing.ticket_buying_flags TO ticketing_writer;

-- Initial schema for a NEW database only. Existing volumes require explicit migrations.
\set ON_ERROR_STOP on
BEGIN;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA ops;
CREATE SCHEMA raw;
CREATE SCHEMA work;

CREATE TABLE ops.dataset (
    id integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    title text NOT NULL DEFAULT 'Отчёт об активности отдела',
    coverage_status text NOT NULL DEFAULT 'partial'
        CHECK (coverage_status IN ('partial','complete','unknown')),
    coverage_note text NOT NULL DEFAULT 'Учтены только загруженные и подтверждённые сведения.'
);
INSERT INTO ops.dataset (id) VALUES (1);

CREATE TABLE ops.import_runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind text NOT NULL,
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    source_name text NOT NULL,
    parser_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('running','succeeded','failed')),
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    counts jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_code text,
    UNIQUE (kind, content_hash)
);

CREATE TABLE raw.sources (
    source_id text PRIMARY KEY,
    kind text NOT NULL,
    external_id text NOT NULL,
    display_name text NOT NULL,
    coverage_status text NOT NULL DEFAULT 'partial'
        CHECK (coverage_status IN ('partial','complete','unknown'))
);
CREATE TABLE raw.messages (
    message_key text PRIMARY KEY,
    source_id text NOT NULL REFERENCES raw.sources(source_id),
    external_id text NOT NULL,
    current_revision_hash text,
    has_conflict boolean NOT NULL DEFAULT false,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, external_id)
);
CREATE TABLE raw.message_revisions (
    message_key text NOT NULL REFERENCES raw.messages(message_key),
    revision_hash text NOT NULL CHECK (revision_hash ~ '^[0-9a-f]{64}$'),
    sent_at timestamptz NOT NULL,
    edited_at timestamptz,
    source_updated_at timestamptz NOT NULL,
    text_plain text NOT NULL DEFAULT '',
    sender_id text,
    sender_name text,
    reply_to_external_id text,
    source_url text,
    media_missing boolean NOT NULL DEFAULT false,
    raw_json jsonb NOT NULL,
    first_import_id bigint NOT NULL REFERENCES ops.import_runs(id),
    PRIMARY KEY (message_key, revision_hash),
    CHECK (edited_at IS NULL OR edited_at >= sent_at),
    CHECK (source_updated_at >= sent_at)
);
ALTER TABLE raw.messages ADD CONSTRAINT messages_current_revision_fk
    FOREIGN KEY (message_key, current_revision_hash)
    REFERENCES raw.message_revisions(message_key, revision_hash)
    DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX message_revisions_sent_idx ON raw.message_revisions(sent_at);

CREATE TABLE work.proposals (
    proposal_id text PRIMARY KEY,
    task_id text NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    document jsonb NOT NULL,
    state text NOT NULL DEFAULT 'proposed' CHECK (state IN ('proposed','accepted','rejected')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (task_id, version)
);
CREATE TABLE work.tasks (
    task_id text PRIMARY KEY,
    title text NOT NULL CHECK (length(btrim(title)) > 0),
    category text NOT NULL CHECK (category IN ('integration','incident','support','change','other')),
    client text,
    assignee text,
    external_refs jsonb NOT NULL DEFAULT '{}'::jsonb,
    revision integer NOT NULL CHECK (revision > 0),
    accepted_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE work.events (
    event_id text PRIMARY KEY,
    task_id text NOT NULL REFERENCES work.tasks(task_id),
    kind text NOT NULL CHECK (kind IN (
        'requested','observed_open','assigned','waiting','resumed','work_ready',
        'completed','reopened','cancelled')),
    occurred_at timestamptz NOT NULL,
    time_basis text NOT NULL CHECK (time_basis IN ('event','message','notification','observed')),
    UNIQUE (event_id, task_id)
);
CREATE INDEX events_task_time_idx ON work.events(task_id, occurred_at, event_id);
CREATE INDEX events_kind_time_idx ON work.events(kind, occurred_at);
CREATE TABLE work.evidence (
    evidence_id text PRIMARY KEY,
    task_id text NOT NULL REFERENCES work.tasks(task_id),
    event_id text,
    message_key text NOT NULL,
    revision_hash text NOT NULL,
    FOREIGN KEY (event_id, task_id) REFERENCES work.events(event_id, task_id),
    FOREIGN KEY (message_key, revision_hash)
        REFERENCES raw.message_revisions(message_key, revision_hash)
);
CREATE INDEX evidence_task_idx ON work.evidence(task_id);

CREATE TABLE ops.review_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    proposal_id text NOT NULL REFERENCES work.proposals(proposal_id),
    task_id text NOT NULL,
    actor text NOT NULL CHECK (length(btrim(actor)) > 0),
    decision text NOT NULL CHECK (decision IN ('accept','reject')),
    reason text,
    reviewed_at timestamptz NOT NULL DEFAULT now(),
    before_snapshot jsonb,
    after_snapshot jsonb
);

GRANT USAGE ON SCHEMA ops, raw, work TO activity_reader, activity_writer;
GRANT SELECT ON ops.dataset, ops.import_runs,
    raw.sources, raw.messages, raw.message_revisions,
    work.tasks, work.events, work.evidence, work.proposals TO activity_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA ops, raw, work TO activity_writer;
GRANT INSERT, UPDATE ON raw.sources, raw.messages, work.tasks TO activity_writer;
GRANT INSERT ON raw.message_revisions, work.proposals TO activity_writer;
GRANT UPDATE (state) ON work.proposals TO activity_writer;
GRANT INSERT, DELETE ON work.events, work.evidence TO activity_writer;
GRANT INSERT, UPDATE ON ops.import_runs TO activity_writer;
GRANT INSERT ON ops.review_log TO activity_writer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ops TO activity_writer;
-- No CREATE/TRUNCATE/role administration grants. Future tables get no automatic grants.
COMMIT;


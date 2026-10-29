-- Additive migration; also runs after 10-schema.sql for new databases.
\set ON_ERROR_STOP on
BEGIN;
SELECT pg_advisory_xact_lock(814211970261022);
CREATE TABLE IF NOT EXISTS ops.tenants (
    tenant_id text PRIMARY KEY,
    display_name text NOT NULL,
    account_id text NOT NULL UNIQUE,
    account_alias text NOT NULL,
    UNIQUE (tenant_id, account_id)
);
INSERT INTO ops.tenants VALUES
    ('traffic-angels','Traffic Angels','account-1','Kalisto'),
    ('theraw','TheRaw','account-2','mephisto')
ON CONFLICT (tenant_id) DO NOTHING;

ALTER TABLE raw.sources ADD COLUMN IF NOT EXISTS tenant_id text;
ALTER TABLE raw.sources ADD COLUMN IF NOT EXISTS account_id text;
ALTER TABLE work.tasks ADD COLUMN IF NOT EXISTS tenant_id text
    GENERATED ALWAYS AS (external_refs->>'tenant_id') STORED;
ALTER TABLE work.proposals ADD COLUMN IF NOT EXISTS tenant_id text
    GENERATED ALWAYS AS (document->'external_refs'->>'tenant_id') STORED;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='source_tenant_account_fk' AND conrelid='raw.sources'::regclass) THEN
        ALTER TABLE raw.sources ADD CONSTRAINT source_tenant_account_fk
            FOREIGN KEY (tenant_id,account_id) REFERENCES ops.tenants(tenant_id,account_id);
        ALTER TABLE raw.sources ADD CONSTRAINT source_context_pair
            CHECK ((tenant_id IS NULL) = (account_id IS NULL));
        ALTER TABLE work.tasks ADD CONSTRAINT task_tenant_fk FOREIGN KEY (tenant_id) REFERENCES ops.tenants;
        ALTER TABLE work.proposals ADD CONSTRAINT proposal_tenant_fk FOREIGN KEY (tenant_id) REFERENCES ops.tenants;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION work.check_proposal_tenant() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE tenant text := NEW.document->'external_refs'->>'tenant_id'; old_tenant text;
BEGIN
    -- Both top-level and per-event evidence count; tagged/untagged evidence cannot mix.
    IF EXISTS (
        WITH refs AS (
            SELECT value FROM jsonb_array_elements(NEW.document->'evidence')
            UNION ALL
            SELECT r.value FROM jsonb_array_elements(NEW.document->'events') e,
                 LATERAL jsonb_array_elements(e->'evidence') r
        )
        SELECT 1 FROM refs JOIN raw.messages m ON m.message_key=refs.value->>'message_key'
        JOIN raw.sources s ON s.source_id=m.source_id
        WHERE s.tenant_id IS DISTINCT FROM tenant
    ) THEN RAISE EXCEPTION 'proposal_tenant_evidence_conflict'; END IF;
    SELECT t.tenant_id INTO old_tenant FROM work.tasks t WHERE t.task_id=NEW.task_id;
    IF old_tenant IS NOT NULL AND old_tenant IS DISTINCT FROM tenant THEN
        RAISE EXCEPTION 'proposal_existing_task_tenant_conflict';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS proposal_tenant_guard ON work.proposals;
CREATE TRIGGER proposal_tenant_guard BEFORE INSERT ON work.proposals
    FOR EACH ROW EXECUTE FUNCTION work.check_proposal_tenant();

CREATE OR REPLACE FUNCTION work.check_evidence_tenant() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM raw.messages m JOIN raw.sources s USING(source_id), work.tasks t
        WHERE m.message_key=NEW.message_key AND t.task_id=NEW.task_id
          AND s.tenant_id IS DISTINCT FROM t.tenant_id)
    THEN RAISE EXCEPTION 'task_tenant_evidence_conflict'; END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS evidence_tenant_guard ON work.evidence;
CREATE TRIGGER evidence_tenant_guard BEFORE INSERT OR UPDATE ON work.evidence
    FOR EACH ROW EXECUTE FUNCTION work.check_evidence_tenant();

CREATE OR REPLACE FUNCTION work.keep_task_tenant() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.external_refs->>'tenant_id' IS NOT NULL AND
       (OLD.external_refs->>'tenant_id') IS DISTINCT FROM (NEW.external_refs->>'tenant_id')
    THEN RAISE EXCEPTION 'task_tenant_change_forbidden'; END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS task_tenant_guard ON work.tasks;
CREATE TRIGGER task_tenant_guard BEFORE UPDATE ON work.tasks
    FOR EACH ROW EXECUTE FUNCTION work.keep_task_tenant();

CREATE OR REPLACE FUNCTION raw.keep_source_context() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.tenant_id IS NOT NULL AND
       (OLD.tenant_id IS DISTINCT FROM NEW.tenant_id OR OLD.account_id IS DISTINCT FROM NEW.account_id)
    THEN RAISE EXCEPTION 'source_context_change_forbidden'; END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS source_context_guard ON raw.sources;
CREATE TRIGGER source_context_guard BEFORE UPDATE ON raw.sources
    FOR EACH ROW EXECUTE FUNCTION raw.keep_source_context();

GRANT SELECT ON ops.tenants TO activity_reader,activity_writer;
GRANT UPDATE (tenant_id,account_id) ON raw.sources TO activity_writer;
COMMIT;

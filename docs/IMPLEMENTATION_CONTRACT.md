# Team Activity Explorer — shared implementation contract

This document specifies the dashboard's data/API contract. Its original
local-only implementation phase is complete: the dashboard is deployed on the
old VPS and a separate collector exists. Current scope, operational facts and
migration authority are in WORKFLOW.md, STATUS.md and MIGRATION.md.
Scope: an operational dashboard for a four-person department, using Telegram
messages and Asana evidence, not AIO visits/conversions. No unattended external
AI calls or fabricated business data; production credentials stay outside Git.

## Dataset shape used by domain/API and demo

```json
{
  "coverage": {"status":"partial","note":"Only imported evidence is known"},
  "demo": false,
  "tasks": [{
    "task_id":"stable-id", "title":"...", "category":"support",
    "client":null, "assignee":null, "external_refs":{}, "revision":1,
    "events":[{
      "event_id":"stable-event-id", "kind":"requested",
      "occurred_at":"2026-10-20T11:00:00+03:00", "time_basis":"message"
    }],
    "evidence":[{
      "message_key":"telegram-desktop:chat-id:message-id",
      "revision_hash":"sha256", "event_id":null
    }]
  }],
  "proposals": [], "messages": [], "imports": []
}
```

`category`: integration / incident / support / change / other.
`assignee`: explicit string or null; no inference from sender/shift.
Task IDs and external IDs are strings. Never coerce destination IDs to numbers.
Only accepted tasks enter dashboard totals. Proposals remain a separate queue.

Event kinds: requested, observed_open, assigned, waiting, resumed, work_ready,
completed, reopened, cancelled. Every event has a timezone-aware timestamp.
`time_basis`: event / message / notification / observed.
Assignment and observation DO NOT count as a new request. `work_ready` is not
confirmed completion. Requested/observed_open/assigned/reopened/resumed imply an
observed open task unless invalid under the already-known lifecycle. Waiting and
work_ready are open states. Completed/cancelled close a case. Reopening preserves
identity and history. Duplicate completed events while already closed are not a
new closure. A completed event without a known opening is an observed closure,
but has no measurable request-to-done duration. Equal-time event order must be
deterministic and documented. Negative durations are rejected, never clamped.

## Reporting semantics

- Timezone Europe/Moscow; dates in URLs are inclusive, convert to half-open UTC
  boundaries. Week starts Monday; edge weeks are partial and explicitly flagged.
- Incoming = distinct tasks with their first valid `requested` in the bucket.
- Completed = distinct tasks with a valid completion in the bucket. Additionally
  expose completion event count so reopened cases do not silently look like new
  tasks. Period distinct completed count is NOT the sum of bucket distinct counts.
- Reopened/cancelled are separate flow fields.
- Observed backlog = number of known open tasks at bucket end; include history
  before selected start. Never sum daily snapshots to obtain a weekly backlog.
- Complete department backlog is unknown with partial input. Label the line
  "Известно открытых" and expose coverage warning; do not claim staffing utilization.
- Resolution median = request-to-completion elapsed hours for completed cases
  with a known first request. State how repeat completions contribute (latest
  completion per task in the selected bucket). Missing start => null duration.
- Unknown numeric values render as a dash, not zero. Zero observed counts are
  legitimate but coverage remains visible. Empty dataset = no data, not measured
  zero load. No synthetic tasks in normal mode.
- Filters: category, client, assignee (`__unknown__` for null), q search. Task
  attributes are current classifications, not reconstructed historical assignments.
- One in-memory snapshot of a repository dataset powers each response; Postgres
  repository uses a read-only REPEATABLE READ transaction. No silent truncation;
  explicit error when configured safety bound is exceeded.

## API contract

All routes GET. API has no write credentials and exposes no write route.

- `/api/health` -> `{ok:1, mode:"postgres"|"demo"}`; checks database in normal mode.
- `/api/meta` -> `{timezone,today,min_date,max_date,default_from,default_to,
  categories:[{value,label}],clients:[string],assignees:[string],coverage,demo,
  last_import_at, pending_proposals}`. `today` is Moscow date.
- `/api/report?from=YYYY-MM-DD&to=YYYY-MM-DD&bucket=day|week&...filters` ->
  `{timezone,from,to,bucket,coverage,demo,empty,summary,series,breakdown}`.
  summary: incoming, completed, completion_events, reopened, cancelled,
  observed_backlog, resolution_median_hours, resolution_sample_size,
  missing_request_count, pending_proposals.
  series[]: period_start, period_end, partial, incoming, completed,
  completion_events, reopened, cancelled, observed_backlog,
  resolution_median_hours, resolution_sample_size.
  breakdown[]: category, label, tasks, completed, observed_backlog.
- `/api/tasks?from=...&to=...&status=all|open|completed|waiting&metric=all|incoming|completed|backlog&page=1&page_size=50&...filters`
  -> `{rows,total,page,page_size,has_more,coverage,demo}`.
  Each row: task_id,title,category,client,assignee,status,requested_at,
  completed_at,resolution_hours,evidence_count.
  `metric` supports chart drilldown using bucket date range. Backlog at end
  includes tasks predating range. Current state for this endpoint is state at `to`.
- `/api/tasks/{task_id:path}` -> `{task,events,evidence,coverage,demo}`.
  Evidence rows: message_key,revision_hash,text_plain,sent_at,edited_at,
  sender_name,source_name,source_url,has_conflict,media_missing.
- `/api/proposals` -> `{rows,total,demo}` with flat pending candidate documents
  and review metadata (`proposal_id`, `task_id`, `version`, `state`, `created_at`).
  Each row preserves its original evidence references and adds `evidence_details`
  with the same safe message fields as task detail above. Details combine exact
  `(message_key, revision_hash)` references from top-level `evidence` and every
  event's `evidence`, deduplicate pairs, and never substitute a newer revision.
  Pending evidence is readable before acceptance; candidates remain excluded
  from all accepted-task metrics. Combined accepted/pending reference and byte
  limits are enforced, including the expanded review response.
- `/api/export/tasks?...` -> safe CSV of complete filtered registry with explicit
  row/byte limit rejection. Neutralize formula-leading text cells. No partial CSV.

`from/to` required for report/list/export. Max range 366 days. Read-only errors
must not leak DB DSNs or raw database errors. Unknown /api routes return JSON 404,
never the SPA HTML. Static serving confined to dist.

## PostgreSQL tables (root creates exact DDL)

- `ops.dataset`: id integer PK=1, title, coverage_status, coverage_note.
- `ops.import_runs`: id bigint identity PK, kind, content_hash, source_name,
  parser_version, status, started_at, completed_at, counts jsonb, error_code;
  unique(kind,content_hash). Reprocess failed runs; skip succeeded same hash.
- `raw.sources`: source_id text PK, kind, external_id, display_name,
  coverage_status text default partial.
- `raw.messages`: message_key text PK, source_id FK, external_id,
  current_revision_hash text, has_conflict boolean, first_seen_at,last_seen_at;
  unique(source_id,external_id).
- `raw.message_revisions`: message_key FK, revision_hash, sent_at, edited_at,
  source_updated_at, text_plain, sender_id, sender_name, reply_to_external_id,
  source_url, media_missing boolean, raw_json jsonb, first_import_id FK;
  composite PK(message_key,revision_hash).
- `work.proposals`: proposal_id text PK, task_id text, version integer,
  content_hash text, document jsonb, state text default proposed, created_at;
  unique(task_id,version).
- `work.tasks`: task_id text PK,title,category,client,assignee,external_refs jsonb,
  revision integer, accepted_at,updated_at.
- `work.events`: event_id text PK,task_id FK,kind,occurred_at,time_basis.
- `work.evidence`: evidence_id text PK,task_id FK,event_id nullable FK,
  message_key,revision_hash; composite FK to message_revisions.
- `ops.review_log`: id bigint identity PK,proposal_id FK,task_id,actor,decision,
  reason,reviewed_at,before_snapshot jsonb,after_snapshot jsonb.

SQL role `activity_reader` gets SELECT only on required tables; `activity_writer`
gets scoped INSERT/UPDATE/DELETE for ingestion/explicit review. Admin exclusively
for initialization/migrations. Web environment includes only reader credential.
No existing AIO schemas, connections, networks, token files, state or credentials.

## Import/review CLI

`python sync.py normalize INPUT.json --output BUNDLE.json`: network/DB-free
normalization; accepts one-chat and Telegram Desktop chats.list multi-chat exports.
Source namespace `telegram-desktop`; retain raw IDs, raw JSON/entities and replies.
Prefer Unix timestamps; fallback date needs explicit timezone argument. Preserve
edits and media-missing flags. Hash revisions canonically. No task inference from
raw message counts or generic reply roots.

`python sync.py ingest-messages INPUT.json [--dry-run]`: normalize + PG writer;
whole-file and revision idempotence, compare source_updated_at, preserve older
revisions without replacing newer current, equal-time unequal-content conflict.
No deletion inferred from partial export. Use transaction advisory lock.

`python sync.py ingest-proposals TASKS.json [--dry-run]`: strict contract validation
and every evidence FK checked, proposals stay proposed. Never overwrite accepted
task state automatically. All explicit completed/requested events require evidence.

`python sync.py review PROPOSAL_ID --decision accept|reject --actor NAME
[--reason TEXT] [--expected-revision N]`: explicit human decision. Accept replaces
canonical events/evidence transactionally and records before/after audit; reject
does not delete a previously accepted task. Guard conflicting task revision.
Cannot execute arbitrary SQL or arbitrary filesystem paths from imported data.

Proposal file: `{schema_version:1, proposals:[...]}`. Each proposal contains the
task fields above, version, events (each with evidence array), evidence array,
optional context_missing boolean and note. No self-asserted review status honored.
Examples are clearly fictional test fixtures, never imported in normal startup.

## Local demo/testing

`ACTIVITY_DEMO=1`: read-only fake in-memory dataset ONLY with conspicuous
"Демонстрационные данные — не отчёт отдела" label. Default is postgres mode;
missing DB credentials produces a clear non-secret config failure, not a demo
fallback. Test data live under examples/tests and carry explicit demo=true.
No real chat content or credentials bundled in the new project.

Frontend uses the actual API in both modes; there is no URL-triggered fixture
adapter or silent fallback on API error. Abort stale requests; failed filter change
clears report/actions. Null remains null. Persist only non-secret display settings.

## Verification priorities

Moscow day boundaries; partial/Monday weeks; tasks before range; unknown request;
reopening; cancelled tasks; no duplicated close; weekly snapshots vs sums;
stable IDs across exports; newer then older edit; same-time conflict; manual
review survives new proposal; explicit null correction; source immutability;
read-only SQL grants; escaping, pagination and safe CSV.

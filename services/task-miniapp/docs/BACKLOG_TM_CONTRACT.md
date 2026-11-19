# Backlog TM-01–TM-07: DB/API contract

Status: accepted implementation contract; no production migration has been performed.

## Compatibility and migration ownership

Keep canonical ticketing/reporting schemas, global ticket IDs, original messages,
manual audit and all existing migration checksums. Add new numbered migrations;
never rewrite 0001–0005. Mini App migration runner applies shared workflow DDL
only after canonical 0001. New code requires these migrations before traffic.
Historical ticket descriptions and submitted drafts are not parsed or rewritten.
No supervisor assignments or advertiser policies are inferred from historical data.

## Kalisto Destination input

New Kalisto Destination creation and draft submission require a catalog advertiser
and a structured destination request: naming, geo, rate, currency, cap, landing_url;
clickid_subid and additional are optional text. Naming is the final user value and
does not have to start with the selected catalog advertiser; these are separate fields.
Cap is a positive integer plus an explicit `daily`, `monthly` or `lifetime` mode,
or the separate `unlim` mode without a number. Historical rows keep an unknown
mode as NULL; the migration must not infer a period that was never recorded.
Rate uses decimal representation, never binary floating point. GEO and currency
are explicit user values; the formatter cannot invent or overwrite them.
Draft save permits incomplete fields; submit validates the complete request.
General rejects Destination-specific input. Mephisto retains advertiser/cap/body.

Persist structured request fields in PostgreSQL for drafts and created tickets,
including model fallback. Store canonical public source text in the ticket's first
public message using a deterministic readable template. Never reconstruct user
input by parsing the rendered description. Existing drafts retain source evidence
and receive no inferred type or structured fields; their author completes them.
Existing submitted drafts and historical tickets keep their original data.

The API determines profile from its process, never from request fields. Extend
create/save/submit DTOs and idempotency hashes to include all structured fields.
Incomplete save and complete submit share the same field semantics. Returning a
draft to editing never erases its preserved source messages.

## Formatter and results

Kalisto model input contains only URL-related user fields, server advertiser
parameters and the explicit advertiser identifier policy. Naming, GEO, rate and
CAP remain server-owned request data and are not delegated to the model. The model
returns a strict structured object containing only URL and warnings; the server
then attaches the exact user-entered Naming after URL validation. Extra properties,
missing fields, invalid HTTP(S) URLs, credentials, whitespace/control characters,
unresolved single-brace placeholders, malformed or unexpectedly changed AIO macros
are rejected. Warnings are internal diagnostic
content; do not expose model/API internals through public DTOs or FAQ. A warning
about immutable business metadata does not override successful deterministic URL
validation and does not force fallback; preserved warnings remain internal.

The returned Destination URL never contains the advertiser `url_parameters` tail.
AIO appends that configured advertiser tail at runtime. Complete `name=value`
entries owned by the tail are removed from Destination while every unrelated source
parameter is preserved. A leading continuation fragment remains excluded from the
returned URL, but Destination must end with the source value that fragment extends.
An exact single-brace query value such as `{ваш_клик}` or `{веб}` is a dynamic
placeholder, not a literal static value. When the selected advertiser already has
dynamic AIO macros, its whole source parameter is removed even if the parameter
name differs. No successful Destination may retain an unresolved `{...}` value.
The continuation-tail exception keeps its required target parameter and validates
the placeholder-to-AIO conversion instead of removing that target.

Python inspects and validates URLs; it never assembles or repairs query strings.
On formatting/policy/validation failure, preserve the original request, commit one
ticket through the existing fallback path and create an internal diagnostic note.
Do not expose an unverified result URL as a successful Destination. Store ordered
validated result pairs independently from rendered presentation; no invented
results are backfilled for historical tickets.

Mephisto retains its multi-result source processing. Validate every returned URL
and preserve result order. A new parse failure follows the same fallback path;
no partially validated multi-result set is published.

## External identifier policy (TM-06)

Each advertiser configuration may explicitly declare rules for external identifier
parameters. A rule identifies its input query parameter and action: replace with
Visit UUID or remove the whole parameter. Replacement retains the input parameter
name unless that advertiser explicitly specifies the output parameter name.
The advertiser configuration is authoritative; model instructions cannot override
it. A Visit UUID rule uses exactly {{aio.visit.uuid}}. Other supported Mephisto
macros include {{aio.visit.fields.buyer_utm}}, {{aio.visit.fields.fb_ad_name}} and
{{aio.visit.fields.fb_ad_set_name}}.

Absence or ambiguity of the applicable policy triggers fallback, not a guessed
repair. Do not populate advertiser rules automatically from unrelated suffixes.
Send explicit policy to the model and independently validate every rule in Python.
The previous prompt instruction to preserve sub_id_10={external_id} is superseded.

## Forward batches

Persist one batch identity, profile, author, chat, received_at, immutable due_at,
state, and resulting draft ID. Persist each original message and forward metadata
with a unique (profile, chat, telegram_message_id) delivery identity. Preserve
message order by Telegram message ID, independently of update delivery order.
Use server receipt time rather than Telegram original-forward time for the window.
The configurable window defaults to five seconds. Snapshot due_at when creating
the batch; subsequent messages and configuration changes do not extend it.

Serialize intake for the same profile/author/chat. A duplicate delivery resolves
to its original batch, including after closure. Messages received at or after
its due_at belong to a new batch even if a worker has not yet closed the old one.
The worker locks due batches and creates one draft plus source associations and
closes the batch in the same transaction. Concurrent workers and retries cannot
create another draft. Handler performs no sleep and never calls the model.
Worker failure leaves a retryable batch. One message batch is also supported.

## Draft type and navigation

New forwarded drafts have ticket_type=NULL. Save accepts NULL/general/destination;
submit rejects NULL. General only needs the task body. Destination uses the form
for the current profile. Switching type preserves original source evidence and
never implicitly submits. All multi-step UI flows provide Back navigation.
Drafts remain private to their author until explicit submission and stay out of
staff queues and reporting. Submission keeps its stable idempotency identity.

## Presentation

Summary uses a short naming rather than the full description. Destination detail
renders each ordered result once as naming, then 'Destination:', then its URL.
Fallback displays the request status without fabricating a successful URL.
General description remains body content, not a duplicated large heading.
All long text and URLs wrap within mobile grid/flex ancestors.

## Supervisor

Add supervisor to current profile membership roles and recorded actor/author role
constraints. Supervisor is never staff. A profile-scoped relation links supervisor
membership to subordinate membership, with foreign keys for both memberships,
no self-link, explicit active state and timestamps. Team membership lives in
PostgreSQL, never env. Migration creates no real assignments.

Visibility is own tickets plus tickets authored by directly assigned active
subordinates in the same profile; no recursive/transitive team access. Public
messages only. List, detail and message mutation queries enforce the same predicate
using current active memberships/relations. Changes revoke access immediately.
Cross-profile, absent and unauthorized objects use the existing indistinguishable
404 boundary. Supervisor cannot see internal notes even on own tickets.

Supervisor can create tickets and write public messages where existing message
state rules permit. Supervisor cannot execute technical status or assignee
transitions. On their own tickets supervisors retain the existing author-only
accept, return and reopen actions. On subordinate tickets they can only read and
write public messages. No technician capability is inherited. UI capabilities
must match server enforcement. Record supervisor as
the real actor in immutable audit. No team-management UI or env-based role sync
is introduced by this backlog.

## Verification

Target structured validation and unchanged naming; fallback preservation;
per-advertiser replace/remove/missing/ambiguous rules; malformed and preserved
macros; profile separation; duplicate deliveries, fixed deadline, late messages,
concurrent close and worker retry; draft type and Back; ordered presentation and
mobile overflow; supervisor query/mutation/notes denial and immediate revocation.
Use synthetic data and isolated PostgreSQL only. Run the full release suite once
before release handoff. Before production, restore a real backup to an isolated
PostgreSQL database and apply/verify migrations there. Production access, push,
deploy, secrets and real team assignments require separate user authorization.

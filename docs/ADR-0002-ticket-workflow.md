# ADR-0002: ticket workflow and atomic mutations

Status: accepted for implementation stage A.

## Decision

Mini App HTTP handlers do not update tickets or call Telegram directly. They
validate session, CSRF, UUID idempotency key and input DTO, then invoke the
`TicketWorkflow` application service. Pure domain planners decide authorization,
state transition, append-only event, optional public message and notification
intents.

The canonical PostgreSQL adapter implements `TicketMutationUnitOfWork`. Each
method must lock the profile-scoped ticket and commit the following as one
transaction:

- ticket state and review-cycle;
- append-only event and optional public message;
- idempotency key, request hash and stored response;
- notification/outbox intents.

Idempotency namespace is exactly
`miniapp:{profile}:{actor_telegram_user_id}:{operation}:{uuid}`. Same scoped
key/payload returns the stored result. Same scoped key with another payload is a
conflict. The same UUID in another profile or operation is independent. Telegram
delivery happens later in an outbox worker and does not change a committed HTTP
result.

Notification intent with `target_telegram_user_id=None` means the eligible staff
audience of the same server-side profile; it never means a cross-profile or
global broadcast.

For ticket mutations the UOW first loads and locks only a ticket visible to the
actor in the server-side profile. For clients, absent, another author's ticket
and another profile are the same ResourceNotFound/404 before a domain planner is
called. Client probes of staff-only operations are rejected before ticket lookup.

Destination preparation is application orchestration, not persistence. The
workflow resolves a catalog key server-side, invokes the existing formatter/
OpenRouter adapter before the transaction, and uses its defined fallback on a
model failure. Only a prepared aggregate reaches the UOW: formatted ticket,
original public source message, optional internal note, advertiser display name,
cap and notification/event drafts. Client input never supplies suffix or
technician note.

## State machines

General:

    open --staff submit--> review
    review --author accept--> completed
    review --author return + public reason--> open
    completed --author reopen + public reason--> open

Destination:

    open --staff complete--> completed
    completed --author reopen + public reason--> open

`staff` means technician or admin. Author checks use `telegram_user_id`. A staff
member who authored a ticket remains staff for projection/internal-history and
may use author-only transitions on that ticket.

## Visibility

Internal notes are role-based and are never projected to a client. Return and
reopen reasons are always public. Destination completion creates only a neutral
client notification; internal content is not copied into it.

## Review reminder

Every transition to review increments `review_cycle`. One reminder intent is due
at 12:00 Europe/Moscow on the next calendar day. Its dedup key contains ticket
and cycle. Delivery requires both current status=review and the same cycle, so a
return, accept or reopen makes an old reminder ineligible. A later review cycle
gets its own one-time reminder.

## Deferred to stage B

PostgreSQL schema/migration, concrete repository, persistent session store,
outbox worker, retry policy, reporting views and real restore/integration tests.
No production SQLite or JSON persistence is permitted.

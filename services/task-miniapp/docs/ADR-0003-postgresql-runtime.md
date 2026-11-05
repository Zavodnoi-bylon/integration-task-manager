# ADR-0003: PostgreSQL runtime boundary

Status: proposed for canonical review

## Context

The Mini App must use the existing Integration Task Manager database rather than
introduce a second task store. Kalisto and Mephisto are separate Telegram bot
instances and profiles, while reporting may aggregate accepted data across both
profiles. The analytics bot remains a separate read-only product.

## Decision

- The canonical `ticketing` PostgreSQL schema is the only task write model.
- Migration `0002_miniapp_workflow.sql` extends canonical migration 0001; it does
  not recreate or replace the canonical schema.
- Every ticket mutation writes the ticket, messages, append-only events,
  notification outbox rows and the completed idempotency response in one
  transaction.
- Destination preparation obtains a durable idempotency reservation before any
  catalog or formatter call. The final create transaction locks and completes
  that same reservation. A late failure report cannot overwrite a completed
  reservation.
- Sessions are opaque, hashed and profile-bound. Membership and role are read
  again from PostgreSQL for every authenticated request/mutation.
- The outbox worker leases rows with `FOR UPDATE SKIP LOCKED`, retries transient
  delivery failures and re-checks status/review-cycle preconditions before
  scheduled delivery.
- Reporting views derive review, return and reopen metrics from canonical tickets
  and append-only events. Session and idempotency tables are not exposed to the
  reporting reader.

## Consequences

- There is no JSON or SQLite task database and no cross-profile client access.
- A Telegram delivery can still be repeated after a process crash between the
  external send and the database acknowledgement. Notification copy must remain
  safe to repeat; database enqueueing itself is deduplicated.
- Production wiring, Telegram delivery and deployment stay in the canonical
  project after review. This repository provides the transport/runtime adapter
  and integration evidence only.

## Verification

- Run all contract tests with `python -m unittest discover -s tests -v`.
- Run `tests/integration` against a disposable PostgreSQL 17 database containing
  canonical migration 0001 plus this migration.
- Run `scripts/verify_postgres.py` to apply 0002 twice after restoring 0001.
- Do not use a production database for these checks.

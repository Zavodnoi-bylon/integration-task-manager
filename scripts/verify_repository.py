"""Exercise the typed repository contract against a disposable PostgreSQL."""
from __future__ import annotations

import config

from app.domain import EVENT_PUBLIC_MESSAGE, EVENT_STATUS_CHANGED, STATUS_OPEN
from app.repositories import build_repository
from app.services.ticket_service import TicketService
from app.settings import Settings


def main() -> None:
    if not config.TICKETING_DB_NAME.startswith("integration_task_manager_verify_"):
        raise SystemExit(
            "Refusing non-disposable database; expected name prefix "
            "integration_task_manager_verify_"
        )

    settings = Settings(
        bot_token="123456:VERIFY_TOKEN",
        admin_users=frozenset({990000000001}),
        admin_message=990000000001,
        technician_users=frozenset(),
        bot_key="kalisto",
    )
    repository = build_repository("postgres")
    repository.initialize()
    service = TicketService(settings, repository)
    service.register_user(990000000001, "Verify Admin", "2026-11-01 12:00:00")
    service.register_user(990000000002, "Verify Client", "2026-11-01 12:00:00")

    ticket = service.create_ticket(
        990000000002,
        "Repository verification",
        "2026-11-01 12:01:00",
        request_key="repository-verification:ticket",
    )
    service.add_public_message(
        ticket.ticket_id,
        990000000002,
        "Client message",
        "2026-11-01 12:02:00",
        idempotency_key="repository-verification:public",
    )
    service.add_comment(
        ticket.ticket_id,
        990000000001,
        "Internal note",
        "2026-11-01 12:03:00",
        idempotency_key="repository-verification:internal",
    )
    service.complete_ticket(
        ticket.ticket_id,
        990000000001,
        "2026-11-01 12:04:00",
        idempotency_key="repository-verification:completed",
    )
    reopened = service.reopen_ticket(
        ticket.ticket_id,
        990000000002,
        "2026-11-01 12:05:00",
        idempotency_key="repository-verification:reopened",
    )
    if reopened.status != STATUS_OPEN:
        raise AssertionError(f"Unexpected status: {reopened.status}")
    listed = service.list_user_tickets(990000000002, STATUS_OPEN)
    if listed.total != 1 or listed.items[0].ticket_id != ticket.ticket_id:
        raise AssertionError(f"Unexpected repository listing: {listed}")
    events = service.get_events(ticket.ticket_id)
    event_types = [event.event_type for event in events]
    if EVENT_PUBLIC_MESSAGE not in event_types:
        raise AssertionError(f"Public message missing: {event_types}")
    if event_types.count(EVENT_STATUS_CHANGED) != 2:
        raise AssertionError(f"Status history mismatch: {event_types}")
    if service.latest_comment(ticket.ticket_id) != "Internal note":
        raise AssertionError("Internal note mapping failed")

    print("POSTGRES_REPOSITORY_VERIFIED")


if __name__ == "__main__":
    main()

from __future__ import annotations

from typing import Protocol

from app.domain import TicketDraftRecord, TicketEventRecord, TicketRecord, UserRecord


class TicketRepository(Protocol):
    """Typed persistence boundary used by ticketing use cases."""

    def initialize(self) -> None: ...

    def timestamp(self, value=None) -> str: ...

    def sync_configured_roles(
        self,
        profile_key: str,
        admin_ids: frozenset[int],
        technician_ids: frozenset[int],
    ) -> None: ...

    def register_user(
        self,
        profile_key: str,
        telegram_user_id: int,
        display_name: str,
        role: str,
        registered_at: str,
    ) -> UserRecord: ...

    def get_user(self, profile_key: str, telegram_user_id: int) -> UserRecord | None: ...

    def get_ticket(self, profile_key: str, ticket_id: int) -> TicketRecord | None: ...

    def get_events(
        self,
        profile_key: str,
        ticket_id: int,
        limit: int = 20,
    ) -> tuple[TicketEventRecord, ...]: ...

    def create_ticket(
        self,
        profile_key: str,
        owner_id: int,
        assigned_to: int | None,
        body: str,
        created_at: str,
        request_key: str | None,
        ticket_type: str,
    ) -> TicketRecord: ...

    def count_tickets(
        self,
        profile_key: str,
        owner_id: int | None,
        status: str,
    ) -> int: ...

    def list_user_tickets(
        self,
        profile_key: str,
        owner_id: int,
        status: str,
        limit: int,
        offset: int,
    ) -> tuple[TicketRecord, ...]: ...

    def list_team_tickets(
        self,
        profile_key: str,
        status: str,
        limit: int,
        offset: int,
    ) -> tuple[TicketRecord, ...]: ...

    def add_internal_note(
        self,
        profile_key: str,
        ticket_id: int,
        actor_id: int,
        text: str,
        created_at: str,
        idempotency_key: str | None,
    ) -> None: ...

    def add_public_message(
        self,
        profile_key: str,
        ticket_id: int,
        actor_id: int,
        text: str,
        created_at: str,
        idempotency_key: str | None,
    ) -> None: ...

    def change_status(
        self,
        profile_key: str,
        ticket_id: int,
        new_status: str,
        actor_id: int,
        changed_at: str,
        idempotency_key: str | None,
    ) -> bool: ...

    def latest_internal_note(self, profile_key: str, ticket_id: int) -> str | None: ...

    def save_draft_intake(
        self,
        profile_key: str,
        author_id: int,
        chat_id: int,
        message_id: int,
        body: str,
        received_at: str,
    ) -> None: ...

    def create_forwarded_draft(
        self,
        profile_key: str,
        author_id: int,
        source_chat_id: int,
        source_message_id: int,
        source_body: str,
        forward_origin_type: str | None,
        forward_origin_name: str | None,
        forward_origin_date: str | None,
        created_at: str,
    ) -> TicketDraftRecord: ...

    def append_forwarded_message(
        self,
        profile_key: str,
        author_id: int,
        chat_id: int,
        message_id: int,
        body: str,
        received_at: str,
        window_seconds: int,
        forward_origin_type: str | None,
        forward_origin_name: str | None,
        forward_origin_date: str | None,
    ) -> str: ...

    def finalize_due_forward_batches(
        self,
        profile_key: str,
        now: str,
        limit: int = 20,
    ) -> tuple[TicketDraftRecord, ...]: ...

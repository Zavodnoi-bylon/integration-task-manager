from __future__ import annotations

from types import ModuleType

from app.domain import (
    STATUS_COMPLETED,
    STATUS_OPEN,
    TicketDraftRecord,
    TicketEventRecord,
    TicketRecord,
    UserRecord,
)


class ModuleTicketRepository:
    """Typed adapter around the existing SQLite/PostgreSQL persistence modules.

    Positional legacy rows are contained here and never cross into services.
    """

    def __init__(self, backend: ModuleType) -> None:
        self._backend = backend

    def initialize(self) -> None:
        self._backend.create_tables()

    def timestamp(self, value=None) -> str:
        return self._backend.moscow_timestamp(value)

    def sync_configured_roles(
        self,
        profile_key: str,
        admin_ids: frozenset[int],
        technician_ids: frozenset[int],
    ) -> None:
        self._backend.sync_user_roles(profile_key, admin_ids, technician_ids)

    def register_user(
        self,
        profile_key: str,
        telegram_user_id: int,
        display_name: str,
        role: str,
        registered_at: str,
    ) -> UserRecord:
        self._backend.ensure_user(
            telegram_user_id,
            registered_at,
            display_name,
            role,
            profile_key,
        )
        user = self.get_user(profile_key, telegram_user_id)
        if user is None:
            raise RuntimeError(f"Unable to load registered user {telegram_user_id}")
        return user

    def get_user(self, profile_key: str, telegram_user_id: int) -> UserRecord | None:
        row = self._backend.get_user_by_id(telegram_user_id, profile_key)
        return UserRecord.from_mapping(row) if row else None

    def get_ticket(self, profile_key: str, ticket_id: int) -> TicketRecord | None:
        row = self._backend.get_ticket_info(ticket_id, profile_key)
        return TicketRecord.from_mapping(row) if row else None

    def get_events(
        self,
        profile_key: str,
        ticket_id: int,
        limit: int = 20,
    ) -> tuple[TicketEventRecord, ...]:
        return tuple(
            TicketEventRecord.from_mapping(row)
            for row in self._backend.get_ticket_events(
                ticket_id,
                limit=limit,
                bot_key=profile_key,
            )
        )

    def create_ticket(
        self,
        profile_key: str,
        owner_id: int,
        assigned_to: int | None,
        body: str,
        created_at: str,
        request_key: str | None,
        ticket_type: str,
    ) -> TicketRecord:
        ticket_id = self._backend.add_ticket(
            owner_id,
            "",
            "",
            body,
            created_at,
            STATUS_OPEN,
            "",
            assigned_to=assigned_to,
            bot_key=profile_key,
            request_key=request_key,
            ticket_type=ticket_type,
        )
        # These fields only exist in the legacy SQLite representation. The
        # PostgreSQL adapter intentionally treats them as compatibility no-ops.
        self._backend.update_user_field(
            owner_id, "history_ticket", str(ticket_id), profile_key
        )
        self._backend.update_user_field(
            owner_id, "data_ticket", created_at, profile_key
        )
        ticket = self.get_ticket(profile_key, ticket_id)
        if ticket is None:
            raise RuntimeError(f"Unable to load created ticket {ticket_id}")
        return ticket

    def count_tickets(
        self,
        profile_key: str,
        owner_id: int | None,
        status: str,
    ) -> int:
        return int(self._backend.get_ticket_count(owner_id, status, profile_key))

    def list_user_tickets(
        self,
        profile_key: str,
        owner_id: int,
        status: str,
        limit: int,
        offset: int,
    ) -> tuple[TicketRecord, ...]:
        rows = self._backend.get_tickets_by_user(
            owner_id,
            status,
            limit=limit,
            offset=offset,
            bot_key=profile_key,
        )
        return tuple(TicketRecord.from_mapping(row) for row in rows)

    def list_team_tickets(
        self,
        profile_key: str,
        status: str,
        limit: int,
        offset: int,
    ) -> tuple[TicketRecord, ...]:
        if status == STATUS_OPEN:
            rows = self._backend.get_all_tickets_in_progress(
                limit, offset, profile_key
            )
        elif status == STATUS_COMPLETED:
            rows = self._backend.get_all_completed_tickets(
                limit, offset, profile_key
            )
        else:
            raise ValueError(f"Unsupported ticket status: {status}")
        return tuple(TicketRecord.from_mapping(row) for row in rows)

    def add_internal_note(
        self,
        profile_key: str,
        ticket_id: int,
        actor_id: int,
        text: str,
        created_at: str,
        idempotency_key: str | None,
    ) -> None:
        self._backend.add_ticket_comment(
            ticket_id,
            actor_id,
            text,
            created_at,
            bot_key=profile_key,
            idempotency_key=idempotency_key,
        )

    def add_public_message(
        self,
        profile_key: str,
        ticket_id: int,
        actor_id: int,
        text: str,
        created_at: str,
        idempotency_key: str | None,
    ) -> None:
        self._backend.add_ticket_public_message(
            ticket_id,
            actor_id,
            text,
            created_at,
            bot_key=profile_key,
            idempotency_key=idempotency_key,
        )

    def change_status(
        self,
        profile_key: str,
        ticket_id: int,
        new_status: str,
        actor_id: int,
        changed_at: str,
        idempotency_key: str | None,
    ) -> bool:
        return bool(
            self._backend.change_ticket_status(
                ticket_id,
                new_status,
                actor_id,
                changed_at,
                bot_key=profile_key,
                idempotency_key=idempotency_key,
            )
        )

    def latest_internal_note(self, profile_key: str, ticket_id: int) -> str | None:
        return self._backend.read_ticket_comment(ticket_id, profile_key)

    def save_draft_intake(
        self,
        profile_key: str,
        author_id: int,
        chat_id: int,
        message_id: int,
        body: str,
        received_at: str,
    ) -> None:
        self._backend.save_draft_intake(
            profile_key, author_id, chat_id, message_id, body, received_at
        )

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
    ) -> TicketDraftRecord:
        row = self._backend.create_forwarded_draft(
            profile_key,
            author_id,
            source_chat_id,
            source_message_id,
            source_body,
            forward_origin_type,
            forward_origin_name,
            forward_origin_date,
            created_at,
        )
        return TicketDraftRecord.from_mapping(row)

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
    ) -> str:
        return str(self._backend.append_forwarded_message(
            profile_key,
            author_id,
            chat_id,
            message_id,
            body,
            received_at,
            window_seconds,
            forward_origin_type,
            forward_origin_name,
            forward_origin_date,
        ))

    def finalize_due_forward_batches(
        self,
        profile_key: str,
        now: str,
        limit: int = 20,
    ) -> tuple[TicketDraftRecord, ...]:
        return tuple(
            TicketDraftRecord.from_mapping(row)
            for row in self._backend.finalize_due_forward_batches(
                profile_key, now, limit
            )
        )

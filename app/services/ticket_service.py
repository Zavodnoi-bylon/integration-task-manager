from __future__ import annotations

import datetime

from app import storage as sql
from app.domain import (
    ROLE_CLIENT,
    ROLE_ADMIN,
    ROLE_TECHNICIAN,
    STATUS_COMPLETED,
    STATUS_OPEN,
    TICKET_TYPE_DESTINATION,
    TICKET_TYPE_GENERAL,
    TICKET_TYPES,
    TicketEventRecord,
    TicketPage,
    TicketRecord,
    UserRecord,
)
from app.settings import Settings

TimestampInput = datetime.datetime | str | None


class TicketServiceError(RuntimeError):
    pass


class TicketNotFoundError(TicketServiceError):
    pass


class TicketStateError(TicketServiceError):
    pass


class AccessDeniedError(TicketServiceError):
    pass


class TicketService:
    """Use cases задач; не зависит от Telegram transport и UI."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._admin_users = frozenset(int(user_id) for user_id in settings.admin_users)
        self._technician_users = frozenset(
            int(user_id) for user_id in settings.technician_users
        )

    def is_admin(self, tg_id: int) -> bool:
        return tg_id in self._admin_users

    def is_technician(self, tg_id: int) -> bool:
        return tg_id in self._admin_users or tg_id in self._technician_users

    def role_for(self, tg_id: int) -> str:
        if self.is_admin(tg_id):
            return ROLE_ADMIN
        if tg_id in self._technician_users:
            return ROLE_TECHNICIAN
        return ROLE_CLIENT

    def register_user(
        self,
        tg_id: int,
        display_name: str,
        registered_at: TimestampInput,
    ) -> UserRecord:
        registered_at_value = sql.moscow_timestamp(registered_at)
        role = self.role_for(tg_id)
        sql.ensure_user(
            tg_id=tg_id,
            data_reg=registered_at_value,
            user_name=display_name,
            role=role,
            bot_key=self.settings.bot_key,
        )
        user = self.get_user(tg_id)
        if user is None:
            raise TicketServiceError(f"Unable to register user {tg_id}")
        return user

    def get_user(self, tg_id: int) -> UserRecord | None:
        row = sql.get_user_by_id(tg_id, self.settings.bot_key)
        return UserRecord.from_mapping(row) if row else None

    def get_ticket(self, ticket_id: int) -> TicketRecord:
        row = sql.get_ticket_info(ticket_id, self.settings.bot_key)
        if row is None:
            raise TicketNotFoundError(f"Ticket {ticket_id} does not exist")
        return TicketRecord.from_row(row)

    def get_events(self, ticket_id: int, limit: int = 20) -> tuple[TicketEventRecord, ...]:
        self.get_ticket(ticket_id)
        return tuple(
            TicketEventRecord.from_row(row)
            for row in sql.get_ticket_events(
                ticket_id,
                limit=limit,
                bot_key=self.settings.bot_key,
            )
        )

    def can_view(self, viewer_id: int, ticket: TicketRecord) -> bool:
        return self.is_technician(viewer_id) or ticket.owner_id == viewer_id

    def can_reopen(self, actor_id: int, ticket: TicketRecord) -> bool:
        return self.is_technician(actor_id) or ticket.owner_id == actor_id

    def require_view_access(self, viewer_id: int, ticket: TicketRecord) -> None:
        if not self.can_view(viewer_id, ticket):
            raise AccessDeniedError("Cannot view another user's ticket")

    def create_ticket(
        self,
        owner_id: int,
        body: str,
        created_at: TimestampInput,
        request_key: str | None = None,
        ticket_type: str = TICKET_TYPE_GENERAL,
    ) -> TicketRecord:
        body = body.strip()
        if not body:
            raise ValueError("Ticket body cannot be empty")
        if ticket_type not in TICKET_TYPES:
            raise ValueError(f"Unsupported ticket type: {ticket_type}")

        created_at_value = sql.moscow_timestamp(created_at)

        ticket_id = sql.add_ticket(
            owner_id,
            "",
            "",
            body,
            created_at_value,
            STATUS_OPEN,
            "",
            assigned_to=self.settings.admin_message,
            bot_key=self.settings.bot_key,
            request_key=request_key,
            ticket_type=ticket_type,
        )
        sql.update_user_field(
            owner_id,
            "history_ticket",
            str(ticket_id),
            self.settings.bot_key,
        )
        sql.update_user_field(
            owner_id,
            "data_ticket",
            created_at_value,
            self.settings.bot_key,
        )
        return self.get_ticket(ticket_id)

    def create_destination_ticket(
        self,
        owner_id: int,
        advertiser: str,
        source_body: str,
        cap: int,
        formatted_body: str,
        created_at: TimestampInput,
        request_key: str,
        technician_note: str | None = None,
    ) -> TicketRecord:
        advertiser = advertiser.strip()
        source_body = source_body.strip()
        if not advertiser:
            raise ValueError("Advertiser cannot be empty")
        if not source_body:
            raise ValueError("Source offer cannot be empty")
        if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
            raise ValueError("Cap must be a positive integer")

        ticket = self.create_ticket(
            owner_id,
            formatted_body,
            created_at,
            request_key=request_key,
            ticket_type=TICKET_TYPE_DESTINATION,
        )
        source_message = (
            f"Advertiser: {advertiser}\n"
            f"Cap: {cap}\n\n"
            f"{source_body}"
        )
        self.add_public_message(
            ticket.ticket_id,
            owner_id,
            source_message,
            created_at,
            idempotency_key=f"{request_key}:source",
        )
        if technician_note:
            sql.add_ticket_comment(
                ticket.ticket_id,
                self.settings.admin_message,
                f"Системная пометка: {technician_note.strip()}",
                sql.moscow_timestamp(created_at),
                bot_key=self.settings.bot_key,
                idempotency_key=f"{request_key}:technician-note",
            )
        return self.get_ticket(ticket.ticket_id)

    def list_user_tickets(
        self,
        owner_id: int,
        status: str,
        page: int = 1,
        page_size: int = 8,
    ) -> TicketPage:
        page, page_size = self._normalize_pagination(page, page_size)
        total = sql.get_ticket_count(owner_id, status, self.settings.bot_key)
        rows = sql.get_tickets_by_user(
            owner_id,
            status,
            limit=page_size,
            offset=(page - 1) * page_size,
            bot_key=self.settings.bot_key,
        )
        return TicketPage(
            items=tuple(TicketRecord.from_row(row) for row in rows),
            total=total,
            page=page,
            page_size=page_size,
        )

    def list_technician_tickets(
        self,
        status: str,
        page: int = 1,
        page_size: int = 10,
    ) -> TicketPage:
        page, page_size = self._normalize_pagination(page, page_size)
        total = sql.get_ticket_count(None, status, self.settings.bot_key)
        offset = (page - 1) * page_size
        if status == STATUS_OPEN:
            rows = sql.get_all_tickets_in_progress(
                page_size,
                offset,
                self.settings.bot_key,
            )
        elif status == STATUS_COMPLETED:
            rows = sql.get_all_completed_tickets(
                page_size,
                offset,
                self.settings.bot_key,
            )
        else:
            raise ValueError(f"Unsupported ticket status: {status}")
        return TicketPage(
            items=tuple(TicketRecord.from_row(row) for row in rows),
            total=total,
            page=page,
            page_size=page_size,
        )

    def add_comment(
        self,
        ticket_id: int,
        actor_id: int,
        text: str,
        created_at: TimestampInput,
        idempotency_key: str | None = None,
    ) -> TicketRecord:
        if not self.is_technician(actor_id):
            raise AccessDeniedError("Only technicians can add comments")
        ticket = self.get_ticket(ticket_id)
        if ticket.status != STATUS_OPEN:
            raise TicketStateError("Ticket is not open")
        text = text.strip()
        if not text:
            raise ValueError("Comment cannot be empty")
        sql.add_ticket_comment(
            ticket_id,
            actor_id,
            text,
            sql.moscow_timestamp(created_at),
            bot_key=self.settings.bot_key,
            idempotency_key=idempotency_key,
        )
        return self.get_ticket(ticket_id)

    def add_public_message(
        self,
        ticket_id: int,
        actor_id: int,
        text: str,
        created_at: TimestampInput,
        idempotency_key: str | None = None,
    ) -> TicketRecord:
        ticket = self.get_ticket(ticket_id)
        self.require_view_access(actor_id, ticket)
        if ticket.status != STATUS_OPEN:
            raise TicketStateError("Ticket is not open")
        text = text.strip()
        if not text:
            raise ValueError("Message cannot be empty")
        sql.add_ticket_public_message(
            ticket_id,
            actor_id,
            text,
            sql.moscow_timestamp(created_at),
            bot_key=self.settings.bot_key,
            idempotency_key=idempotency_key,
        )
        return self.get_ticket(ticket_id)

    def complete_ticket(
        self,
        ticket_id: int,
        actor_id: int,
        changed_at: TimestampInput,
        idempotency_key: str | None = None,
    ) -> TicketRecord:
        if not self.is_technician(actor_id):
            raise AccessDeniedError("Only technicians can complete tickets")
        ticket = self.get_ticket(ticket_id)
        if ticket.status != STATUS_OPEN:
            raise TicketStateError("Ticket is not open")
        if not sql.change_ticket_status(
            ticket_id,
            STATUS_COMPLETED,
            actor_id,
            sql.moscow_timestamp(changed_at),
            bot_key=self.settings.bot_key,
            idempotency_key=idempotency_key,
        ):
            raise TicketStateError("Ticket status was not changed")
        return self.get_ticket(ticket_id)

    def reopen_ticket(
        self,
        ticket_id: int,
        actor_id: int,
        changed_at: TimestampInput,
        idempotency_key: str | None = None,
    ) -> TicketRecord:
        ticket = self.get_ticket(ticket_id)
        if not self.can_reopen(actor_id, ticket):
            raise AccessDeniedError("Cannot reopen another user's ticket")
        if ticket.status != STATUS_COMPLETED:
            raise TicketStateError("Ticket is not completed")
        if not sql.change_ticket_status(
            ticket_id,
            STATUS_OPEN,
            actor_id,
            sql.moscow_timestamp(changed_at),
            bot_key=self.settings.bot_key,
            idempotency_key=idempotency_key,
        ):
            raise TicketStateError("Ticket status was not changed")
        return self.get_ticket(ticket_id)

    def latest_comment(self, ticket_id: int) -> str | None:
        self.get_ticket(ticket_id)
        return sql.read_ticket_comment(ticket_id, self.settings.bot_key)

    @staticmethod
    def _normalize_pagination(page: int, page_size: int) -> tuple[int, int]:
        if page < 1:
            raise ValueError("Page must be positive")
        if page_size < 1 or page_size > 100:
            raise ValueError("Page size must be between 1 and 100")
        return page, page_size

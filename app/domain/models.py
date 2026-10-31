from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


def _value(row: Sequence, index: int, default=None):
    return row[index] if len(row) > index else default


@dataclass(frozen=True, slots=True)
class UserRecord:
    tg_id: int
    registered_at: str
    display_name: str
    role: str
    bot_key: str

    @classmethod
    def from_mapping(cls, row: Mapping) -> UserRecord:
        return cls(
            tg_id=int(row["tg_id"]),
            registered_at=str(row.get("data_reg") or ""),
            display_name=str(row.get("user_name") or f"ID {row['tg_id']}"),
            role=str(row.get("role") or "client"),
            bot_key=str(row.get("bot_key") or "default"),
        )


@dataclass(frozen=True, slots=True)
class AdvertiserEntry:
    key: str
    name: str
    url_parameters: str
    technician_note: str | None
    priority: int


@dataclass(frozen=True, slots=True)
class TicketRecord:
    ticket_id: int
    owner_id: int
    body: str
    created_at: str
    status: str
    latest_comment: str
    assigned_to: int | None
    updated_at: str
    closed_at: str | None
    bot_key: str
    ticket_type: str = "general"

    @classmethod
    def from_row(cls, row: Sequence) -> TicketRecord:
        if len(row) < 8:
            raise ValueError("Ticket row does not match the expected schema")
        created_at = str(_value(row, 5, "") or "")
        assigned_to = _value(row, 8)
        return cls(
            ticket_id=int(row[0]),
            owner_id=int(row[1]),
            body=str(_value(row, 4, "") or ""),
            created_at=created_at,
            status=str(_value(row, 6, "") or ""),
            latest_comment=str(_value(row, 7, "") or ""),
            assigned_to=int(assigned_to) if assigned_to is not None else None,
            updated_at=str(_value(row, 9, created_at) or created_at),
            closed_at=(str(_value(row, 10)) if _value(row, 10) else None),
            bot_key=str(_value(row, 11, "default") or "default"),
            ticket_type=str(_value(row, 12, "general") or "general"),
        )


@dataclass(frozen=True, slots=True)
class TicketEventRecord:
    event_id: int
    ticket_id: int
    actor_id: int
    event_type: str
    from_status: str | None
    to_status: str | None
    text: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: Sequence) -> TicketEventRecord:
        if len(row) < 8:
            raise ValueError("Ticket event row does not match the expected schema")
        return cls(
            event_id=int(row[0]),
            ticket_id=int(row[1]),
            actor_id=int(row[2]),
            event_type=str(row[3]),
            from_status=str(row[4]) if row[4] is not None else None,
            to_status=str(row[5]) if row[5] is not None else None,
            text=str(row[6]) if row[6] is not None else None,
            created_at=str(row[7]),
        )


@dataclass(frozen=True, slots=True)
class TicketPage:
    items: tuple[TicketRecord, ...]
    total: int
    page: int
    page_size: int

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page * self.page_size < self.total

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


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
    def from_mapping(cls, row: Mapping) -> TicketRecord:
        created_at = str(row.get("created_at") or "")
        assigned_to = row.get("assigned_to")
        return cls(
            ticket_id=int(row["ticket_id"]),
            owner_id=int(row["owner_id"]),
            body=str(row.get("body") or ""),
            created_at=created_at,
            status=str(row.get("status") or ""),
            latest_comment=str(row.get("latest_comment") or ""),
            assigned_to=int(assigned_to) if assigned_to is not None else None,
            updated_at=str(row.get("updated_at") or created_at),
            closed_at=(str(row["closed_at"]) if row.get("closed_at") else None),
            bot_key=str(row.get("bot_key") or "default"),
            ticket_type=str(row.get("ticket_type") or "general"),
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
    def from_mapping(cls, row: Mapping) -> TicketEventRecord:
        return cls(
            event_id=int(row["event_id"]),
            ticket_id=int(row["ticket_id"]),
            actor_id=int(row.get("actor_id") or 0),
            event_type=str(row["event_type"]),
            from_status=(
                str(row["from_status"]) if row.get("from_status") is not None else None
            ),
            to_status=(
                str(row["to_status"]) if row.get("to_status") is not None else None
            ),
            text=str(row["text"]) if row.get("text") is not None else None,
            created_at=str(row["created_at"]),
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

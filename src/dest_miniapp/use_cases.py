from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID

from .domain import (
    EventDraft,
    EventKind,
    MessagePlan,
    NotificationKind,
    NotificationIntentDraft,
    TicketSnapshot,
    TransitionPlan,
    WorkflowAction,
    plan_message,
    plan_transition,
)
from .models import Actor


class IdempotencyConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class CatalogAdvertiser:
    kind: Literal["catalog"]
    key: str


@dataclass(frozen=True)
class NewAdvertiser:
    kind: Literal["new"]
    name: str


@dataclass(frozen=True)
class CreateTicketCommand:
    profile_key: str
    actor: Actor
    idempotency_key: UUID
    ticket_type: Literal["general", "destination"]
    body: str
    advertiser: CatalogAdvertiser | NewAdvertiser | None = None
    cap: int | None = None


@dataclass(frozen=True)
class AddMessageCommand:
    profile_key: str
    actor: Actor
    ticket_id: int
    idempotency_key: UUID
    visibility: Literal["public", "internal"]
    body: str


@dataclass(frozen=True)
class TransitionCommand:
    profile_key: str
    actor: Actor
    ticket_id: int
    idempotency_key: UUID
    action: WorkflowAction
    reason: str | None = None


@dataclass(frozen=True)
class MutationResult:
    ticket_id: int
    status: Literal["open", "review", "completed"]
    ticket_type: Literal["general", "destination"]
    review_cycle: int
    replayed: bool = False


class TicketMutationUnitOfWork(Protocol):
    """Canonical adapter must make each method one atomic transaction.

    The transaction owns ticket/event/message/outbox/idempotency writes. For
    transition/message, it loads and locks the profile-scoped snapshot before
    invoking the pure planner. A repeated key with the same payload returns the
    stored result; the same key with another hash raises IdempotencyConflict.
    """

    async def create_ticket(
        self,
        command: CreateTicketCommand,
        *,
        payload_hash: str,
        event: EventDraft,
        notification: NotificationIntentDraft,
    ) -> MutationResult: ...

    async def add_message(
        self,
        command: AddMessageCommand,
        *,
        payload_hash: str,
        planner: Callable[[TicketSnapshot], MessagePlan],
    ) -> MutationResult: ...

    async def transition(
        self,
        command: TransitionCommand,
        *,
        payload_hash: str,
        planner: Callable[[TicketSnapshot], TransitionPlan],
    ) -> MutationResult: ...


class TicketMutationService(Protocol):
    async def create_ticket(self, command: CreateTicketCommand) -> MutationResult: ...

    async def add_message(self, command: AddMessageCommand) -> MutationResult: ...

    async def transition(self, command: TransitionCommand) -> MutationResult: ...


class TicketWorkflow:
    def __init__(
        self,
        unit_of_work: TicketMutationUnitOfWork,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._clock = clock or (lambda: datetime.now(UTC))

    async def create_ticket(self, command: CreateTicketCommand) -> MutationResult:
        if command.ticket_type == "general":
            if command.advertiser is not None or command.cap is not None:
                raise ValueError("general ticket cannot contain destination fields")
        elif command.advertiser is None or command.cap is None:
            raise ValueError("destination ticket requires advertiser and cap")
        notification = NotificationIntentDraft(
            kind=NotificationKind.STAFF_TICKET_OPENED,
            dedup_key=f"idempotency:{command.profile_key}:{command.idempotency_key}:opened",
            target_telegram_user_id=None,
        )
        return await self._unit_of_work.create_ticket(
            command,
            payload_hash=_payload_hash(command),
            event=EventDraft(
                EventKind.TICKET_CREATED, command.actor.telegram_user_id
            ),
            notification=notification,
        )

    async def add_message(self, command: AddMessageCommand) -> MutationResult:
        return await self._unit_of_work.add_message(
            command,
            payload_hash=_payload_hash(command),
            planner=lambda ticket: plan_message(
                ticket,
                actor=command.actor,
                visibility=command.visibility,
                body=command.body,
            ),
        )

    async def transition(self, command: TransitionCommand) -> MutationResult:
        now = self._clock()
        return await self._unit_of_work.transition(
            command,
            payload_hash=_payload_hash(command),
            planner=lambda ticket: plan_transition(
                ticket,
                action=command.action,
                actor=command.actor,
                now=now,
                reason=command.reason,
            ),
        )


def _payload_hash(command: object) -> str:
    raw = asdict(command)
    raw.pop("idempotency_key", None)
    actor = raw.pop("actor", None)
    if actor is not None:
        raw["actor_telegram_user_id"] = actor["telegram_user_id"]
    raw["command_type"] = type(command).__name__
    payload = json.dumps(
        raw,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()

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


class DestinationFormattingError(RuntimeError):
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


@dataclass(frozen=True)
class IdempotencyScope:
    profile_key: str
    actor_telegram_user_id: int
    operation: str
    key: UUID

    @property
    def namespace(self) -> str:
        return (
            f"miniapp:{self.profile_key}:{self.actor_telegram_user_id}:"
            f"{self.operation}:{self.key}"
        )


@dataclass(frozen=True)
class MutationContext:
    profile_key: str
    actor: Actor


@dataclass(frozen=True)
class ResolvedAdvertiser:
    name: str
    suffix: str | None = None
    technician_note: str | None = None


@dataclass(frozen=True)
class DestinationFormatRequest:
    advertiser_name: str
    suffix: str | None
    technician_note: str | None
    cap: int
    source_body: str


@dataclass(frozen=True)
class DestinationFormatResult:
    formatted_ticket: str
    internal_note: str | None
    used_fallback: bool = False


@dataclass(frozen=True)
class PreparedTicketAggregate:
    ticket_type: Literal["general", "destination"]
    formatted_ticket: str
    source_public_message: str
    internal_note: str | None
    advertiser_name: str | None = None
    cap: int | None = None
    used_fallback: bool = False


class AdvertiserCatalogPort(Protocol):
    async def resolve(
        self, *, profile_key: str, catalog_key: str
    ) -> ResolvedAdvertiser: ...


class DestinationFormatterPort(Protocol):
    async def format(self, request: DestinationFormatRequest) -> DestinationFormatResult: ...

    def fallback(self, request: DestinationFormatRequest) -> DestinationFormatResult: ...


class TicketMutationUnitOfWork(Protocol):
    """Canonical adapter must make each method one atomic transaction.

    The transaction owns ticket/event/message/outbox/idempotency writes. For
    transition/message, it first loads and locks a ticket visible to the actor
    in the server-side profile, then invokes the planner. For a client,
    absent/foreign/cross-profile all raise ResourceNotFound before the planner.
    A repeated scoped key with the same payload returns the stored result; the
    same scoped key with another hash raises IdempotencyConflict.
    """

    async def create_ticket(
        self,
        context: MutationContext,
        aggregate: PreparedTicketAggregate,
        *,
        idempotency: IdempotencyScope,
        payload_hash: str,
        event: EventDraft,
        notification: NotificationIntentDraft,
    ) -> MutationResult: ...

    async def add_message(
        self,
        command: AddMessageCommand,
        *,
        idempotency: IdempotencyScope,
        payload_hash: str,
        planner: Callable[[TicketSnapshot], MessagePlan],
    ) -> MutationResult: ...

    async def transition(
        self,
        command: TransitionCommand,
        *,
        idempotency: IdempotencyScope,
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
        advertisers: AdvertiserCatalogPort,
        destination_formatter: DestinationFormatterPort,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._advertisers = advertisers
        self._destination_formatter = destination_formatter
        self._clock = clock or (lambda: datetime.now(UTC))

    async def create_ticket(self, command: CreateTicketCommand) -> MutationResult:
        if command.ticket_type == "general":
            if command.advertiser is not None or command.cap is not None:
                raise ValueError("general ticket cannot contain destination fields")
            aggregate = PreparedTicketAggregate(
                ticket_type="general",
                formatted_ticket=command.body,
                source_public_message=command.body,
                internal_note=None,
            )
        elif command.advertiser is None or command.cap is None:
            raise ValueError("destination ticket requires advertiser and cap")
        else:
            aggregate = await self._prepare_destination(command)
        idempotency = _idempotency_scope(command, "create_ticket")
        notification = NotificationIntentDraft(
            kind=NotificationKind.STAFF_TICKET_OPENED,
            dedup_key=f"{idempotency.namespace}:opened",
            target_telegram_user_id=None,
        )
        return await self._unit_of_work.create_ticket(
            MutationContext(command.profile_key, command.actor),
            aggregate,
            idempotency=idempotency,
            payload_hash=_payload_hash(command),
            event=EventDraft(
                EventKind.TICKET_CREATED, command.actor.telegram_user_id
            ),
            notification=notification,
        )

    async def add_message(self, command: AddMessageCommand) -> MutationResult:
        return await self._unit_of_work.add_message(
            command,
            idempotency=_idempotency_scope(command, "add_message"),
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
            idempotency=_idempotency_scope(command, command.action.value),
            payload_hash=_payload_hash(command),
            planner=lambda ticket: plan_transition(
                ticket,
                action=command.action,
                actor=command.actor,
                now=now,
                reason=command.reason,
            ),
        )

    async def _prepare_destination(
        self, command: CreateTicketCommand
    ) -> PreparedTicketAggregate:
        assert command.advertiser is not None and command.cap is not None
        if isinstance(command.advertiser, CatalogAdvertiser):
            advertiser = await self._advertisers.resolve(
                profile_key=command.profile_key,
                catalog_key=command.advertiser.key,
            )
        else:
            advertiser = ResolvedAdvertiser(name=command.advertiser.name)
        request = DestinationFormatRequest(
            advertiser_name=advertiser.name,
            suffix=advertiser.suffix,
            technician_note=advertiser.technician_note,
            cap=command.cap,
            source_body=command.body,
        )
        try:
            formatted = await self._destination_formatter.format(request)
        except DestinationFormattingError:
            formatted = self._destination_formatter.fallback(request)
        return PreparedTicketAggregate(
            ticket_type="destination",
            formatted_ticket=formatted.formatted_ticket,
            source_public_message=command.body,
            internal_note=formatted.internal_note or advertiser.technician_note,
            advertiser_name=advertiser.name,
            cap=command.cap,
            used_fallback=formatted.used_fallback,
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


def _idempotency_scope(
    command: CreateTicketCommand | AddMessageCommand | TransitionCommand,
    operation: str,
) -> IdempotencyScope:
    return IdempotencyScope(
        profile_key=command.profile_key,
        actor_telegram_user_id=command.actor.telegram_user_id,
        operation=operation,
        key=command.idempotency_key,
    )

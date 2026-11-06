from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Literal, Protocol, TypeAlias
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


CapValue: TypeAlias = int | Literal["uncap"]


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
    cap: CapValue | None = None


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
    cap: CapValue
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
    source_public_message: str | None
    internal_note: str | None
    advertiser_name: str | None = None
    cap: CapValue | None = None
    used_fallback: bool = False


class AdvertiserCatalogPort(Protocol):
    async def resolve(
        self, *, profile_key: str, catalog_key: str
    ) -> ResolvedAdvertiser: ...


class DestinationFormatterPort(Protocol):
    async def format(self, request: DestinationFormatRequest) -> DestinationFormatResult: ...

    def fallback(self, request: DestinationFormatRequest) -> DestinationFormatResult: ...


@dataclass(frozen=True)
class IdempotencyLease:
    token: str


@dataclass(frozen=True)
class IdempotencyReplay:
    result: MutationResult


class IdempotencyReservationPort(Protocol):
    """Durable single-flight gate used before external create side effects.

    acquire returns a lease to one caller. A concurrent same-hash caller waits
    for the stored result or takes over only after lease expiry. A completed
    entry returns IdempotencyReplay; another hash raises IdempotencyConflict.
    The final UOW transaction stores the response and completes the lease.
    fail releases/marks a live lease retryable. A process crash is recovered by
    finite lease expiry, so no key stays processing forever.
    """

    async def acquire(
        self, *, scope: IdempotencyScope, payload_hash: str
    ) -> IdempotencyLease | IdempotencyReplay: ...

    async def fail(self, *, scope: IdempotencyScope, lease_token: str) -> None: ...


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
        idempotency_lease: str,
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
        idempotency_reservations: IdempotencyReservationPort,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._advertisers = advertisers
        self._destination_formatter = destination_formatter
        self._idempotency_reservations = idempotency_reservations
        self._clock = clock or (lambda: datetime.now(UTC))

    async def create_ticket(self, command: CreateTicketCommand) -> MutationResult:
        idempotency = _idempotency_scope(command, "create_ticket")
        payload_hash = _payload_hash(command)
        reservation = await self._idempotency_reservations.acquire(
            scope=idempotency, payload_hash=payload_hash
        )
        if isinstance(reservation, IdempotencyReplay):
            return replace(reservation.result, replayed=True)
        try:
            aggregate = await self._prepare_create_aggregate(command)
            notification = NotificationIntentDraft(
                kind=NotificationKind.STAFF_TICKET_OPENED,
                dedup_key=f"{idempotency.namespace}:opened",
                target_telegram_user_id=None,
            )
            return await self._unit_of_work.create_ticket(
                MutationContext(command.profile_key, command.actor),
                aggregate,
                idempotency=idempotency,
                idempotency_lease=reservation.token,
                payload_hash=payload_hash,
                event=EventDraft(
                    EventKind.TICKET_CREATED, command.actor.telegram_user_id
                ),
                notification=notification,
            )
        except Exception:
            await self._idempotency_reservations.fail(
                scope=idempotency, lease_token=reservation.token
            )
            raise

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
        if not _valid_cap(command.cap):
            raise ValueError("destination cap must be a positive integer or uncap")
        if isinstance(command.advertiser, CatalogAdvertiser):
            advertiser = await self._advertisers.resolve(
                profile_key=command.profile_key,
                catalog_key=command.advertiser.key,
            )
        else:
            advertiser = ResolvedAdvertiser(
                name=command.advertiser.name,
                technician_note="Новый рекл",
            )
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
            source_public_message=_destination_source_message(
                advertiser.name, command.cap, command.body
            ),
            internal_note=_merge_notes(
                advertiser.technician_note, formatted.internal_note
            ),
            advertiser_name=advertiser.name,
            cap=command.cap,
            used_fallback=formatted.used_fallback,
        )

    async def _prepare_create_aggregate(
        self, command: CreateTicketCommand
    ) -> PreparedTicketAggregate:
        if command.ticket_type == "general":
            if command.advertiser is not None or command.cap is not None:
                raise ValueError("general ticket cannot contain destination fields")
            return PreparedTicketAggregate(
                ticket_type="general",
                formatted_ticket=command.body,
                source_public_message=None,
                internal_note=None,
            )
        if command.advertiser is None or command.cap is None:
            raise ValueError("destination ticket requires advertiser and cap")
        return await self._prepare_destination(command)


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


def _destination_source_message(
    advertiser_name: str, cap: CapValue, body: str
) -> str:
    return f"Advertiser: {advertiser_name}\nCap: {cap}\n\n{body}"


def _valid_cap(value: object) -> bool:
    return value == "uncap" or (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 1 <= value <= 1_000_000
    )


def _merge_notes(*notes: str | None) -> str | None:
    merged: list[str] = []
    for note in notes:
        clean = note.strip() if note else ""
        if clean and clean not in merged:
            merged.append(clean)
    return "\n\n".join(merged) or None

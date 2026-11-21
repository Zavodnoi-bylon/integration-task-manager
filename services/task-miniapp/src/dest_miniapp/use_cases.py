from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
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
CapMode: TypeAlias = Literal["unlim", "daily", "monthly", "lifetime"]


@dataclass(frozen=True)
class CatalogAdvertiser:
    kind: Literal["catalog"]
    key: str


@dataclass(frozen=True)
class NewAdvertiser:
    kind: Literal["new"]
    name: str


@dataclass(frozen=True)
class DestinationRequestDetails:
    naming: str
    offer_name: str
    platform: str
    approach: str
    geo: str
    rate: Decimal
    currency: str
    landing_url: str
    clickid_subid: str = ""
    additional: str = ""


@dataclass(frozen=True)
class ExternalIdentifierPolicySpec:
    parameter: str
    action: Literal["replace_visit_uuid", "remove"]
    output_parameter: str | None = None


@dataclass(frozen=True)
class DestinationResult:
    naming: str
    url: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CreateTicketCommand:
    profile_key: str
    actor: Actor
    idempotency_key: UUID
    ticket_type: Literal["general", "destination"]
    body: str
    advertiser: CatalogAdvertiser | NewAdvertiser | None = None
    cap: CapValue | None = None
    cap_mode: CapMode | None = None
    details: DestinationRequestDetails | None = None
    important_comment: str | None = None


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
    ticket_number: int | None = None

    @property
    def public_number(self) -> int:
        return self.ticket_number or self.ticket_id


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
    external_id_policy: tuple[ExternalIdentifierPolicySpec, ...] = ()


@dataclass(frozen=True)
class DestinationFormatRequest:
    profile_key: str
    advertiser_name: str
    suffix: str | None
    technician_note: str | None
    cap: CapValue
    cap_mode: CapMode
    source_body: str
    details: DestinationRequestDetails | None = None
    external_id_policy: tuple[ExternalIdentifierPolicySpec, ...] = ()


@dataclass(frozen=True)
class DestinationFormatResult:
    formatted_ticket: str
    internal_note: str | None
    used_fallback: bool = False
    results: tuple[DestinationResult, ...] = ()


@dataclass(frozen=True)
class PreparedDestinationRequest:
    advertiser_key: str
    advertiser_name: str
    details: DestinationRequestDetails
    cap: CapValue
    cap_mode: CapMode
    source_body: str
    processing_status: Literal["validated", "fallback", "passthrough"]


@dataclass(frozen=True)
class PreparedTicketAggregate:
    ticket_type: Literal["general", "destination"]
    formatted_ticket: str
    source_public_message: str | None
    internal_note: str | None
    advertiser_name: str | None = None
    cap: CapValue | None = None
    cap_mode: CapMode | None = None
    used_fallback: bool = False
    title: str | None = None
    destination_request: PreparedDestinationRequest | None = None
    destination_results: tuple[DestinationResult, ...] = ()
    important_comment: str | None = None
    new_advertiser: bool = False


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
        if not _valid_cap(command.cap, command.cap_mode):
            raise ValueError(
                "destination cap and cap mode must be a valid matching pair"
            )
        assert command.cap_mode is not None
        if command.profile_key == "kalisto":
            if command.details is None:
                raise ValueError("Kalisto requires structured destination details")
            if any(
                not value.strip()
                for value in (
                    command.details.offer_name,
                    command.details.platform,
                    command.details.approach,
                )
            ):
                raise ValueError("Kalisto requires complete naming components")
        elif command.details is not None:
            raise ValueError("structured destination details are only valid for Kalisto")
        important_comment = (command.important_comment or "").strip() or None
        if isinstance(command.advertiser, CatalogAdvertiser):
            advertiser = await self._advertisers.resolve(
                profile_key=command.profile_key,
                catalog_key=command.advertiser.key,
            )
        else:
            advertiser = ResolvedAdvertiser(
                name=command.advertiser.name,
            )
        is_new_advertiser = isinstance(command.advertiser, NewAdvertiser)
        source_body = (
            _kalisto_source_message(
                advertiser.name,
                command.cap,
                command.cap_mode,
                command.details,
                original_body=command.body,
            )
            if command.details is not None
            else command.body
        )
        request = DestinationFormatRequest(
            profile_key=command.profile_key,
            advertiser_name=advertiser.name,
            suffix=advertiser.suffix,
            technician_note=advertiser.technician_note,
            cap=command.cap,
            cap_mode=command.cap_mode,
            source_body=source_body,
            details=command.details,
            external_id_policy=advertiser.external_id_policy,
        )
        fallback_diagnostic = None
        passthrough = command.profile_key == "kalisto" and is_new_advertiser
        if passthrough:
            assert command.details is not None
            formatted = _kalisto_passthrough(command.details)
        else:
            try:
                formatted = await self._destination_formatter.format(request)
                if not formatted.results:
                    raise DestinationFormattingError(
                        "formatter returned no validated results"
                    )
                if command.details is not None and (
                    len(formatted.results) != 1
                    or formatted.results[0].naming != command.details.naming
                ):
                    raise DestinationFormattingError(
                        "formatter changed the immutable naming"
                    )
            except DestinationFormattingError as exc:
                formatted = self._destination_formatter.fallback(request)
                fallback_diagnostic = (
                    str(exc).strip() or "destination validation failed"
                )
        prepared_request = None
        if command.details is not None:
            prepared_request = PreparedDestinationRequest(
                advertiser_key=(
                    command.advertiser.key
                    if isinstance(command.advertiser, CatalogAdvertiser)
                    else "__new__"
                ),
                advertiser_name=advertiser.name,
                details=command.details,
                cap=command.cap,
                cap_mode=command.cap_mode,
                source_body=source_body,
                processing_status=(
                    "passthrough"
                    if passthrough
                    else "fallback" if formatted.used_fallback else "validated"
                ),
            )
        return PreparedTicketAggregate(
            ticket_type="destination",
            formatted_ticket=formatted.formatted_ticket,
            source_public_message=(
                source_body
                if command.details is not None
                else _destination_source_message(
                    advertiser.name,
                    command.cap,
                    command.cap_mode,
                    command.body,
                )
            ),
            internal_note=_merge_notes(
                advertiser.technician_note,
                formatted.internal_note,
                (
                    f"Destination validation fallback: {fallback_diagnostic}"
                    if fallback_diagnostic
                    else None
                ),
            ),
            advertiser_name=advertiser.name,
            cap=command.cap,
            cap_mode=command.cap_mode,
            used_fallback=formatted.used_fallback,
            title=(
                command.details.naming
                if command.details is not None
                else formatted.results[0].naming if formatted.results else None
            ),
            destination_request=prepared_request,
            destination_results=formatted.results,
            important_comment=important_comment,
            new_advertiser=is_new_advertiser,
        )

    async def _prepare_create_aggregate(
        self, command: CreateTicketCommand
    ) -> PreparedTicketAggregate:
        if command.ticket_type == "general":
            if (
                command.advertiser is not None
                or command.cap is not None
                or command.details is not None
                or command.important_comment
            ):
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
    advertiser_name: str, cap: CapValue, cap_mode: CapMode, body: str
) -> str:
    return (
        f"Advertiser: {advertiser_name}\n"
        f"Cap: {_render_cap(cap, cap_mode)}\n\n{body}"
    )


def _kalisto_source_message(
    advertiser_name: str,
    cap: CapValue,
    cap_mode: CapMode,
    details: DestinationRequestDetails | None,
    *,
    original_body: str,
) -> str:
    assert details is not None
    lines = [
        f"Advertiser: {advertiser_name}",
        f"Naming: {details.naming}",
        f"Offer: {details.offer_name}",
        f"Platform: {details.platform}",
        f"GEO: {details.geo}",
        f"Approach: {details.approach}",
        f"Rate: {details.rate} {details.currency}",
        f"Cap: {_render_cap(cap, cap_mode)}",
        f"Landing URL: {details.landing_url}",
    ]
    if details.clickid_subid:
        lines.append(f"ClickID / SubID: {details.clickid_subid}")
    if details.additional:
        lines.append(f"Additional: {details.additional}")
    if original_body.strip():
        lines.extend(("", "Original source:", original_body.strip()))
    return "\n".join(lines)


def _kalisto_passthrough(
    details: DestinationRequestDetails,
) -> DestinationFormatResult:
    return DestinationFormatResult(
        formatted_ticket=(
            f"{details.naming}\n\nDestination:\n{details.landing_url}"
        ),
        internal_note=None,
        results=(DestinationResult(details.naming, details.landing_url),),
    )


def _valid_cap(value: object, mode: object) -> bool:
    if mode == "unlim":
        return value == "uncap"
    return mode in {"daily", "monthly", "lifetime"} and (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 1 <= value <= 1_000_000
    )


def _render_cap(value: CapValue, mode: CapMode) -> str:
    return "unlim" if mode == "unlim" else f"{value} {mode}"


def _merge_notes(*notes: str | None) -> str | None:
    merged: list[str] = []
    for note in notes:
        clean = note.strip() if note else ""
        if clean and clean not in merged:
            merged.append(clean)
    return "\n\n".join(merged) or None

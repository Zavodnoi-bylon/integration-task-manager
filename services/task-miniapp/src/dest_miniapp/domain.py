from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum
from typing import Literal
from zoneinfo import ZoneInfo

from .models import Actor


TicketType = Literal["general", "destination"]
TicketStatus = Literal["open", "review", "completed"]


class WorkflowAction(StrEnum):
    SUBMIT_REVIEW = "submit_review"
    ACCEPT = "accept"
    RETURN = "return"
    COMPLETE_DESTINATION = "complete_destination"
    REOPEN = "reopen"


class EventKind(StrEnum):
    TICKET_CREATED = "ticket_created"
    GENERAL_SUBMITTED_FOR_REVIEW = "general_submitted_for_review"
    GENERAL_ACCEPTED = "general_accepted"
    GENERAL_RETURNED = "general_returned"
    DESTINATION_COMPLETED = "destination_completed"
    TICKET_REOPENED = "ticket_reopened"


class NotificationKind(StrEnum):
    STAFF_TICKET_OPENED = "staff_ticket_opened"
    AUTHOR_PUBLIC_MESSAGE = "author_public_message"
    STAFF_PUBLIC_MESSAGE = "staff_public_message"
    AUTHOR_REVIEW_REQUESTED = "author_review_requested"
    AUTHOR_REVIEW_REMINDER = "author_review_reminder"
    STAFF_TICKET_ACCEPTED = "staff_ticket_accepted"
    STAFF_TICKET_RETURNED = "staff_ticket_returned"
    AUTHOR_DESTINATION_COMPLETED = "author_destination_completed"
    STAFF_TICKET_REOPENED = "staff_ticket_reopened"


class DomainError(RuntimeError):
    pass


class InvalidTransition(DomainError):
    pass


class ActionForbidden(DomainError):
    pass


class TicketHidden(DomainError):
    pass


class ReasonRequired(DomainError):
    pass


@dataclass(frozen=True)
class TicketSnapshot:
    ticket_id: int
    profile_key: str
    ticket_type: TicketType
    status: TicketStatus
    author_telegram_user_id: int
    review_cycle: int = 0
    event_sequence: int = 0


@dataclass(frozen=True)
class EventDraft:
    kind: EventKind
    actor_telegram_user_id: int
    public_reason: str | None = None
    review_cycle: int | None = None


@dataclass(frozen=True)
class NotificationIntentDraft:
    kind: NotificationKind
    dedup_key: str
    target_telegram_user_id: int | None
    due_at: datetime | None = None
    public_reason: str | None = None
    review_cycle: int | None = None
    deliver_if_status: TicketStatus | None = None


@dataclass(frozen=True)
class TransitionPlan:
    new_status: TicketStatus
    review_cycle: int
    event: EventDraft
    public_message: str | None
    notifications: tuple[NotificationIntentDraft, ...]


@dataclass(frozen=True)
class MessagePlan:
    visibility: Literal["public", "internal"]
    body: str
    notifications: tuple[NotificationIntentDraft, ...]


def plan_transition(
    ticket: TicketSnapshot,
    *,
    action: WorkflowAction,
    actor: Actor,
    now: datetime,
    reason: str | None = None,
) -> TransitionPlan:
    clean_reason = reason.strip() if reason else None
    is_author = actor.telegram_user_id == ticket.author_telegram_user_id
    next_sequence = ticket.event_sequence + 1

    if action is WorkflowAction.SUBMIT_REVIEW:
        _require_staff(actor)
        _require(ticket.ticket_type == "general" and ticket.status == "open")
        cycle = ticket.review_cycle + 1
        reminder_at = _next_moscow_noon(now)
        return TransitionPlan(
            new_status="review",
            review_cycle=cycle,
            event=EventDraft(
                EventKind.GENERAL_SUBMITTED_FOR_REVIEW,
                actor.telegram_user_id,
                review_cycle=cycle,
            ),
            public_message=None,
            notifications=(
                NotificationIntentDraft(
                    NotificationKind.AUTHOR_REVIEW_REQUESTED,
                    f"ticket:{ticket.ticket_id}:review:{cycle}:requested",
                    ticket.author_telegram_user_id,
                    review_cycle=cycle,
                ),
                NotificationIntentDraft(
                    NotificationKind.AUTHOR_REVIEW_REMINDER,
                    f"ticket:{ticket.ticket_id}:review:{cycle}:reminder",
                    ticket.author_telegram_user_id,
                    due_at=reminder_at,
                    review_cycle=cycle,
                    deliver_if_status="review",
                ),
            ),
        )

    if action is WorkflowAction.ACCEPT:
        _require_author(actor, is_author)
        _require(ticket.ticket_type == "general" and ticket.status == "review")
        return TransitionPlan(
            new_status="completed",
            review_cycle=ticket.review_cycle,
            event=EventDraft(
                EventKind.GENERAL_ACCEPTED,
                actor.telegram_user_id,
                review_cycle=ticket.review_cycle,
            ),
            public_message=None,
            notifications=(
                NotificationIntentDraft(
                    NotificationKind.STAFF_TICKET_ACCEPTED,
                    f"ticket:{ticket.ticket_id}:review:{ticket.review_cycle}:accepted",
                    None,
                    review_cycle=ticket.review_cycle,
                ),
            ),
        )

    if action is WorkflowAction.RETURN:
        _require_author(actor, is_author)
        _require(ticket.ticket_type == "general" and ticket.status == "review")
        clean_reason = _require_reason(clean_reason)
        return TransitionPlan(
            new_status="open",
            review_cycle=ticket.review_cycle,
            event=EventDraft(
                EventKind.GENERAL_RETURNED,
                actor.telegram_user_id,
                public_reason=clean_reason,
                review_cycle=ticket.review_cycle,
            ),
            public_message=clean_reason,
            notifications=(
                NotificationIntentDraft(
                    NotificationKind.STAFF_TICKET_RETURNED,
                    f"ticket:{ticket.ticket_id}:review:{ticket.review_cycle}:returned",
                    None,
                    public_reason=clean_reason,
                    review_cycle=ticket.review_cycle,
                ),
            ),
        )

    if action is WorkflowAction.COMPLETE_DESTINATION:
        _require_staff(actor)
        _require(ticket.ticket_type == "destination" and ticket.status == "open")
        return TransitionPlan(
            new_status="completed",
            review_cycle=ticket.review_cycle,
            event=EventDraft(
                EventKind.DESTINATION_COMPLETED, actor.telegram_user_id
            ),
            public_message=None,
            notifications=(
                NotificationIntentDraft(
                    NotificationKind.AUTHOR_DESTINATION_COMPLETED,
                    f"ticket:{ticket.ticket_id}:transition:{next_sequence}:destination-completed",
                    ticket.author_telegram_user_id,
                ),
            ),
        )

    if action is WorkflowAction.REOPEN:
        _require_author(actor, is_author)
        _require(ticket.status == "completed")
        clean_reason = _require_reason(clean_reason)
        return TransitionPlan(
            new_status="open",
            review_cycle=ticket.review_cycle,
            event=EventDraft(
                EventKind.TICKET_REOPENED,
                actor.telegram_user_id,
                public_reason=clean_reason,
                review_cycle=ticket.review_cycle or None,
            ),
            public_message=clean_reason,
            notifications=(
                NotificationIntentDraft(
                    NotificationKind.STAFF_TICKET_REOPENED,
                    f"ticket:{ticket.ticket_id}:transition:{next_sequence}:reopened",
                    None,
                    public_reason=clean_reason,
                    review_cycle=ticket.review_cycle or None,
                ),
            ),
        )

    raise InvalidTransition("unsupported action")


def plan_message(
    ticket: TicketSnapshot,
    *,
    actor: Actor,
    visibility: Literal["public", "internal"],
    body: str,
) -> MessagePlan:
    clean_body = body.strip()
    if not clean_body:
        raise ReasonRequired("message body is required")
    is_author = actor.telegram_user_id == ticket.author_telegram_user_id
    if visibility == "internal":
        _require_staff(actor)
        if ticket.status == "completed":
            raise InvalidTransition("completed ticket does not accept messages")
        return MessagePlan("internal", clean_body, ())
    if actor.role == "client" and not is_author:
        raise TicketHidden("ticket not found")
    if ticket.status == "completed":
        raise InvalidTransition("completed ticket does not accept messages")
    if actor.is_staff:
        target = ticket.author_telegram_user_id
        kind = NotificationKind.AUTHOR_PUBLIC_MESSAGE
    else:
        target = None
        kind = NotificationKind.STAFF_PUBLIC_MESSAGE
    sequence = ticket.event_sequence + 1
    return MessagePlan(
        "public",
        clean_body,
        (
            NotificationIntentDraft(
                kind,
                f"ticket:{ticket.ticket_id}:message:{sequence}:{kind.value}",
                target,
                public_reason=clean_body,
            ),
        ),
    )


def _require(condition: bool) -> None:
    if not condition:
        raise InvalidTransition("transition is not allowed from current state")


def _require_staff(actor: Actor) -> None:
    if not actor.is_staff:
        raise ActionForbidden("staff role required")


def _require_author(actor: Actor, is_author: bool) -> None:
    if is_author:
        return
    if actor.role == "client":
        raise TicketHidden("ticket not found")
    raise ActionForbidden("ticket author required")


def _require_reason(reason: str | None) -> str:
    if not reason:
        raise ReasonRequired("public reason is required")
    return reason


def _next_moscow_noon(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    moscow = ZoneInfo("Europe/Moscow")
    local_now = now.astimezone(moscow)
    tomorrow = local_now.date() + timedelta(days=1)
    return datetime.combine(tomorrow, time(hour=12), tzinfo=moscow)

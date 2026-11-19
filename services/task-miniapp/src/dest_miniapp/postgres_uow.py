from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from .domain import (
    EventKind,
    MessagePlan,
    NotificationIntentDraft,
    TicketSnapshot,
    TransitionPlan,
)
from .gateway import AccessDenied, ResourceNotFound
from .models import Actor
from .postgres import PostgresDatabase, mutation_result_json
from .use_cases import (
    AddMessageCommand,
    IdempotencyConflict,
    IdempotencyScope,
    MutationContext,
    MutationResult,
    PreparedTicketAggregate,
    TicketMutationUnitOfWork,
    TransitionCommand,
)


class PostgresTicketMutationUow(TicketMutationUnitOfWork):
    def __init__(
        self,
        database: PostgresDatabase,
        *,
        technical_actor_telegram_id: int,
    ) -> None:
        self._database = database
        self._technical_actor_telegram_id = technical_actor_telegram_id

    async def create_ticket(
        self,
        context: MutationContext,
        aggregate: PreparedTicketAggregate,
        *,
        idempotency: IdempotencyScope,
        idempotency_lease: str,
        payload_hash: str,
        event,
        notification: NotificationIntentDraft,
    ) -> MutationResult:
        now = datetime.now(UTC)
        async with self._database.pool.connection() as connection:
            async with connection.transaction():
                replay = await _lock_reserved_idempotency(
                    connection, idempotency, payload_hash, idempotency_lease
                )
                if replay:
                    return replay
                actor_id, actor = await _actor(connection, context.profile_key,
                                               context.actor.telegram_user_id)
                _same_role(context.actor, actor)
                row = await (
                    await connection.execute(
                        """
                        INSERT INTO ticketing.tickets
                            (profile_key,ticket_number,requester_user_id,status_code,
                             ticket_type_code,description,title,important_comment,
                             request_key,source,
                             created_at,updated_at,metadata)
                        VALUES (
                            %s,ticketing.next_ticket_number(%s),%s,'open',%s,%s,%s,%s,
                            %s,'ui',%s,%s,%s
                        )
                        RETURNING ticket_id,ticket_number,review_cycle
                        """,
                        (
                            context.profile_key, context.profile_key, actor_id,
                            aggregate.ticket_type,
                            aggregate.formatted_ticket, aggregate.title,
                            aggregate.important_comment,
                            idempotency.namespace,
                            now, now, Jsonb({
                                "advertiser_name": aggregate.advertiser_name,
                                "cap": aggregate.cap,
                                "cap_mode": aggregate.cap_mode,
                                "used_fallback": aggregate.used_fallback,
                            }),
                        ),
                    )
                ).fetchone()
                ticket_id = int(row["ticket_id"])
                if aggregate.destination_request is not None:
                    request = aggregate.destination_request
                    details = request.details
                    await connection.execute(
                        """
                        INSERT INTO ticketing.destination_requests
                            (ticket_id,profile_key,advertiser_key,advertiser_name,
                             naming,geo,rate,currency,cap,cap_unlimited,cap_mode,landing_url,
                             clickid_subid,additional,source_body,processing_status,
                             created_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            ticket_id,
                            context.profile_key,
                            request.advertiser_key,
                            request.advertiser_name,
                            details.naming,
                            details.geo,
                            details.rate,
                            details.currency,
                            request.cap if isinstance(request.cap, int) else None,
                            request.cap == "uncap",
                            request.cap_mode,
                            details.landing_url,
                            details.clickid_subid,
                            details.additional,
                            request.source_body,
                            request.processing_status,
                            now,
                        ),
                    )
                for order, destination in enumerate(
                    aggregate.destination_results, start=1
                ):
                    await connection.execute(
                        """
                        INSERT INTO ticketing.destination_results
                            (ticket_id,profile_key,result_order,naming,destination_url,
                             warnings,created_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            ticket_id,
                            context.profile_key,
                            order,
                            destination.naming,
                            destination.url,
                            list(destination.warnings),
                            now,
                        ),
                    )
                event_id = await _insert_event(
                    connection, ticket_id, context.profile_key, actor_id, actor.role,
                    "created", None, None, f"{idempotency.namespace}:event", now,
                    {"workflow_action": event.kind.value},
                )
                if aggregate.source_public_message:
                    message_id = await _insert_message(
                        connection, ticket_id, context.profile_key, actor_id,
                        actor.role, "public", aggregate.source_public_message,
                        f"{idempotency.namespace}:source", now,
                    )
                    await _insert_event(
                        connection, ticket_id, context.profile_key, actor_id,
                        actor.role, "public_message", None, None,
                        f"{idempotency.namespace}:source:event", now,
                        {"message_id": message_id}, message_id=message_id,
                    )
                if aggregate.internal_note:
                    staff_id, staff_role = await _technical_staff(
                        connection,
                        context.profile_key,
                        self._technical_actor_telegram_id,
                    )
                    message_id = await _insert_message(
                        connection, ticket_id, context.profile_key, staff_id,
                        staff_role, "internal",
                        f"Техническая пометка: {aggregate.internal_note}",
                        f"{idempotency.namespace}:internal", now,
                    )
                    await _insert_event(
                        connection, ticket_id, context.profile_key, staff_id,
                        staff_role, "internal_note", None, None,
                        f"{idempotency.namespace}:internal:event", now,
                        {"message_id": message_id}, message_id=message_id,
                    )
                await _insert_notifications(
                    connection, context.profile_key, ticket_id,
                    int(row["ticket_number"]), event_id,
                    (notification,), now,
                )
                result = MutationResult(
                    ticket_id, "open", aggregate.ticket_type, int(row["review_cycle"]),
                    ticket_number=int(row["ticket_number"]),
                )
                await _complete_idempotency(
                    connection, idempotency, idempotency_lease, result
                )
                return result

    async def add_message(
        self,
        command: AddMessageCommand,
        *,
        idempotency: IdempotencyScope,
        payload_hash: str,
        planner,
    ) -> MutationResult:
        now = datetime.now(UTC)
        async with self._database.pool.connection() as connection:
            async with connection.transaction():
                actor_id, actor = await _actor(
                    connection, command.profile_key, command.actor.telegram_user_id
                )
                _same_role(command.actor, actor)
                replay, lease = await _begin_inline_idempotency(
                    connection, idempotency, payload_hash, actor_id
                )
                if replay:
                    return replay
                ticket, snapshot = await _visible_ticket(
                    connection, command.profile_key, actor_id, actor,
                    command.ticket_id,
                )
                plan: MessagePlan = planner(snapshot)
                message_id = await _insert_message(
                    connection, command.ticket_id, command.profile_key, actor_id,
                    actor.role, plan.visibility, plan.body,
                    f"{idempotency.namespace}:message", now,
                )
                event_id = await _insert_event(
                    connection, command.ticket_id, command.profile_key, actor_id,
                    actor.role,
                    "public_message" if plan.visibility == "public" else "internal_note",
                    None, None, f"{idempotency.namespace}:event", now,
                    {"message_id": message_id}, message_id=message_id,
                )
                await connection.execute(
                    "UPDATE ticketing.tickets SET updated_at=%s,version=version+1 "
                    "WHERE ticket_id=%s", (now, command.ticket_id)
                )
                await _insert_notifications(
                    connection, command.profile_key, command.ticket_id,
                    int(ticket["ticket_number"]), event_id,
                    plan.notifications, now,
                )
                result = MutationResult(
                    command.ticket_id, ticket["status_code"],
                    ticket["ticket_type_code"], int(ticket["review_cycle"]),
                    ticket_number=int(ticket["ticket_number"]),
                )
                await _complete_idempotency(connection, idempotency, lease, result)
                return result

    async def transition(
        self,
        command: TransitionCommand,
        *,
        idempotency: IdempotencyScope,
        payload_hash: str,
        planner,
    ) -> MutationResult:
        now = datetime.now(UTC)
        async with self._database.pool.connection() as connection:
            async with connection.transaction():
                actor_id, actor = await _actor(
                    connection, command.profile_key, command.actor.telegram_user_id
                )
                _same_role(command.actor, actor)
                replay, lease = await _begin_inline_idempotency(
                    connection, idempotency, payload_hash, actor_id
                )
                if replay:
                    return replay
                ticket, snapshot = await _visible_ticket(
                    connection, command.profile_key, actor_id, actor,
                    command.ticket_id,
                )
                plan: TransitionPlan = planner(snapshot)
                message_id = None
                if plan.public_message:
                    message_id = await _insert_message(
                        connection, command.ticket_id, command.profile_key,
                        actor_id, actor.role, "public", plan.public_message,
                        f"{idempotency.namespace}:reason", now,
                    )
                closed_at = now if plan.new_status == "completed" else None
                review_started_at = now if plan.new_status == "review" else None
                accepted_at = (
                    now if plan.event.kind is EventKind.GENERAL_ACCEPTED else None
                )
                await connection.execute(
                    """
                    UPDATE ticketing.tickets SET
                        status_code=%s,review_cycle=%s,review_started_at=%s,
                        first_review_at=CASE WHEN %s='review'
                            THEN coalesce(first_review_at,%s) ELSE first_review_at END,
                        accepted_at=%s,closed_at=%s,
                        updated_at=%s,version=version+1
                    WHERE ticket_id=%s AND profile_key=%s
                    """,
                    (
                        plan.new_status, plan.review_cycle, review_started_at,
                        plan.new_status, now, accepted_at, closed_at, now,
                        command.ticket_id, command.profile_key,
                    ),
                )
                event_id = await _insert_event(
                    connection, command.ticket_id, command.profile_key, actor_id,
                    actor.role, "status_changed", ticket["status_code"],
                    plan.new_status, f"{idempotency.namespace}:event", now,
                    {
                        "workflow_action": plan.event.kind.value,
                        "review_cycle": plan.event.review_cycle,
                        "public_reason": plan.event.public_reason,
                    }, message_id=message_id,
                )
                await _insert_notifications(
                    connection, command.profile_key, command.ticket_id,
                    int(ticket["ticket_number"]), event_id,
                    plan.notifications, now,
                )
                result = MutationResult(
                    command.ticket_id, plan.new_status,
                    ticket["ticket_type_code"], plan.review_cycle,
                    ticket_number=int(ticket["ticket_number"]),
                )
                await _complete_idempotency(connection, idempotency, lease, result)
                return result


async def _actor(connection, profile_key: str, telegram_user_id: int):
    row = await (await connection.execute(
        """SELECT u.user_id,u.telegram_user_id,u.display_name,m.role,m.active
           FROM ticketing.telegram_users u JOIN ticketing.memberships m USING(user_id)
           WHERE u.telegram_user_id=%s AND m.profile_key=%s AND m.active""",
        (telegram_user_id, profile_key),
    )).fetchone()
    if row is None:
        raise AccessDenied("membership inactive")
    return int(row["user_id"]), Actor(
        int(row["telegram_user_id"]), row["display_name"], row["role"], True
    )


def _same_role(request_actor: Actor, database_actor: Actor) -> None:
    if request_actor.role != database_actor.role:
        raise AccessDenied("membership changed")


async def _visible_ticket(connection, profile_key, actor_id, actor, ticket_id):
    params: list[Any] = [ticket_id, profile_key]
    owner_clause = ""
    if actor.is_supervisor:
        owner_clause = """
          AND (
            requester_user_id=%s
            OR EXISTS (
                SELECT 1
                FROM ticketing.supervisor_subordinates ss
                JOIN ticketing.memberships supervisor_membership
                  ON supervisor_membership.profile_key=ss.profile_key
                 AND supervisor_membership.user_id=ss.supervisor_user_id
                JOIN ticketing.memberships subordinate_membership
                  ON subordinate_membership.profile_key=ss.profile_key
                 AND subordinate_membership.user_id=ss.subordinate_user_id
                WHERE ss.profile_key=t.profile_key
                  AND ss.supervisor_user_id=%s
                  AND ss.subordinate_user_id=t.requester_user_id
                  AND ss.active
                  AND supervisor_membership.active
                  AND supervisor_membership.role='supervisor'
                  AND subordinate_membership.active
            )
          )
        """
        params.extend((actor_id, actor_id))
    elif not actor.is_staff:
        owner_clause = " AND requester_user_id=%s"
        params.append(actor_id)
    row = await (await connection.execute(
        "SELECT * FROM ticketing.tickets t WHERE ticket_id=%s AND profile_key=%s"
        + owner_clause + " FOR UPDATE", params,
    )).fetchone()
    if row is None:
        raise ResourceNotFound
    requester = await (await connection.execute(
        "SELECT telegram_user_id FROM ticketing.telegram_users WHERE user_id=%s",
        (row["requester_user_id"],),
    )).fetchone()
    return row, TicketSnapshot(
        ticket_id=int(row["ticket_id"]), profile_key=profile_key,
        ticket_type=row["ticket_type_code"], status=row["status_code"],
        author_telegram_user_id=int(requester["telegram_user_id"]),
        review_cycle=int(row["review_cycle"]), event_sequence=int(row["version"]),
    )


async def _technical_staff(
    connection, profile_key: str, telegram_user_id: int
) -> tuple[int, str]:
    row = await (await connection.execute(
        """SELECT m.user_id,m.role FROM ticketing.memberships m
           JOIN ticketing.telegram_users u USING(user_id)
           WHERE m.profile_key=%s AND u.telegram_user_id=%s AND m.active
             AND m.role IN ('admin','technician')
           LIMIT 1""",
        (profile_key, telegram_user_id),
    )).fetchone()
    if row is None:
        raise AccessDenied("configured technical actor is not active staff")
    return int(row["user_id"]), row["role"]


async def _insert_message(
    connection, ticket_id, profile_key, author_id, author_role,
    visibility, body, key, sent_at,
) -> int:
    row = await (await connection.execute(
        """INSERT INTO ticketing.ticket_messages
             (ticket_id,profile_key,author_user_id,author_role,visibility,body,
              idempotency_key,sent_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING message_id""",
        (ticket_id, profile_key, author_id, author_role, visibility, body, key, sent_at),
    )).fetchone()
    return int(row["message_id"])


async def _insert_event(
    connection, ticket_id, profile_key, actor_id, actor_role, event_type,
    from_status, to_status, key, occurred_at, metadata, *, message_id=None,
) -> int:
    row = await (await connection.execute(
        """INSERT INTO ticketing.ticket_events
             (ticket_id,profile_key,actor_user_id,actor_role,event_type,
              from_status,to_status,message_id,idempotency_key,occurred_at,metadata)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING event_id""",
        (ticket_id, profile_key, actor_id, actor_role, event_type,
         from_status, to_status, message_id, key, occurred_at, Jsonb(metadata)),
    )).fetchone()
    return int(row["event_id"])


async def _insert_notifications(
    connection, profile_key: str, ticket_id: int, ticket_number: int, event_id: int,
    intents: tuple[NotificationIntentDraft, ...], now: datetime,
) -> None:
    for intent in intents:
        if intent.target_telegram_user_id is None:
            recipients = await (await connection.execute(
                """SELECT m.user_id FROM ticketing.memberships m
                   WHERE m.profile_key=%s AND m.active
                     AND m.role IN ('admin','technician')""",
                (profile_key,),
            )).fetchall()
        else:
            recipients = await (await connection.execute(
                """SELECT u.user_id FROM ticketing.telegram_users u
                   JOIN ticketing.memberships m USING(user_id)
                   WHERE u.telegram_user_id=%s AND m.profile_key=%s AND m.active""",
                (intent.target_telegram_user_id, profile_key),
            )).fetchall()
        for recipient in recipients:
            recipient_id = int(recipient["user_id"])
            await connection.execute(
                """INSERT INTO ticketing.outbox
                     (profile_key,ticket_id,event_id,recipient_user_id,
                      notification_type,deduplication_key,payload,available_at,
                      review_cycle,required_status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (profile_key,deduplication_key) DO NOTHING""",
                (
                    profile_key, ticket_id, event_id, recipient_id,
                    intent.kind.value, f"{intent.dedup_key}:{recipient_id}",
                    Jsonb({
                        "ticket_id": ticket_id,
                        "ticket_number": ticket_number,
                        "public_reason": intent.public_reason,
                        "review_cycle": intent.review_cycle,
                    }), intent.due_at or now, intent.review_cycle,
                    intent.deliver_if_status,
                ),
            )


async def _begin_inline_idempotency(
    connection, scope: IdempotencyScope, payload_hash: str, actor_id: int,
) -> tuple[MutationResult | None, str]:
    row = await (await connection.execute(
        "SELECT * FROM ticketing.miniapp_idempotency WHERE namespace=%s FOR UPDATE",
        (scope.namespace,),
    )).fetchone()
    if row:
        if row["payload_hash"] != payload_hash:
            raise IdempotencyConflict("idempotency payload mismatch")
        if row["state"] == "completed":
            payload = row["response"]
            return MutationResult(
                int(payload["ticket_id"]), payload["status"], payload["ticket_type"],
                int(payload["review_cycle"]), True,
                int(payload.get("ticket_number") or payload["ticket_id"]),
            ), ""
    lease = str(UUID(bytes=secrets.token_bytes(16)))
    expires = datetime.now(UTC) + timedelta(seconds=60)
    if row:
        await connection.execute(
            """UPDATE ticketing.miniapp_idempotency SET state='processing',
               lease_token=%s,lease_expires_at=%s,updated_at=now(),last_error_code=NULL
               WHERE namespace=%s""", (UUID(lease), expires, scope.namespace)
        )
    else:
        await connection.execute(
            """INSERT INTO ticketing.miniapp_idempotency
               (namespace,profile_key,actor_user_id,operation,request_uuid,payload_hash,
                state,lease_token,lease_expires_at)
               VALUES (%s,%s,%s,%s,%s,%s,'processing',%s,%s)""",
            (scope.namespace, scope.profile_key, actor_id, scope.operation, scope.key,
             payload_hash, UUID(lease), expires),
        )
    return None, lease


async def _lock_reserved_idempotency(
    connection, scope: IdempotencyScope, payload_hash: str, lease_token: str,
) -> MutationResult | None:
    row = await (await connection.execute(
        "SELECT * FROM ticketing.miniapp_idempotency WHERE namespace=%s FOR UPDATE",
        (scope.namespace,),
    )).fetchone()
    if row is None or row["payload_hash"] != payload_hash:
        raise IdempotencyConflict("missing or conflicting reservation")
    if row["state"] == "completed":
        payload = row["response"]
        return MutationResult(
            int(payload["ticket_id"]), payload["status"], payload["ticket_type"],
            int(payload["review_cycle"]), True,
            int(payload.get("ticket_number") or payload["ticket_id"]),
        )
    if row["state"] != "processing" or row["lease_token"] != UUID(lease_token):
        raise IdempotencyConflict("idempotency lease lost")
    return None


async def _complete_idempotency(
    connection, scope: IdempotencyScope, lease_token: str, result: MutationResult,
) -> None:
    cursor = await connection.execute(
        """UPDATE ticketing.miniapp_idempotency SET state='completed',
           lease_token=NULL,lease_expires_at=NULL,response=%s,completed_at=now(),
           updated_at=now(),last_error_code=NULL
           WHERE namespace=%s AND state='processing' AND lease_token=%s""",
        (Jsonb(mutation_result_json(result)), scope.namespace, UUID(lease_token)),
    )
    if cursor.rowcount != 1:
        raise IdempotencyConflict("idempotency lease lost")

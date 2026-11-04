from __future__ import annotations

import asyncio
import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from uuid import UUID

from dest_miniapp.domain import EventDraft, EventKind, NotificationIntentDraft, NotificationKind, WorkflowAction
from dest_miniapp.canonical import PostgresMiniAppGateway
from dest_miniapp.gateway import ResourceNotFound
from dest_miniapp.models import Actor
from dest_miniapp.outbox import PostgresOutboxWorker
from dest_miniapp.postgres import PostgresDatabase, PostgresIdempotencyReservations, PostgresSessionStore
from dest_miniapp.postgres_uow import PostgresTicketMutationUow
from dest_miniapp.sessions import token_hash
from dest_miniapp.use_cases import (
    CatalogAdvertiser,
    CreateTicketCommand,
    DestinationFormatResult,
    IdempotencyReplay,
    IdempotencyScope,
    MutationContext,
    PreparedTicketAggregate,
    ResolvedAdvertiser,
    TicketWorkflow,
    TransitionCommand,
)


DSN = os.getenv("TEST_DATABASE_URL", "")

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())



class Catalog:
    async def resolve(self, *, profile_key, catalog_key):
        return ResolvedAdvertiser("Advertiser", "suffix=1", "catalog-note")


class Formatter:
    def __init__(self):
        self.calls = 0

    async def format(self, request):
        self.calls += 1
        await asyncio.sleep(0.05)
        return DestinationFormatResult("formatted", "formatter-note")

    def fallback(self, request):
        raise AssertionError("fallback not expected")


class Delivery:
    def __init__(self):
        self.items = []

    async def send(self, **item):
        self.items.append(item)


class FailingDelivery(Delivery):
    async def send(self, **item):
        raise RuntimeError("temporary")


class EmptyCatalog:
    count = 0
    def popular(self, limit=6): return ()
    def search(self, query, limit=8): return ()
    def page(self, page, page_size=8): return (), 1


class EmptyPostbacks:
    def list_tenants(self): return {}
    def get_template(self, key): raise KeyError(key)


@unittest.skipUnless(DSN, "TEST_DATABASE_URL is required")
class PostgresIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        admin_db = PostgresDatabase(DSN, min_size=1, max_size=1)
        await admin_db.open()
        async with admin_db.pool.connection() as connection:
            await connection.execute(
                """TRUNCATE ticketing.outbox,ticketing.ticket_events,
                   ticketing.ticket_messages,ticketing.tickets,
                   ticketing.miniapp_sessions,ticketing.miniapp_idempotency,
                   ticketing.memberships,ticketing.telegram_users RESTART IDENTITY CASCADE"""
            )
            for telegram_id, role, name in (
                (100, "client", "Client"),
                (200, "technician", "Tech"),
            ):
                await connection.execute(
                    "SELECT ticketing.register_member(%s,%s,NULL,%s,%s,now())",
                    ("kalisto", telegram_id, name, role),
                )
        await admin_db.close()
        self.db = PostgresDatabase(
            DSN, min_size=1, max_size=8, role="ticketing_writer"
        )
        await self.db.open()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_concurrent_destination_create_is_single_flight_and_atomic(self):
        reservations = PostgresIdempotencyReservations(
            self.db, lease_seconds=10, poll_seconds=0.01
        )
        formatter = Formatter()
        workflow = TicketWorkflow(
            PostgresTicketMutationUow(self.db), advertisers=Catalog(),
            destination_formatter=formatter,
            idempotency_reservations=reservations,
        )
        command = CreateTicketCommand(
            profile_key="kalisto", actor=Actor(100, "Client", "client"),
            idempotency_key=UUID("11111111-1111-4111-8111-111111111111"),
            ticket_type="destination", body="source",
            advertiser=CatalogAdvertiser("catalog", "key"), cap=25,
        )
        first, second = await asyncio.gather(
            workflow.create_ticket(command), workflow.create_ticket(command)
        )
        self.assertEqual(formatter.calls, 1)
        self.assertEqual(first.ticket_id, second.ticket_id)
        self.assertEqual(sum(item.replayed for item in (first, second)), 1)
        async with self.db.pool.connection() as connection:
            counts = await (await connection.execute(
                """SELECT
                  (SELECT count(*) FROM ticketing.tickets) tickets,
                  (SELECT count(*) FROM ticketing.ticket_messages) messages,
                  (SELECT count(*) FROM ticketing.ticket_events) events,
                  (SELECT count(*) FROM ticketing.outbox) outbox,
                  (SELECT count(*) FROM ticketing.miniapp_idempotency
                   WHERE state='completed') completed"""
            )).fetchone()
        self.assertEqual(tuple(counts.values()), (1, 2, 3, 1, 1))

    async def test_runtime_uses_limited_writer_role(self):
        async with self.db.pool.connection() as connection:
            role = await (await connection.execute(
                "SELECT current_role AS value"
            )).fetchone()
        self.assertEqual(role["value"], "ticketing_writer")

    async def test_persistent_session_rotation_and_membership_recheck(self):
        store = PostgresSessionStore(self.db)
        first = await store.rotate(
            telegram_user_id=100, profile_key="kalisto", ttl_seconds=600
        )
        record = await store.get(first.session_id)
        self.assertEqual(record.telegram_user_id, 100)
        second = await store.rotate(
            telegram_user_id=100, profile_key="kalisto", ttl_seconds=600
        )
        self.assertIsNone(await store.get(first.session_id))
        self.assertIsNotNone(await store.get(second.session_id))

        gateway = PostgresMiniAppGateway(
            self.db, profile_key="kalisto", profile_name="Kalisto",
            advertiser_catalog=EmptyCatalog(), postback_service=EmptyPostbacks(),
        )
        actor = await gateway.resolve_actor(
            profile_key="kalisto", telegram_user_id=100
        )
        self.assertTrue(actor.active)
        async with self.db.pool.connection() as connection:
            await connection.execute(
                "UPDATE ticketing.memberships SET active=false WHERE profile_key=%s",
                ("kalisto",),
            )
        actor = await gateway.resolve_actor(
            profile_key="kalisto", telegram_user_id=100
        )
        self.assertFalse(actor.active)

    async def test_gateway_hides_internal_history_and_foreign_client_ticket(self):
        reservations = PostgresIdempotencyReservations(self.db, lease_seconds=10)
        workflow = TicketWorkflow(
            PostgresTicketMutationUow(self.db), advertisers=Catalog(),
            destination_formatter=Formatter(),
            idempotency_reservations=reservations,
        )
        created = await workflow.create_ticket(CreateTicketCommand(
            profile_key="kalisto", actor=Actor(100, "Client", "client"),
            idempotency_key=UUID("88888888-8888-4888-8888-888888888888"),
            ticket_type="destination", body="source",
            advertiser=CatalogAdvertiser("catalog", "key"), cap=25,
        ))
        async with self.db.pool.connection() as connection:
            await connection.execute(
                "SELECT ticketing.register_member(%s,%s,NULL,%s,%s,now())",
                ("kalisto", 101, "Other client", "client"),
            )
        gateway = PostgresMiniAppGateway(
            self.db, profile_key="kalisto", profile_name="Kalisto",
            advertiser_catalog=EmptyCatalog(), postback_service=EmptyPostbacks(),
        )

        client_view = await gateway.get_ticket_view(
            profile_key="kalisto", actor=Actor(100, "Client", "client"),
            ticket_id=created.ticket_id,
        )
        self.assertFalse(hasattr(client_view, "internal_history"))
        self.assertEqual(len(client_view.conversation), 1)

        staff_view = await gateway.get_ticket_view(
            profile_key="kalisto", actor=Actor(200, "Tech", "technician"),
            ticket_id=created.ticket_id,
        )
        self.assertEqual(len(staff_view.internal_history), 1)
        with self.assertRaises(ResourceNotFound):
            await gateway.get_ticket_view(
                profile_key="kalisto",
                actor=Actor(101, "Other client", "client"),
                ticket_id=created.ticket_id,
            )

    async def test_completed_reservation_survives_ambiguous_fail_and_expired_lease_takes_over(self):
        reservations = PostgresIdempotencyReservations(
            self.db, lease_seconds=10, poll_seconds=0.01
        )
        scope = IdempotencyScope(
            "kalisto", 100, "create_ticket",
            UUID("22222222-2222-4222-8222-222222222222"),
        )
        lease = await asyncio.wait_for(
            reservations.acquire(scope=scope, payload_hash="a" * 64), 5
        )
        uow = PostgresTicketMutationUow(self.db)
        result = await asyncio.wait_for(uow.create_ticket(
            MutationContext("kalisto", Actor(100, "Client", "client")),
            PreparedTicketAggregate("general", "body", None, None),
            idempotency=scope, idempotency_lease=lease.token,
            payload_hash="a" * 64,
            event=EventDraft(EventKind.TICKET_CREATED, 100),
            notification=NotificationIntentDraft(
                NotificationKind.STAFF_TICKET_OPENED, "opened", None
            ),
        ), 5)
        await reservations.fail(scope=scope, lease_token=lease.token)
        replay = await reservations.acquire(scope=scope, payload_hash="a" * 64)
        self.assertIsInstance(replay, IdempotencyReplay)
        self.assertEqual(replay.result.ticket_id, result.ticket_id)

        takeover_scope = IdempotencyScope(
            "kalisto", 100, "create_ticket",
            UUID("33333333-3333-4333-8333-333333333333"),
        )
        old = await reservations.acquire(scope=takeover_scope, payload_hash="b" * 64)
        async with self.db.pool.connection() as connection:
            await connection.execute(
                "UPDATE ticketing.miniapp_idempotency SET lease_expires_at=%s "
                "WHERE namespace=%s",
                (datetime.now(UTC) - timedelta(seconds=1), takeover_scope.namespace),
            )
        new = await reservations.acquire(scope=takeover_scope, payload_hash="b" * 64)
        self.assertNotEqual(old.token, new.token)

    async def test_review_reminder_is_skipped_after_return(self):
        reservations = PostgresIdempotencyReservations(self.db, lease_seconds=10)
        workflow = TicketWorkflow(
            PostgresTicketMutationUow(self.db), advertisers=Catalog(),
            destination_formatter=Formatter(),
            idempotency_reservations=reservations,
        )
        created = await workflow.create_ticket(CreateTicketCommand(
            "kalisto", Actor(100, "Client", "client"),
            UUID("44444444-4444-4444-8444-444444444444"), "general", "body"
        ))
        await workflow.transition(TransitionCommand(
            "kalisto", Actor(200, "Tech", "technician"), created.ticket_id,
            UUID("55555555-5555-4555-8555-555555555555"),
            WorkflowAction.SUBMIT_REVIEW,
        ))
        await workflow.transition(TransitionCommand(
            "kalisto", Actor(100, "Client", "client"), created.ticket_id,
            UUID("66666666-6666-4666-8666-666666666666"),
            WorkflowAction.RETURN, "fix it",
        ))
        async with self.db.pool.connection() as connection:
            await connection.execute(
                "UPDATE ticketing.outbox SET available_at=now()-interval '1 second'"
            )
        delivery = Delivery()
        await PostgresOutboxWorker(
            self.db, delivery, profile_key="kalisto"
        ).run_once(limit=20)
        sent_types = {item["notification_type"] for item in delivery.items}
        self.assertNotIn("author_review_reminder", sent_types)

    async def test_accept_timestamp_is_cleared_and_replaced_after_reopen(self):
        reservations = PostgresIdempotencyReservations(self.db, lease_seconds=10)
        workflow = TicketWorkflow(
            PostgresTicketMutationUow(self.db), advertisers=Catalog(),
            destination_formatter=Formatter(),
            idempotency_reservations=reservations,
        )
        created = await workflow.create_ticket(CreateTicketCommand(
            "kalisto", Actor(100, "Client", "client"),
            UUID("99999999-9999-4999-8999-999999999991"), "general", "body"
        ))
        await workflow.transition(TransitionCommand(
            "kalisto", Actor(200, "Tech", "technician"), created.ticket_id,
            UUID("99999999-9999-4999-8999-999999999992"),
            WorkflowAction.SUBMIT_REVIEW,
        ))
        await workflow.transition(TransitionCommand(
            "kalisto", Actor(100, "Client", "client"), created.ticket_id,
            UUID("99999999-9999-4999-8999-999999999993"),
            WorkflowAction.ACCEPT,
        ))
        first = await self._accepted_at(created.ticket_id)
        self.assertIsNotNone(first)
        await workflow.transition(TransitionCommand(
            "kalisto", Actor(100, "Client", "client"), created.ticket_id,
            UUID("99999999-9999-4999-8999-999999999994"),
            WorkflowAction.REOPEN, "again",
        ))
        self.assertIsNone(await self._accepted_at(created.ticket_id))
        await workflow.transition(TransitionCommand(
            "kalisto", Actor(200, "Tech", "technician"), created.ticket_id,
            UUID("99999999-9999-4999-8999-999999999995"),
            WorkflowAction.SUBMIT_REVIEW,
        ))
        self.assertIsNone(await self._accepted_at(created.ticket_id))
        await workflow.transition(TransitionCommand(
            "kalisto", Actor(100, "Client", "client"), created.ticket_id,
            UUID("99999999-9999-4999-8999-999999999996"),
            WorkflowAction.ACCEPT,
        ))
        second = await self._accepted_at(created.ticket_id)
        self.assertGreater(second, first)

    async def _accepted_at(self, ticket_id):
        async with self.db.pool.connection() as connection:
            row = await (await connection.execute(
                "SELECT accepted_at FROM ticketing.tickets WHERE ticket_id=%s",
                (ticket_id,),
            )).fetchone()
        return row["accepted_at"]

    async def test_outbox_failure_retries_without_duplicate_delivery(self):
        reservations = PostgresIdempotencyReservations(self.db, lease_seconds=10)
        workflow = TicketWorkflow(
            PostgresTicketMutationUow(self.db), advertisers=Catalog(),
            destination_formatter=Formatter(),
            idempotency_reservations=reservations,
        )
        await workflow.create_ticket(CreateTicketCommand(
            "kalisto", Actor(100, "Client", "client"),
            UUID("77777777-7777-4777-8777-777777777777"), "general", "body"
        ))
        await PostgresOutboxWorker(
            self.db, FailingDelivery(), profile_key="kalisto"
        ).run_once()
        async with self.db.pool.connection() as connection:
            failed = await (await connection.execute(
                "SELECT status,attempts FROM ticketing.outbox"
            )).fetchone()
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["attempts"], 1)
            await connection.execute(
                "UPDATE ticketing.outbox SET available_at=now()-interval '1 second'"
            )
        delivery = Delivery()
        await PostgresOutboxWorker(
            self.db, delivery, profile_key="kalisto"
        ).run_once()
        self.assertEqual(len(delivery.items), 1)
        async with self.db.pool.connection() as connection:
            sent = await (await connection.execute(
                "SELECT status,attempts FROM ticketing.outbox"
            )).fetchone()
        self.assertEqual(sent["status"], "sent")
        self.assertEqual(sent["attempts"], 2)


if __name__ == "__main__":
    unittest.main()

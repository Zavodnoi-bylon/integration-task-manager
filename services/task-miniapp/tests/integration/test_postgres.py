from __future__ import annotations

import asyncio
import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from dest_miniapp.domain import EventDraft, EventKind, InvalidTransition, NotificationIntentDraft, NotificationKind, WorkflowAction
from dest_miniapp.canonical import PostgresMiniAppGateway
from dest_miniapp.dto import DraftSaveInput
from dest_miniapp.gateway import AccessDenied, ResourceNotFound, TicketQuery
from dest_miniapp.models import Actor
from dest_miniapp.outbox import PostgresOutboxWorker
from dest_miniapp.postgres import PostgresDatabase, PostgresIdempotencyReservations, PostgresSessionStore
from dest_miniapp.postgres_uow import PostgresTicketMutationUow
from dest_miniapp.sessions import token_hash
from dest_miniapp.use_cases import (
    CatalogAdvertiser,
    CreateTicketCommand,
    DestinationFormatResult,
    DestinationRequestDetails,
    DestinationResult,
    IdempotencyReplay,
    IdempotencyScope,
    MutationContext,
    NewAdvertiser,
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
        assert request.details is not None
        return DestinationFormatResult(
            "formatted",
            "formatter-note",
            results=(DestinationResult(
                request.details.naming,
                request.details.landing_url,
            ),),
        )

    def fallback(self, request):
        raise AssertionError("fallback not expected")


class MephistoFormatter:
    async def format(self, request):
        return DestinationFormatResult(
            "formatted",
            None,
            results=(DestinationResult(
                "Advertiser | Brand | ASO | PL | 140 EUR",
                "https://example.test/destination",
            ),),
        )

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
    def __init__(self):
        self.invalidations = 0
    count = 0
    def popular(self, limit=6): return ()
    def search(self, query, limit=8): return ()
    def page(self, page, page_size=8): return (), 1
    def invalidate(self): self.invalidations += 1


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
                """TRUNCATE ticketing.ticket_draft_intake,ticketing.ticket_drafts,
                   ticketing.aio_advertiser_refresh_requests,
                   ticketing.outbox,ticketing.ticket_events,
                   ticketing.ticket_messages,ticketing.tickets,
                   ticketing.miniapp_sessions,ticketing.miniapp_idempotency,
                   ticketing.profile_ticket_counters,
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

    async def test_advertiser_refresh_request_is_single_profile_state(self):
        catalog = EmptyCatalog()
        gateway = PostgresMiniAppGateway(
            self.db,
            profile_key="kalisto",
            profile_name="Kalisto",
            advertiser_catalog=catalog,
            postback_service=EmptyPostbacks(),
        )
        actor = Actor(100, "Client", "client")
        self.assertEqual(
            await gateway.get_advertiser_refresh_status(
                profile_key="kalisto", actor=actor
            ),
            {"status": "idle"},
        )
        self.assertEqual(
            await gateway.request_advertiser_refresh(
                profile_key="kalisto", actor=actor
            ),
            {"status": "pending"},
        )
        self.assertEqual(
            await gateway.request_advertiser_refresh(
                profile_key="kalisto", actor=actor
            ),
            {"status": "pending"},
        )
        async with self.db.pool.connection() as connection:
            count = await (
                await connection.execute(
                    """
                    SELECT count(*) AS value
                    FROM ticketing.aio_advertiser_refresh_requests
                    WHERE profile_key='kalisto'
                    """
                )
            ).fetchone()
            await connection.execute(
                """
                UPDATE ticketing.aio_advertiser_refresh_requests
                SET started_at=now()
                WHERE profile_key='kalisto'
                """
            )
        self.assertEqual(count["value"], 1)
        self.assertEqual(
            await gateway.get_advertiser_refresh_status(
                profile_key="kalisto", actor=actor
            ),
            {"status": "running"},
        )
        async with self.db.pool.connection() as connection:
            await connection.execute(
                """
                UPDATE ticketing.aio_advertiser_refresh_requests
                SET completed_at=now(),outcome='succeeded'
                WHERE profile_key='kalisto'
                """
            )
        self.assertEqual(
            await gateway.get_advertiser_refresh_status(
                profile_key="kalisto", actor=actor
            ),
            {"status": "succeeded"},
        )
        self.assertEqual(catalog.invalidations, 1)
        self.assertEqual(
            await gateway.request_advertiser_refresh(
                profile_key="kalisto", actor=actor
            ),
            {"status": "succeeded"},
        )

    async def test_kalisto_new_advertiser_is_persisted_as_passthrough(self):
        formatter = Formatter()
        workflow = TicketWorkflow(
            PostgresTicketMutationUow(
                self.db, technical_actor_telegram_id=200
            ),
            advertisers=Catalog(),
            destination_formatter=formatter,
            idempotency_reservations=PostgresIdempotencyReservations(self.db),
        )
        naming = "New Brand | FB | BR | Slots | 20 USD"
        landing_url = "https://new.example/?click={clickid}&sub={web}"
        created = await workflow.create_ticket(CreateTicketCommand(
            profile_key="kalisto",
            actor=Actor(100, "Client", "client"),
            idempotency_key=UUID("14141414-1414-4414-8414-141414141414"),
            ticket_type="destination",
            body="",
            advertiser=NewAdvertiser("new", "New Brand"),
            cap=20,
            cap_mode="daily",
            details=DestinationRequestDetails(
                naming=naming,
                geo="BR",
                rate=Decimal("20"),
                currency="USD",
                landing_url=landing_url,
            ),
        ))
        self.assertEqual(formatter.calls, 0)
        async with self.db.pool.connection() as connection:
            row = await (
                await connection.execute(
                    """
                    SELECT t.new_advertiser,r.advertiser_key,r.processing_status,
                           result.naming,result.destination_url
                    FROM ticketing.tickets t
                    JOIN ticketing.destination_requests r
                      ON r.ticket_id=t.ticket_id AND r.profile_key=t.profile_key
                    JOIN ticketing.destination_results result
                      ON result.ticket_id=t.ticket_id
                     AND result.profile_key=t.profile_key
                    WHERE t.ticket_id=%s
                    """,
                    (created.ticket_id,),
                )
            ).fetchone()
        self.assertTrue(row["new_advertiser"])
        self.assertEqual(row["advertiser_key"], "__new__")
        self.assertEqual(row["processing_status"], "passthrough")
        self.assertEqual(row["naming"], naming)
        self.assertEqual(row["destination_url"], landing_url)

    async def test_kalisto_important_comment_is_persisted_and_reusable(self):
        workflow = TicketWorkflow(
            PostgresTicketMutationUow(
                self.db, technical_actor_telegram_id=200
            ),
            advertisers=Catalog(),
            destination_formatter=Formatter(),
            idempotency_reservations=PostgresIdempotencyReservations(self.db),
        )
        comment = "Сделать тест реги"
        created = await workflow.create_ticket(CreateTicketCommand(
            profile_key="kalisto",
            actor=Actor(100, "Client", "client"),
            idempotency_key=UUID("15151515-1515-4515-8515-151515151515"),
            ticket_type="destination",
            body="",
            advertiser=CatalogAdvertiser("catalog", "key"),
            cap=20,
            cap_mode="daily",
            details=DestinationRequestDetails(
                naming="Advertiser | FB | BR | Slots | 20 USD",
                geo="BR",
                rate=Decimal("20"),
                currency="USD",
                landing_url="https://example.test/offer",
            ),
            important_comment=comment,
        ))
        gateway = PostgresMiniAppGateway(
            self.db, profile_key="kalisto", profile_name="Kalisto",
            advertiser_catalog=EmptyCatalog(), postback_service=EmptyPostbacks(),
        )
        comments = await gateway.list_destination_comments(
            profile_key="kalisto", actor=Actor(100, "Client", "client")
        )
        detail = await gateway.get_ticket_view(
            profile_key="kalisto",
            actor=Actor(100, "Client", "client"),
            ticket_id=created.ticket_id,
        )
        self.assertEqual(comments, [comment])
        self.assertEqual(detail.important_comment, comment)

    async def test_concurrent_destination_create_is_single_flight_and_atomic(self):
        async with self.db.pool.connection() as connection:
            await connection.execute(
                "SELECT ticketing.register_member(%s,%s,NULL,%s,%s,now())",
                ("kalisto", 150, "Earlier admin", "admin"),
            )
        reservations = PostgresIdempotencyReservations(
            self.db, lease_seconds=10, poll_seconds=0.01
        )
        formatter = Formatter()
        workflow = TicketWorkflow(
            PostgresTicketMutationUow(
                self.db, technical_actor_telegram_id=200
            ), advertisers=Catalog(),
            destination_formatter=formatter,
            idempotency_reservations=reservations,
        )
        command = CreateTicketCommand(
            profile_key="kalisto", actor=Actor(100, "Client", "client"),
            idempotency_key=UUID("11111111-1111-4111-8111-111111111111"),
            ticket_type="destination", body="source",
            advertiser=CatalogAdvertiser("catalog", "key"), cap=25,
            cap_mode="daily",
            details=DestinationRequestDetails(
                naming="Advertiser | Test | BR | Slots | 10$",
                geo="BR",
                rate=Decimal("10"),
                currency="usd",
                landing_url="https://example.test/?click={clickid}",
            ),
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
            technical_note = await (await connection.execute(
                """SELECT u.telegram_user_id,m.body
                   FROM ticketing.ticket_messages m
                   JOIN ticketing.telegram_users u ON u.user_id=m.author_user_id
                   WHERE m.visibility='internal'"""
            )).fetchone()
        self.assertEqual(tuple(counts.values()), (1, 2, 3, 2, 1))
        self.assertEqual(technical_note["telegram_user_id"], 200)
        self.assertTrue(technical_note["body"].startswith("Техническая пометка:"))

    async def test_runtime_uses_limited_writer_role(self):
        async with self.db.pool.connection() as connection:
            role = await (await connection.execute(
                "SELECT current_role AS value"
            )).fetchone()
        self.assertEqual(role["value"], "ticketing_writer")

    async def test_ticket_numbers_increment_independently_per_profile(self):
        async with self.db.pool.connection() as connection:
            for telegram_id, role, name in (
                (100, "client", "Client"),
                (200, "technician", "Tech"),
            ):
                await connection.execute(
                    "SELECT ticketing.register_member(%s,%s,NULL,%s,%s,now())",
                    ("mephisto", telegram_id, name, role),
                )
        workflow = TicketWorkflow(
            PostgresTicketMutationUow(
                self.db, technical_actor_telegram_id=200
            ),
            advertisers=Catalog(),
            destination_formatter=Formatter(),
            idempotency_reservations=PostgresIdempotencyReservations(
                self.db, lease_seconds=10
            ),
        )
        first_kalisto = await workflow.create_ticket(CreateTicketCommand(
            "kalisto", Actor(100, "Client", "client"),
            UUID("00000000-0000-4000-8000-000000000001"), "general", "one",
        ))
        first_mephisto = await workflow.create_ticket(CreateTicketCommand(
            "mephisto", Actor(100, "Client", "client"),
            UUID("00000000-0000-4000-8000-000000000002"), "general", "two",
        ))
        second_kalisto = await workflow.create_ticket(CreateTicketCommand(
            "kalisto", Actor(100, "Client", "client"),
            UUID("00000000-0000-4000-8000-000000000003"), "general", "three",
        ))

        self.assertEqual(first_kalisto.ticket_number, 1)
        self.assertEqual(first_mephisto.ticket_number, 1)
        self.assertEqual(second_kalisto.ticket_number, 2)
        self.assertEqual(
            len({
                first_kalisto.ticket_id,
                first_mephisto.ticket_id,
                second_kalisto.ticket_id,
            }),
            3,
        )

    async def test_mephisto_comment_history_is_private_and_deduplicated(self):
        async with self.db.pool.connection() as connection:
            for telegram_id, role, name in (
                (100, "client", "Client"),
                (101, "client", "Other client"),
                (200, "technician", "Tech"),
            ):
                await connection.execute(
                    "SELECT ticketing.register_member(%s,%s,NULL,%s,%s,now())",
                    ("mephisto", telegram_id, name, role),
                )
        workflow = TicketWorkflow(
            PostgresTicketMutationUow(
                self.db, technical_actor_telegram_id=200
            ),
            advertisers=Catalog(),
            destination_formatter=MephistoFormatter(),
            idempotency_reservations=PostgresIdempotencyReservations(
                self.db, lease_seconds=10
            ),
        )
        first = await workflow.create_ticket(CreateTicketCommand(
            profile_key="mephisto",
            actor=Actor(100, "Client", "client"),
            idempotency_key=UUID("00000000-0000-4000-8000-000000000011"),
            ticket_type="destination",
            body="offer one",
            advertiser=CatalogAdvertiser("catalog", "key"),
            cap=20,
            cap_mode="lifetime",
            important_comment="Share only with Dasha",
        ))
        second = await workflow.create_ticket(CreateTicketCommand(
            profile_key="mephisto",
            actor=Actor(100, "Client", "client"),
            idempotency_key=UUID("00000000-0000-4000-8000-000000000012"),
            ticket_type="destination",
            body="offer two",
            advertiser=CatalogAdvertiser("catalog", "key"),
            cap=20,
            cap_mode="lifetime",
            important_comment="share   ONLY with Dasha",
        ))
        await workflow.create_ticket(CreateTicketCommand(
            profile_key="mephisto",
            actor=Actor(101, "Other client", "client"),
            idempotency_key=UUID("00000000-0000-4000-8000-000000000013"),
            ticket_type="destination",
            body="foreign offer",
            advertiser=CatalogAdvertiser("catalog", "key"),
            cap=20,
            cap_mode="lifetime",
            important_comment="Private comment of another author",
        ))
        gateway = PostgresMiniAppGateway(
            self.db, profile_key="mephisto", profile_name="Mephisto",
            advertiser_catalog=EmptyCatalog(), postback_service=EmptyPostbacks(),
        )
        comments = await gateway.list_destination_comments(
            profile_key="mephisto", actor=Actor(100, "Client", "client")
        )
        self.assertEqual(comments, ["share   ONLY with Dasha"])
        detail = await gateway.get_ticket_view(
            profile_key="mephisto",
            actor=Actor(100, "Client", "client"),
            ticket_id=second.ticket_id,
        )
        self.assertEqual(detail.important_comment, "share   ONLY with Dasha")
        self.assertNotEqual(first.ticket_id, second.ticket_id)

    async def test_forwarded_draft_is_private_to_author_and_reporting_reader(self):
        async with self.db.pool.connection() as connection:
            await connection.execute(
                "SELECT ticketing.save_draft_intake(%s,%s,%s,%s,%s,now())",
                ("kalisto", 100, 500, 10, "Client note"),
            )
            row = await (
                await connection.execute(
                    "SELECT ticketing.create_forwarded_draft("
                    "%s,%s,%s,%s,%s,%s,%s,now(),now()) AS draft_id",
                    (
                        "kalisto", 100, 500, 11, "Forwarded offer",
                        "user", "Advertiser",
                    ),
                )
            ).fetchone()
            reader_access = await (
                await connection.execute(
                    "SELECT COALESCE((SELECT has_table_privilege("
                    "role_account.oid,'ticketing.ticket_drafts','SELECT') "
                    "FROM pg_roles role_account "
                    "WHERE role_account.rolname='activity_reader'),false) AS allowed"
                )
            ).fetchone()

        gateway = PostgresMiniAppGateway(
            self.db, profile_key="kalisto", profile_name="Kalisto",
            advertiser_catalog=EmptyCatalog(), postback_service=EmptyPostbacks(),
        )
        author = Actor(100, "Client", "client")
        foreign = Actor(101, "Other client", "client")
        drafts = await gateway.list_drafts(profile_key="kalisto", actor=author)
        self.assertEqual([item.draft_id for item in drafts], [row["draft_id"]])
        self.assertFalse(reader_access["allowed"])
        with self.assertRaises(ResourceNotFound):
            await gateway.get_draft(
                profile_key="kalisto", actor=foreign, draft_id=row["draft_id"]
            )

        saved = await gateway.save_draft(
            profile_key="kalisto",
            actor=author,
            draft_id=row["draft_id"],
            payload=DraftSaveInput(
                advertiser={"kind": "catalog", "key": "adv-1"},
                cap=25,
                body="Edited offer",
                client_comment="Edited note",
            ),
        )
        self.assertEqual(saved.edited_body, "Edited offer")
        self.assertEqual(saved.cap, 25)

        unlimited = await gateway.save_draft(
            profile_key="kalisto",
            actor=author,
            draft_id=row["draft_id"],
            payload=DraftSaveInput(
                advertiser={"kind": "catalog", "key": "adv-1"},
                cap="uncap",
                body="Edited offer",
                client_comment="Edited note",
            ),
        )
        self.assertEqual(unlimited.cap, "uncap")
        async with self.db.pool.connection() as connection:
            persisted = await (
                await connection.execute(
                    "SELECT cap,cap_unlimited FROM ticketing.ticket_drafts "
                    "WHERE draft_id=%s",
                    (row["draft_id"],),
                )
            ).fetchone()
        self.assertIsNone(persisted["cap"])
        self.assertTrue(persisted["cap_unlimited"])

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
            PostgresTicketMutationUow(
                self.db, technical_actor_telegram_id=200
            ), advertisers=Catalog(),
            destination_formatter=Formatter(),
            idempotency_reservations=reservations,
        )
        created = await workflow.create_ticket(CreateTicketCommand(
            profile_key="kalisto", actor=Actor(100, "Client", "client"),
            idempotency_key=UUID("88888888-8888-4888-8888-888888888888"),
            ticket_type="destination", body="source",
            advertiser=CatalogAdvertiser("catalog", "key"), cap=25,
            cap_mode="daily",
            details=DestinationRequestDetails(
                naming="Advertiser | Test | BR | Slots | 10$",
                geo="BR",
                rate=Decimal("10"),
                currency="usd",
                landing_url="https://example.test/?click={clickid}",
            ),
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

    async def test_buying_flag_is_current_state_without_audit(self):
        admin_db = PostgresDatabase(DSN, min_size=1, max_size=1)
        await admin_db.open()
        async with admin_db.pool.connection() as connection:
            await connection.execute(
                "SELECT ticketing.register_member(%s,%s,NULL,%s,%s,now())",
                ("kalisto", 300, "Supervisor", "supervisor"),
            )
            await connection.execute(
                """
                INSERT INTO ticketing.supervisor_subordinates (
                    profile_key,supervisor_user_id,subordinate_user_id
                )
                SELECT 'kalisto',supervisor.user_id,subordinate.user_id
                FROM ticketing.telegram_users supervisor
                CROSS JOIN ticketing.telegram_users subordinate
                WHERE supervisor.telegram_user_id=300
                  AND subordinate.telegram_user_id=100
                """
            )
        await admin_db.close()
        workflow = TicketWorkflow(
            PostgresTicketMutationUow(
                self.db, technical_actor_telegram_id=200
            ),
            advertisers=Catalog(),
            destination_formatter=Formatter(),
            idempotency_reservations=PostgresIdempotencyReservations(
                self.db, lease_seconds=10
            ),
        )
        created = await workflow.create_ticket(CreateTicketCommand(
            profile_key="kalisto",
            actor=Actor(100, "Client", "client"),
            idempotency_key=UUID("12121212-1212-4212-8212-121212121212"),
            ticket_type="destination",
            body="source",
            advertiser=CatalogAdvertiser("catalog", "key"),
            cap=25,
            cap_mode="daily",
            details=DestinationRequestDetails(
                naming="Advertiser | Test | BR | Slots | 10$",
                geo="BR",
                rate=Decimal("10"),
                currency="usd",
                landing_url="https://example.test/?click={clickid}",
            ),
        ))
        gateway = PostgresMiniAppGateway(
            self.db,
            profile_key="kalisto",
            profile_name="Kalisto",
            advertiser_catalog=EmptyCatalog(),
            postback_service=EmptyPostbacks(),
        )
        supervisor = Actor(300, "Supervisor", "supervisor")
        with self.assertRaises(InvalidTransition):
            await gateway.set_buying_flag(
                profile_key="kalisto",
                actor=supervisor,
                ticket_id=created.ticket_id,
                issued=True,
            )
        await workflow.transition(TransitionCommand(
            "kalisto",
            Actor(200, "Tech", "technician"),
            created.ticket_id,
            UUID("13131313-1313-4313-8313-131313131313"),
            WorkflowAction.COMPLETE_DESTINATION,
        ))
        async with self.db.pool.connection() as connection:
            before = await (await connection.execute(
                """
                SELECT
                  (SELECT count(*) FROM ticketing.ticket_events
                   WHERE ticket_id=%s) AS events,
                  (SELECT count(*) FROM ticketing.ticket_messages
                   WHERE ticket_id=%s) AS messages
                """,
                (created.ticket_id, created.ticket_id),
            )).fetchone()
            columns = await (await connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='ticketing'
                  AND table_name='ticket_buying_flags'
                ORDER BY ordinal_position
                """
            )).fetchall()
        self.assertEqual(
            [row["column_name"] for row in columns],
            ["profile_key", "ticket_id"],
        )
        self.assertTrue(await gateway.set_buying_flag(
            profile_key="kalisto",
            actor=supervisor,
            ticket_id=created.ticket_id,
            issued=True,
        ))
        detail = await gateway.get_ticket_view(
            profile_key="kalisto",
            actor=supervisor,
            ticket_id=created.ticket_id,
        )
        self.assertTrue(detail.issued_to_buying)
        staff_detail = await gateway.get_ticket_view(
            profile_key="kalisto",
            actor=Actor(200, "Tech", "technician"),
            ticket_id=created.ticket_id,
        )
        self.assertFalse(staff_detail.issued_to_buying)
        issued_page = await gateway.list_tickets(
            profile_key="kalisto",
            actor=supervisor,
            query=TicketQuery(
                scope="team",
                status=None,
                issued_to_buying=True,
                page=1,
                page_size=20,
            ),
        )
        self.assertEqual(
            [item.ticket_id for item in issued_page.items],
            [created.ticket_id],
        )
        async with self.db.pool.connection() as connection:
            after = await (await connection.execute(
                """
                SELECT
                  (SELECT count(*) FROM ticketing.ticket_events
                   WHERE ticket_id=%s) AS events,
                  (SELECT count(*) FROM ticketing.ticket_messages
                   WHERE ticket_id=%s) AS messages
                """,
                (created.ticket_id, created.ticket_id),
            )).fetchone()
        self.assertEqual(after, before)
        await workflow.transition(TransitionCommand(
            "kalisto",
            Actor(100, "Client", "client"),
            created.ticket_id,
            UUID("14141414-1414-4414-8414-141414141414"),
            WorkflowAction.REOPEN,
            "Нужна доработка",
        ))
        reopened = await gateway.get_ticket_view(
            profile_key="kalisto",
            actor=supervisor,
            ticket_id=created.ticket_id,
        )
        self.assertTrue(reopened.issued_to_buying)
        self.assertFalse(await gateway.set_buying_flag(
            profile_key="kalisto",
            actor=supervisor,
            ticket_id=created.ticket_id,
            issued=False,
        ))
        with self.assertRaises(AccessDenied):
            await gateway.set_buying_flag(
                profile_key="kalisto",
                actor=Actor(200, "Tech", "technician"),
                ticket_id=created.ticket_id,
                issued=True,
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
        uow = PostgresTicketMutationUow(
            self.db, technical_actor_telegram_id=200
        )
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
            PostgresTicketMutationUow(
                self.db, technical_actor_telegram_id=200
            ), advertisers=Catalog(),
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
            PostgresTicketMutationUow(
                self.db, technical_actor_telegram_id=200
            ), advertisers=Catalog(),
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
            PostgresTicketMutationUow(
                self.db, technical_actor_telegram_id=200
            ), advertisers=Catalog(),
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

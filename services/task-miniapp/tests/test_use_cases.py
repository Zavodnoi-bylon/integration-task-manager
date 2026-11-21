from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from dest_miniapp.domain import TicketSnapshot, WorkflowAction
from dest_miniapp.models import Actor
from dest_miniapp.use_cases import (
    AddMessageCommand,
    CatalogAdvertiser,
    CreateTicketCommand,
    DestinationFormatResult,
    DestinationFormattingError,
    DestinationRequestDetails,
    DestinationResult,
    IdempotencyConflict,
    IdempotencyLease,
    IdempotencyReplay,
    MutationResult,
    NewAdvertiser,
    ResolvedAdvertiser,
    TicketWorkflow,
    TransitionCommand,
)


class RecordingUnitOfWork:
    def __init__(self, snapshot: TicketSnapshot) -> None:
        self.snapshot = snapshot
        self.records: dict[str, tuple[str, MutationResult]] = {}
        self.events = []
        self.messages = []
        self.notifications = []
        self.aggregates = []
        self.reservations = None

    async def create_ticket(
        self, context, aggregate, *, idempotency, idempotency_lease,
        payload_hash, event, notification
    ):
        key = idempotency.namespace
        stored = self.records.get(key)
        if stored:
            stored_hash, result = stored
            if stored_hash != payload_hash:
                raise IdempotencyConflict
            return replace(result, replayed=True)
        result = MutationResult(100, "open", aggregate.ticket_type, 0)
        self.aggregates.append(aggregate)
        self.events.append(event)
        self.notifications.append(notification)
        self.records[key] = (payload_hash, result)
        await self.reservations.complete(
            scope=idempotency, lease_token=idempotency_lease, result=result
        )
        return result

    async def add_message(
        self, command, *, idempotency, payload_hash, planner
    ):
        key = idempotency.namespace
        stored = self.records.get(key)
        if stored:
            stored_hash, result = stored
            if stored_hash != payload_hash:
                raise IdempotencyConflict
            return replace(result, replayed=True)
        plan = planner(self.snapshot)
        self.messages.append(plan)
        self.notifications.extend(plan.notifications)
        self.snapshot = replace(
            self.snapshot, event_sequence=self.snapshot.event_sequence + 1
        )
        result = MutationResult(
            self.snapshot.ticket_id,
            self.snapshot.status,
            self.snapshot.ticket_type,
            self.snapshot.review_cycle,
        )
        self.records[key] = (payload_hash, result)
        return result

    async def transition(
        self, command, *, idempotency, payload_hash, planner
    ):
        key = idempotency.namespace
        stored = self.records.get(key)
        if stored:
            stored_hash, result = stored
            if stored_hash != payload_hash:
                raise IdempotencyConflict
            return replace(result, replayed=True)
        plan = planner(self.snapshot)
        self.events.append(plan.event)
        if plan.public_message:
            self.messages.append(plan.public_message)
        self.notifications.extend(plan.notifications)
        self.snapshot = replace(
            self.snapshot,
            status=plan.new_status,
            review_cycle=plan.review_cycle,
            event_sequence=self.snapshot.event_sequence + 1,
        )
        result = MutationResult(
            self.snapshot.ticket_id,
            self.snapshot.status,
            self.snapshot.ticket_type,
            self.snapshot.review_cycle,
        )
        self.records[key] = (payload_hash, result)
        return result


class StubAdvertisers:
    def __init__(self) -> None:
        self.keys = []

    async def resolve(self, *, profile_key, catalog_key):
        self.keys.append((profile_key, catalog_key))
        return ResolvedAdvertiser(
            name="Resolved advertiser",
            suffix="catalog-suffix",
            technician_note="catalog-note",
        )


class StubFormatter:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.requests = []

    async def format(self, request):
        self.requests.append(request)
        await asyncio.sleep(0)
        if self.fail:
            raise DestinationFormattingError
        return DestinationFormatResult(
            formatted_ticket="model-formatted",
            internal_note="model-internal",
            results=(DestinationResult(
                "Resolved advertiser | Brand | FB | BR | Slots | 100 USD | cap 25",
                "https://example.test/?offer=1",
            ),),
        )

    def fallback(self, request):
        return DestinationFormatResult(
            formatted_ticket=f"fallback:{request.advertiser_name}:{request.cap}",
            internal_note=request.technician_note,
            used_fallback=True,
        )


class RecordingReservations:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._entries = {}
        self._sequence = 0

    async def acquire(self, *, scope, payload_hash):
        while True:
            async with self._lock:
                entry = self._entries.get(scope.namespace)
                if entry is None:
                    self._sequence += 1
                    token = f"lease-{self._sequence}"
                    future = asyncio.get_running_loop().create_future()
                    self._entries[scope.namespace] = {
                        "hash": payload_hash,
                        "token": token,
                        "future": future,
                        "result": None,
                    }
                    return IdempotencyLease(token)
                if entry["hash"] != payload_hash:
                    raise IdempotencyConflict
                if entry["result"] is not None:
                    return IdempotencyReplay(entry["result"])
                future = entry["future"]
            await asyncio.shield(future)

    async def complete(self, *, scope, lease_token, result):
        async with self._lock:
            entry = self._entries[scope.namespace]
            self.assert_token(entry, lease_token)
            entry["result"] = result
            if not entry["future"].done():
                entry["future"].set_result(True)

    async def fail(self, *, scope, lease_token):
        async with self._lock:
            entry = self._entries.get(scope.namespace)
            if entry is None or entry["token"] != lease_token:
                return
            self._entries.pop(scope.namespace)
            if not entry["future"].done():
                entry["future"].set_result(False)

    @staticmethod
    def assert_token(entry, lease_token):
        if entry["token"] != lease_token:
            raise AssertionError("wrong idempotency lease")

def make_workflow(
    uow, *, advertisers=None, formatter=None, reservations=None, clock=None
):
    reservations = reservations or RecordingReservations()
    uow.reservations = reservations
    return TicketWorkflow(
        uow,
        advertisers=advertisers or StubAdvertisers(),
        destination_formatter=formatter or StubFormatter(),
        idempotency_reservations=reservations,
        clock=clock,
    )


class UseCaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_ticket_is_idempotent_and_plans_staff_notification(self) -> None:
        uow = RecordingUnitOfWork(
            TicketSnapshot(7, "kalisto", "general", "open", 10)
        )
        workflow = make_workflow(uow)
        command = CreateTicketCommand(
            profile_key="kalisto",
            actor=Actor(10, "Author", "client"),
            idempotency_key=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            ticket_type="general",
            body="Need help",
        )
        first = await workflow.create_ticket(command)
        second = await workflow.create_ticket(command)
        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(len(uow.notifications), 1)
        self.assertEqual(len(uow.events), 1)
        self.assertIsNone(uow.aggregates[0].source_public_message)

    async def test_catalog_destination_is_resolved_and_formatted_before_uow(self) -> None:
        uow = RecordingUnitOfWork(
            TicketSnapshot(7, "mephisto", "destination", "open", 10)
        )
        advertisers = StubAdvertisers()
        formatter = StubFormatter()
        workflow = make_workflow(
            uow, advertisers=advertisers, formatter=formatter
        )
        command = CreateTicketCommand(
            profile_key="mephisto",
            actor=Actor(10, "Author", "client"),
            idempotency_key=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
            ticket_type="destination",
            body="Original client request",
            advertiser=CatalogAdvertiser("catalog", "catalog-key"),
            cap=25,
            cap_mode="daily",
        )
        await workflow.create_ticket(command)
        self.assertEqual(advertisers.keys, [("mephisto", "catalog-key")])
        self.assertEqual(formatter.requests[0].suffix, "catalog-suffix")
        aggregate = uow.aggregates[0]
        self.assertEqual(aggregate.formatted_ticket, "model-formatted")
        self.assertEqual(
            aggregate.source_public_message,
            "Advertiser: Resolved advertiser\nCap: 25 daily\n\nOriginal client request",
        )
        self.assertEqual(aggregate.internal_note, "catalog-note\n\nmodel-internal")
        self.assertFalse(aggregate.used_fallback)

    async def test_mephisto_important_comment_is_not_sent_to_formatter(self) -> None:
        uow = RecordingUnitOfWork(
            TicketSnapshot(7, "mephisto", "destination", "open", 10)
        )
        formatter = StubFormatter()
        workflow = make_workflow(uow, formatter=formatter)
        await workflow.create_ticket(CreateTicketCommand(
            profile_key="mephisto",
            actor=Actor(10, "Author", "client"),
            idempotency_key=UUID("cececece-cece-4ece-8ece-cececececece"),
            ticket_type="destination",
            body="Original offer body",
            advertiser=CatalogAdvertiser("catalog", "catalog-key"),
            cap=20,
            cap_mode="lifetime",
            important_comment="Share only with Dasha",
        ))
        aggregate = uow.aggregates[0]
        self.assertEqual(formatter.requests[0].source_body, "Original offer body")
        self.assertNotIn("Share only with Dasha", aggregate.source_public_message)
        self.assertEqual(aggregate.important_comment, "Share only with Dasha")

    async def test_new_advertiser_does_not_use_catalog(self) -> None:
        uow = RecordingUnitOfWork(
            TicketSnapshot(7, "mephisto", "destination", "open", 10)
        )
        advertisers = StubAdvertisers()
        formatter = StubFormatter()
        workflow = make_workflow(
            uow, advertisers=advertisers, formatter=formatter
        )
        await workflow.create_ticket(
            CreateTicketCommand(
                profile_key="mephisto",
                actor=Actor(10, "Author", "client"),
                idempotency_key=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
                ticket_type="destination",
                body="Original",
                advertiser=NewAdvertiser("new", "New advertiser"),
                cap=30,
                cap_mode="monthly",
            )
        )
        self.assertEqual(advertisers.keys, [])
        self.assertEqual(formatter.requests[0].advertiser_name, "New advertiser")
        self.assertIsNone(formatter.requests[0].suffix)
        self.assertEqual(
            uow.aggregates[0].source_public_message,
            "Advertiser: New advertiser\nCap: 30 monthly\n\nOriginal",
        )
        self.assertEqual(
            uow.aggregates[0].internal_note,
            "model-internal",
        )
        self.assertTrue(uow.aggregates[0].new_advertiser)

    async def test_kalisto_new_advertiser_bypasses_formatter(self) -> None:
        uow = RecordingUnitOfWork(
            TicketSnapshot(7, "kalisto", "destination", "open", 10)
        )
        formatter = StubFormatter()
        workflow = make_workflow(uow, formatter=formatter)
        details = DestinationRequestDetails(
            naming="New Brand | FB | BR | Slots | 20 USD",
            offer_name="New Brand",
            platform="FB",
            approach="Slots",
            geo="BR",
            rate=Decimal("20"),
            currency="USD",
            landing_url=(
                "https://new.example/offer?click={clickid}&sub={web}"
            ),
        )
        await workflow.create_ticket(CreateTicketCommand(
            profile_key="kalisto",
            actor=Actor(10, "Author", "client"),
            idempotency_key=UUID("dededede-dede-4ede-8ede-dededededede"),
            ticket_type="destination",
            body="",
            advertiser=NewAdvertiser("new", "New Brand"),
            cap=30,
            cap_mode="daily",
            details=details,
        ))

        self.assertEqual(formatter.requests, [])
        aggregate = uow.aggregates[0]
        self.assertTrue(aggregate.new_advertiser)
        self.assertEqual(aggregate.destination_results[0].naming, details.naming)
        self.assertEqual(aggregate.destination_results[0].url, details.landing_url)
        self.assertEqual(
            aggregate.destination_request.processing_status,
            "passthrough",
        )
        self.assertEqual(aggregate.destination_request.advertiser_key, "__new__")
        self.assertIsNone(aggregate.internal_note)

    async def test_kalisto_persists_complete_structured_request(self) -> None:
        uow = RecordingUnitOfWork(
            TicketSnapshot(7, "kalisto", "destination", "open", 10)
        )
        formatter = StubFormatter()
        workflow = make_workflow(uow, formatter=formatter)
        naming = (
            "Resolved advertiser | Brand | FB | BR | Slots | 100 USD | cap 25"
        )
        details = DestinationRequestDetails(
            naming=naming,
            offer_name="Brand",
            platform="FB",
            approach="Slots",
            geo="BR",
            rate=Decimal("100.00"),
            currency="USD",
            landing_url="https://example.test/?offer=1",
            clickid_subid="click_id from advertiser",
            additional="KPI: none",
        )

        await workflow.create_ticket(CreateTicketCommand(
            profile_key="kalisto",
            actor=Actor(10, "Author", "client"),
            idempotency_key=UUID("cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd"),
            ticket_type="destination",
            body="Forwarded source evidence",
            advertiser=CatalogAdvertiser("catalog", "catalog-key"),
            cap=25,
            cap_mode="lifetime",
            details=details,
            important_comment="Сделать тест реги",
        ))

        aggregate = uow.aggregates[0]
        self.assertEqual(aggregate.title, naming)
        self.assertEqual(aggregate.destination_request.details, details)
        self.assertEqual(aggregate.destination_request.processing_status, "validated")
        self.assertEqual(aggregate.destination_results[0].naming, naming)
        self.assertEqual(aggregate.important_comment, "Сделать тест реги")
        self.assertNotIn("Сделать тест реги", formatter.requests[0].source_body)
        self.assertNotIn("Сделать тест реги", aggregate.source_public_message)
        self.assertIn("Landing URL: https://example.test/?offer=1", aggregate.source_public_message)
        self.assertIn("Original source:\nForwarded source evidence", aggregate.source_public_message)

    async def test_destination_accepts_uncap_without_coercing_it(self) -> None:
        uow = RecordingUnitOfWork(
            TicketSnapshot(7, "mephisto", "destination", "open", 10)
        )
        formatter = StubFormatter()
        workflow = make_workflow(uow, formatter=formatter)
        await workflow.create_ticket(
            CreateTicketCommand(
                profile_key="mephisto",
                actor=Actor(10, "Author", "client"),
                idempotency_key=UUID("dcdcdcdc-dcdc-4dcd-8dcd-dcdcdcdcdcdc"),
                ticket_type="destination",
                body="Original",
                advertiser=CatalogAdvertiser("catalog", "catalog-key"),
                cap="uncap",
                cap_mode="unlim",
            )
        )

        self.assertEqual(formatter.requests[0].cap, "uncap")
        self.assertEqual(formatter.requests[0].cap_mode, "unlim")
        self.assertEqual(uow.aggregates[0].cap, "uncap")
        self.assertEqual(
            uow.aggregates[0].source_public_message,
            "Advertiser: Resolved advertiser\nCap: unlim\n\nOriginal",
        )

    async def test_destination_formatter_failure_uses_prepared_fallback(self) -> None:
        uow = RecordingUnitOfWork(
            TicketSnapshot(7, "mephisto", "destination", "open", 10)
        )
        formatter = StubFormatter(fail=True)
        workflow = make_workflow(uow, formatter=formatter)
        await workflow.create_ticket(
            CreateTicketCommand(
                profile_key="mephisto",
                actor=Actor(10, "Author", "client"),
                idempotency_key=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
                ticket_type="destination",
                body="Original",
                advertiser=CatalogAdvertiser("catalog", "catalog-key"),
                cap=40,
                cap_mode="daily",
            )
        )
        aggregate = uow.aggregates[0]
        self.assertTrue(aggregate.used_fallback)
        self.assertEqual(aggregate.formatted_ticket, "fallback:Resolved advertiser:40")
        self.assertEqual(
            aggregate.internal_note,
            "catalog-note\n\nDestination validation fallback: destination validation failed",
        )

    async def test_replay_and_concurrent_duplicate_call_formatter_once(self) -> None:
        uow = RecordingUnitOfWork(
            TicketSnapshot(7, "mephisto", "destination", "open", 10)
        )
        formatter = StubFormatter()
        workflow = make_workflow(uow, formatter=formatter)
        command = CreateTicketCommand(
            profile_key="mephisto",
            actor=Actor(10, "Author", "client"),
            idempotency_key=UUID("abababab-abab-4bab-8bab-abababababab"),
            ticket_type="destination",
            body="Original",
            advertiser=CatalogAdvertiser("catalog", "catalog-key"),
            cap=25,
            cap_mode="daily",
        )
        first, second = await asyncio.gather(
            workflow.create_ticket(command), workflow.create_ticket(command)
        )
        third = await workflow.create_ticket(command)
        self.assertEqual(len(formatter.requests), 1)
        self.assertEqual(sum(result.replayed for result in (first, second, third)), 2)

        with self.assertRaises(IdempotencyConflict):
            await workflow.create_ticket(replace(command, body="Changed"))
        self.assertEqual(len(formatter.requests), 1)

    async def test_failed_preparation_releases_lease_for_retry(self) -> None:
        class FlakyFormatter(StubFormatter):
            async def format(self, request):
                self.requests.append(request)
                if len(self.requests) == 1:
                    raise RuntimeError("temporary failure")
                return DestinationFormatResult(
                    "recovered",
                    None,
                    results=(DestinationResult(
                        "New advertiser | Brand | FB | BR | Slots | 100 USD | cap 25",
                        "https://example.test/?offer=1",
                    ),),
                )

        uow = RecordingUnitOfWork(
            TicketSnapshot(7, "mephisto", "destination", "open", 10)
        )
        formatter = FlakyFormatter()
        workflow = make_workflow(uow, formatter=formatter)
        command = CreateTicketCommand(
            profile_key="mephisto",
            actor=Actor(10, "Author", "client"),
            idempotency_key=UUID("acacacac-acac-4cac-8cac-acacacacacac"),
            ticket_type="destination",
            body="Original",
            advertiser=NewAdvertiser("new", "New advertiser"),
            cap=25,
            cap_mode="lifetime",
        )
        with self.assertRaises(RuntimeError):
            await workflow.create_ticket(command)
        result = await workflow.create_ticket(command)
        self.assertEqual(result.status, "open")
        self.assertEqual(len(formatter.requests), 2)

    async def test_idempotency_namespace_isolated_by_profile_and_operation(self) -> None:
        uow = RecordingUnitOfWork(
            TicketSnapshot(7, "kalisto", "general", "open", 10)
        )
        workflow = make_workflow(uow)
        key = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
        base = CreateTicketCommand(
            profile_key="kalisto",
            actor=Actor(10, "Author", "client"),
            idempotency_key=key,
            ticket_type="general",
            body="Original",
        )
        await workflow.create_ticket(base)
        await workflow.create_ticket(replace(base, profile_key="mephisto"))
        await workflow.add_message(
            AddMessageCommand(
                profile_key="kalisto",
                actor=base.actor,
                ticket_id=7,
                idempotency_key=key,
                visibility="public",
                body="Message",
            )
        )
        self.assertEqual(len(uow.records), 3)
        self.assertTrue(any(":kalisto:10:create_ticket:" in key for key in uow.records))
        self.assertTrue(any(":mephisto:10:create_ticket:" in key for key in uow.records))
        self.assertTrue(any(":kalisto:10:add_message:" in key for key in uow.records))

    async def test_public_message_is_idempotent(self) -> None:
        uow = RecordingUnitOfWork(
            TicketSnapshot(7, "kalisto", "general", "open", 10)
        )
        workflow = make_workflow(uow)
        command = AddMessageCommand(
            profile_key="kalisto",
            actor=Actor(10, "Author", "client"),
            ticket_id=7,
            idempotency_key=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            visibility="public",
            body="Question",
        )
        first = await workflow.add_message(command)
        second = await workflow.add_message(command)
        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(len(uow.messages), 1)
        self.assertEqual(len(uow.notifications), 1)

    async def test_transition_persists_event_state_and_notification_plan(self) -> None:
        uow = RecordingUnitOfWork(
            TicketSnapshot(7, "kalisto", "general", "open", 10)
        )
        workflow = make_workflow(
            uow, clock=lambda: datetime(2026, 11, 4, 10, tzinfo=UTC)
        )
        command = TransitionCommand(
            profile_key="kalisto",
            actor=Actor(20, "Tech", "technician"),
            ticket_id=7,
            idempotency_key=UUID("11111111-1111-4111-8111-111111111111"),
            action=WorkflowAction.SUBMIT_REVIEW,
        )
        result = await workflow.transition(command)
        self.assertEqual(result.status, "review")
        self.assertEqual(len(uow.events), 1)
        self.assertEqual(len(uow.notifications), 2)
        reminder = uow.notifications[1]
        self.assertEqual(reminder.deliver_if_status, "review")
        self.assertEqual(reminder.review_cycle, 1)

    async def test_same_idempotency_key_and_payload_replays_result(self) -> None:
        uow = RecordingUnitOfWork(
            TicketSnapshot(7, "kalisto", "destination", "open", 10)
        )
        workflow = make_workflow(uow)
        command = TransitionCommand(
            profile_key="kalisto",
            actor=Actor(20, "Tech", "technician"),
            ticket_id=7,
            idempotency_key=UUID("22222222-2222-4222-8222-222222222222"),
            action=WorkflowAction.COMPLETE_DESTINATION,
        )
        first = await workflow.transition(command)
        second = await workflow.transition(
            replace(command, actor=Actor(20, "Renamed tech", "admin"))
        )
        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(len(uow.events), 1)
        self.assertEqual(len(uow.notifications), 1)

    async def test_same_idempotency_key_with_other_payload_conflicts(self) -> None:
        uow = RecordingUnitOfWork(
            TicketSnapshot(7, "kalisto", "general", "review", 10, 1)
        )
        workflow = make_workflow(uow)
        key = UUID("33333333-3333-4333-8333-333333333333")
        base = TransitionCommand(
            profile_key="kalisto",
            actor=Actor(10, "Author", "client"),
            ticket_id=7,
            idempotency_key=key,
            action=WorkflowAction.RETURN,
            reason="First reason",
        )
        await workflow.transition(base)
        with self.assertRaises(IdempotencyConflict):
            await workflow.transition(replace(base, reason="Changed reason"))
        self.assertEqual(len(uow.events), 1)


if __name__ == "__main__":
    unittest.main()

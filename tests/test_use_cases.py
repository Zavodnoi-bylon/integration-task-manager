from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from dest_miniapp.domain import TicketSnapshot, WorkflowAction
from dest_miniapp.models import Actor
from dest_miniapp.use_cases import (
    AddMessageCommand,
    CatalogAdvertiser,
    CreateTicketCommand,
    DestinationFormatResult,
    DestinationFormattingError,
    IdempotencyConflict,
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

    async def create_ticket(
        self, context, aggregate, *, idempotency, payload_hash, event, notification
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
        if self.fail:
            raise DestinationFormattingError
        return DestinationFormatResult(
            formatted_ticket="model-formatted",
            internal_note="model-internal",
        )

    def fallback(self, request):
        return DestinationFormatResult(
            formatted_ticket=f"fallback:{request.advertiser_name}:{request.cap}",
            internal_note=request.technician_note,
            used_fallback=True,
        )


def make_workflow(uow, *, advertisers=None, formatter=None, clock=None):
    return TicketWorkflow(
        uow,
        advertisers=advertisers or StubAdvertisers(),
        destination_formatter=formatter or StubFormatter(),
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
        self.assertEqual(uow.aggregates[0].source_public_message, "Need help")

    async def test_catalog_destination_is_resolved_and_formatted_before_uow(self) -> None:
        uow = RecordingUnitOfWork(
            TicketSnapshot(7, "kalisto", "destination", "open", 10)
        )
        advertisers = StubAdvertisers()
        formatter = StubFormatter()
        workflow = make_workflow(
            uow, advertisers=advertisers, formatter=formatter
        )
        command = CreateTicketCommand(
            profile_key="kalisto",
            actor=Actor(10, "Author", "client"),
            idempotency_key=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
            ticket_type="destination",
            body="Original client request",
            advertiser=CatalogAdvertiser("catalog", "catalog-key"),
            cap=25,
        )
        await workflow.create_ticket(command)
        self.assertEqual(advertisers.keys, [("kalisto", "catalog-key")])
        self.assertEqual(formatter.requests[0].suffix, "catalog-suffix")
        aggregate = uow.aggregates[0]
        self.assertEqual(aggregate.formatted_ticket, "model-formatted")
        self.assertEqual(aggregate.source_public_message, "Original client request")
        self.assertEqual(aggregate.internal_note, "model-internal")
        self.assertFalse(aggregate.used_fallback)

    async def test_new_advertiser_does_not_use_catalog(self) -> None:
        uow = RecordingUnitOfWork(
            TicketSnapshot(7, "kalisto", "destination", "open", 10)
        )
        advertisers = StubAdvertisers()
        formatter = StubFormatter()
        workflow = make_workflow(
            uow, advertisers=advertisers, formatter=formatter
        )
        await workflow.create_ticket(
            CreateTicketCommand(
                profile_key="kalisto",
                actor=Actor(10, "Author", "client"),
                idempotency_key=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
                ticket_type="destination",
                body="Original",
                advertiser=NewAdvertiser("new", "New advertiser"),
                cap=30,
            )
        )
        self.assertEqual(advertisers.keys, [])
        self.assertEqual(formatter.requests[0].advertiser_name, "New advertiser")
        self.assertIsNone(formatter.requests[0].suffix)

    async def test_destination_formatter_failure_uses_prepared_fallback(self) -> None:
        uow = RecordingUnitOfWork(
            TicketSnapshot(7, "kalisto", "destination", "open", 10)
        )
        formatter = StubFormatter(fail=True)
        workflow = make_workflow(uow, formatter=formatter)
        await workflow.create_ticket(
            CreateTicketCommand(
                profile_key="kalisto",
                actor=Actor(10, "Author", "client"),
                idempotency_key=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
                ticket_type="destination",
                body="Original",
                advertiser=CatalogAdvertiser("catalog", "catalog-key"),
                cap=40,
            )
        )
        aggregate = uow.aggregates[0]
        self.assertTrue(aggregate.used_fallback)
        self.assertEqual(aggregate.formatted_ticket, "fallback:Resolved advertiser:40")
        self.assertEqual(aggregate.internal_note, "catalog-note")

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

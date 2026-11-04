from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from dest_miniapp.domain import TicketSnapshot, WorkflowAction
from dest_miniapp.models import Actor
from dest_miniapp.use_cases import (
    AddMessageCommand,
    CreateTicketCommand,
    IdempotencyConflict,
    MutationResult,
    TicketWorkflow,
    TransitionCommand,
)


class RecordingUnitOfWork:
    def __init__(self, snapshot: TicketSnapshot) -> None:
        self.snapshot = snapshot
        self.records: dict[tuple[int, UUID], tuple[str, MutationResult]] = {}
        self.events = []
        self.messages = []
        self.notifications = []

    async def create_ticket(self, command, *, payload_hash, event, notification):
        key = (command.actor.telegram_user_id, command.idempotency_key)
        stored = self.records.get(key)
        if stored:
            stored_hash, result = stored
            if stored_hash != payload_hash:
                raise IdempotencyConflict
            return replace(result, replayed=True)
        result = MutationResult(100, "open", command.ticket_type, 0)
        self.events.append(event)
        self.notifications.append(notification)
        self.records[key] = (payload_hash, result)
        return result

    async def add_message(self, command, *, payload_hash, planner):
        key = (command.actor.telegram_user_id, command.idempotency_key)
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

    async def transition(self, command, *, payload_hash, planner):
        key = (command.actor.telegram_user_id, command.idempotency_key)
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


class UseCaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_ticket_is_idempotent_and_plans_staff_notification(self) -> None:
        uow = RecordingUnitOfWork(
            TicketSnapshot(7, "kalisto", "general", "open", 10)
        )
        workflow = TicketWorkflow(uow)
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

    async def test_public_message_is_idempotent(self) -> None:
        uow = RecordingUnitOfWork(
            TicketSnapshot(7, "kalisto", "general", "open", 10)
        )
        workflow = TicketWorkflow(uow)
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
        workflow = TicketWorkflow(
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
        workflow = TicketWorkflow(uow)
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
        workflow = TicketWorkflow(uow)
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

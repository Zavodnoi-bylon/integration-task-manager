from __future__ import annotations

import unittest
from datetime import UTC, datetime

from dest_miniapp.domain import (
    ActionForbidden,
    InvalidTransition,
    NotificationKind,
    ReasonRequired,
    TicketHidden,
    TicketSnapshot,
    WorkflowAction,
    plan_message,
    plan_transition,
)
from dest_miniapp.models import Actor


NOW = datetime(2026, 11, 4, 18, 30, tzinfo=UTC)
AUTHOR = Actor(10, "Author", "client")
TECH = Actor(20, "Tech", "technician")
ADMIN_AUTHOR = Actor(10, "Staff author", "admin")
SUPERVISOR_AUTHOR = Actor(10, "Supervisor author", "supervisor")
SUPERVISOR = Actor(30, "Supervisor", "supervisor")


def ticket(
    *, kind="general", status="open", author=10, cycle=0, sequence=0
) -> TicketSnapshot:
    return TicketSnapshot(
        ticket_id=7,
        profile_key="kalisto",
        ticket_type=kind,
        status=status,
        author_telegram_user_id=author,
        review_cycle=cycle,
        event_sequence=sequence,
    )


class WorkflowTests(unittest.TestCase):
    def test_staff_submits_general_for_review_and_schedules_next_noon(self) -> None:
        plan = plan_transition(
            ticket(), action=WorkflowAction.SUBMIT_REVIEW, actor=TECH, now=NOW
        )
        self.assertEqual(plan.new_status, "review")
        self.assertEqual(plan.review_cycle, 1)
        self.assertEqual(
            [item.kind for item in plan.notifications],
            [
                NotificationKind.AUTHOR_REVIEW_REQUESTED,
                NotificationKind.AUTHOR_REVIEW_REMINDER,
            ],
        )
        reminder = plan.notifications[1]
        self.assertEqual(reminder.due_at.isoformat(), "2026-11-05T12:00:00+03:00")
        self.assertIn("review:1:reminder", reminder.dedup_key)

    def test_new_review_cycle_gets_another_deduplicated_reminder(self) -> None:
        plan = plan_transition(
            ticket(cycle=1, sequence=3),
            action=WorkflowAction.SUBMIT_REVIEW,
            actor=TECH,
            now=NOW,
        )
        self.assertEqual(plan.review_cycle, 2)
        self.assertIn("review:2:reminder", plan.notifications[1].dedup_key)

    def test_only_staff_can_submit_review(self) -> None:
        with self.assertRaises(ActionForbidden):
            plan_transition(
                ticket(), action=WorkflowAction.SUBMIT_REVIEW, actor=AUTHOR, now=NOW
            )

    def test_author_accepts_general_review(self) -> None:
        plan = plan_transition(
            ticket(status="review", cycle=1),
            action=WorkflowAction.ACCEPT,
            actor=AUTHOR,
            now=NOW,
        )
        self.assertEqual(plan.new_status, "completed")
        self.assertEqual(plan.notifications[0].kind, NotificationKind.STAFF_TICKET_ACCEPTED)

    def test_staff_author_may_accept_own_general_ticket(self) -> None:
        plan = plan_transition(
            ticket(status="review", cycle=1),
            action=WorkflowAction.ACCEPT,
            actor=ADMIN_AUTHOR,
            now=NOW,
        )
        self.assertEqual(plan.new_status, "completed")

    def test_supervisor_keeps_author_actions_only_on_own_ticket(self) -> None:
        plan = plan_transition(
            ticket(status="review", cycle=1),
            action=WorkflowAction.ACCEPT,
            actor=SUPERVISOR_AUTHOR,
            now=NOW,
        )
        self.assertEqual(plan.new_status, "completed")
        with self.assertRaises(ActionForbidden):
            plan_transition(
                ticket(status="review", cycle=1),
                action=WorkflowAction.ACCEPT,
                actor=SUPERVISOR,
                now=NOW,
            )
        with self.assertRaises(ActionForbidden):
            plan_transition(
                ticket(),
                action=WorkflowAction.SUBMIT_REVIEW,
                actor=SUPERVISOR_AUTHOR,
                now=NOW,
            )

    def test_non_author_client_is_hidden_and_non_author_staff_is_forbidden(self) -> None:
        other_client = Actor(11, "Other", "client")
        with self.assertRaises(TicketHidden):
            plan_transition(
                ticket(status="review"),
                action=WorkflowAction.ACCEPT,
                actor=other_client,
                now=NOW,
            )
        with self.assertRaises(ActionForbidden):
            plan_transition(
                ticket(status="review"),
                action=WorkflowAction.ACCEPT,
                actor=TECH,
                now=NOW,
            )

    def test_non_author_cannot_probe_type_or_status(self) -> None:
        other_client = Actor(11, "Other", "client")
        probes = [
            (ticket(kind="destination", status="open"), WorkflowAction.ACCEPT),
            (ticket(kind="general", status="completed"), WorkflowAction.RETURN),
            (ticket(kind="destination", status="review"), WorkflowAction.REOPEN),
        ]
        for snapshot, action in probes:
            with self.subTest(action=action, snapshot=snapshot):
                with self.assertRaises(TicketHidden):
                    plan_transition(
                        snapshot, action=action, actor=other_client, now=NOW,
                        reason="Probe",
                    )

    def test_client_staff_only_probe_is_forbidden_before_state_check(self) -> None:
        with self.assertRaises(ActionForbidden):
            plan_transition(
                ticket(kind="destination", status="completed"),
                action=WorkflowAction.SUBMIT_REVIEW,
                actor=AUTHOR,
                now=NOW,
            )
        with self.assertRaises(ActionForbidden):
            plan_message(
                ticket(status="completed"),
                actor=AUTHOR,
                visibility="internal",
                body="Probe",
            )

    def test_return_requires_public_reason(self) -> None:
        with self.assertRaises(ReasonRequired):
            plan_transition(
                ticket(status="review", cycle=1),
                action=WorkflowAction.RETURN,
                actor=AUTHOR,
                now=NOW,
                reason="   ",
            )
        plan = plan_transition(
            ticket(status="review", cycle=1),
            action=WorkflowAction.RETURN,
            actor=AUTHOR,
            now=NOW,
            reason="  Please fix tracking  ",
        )
        self.assertEqual(plan.new_status, "open")
        self.assertEqual(plan.public_message, "Please fix tracking")
        self.assertEqual(plan.notifications[0].public_reason, "Please fix tracking")

    def test_destination_completes_directly_with_neutral_notification(self) -> None:
        plan = plan_transition(
            ticket(kind="destination"),
            action=WorkflowAction.COMPLETE_DESTINATION,
            actor=TECH,
            now=NOW,
        )
        self.assertEqual(plan.new_status, "completed")
        notice = plan.notifications[0]
        self.assertEqual(notice.kind, NotificationKind.AUTHOR_DESTINATION_COMPLETED)
        self.assertIsNone(notice.public_reason)

    def test_reopen_requires_author_and_public_reason(self) -> None:
        with self.assertRaises(ReasonRequired):
            plan_transition(
                ticket(status="completed"),
                action=WorkflowAction.REOPEN,
                actor=AUTHOR,
                now=NOW,
            )
        plan = plan_transition(
            ticket(status="completed", sequence=5),
            action=WorkflowAction.REOPEN,
            actor=AUTHOR,
            now=NOW,
            reason="Problem reproduced",
        )
        self.assertEqual(plan.new_status, "open")
        self.assertEqual(plan.public_message, "Problem reproduced")
        self.assertIn("transition:6:reopened", plan.notifications[0].dedup_key)

    def test_wrong_ticket_type_or_state_is_conflict(self) -> None:
        with self.assertRaises(InvalidTransition):
            plan_transition(
                ticket(kind="destination"),
                action=WorkflowAction.SUBMIT_REVIEW,
                actor=TECH,
                now=NOW,
            )
        with self.assertRaises(InvalidTransition):
            plan_transition(
                ticket(status="review"),
                action=WorkflowAction.COMPLETE_DESTINATION,
                actor=TECH,
                now=NOW,
            )

    def test_internal_message_is_role_based_not_author_based(self) -> None:
        staff_plan = plan_message(
            ticket(), actor=ADMIN_AUTHOR, visibility="internal", body=" Internal "
        )
        self.assertEqual(staff_plan.visibility, "internal")
        self.assertEqual(staff_plan.body, "Internal")
        with self.assertRaises(ActionForbidden):
            plan_message(
                ticket(), actor=AUTHOR, visibility="internal", body="Not allowed"
            )

    def test_public_message_never_creates_internal_payload(self) -> None:
        plan = plan_message(
            ticket(), actor=AUTHOR, visibility="public", body="Question"
        )
        self.assertEqual(plan.visibility, "public")
        self.assertEqual(plan.notifications[0].public_reason, "Question")

    def test_supervisor_can_only_write_public_messages(self) -> None:
        plan = plan_message(
            ticket(), actor=SUPERVISOR, visibility="public", body="Status?"
        )
        self.assertEqual(plan.visibility, "public")
        with self.assertRaises(ActionForbidden):
            plan_message(
                ticket(), actor=SUPERVISOR, visibility="internal", body="Hidden"
            )


if __name__ == "__main__":
    unittest.main()

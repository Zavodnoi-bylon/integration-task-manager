import os
import tempfile
import unittest

from app import sql
from app.domain import (
    EVENT_COMMENT,
    EVENT_PUBLIC_MESSAGE,
    EVENT_STATUS_CHANGED,
    STATUS_COMPLETED,
    STATUS_OPEN,
    TICKET_TYPE_DESTINATION,
)
from app.services.ticket_service import (
    AccessDeniedError,
    TicketNotFoundError,
    TicketService,
)
from app.settings import Settings


class TicketServiceTests(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.original_db_path = sql.DB_PATH
        sql.DB_PATH = self.db_path
        sql.create_tables()
        self.settings = Settings(
            bot_token="123456:TEST_TOKEN",
            admin_users=frozenset({200}),
            admin_message=200,
            bot_key="kalisto",
        )
        self.service = TicketService(self.settings)
        self.service.register_user(100, "@client", "2026-10-28 10:00:00")
        self.service.register_user(200, "@technician", "2026-10-28 10:00:00")

    def tearDown(self):
        sql.DB_PATH = self.original_db_path
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_complete_flow_preserves_history(self):
        ticket = self.service.create_ticket(100, "Проверить интеграцию", "2026-10-28 10:01:00")
        self.assertEqual(ticket.assigned_to, 200)
        self.service.add_comment(ticket.ticket_id, 200, "Проверено", "2026-10-28 10:05:00")
        completed = self.service.complete_ticket(ticket.ticket_id, 200, "2026-10-28 10:06:00")
        self.assertEqual(completed.status, STATUS_COMPLETED)
        reopened = self.service.reopen_ticket(ticket.ticket_id, 100, "2026-10-28 10:07:00")
        self.assertEqual(reopened.status, STATUS_OPEN)

        event_types = [event.event_type for event in self.service.get_events(ticket.ticket_id)]
        self.assertIn(EVENT_COMMENT, event_types)
        self.assertEqual(event_types.count(EVENT_STATUS_CHANGED), 2)

    def test_public_messages_are_bound_to_the_selected_ticket(self):
        ticket = self.service.create_ticket(
            100,
            "Обсудить настройку",
            "2026-10-28 10:01:00",
        )
        self.service.add_public_message(
            ticket.ticket_id,
            100,
            "Вопрос клиента",
            "2026-10-28 10:02:00",
        )
        self.service.add_public_message(
            ticket.ticket_id,
            200,
            "Ответ поддержки",
            "2026-10-28 10:03:00",
        )

        events = self.service.get_events(ticket.ticket_id)
        public = [event.text for event in events if event.event_type == EVENT_PUBLIC_MESSAGE]
        self.assertEqual(public, ["Вопрос клиента", "Ответ поддержки"])

    def test_destination_ticket_keeps_formatted_body_and_original_source(self):
        ticket = self.service.create_destination_ticket(
            100,
            "V.Partners",
            "Исходное тело оффера",
            "V.Partners | Slotoro | FB Apps | LT | Slots | 150 USD",
            "2026-10-28 10:01:00",
            request_key="telegram-message:100:55",
        )

        self.assertEqual(ticket.ticket_type, TICKET_TYPE_DESTINATION)
        self.assertTrue(ticket.body.startswith("V.Partners | Slotoro"))
        public = [
            event.text
            for event in self.service.get_events(ticket.ticket_id)
            if event.event_type == EVENT_PUBLIC_MESSAGE
        ]
        self.assertEqual(
            public,
            ["Advertiser: V.Partners\n\nИсходное тело оффера"],
        )

    def test_destination_ticket_can_add_internal_technician_note(self):
        ticket = self.service.create_destination_ticket(
            100,
            "V1VA",
            "Исходное тело оффера",
            "V1VA | Brand | FB | PT | Slots | 100 USD | cap 10 FTD",
            "2026-10-28 10:01:00",
            request_key="telegram-message:100:56",
            technician_note="Выбрать advertiser для нужного GEO",
        )

        internal = [
            event.text
            for event in self.service.get_events(ticket.ticket_id)
            if event.event_type == EVENT_COMMENT
        ]
        self.assertEqual(
            internal,
            ["Системная пометка: Выбрать advertiser для нужного GEO"],
        )

    def test_client_cannot_access_or_reopen_another_clients_ticket(self):
        self.service.register_user(101, "@other", "2026-10-28 10:00:00")
        ticket = self.service.create_ticket(100, "Чужая задача", "2026-10-28 10:01:00")
        with self.assertRaises(AccessDeniedError):
            self.service.require_view_access(101, ticket)

        completed = self.service.complete_ticket(ticket.ticket_id, 200, "2026-10-28 10:02:00")
        with self.assertRaises(AccessDeniedError):
            self.service.reopen_ticket(completed.ticket_id, 101, "2026-10-28 10:03:00")

    def test_technician_can_view_any_ticket(self):
        ticket = self.service.create_ticket(100, "Задача", "2026-10-28 10:01:00")
        self.service.require_view_access(200, ticket)
        self.assertTrue(self.service.can_view(200, ticket))

    def test_bot_profiles_do_not_see_each_others_tickets(self):
        kalisto_ticket = self.service.create_ticket(
            100,
            "Только Kalisto",
            "2026-10-28 10:01:00",
        )
        mephisto = TicketService(Settings(
            bot_token="123456:SECOND_TEST_TOKEN",
            admin_users=frozenset({300}),
            admin_message=300,
            bot_key="mephisto",
        ))
        mephisto.register_user(101, "@raw_client", "2026-10-28 10:02:00")
        mephisto.register_user(300, "@mephisto", "2026-10-28 10:02:00")

        self.assertEqual(
            mephisto.list_technician_tickets(STATUS_OPEN).total,
            0,
        )
        with self.assertRaises(TicketNotFoundError):
            mephisto.get_ticket(kalisto_ticket.ticket_id)

    def test_telegram_account_can_be_technician_in_both_bot_profiles(self):
        mephisto = TicketService(Settings(
            bot_token="123456:SECOND_TEST_TOKEN",
            admin_users=frozenset({200}),
            admin_message=200,
            bot_key="mephisto",
            technician_users=frozenset({300}),
        ))

        mephisto_user = mephisto.register_user(
            200,
            "@main_technician",
            "2026-10-28 10:02:00",
        )

        self.assertEqual(mephisto_user.bot_key, "mephisto")
        self.assertEqual(mephisto_user.role, "admin")
        self.assertTrue(mephisto.is_technician(200))
        self.assertIsNotNone(sql.get_user_by_id(200, "kalisto"))
        self.assertIsNotNone(sql.get_user_by_id(200, "mephisto"))

        technician = mephisto.register_user(
            300,
            "@mephisto_technician",
            "2026-10-28 10:03:00",
        )
        self.assertEqual(technician.role, "technician")
        self.assertTrue(mephisto.is_technician(300))


if __name__ == "__main__":
    unittest.main()

import unittest

from app.domain import (
    EVENT_PUBLIC_MESSAGE,
    STATUS_COMPLETED,
    TicketEventRecord,
    TicketRecord,
)
from app.views.tickets import ticket_card


class TicketCardTests(unittest.TestCase):
    def setUp(self):
        self.ticket = TicketRecord(
            ticket_id=17,
            owner_id=100,
            body="Проверить интеграцию",
            created_at="2026-10-29 11:13:48",
            status=STATUS_COMPLETED,
            latest_comment="Готово",
            assigned_to=200,
            updated_at="2026-10-29 11:15:00",
            closed_at="2026-10-29 11:15:00",
            bot_key="kalisto",
        )

    def test_client_card_hides_operational_metadata(self):
        text, _ = ticket_card(self.ticket, "@client", admin_view=False)

        self.assertIn("Проверить интеграцию", text)
        self.assertNotIn("Автор:", text)
        self.assertNotIn("Статус:", text)
        self.assertNotIn("Создана:", text)
        self.assertNotIn("Обновлена:", text)
        self.assertNotIn("Завершена:", text)

    def test_admin_card_keeps_operational_metadata(self):
        text, _ = ticket_card(self.ticket, "@client", admin_view=True)

        self.assertIn("Автор:", text)
        self.assertIn("Статус:", text)
        self.assertIn("Создана:", text)
        self.assertIn("Обновлена:", text)
        self.assertIn("Завершена:", text)

    def test_client_conversation_hides_staff_identity(self):
        events = (
            TicketEventRecord(1, 17, 100, EVENT_PUBLIC_MESSAGE, None, None,
                              "Вопрос", "2026-10-29 11:14:00"),
            TicketEventRecord(2, 17, 200, EVENT_PUBLIC_MESSAGE, None, None,
                              "Ответ", "2026-10-29 11:15:00"),
        )
        text, _ = ticket_card(
            self.ticket,
            "@client",
            admin_view=False,
            events=events,
            actor_names={100: "@client", 200: "@technician"},
            viewer_id=100,
        )

        self.assertIn("<b>Вы:</b> Вопрос", text)
        self.assertIn("<b>Поддержка:</b> Ответ", text)
        self.assertNotIn("@technician", text)


if __name__ == "__main__":
    unittest.main()

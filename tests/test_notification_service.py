import unittest
from unittest.mock import AsyncMock

from app.domain import STATUS_OPEN, TICKET_TYPE_DESTINATION, TicketRecord
from app.services.notification_service import NotificationService
from app.settings import Settings


class NotificationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_internal_note_is_included_only_in_technician_alert(self):
        bot = AsyncMock()
        service = NotificationService(
            bot,
            Settings(
                bot_token="123456:TEST_TOKEN",
                admin_users=frozenset({200}),
                technician_users=frozenset({200}),
                admin_message=200,
                bot_key="kalisto",
            ),
        )
        ticket = TicketRecord(
            ticket_id=7,
            owner_id=100,
            body="Destination | New Brand | cap 20",
            created_at="2026-10-31 10:00:00",
            status=STATUS_OPEN,
            latest_comment="Системная пометка: Новый рекл",
            assigned_to=200,
            updated_at="2026-10-31 10:00:00",
            closed_at=None,
            bot_key="kalisto",
            ticket_type=TICKET_TYPE_DESTINATION,
        )

        await service.new_ticket(ticket, "@client")

        sent_text = bot.send_message.await_args.args[1]
        self.assertIn("Новый рекл", sent_text)
        self.assertIn("Внутренняя пометка", sent_text)
        self.assertNotIn("Telegram ID", sent_text)
        self.assertNotIn("<code>100</code>", sent_text)


if __name__ == "__main__":
    unittest.main()

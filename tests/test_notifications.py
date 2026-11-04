from __future__ import annotations

import json
import unittest

import httpx

from dest_miniapp.domain import NotificationKind
from dest_miniapp.notifications import TelegramNotificationDelivery, render_notification


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    def test_every_notification_kind_has_safe_copy(self):
        for kind in NotificationKind:
            text = render_notification(kind, {
                "ticket_id": 42,
                "public_reason": "public text",
                "internal_note": "must-not-leak",
            })
            self.assertIn("#42", text)
            self.assertNotIn("must-not-leak", text)
            self.assertLessEqual(len(text), 4096)

    async def test_delivery_calls_current_bot_api(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"ok": True, "result": {}})

        delivery = TelegramNotificationDelivery(
            "test-token", transport=httpx.MockTransport(handler)
        )
        await delivery.send(
            profile_key="kalisto",
            telegram_user_id=123,
            notification_type="author_review_requested",
            payload={"ticket_id": 7},
        )
        self.assertEqual(captured["body"]["chat_id"], 123)
        self.assertEqual(captured["url"], "https://api.telegram.org/bottest-token/sendMessage")


if __name__ == "__main__":
    unittest.main()

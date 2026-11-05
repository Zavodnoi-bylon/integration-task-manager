import unittest
from datetime import datetime, timezone
import logging
from unittest.mock import patch

import httpx

from app.integrations.telegram import (
    TelegramConfig,
    TelegramDeliveryError,
    TelegramSender,
)
from app.services.aio_alert_store import PendingAlert
from app.workers.aio_ftd import (
    WorkerSettings,
    format_alert,
    main,
    run_cycle,
    seconds_until_poll,
)


class InitializingStore:
    def __init__(self, now: datetime) -> None:
        self.now = now
        self.reservations = 0

    async def reserve_due(self, limit: int):
        self.reservations += 1
        return ()

    async def checkpoint_or_initialize(self, tenant_uuid, tenant_name, now):
        return self.now, self.now, True


class UnusedSender:
    async def send(self, text: str):
        raise AssertionError("No Telegram delivery is expected during initialization")


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
        self.settings = WorkerSettings(
            aio_token="aio-token",
            tenant_uuid="tenant-1",
            tenant_name="TA",
            ftd_type_uuid="ftd-type",
            telegram_token="bot-token",
            telegram_chat_id="123",
        )

    async def test_first_cycle_only_sets_checkpoint(self):
        store = InitializingStore(self.now)
        summary = await run_cycle(
            self.settings,
            store,
            object(),
            UnusedSender(),
            now=self.now,
        )
        self.assertTrue(summary.initialized)
        self.assertEqual(summary.candidates, 0)
        self.assertEqual(summary.sent, 0)
        self.assertEqual(store.reservations, 1)

    async def test_telegram_429_preserves_retry_after(self):
        async def handler(request):
            return httpx.Response(
                429,
                json={"ok": False, "parameters": {"retry_after": 17}},
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://telegram.test",
        ) as raw:
            async with TelegramSender(
                TelegramConfig("secret-token", "123"), client=raw
            ) as sender:
                with self.assertRaises(TelegramDeliveryError) as caught:
                    await sender.send("test")
        self.assertEqual(caught.exception.error_code, "rate_limited")
        self.assertEqual(caught.exception.retry_after, 17)
        self.assertNotIn("secret-token", str(caught.exception))


class AlertFormatTests(unittest.TestCase):
    def test_alert_contains_business_fields_and_escapes_html(self):
        alert = PendingAlert(
            tenant_uuid="tenant-1",
            tenant_name="TA & Co",
            conversion_uuid="conversion-1",
            visit_uuid="visit-1",
            conversion_at=datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc),
            destination_name="Casino <BR>",
            advertiser_name="Advertiser & One",
            revenue="0",
            currency="USD",
            attempts=1,
        )
        message = format_alert(alert)
        self.assertIn("Casino &lt;BR&gt;", message)
        self.assertIn("Advertiser &amp; One", message)
        self.assertIn("TA &amp; Co", message)
        self.assertIn("0 USD", message)
        self.assertIn("01.11.2026 12:00:00 МСК", message)


class PollScheduleTests(unittest.TestCase):
    def test_poll_is_aligned_two_minutes_after_each_ten_minute_boundary(self):
        now = datetime(2026, 11, 1, 15, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(seconds_until_poll(now, 600, 120), 120.0)

    def test_exact_poll_slot_has_no_delay(self):
        now = datetime(2026, 11, 1, 15, 2, 0, tzinfo=timezone.utc)
        self.assertEqual(seconds_until_poll(now, 600, 120), 0.0)

    def test_poll_after_slot_waits_for_the_next_ten_minute_slot(self):
        now = datetime(2026, 11, 1, 15, 2, 5, tzinfo=timezone.utc)
        self.assertEqual(seconds_until_poll(now, 600, 120), 595.0)


class LoggingSafetyTests(unittest.TestCase):
    def test_main_suppresses_http_client_info_logs(self):
        with (
            patch("app.workers.aio_ftd.load_dotenv"),
            patch("app.workers.aio_ftd.argparse.ArgumentParser.parse_args") as args,
            patch(
                "app.workers.aio_ftd.run_audit",
                new=lambda hours: object(),
            ),
            patch("app.workers.aio_ftd.asyncio.run", return_value=0),
        ):
            args.return_value.serve = False
            args.return_value.once = False
            args.return_value.hours = 1.0
            with self.assertRaises(SystemExit):
                main()
        self.assertGreaterEqual(logging.getLogger("httpx").level, logging.WARNING)
        self.assertGreaterEqual(logging.getLogger("httpcore").level, logging.WARNING)


if __name__ == "__main__":
    unittest.main()

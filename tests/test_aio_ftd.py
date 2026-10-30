import unittest
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from app.integrations.aio.client import (
    AioApiError,
    AioClient,
    AioClientConfig,
    AioResponseError,
    TABLE,
)
from app.integrations.aio.models import AioConversion, AioPayloadError
from app.workers.aio_ftd import DEFAULT_FTD_TYPE_UUID, audit_window


def row(
    *,
    uuid: str = "conversion-1",
    revenue="0",
    type_uuid: str = DEFAULT_FTD_TYPE_UUID,
    destination=True,
):
    return {
        "uuid": uuid,
        "visit_uuid": "visit-1",
        "conversion_date": "2026-10-30 11:00:00",
        "revenue": revenue,
        "type": {"uuid": type_uuid, "name": "FTD"},
        "destination": (
            {
                "uuid": "destination-1",
                "name": "Destination One",
                "advertiser": {
                    "uuid": "advertiser-1",
                    "name": "Advertiser One",
                },
            }
            if destination
            else None
        ),
    }


def response(rows, next_page, *, remaining="59"):
    return {
        TABLE: {
            "response": {
                "rows": rows,
                "next": next_page,
            }
        }
    }, {"x-ratelimit-limit": "60", "x-ratelimit-remaining": remaining}


class AioConversionTests(unittest.TestCase):
    def test_zero_revenue_ftd_is_parsed(self):
        conversion = AioConversion.from_row(row(revenue="0.00"))
        self.assertEqual(conversion.revenue, Decimal("0.00"))
        self.assertTrue(conversion.is_zero_revenue_ftd(DEFAULT_FTD_TYPE_UUID))
        self.assertEqual(conversion.destination_name, "Destination One")
        self.assertEqual(conversion.advertiser_name, "Advertiser One")

    def test_empty_revenue_is_zero(self):
        conversion = AioConversion.from_row(row(revenue=""))
        self.assertEqual(conversion.revenue, 0)

    def test_invalid_revenue_is_rejected(self):
        with self.assertRaises(AioPayloadError):
            AioConversion.from_row(row(revenue="not-money"))

    def test_unknown_destination_is_rejected(self):
        with self.assertRaises(AioPayloadError):
            AioConversion.from_row(row(destination=False))


class AioClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.start = datetime(2026, 10, 30, 7, 0, tzinfo=timezone.utc)
        self.end = datetime(2026, 10, 30, 8, 0, tzinfo=timezone.utc)
        self.config = AioClientConfig(token="test-token", tenant_id="test-tenant")

    async def test_payload_uses_exact_ftd_filter_and_moscow_dates(self):
        client = AioClient(self.config, client=httpx.AsyncClient())
        payload = client.build_payload(
            self.start,
            self.end,
            1,
            DEFAULT_FTD_TYPE_UUID,
        )
        request = payload["request"][TABLE]
        self.assertEqual(
            request["filters"],
            [{"key": "conversion_type_uuid", "values": [DEFAULT_FTD_TYPE_UUID]}],
        )
        self.assertEqual(request["dates"][0], "2026-10-30 10:00:00")
        self.assertEqual(request["dates"][2], "Europe/Moscow")
        await client._client.aclose()

    async def test_pagination_stops_on_empty_page_even_when_next_is_true(self):
        calls = 0

        async def handler(request):
            nonlocal calls
            calls += 1
            payload, headers = response([], True)
            return httpx.Response(200, json=payload, headers=headers)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as raw:
            async with AioClient(self.config, client=raw) as client:
                pages = [
                    page
                    async for page in client.iter_pages(
                        self.start,
                        self.end,
                        DEFAULT_FTD_TYPE_UUID,
                    )
                ]
        self.assertEqual(len(pages), 1)
        self.assertEqual(calls, 1)

    async def test_audit_deduplicates_and_selects_only_zero_ftd(self):
        page_one, headers = response(
            [row(uuid="zero"), row(uuid="paid", revenue="12.50")],
            True,
            remaining="58",
        )
        page_two, _ = response([row(uuid="zero")], False, remaining="57")
        replies = [page_one, page_two]

        async def handler(request):
            return httpx.Response(200, json=replies.pop(0), headers=headers)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as raw:
            async with AioClient(self.config, client=raw) as client:
                summary, candidates = await audit_window(
                    client,
                    self.start,
                    self.end,
                    DEFAULT_FTD_TYPE_UUID,
                )
        self.assertEqual(summary.pages, 2)
        self.assertEqual(summary.rows, 3)
        self.assertEqual(summary.zero_revenue_ftd, 1)
        self.assertEqual(summary.duplicate_rows, 1)
        self.assertEqual(candidates[0].conversion_uuid, "zero")

    async def test_invalid_response_is_rejected(self):
        async def handler(request):
            return httpx.Response(200, json={"unexpected": {}})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as raw:
            async with AioClient(self.config, client=raw) as client:
                with self.assertRaises(AioResponseError):
                    await client.fetch_page(
                        self.start,
                        self.end,
                        1,
                        DEFAULT_FTD_TYPE_UUID,
                    )

    async def test_429_honors_retry_after_without_losing_the_page(self):
        calls = 0
        delays = []

        async def sleep(delay):
            delays.append(delay)

        async def handler(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(429, headers={"retry-after": "3"})
            payload, headers = response([], False)
            return httpx.Response(200, json=payload, headers=headers)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as raw:
            async with AioClient(self.config, client=raw, sleep=sleep) as client:
                page = await client.fetch_page(
                    self.start,
                    self.end,
                    1,
                    DEFAULT_FTD_TYPE_UUID,
                )
        self.assertEqual(page.rows, ())
        self.assertEqual(calls, 2)
        self.assertEqual(delays, [3.0])

    async def test_low_rate_limit_remaining_triggers_safety_pause(self):
        delays = []

        async def sleep(delay):
            delays.append(delay)

        async def handler(request):
            payload, headers = response([], False, remaining="2")
            return httpx.Response(200, json=payload, headers=headers)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as raw:
            async with AioClient(self.config, client=raw, sleep=sleep) as client:
                await client.fetch_page(
                    self.start,
                    self.end,
                    1,
                    DEFAULT_FTD_TYPE_UUID,
                )
        self.assertEqual(delays, [60.0])

    async def test_http_error_does_not_expose_token(self):
        async def handler(request):
            return httpx.Response(403)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as raw:
            async with AioClient(self.config, client=raw) as client:
                with self.assertRaises(AioApiError) as caught:
                    await client.fetch_page(
                        self.start,
                        self.end,
                        1,
                        DEFAULT_FTD_TYPE_UUID,
                    )
        self.assertNotIn("test-token", str(caught.exception))
        self.assertIn("status=403", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

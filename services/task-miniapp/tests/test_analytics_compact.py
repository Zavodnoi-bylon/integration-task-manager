from __future__ import annotations

import asyncio
import unittest

import httpx

from dest_miniapp.analytics_compact import (
    CompactAnalyticsError,
    HttpCompactAnalyticsClient,
)


def summary_payload() -> dict[str, object]:
    return {
        "tenant": "theraw",
        "period": {
            "from": "2026-11-01",
            "to": "2026-11-21",
            "basis": "destination_created_at",
            "timezone": "Europe/Moscow",
        },
        "filters": {"advertisers": []},
        "metrics": {
            "created": 12,
            "launched": 7,
            "not_launched": 5,
            "with_ftd": 3,
            "reg_sum": 34,
            "ftd_sum": 6,
        },
        "available_filters": {
            "advertisers": ["Example Advertiser"],
            "created_from": "2025-01-01",
            "created_to": "2026-11-20",
        },
        "coverage": {
            "conversion_history_from": "2026-01-01",
            "history_complete_for_cohort": True,
            "status": "complete_for_creation_cohort",
            "catalog_fetched_at": "2026-11-20T21:52:00Z",
            "conversion_latest_event_at": "2026-11-21T13:00:00Z",
            "mart_refreshed_at": "2026-11-21T13:01:00Z",
            "cap_target_available": False,
            "note": "CAP target в Explorer пока отсутствует.",
        },
        "empty": False,
    }


class CompactAnalyticsClientTests(unittest.TestCase):
    def test_bearer_is_sent_only_to_fixed_https_endpoint(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=summary_payload())

        client = HttpCompactAnalyticsClient(
            "https://explorer.example/base",
            transport=httpx.MockTransport(handler),
        )
        result = asyncio.run(client.summary(
            token="signed-token",
            params=[("from", "2026-11-01"), ("to", "2026-11-21")],
        ))
        self.assertEqual(result.tenant, "theraw")
        self.assertEqual(len(seen), 1)
        self.assertEqual(
            seen[0].url.path,
            "/base/api/compact/destinations/summary",
        )
        self.assertEqual(seen[0].headers["authorization"], "Bearer signed-token")
        self.assertNotIn("tenant", seen[0].url.params)

    def test_redirects_and_invalid_contract_fail_closed(self) -> None:
        for response in (
            httpx.Response(302, headers={"Location": "https://evil.example"}),
            httpx.Response(200, json={**summary_payload(), "unexpected": True}),
        ):
            with self.subTest(status=response.status_code):
                client = HttpCompactAnalyticsClient(
                    "https://explorer.example",
                    transport=httpx.MockTransport(lambda _request: response),
                )
                with self.assertRaisesRegex(
                    CompactAnalyticsError, "invalid response|temporarily unavailable"
                ):
                    asyncio.run(client.summary(
                        token="signed-token",
                        params=[("from", "2026-11-01"), ("to", "2026-11-21")],
                    ))


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.integrations.aio import AioClientConfig
from app.integrations.aio.advertisers import (
    ADVERTISER_ACTION,
    ADVERTISER_REPOSITORY,
    ADVERTISER_TABLE,
    AioAdvertiser,
    AioAdvertiserClient,
    AioAdvertiserPage,
    discover_available_tenants,
)
from app.services.aio_advertiser_store import AdvertiserSyncState
from app.workers.aio_advertisers import (
    AdvertiserSyncSummary,
    AdvertiserWorkerSettings,
    TENANT_PROFILES,
    run_cycle,
    run_once,
)


NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def advertiser(
    uuid: str = "10838f58-b93f-4a4f-a98a-a114a865521a",
    name: str = "UpUpPartners",
    created_at: datetime = NOW,
) -> AioAdvertiser:
    return AioAdvertiser(uuid, name, created_at, created_at, False)


class AioAdvertiserClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = AioClientConfig(
            token="secret-token",
            tenant_id="tenant-1",
        )

    async def test_list_uses_advertiser_table_and_descending_created_at(self):
        captured = {}

        async def handler(request):
            captured.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    ADVERTISER_TABLE: {
                        "response": {
                            "rows": [
                                {
                                    "_identity": {
                                        "uuid": "10838f58-b93f-4a4f-a98a-a114a865521a",
                                        "name": "UpUpPartners",
                                        "created_at": "2026-11-16T13:20:11.000000Z",
                                    },
                                    "uuid": "10838f58-b93f-4a4f-a98a-a114a865521a",
                                    "is_archived": False,
                                    "created_at": "2026-11-16T13:20:11.000000Z",
                                    "updated_at": "2026-11-16T13:20:11.000000Z",
                                }
                            ],
                            "next": False,
                        }
                    }
                },
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=transport, base_url="https://test"
        ) as raw:
            async with AioAdvertiserClient(self.config, client=raw) as client:
                page = await client.fetch_page(1)

        request = captured["request"][ADVERTISER_TABLE]
        self.assertEqual(request["table"], ADVERTISER_TABLE)
        self.assertEqual(request["sort_key"], "created_at")
        self.assertEqual(request["sort_direction"], "desc")
        self.assertEqual(page.advertisers[0].name, "UpUpPartners")

    async def test_discovers_every_available_tenant_without_tenant_header(self):
        captured = {}

        async def handler(request):
            captured["path"] = request.url.path
            captured["token"] = request.url.params.get("token")
            captured["tenant_header"] = request.headers.get("X-Tenant-Id")
            return httpx.Response(
                200,
                json={
                    "available_tenants": [
                        {"tenant": {"uuid": "tenant-b", "name": "Beta"}},
                        {"tenant": {"uuid": "tenant-a", "name": "Alpha"}},
                        {"tenant": {"uuid": "tenant-a", "name": "Alpha"}},
                    ]
                },
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=transport, base_url="https://test"
        ) as raw:
            tenants = await discover_available_tenants(
                token="secret-token",
                client=raw,
            )

        self.assertEqual(captured["path"], "/api/v1/auth/user")
        self.assertEqual(captured["token"], "secret-token")
        self.assertIsNone(captured["tenant_header"])
        self.assertEqual(
            [(tenant.uuid, tenant.name) for tenant in tenants],
            [("tenant-a", "Alpha"), ("tenant-b", "Beta")],
        )

    async def test_settings_request_reads_parameters_from_json_string(self):
        captured = {}

        async def handler(request):
            captured.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "fields": [
                        {"name": "name", "value": "Bitgames.partners"},
                        {
                            "name": "settings",
                            "value": json.dumps(
                                {
                                    "parameters": (
                                        "&sub_id1={{aio.visit.uuid}}"
                                        "&sub_id2={{aio.visit.fields.buyer_utm}}"
                                    )
                                }
                            ),
                        },
                    ]
                },
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=transport, base_url="https://test"
        ) as raw:
            async with AioAdvertiserClient(self.config, client=raw) as client:
                parameters = await client.fetch_url_parameters("advertiser-1")

        self.assertEqual(captured["action"], ADVERTISER_ACTION)
        self.assertEqual(captured["repository"], ADVERTISER_REPOSITORY)
        self.assertEqual(captured["uuids"], ["advertiser-1"])
        self.assertEqual(captured["arguments"], [])
        self.assertIn("{{aio.visit.uuid}}", parameters)

    async def test_null_parameters_mean_an_empty_advertiser_tail(self):
        async def handler(request):
            return httpx.Response(
                200,
                json={
                    "fields": [
                        {
                            "name": "settings",
                            "value": json.dumps({"parameters": None}),
                        }
                    ]
                },
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=transport, base_url="https://test"
        ) as raw:
            async with AioAdvertiserClient(self.config, client=raw) as client:
                parameters = await client.fetch_url_parameters("advertiser-1")

        self.assertEqual(parameters, "")


class StubStore:
    def __init__(self, state=None):
        self.state = state
        self.seeded = ()
        self.commit = None

    async def verify_schema(self):
        return None

    async def seed_catalog(self, rows):
        self.seeded = rows
        return len(rows)

    async def get_state(self, source_key):
        return self.state

    async def commit_sync(self, **kwargs):
        self.commit = kwargs
        return len(kwargs["profiles"]) * len(kwargs["rows"])


class StubClient:
    def __init__(self, pages):
        self.pages = pages
        self.details = []

    async def iter_pages(self):
        for page in self.pages:
            yield page

    async def fetch_url_parameters(self, advertiser_uuid):
        self.details.append(advertiser_uuid)
        return f"click_id={{{{aio.visit.uuid}}}}&source={advertiser_uuid}"


class AioAdvertiserWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.catalog = Path(self.tempdir.name) / "advertisers.json"
        self.catalog.write_text(
            json.dumps(
                {
                    "advertisers": [
                        {
                            "bot_key": "kalisto",
                            "name": "Seed K",
                            "url_parameters": "old=1",
                        },
                        {
                            "bot_key": "mephisto",
                            "name": "Seed M",
                            "url_parameters": "old=2",
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.settings = AdvertiserWorkerSettings(
            aio_token="token",
            profiles=("kalisto", "mephisto"),
            catalog_path=self.catalog,
            detail_delay_seconds=0,
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_tenants_are_mapped_to_their_bot_catalogs(self):
        self.assertEqual(
            TENANT_PROFILES["5b758a0a-6b0f-4c69-9f11-9f084c722324"],
            ("kalisto",),
        )
        self.assertEqual(
            TENANT_PROFILES["1511e872-c02f-419b-a73d-01b7ef9a4eaa"],
            ("kalisto",),
        )
        self.assertEqual(
            TENANT_PROFILES["71bf7226-d6a6-4074-a7bc-ce8e61865e1f"],
            ("mephisto",),
        )
        self.assertNotIn("666", TENANT_PROFILES)

    def test_existing_advertiser_tails_are_refreshed_daily_by_default(self):
        self.assertEqual(self.settings.sync_seconds, 43_200)
        self.assertEqual(self.settings.full_sync_seconds, 86_400)
        self.assertEqual(self.settings.request_poll_seconds, 5)

    async def test_manual_refresh_runs_only_the_requested_profile_tenants(self):
        tenants = [
            SimpleNamespace(
                uuid="5b758a0a-6b0f-4c69-9f11-9f084c722324",
                name="Kalisto tenant",
            ),
            SimpleNamespace(
                uuid="71bf7226-d6a6-4074-a7bc-ce8e61865e1f",
                name="Mephisto tenant",
            ),
        ]
        summary = AdvertiserSyncSummary(
            full=False,
            pages=1,
            listed=1,
            detailed=1,
            database_rows=1,
        )
        client_context = MagicMock()
        client_context.__aenter__ = AsyncMock(return_value=MagicMock())
        client_context.__aexit__ = AsyncMock(return_value=None)
        with (
            patch(
                "app.workers.aio_advertisers.database_conninfo",
                return_value="postgresql://test",
            ),
            patch(
                "app.workers.aio_advertisers.discover_available_tenants",
                new=AsyncMock(return_value=tenants),
            ),
            patch(
                "app.workers.aio_advertisers.AioAdvertiserClient",
                return_value=client_context,
            ),
            patch(
                "app.workers.aio_advertisers.run_cycle",
                new=AsyncMock(return_value=summary),
            ) as mocked_cycle,
        ):
            result = await run_once(self.settings, profiles=("mephisto",))

        self.assertEqual(result.tenants, 1)
        self.assertEqual(mocked_cycle.await_count, 1)
        self.assertEqual(
            mocked_cycle.await_args.kwargs["tenant_id"],
            "71bf7226-d6a6-4074-a7bc-ce8e61865e1f",
        )
        self.assertEqual(mocked_cycle.await_args.kwargs["profiles"], ("mephisto",))

    async def test_first_cycle_is_full_and_updates_both_profiles(self):
        page = AioAdvertiserPage((advertiser(),), False, 60, 59)
        client = StubClient([page])
        store = StubStore()

        summary = await run_cycle(
            self.settings,
            store,
            client,
            tenant_id="tenant-a",
            profiles=("kalisto", "mephisto"),
            now=NOW,
        )

        self.assertTrue(summary.full)
        self.assertEqual(summary.detailed, 1)
        self.assertEqual(summary.database_rows, 2)
        self.assertEqual(len(store.seeded), 2)
        self.assertEqual(client.details, [advertiser().uuid])
        self.assertEqual(store.commit["profiles"], ("kalisto", "mephisto"))
        self.assertEqual(store.commit["source_key"], "aio:tenant-a")

    async def test_incremental_cycle_stops_after_crossing_watermark(self):
        watermark = NOW - timedelta(days=1)
        newer = advertiser("new", "New", NOW)
        older = advertiser("old", "Old", NOW - timedelta(days=2))
        pages = [
            AioAdvertiserPage((newer, older), True, 60, 58),
            AioAdvertiserPage((advertiser("unused"),), False, 60, 57),
        ]
        state = AdvertiserSyncState(
            latest_created_at=watermark,
            last_incremental_at=watermark,
            last_full_at=NOW - timedelta(hours=12),
        )
        client = StubClient(pages)
        store = StubStore(state)

        summary = await run_cycle(
            self.settings,
            store,
            client,
            tenant_id="tenant-a",
            profiles=("kalisto", "mephisto"),
            now=NOW,
        )

        self.assertFalse(summary.full)
        self.assertEqual(summary.pages, 1)
        self.assertEqual(client.details, ["new"])

    async def test_daily_full_cycle_refreshes_existing_advertiser_tail(self):
        existing = advertiser(
            "existing",
            "Existing advertiser",
            NOW - timedelta(days=30),
        )
        state = AdvertiserSyncState(
            latest_created_at=NOW - timedelta(days=1),
            last_incremental_at=NOW - timedelta(hours=12),
            last_full_at=NOW - timedelta(days=1),
        )
        client = StubClient([
            AioAdvertiserPage((existing,), False, 60, 59),
        ])
        store = StubStore(state)

        summary = await run_cycle(
            self.settings,
            store,
            client,
            tenant_id="tenant-a",
            profiles=("kalisto",),
            now=NOW,
        )

        self.assertTrue(summary.full)
        self.assertEqual(client.details, ["existing"])
        self.assertEqual(store.commit["rows"][0].url_parameters,
                         "click_id={{aio.visit.uuid}}&source=existing")
        self.assertTrue(store.commit["full"])


if __name__ == "__main__":
    unittest.main()

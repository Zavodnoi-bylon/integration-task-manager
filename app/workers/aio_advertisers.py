from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.integrations.aio import (
    AioAdvertiserClient,
    AioClientConfig,
    discover_available_tenants,
)
from app.services.aio_advertiser_store import (
    AioAdvertiserStore,
    SeedAdvertiser,
    SyncedAdvertiser,
    database_conninfo,
)


LOG = logging.getLogger(__name__)
ALLOWED_PROFILES = frozenset({"kalisto", "mephisto"})
TENANT_PROFILES = {
    "5b758a0a-6b0f-4c69-9f11-9f084c722324": ("kalisto",),  # AI Buying - G
    "1511e872-c02f-419b-a73d-01b7ef9a4eaa": ("kalisto",),  # Traffic Angels
    "71bf7226-d6a6-4074-a7bc-ce8e61865e1f": ("mephisto",),  # TheRaw Gambling
}


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _positive_float(name: str, default: str) -> float:
    value = float(os.getenv(name, default))
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


@dataclass(frozen=True, slots=True)
class AdvertiserWorkerSettings:
    aio_token: str
    profiles: tuple[str, ...]
    catalog_path: Path
    aio_base_url: str = "https://app.aio.tech"
    sync_seconds: float = 43_200
    full_sync_seconds: float = 86_400
    request_poll_seconds: float = 5
    detail_delay_seconds: float = 1.1
    page_size: int = 100
    max_pages: int = 100

    @classmethod
    def from_env(cls) -> AdvertiserWorkerSettings:
        profiles = tuple(
            dict.fromkeys(
                item.strip()
                for item in os.getenv(
                    "AIO_ADVERTISER_PROFILES", "kalisto,mephisto"
                ).split(",")
                if item.strip()
            )
        )
        if not profiles or any(item not in ALLOWED_PROFILES for item in profiles):
            raise RuntimeError(
                "AIO_ADVERTISER_PROFILES must contain kalisto and/or mephisto"
            )
        path = Path(
            os.getenv(
                "DESTINATION_CATALOG_PATH",
                "content/destination/advertisers.json",
            )
        )
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[2] / path
        return cls(
            aio_token=_required("AIO_TOKEN"),
            profiles=profiles,
            catalog_path=path,
            aio_base_url=os.getenv(
                "AIO_API_BASE_URL", "https://app.aio.tech"
            ).strip(),
            sync_seconds=_positive_float(
                "AIO_ADVERTISER_SYNC_SECONDS", "43200"
            ),
            full_sync_seconds=_positive_float(
                "AIO_ADVERTISER_FULL_SYNC_SECONDS", "86400"
            ),
            request_poll_seconds=_positive_float(
                "AIO_ADVERTISER_REQUEST_POLL_SECONDS", "5"
            ),
            detail_delay_seconds=_positive_float(
                "AIO_ADVERTISER_DETAIL_DELAY_SECONDS", "1.1"
            ),
            page_size=int(os.getenv("AIO_ADVERTISER_PAGE_SIZE", "100")),
            max_pages=int(os.getenv("AIO_ADVERTISER_MAX_PAGES", "100")),
        )


@dataclass(frozen=True, slots=True)
class AdvertiserSyncSummary:
    full: bool
    pages: int
    listed: int
    detailed: int
    database_rows: int
    tenants: int = 1


def load_seed_catalog(
    path: Path,
    profiles: tuple[str, ...],
) -> tuple[SeedAdvertiser, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_rows = payload.get("advertisers")
    if not isinstance(raw_rows, list):
        raise RuntimeError("Destination catalog must contain advertisers")
    result: list[SeedAdvertiser] = []
    for raw in raw_rows:
        if not isinstance(raw, dict) or raw.get("bot_key") not in profiles:
            continue
        policies = raw.get("external_id_policy") or []
        if not isinstance(policies, list) or not all(
            isinstance(policy, dict) for policy in policies
        ):
            raise RuntimeError("Invalid external_id_policy in destination catalog")
        result.append(
            SeedAdvertiser(
                profile_key=str(raw["bot_key"]),
                name=str(raw.get("name") or "").strip(),
                url_parameters=str(raw.get("url_parameters") or "").strip(),
                technician_note=(
                    str(raw.get("technician_note") or "").strip() or None
                ),
                priority=max(0, int(raw.get("priority") or 0)),
                external_id_policy=tuple(dict(policy) for policy in policies),
            )
        )
    if not result or any(not row.name for row in result):
        raise RuntimeError("Destination seed catalog is empty or invalid")
    return tuple(result)


async def run_cycle(
    settings: AdvertiserWorkerSettings,
    store: AioAdvertiserStore,
    client: AioAdvertiserClient,
    *,
    tenant_id: str,
    profiles: tuple[str, ...],
    now: datetime | None = None,
) -> AdvertiserSyncSummary:
    completed_at = now or datetime.now(timezone.utc)
    source_key = f"aio:{tenant_id}"
    await store.verify_schema()
    seeds = load_seed_catalog(settings.catalog_path, profiles)
    await store.seed_catalog(seeds)
    state = await store.get_state(source_key)
    full = (
        state is None
        or state.last_full_at is None
        or completed_at - state.last_full_at
        >= timedelta(seconds=settings.full_sync_seconds)
    )
    watermark = state.latest_created_at if state else None
    advertisers = []
    pages = 0
    listed = 0
    async for page in client.iter_pages():
        pages += 1
        listed += len(page.advertisers)
        for advertiser in page.advertisers:
            if full or watermark is None or advertiser.created_at >= watermark:
                advertisers.append(advertiser)
        if (
            not full
            and watermark is not None
            and page.advertisers
            and min(item.created_at for item in page.advertisers) < watermark
        ):
            break

    detailed: list[SyncedAdvertiser] = []
    for index, advertiser in enumerate(advertisers):
        parameters = await client.fetch_url_parameters(advertiser.uuid)
        detailed.append(SyncedAdvertiser(advertiser, parameters))
        if index + 1 < len(advertisers):
            await asyncio.sleep(settings.detail_delay_seconds)

    latest_created_at = max(
        (advertiser.created_at for advertiser in advertisers),
        default=watermark,
    )
    database_rows = await store.commit_sync(
        source_key=source_key,
        profiles=profiles,
        rows=tuple(detailed),
        latest_created_at=latest_created_at,
        full=full,
        completed_at=completed_at,
    )
    return AdvertiserSyncSummary(
        full=full,
        pages=pages,
        listed=listed,
        detailed=len(detailed),
        database_rows=database_rows,
    )


def _client_config(
    settings: AdvertiserWorkerSettings,
    tenant_id: str,
) -> AioClientConfig:
    return AioClientConfig(
        token=settings.aio_token,
        tenant_id=tenant_id,
        base_url=settings.aio_base_url,
        page_size=settings.page_size,
        max_pages=settings.max_pages,
    )


async def run_once(
    settings: AdvertiserWorkerSettings,
    *,
    profiles: tuple[str, ...] | None = None,
) -> AdvertiserSyncSummary:
    store = AioAdvertiserStore(database_conninfo())
    selected_profiles = frozenset(profiles or settings.profiles)
    if not selected_profiles or not selected_profiles.issubset(settings.profiles):
        raise ValueError("Requested profiles must be enabled for this worker")
    tenants = await discover_available_tenants(
        token=settings.aio_token,
        base_url=settings.aio_base_url,
    )
    summaries: list[AdvertiserSyncSummary] = []
    for tenant in tenants:
        profiles = tuple(
            profile
            for profile in TENANT_PROFILES.get(tenant.uuid, ())
            if profile in selected_profiles
        )
        if not profiles:
            LOG.info("AIO tenant skipped: tenant=%s", tenant.name)
            continue
        async with AioAdvertiserClient(
            _client_config(settings, tenant.uuid)
        ) as client:
            summary = await run_cycle(
                settings,
                store,
                client,
                tenant_id=tenant.uuid,
                profiles=profiles,
            )
        summaries.append(summary)
        LOG.info(
            "AIO tenant advertiser sync complete: tenant=%s profiles=%s full=%s "
            "pages=%d listed=%d detailed=%d database_rows=%d",
            tenant.name,
            ",".join(profiles),
            summary.full,
            summary.pages,
            summary.listed,
            summary.detailed,
            summary.database_rows,
        )
    return AdvertiserSyncSummary(
        full=all(summary.full for summary in summaries),
        pages=sum(summary.pages for summary in summaries),
        listed=sum(summary.listed for summary in summaries),
        detailed=sum(summary.detailed for summary in summaries),
        database_rows=sum(summary.database_rows for summary in summaries),
        tenants=len(summaries),
    )


async def serve(settings: AdvertiserWorkerSettings) -> None:
    store = AioAdvertiserStore(database_conninfo())
    await store.verify_schema()
    loop = asyncio.get_running_loop()
    next_scheduled_at = 0.0
    while True:
        claimed_profiles: tuple[str, ...] = ()
        scheduled = False
        try:
            claimed_profiles = await store.claim_refresh_profiles(
                settings.profiles
            )
            scheduled = loop.time() >= next_scheduled_at
            if not scheduled and not claimed_profiles:
                await asyncio.sleep(
                    min(
                        settings.request_poll_seconds,
                        max(0.1, next_scheduled_at - loop.time()),
                    )
                )
                continue
            summary = await run_once(
                settings,
                profiles=None if scheduled else claimed_profiles,
            )
            if claimed_profiles:
                await store.complete_refresh_profiles(
                    claimed_profiles,
                    succeeded=True,
                )
            LOG.info(
                "AIO advertiser sync complete: trigger=%s tenants=%d full=%s "
                "pages=%d listed=%d detailed=%d database_rows=%d",
                "scheduled" if scheduled else "manual",
                summary.tenants,
                summary.full,
                summary.pages,
                summary.listed,
                summary.detailed,
                summary.database_rows,
            )
        except Exception:
            if claimed_profiles:
                try:
                    await store.complete_refresh_profiles(
                        claimed_profiles,
                        succeeded=False,
                    )
                except Exception:
                    LOG.exception("Unable to record advertiser refresh failure")
            LOG.exception("AIO advertiser sync failed")
            await asyncio.sleep(settings.request_poll_seconds)
        finally:
            if scheduled:
                next_scheduled_at = loop.time() + settings.sync_seconds


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    load_dotenv()
    settings = AdvertiserWorkerSettings.from_env()
    if args.once:
        summary = asyncio.run(run_once(settings))
        print(
            "AIO_ADVERTISER_SYNC_OK "
            f"tenants={summary.tenants} "
            f"full={str(summary.full).lower()} pages={summary.pages} "
            f"listed={summary.listed} detailed={summary.detailed} "
            f"database_rows={summary.database_rows}"
        )
    else:
        asyncio.run(serve(settings))


if __name__ == "__main__":
    main()

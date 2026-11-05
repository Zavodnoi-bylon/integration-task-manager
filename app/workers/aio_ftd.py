from __future__ import annotations

import argparse
import asyncio
import html
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from app.integrations.aio import (
    AioClient,
    AioClientConfig,
    AioConversion,
    AioPayloadError,
)
from app.integrations.telegram import (
    TelegramConfig,
    TelegramDeliveryError,
    TelegramSender,
)
from app.services.aio_alert_store import (
    AioAlertStore,
    PendingAlert,
    database_conninfo,
)

LOG = logging.getLogger(__name__)
DEFAULT_FTD_TYPE_UUID = "d0f58cc5-cdaf-47de-85dc-2e398fd19fd8"
MOSCOW = ZoneInfo("Europe/Moscow")


@dataclass(frozen=True, slots=True)
class AuditSummary:
    started_at: str
    finished_at: str
    pages: int
    rows: int
    zero_revenue_ftd: int
    invalid_rows: int
    duplicate_rows: int
    min_rate_remaining: int | None


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    aio_token: str
    tenant_uuid: str
    tenant_name: str
    ftd_type_uuid: str
    telegram_token: str
    telegram_chat_id: str
    aio_base_url: str = "https://app.aio.tech"
    telegram_base_url: str = "https://api.telegram.org"
    poll_seconds: int = 600
    poll_offset_seconds: int = 120
    overlap_seconds: int = 600
    catchup_chunk_hours: int = 6
    delivery_batch_size: int = 50

    @classmethod
    def from_env(cls) -> "WorkerSettings":
        poll_seconds = _bounded_int("AIO_POLL_SECONDS", 600, 60, 3600)
        poll_offset_seconds = _bounded_int(
            "AIO_POLL_OFFSET_SECONDS",
            min(120, poll_seconds - 1),
            0,
            poll_seconds - 1,
        )
        return cls(
            aio_token=_required_env("AIO_TOKEN"),
            tenant_uuid=_required_env("AIO_TENANT_ID"),
            tenant_name=os.getenv("AIO_TENANT_NAME", "AIO").strip() or "AIO",
            ftd_type_uuid=(
                os.getenv("AIO_FTD_TYPE_UUID", DEFAULT_FTD_TYPE_UUID).strip()
                or DEFAULT_FTD_TYPE_UUID
            ),
            telegram_token=_required_env("AIO_ALERT_BOT_TOKEN"),
            telegram_chat_id=_required_env("AIO_ALERT_CHAT_ID"),
            aio_base_url=os.getenv(
                "AIO_API_BASE_URL", "https://app.aio.tech"
            ).strip(),
            telegram_base_url=os.getenv(
                "TELEGRAM_API_BASE_URL", "https://api.telegram.org"
            ).strip(),
            poll_seconds=poll_seconds,
            poll_offset_seconds=poll_offset_seconds,
            overlap_seconds=_bounded_int(
                "AIO_OVERLAP_SECONDS", 600, 0, 3600
            ),
            catchup_chunk_hours=_bounded_int(
                "AIO_CATCHUP_CHUNK_HOURS", 6, 1, 24
            ),
            delivery_batch_size=_bounded_int(
                "AIO_DELIVERY_BATCH_SIZE", 50, 1, 200
            ),
        )


@dataclass(frozen=True, slots=True)
class CycleSummary:
    initialized: bool
    window_start: str | None
    window_end: str | None
    rows: int
    candidates: int
    queued: int
    sent: int
    failed: int


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def seconds_until_poll(
    now: datetime,
    interval_seconds: int,
    offset_seconds: int,
) -> float:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    if offset_seconds < 0 or offset_seconds >= interval_seconds:
        raise ValueError("offset_seconds must be within the polling interval")
    remainder = (now.timestamp() - offset_seconds) % interval_seconds
    if remainder < 0.001 or interval_seconds - remainder < 0.001:
        return 0.0
    return interval_seconds - remainder


async def audit_window(
    client: AioClient,
    start: datetime,
    end: datetime,
    ftd_type_uuid: str,
) -> tuple[AuditSummary, tuple[AioConversion, ...]]:
    pages = 0
    rows = 0
    invalid_rows = 0
    duplicate_rows = 0
    minimum_remaining: int | None = None
    seen: set[str] = set()
    candidates: list[AioConversion] = []
    async for page in client.iter_pages(start, end, ftd_type_uuid):
        pages += 1
        rows += len(page.rows)
        if page.rate_remaining is not None:
            minimum_remaining = (
                page.rate_remaining
                if minimum_remaining is None
                else min(minimum_remaining, page.rate_remaining)
            )
        for raw_row in page.rows:
            try:
                conversion = AioConversion.from_row(raw_row)
            except AioPayloadError as exc:
                invalid_rows += 1
                LOG.warning("Invalid AIO conversion row skipped: %s", exc)
                continue
            if conversion.conversion_uuid in seen:
                duplicate_rows += 1
                continue
            seen.add(conversion.conversion_uuid)
            if conversion.is_zero_revenue_ftd(ftd_type_uuid):
                candidates.append(conversion)
    summary = AuditSummary(
        started_at=start.astimezone(timezone.utc).isoformat(),
        finished_at=end.astimezone(timezone.utc).isoformat(),
        pages=pages,
        rows=rows,
        zero_revenue_ftd=len(candidates),
        invalid_rows=invalid_rows,
        duplicate_rows=duplicate_rows,
        min_rate_remaining=minimum_remaining,
    )
    return summary, tuple(candidates)


def format_alert(alert: PendingAlert) -> str:
    conversion_time = alert.conversion_at.astimezone(MOSCOW).strftime(
        "%d.%m.%Y %H:%M:%S МСК"
    )
    advertiser = html.escape(alert.advertiser_name or "—")
    currency = html.escape(alert.currency or "")
    revenue = html.escape(alert.revenue)
    return (
        "🚨 <b>FTD без Revenue</b>\n\n"
        f"<b>Destination:</b> {html.escape(alert.destination_name)}\n"
        f"<b>Advertiser:</b> {advertiser}\n"
        f"<b>Tenant:</b> {html.escape(alert.tenant_name)}\n"
        f"<b>Visit UUID:</b> <code>{html.escape(alert.visit_uuid)}</code>\n"
        f"<b>Conversion UUID:</b> <code>{html.escape(alert.conversion_uuid)}</code>\n"
        f"<b>Revenue:</b> {revenue}{(' ' + currency) if currency else ''}\n"
        f"<b>Время конверсии:</b> {conversion_time}"
    )


async def deliver_due(
    store: AioAlertStore,
    sender: TelegramSender,
    batch_size: int,
) -> tuple[int, int]:
    sent = 0
    failed = 0
    for alert in await store.reserve_due(batch_size):
        try:
            message_id = await sender.send(format_alert(alert))
        except TelegramDeliveryError as exc:
            failed += 1
            retry_after = exc.retry_after or min(
                60 * (2 ** min(max(alert.attempts - 1, 0), 6)),
                3600,
            )
            await store.mark_failed(
                alert.tenant_uuid,
                alert.conversion_uuid,
                exc.error_code,
                retry_after,
            )
            LOG.warning(
                "Telegram delivery deferred: conversion_uuid=%s code=%s retry=%ss",
                alert.conversion_uuid,
                exc.error_code,
                retry_after,
            )
        else:
            sent += 1
            await store.mark_sent(
                alert.tenant_uuid,
                alert.conversion_uuid,
                message_id,
            )
    return sent, failed


async def run_cycle(
    settings: WorkerSettings,
    store: AioAlertStore,
    client: AioClient,
    sender: TelegramSender,
    *,
    now: datetime | None = None,
) -> CycleSummary:
    cycle_now = now or datetime.now(timezone.utc)
    sent_before, failed_before = await deliver_due(
        store, sender, settings.delivery_batch_size
    )
    checkpoint, monitoring_started_at, initialized = (
        await store.checkpoint_or_initialize(
            settings.tenant_uuid,
            settings.tenant_name,
            cycle_now,
        )
    )
    if initialized:
        LOG.info(
            "FTD monitor initialized without historical delivery: tenant=%s checkpoint=%s",
            settings.tenant_name,
            checkpoint.isoformat(),
        )
        return CycleSummary(
            True, None, None, 0, 0, 0, sent_before, failed_before
        )

    chunk_end = min(
        cycle_now,
        checkpoint + timedelta(hours=settings.catchup_chunk_hours),
    )
    window_start = max(
        monitoring_started_at,
        checkpoint - timedelta(seconds=settings.overlap_seconds),
    )
    if chunk_end <= window_start:
        return CycleSummary(
            False,
            window_start.isoformat(),
            chunk_end.isoformat(),
            0,
            0,
            0,
            sent_before,
            failed_before,
        )

    summary, candidates = await audit_window(
        client,
        window_start,
        chunk_end,
        settings.ftd_type_uuid,
    )
    queued = await store.commit_poll(
        settings.tenant_uuid,
        settings.tenant_name,
        chunk_end,
        candidates,
    )
    sent_after, failed_after = await deliver_due(
        store, sender, settings.delivery_batch_size
    )
    LOG.info(
        "FTD poll committed: tenant=%s start=%s end=%s rows=%d candidates=%d queued=%d",
        settings.tenant_name,
        window_start.isoformat(),
        chunk_end.isoformat(),
        summary.rows,
        len(candidates),
        queued,
    )
    return CycleSummary(
        False,
        window_start.isoformat(),
        chunk_end.isoformat(),
        summary.rows,
        len(candidates),
        queued,
        sent_before + sent_after,
        failed_before + failed_after,
    )


def _aio_config(settings: WorkerSettings) -> AioClientConfig:
    return AioClientConfig(
        token=settings.aio_token,
        tenant_id=settings.tenant_uuid,
        base_url=settings.aio_base_url,
    )


def _telegram_config(settings: WorkerSettings) -> TelegramConfig:
    return TelegramConfig(
        token=settings.telegram_token,
        chat_id=settings.telegram_chat_id,
        base_url=settings.telegram_base_url,
    )


async def run_once() -> int:
    settings = WorkerSettings.from_env()
    store = AioAlertStore(database_conninfo())
    await store.verify_schema()
    async with AioClient(_aio_config(settings)) as client:
        async with TelegramSender(_telegram_config(settings)) as sender:
            summary = await run_cycle(settings, store, client, sender)
    print(json.dumps(asdict(summary), ensure_ascii=False))
    return 0


async def serve() -> int:
    settings = WorkerSettings.from_env()
    store = AioAlertStore(database_conninfo())
    await store.verify_schema()
    async with AioClient(_aio_config(settings)) as client:
        async with TelegramSender(_telegram_config(settings)) as sender:
            while True:
                delay = seconds_until_poll(
                    datetime.now(timezone.utc),
                    settings.poll_seconds,
                    settings.poll_offset_seconds,
                )
                if delay:
                    LOG.info("Next AIO FTD poll in %.1f seconds", delay)
                    await asyncio.sleep(delay)
                try:
                    await run_cycle(settings, store, client, sender)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    LOG.exception("FTD alert cycle failed: %s", type(exc).__name__)
                    try:
                        await store.record_poll_error(
                            settings.tenant_uuid,
                            type(exc).__name__,
                        )
                    except Exception:
                        LOG.exception("Could not persist FTD cycle error")


async def run_audit(hours: float) -> int:
    if hours <= 0 or hours > 168:
        raise ValueError("audit hours must be greater than 0 and no more than 168")
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    ftd_type_uuid = os.getenv("AIO_FTD_TYPE_UUID", DEFAULT_FTD_TYPE_UUID).strip()
    config = AioClientConfig(
        token=_required_env("AIO_TOKEN"),
        tenant_id=_required_env("AIO_TENANT_ID"),
        base_url=os.getenv("AIO_API_BASE_URL", "https://app.aio.tech").strip(),
    )
    async with AioClient(config) as client:
        summary, _ = await audit_window(client, start, end, ftd_type_uuid)
    print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
    return 2 if summary.invalid_rows else 0


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="AIO FTD zero-revenue audit and Telegram alert worker."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--serve", action="store_true", help="Run the polling loop")
    mode.add_argument("--once", action="store_true", help="Run one live cycle")
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Read AIO and print aggregate counts without DB or Telegram writes",
    )
    parser.add_argument(
        "--hours",
        type=float,
        default=float(os.getenv("AIO_AUDIT_HOURS", "24")),
        help="Dry-run window in hours (default: 24, maximum: 168).",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # AIO authenticates through a query parameter. httpx logs complete request
    # URLs at INFO, which would expose the token even though our own errors do not.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if args.serve:
        coroutine = serve()
    elif args.once:
        coroutine = run_once()
    else:
        coroutine = run_audit(args.hours)
    raise SystemExit(asyncio.run(coroutine))

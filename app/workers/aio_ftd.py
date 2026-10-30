from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from app.integrations.aio import (
    AioClient,
    AioClientConfig,
    AioConversion,
    AioPayloadError,
)

LOG = logging.getLogger(__name__)
DEFAULT_FTD_TYPE_UUID = "d0f58cc5-cdaf-47de-85dc-2e398fd19fd8"


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


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


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


async def run(hours: float) -> int:
    load_dotenv()
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
    parser = argparse.ArgumentParser(
        description="Read-only audit of AIO FTD conversions with zero revenue."
    )
    parser.add_argument(
        "--hours",
        type=float,
        default=float(os.getenv("AIO_AUDIT_HOURS", "24")),
        help="Audit window in hours (default: 24, maximum: 168).",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    raise SystemExit(asyncio.run(run(args.hours)))

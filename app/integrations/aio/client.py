from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

LOG = logging.getLogger(__name__)
TABLE = "MTK\\Conversions"
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
Sleep = Callable[[float], Awaitable[None]]


class AioApiError(RuntimeError):
    pass


class AioResponseError(AioApiError):
    pass


@dataclass(frozen=True, slots=True)
class AioClientConfig:
    token: str
    tenant_id: str
    base_url: str = "https://app.aio.tech"
    timezone: str = "Europe/Moscow"
    page_size: int = 100
    max_pages: int = 100
    timeout_seconds: float = 40.0
    max_attempts: int = 3

    def __post_init__(self) -> None:
        if not self.token.strip():
            raise ValueError("AIO token is required")
        if not self.tenant_id.strip():
            raise ValueError("AIO tenant ID is required")
        if self.page_size < 1 or self.page_size > 100:
            raise ValueError("AIO page size must be between 1 and 100")
        if self.max_pages < 1:
            raise ValueError("AIO max pages must be positive")
        if self.max_attempts < 1:
            raise ValueError("AIO max attempts must be positive")


@dataclass(frozen=True, slots=True)
class AioPage:
    rows: tuple[Mapping[str, Any], ...]
    has_next: bool
    rate_limit: int | None
    rate_remaining: int | None


def _integer_header(headers: Mapping[str, str], name: str) -> int | None:
    value = headers.get(name)
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), 60.0)
            except ValueError:
                pass
    return min(float(2 ** attempt), 10.0)


class AioClient:
    def __init__(
        self,
        config: AioClientConfig,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.config = config
        self._client = client
        self._owns_client = client is None
        self._sleep = sleep

    async def __aenter__(self) -> AioClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url.rstrip("/"),
                timeout=httpx.Timeout(self.config.timeout_seconds),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Tenant-Id": self.config.tenant_id,
                    "User-Agent": "integration-task-manager/aio-ftd-worker",
                },
            )
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
        self._client = None

    def build_payload(
        self,
        start: datetime,
        end: datetime,
        page: int,
        ftd_type_uuid: str,
    ) -> dict[str, Any]:
        if page < 1:
            raise ValueError("page must be positive")
        zone = ZoneInfo(self.config.timezone)
        start_local = start.astimezone(zone)
        end_local = end.astimezone(zone)
        if start_local > end_local:
            raise ValueError("start must not be after end")
        return {
            "request": {
                TABLE: {
                    "table": TABLE,
                    "page": page,
                    "limit": self.config.page_size,
                    "search": "",
                    "sort_key": "conversion_date",
                    "sort_direction": "asc",
                    "analytics_position": 1,
                    "filters": [
                        {
                            "key": "conversion_type_uuid",
                            "values": [ftd_type_uuid],
                        }
                    ],
                    "dates": [
                        start_local.strftime("%Y-%m-%d %H:%M:%S"),
                        end_local.strftime("%Y-%m-%d %H:%M:%S"),
                        self.config.timezone,
                    ],
                    "hide_empty_metrics": False,
                    "hide_bots": True,
                    "unwrap_tree": False,
                    "hide_trash": True,
                    "event_time_attribution": False,
                    "back_fix_attribution": False,
                    "metric_filters": [],
                    "metric_definition": None,
                    "metric_definitions": [],
                }
            }
        }

    async def fetch_page(
        self,
        start: datetime,
        end: datetime,
        page: int,
        ftd_type_uuid: str,
    ) -> AioPage:
        if self._client is None:
            raise RuntimeError("AioClient must be used as an async context manager")
        payload = self.build_payload(start, end, page, ftd_type_uuid)
        response: httpx.Response | None = None
        for attempt in range(self.config.max_attempts):
            try:
                response = await self._client.post(
                    "/api/v1/tables/data",
                    params={"token": self.config.token},
                    json=payload,
                )
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    if response.is_error:
                        raise AioApiError(
                            f"AIO rejected the request: status={response.status_code}"
                        )
                    parsed = self._parse_page(response)
                    if (
                        parsed.rate_remaining is not None
                        and parsed.rate_remaining <= 2
                    ):
                        LOG.warning(
                            "AIO rate limit safety pause: remaining=%d",
                            parsed.rate_remaining,
                        )
                        await self._sleep(60.0)
                    return parsed
                if attempt == self.config.max_attempts - 1:
                    break
            except AioApiError:
                raise
            except httpx.TransportError as exc:
                if attempt == self.config.max_attempts - 1:
                    raise AioApiError("AIO connection failed after retries") from None
            delay = _retry_delay(response, attempt)
            LOG.warning(
                "AIO request retry: page=%d attempt=%d delay=%.1fs status=%s",
                page,
                attempt + 1,
                delay,
                response.status_code if response is not None else "transport",
            )
            await self._sleep(delay)
        status = response.status_code if response is not None else "transport"
        raise AioApiError(f"AIO request failed after retries: status={status}")

    def _parse_page(self, response: httpx.Response) -> AioPage:
        try:
            payload = response.json()
            table = payload[TABLE]
            result = table["response"]
            rows = result["rows"]
            has_next = result["next"]
        except (KeyError, TypeError, ValueError) as exc:
            raise AioResponseError("Unexpected AIO response structure") from exc
        if not isinstance(rows, list) or not isinstance(has_next, bool):
            raise AioResponseError("Unexpected AIO rows or pagination type")
        if not all(isinstance(row, Mapping) for row in rows):
            raise AioResponseError("AIO rows must be objects")
        rate_limit = _integer_header(response.headers, "x-ratelimit-limit")
        rate_remaining = _integer_header(response.headers, "x-ratelimit-remaining")
        if rate_remaining is not None and rate_remaining <= 2:
            LOG.warning(
                "AIO rate limit is nearly exhausted: remaining=%d limit=%s",
                rate_remaining,
                rate_limit,
            )
        return AioPage(tuple(rows), has_next, rate_limit, rate_remaining)

    async def iter_pages(
        self,
        start: datetime,
        end: datetime,
        ftd_type_uuid: str,
    ) -> AsyncIterator[AioPage]:
        for page_number in range(1, self.config.max_pages + 1):
            page = await self.fetch_page(start, end, page_number, ftd_type_uuid)
            yield page
            # AIO may return next=true with an empty page when a table filter has no rows.
            if not page.rows or not page.has_next:
                return
        raise AioResponseError(
            f"AIO pagination exceeded the configured limit of {self.config.max_pages} pages"
        )

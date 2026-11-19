from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from .client import (
    AioApiError,
    AioClientConfig,
    AioResponseError,
    RETRYABLE_STATUS_CODES,
    _integer_header,
    _retry_delay,
)


LOG = logging.getLogger(__name__)
ADVERTISER_TABLE = "MTK\\Settings\\Advertisers"
ADVERTISER_ACTION = "Advertiser\\Create"
ADVERTISER_REPOSITORY = "Eloquent\\AdvertiserRepository"
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class AioTenant:
    uuid: str
    name: str

    @classmethod
    def from_available_tenant(cls, value: Mapping[str, Any]) -> AioTenant:
        tenant_value = value.get("tenant")
        tenant = tenant_value if isinstance(tenant_value, Mapping) else {}
        uuid = str(tenant.get("uuid") or "").strip()
        name = str(tenant.get("name") or uuid).strip()
        if not uuid:
            raise AioResponseError("AIO available tenant UUID is required")
        return cls(uuid=uuid, name=name)


async def discover_available_tenants(
    *,
    token: str,
    base_url: str = "https://app.aio.tech",
    client: httpx.AsyncClient | None = None,
    sleep: Sleep = asyncio.sleep,
    timeout_seconds: float = 40.0,
    max_attempts: int = 3,
) -> tuple[AioTenant, ...]:
    """Return every tenant available to the AIO API token."""

    if not token.strip():
        raise ValueError("AIO token is required")
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    owns_client = client is None
    raw_client = client or httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        timeout=httpx.Timeout(timeout_seconds),
        headers={
            "Accept": "application/json",
            "User-Agent": "integration-task-manager/aio-advertiser-sync",
        },
    )
    response: httpx.Response | None = None
    try:
        for attempt in range(max_attempts):
            try:
                response = await raw_client.get(
                    "/api/v1/auth/user",
                    params={"token": token},
                )
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    if response.is_error:
                        raise AioApiError(
                            "AIO rejected tenant discovery: "
                            f"status={response.status_code}"
                        )
                    break
                if attempt == max_attempts - 1:
                    break
            except AioApiError:
                raise
            except httpx.TransportError:
                if attempt == max_attempts - 1:
                    raise AioApiError(
                        "AIO tenant discovery failed after retries"
                    ) from None
            delay = _retry_delay(response, attempt)
            LOG.warning(
                "AIO tenant discovery retry: attempt=%d delay=%.1fs status=%s",
                attempt + 1,
                delay,
                response.status_code if response is not None else "transport",
            )
            await sleep(delay)
        else:  # pragma: no cover - the loop always exits through break or raise
            raise AioApiError("AIO tenant discovery failed")
        if response is None or response.status_code in RETRYABLE_STATUS_CODES:
            status = response.status_code if response is not None else "transport"
            raise AioApiError(
                f"AIO tenant discovery failed after retries: status={status}"
            )
        try:
            payload = response.json()
            available = payload["available_tenants"]
        except (KeyError, TypeError, ValueError) as exc:
            raise AioResponseError(
                "Unexpected AIO tenant discovery response structure"
            ) from exc
        if not isinstance(available, list) or not all(
            isinstance(item, Mapping) for item in available
        ):
            raise AioResponseError("AIO available_tenants must be a list")
        by_uuid: dict[str, AioTenant] = {}
        for item in available:
            tenant = AioTenant.from_available_tenant(item)
            by_uuid.setdefault(tenant.uuid, tenant)
        if not by_uuid:
            raise AioResponseError("AIO token has no available tenants")
        return tuple(
            sorted(by_uuid.values(), key=lambda item: (item.name.casefold(), item.uuid))
        )
    finally:
        if owns_client:
            await raw_client.aclose()


def _timestamp(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise AioResponseError(f"AIO advertiser {field} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AioResponseError(
            f"AIO advertiser {field} must be an ISO datetime"
        ) from exc
    if parsed.tzinfo is None:
        raise AioResponseError(f"AIO advertiser {field} must include a timezone")
    return parsed


@dataclass(frozen=True, slots=True)
class AioAdvertiser:
    uuid: str
    name: str
    created_at: datetime
    updated_at: datetime
    is_archived: bool

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> AioAdvertiser:
        identity_value = row.get("_identity")
        identity = identity_value if isinstance(identity_value, Mapping) else {}
        uuid = str(row.get("uuid") or identity.get("uuid") or "").strip()
        name = str(identity.get("name") or row.get("name") or "").strip()
        if not uuid or not name:
            raise AioResponseError("AIO advertiser uuid and name are required")
        created = row.get("created_at") or identity.get("created_at")
        updated = row.get("updated_at") or created
        archived = row.get("is_archived", identity.get("is_archived", False))
        if not isinstance(archived, bool):
            raise AioResponseError("AIO advertiser is_archived must be boolean")
        return cls(
            uuid=uuid,
            name=name,
            created_at=_timestamp(created, "created_at"),
            updated_at=_timestamp(updated, "updated_at"),
            is_archived=archived,
        )


@dataclass(frozen=True, slots=True)
class AioAdvertiserPage:
    advertisers: tuple[AioAdvertiser, ...]
    has_next: bool
    rate_limit: int | None
    rate_remaining: int | None


class AioAdvertiserClient:
    """Read-only AIO client for advertiser metadata and URL parameters."""

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

    async def __aenter__(self) -> AioAdvertiserClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url.rstrip("/"),
                timeout=httpx.Timeout(self.config.timeout_seconds),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Tenant-Id": self.config.tenant_id,
                    "User-Agent": "integration-task-manager/aio-advertiser-sync",
                },
            )
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
        self._client = None

    def build_list_payload(self, page: int) -> dict[str, Any]:
        if page < 1:
            raise ValueError("page must be positive")
        return {
            "request": {
                ADVERTISER_TABLE: {
                    "table": ADVERTISER_TABLE,
                    "page": page,
                    "limit": self.config.page_size,
                    "search": "",
                    "sort_key": "created_at",
                    "sort_direction": "desc",
                    "analytics_position": 1,
                    "filters": [],
                    "dates": [],
                    "hide_empty_metrics": False,
                    "hide_bots": True,
                    "unwrap_tree": False,
                    "hide_trash": False,
                    "event_time_attribution": False,
                    "back_fix_attribution": False,
                    "metric_filters": [],
                    "metric_definition": None,
                    "metric_definitions": [],
                }
            }
        }

    async def fetch_page(self, page: int) -> AioAdvertiserPage:
        response = await self._post(
            "/api/v1/tables/data",
            self.build_list_payload(page),
            operation=f"advertiser-page-{page}",
        )
        try:
            payload = response.json()
            result = payload[ADVERTISER_TABLE]["response"]
            rows = result["rows"]
            has_next = result["next"]
        except (KeyError, TypeError, ValueError) as exc:
            raise AioResponseError(
                "Unexpected AIO advertiser list response structure"
            ) from exc
        if not isinstance(rows, list) or not isinstance(has_next, bool):
            raise AioResponseError("Unexpected AIO advertiser pagination type")
        if not all(isinstance(row, Mapping) for row in rows):
            raise AioResponseError("AIO advertiser rows must be objects")
        page_result = AioAdvertiserPage(
            advertisers=tuple(AioAdvertiser.from_row(row) for row in rows),
            has_next=has_next,
            rate_limit=_integer_header(response.headers, "x-ratelimit-limit"),
            rate_remaining=_integer_header(
                response.headers, "x-ratelimit-remaining"
            ),
        )
        await self._respect_rate_limit(page_result.rate_remaining)
        return page_result

    async def iter_pages(self) -> AsyncIterator[AioAdvertiserPage]:
        for page_number in range(1, self.config.max_pages + 1):
            page = await self.fetch_page(page_number)
            yield page
            if not page.advertisers or not page.has_next:
                return
        raise AioResponseError(
            "AIO advertiser pagination exceeded the configured limit of "
            f"{self.config.max_pages} pages"
        )

    async def fetch_url_parameters(self, advertiser_uuid: str) -> str:
        uuid = advertiser_uuid.strip()
        if not uuid:
            raise ValueError("advertiser_uuid is required")
        response = await self._post(
            "/api/v1/actions/data",
            {
                "uuids": [uuid],
                "action": ADVERTISER_ACTION,
                "arguments": [],
                "repository": ADVERTISER_REPOSITORY,
            },
            operation="advertiser-settings",
        )
        try:
            payload = response.json()
            fields = payload["fields"]
            settings_field = next(
                field
                for field in fields
                if isinstance(field, Mapping) and field.get("name") == "settings"
            )
            settings_value = settings_field["value"]
            settings = json.loads(settings_value)
            parameters = settings.get("parameters", "")
        except (KeyError, StopIteration, TypeError, ValueError) as exc:
            raise AioResponseError(
                "Unexpected AIO advertiser settings response structure"
            ) from exc
        if parameters is None:
            parameters = ""
        if not isinstance(parameters, str):
            raise AioResponseError("AIO advertiser parameters must be a string")
        await self._respect_rate_limit(
            _integer_header(response.headers, "x-ratelimit-remaining")
        )
        return parameters.strip()

    async def _post(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        operation: str,
    ) -> httpx.Response:
        if self._client is None:
            raise RuntimeError(
                "AioAdvertiserClient must be used as an async context manager"
            )
        response: httpx.Response | None = None
        for attempt in range(self.config.max_attempts):
            try:
                response = await self._client.post(
                    path,
                    params={"token": self.config.token},
                    json=payload,
                )
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    if response.is_error:
                        raise AioApiError(
                            "AIO rejected the advertiser request: "
                            f"status={response.status_code}"
                        )
                    return response
                if attempt == self.config.max_attempts - 1:
                    break
            except AioApiError:
                raise
            except httpx.TransportError:
                if attempt == self.config.max_attempts - 1:
                    raise AioApiError(
                        "AIO advertiser connection failed after retries"
                    ) from None
            delay = _retry_delay(response, attempt)
            LOG.warning(
                "AIO advertiser request retry: operation=%s attempt=%d "
                "delay=%.1fs status=%s",
                operation,
                attempt + 1,
                delay,
                response.status_code if response is not None else "transport",
            )
            await self._sleep(delay)
        status = response.status_code if response is not None else "transport"
        raise AioApiError(
            f"AIO advertiser request failed after retries: status={status}"
        )

    async def _respect_rate_limit(self, remaining: int | None) -> None:
        if remaining is not None and remaining <= 2:
            LOG.warning(
                "AIO advertiser rate limit safety pause: remaining=%d",
                remaining,
            )
            await self._sleep(60.0)

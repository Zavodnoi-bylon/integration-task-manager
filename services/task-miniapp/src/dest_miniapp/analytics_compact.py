from __future__ import annotations

from typing import Protocol
from urllib.parse import urljoin

import httpx
from pydantic import ValidationError

from .dto import (
    AnalyticsCompactDestinationsOutput,
    AnalyticsCompactSummaryOutput,
)


class CompactAnalyticsError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class CompactAnalyticsClient(Protocol):
    async def summary(
        self,
        *,
        token: str,
        params: list[tuple[str, str]],
    ) -> AnalyticsCompactSummaryOutput: ...

    async def destinations(
        self,
        *,
        token: str,
        params: list[tuple[str, str]],
    ) -> AnalyticsCompactDestinationsOutput: ...


class HttpCompactAnalyticsClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/") + "/"
        self._timeout = httpx.Timeout(timeout_seconds)
        self._transport = transport

    async def summary(
        self,
        *,
        token: str,
        params: list[tuple[str, str]],
    ) -> AnalyticsCompactSummaryOutput:
        payload = await self._get(
            "api/compact/destinations/summary",
            token=token,
            params=params,
        )
        return self._validate(AnalyticsCompactSummaryOutput, payload)

    async def destinations(
        self,
        *,
        token: str,
        params: list[tuple[str, str]],
    ) -> AnalyticsCompactDestinationsOutput:
        payload = await self._get(
            "api/compact/destinations",
            token=token,
            params=params,
        )
        return self._validate(AnalyticsCompactDestinationsOutput, payload)

    async def _get(
        self,
        path: str,
        *,
        token: str,
        params: list[tuple[str, str]],
    ) -> object:
        url = urljoin(self._base_url, path)
        try:
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=self._timeout,
                trust_env=False,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    url,
                    params=params,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "User-Agent": "task-manager-compact-analytics/1",
                    },
                )
        except (httpx.TimeoutException, httpx.NetworkError):
            raise CompactAnalyticsError(
                503, "analytics temporarily unavailable"
            ) from None

        if response.status_code == 200:
            try:
                return response.json()
            except ValueError:
                raise CompactAnalyticsError(
                    503, "analytics returned an invalid response"
                ) from None
        if response.status_code == 401:
            raise CompactAnalyticsError(
                503, "analytics handoff was rejected"
            )
        if response.status_code in {400, 403, 404, 409, 422}:
            details = {
                400: "invalid analytics period or filters",
                403: "operation forbidden",
                404: "resource not found",
                409: "analytics session changed; reopen from the bot",
                422: "invalid analytics filter value",
            }
            raise CompactAnalyticsError(
                response.status_code, details[response.status_code]
            )
        raise CompactAnalyticsError(503, "analytics temporarily unavailable")

    @staticmethod
    def _validate(model_type, payload):  # type: ignore[no-untyped-def]
        try:
            return model_type.model_validate(payload)
        except ValidationError:
            raise CompactAnalyticsError(
                503, "analytics returned an invalid response"
            ) from None

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class TelegramDeliveryError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        *,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    token: str
    chat_id: str
    base_url: str = "https://api.telegram.org"
    timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        if not self.token.strip():
            raise ValueError("Telegram bot token is required")
        if not self.chat_id.strip():
            raise ValueError("Telegram chat ID is required")


class TelegramSender:
    def __init__(
        self,
        config: TelegramConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> TelegramSender:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url.rstrip("/"),
                timeout=httpx.Timeout(self.config.timeout_seconds),
            )
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
        self._client = None

    async def send(self, text: str) -> int:
        if self._client is None:
            raise RuntimeError("TelegramSender must be used as an async context manager")
        try:
            response = await self._client.post(
                f"/bot{self.config.token}/sendMessage",
                json={
                    "chat_id": self.config.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
        except httpx.TransportError as exc:
            raise TelegramDeliveryError("transport_error") from None

        retry_after = _retry_after(response)
        if response.status_code == 429:
            raise TelegramDeliveryError("rate_limited", retry_after=retry_after)
        if response.is_error:
            raise TelegramDeliveryError(f"http_{response.status_code}")
        try:
            payload: dict[str, Any] = response.json()
        except (TypeError, ValueError):
            raise TelegramDeliveryError("invalid_response") from None
        if payload.get("ok") is not True:
            raise TelegramDeliveryError("telegram_rejected")
        try:
            return int(payload["result"]["message_id"])
        except (KeyError, TypeError, ValueError):
            raise TelegramDeliveryError("invalid_response") from None


def _retry_after(response: httpx.Response) -> int | None:
    try:
        payload = response.json()
        value = payload.get("parameters", {}).get("retry_after")
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None

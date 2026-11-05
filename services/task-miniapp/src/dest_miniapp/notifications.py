from __future__ import annotations

from typing import Any

import httpx

from .domain import NotificationKind


class TelegramDeliveryError(RuntimeError):
    pass


class TelegramNotificationDelivery:
    def __init__(
        self,
        bot_token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not bot_token.strip():
            raise ValueError("BOT_TOKEN is required")
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._transport = transport

    async def send(
        self,
        *,
        profile_key: str,
        telegram_user_id: int,
        notification_type: str,
        payload: dict[str, Any],
    ) -> None:
        try:
            kind = NotificationKind(notification_type)
        except ValueError as exc:
            raise TelegramDeliveryError("unsupported notification type") from exc
        text = render_notification(kind, payload)
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=httpx.Timeout(20.0, connect=10.0),
            ) as client:
                response = await client.post(
                    self._url,
                    json={
                        "chat_id": telegram_user_id,
                        "text": text,
                        "disable_web_page_preview": True,
                    },
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TelegramDeliveryError(type(exc).__name__) from exc
        if not isinstance(data, dict) or data.get("ok") is not True:
            raise TelegramDeliveryError("Telegram rejected notification")


def render_notification(
    kind: NotificationKind, payload: dict[str, Any]
) -> str:
    ticket_id = int(payload["ticket_id"])
    reason = str(payload.get("public_reason") or "").strip()
    base = {
        NotificationKind.STAFF_TICKET_OPENED: f"Открыт тикет #{ticket_id}",
        NotificationKind.AUTHOR_PUBLIC_MESSAGE: f"Новое сообщение в тикете #{ticket_id}",
        NotificationKind.STAFF_PUBLIC_MESSAGE: f"Клиент ответил в тикете #{ticket_id}",
        NotificationKind.AUTHOR_REVIEW_REQUESTED: f"Тикет #{ticket_id} ждёт вашей проверки",
        NotificationKind.AUTHOR_REVIEW_REMINDER: f"Напоминание: проверьте тикет #{ticket_id}",
        NotificationKind.STAFF_TICKET_ACCEPTED: f"Клиент принял тикет #{ticket_id}",
        NotificationKind.STAFF_TICKET_RETURNED: f"Тикет #{ticket_id} вернут на доработку",
        NotificationKind.AUTHOR_DESTINATION_COMPLETED: f"Destination #{ticket_id} завершён",
        NotificationKind.STAFF_TICKET_REOPENED: f"Тикет #{ticket_id} возобновлён",
    }[kind]
    if reason and kind in {
        NotificationKind.AUTHOR_PUBLIC_MESSAGE,
        NotificationKind.STAFF_PUBLIC_MESSAGE,
        NotificationKind.STAFF_TICKET_RETURNED,
        NotificationKind.STAFF_TICKET_REOPENED,
    }:
        base += "\n\n" + reason[:3000]
    return base[:4096]

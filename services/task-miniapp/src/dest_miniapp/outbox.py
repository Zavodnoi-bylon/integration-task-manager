from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from .postgres import PostgresDatabase


class NotificationDelivery(Protocol):
    async def send(
        self, *, profile_key: str, telegram_user_id: int,
        notification_type: str, payload: dict[str, Any],
    ) -> None: ...


@dataclass(frozen=True)
class OutboxItem:
    outbox_id: int
    profile_key: str
    telegram_user_id: int
    notification_type: str
    payload: dict[str, Any]
    attempts: int


class PostgresOutboxWorker:
    def __init__(
        self, database: PostgresDatabase, delivery: NotificationDelivery,
        *, profile_key: str, lease_seconds: int = 60, max_attempts: int = 8,
    ) -> None:
        self._database = database
        self._delivery = delivery
        self._profile_key = profile_key
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts

    async def run_once(self, *, limit: int = 25) -> int:
        items = await self._claim(limit)
        for item in items:
            try:
                await self._delivery.send(
                    profile_key=item.profile_key,
                    telegram_user_id=item.telegram_user_id,
                    notification_type=item.notification_type,
                    payload=item.payload,
                )
            except Exception as exc:
                await self._failed(item, type(exc).__name__)
            else:
                await self._sent(item.outbox_id)
        return len(items)

    async def _claim(self, limit: int) -> list[OutboxItem]:
        now = datetime.now(UTC)
        lease = now + timedelta(seconds=self._lease_seconds)
        async with self._database.pool.connection() as connection:
            async with connection.transaction():
                rows = await (await connection.execute(
                    """
                    SELECT o.*,u.telegram_user_id,t.status_code,t.review_cycle AS current_cycle
                    FROM ticketing.outbox o
                    JOIN ticketing.telegram_users u ON u.user_id=o.recipient_user_id
                    LEFT JOIN ticketing.tickets t ON t.ticket_id=o.ticket_id
                                               AND t.profile_key=o.profile_key
                    WHERE o.profile_key=%s AND o.available_at<=%s
                      AND o.attempts<%s AND (
                        o.status IN ('pending','failed') OR
                        (o.status='sending' AND o.lease_expires_at<=%s)
                    )
                    ORDER BY o.available_at,o.outbox_id
                    FOR UPDATE OF o SKIP LOCKED LIMIT %s
                    """, (self._profile_key, now, self._max_attempts, now, limit)
                )).fetchall()
                result: list[OutboxItem] = []
                for row in rows:
                    eligible = (
                        (row["required_status"] is None
                         or row["required_status"] == row["status_code"])
                        and (row["review_cycle"] is None
                             or row["review_cycle"] == row["current_cycle"])
                    )
                    if not eligible:
                        await connection.execute(
                            """UPDATE ticketing.outbox SET status='sent',sent_at=%s,
                               locked_at=NULL,lease_expires_at=NULL,
                               last_error_code='condition_not_met'
                               WHERE outbox_id=%s""", (now, row["outbox_id"])
                        )
                        continue
                    await connection.execute(
                        """UPDATE ticketing.outbox SET status='sending',locked_at=%s,
                           lease_expires_at=%s,attempts=attempts+1
                           WHERE outbox_id=%s""", (now, lease, row["outbox_id"])
                    )
                    result.append(OutboxItem(
                        outbox_id=int(row["outbox_id"]),
                        profile_key=row["profile_key"],
                        telegram_user_id=int(row["telegram_user_id"]),
                        notification_type=row["notification_type"],
                        payload=row["payload"], attempts=int(row["attempts"]) + 1,
                    ))
                return result

    async def _sent(self, outbox_id: int) -> None:
        async with self._database.pool.connection() as connection:
            await connection.execute(
                """UPDATE ticketing.outbox SET status='sent',sent_at=now(),
                   locked_at=NULL,lease_expires_at=NULL,last_error_code=NULL
                   WHERE outbox_id=%s AND status='sending'""", (outbox_id,)
            )

    async def _failed(self, item: OutboxItem, error_code: str) -> None:
        delay = min(3600, 2 ** min(item.attempts, 10))
        async with self._database.pool.connection() as connection:
            await connection.execute(
                """UPDATE ticketing.outbox SET status=%s,available_at=%s,
                   locked_at=NULL,lease_expires_at=NULL,last_error_code=%s
                   WHERE outbox_id=%s AND status='sending'""",
                ("failed", datetime.now(UTC) + timedelta(seconds=delay),
                 error_code[:128], item.outbox_id),
            )

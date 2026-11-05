from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from app.integrations.aio.models import AioConversion


@dataclass(frozen=True, slots=True)
class PendingAlert:
    tenant_uuid: str
    tenant_name: str
    conversion_uuid: str
    visit_uuid: str
    conversion_at: datetime
    destination_name: str
    advertiser_name: str | None
    revenue: str
    currency: str | None
    attempts: int


def database_conninfo() -> str:
    import os

    password = os.getenv("TICKETING_DB_PASSWORD", "")
    if not password:
        raise RuntimeError("TICKETING_DB_PASSWORD is required")
    return make_conninfo(
        host=os.getenv("TICKETING_DB_HOST", "postgres"),
        port=int(os.getenv("TICKETING_DB_PORT", "5432")),
        dbname=os.getenv("TICKETING_DB_NAME", "activity_trial"),
        user=os.getenv("TICKETING_DB_USER", "ticket_bot"),
        password=password,
        application_name="integration-task-manager-aio-ftd",
    )


class AioAlertStore:
    def __init__(self, conninfo: str) -> None:
        self._conninfo = conninfo

    async def _connect(self) -> psycopg.AsyncConnection:
        return await psycopg.AsyncConnection.connect(
            self._conninfo,
            row_factory=dict_row,
            autocommit=False,
        )

    async def verify_schema(self) -> None:
        async with await self._connect() as conn:
            cursor = await conn.execute(
                "SELECT to_regclass('aio_alerts.monitor_state') AS state, "
                "to_regclass('aio_alerts.alert_events') AS events"
            )
            row = await cursor.fetchone()
        if not row or row["state"] is None or row["events"] is None:
            raise RuntimeError(
                "AIO alert schema is not installed; run the additive migration first"
            )

    async def checkpoint_or_initialize(
        self,
        tenant_uuid: str,
        tenant_name: str,
        now: datetime,
    ) -> tuple[datetime, datetime, bool]:
        async with await self._connect() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO aio_alerts.monitor_state (
                    tenant_uuid, tenant_name, checkpoint_at,
                    initialized_at, updated_at, last_success_at
                ) VALUES (%s,%s,%s,now(),now(),now())
                ON CONFLICT (tenant_uuid) DO NOTHING
                RETURNING checkpoint_at, initialized_at
                """,
                (tenant_uuid, tenant_name, now),
            )
            inserted = await cursor.fetchone()
            if inserted:
                return inserted["checkpoint_at"], inserted["initialized_at"], True
            cursor = await conn.execute(
                """
                SELECT checkpoint_at, initialized_at
                FROM aio_alerts.monitor_state
                WHERE tenant_uuid=%s
                """,
                (tenant_uuid,),
            )
            row = await cursor.fetchone()
            if not row:
                raise RuntimeError("AIO checkpoint disappeared during initialization")
            return row["checkpoint_at"], row["initialized_at"], False

    async def commit_poll(
        self,
        tenant_uuid: str,
        tenant_name: str,
        checkpoint_at: datetime,
        conversions: tuple[AioConversion, ...],
    ) -> int:
        inserted = 0
        async with await self._connect() as conn:
            for conversion in conversions:
                cursor = await conn.execute(
                    """
                    INSERT INTO aio_alerts.alert_events (
                        tenant_uuid, tenant_name, conversion_uuid, visit_uuid,
                        conversion_at, destination_uuid, destination_name,
                        advertiser_uuid, advertiser_name, revenue, currency,
                        status, attempts, available_at, created_at, updated_at
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        'pending',0,now(),now(),now()
                    )
                    ON CONFLICT (tenant_uuid, conversion_uuid) DO NOTHING
                    RETURNING conversion_uuid
                    """,
                    (
                        tenant_uuid,
                        tenant_name,
                        conversion.conversion_uuid,
                        conversion.visit_uuid,
                        conversion.conversion_at,
                        conversion.destination_uuid,
                        conversion.destination_name,
                        conversion.advertiser_uuid,
                        conversion.advertiser_name,
                        conversion.revenue,
                        conversion.currency,
                    ),
                )
                if await cursor.fetchone():
                    inserted += 1
            await conn.execute(
                """
                UPDATE aio_alerts.monitor_state
                SET tenant_name=%s,
                    checkpoint_at=GREATEST(checkpoint_at,%s),
                    updated_at=now(),
                    last_success_at=now(),
                    last_error_code=NULL
                WHERE tenant_uuid=%s
                """,
                (tenant_name, checkpoint_at, tenant_uuid),
            )
        return inserted

    async def record_poll_error(self, tenant_uuid: str, error_code: str) -> None:
        async with await self._connect() as conn:
            await conn.execute(
                """
                UPDATE aio_alerts.monitor_state
                SET updated_at=now(), last_error_code=%s
                WHERE tenant_uuid=%s
                """,
                (error_code[:80], tenant_uuid),
            )

    async def reserve_due(self, limit: int) -> tuple[PendingAlert, ...]:
        async with await self._connect() as conn:
            cursor = await conn.execute(
                """
                WITH due AS (
                    SELECT tenant_uuid, conversion_uuid
                    FROM aio_alerts.alert_events
                    WHERE (
                        status IN ('pending','failed') AND available_at <= now()
                    ) OR (
                        status='sending'
                        AND last_attempt_at < now() - interval '10 minutes'
                    )
                    ORDER BY available_at, created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                )
                UPDATE aio_alerts.alert_events AS event
                SET status='sending',
                    attempts=event.attempts+1,
                    last_attempt_at=now(),
                    updated_at=now()
                FROM due
                WHERE event.tenant_uuid=due.tenant_uuid
                  AND event.conversion_uuid=due.conversion_uuid
                RETURNING event.tenant_uuid, event.tenant_name,
                          event.conversion_uuid,
                          event.visit_uuid, event.conversion_at,
                          event.destination_name, event.advertiser_name,
                          event.revenue::text, event.currency, event.attempts
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
        return tuple(PendingAlert(**row) for row in rows)

    async def mark_sent(
        self,
        tenant_uuid: str,
        conversion_uuid: str,
        telegram_message_id: int,
    ) -> None:
        async with await self._connect() as conn:
            await conn.execute(
                """
                UPDATE aio_alerts.alert_events
                SET status='sent', sent_at=now(), updated_at=now(),
                    telegram_message_id=%s, last_error_code=NULL
                WHERE tenant_uuid=%s AND conversion_uuid=%s
                """,
                (telegram_message_id, tenant_uuid, conversion_uuid),
            )

    async def mark_failed(
        self,
        tenant_uuid: str,
        conversion_uuid: str,
        error_code: str,
        retry_after_seconds: int,
    ) -> None:
        delay = max(5, min(retry_after_seconds, 3600))
        available_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
        async with await self._connect() as conn:
            await conn.execute(
                """
                UPDATE aio_alerts.alert_events
                SET status='failed', available_at=%s, updated_at=now(),
                    last_error_code=%s
                WHERE tenant_uuid=%s AND conversion_uuid=%s
                """,
                (available_at, error_code[:80], tenant_uuid, conversion_uuid),
            )

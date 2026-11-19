from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.integrations.aio.advertisers import AioAdvertiser


@dataclass(frozen=True, slots=True)
class SeedAdvertiser:
    profile_key: str
    name: str
    url_parameters: str
    technician_note: str | None
    priority: int
    external_id_policy: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class SyncedAdvertiser:
    advertiser: AioAdvertiser
    url_parameters: str


@dataclass(frozen=True, slots=True)
class AdvertiserSyncState:
    latest_created_at: datetime | None
    last_incremental_at: datetime | None
    last_full_at: datetime | None


def database_conninfo() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        return make_conninfo(
            database_url,
            application_name="integration-task-manager-aio-advertisers",
        )
    password = os.getenv("TICKETING_DB_PASSWORD", "")
    if not password:
        raise RuntimeError("DATABASE_URL or TICKETING_DB_PASSWORD is required")
    return make_conninfo(
        host=os.getenv("TICKETING_DB_HOST", "postgres"),
        port=int(os.getenv("TICKETING_DB_PORT", "5432")),
        dbname=os.getenv("TICKETING_DB_NAME", "activity_trial"),
        user=os.getenv("TICKETING_DB_USER", "ticket_bot"),
        password=password,
        application_name="integration-task-manager-aio-advertisers",
    )


def _catalog_key(profile_key: str, identity: str) -> str:
    return hashlib.sha256(
        f"{profile_key}\0{identity}".encode("utf-8")
    ).hexdigest()[:12]


class AioAdvertiserStore:
    def __init__(self, conninfo: str) -> None:
        self._conninfo = conninfo

    async def _connect(self) -> psycopg.AsyncConnection:
        return await psycopg.AsyncConnection.connect(
            self._conninfo,
            row_factory=dict_row,
            autocommit=False,
        )

    async def verify_schema(self) -> None:
        async with await self._connect() as connection:
            cursor = await connection.execute(
                "SELECT to_regclass('ticketing.advertiser_catalog') AS catalog, "
                "to_regclass('ticketing.aio_advertiser_sync_state') AS state, "
                "to_regclass('ticketing.aio_advertiser_refresh_requests') "
                "AS refresh_requests"
            )
            row = await cursor.fetchone()
        if (
            not row
            or row["catalog"] is None
            or row["state"] is None
            or row["refresh_requests"] is None
        ):
            raise RuntimeError(
                "AIO advertiser schema is not installed; run migrations 0012-0014"
            )

    async def claim_refresh_profiles(
        self,
        profiles: tuple[str, ...],
        *,
        stale_after_seconds: int = 1_800,
    ) -> tuple[str, ...]:
        if not profiles:
            return ()
        async with await self._connect() as connection:
            cursor = await connection.execute(
                """
                WITH candidates AS (
                    SELECT profile_key
                    FROM ticketing.aio_advertiser_refresh_requests
                    WHERE profile_key = ANY(%s)
                      AND completed_at IS NULL
                      AND (
                          started_at IS NULL
                          OR started_at <= now() - make_interval(secs => %s)
                      )
                    ORDER BY requested_at,profile_key
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE ticketing.aio_advertiser_refresh_requests request SET
                    started_at=now(),completed_at=NULL,outcome=NULL
                FROM candidates
                WHERE request.profile_key=candidates.profile_key
                RETURNING request.profile_key
                """,
                (list(profiles), stale_after_seconds),
            )
            rows = await cursor.fetchall()
        return tuple(row["profile_key"] for row in rows)

    async def complete_refresh_profiles(
        self,
        profiles: tuple[str, ...],
        *,
        succeeded: bool,
    ) -> None:
        if not profiles:
            return
        async with await self._connect() as connection:
            await connection.execute(
                """
                UPDATE ticketing.aio_advertiser_refresh_requests SET
                    completed_at=now(),outcome=%s
                WHERE profile_key = ANY(%s)
                  AND started_at IS NOT NULL
                  AND completed_at IS NULL
                """,
                ("succeeded" if succeeded else "failed", list(profiles)),
            )

    async def seed_catalog(self, rows: tuple[SeedAdvertiser, ...]) -> int:
        changed = 0
        async with await self._connect() as connection:
            for row in rows:
                key = _catalog_key(row.profile_key, row.name.casefold())
                cursor = await connection.execute(
                    """
                    INSERT INTO ticketing.advertiser_catalog (
                        profile_key,catalog_key,aio_uuid,name,url_parameters,
                        technician_note,priority,external_id_policy,source,
                        aio_archived,synced_at
                    ) VALUES (%s,%s,NULL,%s,%s,%s,%s,%s,'seed',false,now())
                    ON CONFLICT (profile_key,normalized_name) DO UPDATE SET
                        url_parameters=CASE
                            WHEN ticketing.advertiser_catalog.aio_uuid IS NULL
                            THEN EXCLUDED.url_parameters
                            ELSE ticketing.advertiser_catalog.url_parameters
                        END,
                        technician_note=EXCLUDED.technician_note,
                        priority=EXCLUDED.priority,
                        external_id_policy=EXCLUDED.external_id_policy,
                        synced_at=now()
                    RETURNING catalog_key
                    """,
                    (
                        row.profile_key,
                        key,
                        row.name,
                        row.url_parameters,
                        row.technician_note,
                        row.priority,
                        Jsonb(list(row.external_id_policy)),
                    ),
                )
                if await cursor.fetchone():
                    changed += 1
        return changed

    async def get_state(self, source_key: str) -> AdvertiserSyncState | None:
        async with await self._connect() as connection:
            cursor = await connection.execute(
                """
                SELECT latest_created_at,last_incremental_at,last_full_at
                FROM ticketing.aio_advertiser_sync_state
                WHERE source_key=%s
                """,
                (source_key,),
            )
            row = await cursor.fetchone()
        return AdvertiserSyncState(**row) if row else None

    async def commit_sync(
        self,
        *,
        source_key: str,
        profiles: tuple[str, ...],
        rows: tuple[SyncedAdvertiser, ...],
        latest_created_at: datetime | None,
        full: bool,
        completed_at: datetime,
    ) -> int:
        updated = 0
        async with await self._connect() as connection:
            for profile_key in profiles:
                for row in rows:
                    advertiser = row.advertiser
                    cursor = await connection.execute(
                        """
                        SELECT catalog_key
                        FROM ticketing.advertiser_catalog
                        WHERE profile_key=%s
                          AND (aio_uuid=%s OR normalized_name=lower(btrim(%s)))
                        ORDER BY (aio_uuid=%s) DESC
                        LIMIT 1
                        FOR UPDATE
                        """,
                        (
                            profile_key,
                            advertiser.uuid,
                            advertiser.name,
                            advertiser.uuid,
                        ),
                    )
                    existing = await cursor.fetchone()
                    if existing:
                        await connection.execute(
                            """
                            UPDATE ticketing.advertiser_catalog SET
                                aio_uuid=%s,name=%s,url_parameters=%s,source='aio',
                                aio_created_at=%s,aio_updated_at=%s,
                                aio_archived=%s,last_seen_at=%s,synced_at=%s
                            WHERE profile_key=%s AND catalog_key=%s
                            """,
                            (
                                advertiser.uuid,
                                advertiser.name,
                                row.url_parameters,
                                advertiser.created_at,
                                advertiser.updated_at,
                                advertiser.is_archived,
                                completed_at,
                                completed_at,
                                profile_key,
                                existing["catalog_key"],
                            ),
                        )
                    else:
                        await connection.execute(
                            """
                            INSERT INTO ticketing.advertiser_catalog (
                                profile_key,catalog_key,aio_uuid,name,url_parameters,
                                technician_note,priority,external_id_policy,source,
                                aio_created_at,aio_updated_at,aio_archived,
                                last_seen_at,synced_at
                            ) VALUES (
                                %s,%s,%s,%s,%s,NULL,0,'[]'::jsonb,'aio',
                                %s,%s,%s,%s,%s
                            )
                            """,
                            (
                                profile_key,
                                _catalog_key(profile_key, f"aio:{advertiser.uuid}"),
                                advertiser.uuid,
                                advertiser.name,
                                row.url_parameters,
                                advertiser.created_at,
                                advertiser.updated_at,
                                advertiser.is_archived,
                                completed_at,
                                completed_at,
                            ),
                        )
                    updated += 1
            await connection.execute(
                """
                INSERT INTO ticketing.aio_advertiser_sync_state (
                    source_key,latest_created_at,last_incremental_at,last_full_at,
                    last_success_at,updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (source_key) DO UPDATE SET
                    latest_created_at=CASE
                        WHEN EXCLUDED.latest_created_at IS NULL
                        THEN ticketing.aio_advertiser_sync_state.latest_created_at
                        WHEN ticketing.aio_advertiser_sync_state.latest_created_at IS NULL
                        THEN EXCLUDED.latest_created_at
                        ELSE GREATEST(
                            ticketing.aio_advertiser_sync_state.latest_created_at,
                            EXCLUDED.latest_created_at
                        )
                    END,
                    last_incremental_at=EXCLUDED.last_incremental_at,
                    last_full_at=COALESCE(
                        EXCLUDED.last_full_at,
                        ticketing.aio_advertiser_sync_state.last_full_at
                    ),
                    last_success_at=EXCLUDED.last_success_at,
                    updated_at=EXCLUDED.updated_at
                """,
                (
                    source_key,
                    latest_created_at,
                    completed_at,
                    completed_at if full else None,
                    completed_at,
                    completed_at,
                ),
            )
        return updated

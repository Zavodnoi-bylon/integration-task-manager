from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .gateway import AccessDenied
from .sessions import IssuedSession, SessionRecord, SessionStore, token_hash
from .use_cases import (
    IdempotencyConflict,
    IdempotencyLease,
    IdempotencyReplay,
    IdempotencyReservationPort,
    IdempotencyScope,
    MutationResult,
)


class PostgresDatabase:
    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 8) -> None:
        if not dsn.strip():
            raise ValueError("PostgreSQL DSN is required")
        self.pool = AsyncConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            open=False,
            kwargs={"row_factory": dict_row},
        )

    async def open(self) -> None:
        await self.pool.open(wait=True)

    async def close(self) -> None:
        await self.pool.close()


class PostgresSessionStore(SessionStore):
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def rotate(
        self, *, telegram_user_id: int, profile_key: str, ttl_seconds: int
    ) -> IssuedSession:
        session_id = secrets.token_urlsafe(48)
        csrf_token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        async with self._database.pool.connection() as connection:
            async with connection.transaction():
                row = await (
                    await connection.execute(
                        """
                        SELECT u.user_id
                        FROM ticketing.telegram_users u
                        JOIN ticketing.memberships m USING (user_id)
                        WHERE u.telegram_user_id=%s AND m.profile_key=%s AND m.active
                        FOR UPDATE OF m
                        """,
                        (telegram_user_id, profile_key),
                    )
                ).fetchone()
                if row is None:
                    raise AccessDenied("membership inactive")
                await connection.execute(
                    "DELETE FROM ticketing.miniapp_sessions "
                    "WHERE profile_key=%s AND user_id=%s",
                    (profile_key, row["user_id"]),
                )
                await connection.execute(
                    """
                    INSERT INTO ticketing.miniapp_sessions
                        (session_hash,csrf_hash,profile_key,user_id,expires_at)
                    VALUES (%s,%s,%s,%s,%s)
                    """,
                    (
                        token_hash(session_id),
                        token_hash(csrf_token),
                        profile_key,
                        row["user_id"],
                        expires_at,
                    ),
                )
        return IssuedSession(session_id, csrf_token, expires_at)

    async def get(self, session_id: str) -> SessionRecord | None:
        session_hash = token_hash(session_id)
        async with self._database.pool.connection() as connection:
            async with connection.transaction():
                row = await (
                    await connection.execute(
                        """
                        SELECT s.session_hash,s.csrf_hash,s.profile_key,s.expires_at,
                               u.telegram_user_id
                        FROM ticketing.miniapp_sessions s
                        JOIN ticketing.telegram_users u USING (user_id)
                        WHERE s.session_hash=%s
                        FOR UPDATE OF s
                        """,
                        (session_hash,),
                    )
                ).fetchone()
                if row is None:
                    return None
                if row["expires_at"] <= datetime.now(UTC):
                    await connection.execute(
                        "DELETE FROM ticketing.miniapp_sessions WHERE session_hash=%s",
                        (session_hash,),
                    )
                    return None
                return SessionRecord(
                    session_hash=row["session_hash"],
                    csrf_hash=row["csrf_hash"],
                    telegram_user_id=row["telegram_user_id"],
                    profile_key=row["profile_key"],
                    expires_at=row["expires_at"],
                )

    async def invalidate(self, session_id: str) -> None:
        async with self._database.pool.connection() as connection:
            await connection.execute(
                "DELETE FROM ticketing.miniapp_sessions WHERE session_hash=%s",
                (token_hash(session_id),),
            )


class PostgresIdempotencyReservations(IdempotencyReservationPort):
    def __init__(
        self,
        database: PostgresDatabase,
        *,
        lease_seconds: int = 120,
        poll_seconds: float = 0.1,
    ) -> None:
        if lease_seconds < 10:
            raise ValueError("idempotency lease must be at least 10 seconds")
        self._database = database
        self._lease_seconds = lease_seconds
        self._poll_seconds = poll_seconds

    async def acquire(
        self, *, scope: IdempotencyScope, payload_hash: str
    ) -> IdempotencyLease | IdempotencyReplay:
        while True:
            now = datetime.now(UTC)
            lease_token = UUID(bytes=secrets.token_bytes(16))
            lease_expires_at = now + timedelta(seconds=self._lease_seconds)
            async with self._database.pool.connection() as connection:
                async with connection.transaction():
                    actor_id = await _active_user_id(
                        connection, scope.profile_key, scope.actor_telegram_user_id
                    )
                    row = await (
                        await connection.execute(
                            "SELECT * FROM ticketing.miniapp_idempotency "
                            "WHERE namespace=%s FOR UPDATE",
                            (scope.namespace,),
                        )
                    ).fetchone()
                    if row is None:
                        await connection.execute(
                            """
                            INSERT INTO ticketing.miniapp_idempotency
                                (namespace,profile_key,actor_user_id,operation,
                                 request_uuid,payload_hash,state,lease_token,
                                 lease_expires_at)
                            VALUES (%s,%s,%s,%s,%s,%s,'processing',%s,%s)
                            """,
                            (
                                scope.namespace,
                                scope.profile_key,
                                actor_id,
                                scope.operation,
                                scope.key,
                                payload_hash,
                                lease_token,
                                lease_expires_at,
                            ),
                        )
                        return IdempotencyLease(str(lease_token))
                    if row["payload_hash"] != payload_hash:
                        raise IdempotencyConflict("idempotency payload mismatch")
                    if row["state"] == "completed":
                        return IdempotencyReplay(_mutation_result(row["response"]))
                    if row["state"] == "failed" or row["lease_expires_at"] <= now:
                        await connection.execute(
                            """
                            UPDATE ticketing.miniapp_idempotency
                            SET state='processing',lease_token=%s,lease_expires_at=%s,
                                last_error_code=NULL,updated_at=now()
                            WHERE namespace=%s
                            """,
                            (lease_token, lease_expires_at, scope.namespace),
                        )
                        return IdempotencyLease(str(lease_token))
            await asyncio.sleep(self._poll_seconds)

    async def fail(self, *, scope: IdempotencyScope, lease_token: str) -> None:
        async with self._database.pool.connection() as connection:
            await connection.execute(
                """
                UPDATE ticketing.miniapp_idempotency
                SET state='failed',lease_token=NULL,lease_expires_at=NULL,
                    last_error_code='application_failure',updated_at=now()
                WHERE namespace=%s AND state='processing' AND lease_token=%s
                """,
                (scope.namespace, UUID(lease_token)),
            )


async def _active_user_id(connection, profile_key: str, telegram_user_id: int) -> int:
    row = await (
        await connection.execute(
            """
            SELECT u.user_id
            FROM ticketing.telegram_users u
            JOIN ticketing.memberships m USING (user_id)
            WHERE u.telegram_user_id=%s AND m.profile_key=%s AND m.active
            """,
            (telegram_user_id, profile_key),
        )
    ).fetchone()
    if row is None:
        raise AccessDenied("membership inactive")
    return int(row["user_id"])


def _mutation_result(payload: dict[str, Any] | str) -> MutationResult:
    if isinstance(payload, str):
        payload = json.loads(payload)
    return MutationResult(
        ticket_id=int(payload["ticket_id"]),
        status=payload["status"],
        ticket_type=payload["ticket_type"],
        review_cycle=int(payload["review_cycle"]),
        replayed=True,
    )


def mutation_result_json(result: MutationResult) -> dict[str, Any]:
    payload = asdict(result)
    payload["replayed"] = False
    return payload

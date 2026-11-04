from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol


@dataclass(frozen=True)
class SessionRecord:
    session_hash: str
    csrf_hash: str
    user_id: int
    profile_key: str
    expires_at: datetime


@dataclass(frozen=True)
class IssuedSession:
    session_id: str
    csrf_token: str
    expires_at: datetime


class SessionStore(Protocol):
    async def rotate(self, *, user_id: int, profile_key: str,
                     ttl_seconds: int) -> IssuedSession: ...

    async def get(self, session_id: str) -> SessionRecord | None: ...

    async def invalidate(self, session_id: str) -> None: ...


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def csrf_matches(record: SessionRecord, candidate: str | None) -> bool:
    return bool(candidate) and hmac.compare_digest(
        record.csrf_hash, token_hash(candidate)
    )


class MemorySessionStore:
    """Test/development store. Production must inject a shared store."""

    def __init__(self) -> None:
        self._records: dict[str, SessionRecord] = {}
        self._user_sessions: dict[tuple[str, int], str] = {}
        self._lock = asyncio.Lock()

    async def rotate(self, *, user_id: int, profile_key: str,
                     ttl_seconds: int) -> IssuedSession:
        async with self._lock:
            identity = (profile_key, user_id)
            previous_hash = self._user_sessions.pop(identity, None)
            if previous_hash:
                self._records.pop(previous_hash, None)
            session_id = secrets.token_urlsafe(48)
            csrf_token = secrets.token_urlsafe(32)
            session_hash = token_hash(session_id)
            expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
            self._records[session_hash] = SessionRecord(
                session_hash=session_hash,
                csrf_hash=token_hash(csrf_token),
                user_id=user_id,
                profile_key=profile_key,
                expires_at=expires_at,
            )
            self._user_sessions[identity] = session_hash
            return IssuedSession(session_id, csrf_token, expires_at)

    async def get(self, session_id: str) -> SessionRecord | None:
        session_hash = token_hash(session_id)
        async with self._lock:
            record = self._records.get(session_hash)
            if record is None:
                return None
            if record.expires_at <= datetime.now(UTC):
                self._records.pop(session_hash, None)
                self._user_sessions.pop((record.profile_key, record.user_id), None)
                return None
            return record

    async def invalidate(self, session_id: str) -> None:
        session_hash = token_hash(session_id)
        async with self._lock:
            record = self._records.pop(session_hash, None)
            if record:
                self._user_sessions.pop((record.profile_key, record.user_id), None)

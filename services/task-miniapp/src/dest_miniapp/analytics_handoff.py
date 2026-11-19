from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import uuid4

from .models import Actor
from .settings import Settings, decode_analytics_secret


ANALYTICS_ROLE = "analytics_viewer"


@dataclass(frozen=True)
class AnalyticsHandoff:
    url: str
    expires_at: int


def issue_analytics_handoff(
    settings: Settings,
    actor: Actor,
    *,
    now: int | None = None,
    jti: str | None = None,
) -> AnalyticsHandoff:
    if not settings.analytics_url:
        raise RuntimeError("analytics handoff is disabled")
    assert settings.analytics_issuer is not None
    assert settings.analytics_bot_uuid is not None
    assert settings.analytics_profile_id is not None
    assert settings.analytics_signing_secret is not None

    issued_at = int(time.time()) if now is None else now
    expires_at = issued_at + settings.analytics_handoff_ttl_seconds
    token_id = jti or str(uuid4())
    header = {
        "alg": "HS256",
        "kid": settings.analytics_bot_uuid,
        "typ": "JWT",
    }
    payload = {
        "iss": settings.analytics_issuer,
        "sub": f"telegram:{actor.telegram_user_id}",
        "telegram_user_id": actor.telegram_user_id,
        "bot_uuid": settings.analytics_bot_uuid,
        "profile_id": settings.analytics_profile_id,
        "role": ANALYTICS_ROLE,
        "iat": issued_at,
        "exp": expires_at,
        "jti": token_id,
    }
    signing_input = b".".join(
        (_encode_json(header).encode("ascii"), _encode_json(payload).encode("ascii"))
    )
    signature = hmac.new(
        decode_analytics_secret(settings.analytics_signing_secret),
        signing_input,
        hashlib.sha256,
    ).digest()
    token = f"{signing_input.decode('ascii')}.{_base64url(signature)}"

    target = urlsplit(settings.analytics_url)
    url = urlunsplit(target._replace(fragment=urlencode({"handoff": token})))
    return AnalyticsHandoff(url=url, expires_at=expires_at)


def _encode_json(value: dict[str, object]) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return _base64url(raw)


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

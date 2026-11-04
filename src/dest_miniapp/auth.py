from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from .models import TelegramIdentity


class InitDataError(ValueError):
    """Telegram initData is malformed, stale, or has an invalid signature."""


@dataclass(frozen=True)
class VerifiedInitData:
    identity: TelegramIdentity
    auth_date: int
    query_id: str | None


def verify_init_data(init_data: str, *, bot_token: str, max_age_seconds: int,
                     now: int | None = None) -> VerifiedInitData:
    if not init_data or len(init_data) > 16_384:
        raise InitDataError("invalid initData")
    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    fields: dict[str, str] = {}
    for key, value in pairs:
        if key in fields:
            raise InitDataError("duplicate initData field")
        fields[key] = value
    received_hash = fields.pop("hash", "")
    if len(received_hash) != 64:
        raise InitDataError("invalid initData hash")
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(fields.items())
    )
    secret_key = hmac.new(
        b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256
    ).digest()
    calculated_hash = hmac.new(
        secret_key, data_check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(received_hash.lower(), calculated_hash):
        raise InitDataError("invalid initData signature")
    try:
        auth_date = int(fields["auth_date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InitDataError("invalid auth_date") from exc
    current_time = int(time.time()) if now is None else now
    age = current_time - auth_date
    if age < -30 or age > max_age_seconds:
        raise InitDataError("stale initData")
    try:
        raw_user = json.loads(fields["user"])
        user_id = int(raw_user["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InitDataError("invalid Telegram user") from exc
    if user_id <= 0:
        raise InitDataError("invalid Telegram user")
    first_name = str(raw_user.get("first_name") or "").strip()
    last_name = str(raw_user.get("last_name") or "").strip()
    username_value = raw_user.get("username")
    username = str(username_value).strip() if username_value else None
    display_name = " ".join(part for part in (first_name, last_name) if part)
    if not display_name:
        display_name = f"@{username}" if username else "Telegram user"
    return VerifiedInitData(
        identity=TelegramIdentity(
            user_id=user_id,
            display_name=display_name[:128],
            username=username[:64] if username else None,
        ),
        auth_date=auth_date,
        query_id=fields.get("query_id"),
    )

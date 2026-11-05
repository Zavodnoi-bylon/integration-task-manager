"""Verification of Telegram Mini App initData for the lead dashboard."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


class AuthenticationError(ValueError):
    pass


class AuthorizationError(ValueError):
    pass


class TelegramWebAppAuth:
    def __init__(self, bot_token: str, allowed_users: frozenset[int], max_age: int = 3600):
        if not bot_token or not allowed_users:
            raise RuntimeError("Lead Mini App authentication is not configured")
        self.bot_token = bot_token
        self.allowed_users = allowed_users
        self.max_age = max_age

    def verify(self, init_data: str) -> int:
        if not init_data or len(init_data) > 16384:
            raise AuthenticationError("Invalid Telegram authentication")
        try:
            pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True, max_num_fields=32)
        except (ValueError, UnicodeError):
            raise AuthenticationError("Invalid Telegram authentication") from None
        if len({key for key, _ in pairs}) != len(pairs):
            raise AuthenticationError("Invalid Telegram authentication")
        values = dict(pairs)
        supplied_hash = values.pop("hash", "")
        if len(supplied_hash) != 64:
            raise AuthenticationError("Invalid Telegram authentication")
        check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
        secret = hmac.new(b"WebAppData", self.bot_token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(supplied_hash, expected):
            raise AuthenticationError("Invalid Telegram authentication")
        try:
            auth_date = int(values["auth_date"])
            user = json.loads(values["user"])
            user_id = user["id"]
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            raise AuthenticationError("Invalid Telegram authentication") from None
        now = int(time.time())
        if type(user_id) is not int or user_id <= 0 or auth_date > now + 30 or now - auth_date > self.max_age:
            raise AuthenticationError("Expired Telegram authentication")
        if user_id not in self.allowed_users:
            raise AuthorizationError("Lead access is required")
        return user_id

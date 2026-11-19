from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID


class ConfigurationError(RuntimeError):
    pass


_ISSUER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
_BASE64URL_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def decode_analytics_secret(value: str) -> bytes:
    if not _BASE64URL_PATTERN.fullmatch(value):
        raise ConfigurationError("ANALYTICS_SIGNING_SECRET must be base64url")
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(value + padding)
    except (ValueError, binascii.Error):
        raise ConfigurationError(
            "ANALYTICS_SIGNING_SECRET must be base64url"
        ) from None
    if len(decoded) < 32:
        raise ConfigurationError(
            "ANALYTICS_SIGNING_SECRET must contain at least 32 random bytes"
        )
    return decoded


@dataclass(frozen=True)
class Settings:
    bot_token: str
    profile_key: str
    profile_name: str
    allowed_origin: str
    session_cookie_name: str
    analytics_url: str | None = None
    analytics_issuer: str | None = None
    analytics_bot_uuid: str | None = None
    analytics_profile_id: str | None = None
    analytics_signing_secret: str | None = None
    analytics_allowed_user_ids: frozenset[int] = frozenset()
    analytics_handoff_ttl_seconds: int = 300
    init_data_max_age_seconds: int = 300
    session_ttl_seconds: int = 43_200

    def __post_init__(self) -> None:
        if not self.bot_token:
            raise ConfigurationError("BOT_TOKEN is required")
        if self.profile_key not in {"kalisto", "mephisto"}:
            raise ConfigurationError("Unsupported profile")
        parsed = urlsplit(self.allowed_origin)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment or parsed.username or parsed.password):
            raise ConfigurationError("ALLOWED_ORIGIN must be an HTTPS origin")
        if not self.session_cookie_name or any(
                char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                for char in self.session_cookie_name):
            raise ConfigurationError("Invalid session cookie name")
        analytics_values = (
            self.analytics_url,
            self.analytics_issuer,
            self.analytics_bot_uuid,
            self.analytics_profile_id,
            self.analytics_signing_secret,
        )
        if any(value is not None for value in analytics_values) and not all(
            value is not None and value.strip() for value in analytics_values
        ):
            raise ConfigurationError(
                "Analytics handoff configuration must be complete"
            )
        if self.analytics_url is not None:
            analytics = urlsplit(self.analytics_url)
            if (
                analytics.scheme != "https"
                or not analytics.hostname
                or analytics.username
                or analytics.password
                or analytics.query
                or analytics.fragment
            ):
                raise ConfigurationError(
                    "ANALYTICS_URL must be an HTTPS URL without query or fragment"
                )
            assert self.analytics_issuer is not None
            assert self.analytics_bot_uuid is not None
            assert self.analytics_profile_id is not None
            assert self.analytics_signing_secret is not None
            if not _ISSUER_PATTERN.fullmatch(self.analytics_issuer):
                raise ConfigurationError("Invalid ANALYTICS_ISSUER")
            for name, value in (
                ("ANALYTICS_BOT_UUID", self.analytics_bot_uuid),
                ("ANALYTICS_PROFILE_ID", self.analytics_profile_id),
            ):
                try:
                    parsed = UUID(value)
                except ValueError:
                    raise ConfigurationError(f"Invalid {name}") from None
                if str(parsed) != value:
                    raise ConfigurationError(f"Invalid {name}")
            decode_analytics_secret(self.analytics_signing_secret)
        if any(user_id <= 0 for user_id in self.analytics_allowed_user_ids):
            raise ConfigurationError("Invalid analytics allowed user ID")
        if not 60 <= self.analytics_handoff_ttl_seconds <= 600:
            raise ConfigurationError("Invalid analytics handoff TTL")
        if not 30 <= self.init_data_max_age_seconds <= 900:
            raise ConfigurationError("Invalid initData max age")
        if not 300 <= self.session_ttl_seconds <= 86_400:
            raise ConfigurationError("Invalid session TTL")

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


class ConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    bot_token: str
    profile_key: str
    profile_name: str
    allowed_origin: str
    session_cookie_name: str
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
        if not 30 <= self.init_data_max_age_seconds <= 900:
            raise ConfigurationError("Invalid initData max age")
        if not 300 <= self.session_ttl_seconds <= 86_400:
            raise ConfigurationError("Invalid session TTL")

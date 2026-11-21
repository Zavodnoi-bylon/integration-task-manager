from __future__ import annotations

import base64
import unittest

from dest_miniapp.settings import ConfigurationError, Settings


SECRET = base64.urlsafe_b64encode(b"0" * 32).rstrip(b"=").decode("ascii")
HANDOFF = {
    "analytics_url": "https://explorer.example",
    "analytics_issuer": "theraw-task-manager",
    "analytics_bot_uuid": "11111111-1111-4111-8111-111111111111",
    "analytics_profile_id": "22222222-2222-4222-8222-222222222222",
    "analytics_signing_secret": SECRET,
}


def settings(**overrides) -> Settings:
    values = {
        "bot_token": "token",
        "profile_key": "kalisto",
        "profile_name": "Kalisto",
        "allowed_origin": "https://miniapp.example",
        "session_cookie_name": "kalisto_session",
    }
    values.update(overrides)
    return Settings(**values)


class AnalyticsSettingsTests(unittest.TestCase):
    def test_complete_configuration_is_required(self) -> None:
        with self.assertRaisesRegex(
            ConfigurationError, "Analytics handoff configuration must be complete"
        ):
            settings(analytics_url="https://explorer.example")

    def test_target_rejects_query_and_fragment(self) -> None:
        common = {
            "analytics_issuer": "kalisto-task-manager",
            "analytics_bot_uuid": "11111111-1111-4111-8111-111111111111",
            "analytics_profile_id": "22222222-2222-4222-8222-222222222222",
            "analytics_signing_secret": SECRET,
        }
        for url in (
            "https://explorer.example/?source=user",
            "https://explorer.example/#existing",
        ):
            with self.subTest(url=url), self.assertRaisesRegex(
                ConfigurationError, "without query or fragment"
            ):
                settings(analytics_url=url, **common)

    def test_secret_requires_at_least_256_bits(self) -> None:
        short_secret = base64.urlsafe_b64encode(b"short").rstrip(b"=").decode("ascii")
        with self.assertRaisesRegex(ConfigurationError, "at least 32 random bytes"):
            settings(
                analytics_url="https://explorer.example",
                analytics_issuer="kalisto-task-manager",
                analytics_bot_uuid="11111111-1111-4111-8111-111111111111",
                analytics_profile_id="22222222-2222-4222-8222-222222222222",
                analytics_signing_secret=short_secret,
            )

    def test_ttl_is_bounded(self) -> None:
        for ttl in (59, 601):
            with self.subTest(ttl=ttl), self.assertRaisesRegex(
                ConfigurationError, "Invalid analytics handoff TTL"
            ):
                settings(analytics_handoff_ttl_seconds=ttl)

    def test_allowed_user_ids_must_be_positive(self) -> None:
        with self.assertRaisesRegex(
            ConfigurationError, "Invalid analytics allowed user ID"
        ):
            settings(analytics_allowed_user_ids=frozenset({0, 42}))

    def test_compact_analytics_is_disabled_by_default(self) -> None:
        configured = settings()
        self.assertFalse(configured.analytics_compact_enabled)
        self.assertIsNone(configured.analytics_compact_base_url)

    def test_compact_analytics_requires_handoff_base_url_and_allowlist(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "base URL"):
            settings(
                analytics_compact_enabled=True,
                analytics_allowed_user_ids=frozenset({42}),
                **HANDOFF,
            )
        with self.assertRaisesRegex(ConfigurationError, "explicit user allowlist"):
            settings(
                analytics_compact_enabled=True,
                analytics_compact_base_url="https://explorer.example",
                **HANDOFF,
            )
        configured = settings(
            analytics_compact_enabled=True,
            analytics_compact_base_url="https://explorer.example",
            analytics_allowed_user_ids=frozenset({42}),
            **HANDOFF,
        )
        self.assertTrue(configured.analytics_compact_enabled)

    def test_compact_base_url_rejects_insecure_or_user_controlled_parts(self) -> None:
        for url in (
            "http://explorer.example",
            "https://user:pass@explorer.example",
            "https://explorer.example?tenant=theraw",
        ):
            with self.subTest(url=url), self.assertRaisesRegex(
                ConfigurationError, "ANALYTICS_COMPACT_BASE_URL"
            ):
                settings(analytics_compact_base_url=url)


if __name__ == "__main__":
    unittest.main()

import hashlib
import hmac
import json
import time
import unittest
from urllib.parse import urlencode
from unittest.mock import patch

from activity.telegram_webapp_auth import AuthenticationError, AuthorizationError, TelegramWebAppAuth
from activity.ticket_analytics import TicketAnalyticsRepository


def signed(token: str, user_id: int, auth_date: int | None = None) -> str:
    values = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "test-query",
        "user": json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


class TelegramWebAppAuthTests(unittest.TestCase):
    def setUp(self):
        self.token = "123456:test-token"
        self.auth = TelegramWebAppAuth(self.token, frozenset({42}))

    def test_accepts_current_signed_allowed_user(self):
        self.assertEqual(self.auth.verify(signed(self.token, 42)), 42)

    def test_rejects_tampering_expiry_and_unknown_user(self):
        with self.assertRaises(AuthenticationError):
            self.auth.verify(signed(self.token, 42).replace("test-query", "changed"))
        with self.assertRaises(AuthenticationError):
            self.auth.verify(signed(self.token, 42, int(time.time()) - 4000))
        with self.assertRaises(AuthorizationError):
            self.auth.verify(signed(self.token, 99))

    def test_rejects_duplicate_fields_and_boolean_user_id(self):
        with self.assertRaises(AuthenticationError):
            self.auth.verify(signed(self.token, 42) + "&auth_date=1")
        raw = signed(self.token, 42)
        with self.assertRaises(AuthenticationError):
            self.auth.verify(raw.replace("%3A42", "%3Atrue"))


class TicketAnalyticsValidationTests(unittest.TestCase):
    def test_allows_only_fixed_periods_and_profiles(self):
        for period in (7, 30, 90):
            for profile in ("all", "kalisto", "mephisto"):
                TicketAnalyticsRepository.validate(period, profile)
        for period, profile in ((1, "all"), (30, "kalisto' OR true--"), (30, "default")):
            with self.assertRaises(ValueError):
                TicketAnalyticsRepository.validate(period, profile)

    def test_configuration_forces_read_only_transactions(self):
        env = {"DB_HOST": "postgres", "DB_NAME": "activity_trial", "DB_USER": "activity_reader",
               "DB_PASSWORD": "secret", "DB_PORT": "5432"}
        with patch.dict("os.environ", env, clear=True):
            config = TicketAnalyticsRepository()._configuration()
        self.assertEqual(config["user"], "activity_reader")
        self.assertIn("default_transaction_read_only=on", config["options"])


if __name__ == "__main__":
    unittest.main()

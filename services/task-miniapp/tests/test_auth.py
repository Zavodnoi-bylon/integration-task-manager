from __future__ import annotations

import hashlib
import hmac
import json
import time
import unittest
from urllib.parse import urlencode

from dest_miniapp.auth import InitDataError, verify_init_data


TOKEN = "123456:test-token"


def signed_init_data(
    *, token: str = TOKEN, user_id: int = 42, auth_date: int | None = None
) -> str:
    fields = {
        "auth_date": str(int(time.time()) if auth_date is None else auth_date),
        "query_id": "AAE-test",
        "user": json.dumps(
            {"id": user_id, "first_name": "Test", "username": "tester"},
            separators=(",", ":"),
        ),
    }
    check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class InitDataTests(unittest.TestCase):
    def test_accepts_valid_payload(self) -> None:
        result = verify_init_data(
            signed_init_data(), bot_token=TOKEN, max_age_seconds=300
        )
        self.assertEqual(result.identity.telegram_user_id, 42)
        self.assertEqual(result.identity.display_name, "Test")

    def test_rejects_tampered_payload(self) -> None:
        payload = signed_init_data().replace("tester", "attacker")
        with self.assertRaises(InitDataError):
            verify_init_data(payload, bot_token=TOKEN, max_age_seconds=300)

    def test_rejects_stale_payload(self) -> None:
        payload = signed_init_data(auth_date=int(time.time()) - 301)
        with self.assertRaises(InitDataError):
            verify_init_data(payload, bot_token=TOKEN, max_age_seconds=300)

    def test_rejects_other_bot_token(self) -> None:
        with self.assertRaises(InitDataError):
            verify_init_data(
                signed_init_data(token="other-token"),
                bot_token=TOKEN,
                max_age_seconds=300,
            )

    def test_rejects_duplicate_fields(self) -> None:
        with self.assertRaises(InitDataError):
            verify_init_data(
                signed_init_data() + "&auth_date=1",
                bot_token=TOKEN,
                max_age_seconds=300,
            )

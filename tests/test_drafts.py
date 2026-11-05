import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from app.handlers.drafts import forwarded_origin_metadata


class DraftHandlerTests(unittest.TestCase):
    def test_forward_origin_user_is_reduced_to_safe_metadata(self):
        origin = SimpleNamespace(
            type="user",
            date=datetime(2026, 11, 5, 10, tzinfo=UTC),
            sender_user=SimpleNamespace(username="advertiser", full_name="Advertiser"),
        )

        origin_type, name, date = forwarded_origin_metadata(origin)

        self.assertEqual(origin_type, "user")
        self.assertEqual(name, "@advertiser")
        self.assertEqual(date, origin.date)

    def test_hidden_forward_origin_keeps_display_name(self):
        origin = SimpleNamespace(
            type="hidden_user",
            date=datetime(2026, 11, 5, 10, tzinfo=UTC),
            sender_user=None,
            sender_user_name="Hidden advertiser",
            chat=None,
        )

        self.assertEqual(
            forwarded_origin_metadata(origin)[1], "Hidden advertiser"
        )


if __name__ == "__main__":
    unittest.main()

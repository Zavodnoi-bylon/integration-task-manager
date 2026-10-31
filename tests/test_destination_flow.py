import unittest

from app.handlers.client_tickets import _parse_destination_cap
from app.views.menus import ticket_created


class DestinationFlowTests(unittest.TestCase):
    def test_cap_accepts_only_positive_plain_integers(self):
        self.assertEqual(_parse_destination_cap("10"), 10)
        self.assertEqual(_parse_destination_cap(" 100 "), 100)
        for value in ("", "0", "-10", "10.5", "10 FTD", "10000000"):
            self.assertIsNone(_parse_destination_cap(value))

    def test_client_confirmation_does_not_disclose_internal_processing(self):
        text, _ = ticket_created(123)

        self.assertIn("Задача зарегистрирована", text)
        self.assertNotIn("модел", text.casefold())
        self.assertNotIn("автомат", text.casefold())
        self.assertNotIn("openrouter", text.casefold())


if __name__ == "__main__":
    unittest.main()

import unittest
import inspect

from aiogram import Bot

from app.bootstrap import build_dispatcher
from app.settings import Settings


class ArchitectureTests(unittest.TestCase):
    def test_main_is_a_thin_entrypoint_without_global_bot(self):
        import main

        source_lines = inspect.getsource(main).splitlines()
        self.assertLessEqual(len(source_lines), 10)
        self.assertFalse(hasattr(main, "bot"))
        self.assertFalse(hasattr(main, "dp"))

    def test_all_feature_routers_are_registered(self):
        settings = Settings(
            bot_token="123456:TEST_TOKEN",
            admin_users=frozenset({200}),
            admin_message=200,
            bot_key="kalisto",
        )
        bot = Bot(token=settings.bot_token)
        dispatcher = build_dispatcher(bot, settings)
        self.assertEqual(
            [router.name for router in dispatcher.sub_routers],
            [
                "common",
                "postbacks",
                "client_tickets",
                "ticket_messages",
                "technician_tickets",
                "ticket_actions",
            ],
        )


if __name__ == "__main__":
    unittest.main()

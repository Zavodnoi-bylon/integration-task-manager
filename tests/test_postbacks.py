import unittest

from app import postbacks


class PostbackTemplateTests(unittest.TestCase):
    def test_bot_profiles_expose_only_their_tenants(self):
        self.assertEqual(
            postbacks.get_tenants("kalisto"),
            {
                "traffic_angels": "Traffic Angels",
                "ai_buying_g": "AI Buying - G",
            },
        )
        self.assertEqual(
            postbacks.get_tenants("mephisto"),
            {"the_raw": "TheRaw"},
        )

    def test_all_configured_templates_exist_and_are_plain_text(self):
        for bot_key, tenants in postbacks.BOT_TENANTS.items():
            for tenant_key in tenants:
                template = postbacks.load_template(bot_key, tenant_key)
                self.assertIn("https://app.aio.tech/", template)
                self.assertNotIn("](", template)
                self.assertNotIn("\\&", template)
                for line in template.splitlines():
                    if line.startswith("https://"):
                        self.assertEqual(line.count("{"), line.count("}"))

    def test_cross_profile_template_is_rejected(self):
        with self.assertRaises(KeyError):
            postbacks.load_template("mephisto", "traffic_angels")


if __name__ == "__main__":
    unittest.main()

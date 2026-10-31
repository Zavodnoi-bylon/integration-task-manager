import json
import tempfile
import unittest
from pathlib import Path

import httpx

from app.services.destination_service import (
    DestinationFormatterError,
    DestinationService,
    normalize_percent_braces,
)


class DestinationServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.prompt = Path(self.tempdir.name) / "prompt.md"
        self.prompt.write_text("FULL AIO PROMPT", encoding="utf-8")

    def tearDown(self):
        self.tempdir.cleanup()

    async def test_formats_offer_without_extra_user_confirmation(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["authorization"] = request.headers["authorization"]
            seen["payload"] = json.loads(request.content)
            return httpx.Response(200, json={
                "id": "generation-1",
                "model": "test-model",
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": (
                        "**Wrong advertiser | Slotoro | FB Apps | LT | Slots | "
                        "150 USD | cap 40 FTD | Joker Stoker #24443**\n\n"
                        "**Destination**\n```text\n"
                        "https://example.test/?click_id=%7Bclick_id%7D\n```"
                    )},
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            })

        service = DestinationService(
            api_key="secret-key",
            model="test-model",
            prompt_path=str(self.prompt),
            transport=httpx.MockTransport(handler),
        )
        result = await service.format_offer(
            advertiser="V.Partners",
            source_body="Slotoro - FB apps",
        )

        self.assertEqual(
            result.text,
            "V.Partners | Slotoro | FB Apps | LT | Slots | 150 USD | "
            "cap 40 FTD | Joker Stoker #24443\n\n"
            "Destination:\nhttps://example.test/?click_id={click_id}",
        )
        self.assertEqual(result.prompt_tokens, 100)
        self.assertEqual(seen["authorization"], "Bearer secret-key")
        messages = seen["payload"]["messages"]
        self.assertEqual(messages[0]["content"], "FULL AIO PROMPT")
        self.assertIn("V.Partners", messages[2]["content"])
        self.assertIn("Slotoro - FB apps", messages[3]["content"])

    async def test_rejects_incomplete_model_output(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": "текст без Destination"},
                }],
            })

        service = DestinationService(
            api_key="secret-key",
            model="test-model",
            prompt_path=str(self.prompt),
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(DestinationFormatterError):
            await service.format_offer(
                advertiser="V.Partners",
                source_body="Offer",
            )

    async def test_http_error_does_not_leak_api_key(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "secret-key"})

        service = DestinationService(
            api_key="secret-key",
            model="test-model",
            prompt_path=str(self.prompt),
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(DestinationFormatterError) as caught:
            await service.format_offer(
                advertiser="V.Partners",
                source_body="Offer",
            )
        self.assertIn("401", str(caught.exception))
        self.assertNotIn("secret-key", str(caught.exception))

    def test_only_percent_encoded_braces_are_decoded(self):
        self.assertEqual(
            normalize_percent_braces(
                "https://x.test/?id=%7Bclick%7D&encoded=%26%3D%2F"
            ),
            "https://x.test/?id={click}&encoded=%26%3D%2F",
        )


if __name__ == "__main__":
    unittest.main()

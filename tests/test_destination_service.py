import json
import tempfile
import unittest
from pathlib import Path

import httpx

from app.domain import ExternalIdentifierPolicy
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
                    "message": {"content": json.dumps({"results": [{
                        "naming": (
                            "V.Partners | Slotoro | FB Apps | LT | Slots | "
                            "150 USD | cap 40 FTD | Joker Stoker #24443"
                        ),
                        "destination_url": "https://example.test/?offer=7",
                        "warnings": [],
                    }]})},
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
            source_body=(
                "Slotoro - FB apps\n"
                "https://example.test/?offer=7&click_id={click_id}"
            ),
            cap=40,
            url_parameters=(
                "click_id={{aio.visit.uuid}}&"
                "sub_id={{aio.visit.fields.buyer_utm}}"
            ),
        )

        self.assertEqual(
            result.text,
            "V.Partners | Slotoro | FB Apps | LT | Slots | 150 USD | "
            "cap 40 FTD | Joker Stoker #24443\n\n"
            "Destination:\nhttps://example.test/?offer=7",
        )
        self.assertEqual(result.results[0].url, "https://example.test/?offer=7")
        self.assertEqual(result.prompt_tokens, 100)
        self.assertEqual(seen["authorization"], "Bearer secret-key")
        messages = seen["payload"]["messages"]
        self.assertEqual(messages[0]["content"], "FULL AIO PROMPT")
        self.assertIn("V.Partners", messages[1]["content"])
        self.assertIn("advertiser_url_parameters", messages[1]["content"])
        self.assertIn('"offer_cap": 40', messages[1]["content"])
        self.assertIn("{{aio.visit.uuid}}", messages[1]["content"])
        self.assertIn("Slotoro - FB apps", messages[2]["content"])
        self.assertEqual(
            seen["payload"]["response_format"], {"type": "json_object"}
        )

    async def test_rejects_incomplete_model_output(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": "текст без JSON"},
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
                source_body="Offer\nhttps://example.test/?offer=1",
                cap=20,
            )

    async def test_metadata_warning_does_not_reject_a_validated_destination_url(self):
        source_url = (
            "https://luckydfmr.life/casino/slots?p=w1lr&open=register&"
            "sub1={clickid}&sub2={IDвеба}"
        )
        result_url = "https://luckydfmr.life/casino/slots?p=w1lr&open=register"
        naming = (
            "Lucky Star | Lucky Star [ownplatform] | IN | "
            "cross low_creo| 300 | 18$"
        )

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": json.dumps({"results": [{
                        "naming": naming,
                        "destination_url": result_url,
                        "warnings": ["conflicting_geo"],
                    }]})},
                }],
            })

        service = DestinationService(
            api_key="secret-key",
            model="test-model",
            prompt_path=str(self.prompt),
            transport=httpx.MockTransport(handler),
        )
        result = await service.format_offer(
            advertiser="Lucky Star",
            source_body=(
                f"Naming: {naming}\nGEO: ВR\nRate: 18 $\nCap: 30\n"
                f"Landing URL: {source_url}"
            ),
            cap=30,
            url_parameters=(
                "&sub1={{aio.visit.uuid}}&"
                "sub2={{aio.visit.fields.buyer_utm}}"
            ),
            expected_source_urls=(source_url,),
        )

        self.assertEqual(result.results[0].url, result_url)
        self.assertEqual(result.results[0].warnings, ("conflicting_geo",))

    async def test_explicit_naming_may_differ_from_selected_advertiser(self):
        source_url = (
            "https://track.spartaaffiliates.com/visit/?bta=36940&nci=5825&"
            "afp10=Facebook&sub_id_10={external_id}"
        )
        result_url = (
            "https://track.spartaaffiliates.com/visit/?bta=36940&nci=5825&"
            "afp10=Facebook&sub_id_10={{aio.visit.uuid}}"
        )
        naming = "Reconnect | Betveo | FB | CO | Slots | 10$ | cap 30"

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": json.dumps({"results": [{
                        "naming": naming,
                        "destination_url": result_url,
                        "warnings": [],
                    }]})},
                }],
            })

        service = DestinationService(
            api_key="secret-key",
            model="test-model",
            prompt_path=str(self.prompt),
            transport=httpx.MockTransport(handler),
        )
        result = await service.format_offer(
            advertiser="Sparta",
            source_body=(
                f"Advertiser: Sparta\nNaming: {naming}\nGEO: CH\n"
                f"Rate: 250 EUR\nCap: uncap\nLanding URL: {source_url}"
            ),
            cap="uncap",
            url_parameters=(
                "afp1={{aio.visit.uuid}}&"
                "utm_campaign={{aio.visit.fields.buyer_utm}}&"
                "afp2={{aio.visit.fields.fb_ad_name}}"
            ),
            external_id_policy=(ExternalIdentifierPolicy(
                parameter="sub_id_10",
                action="replace_visit_uuid",
            ),),
            expected_source_urls=(source_url,),
        )

        self.assertEqual(result.results[0].naming, naming)
        self.assertEqual(result.results[0].url, result_url)

    async def test_structured_request_keeps_naming_out_of_model_response(self):
        seen = {}
        naming = "1Win | 1Win [own] | IN | Cross | 100/buyer test | 45$"
        source_url = (
            "https://1wxnxg.life/casino/list?open=register&p=hh6t&"
            "t1={ваш_клик}&der={веб}"
        )
        result_url = "https://1wxnxg.life/casino/list?open=register&p=hh6t"

        def handler(request: httpx.Request) -> httpx.Response:
            seen["payload"] = json.loads(request.content)
            return httpx.Response(200, json={
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": json.dumps({"results": [{
                        "destination_url": result_url,
                        "warnings": [],
                    }]})},
                }],
            })

        service = DestinationService(
            api_key="secret-key",
            model="test-model",
            prompt_path=str(self.prompt),
            transport=httpx.MockTransport(handler),
        )
        result = await service.format_offer(
            advertiser="1Win",
            source_body=f"Landing URL: {source_url}",
            cap=100,
            cap_mode="lifetime",
            url_parameters=(
                "sub1={{aio.visit.uuid}}&"
                "sub2={{aio.visit.fields.free_param_5}}&"
                "sub3={{aio.visit.fields.fb_ad_name}}"
            ),
            expected_source_urls=(source_url,),
            server_namings=(naming,),
        )

        self.assertEqual(result.results[0].naming, naming)
        self.assertEqual(result.results[0].url, result_url)
        messages = seen["payload"]["messages"]
        self.assertIn('"response_mode": "destination_urls_only"', messages[1]["content"])
        self.assertNotIn('"offer_cap"', messages[1]["content"])
        self.assertEqual(messages[2]["content"], f"Landing URL: {source_url}")
        self.assertNotIn(naming, messages[2]["content"])

    async def test_model_cannot_change_explicit_naming(self):
        source_url = "https://example.test/?offer=7"

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": json.dumps({"results": [{
                        "naming": "Changed | Naming | FB | CO | Slots | 10$ | cap 30",
                        "destination_url": source_url,
                        "warnings": [],
                    }]})},
                }],
            })

        service = DestinationService(
            api_key="secret-key",
            model="test-model",
            prompt_path=str(self.prompt),
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaisesRegex(
            DestinationFormatterError,
            "changed the explicit Naming",
        ):
            await service.format_offer(
                advertiser="Sparta",
                source_body=(
                    "Naming: Reconnect | Betveo | FB | CO | Slots | 10$ | cap 30\n"
                    f"Landing URL: {source_url}"
                ),
                cap="uncap",
                expected_source_urls=(source_url,),
            )

    async def test_unlim_mode_is_sent_as_structured_offer_cap(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["payload"] = json.loads(request.content)
            return httpx.Response(200, json={
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": json.dumps({"results": [{
                        "naming": (
                            "V.Partners | Brand | FB | PL | Slots | 100 USD | uncap"
                        ),
                        "destination_url": "https://example.test/",
                        "warnings": [],
                    }]})},
                }],
            })

        service = DestinationService(
            api_key="secret-key",
            model="test-model",
            prompt_path=str(self.prompt),
            transport=httpx.MockTransport(handler),
        )
        result = await service.format_offer(
            advertiser="V.Partners",
            source_body="Offer\nhttps://example.test/",
            cap="uncap",
            cap_mode="unlim",
        )

        self.assertIn("uncap", result.text)
        self.assertIn(
            '"offer_cap": {',
            seen["payload"]["messages"][1]["content"],
        )
        self.assertIn(
            '"value": null',
            seen["payload"]["messages"][1]["content"],
        )
        self.assertIn(
            '"mode": "unlim"',
            seen["payload"]["messages"][1]["content"],
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
                cap=20,
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

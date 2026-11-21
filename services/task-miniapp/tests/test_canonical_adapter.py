from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace

from dest_miniapp.canonical import CanonicalDestinationFormatterAdapter
from dest_miniapp.use_cases import (
    DestinationFormatRequest,
    DestinationRequestDetails,
)


class RecordingDestinationService:
    def __init__(self) -> None:
        self.kwargs = None

    async def format_offer(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            text=(
                f"{kwargs['server_namings'][0]}\n\nDestination:\n"
                "https://example.test/?offer=1"
            ),
            results=(SimpleNamespace(
                naming=kwargs["server_namings"][0],
                url="https://example.test/?offer=1",
                warnings=(),
            ),),
        )


class CanonicalDestinationFormatterAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_structured_request_sends_only_url_fields_to_model(self) -> None:
        service = RecordingDestinationService()
        adapter = CanonicalDestinationFormatterAdapter(service)
        naming = "1Win | 1Win [own] | IN | Cross | 100/buyer test | 45$"
        details = DestinationRequestDetails(
            naming=naming,
            offer_name="1Win",
            platform="own",
            approach="Cross",
            geo="IN",
            rate=Decimal("45"),
            currency="usd",
            landing_url="https://example.test/?offer=1&t1={click}",
            clickid_subid="t1 = click; der = web",
            additional="Keep offer=1",
        )

        result = await adapter.format(DestinationFormatRequest(
            profile_key="kalisto",
            advertiser_name="1Win",
            suffix="sub1={{aio.visit.uuid}}",
            technician_note=None,
            cap=100,
            cap_mode="lifetime",
            source_body=(
                f"Naming: {naming}\nGEO: IN\nRate: 45 usd\nCap: 100 lifetime\n"
                f"Landing URL: {details.landing_url}"
            ),
            details=details,
        ))

        self.assertEqual(result.results[0].naming, naming)
        self.assertEqual(service.kwargs["server_namings"], (naming,))
        self.assertEqual(service.kwargs["expected_source_urls"], (details.landing_url,))
        self.assertEqual(
            service.kwargs["source_body"],
            "\n".join((
                f"Landing URL: {details.landing_url}",
                "ClickID / SubID: t1 = click; der = web",
                "Additional: Keep offer=1",
            )),
        )
        self.assertNotIn("Naming:", service.kwargs["source_body"])
        self.assertNotIn("Rate:", service.kwargs["source_body"])


if __name__ == "__main__":
    unittest.main()

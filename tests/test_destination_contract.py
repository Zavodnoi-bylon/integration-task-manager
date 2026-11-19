import json
import unittest

from app.domain import ExternalIdentifierPolicy
from app.services.destination_contract import (
    DestinationContractError,
    parse_model_results,
    validate_destination_url,
)


class DestinationContractTests(unittest.TestCase):
    def test_model_result_requires_exact_json_shape(self):
        result = parse_model_results(json.dumps({
            "results": [{
                "naming": "GMA | Brand | FB | BR | Slots | 100 USD | cap 10 FTD",
                "destination_url": "https://example.test/click?offer=1",
                "warnings": [],
            }]
        }))

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].url, "https://example.test/click?offer=1")
        with self.assertRaises(DestinationContractError):
            parse_model_results(json.dumps({"results": [], "extra": True}))

    def test_server_owned_naming_is_not_returned_by_model(self):
        naming = "1Win | 1Win [own] | IN | Cross | 100/buyer test | 45$"
        result = parse_model_results(
            json.dumps({"results": [{
                "destination_url": "https://example.test/click?offer=1",
                "warnings": [],
            }]}),
            server_namings=(naming,),
        )

        self.assertEqual(result[0].naming, naming)
        with self.assertRaises(DestinationContractError):
            parse_model_results(
                json.dumps({"results": [{
                    "naming": naming,
                    "destination_url": "https://example.test/click?offer=1",
                    "warnings": [],
                }]}),
                server_namings=(naming,),
            )

    def test_gma_destination_excludes_advertiser_tail(self):
        source = "https://gmaff.trk2afse.com/click?pid=53&offer_id=629&l=1793984528"

        self.assertEqual(
            validate_destination_url(
                source_url=source,
                result_url=source,
                advertiser_url_parameters=(
                    "sub1={{aio.visit.uuid}}&"
                    "sub2={{aio.visit.fields.buyer_utm}}&"
                    "sub3={{aio.visit.fields.fb_ad_name}}&"
                    "sub4={{aio.visit.fields.fb_ad_set_name}}"
                ),
            ),
            source,
        )

    def test_advertiser_owned_parameter_must_not_be_duplicated(self):
        with self.assertRaisesRegex(
            DestinationContractError, "count changed unexpectedly"
        ):
            validate_destination_url(
                source_url="https://example.test/?offer=1",
                result_url=(
                    "https://example.test/?offer=1&sub1={{aio.visit.uuid}}"
                ),
                advertiser_url_parameters="sub1={{aio.visit.uuid}}",
            )

    def test_only_advertiser_owned_parameters_are_removed(self):
        validate_destination_url(
            source_url=(
                "https://example.test/path?sub_id_1=click&sub_id_2=buyer&"
                "sub_id_3=creative&sub_id_4=adset&sub_id_5=keep"
            ),
            result_url="https://example.test/path?sub_id_5=keep",
            advertiser_url_parameters=(
                "sub_id_1={{aio.visit.uuid}}&"
                "sub_id_2={{aio.visit.fields.buyer_utm}}&"
                "sub_id_3={{aio.visit.fields.fb_ad_name}}&"
                "sub_id_4={{aio.visit.fields.fb_ad_set_name}}"
            ),
        )

    def test_exact_dynamic_placeholders_are_removed_when_advertiser_has_tail(self):
        source = (
            "https://1wxnxg.life/casino/list?open=register&p=hh6t&"
            "t1={ваш_клик}&der={веб}"
        )
        result = "https://1wxnxg.life/casino/list?open=register&p=hh6t"

        self.assertEqual(
            validate_destination_url(
                source_url=source,
                result_url=result,
                advertiser_url_parameters=(
                    "sub1={{aio.visit.uuid}}&"
                    "sub2={{aio.visit.fields.free_param_5}}&"
                    "sub3={{aio.visit.fields.fb_ad_name}}"
                ),
            ),
            result,
        )

    def test_unresolved_placeholder_cannot_be_a_valid_result(self):
        url = "https://example.test/?offer=1&t1={ваш_клик}"
        with self.assertRaisesRegex(
            DestinationContractError, "unresolved placeholder"
        ):
            validate_destination_url(
                source_url=url,
                result_url=url,
                advertiser_url_parameters="sub1={{aio.visit.uuid}}",
            )

    def test_continuation_tail_keeps_prefix_and_removes_complete_tail_parameter(self):
        validate_destination_url(
            source_url=(
                "https://fraglink.test/?serial=1&creative_id=2&"
                "anid={clickid}&tsrc=fb_ads"
            ),
            result_url=(
                "https://fraglink.test/?serial=1&creative_id=2&"
                "anid={{aio.visit.uuid}}"
            ),
            advertiser_url_parameters=(
                "_{{aio.visit.fields.fb_ad_name}}_"
                "{{aio.visit.fields.fb_ad_set_name}}&tsrc=fb_ads"
            ),
        )

    def test_external_identifier_uses_explicit_policy(self):
        validate_destination_url(
            source_url="https://example.test/?offer=1&sub_id_10={external_id}",
            result_url=(
                "https://example.test/?offer=1&sub_id_10={{aio.visit.uuid}}"
            ),
            external_id_policy=(
                ExternalIdentifierPolicy("sub_id_10", "replace_visit_uuid"),
            ),
        )

        with self.assertRaisesRegex(
            DestinationContractError, "No external identifier policy"
        ):
            validate_destination_url(
                source_url=(
                    "https://example.test/?offer=1&sub_id_10={external_id}"
                ),
                result_url=(
                    "https://example.test/?offer=1&sub_id_10={{aio.visit.uuid}}"
                ),
            )

    def test_remove_policy_drops_the_whole_parameter(self):
        validate_destination_url(
            source_url="https://example.test/?offer=1&external={external_id}",
            result_url="https://example.test/?offer=1",
            external_id_policy=(
                ExternalIdentifierPolicy("external", "remove"),
            ),
        )

    def test_base_url_and_unrelated_parameters_cannot_change(self):
        for result in (
            "https://other.test/?offer=1",
            "https://example.test/?offer=2",
        ):
            with self.assertRaises(DestinationContractError):
                validate_destination_url(
                    source_url="https://example.test/?offer=1",
                    result_url=result,
                )


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services.advertiser_catalog import AdvertiserCatalog


class AdvertiserCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.catalog_path = Path(self.tempdir.name) / "advertisers.json"
        self.catalog_path.write_text(json.dumps({
            "advertisers": [
                {
                    "bot_key": "kalisto",
                    "name": "V.Partners",
                    "url_parameters": "click_id={{aio.visit.uuid}}",
                    "external_id_policy": [{
                        "parameter": "sub_id_10",
                        "action": "replace_visit_uuid",
                    }],
                    "priority": 10,
                },
                {
                    "bot_key": "kalisto",
                    "name": "V1VA",
                    "url_parameters": "",
                    "priority": 1,
                    "technician_note": "Выбрать advertiser для нужного GEO",
                },
                {
                    "bot_key": "mephisto",
                    "name": "TheRaw advertiser",
                    "url_parameters": "sub1={{aio.visit.uuid}}",
                    "priority": 100,
                },
            ],
        }), encoding="utf-8")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_catalog_is_bot_scoped_and_searchable(self):
        catalog = AdvertiserCatalog(
            bot_key="kalisto",
            catalog_path=str(self.catalog_path),
        )

        self.assertEqual(catalog.count, 2)
        self.assertEqual(catalog.popular(1)[0].name, "V.Partners")
        self.assertEqual(catalog.search("part")[0].name, "V.Partners")
        self.assertEqual(catalog.search("theraw"), ())
        policy = catalog.search("part")[0].external_id_policy[0]
        self.assertEqual(policy.parameter, "sub_id_10")
        self.assertEqual(policy.action, "replace_visit_uuid")

    def test_v1va_keeps_technician_note_without_url_tail(self):
        catalog = AdvertiserCatalog(
            bot_key="kalisto",
            catalog_path=str(self.catalog_path),
        )

        entry = catalog.search("v1va")[0]
        self.assertEqual(entry.url_parameters, "")
        self.assertEqual(
            entry.technician_note,
            "Выбрать advertiser для нужного GEO",
        )

    def test_duplicate_names_are_rejected_case_insensitively(self):
        self.catalog_path.write_text(json.dumps({
            "advertisers": [
                {"bot_key": "kalisto", "name": "1Win"},
                {"bot_key": "kalisto", "name": "1win"},
            ],
        }), encoding="utf-8")

        with self.assertRaises(ValueError):
            AdvertiserCatalog(
                bot_key="kalisto",
                catalog_path=str(self.catalog_path),
            )

    def test_database_snapshot_replaces_static_rows_without_leaking_suffix(self):
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = [{
            "catalog_key": "abcdef123456",
            "name": "AIO advertiser",
            "url_parameters": "&click_id={{aio.visit.uuid}}",
            "technician_note": None,
            "priority": 20,
            "external_id_policy": [],
        }]
        context = MagicMock()
        context.__enter__.return_value = connection
        context.__exit__.return_value = False

        with patch(
            "app.services.advertiser_catalog.psycopg.connect",
            return_value=context,
        ):
            catalog = AdvertiserCatalog(
                bot_key="kalisto",
                catalog_path=str(self.catalog_path),
                database_url="postgresql://catalog",
            )
            entry = catalog.search("aio")[0]

        self.assertEqual(entry.key, "abcdef123456")
        self.assertEqual(entry.url_parameters, "&click_id={{aio.visit.uuid}}")
        self.assertEqual(catalog.search("v.partners"), ())


if __name__ == "__main__":
    unittest.main()

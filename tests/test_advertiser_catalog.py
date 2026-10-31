import json
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()

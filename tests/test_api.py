import csv
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

API_AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("fastapi", "httpx"))
if API_AVAILABLE:
    from fastapi.testclient import TestClient
    import app as web


@unittest.skipUnless(API_AVAILABLE, "Install requirements including FastAPI/httpx to run HTTP tests")
class ApiTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"ACTIVITY_DEMO": "1"})
        self.environment.start()
        self.client = TestClient(web.app)
        self.query = {"from": "2026-10-05", "to": "2026-10-22"}

    def tearDown(self):
        self.client.close()
        self.environment.stop()
        web.app.dependency_overrides.clear()

    def test_demo_get_routes_and_report_drilldown_agree(self):
        self.assertEqual(self.client.get("/api/health").json(), {"ok": 1, "mode": "demo"})
        self.assertTrue(self.client.get("/api/meta").json()["demo"])
        result = self.client.get("/api/report", params=self.query)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["cache-control"], "no-store")
        summary = result.json()["summary"]
        for metric, field in (("incoming", "incoming"), ("completed", "completed"), ("backlog", "observed_backlog")):
            rows = self.client.get("/api/tasks", params=dict(self.query, metric=metric)).json()
            self.assertEqual(rows["total"], summary[field])
        task = self.client.get("/api/tasks/demo:long-running").json()
        self.assertTrue(task["evidence"])
        self.assertEqual(self.client.get("/api/proposals").json()["total"], 3)

    def test_normal_start_does_not_fall_back_to_demo_and_redacts_configuration(self):
        with patch.dict(os.environ, {"ACTIVITY_DEMO": "0", "DB_HOST": "", "DB_PASSWORD": "never-print-this-secret"}):
            for route in ("/api/health", "/api/meta"):
                result = self.client.get(route)
                self.assertEqual(result.status_code, 503)
                self.assertEqual(result.json()["error"], "configuration_error")
                self.assertNotIn("never-print-this-secret", result.text)
                self.assertNotIn('"demo":true', result.text)

    def test_pending_proposals_show_source_text_before_acceptance_without_affecting_totals(self):
        data = web.DemoRepository().snapshot()
        web.app.dependency_overrides[web.snapshot] = lambda: data
        with_pending = self.client.get("/api/report", params=self.query).json()["summary"]
        candidates = self.client.get("/api/proposals").json()["rows"]
        self.assertEqual(len(candidates), 3)
        accepted_ids = {row["task_id"] for row in data["tasks"]}
        for candidate in candidates:
            self.assertNotIn(candidate["task_id"], accepted_ids)
            self.assertTrue(candidate["evidence_details"])
            self.assertTrue(all(row["text_plain"] and row["revision_hash"] for row in candidate["evidence_details"]))
            self.assertTrue(all("raw_json" not in row for row in candidate["evidence_details"]))
        data["proposals"] = []
        without_pending = self.client.get("/api/report", params=self.query).json()["summary"]
        self.assertEqual({key: value for key, value in with_pending.items() if key != "pending_proposals"},
                         {key: value for key, value in without_pending.items() if key != "pending_proposals"})

    def test_no_write_routes_and_unknown_api_is_json(self):
        self.assertEqual(self.client.post("/api/tasks", json={"title": "do not write"}).status_code, 405)
        for path in ("/api/unknown", "/api", "/api/nested/missing"):
            result = self.client.get(path)
            self.assertEqual(result.status_code, 404)
            self.assertTrue(result.headers["content-type"].startswith("application/json"))

    def test_validation_and_no_silent_page_or_export_truncation(self):
        for params in ({}, dict(self.query, page_size=201), dict(self.query, page=0),
                       dict(self.query, metric="raw-sql"), {"from": "2025-01-01", "to": "2026-10-22"}):
            self.assertEqual(self.client.get("/api/tasks", params=params).status_code, 422)
        first = self.client.get("/api/tasks", params=dict(self.query, page_size=1)).json()
        self.assertEqual(len(first["rows"]), 1)
        self.assertTrue(first["has_more"])
        exported = self.client.get("/api/export/tasks", params=self.query)
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(int(exported.headers["x-export-count"]), first["total"])
        self.assertEqual(len(list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))), first["total"])
        with patch.dict(os.environ, {"ACTIVITY_MAX_EXPORT_ROWS": "1"}):
            result = self.client.get("/api/export/tasks", params=self.query)
            self.assertEqual(result.status_code, 413)
            self.assertNotIn("text/csv", result.headers["content-type"])

    def test_csv_formula_escaping_and_byte_bound(self):
        values = ["=SUM(A1:A2)", " +1", "-cmd", "@x", "\tdata", "\rdata", "\n=1"]
        for value in values:
            self.assertEqual(web.csv_cell(value), "'" + value)
        self.assertEqual(web.csv_cell(1.5), 1.5)
        self.assertEqual(web.csv_cell("ordinary text"), "ordinary text")
        with self.assertRaises(web.HTTPException) as raised:
            web.encode_csv([{"title": "я" * 1000}], max_rows=10, max_bytes=100)
        self.assertEqual(raised.exception.status_code, 413)

    def test_static_files_cannot_escape_dist_via_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            dist = root / "dist"
            dist.mkdir()
            (dist / "index.html").write_text("<main>preview</main>")
            (root / "private.txt").write_text("must-never-serve")
            (dist / "escape.txt").symlink_to(root / "private.txt")
            with patch.object(web, "DIST", dist):
                self.assertEqual(self.client.get("/").status_code, 200)
                result = self.client.get("/escape.txt")
                self.assertEqual(result.status_code, 404)
                self.assertNotIn("must-never-serve", result.text)
                self.assertEqual(self.client.get("/api/unknown").status_code, 404)


if __name__ == "__main__":
    unittest.main()

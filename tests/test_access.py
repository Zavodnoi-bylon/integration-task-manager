import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import app as web
from activity import access


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "ACTIVITY_DEMO": "1", "ACTIVITY_PROXY_TOKEN": "test-proxy-secret-" * 3,
            "ACTIVITY_AUTH_ROLES": json.dumps({"owner": "admin", "viver": "viewer", "lead": "tech_lead"}),
            "ACTIVITY_TECH_LEAD_PANELS": "[]", "ACTIVITY_UI_STATE_DIR": self.folder.name,
        })
        self.env.start()
        self.client = TestClient(web.app)
        self.query = {"from": "2026-10-05", "to": "2026-10-22"}

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.folder.cleanup()

    def headers(self, user):
        return {"x-activity-user": user, "x-activity-proxy-token": os.environ["ACTIVITY_PROXY_TOKEN"]}

    def test_identity_cannot_be_spoofed_and_unknown_users_are_denied(self):
        for headers in ({}, {"x-activity-user": "owner"}, {"x-activity-user": "owner", "x-activity-proxy-token": "wrong"}):
            self.assertEqual(self.client.get("/api/report", params=self.query, headers=headers).status_code, 401)
        self.assertEqual(self.client.get("/api/access", headers=self.headers("unknown")).status_code, 403)
        self.assertEqual(self.client.get("/api/access", headers=self.headers("viver")).json()["can_edit_layout"], False)

    def test_lead_without_assigned_panels_cannot_read_any_data_even_before_snapshot(self):
        with patch.object(web, "repository", side_effect=AssertionError("Should not access the database")):
            for path in ("meta", "report", "tasks", "tasks/any", "proposals", "export/tasks", "schedule"):
                result = self.client.get("/api/" + path, params=self.query, headers=self.headers("lead"))
                self.assertEqual(result.status_code, 403, path)
        layout = self.client.get("/api/layout", headers=self.headers("lead")).json()
        self.assertEqual(layout["layout"]["widgets"], [])

    def test_owner_layout_is_shared_persistent_and_viewer_cannot_change_it(self):
        layout = access.default_layout()
        layout["widgets"].insert(0, layout["widgets"].pop())
        body = {"layout": layout, "revision": 0}
        headers = {**self.headers("owner"), "x-activity-action": "save-layout"}
        self.assertEqual(self.client.put("/api/layout", json=body, headers=self.headers("owner")).status_code, 403)
        self.assertEqual(self.client.put("/api/layout", json=body, headers={**headers,"sec-fetch-site":"cross-site"}).status_code, 403)
        for user in ("viver", "lead"):
            self.assertEqual(self.client.put("/api/layout", json=body, headers={**self.headers(user),"x-activity-action":"save-layout"}).status_code, 403)
        response = self.client.put("/api/layout", json=body, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["revision"], 1)
        with TestClient(web.app) as second:
            self.assertEqual(second.get("/api/layout", headers=self.headers("viver")).json(), response.json())
        self.assertEqual(json.loads((Path(self.folder.name)/"layout.json").read_text())["layout"], layout)
        self.assertEqual(self.client.put("/api/layout", json=body, headers=headers).status_code, 409)
        body["revision"] = 1
        body["layout"]["widgets"][0]["span"] = "3;display:none"
        self.assertEqual(self.client.put("/api/layout", json=body, headers=headers).status_code, 422)
        self.assertEqual(access.read_layout()["revision"], 1)

    def test_partial_panels_filter_report_and_metadata_on_the_server(self):
        with patch.dict(os.environ, {"ACTIVITY_TECH_LEAD_PANELS": '["flow"]'}):
            data = self.client.get("/api/report", params=self.query, headers=self.headers("lead")).json()
            self.assertEqual(data["summary"], {})
            self.assertEqual(data["breakdown"], [])
            self.assertTrue(data["series"])
            for row in data["series"]:
                self.assertEqual(set(row), {"period_start", "period_end", "partial", "incoming", "completed"})
            meta = self.client.get("/api/meta", headers=self.headers("lead")).json()
            self.assertNotIn("assignees", meta)
            self.assertNotIn("clients", meta)
            self.assertNotIn("pending_proposals", meta)
            self.assertEqual(self.client.get("/api/report", params={**self.query,"client":"x"}, headers=self.headers("lead")).status_code, 403)
            self.assertEqual(self.client.get("/api/schedule", headers=self.headers("lead")).status_code, 403)
        with patch.dict(os.environ, {"ACTIVITY_TECH_LEAD_PANELS": '["calendar"]'}):
            self.assertEqual(len(self.client.get("/api/schedule", headers=self.headers("lead")).json()["people"]), 4)

    def test_viewer_and_admin_reports_are_unchanged(self):
        viewer = self.client.get("/api/report", params=self.query, headers=self.headers("viver"))
        owner = self.client.get("/api/report", params=self.query, headers=self.headers("owner"))
        self.assertEqual(viewer.status_code, 200)
        self.assertEqual(viewer.json(), owner.json())
        self.assertTrue(self.client.get("/api/tasks", params=self.query, headers=self.headers("viver")).json()["rows"])

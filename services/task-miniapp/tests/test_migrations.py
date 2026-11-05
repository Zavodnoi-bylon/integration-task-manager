from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.migrate_postgres import discover


class MigrationTests(unittest.TestCase):
    def test_workflow_migration_contains_required_objects(self):
        sql = Path("migrations/0002_miniapp_workflow.sql").read_text(encoding="utf-8")
        for marker in (
            "miniapp_sessions", "miniapp_idempotency", "review_cycle",
            "ticket_review_cycles", "ticket_review_backlog",
        ):
            self.assertIn(marker, sql)

    def test_draft_migration_is_isolated_from_reporting(self):
        sql = Path("migrations/0003_ticket_drafts.sql").read_text(encoding="utf-8")
        for marker in (
            "ticket_drafts", "ticket_draft_intake", "create_forwarded_draft",
            "save_draft_intake",
        ):
            self.assertIn(marker, sql)
        self.assertNotIn("CREATE OR REPLACE VIEW reporting.", sql)

    def test_reporting_reader_cannot_query_private_drafts(self):
        sql = Path(
            "migrations/0004_hide_ticket_drafts_from_reporting.sql"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "REVOKE SELECT ON ticketing.ticket_drafts FROM activity_reader",
            sql,
        )

    def test_discovery_rejects_duplicate_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "0002_one.sql").write_text("SELECT 1", encoding="utf-8")
            (root / "0002_two.sql").write_text("SELECT 2", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Duplicate"):
                discover(root)


if __name__ == "__main__":
    unittest.main()

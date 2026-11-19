from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.migrate_postgres import accepted_digests, discover


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

    def test_uncap_uses_separate_draft_flag(self):
        sql = Path("migrations/0005_ticket_draft_uncap.sql").read_text(
            encoding="utf-8"
        )
        self.assertIn("cap_unlimited boolean", sql)
        self.assertIn("NOT cap_unlimited OR cap IS NULL", sql)

    def test_destination_contract_keeps_structured_requests_and_ordered_results(self):
        sql = Path("migrations/0006_destination_contract.sql").read_text(
            encoding="utf-8"
        )
        for marker in (
            "destination_requests",
            "destination_results",
            "result_order",
            "processing_status",
            "landing_url",
        ):
            self.assertIn(marker, sql)

    def test_forward_batches_have_fixed_deadline_and_delivery_identity(self):
        sql = Path("migrations/0007_forward_batches.sql").read_text(
            encoding="utf-8"
        )
        self.assertIn("due_at = received_at + make_interval", sql)
        self.assertIn("UNIQUE (profile_key, chat_id, telegram_message_id)", sql)
        self.assertIn("WHERE state = 'collecting'", sql)

    def test_supervisor_is_profile_scoped_and_not_env_managed(self):
        sql = Path("migrations/0008_supervisor_role.sql").read_text(
            encoding="utf-8"
        )
        self.assertIn("supervisor_subordinates", sql)
        self.assertIn("membership.role <> 'supervisor'", sql)
        self.assertIn("supervisor_user_id <> subordinate_user_id", sql)

    def test_destination_cap_mode_preserves_unknown_history(self):
        sql = Path("migrations/0009_destination_cap_mode.sql").read_text(
            encoding="utf-8"
        )
        for marker in ("unlim", "daily", "monthly", "lifetime"):
            self.assertIn(marker, sql)
        self.assertIn("cap_mode IS NULL", sql)
        self.assertNotIn("UPDATE ticketing.", sql)

    def test_ticket_numbers_are_profile_scoped_without_rewriting_internal_ids(self):
        sql = Path("migrations/0010_profile_ticket_numbers.sql").read_text(
            encoding="utf-8"
        )
        for marker in (
            "profile_ticket_counters",
            "next_ticket_number",
            "UNIQUE (profile_key, ticket_number)",
            "WHEN profile_key = 'kalisto' THEN row_number()",
            "ELSE ticket_id",
        ):
            self.assertIn(marker, sql)
        self.assertNotIn("UPDATE ticketing.ticket_messages", sql)
        self.assertNotIn("SET ticket_id", sql)

    def test_buying_flag_stores_current_state_without_audit_metadata(self):
        sql = Path("migrations/0011_ticket_buying_flags.sql").read_text(
            encoding="utf-8"
        )
        self.assertIn("ticket_buying_flags", sql)
        self.assertIn("CHECK (profile_key = 'kalisto')", sql)
        self.assertIn("PRIMARY KEY (profile_key, ticket_id)", sql)
        self.assertIn("ON DELETE CASCADE", sql)
        for forbidden in (
            "actor_user_id",
            "created_at",
            "updated_at",
            "ticket_events",
            "ticket_messages",
        ):
            self.assertNotIn(forbidden, sql)

    def test_aio_advertiser_catalog_is_profile_scoped_and_persistent(self):
        sql = Path("migrations/0012_aio_advertiser_catalog.sql").read_text(
            encoding="utf-8"
        )
        for marker in (
            "advertiser_catalog",
            "aio_advertiser_sync_state",
            "UNIQUE (profile_key, normalized_name)",
            "aio_uuid",
            "external_id_policy",
            "last_full_at",
        ):
            self.assertIn(marker, sql)

    def test_important_comments_are_optional_and_mephisto_scoped(self):
        sql = Path("migrations/0013_mephisto_important_comments.sql").read_text(
            encoding="utf-8"
        )
        self.assertEqual(sql.count("ADD COLUMN important_comment text"), 2)
        self.assertIn("profile_key = 'mephisto'", sql)
        self.assertIn("ticket_type_code = 'destination'", sql)
        self.assertIn("ticket_type = 'destination'", sql)
        self.assertIn("WHERE important_comment IS NOT NULL", sql)
        self.assertNotIn("UPDATE ticketing.", sql)

    def test_discovery_rejects_duplicate_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "0002_one.sql").write_text("SELECT 1", encoding="utf-8")
            (root / "0002_two.sql").write_text("SELECT 2", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Duplicate"):
                discover(root)

    def test_migration_checksums_accept_only_line_ending_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lf = root / "lf.sql"
            crlf = root / "crlf.sql"
            changed = root / "changed.sql"
            lf.write_bytes(b"SELECT 1;\nSELECT 2;\n")
            crlf.write_bytes(b"SELECT 1;\r\nSELECT 2;\r\n")
            changed.write_bytes(b"SELECT 1;\nSELECT 3;\n")

            self.assertEqual(accepted_digests(lf), accepted_digests(crlf))
            self.assertTrue(accepted_digests(lf).isdisjoint(accepted_digests(changed)))


if __name__ == "__main__":
    unittest.main()

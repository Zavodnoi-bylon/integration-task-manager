import tempfile
import unittest
from pathlib import Path

from scripts.migrate_postgres import discover_migrations


class MigrationTests(unittest.TestCase):
    def test_initial_migration_is_standalone_sql(self):
        path = Path("migrations/0001_ticketing_schema.sql")
        sql = path.read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS ticketing.tickets", sql)
        self.assertIn("CREATE OR REPLACE VIEW reporting.ticket_facts", sql)
        self.assertNotIn("\\set", sql)
        self.assertNotIn("activity_trial", sql)

    def test_discovery_orders_versions_and_calculates_checksums(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "0002_second.sql").write_text("SELECT 2;", encoding="utf-8")
            (root / "0001_first.sql").write_text("SELECT 1;", encoding="utf-8")
            migrations = discover_migrations(root)
        self.assertEqual([item[0] for item in migrations], [1, 2])
        self.assertTrue(all(len(item[2]) == 64 for item in migrations))

    def test_discovery_rejects_duplicate_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "0001_first.sql").write_text("SELECT 1;", encoding="utf-8")
            (root / "0001_duplicate.sql").write_text("SELECT 2;", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Duplicate migration version"):
                discover_migrations(root)


if __name__ == "__main__":
    unittest.main()

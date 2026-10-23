import json
import os
import sys
import types
import unittest
from unittest.mock import patch

from activity.repository import ConfigurationError, PostgresRepository, RepositoryError, SnapshotLimitError


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, overflow=False):
        self.queries = []
        self.overflow = overflow

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def transaction(self):
        return self

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        if "FROM ops.dataset" in sql:
            rows = [{"coverage_status": "partial", "coverage_note": "Imported evidence"}]
        elif "FROM work.tasks" in sql:
            rows = [{"task_id": "id", "title": "Example", "category": "support", "client": None,
                     "assignee": None, "external_refs": {}, "revision": 1}]
            if self.overflow:
                rows = rows * 2
        elif "AS bytes" in sql:
            rows = [{"bytes": 0}]
        elif "max(completed_at)" in sql:
            rows = [{"completed_at": None}]
        else:
            rows = []
        return Cursor(rows)


class PendingEvidenceConnection(FakeConnection):
    def execute(self, sql, params=None):
        if "FROM work.proposals" in sql:
            self.queries.append((sql, params))
            return Cursor([{"proposal_id": "pending", "task_id": "pending-task", "version": 1,
                            "state": "proposed", "created_at": None,
                            "document": {"title": "Pending review", "evidence": [
                                {"message_key": "source:older", "revision_hash": "old-exact-revision"}],
                                "events": [{"evidence": [
                                    {"message_key": "source:event-only", "revision_hash": "event-revision"},
                                    {"message_key": "source:older", "revision_hash": "old-exact-revision"}]}]}}])
        if "r.sender_name" in sql:
            self.queries.append((sql, params))
            return Cursor([dict(reference, text_plain="Original referenced text", sent_at=None,
                                edited_at=None, sender_name="Synthetic sender", source_name="Synthetic chat",
                                source_url=None, has_conflict=False, media_missing=False)
                           for reference in json.loads(params[0])])
        return super().execute(sql, params)


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"DB_HOST": "local", "DB_NAME": "activity_test",
                                                   "DB_USER": "reader", "DB_PASSWORD": "private-test-only"})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()

    def fake_driver(self, connection=None, fail=False):
        driver = types.ModuleType("psycopg")
        rows = types.ModuleType("psycopg.rows")
        rows.dict_row = object()
        def connect(**config):
            self.assertEqual(config["user"], "reader")
            self.assertIn("default_transaction_read_only=on", config["options"])
            if fail:
                raise RuntimeError("postgresql://reader:private-test-only@private-host/internal")
            return connection
        driver.connect = connect
        return patch.dict(sys.modules, {"psycopg": driver, "psycopg.rows": rows})

    def test_snapshot_sets_repeatable_read_before_all_queries_and_only_selects(self):
        connection = FakeConnection()
        with self.fake_driver(connection):
            data = PostgresRepository().snapshot()
        self.assertEqual(connection.queries[0][0], "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        self.assertTrue(all(sql.startswith("SELECT ") for sql, _params in connection.queries[1:]))
        self.assertEqual(data["tasks"][0]["task_id"], "id")
        self.assertIs(data["demo"], False)
        self.assertTrue(all("raw_json" not in sql for sql, _params in connection.queries))

    def test_explicit_overflow_never_returns_partial_snapshot(self):
        with self.fake_driver(FakeConnection(overflow=True)), patch.dict(os.environ, {"ACTIVITY_MAX_TASKS": "1"}):
            with self.assertRaises(SnapshotLimitError):
                PostgresRepository().snapshot()

    def test_pending_only_and_event_only_exact_revisions_are_loaded_with_parameters(self):
        connection = PendingEvidenceConnection()
        with self.fake_driver(connection):
            data = PostgresRepository().snapshot()
        self.assertEqual(len(data["tasks"]), 1)
        self.assertEqual(len(data["tasks"][0]["evidence"]), 0)
        self.assertEqual({(row["message_key"], row["revision_hash"]) for row in data["messages"]},
                         {("source:older", "old-exact-revision"), ("source:event-only", "event-revision")})
        reads = [(sql, params) for sql, params in connection.queries if "jsonb_to_recordset" in sql]
        self.assertEqual(len(reads), 2)
        self.assertTrue(all("old-exact-revision" not in sql for sql, _params in reads))
        self.assertTrue(all("current_revision_hash" not in sql for sql, _params in reads))
        with self.fake_driver(PendingEvidenceConnection()), patch.dict(os.environ, {"ACTIVITY_MAX_EVIDENCE": "1"}):
            with self.assertRaises(SnapshotLimitError):
                PostgresRepository().snapshot()

    def test_missing_configuration_and_driver_errors_do_not_reveal_secrets(self):
        with patch.dict(os.environ, {"DB_HOST": ""}):
            with self.assertRaises(ConfigurationError) as raised:
                PostgresRepository().health()
            self.assertNotIn("private-test-only", str(raised.exception))
        with self.fake_driver(fail=True):
            with self.assertRaises(RepositoryError) as raised:
                PostgresRepository().health()
            self.assertNotIn("private-test-only", str(raised.exception))
            self.assertNotIn("private-host", str(raised.exception))


if __name__ == "__main__":
    unittest.main()

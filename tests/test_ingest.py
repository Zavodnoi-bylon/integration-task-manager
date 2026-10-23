"""Fictional fixtures only. Run: python -m unittest discover -s tests."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from activity.ingest import (ImportValidationError, choose_current, flatten_text,
                             normalize_document, normalize_file, sha256_json)


def chat(message=None, chat_id=100):
    return {"id": chat_id, "name": "Fictional test chat", "type": "private_group", "messages": [
        message or {"id": 7, "type": "message", "date": "2026-10-20T11:00:00",
                    "date_unixtime": "1792483200", "from": "Shared test account", "from_id": "user1",
                    "text": "Fictional request"}
    ]}


class NormalizationTests(unittest.TestCase):
    def test_composite_identity_distinguishes_chats(self):
        document = {"chats": {"list": [chat(chat_id=100), chat(chat_id=200)]}}
        output = normalize_document(document)
        self.assertEqual({m["message_key"] for m in output["messages"]},
                         {"telegram-desktop:100:7", "telegram-desktop:200:7"})

    def test_raw_entities_links_and_original_are_preserved(self):
        message = chat()["messages"][0]
        message["text"] = ["See ", {"type": "text_link", "text": "fictional task",
                                    "href": "https://example.invalid/tasks/0009?sub={macro}"}, " now"]
        before = copy.deepcopy(message)
        normalized = normalize_document(chat(message))["messages"][0]
        self.assertEqual(normalized["text_plain"], "See fictional task now")
        self.assertEqual(normalized["entity_links"][0]["href"], "https://example.invalid/tasks/0009?sub={macro}")
        self.assertEqual(normalized["raw_json"], before)
        self.assertEqual(message, before)
        self.assertEqual(normalized["revision_hash"], sha256_json(before))

    def test_unix_time_wins_over_inconsistent_display_date(self):
        message = chat()["messages"][0]
        message["date"] = "1900-01-01T00:00:00"
        normalized = normalize_document(chat(message))["messages"][0]
        self.assertEqual(normalized["sent_at"], "2026-10-20T08:00:00+00:00")

    def test_fallback_requires_explicit_timezone(self):
        message = chat()["messages"][0]
        del message["date_unixtime"]
        with self.assertRaisesRegex(ImportValidationError, "timezone"):
            normalize_document(chat(message))
        value = normalize_document(chat(message), fallback_timezone="Europe/Moscow")
        self.assertEqual(value["messages"][0]["sent_at"], "2026-10-20T08:00:00+00:00")

    def test_newer_then_older_cannot_revert_current(self):
        newer = "2026-10-21T08:30:00+00:00"
        older = "2026-10-21T08:00:00+00:00"
        self.assertEqual(choose_current("new", newer, "old", older), ("new", False, "older"))
        self.assertEqual(choose_current("old", older, "new", newer), ("new", False, "newer"))

    def test_equal_time_conflict_keeps_both_identity_and_flag(self):
        when = "2026-10-21T08:00:00+00:00"
        self.assertEqual(choose_current("a", when, "b", when), ("a", True, "conflict"))
        self.assertEqual(choose_current("a", when, "a", when, True), ("a", True, "unchanged"))
        self.assertEqual(choose_current("a", when, "c", "2026-10-21T09:00:00+00:00", True),
                         ("c", False, "newer"))

    def test_canonical_hash_is_independent_of_object_key_order(self):
        self.assertEqual(sha256_json({"id": 1, "text": "x"}), sha256_json({"text": "x", "id": 1}))
        self.assertNotEqual(sha256_json({"id": 1, "text": "x"}), sha256_json({"id": 1, "text": "y"}))

    def test_edits_service_missing_media_and_reply_preserved(self):
        message = chat()["messages"][0]
        message.update({"edited_unixtime": "1792483260", "edited": "2026-10-20T11:01:00",
                        "photo": "(File not included. Change data exporting settings to download.)",
                        "reply_to_message_id": 3})
        normalized = normalize_document(chat(message))["messages"][0]
        self.assertTrue(normalized["media_missing"])
        self.assertEqual(normalized["reply_to_external_id"], "3")
        self.assertEqual(normalized["source_updated_at"], "2026-10-20T08:01:00+00:00")
        message["type"] = "service"
        self.assertEqual(normalize_document(chat(message))["counts"]["service_records"], 1)

    def test_invalid_timestamp_and_identifier_are_rejected(self):
        for field, value in (("id", True), ("date_unixtime", "not-a-time"),
                             ("edited_unixtime", "1792480000")):
            message = chat()["messages"][0]
            message[field] = value
            with self.subTest(field=field), self.assertRaises(ImportValidationError):
                normalize_document(chat(message))

    def test_same_records_in_overlapping_export_have_same_keys_hashes(self):
        one = normalize_document(chat(), source_name="first.json")
        data = chat()
        data["name"] = "Renamed chat"
        data["messages"].append(copy.deepcopy(data["messages"][0]))
        two = normalize_document(data, source_name="second.json")
        self.assertEqual(len(two["messages"]), 1)
        self.assertEqual(one["messages"][0]["message_key"], two["messages"][0]["message_key"])
        self.assertEqual(one["messages"][0]["revision_hash"], two["messages"][0]["revision_hash"])

    def test_normalize_file_does_not_change_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.json"
            source = json.dumps(chat(), ensure_ascii=False).encode("utf-8")
            path.write_bytes(source)
            normalize_file(path)
            self.assertEqual(path.read_bytes(), source)

    def test_dry_run_does_not_open_writer(self):
        import sync
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.json"
            path.write_text(json.dumps(chat()), encoding="utf-8")
            with patch("sync.writer_connection", side_effect=AssertionError("unexpected DB")), patch("builtins.print"):
                self.assertEqual(sync.main(["ingest-messages", str(path), "--dry-run"]), 0)


if __name__ == "__main__":
    unittest.main()

"""Fictional data only; checks evidence, human overrides and lifecycle safety."""
import copy
import unittest

from activity.ingest import normalize_document
from activity.proposals import (ProposalValidationError, ReviewConflict, canonical_task,
                                evidence_index, require_expected_revision,
                                validate_document, verify_offline_evidence)


def fixture():
    bundle = normalize_document({"id": 100, "name": "Fictional evidence", "messages": [
        {"id": 1, "type": "message", "date_unixtime": "1792483200", "text": "Fictional request"},
        {"id": 2, "type": "message", "date_unixtime": "1792486800", "text": "Fictional completion"}
    ]})
    refs = [{"message_key": r["message_key"], "revision_hash": r["revision_hash"]} for r in bundle["messages"]]
    p = {"proposal_id": "test:case:1:v1", "task_id": "test:case:1", "version": 1,
         "title": "Fictional test case", "category": "integration", "client": "Fictional client",
         "assignee": None, "external_refs": {"destination_id": "000123"}, "evidence": [],
         "events": [
             {"event_id": "test:case:1:request", "kind": "requested", "occurred_at": "2026-10-20T11:00:00+03:00",
              "time_basis": "message", "evidence": [refs[0]]},
             {"event_id": "test:case:1:done", "kind": "completed", "occurred_at": "2026-10-20T12:00:00+03:00",
              "time_basis": "message", "evidence": [refs[1]]}
         ]}
    return {"schema_version": 1, "demo": True, "proposals": [p]}, bundle


class ProposalTests(unittest.TestCase):
    def test_empty_extraction_is_valid_and_creates_no_candidates(self):
        self.assertEqual(validate_document({"schema_version": 1, "proposals": []})["proposals"], [])

    def test_valid_evidence_and_explicit_unknown_assignee(self):
        document, bundle = fixture()
        result = validate_document(document)
        verify_offline_evidence(result, evidence_index(bundle))
        self.assertIsNone(result["proposals"][0]["assignee"])
        self.assertEqual(result["proposals"][0]["external_refs"]["destination_id"], "000123")

    def test_missing_evidence_rejected_even_when_message_id_exists(self):
        document, bundle = fixture()
        document["proposals"][0]["events"][0]["evidence"][0]["revision_hash"] = "f" * 64
        with self.assertRaisesRegex(ProposalValidationError, "evidence_not_found"):
            verify_offline_evidence(validate_document(document), evidence_index(bundle))

    def test_self_asserted_acceptance_unknown_fields_rejected(self):
        for field in ("state", "reviewed_by", "revision", "sql"):
            document, _ = fixture()
            document["proposals"][0][field] = "accepted"
            with self.subTest(field=field), self.assertRaises(ProposalValidationError):
                validate_document(document)

    def test_completion_requires_own_evidence(self):
        document, _ = fixture()
        p = document["proposals"][0]
        p["evidence"] = p["events"][1]["evidence"]
        p["events"][1]["evidence"] = []
        with self.assertRaises(ProposalValidationError):
            validate_document(document)

    def test_timezone_and_numeric_ids_are_not_silently_coerced(self):
        document, _ = fixture()
        document["proposals"][0]["events"][0]["occurred_at"] = "2026-10-20T11:00:00"
        with self.assertRaises(ProposalValidationError):
            validate_document(document)
        document, _ = fixture()
        document["proposals"][0]["external_refs"]["destination_id"] = 123
        with self.assertRaises(ProposalValidationError):
            validate_document(document)

    def test_one_message_can_evidence_multiple_cases(self):
        document, bundle = fixture()
        other = copy.deepcopy(document["proposals"][0])
        other.update({"task_id": "test:case:2", "proposal_id": "test:case:2:v1"})
        for event in other["events"]:
            event["event_id"] = event["event_id"].replace(":1:", ":2:")
        document["proposals"].append(other)
        verify_offline_evidence(validate_document(document), evidence_index(bundle))

    def test_full_snapshot_preserves_explicit_null_clear(self):
        document, _ = fixture()
        p = document["proposals"][0]
        previous = {"revision": 3, "assignee": "Fictional prior employee"}
        version = require_expected_revision(previous, 3, decision="accept")
        accepted = canonical_task(p, version + 1)
        self.assertEqual(accepted["revision"], 4)
        self.assertIsNone(accepted["assignee"])
        self.assertEqual(previous["assignee"], "Fictional prior employee")

    def test_human_revision_guard(self):
        with self.assertRaises(ReviewConflict):
            require_expected_revision({"revision": 2}, None, decision="accept")
        with self.assertRaises(ReviewConflict):
            require_expected_revision({"revision": 2}, 1, decision="accept")
        self.assertEqual(require_expected_revision(None, None, decision="accept"), 0)

    def test_duplicate_versions_and_cross_task_event_ids_rejected(self):
        document, _ = fixture()
        document["proposals"].append(copy.deepcopy(document["proposals"][0]))
        with self.assertRaises(ProposalValidationError):
            validate_document(document)
        document, _ = fixture()
        other = copy.deepcopy(document["proposals"][0])
        other.update({"task_id": "test:case:2", "proposal_id": "test:case:2:v1"})
        document["proposals"].append(other)
        with self.assertRaises(ProposalValidationError):
            validate_document(document)

    def test_negative_chronology_is_rejected(self):
        document, _ = fixture()
        document["proposals"][0]["events"][1]["occurred_at"] = "2026-10-20T10:00:00+03:00"
        with self.assertRaises(ProposalValidationError):
            validate_document(document)

    def test_reopening_keeps_same_task_and_history(self):
        document, bundle = fixture()
        p = document["proposals"][0]
        ref = p["events"][1]["evidence"][0]
        p["events"].extend([
            {"event_id": "test:case:1:reopen", "kind": "reopened", "occurred_at": "2026-10-21T10:00:00+03:00",
             "time_basis": "message", "evidence": [ref]},
            {"event_id": "test:case:1:done-again", "kind": "completed", "occurred_at": "2026-10-21T11:00:00+03:00",
             "time_basis": "message", "evidence": [ref]},
        ])
        result = validate_document(document)
        self.assertEqual(len(result["proposals"]), 1)
        self.assertEqual(len(result["proposals"][0]["events"]), 4)

    def test_evidence_bundle_tampering_is_not_silent(self):
        _, bundle = fixture()
        bundle["messages"][0]["raw_json"]["text"] = "changed"
        with self.assertRaises(ProposalValidationError):
            evidence_index(bundle)


if __name__ == "__main__":
    unittest.main()

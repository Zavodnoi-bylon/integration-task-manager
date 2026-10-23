import unittest
from datetime import datetime, timezone

from activity import domain
from activity.demo import DemoRepository


def task(task_id, events, **attributes):
    result = {"task_id": task_id, "title": task_id, "category": "support", "client": None,
              "assignee": None, "events": [], "evidence": [], "external_refs": {}, "revision": 1}
    result.update(attributes)
    result["events"] = [
        {"event_id": f"{task_id}:{index}", "kind": kind, "occurred_at": at, "time_basis": "message"}
        for index, (kind, at) in enumerate(events)
    ]
    return result


def dataset(*tasks):
    return {"demo": False, "coverage": {"status": "partial", "note": "Known evidence only"},
            "tasks": list(tasks), "proposals": [], "messages": [], "imports": []}


class ReportingTests(unittest.TestCase):
    def test_moscow_boundaries_are_inclusive_dates_not_utc_dates(self):
        data = dataset(
            task("before", [("requested", "2026-10-04T20:59:59+00:00")]),
            task("midnight", [("requested", "2026-10-04T21:00:00Z")]),
            task("last", [("requested", "2026-10-05T20:59:59Z")]),
            task("next", [("requested", "2026-10-05T21:00:00Z")]),
        )
        result = domain.report(data, "2026-10-05", "2026-10-05")
        self.assertEqual(result["summary"]["incoming"], 2)
        self.assertEqual(result["summary"]["observed_backlog"], 3)
        self.assertEqual({row["task_id"] for row in domain.task_rows(data, "2026-10-05", "2026-10-05", metric="incoming")}, {"midnight", "last"})

    def test_week_edges_and_backlog_are_snapshots_not_sums(self):
        data = dataset(task("older", [("requested", "2026-09-30T10:00:00+03:00")]),
                       task("newer", [("requested", "2026-10-12T12:00:00+03:00")]))
        result = domain.report(data, "2026-10-07", "2026-10-20", "week")
        self.assertEqual([(r["period_start"], r["period_end"], r["partial"]) for r in result["series"]],
                         [("2026-10-07", "2026-10-11", True), ("2026-10-12", "2026-10-18", False),
                          ("2026-10-19", "2026-10-20", True)])
        self.assertEqual([r["observed_backlog"] for r in result["series"]], [1, 2, 2])
        self.assertEqual(result["summary"]["observed_backlog"], 2)
        rows = domain.task_rows(data, "2026-10-19", "2026-10-20", metric="backlog")
        self.assertEqual({row["task_id"] for row in rows}, {"older", "newer"})

    def test_assignment_is_not_incoming_and_ready_is_still_open(self):
        data = dataset(task("assigned", [("assigned", "2026-10-05T11:00:00+03:00"),
                                         ("work_ready", "2026-10-06T12:00:00+03:00")]))
        summary = domain.report(data, "2026-10-05", "2026-10-06")["summary"]
        self.assertEqual((summary["incoming"], summary["completed"], summary["observed_backlog"]), (0, 0, 1))
        row = domain.task_rows(data, "2026-10-05", "2026-10-06", status="open")[0]
        self.assertEqual(row["status"], "work_ready")
        self.assertIsNone(row["requested_at"])

    def test_missing_request_is_not_fabricated_or_timed(self):
        data = dataset(task("closed", [("completed", "2026-10-05T12:00:00+03:00")]))
        summary = domain.report(data, "2026-10-05", "2026-10-05")["summary"]
        self.assertEqual((summary["completed"], summary["missing_request_count"], summary["resolution_sample_size"]), (1, 1, 0))
        self.assertIsNone(summary["resolution_median_hours"])
        row = domain.task_rows(data, "2026-10-05", "2026-10-05")[0]
        self.assertIsNone(row["resolution_hours"])

    def test_distinct_completions_reopening_duplicates_and_period_median(self):
        data = dataset(task("one", [
            ("requested", "2026-10-05T12:00:00+03:00"),
            ("completed", "2026-10-06T12:00:00+03:00"),
            ("completed", "2026-10-07T12:00:00+03:00"),
            ("reopened", "2026-10-12T12:00:00+03:00"),
            ("completed", "2026-10-13T12:00:00+03:00"),
        ]), task("two", [("requested", "2026-10-13T10:00:00+03:00"),
                           ("completed", "2026-10-13T12:00:00+03:00")]))
        result = domain.report(data, "2026-10-05", "2026-10-18", "week")
        self.assertEqual(result["summary"]["completed"], 2)
        self.assertEqual(result["summary"]["completion_events"], 3)
        self.assertEqual(result["summary"]["reopened"], 1)
        self.assertEqual([row["completed"] for row in result["series"]], [1, 2])
        self.assertEqual(result["summary"]["resolution_median_hours"], 97.0)
        self.assertEqual(result["summary"]["resolution_sample_size"], 2)

    def test_two_completions_same_day_count_one_task_and_latest_duration(self):
        data = dataset(task("same-day", [("requested", "2026-10-05T10:00:00+03:00"),
                                          ("completed", "2026-10-05T12:00:00+03:00"),
                                          ("reopened", "2026-10-05T13:00:00+03:00"),
                                          ("completed", "2026-10-05T16:00:00+03:00")]))
        row = domain.report(data, "2026-10-05", "2026-10-05")["series"][0]
        self.assertEqual((row["completed"], row["completion_events"], row["resolution_median_hours"]), (1, 2, 6))

    def test_cancelled_is_separate_and_initial_reopen_is_allowed(self):
        data = dataset(task("cancelled", [("requested", "2026-10-05T10:00:00+03:00"),
                                          ("cancelled", "2026-10-05T12:00:00+03:00")]),
                       task("unknown-past", [("reopened", "2026-10-05T13:00:00+03:00")]))
        summary = domain.report(data, "2026-10-05", "2026-10-05")["summary"]
        self.assertEqual((summary["incoming"], summary["completed"], summary["cancelled"],
                          summary["reopened"], summary["observed_backlog"]), (1, 0, 1, 1, 1))

    def test_state_is_as_of_to_and_completed_drilldown_can_be_reopened(self):
        data = dataset(task("case", [("requested", "2026-10-05T10:00:00+03:00"),
                                     ("completed", "2026-10-06T10:00:00+03:00"),
                                     ("reopened", "2026-10-07T10:00:00+03:00")]))
        self.assertEqual(domain.task_rows(data, "2026-10-05", "2026-10-06")[0]["status"], "completed")
        row = domain.task_rows(data, "2026-10-06", "2026-10-07", metric="completed")[0]
        self.assertEqual(row["status"], "open")
        self.assertEqual(len(domain.task_rows(data, "2026-10-06", "2026-10-07", metric="completed", status="completed")), 0)

    def test_current_attribute_filters_null_assignee_and_string_ids(self):
        data = dataset(task("00012345678901234567890", [("requested", "2026-10-05T10:00:00+03:00")],
                            title="Настройка Demo", client="A", assignee=None),
                       task("assigned", [("requested", "2026-10-05T10:00:00+03:00")], assignee="Operator"))
        rows = domain.task_rows(data, "2026-10-05", "2026-10-05", filters={"assignee": "__unknown__", "q": "DEMO"})
        self.assertEqual([row["task_id"] for row in rows], ["00012345678901234567890"])
        self.assertEqual(domain.report(data, "2026-10-05", "2026-10-05", filters={"assignee": "Operator"})["summary"]["incoming"], 1)

    def test_no_observation_is_unknown_but_inactive_known_history_has_zero_flow(self):
        for data in (dataset(), dataset(task("future", [("requested", "2026-10-06T10:00:00+03:00")]))):
            result = domain.report(data, "2026-10-05", "2026-10-05")
            self.assertTrue(result["empty"])
            self.assertIsNone(result["summary"]["incoming"])
            self.assertEqual(result["series"], [])
        data = dataset(task("historical", [("completed", "2026-10-04T10:00:00+03:00")]))
        result = domain.report(data, "2026-10-05", "2026-10-05")
        self.assertFalse(result["empty"])
        self.assertEqual(result["summary"]["incoming"], 0)
        self.assertEqual(result["summary"]["observed_backlog"], 0)

    def test_proposals_never_contribute_to_metrics_and_pagination_is_complete(self):
        data = dataset(*[task(str(index), [("requested", "2026-10-05T10:00:00+03:00")]) for index in range(5)])
        data["proposals"] = [{"proposal_id": "p", "state": "proposed"}, {"proposal_id": "accepted", "state": "accepted"}]
        result = domain.report(data, "2026-10-05", "2026-10-05")
        self.assertEqual(result["summary"]["incoming"], 5)
        self.assertEqual(result["summary"]["pending_proposals"], 1)
        pages = [domain.task_page(data, "2026-10-05", "2026-10-05", page=index, page_size=2) for index in (1, 2, 3)]
        self.assertEqual([p["total"] for p in pages], [5, 5, 5])
        self.assertEqual([p["has_more"] for p in pages], [True, True, False])
        self.assertEqual(len({r["task_id"] for p in pages for r in p["rows"]}), 5)

    def test_demo_is_explicit_and_has_resolvable_evidence_and_known_edge_cases(self):
        data = DemoRepository().snapshot()
        self.assertIs(data["demo"], True)
        self.assertIn("Демонстрационные данные — не отчёт отдела", data["coverage"]["note"])
        for row in data["tasks"]:
            detail = domain.task_detail(data, row["task_id"])
            self.assertEqual(detail["task"]["evidence_count"], len(detail["evidence"]))
        day = domain.report(data, "2026-10-05", "2026-10-22", "day")
        week = domain.report(data, "2026-10-05", "2026-10-22", "week")
        self.assertEqual(day["summary"], week["summary"])
        self.assertEqual(day["summary"]["observed_backlog"], day["series"][-1]["observed_backlog"])
        self.assertGreater(day["summary"]["completion_events"], day["summary"]["completed"])
        self.assertGreater(day["summary"]["missing_request_count"], 0)

    def test_pending_evidence_is_exact_deduplicated_and_does_not_change_task_metrics(self):
        data = dataset(task("accepted", [("requested", "2026-10-05T10:00:00+03:00")]))
        before = domain.report(data, "2026-10-05", "2026-10-05")["summary"]
        old = {"message_key": "same-message", "revision_hash": "old"}
        event_only = {"message_key": "event-message", "revision_hash": "event"}
        data["proposals"] = [{"proposal_id": "p", "state": "proposed", "evidence": [old],
                              "events": [{"evidence": [old, event_only]}]}]
        data["messages"] = [dict(old, text_plain="Old referenced text", raw_json={"private": "omitted"}),
                            {"message_key": "same-message", "revision_hash": "new", "text_plain": "Unrelated later edit"},
                            dict(event_only, text_plain="Event-only evidence")]
        rows = domain.pending_proposals(data)
        self.assertEqual([row["text_plain"] for row in rows[0]["evidence_details"]],
                         ["Old referenced text", "Event-only evidence"])
        self.assertNotIn("raw_json", rows[0]["evidence_details"][0])
        self.assertEqual(rows[0]["evidence"], [old])
        after = domain.report(data, "2026-10-05", "2026-10-05")["summary"]
        self.assertEqual({key: value for key, value in before.items() if key != "pending_proposals"},
                         {key: value for key, value in after.items() if key != "pending_proposals"})
        self.assertEqual(after["pending_proposals"], 1)
        data["messages"] = [row for row in data["messages"] if row["revision_hash"] != "old"]
        with self.assertRaises(domain.DomainValidationError):
            domain.pending_proposals(data)


class ValidationTests(unittest.TestCase):
    def test_terminal_task_needs_explicit_reopen(self):
        for kind in ("requested", "assigned", "resumed", "observed_open", "waiting", "work_ready"):
            with self.subTest(kind=kind), self.assertRaises(domain.DomainValidationError):
                domain.validate_event_sequence(task("case", [("completed", "2026-10-05T10:00:00Z"),
                                                               (kind, "2026-10-06T10:00:00Z")])["events"])
        with self.assertRaises(domain.DomainValidationError):
            domain.validate_event_sequence(task("case", [("requested", "2026-10-05T10:00:00Z"),
                                                           ("reopened", "2026-10-06T10:00:00Z")])["events"])

    def test_equal_time_order_is_input_independent(self):
        value = task("case", [("completed", "2026-10-05T10:00:00Z"),
                              ("requested", "2026-10-05T10:00:00Z")])
        first = domain.report(dataset(value), "2026-10-05", "2026-10-05")
        value["events"].reverse()
        second = domain.report(dataset(value), "2026-10-05", "2026-10-05")
        self.assertEqual(first, second)
        self.assertEqual(first["summary"]["resolution_median_hours"], 0)

    def test_naive_timestamps_and_negative_duration_are_rejected(self):
        with self.assertRaises(domain.DomainValidationError):
            domain.validate_event_sequence(task("bad", [("requested", "2026-10-05T10:00:00")])["events"])
        with self.assertRaises(domain.DomainValidationError):
            domain.duration_hours(domain.parse_timestamp("2026-10-06T10:00:00Z"),
                                  domain.parse_timestamp("2026-10-05T10:00:00Z"))
        with self.assertRaises(domain.DomainValidationError):
            domain.validate_event_sequence(task("late-first-request", [
                ("completed", "2026-10-05T10:00:00Z"),
                ("reopened", "2026-10-06T10:00:00Z"),
                ("requested", "2026-10-07T10:00:00Z"),
            ])["events"])

    def test_range_cap_and_moscow_today(self):
        domain.date_window("2024-01-01", "2024-12-31")  # 366 inclusive days.
        for start, end in (("2024-01-01", "2025-01-01"), ("2026-10-06", "2026-10-05"),
                           ("2026-02-30", "2026-03-01")):
            with self.assertRaises(domain.DomainValidationError):
                domain.date_window(start, end)
        meta = domain.metadata(dataset(), datetime(2026, 10, 21, 22, tzinfo=timezone.utc))
        self.assertEqual(meta["today"], "2026-10-22")


if __name__ == "__main__":
    unittest.main()

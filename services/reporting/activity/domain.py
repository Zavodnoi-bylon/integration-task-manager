"""Pure reporting rules for reviewed task histories, independent of SQL or HTTP.

All boundaries are half-open instants in UTC, obtained from Moscow calendar dates.
Events tied on timestamp use EVENT_ORDER, then their stable string event_id.
Only ``reopened`` can open a known terminal task. Duplicate terminal events have
no flow effect. A completion with no known request is counted, but not timed.
When a closure predates all visible requests, a later follow-up cannot supply the
missing original request: record reopened/resumed, or correct the earlier history.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from statistics import median
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

TIMEZONE = "Europe/Moscow"
MOSCOW = ZoneInfo(TIMEZONE)
UTC = timezone.utc
CATEGORIES = {
    "integration": "Интеграции",
    "incident": "Инциденты",
    "support": "Поддержка",
    "change": "Изменения",
    "other": "Прочее",
}
EVENT_ORDER = {
    name: index for index, name in enumerate((
        "requested", "observed_open", "assigned", "reopened", "resumed",
        "waiting", "work_ready", "completed", "cancelled",
    ))
}
TIME_BASES = frozenset(("event", "message", "notification", "observed"))
OPEN_STATES = frozenset(("open", "waiting", "work_ready"))
TERMINAL_STATES = frozenset(("completed", "cancelled"))


class DomainValidationError(ValueError):
    """A safe, user-facing data or query validation error."""


def parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, OverflowError):
            raise DomainValidationError("Event timestamp must be valid ISO 8601.") from None
    else:
        raise DomainValidationError("Event timestamp must be an ISO 8601 string.")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DomainValidationError("Event timestamp must include a timezone.")
    return parsed.astimezone(UTC)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def duration_hours(requested: datetime | None, completed: datetime) -> float | None:
    if requested is None:
        return None
    elapsed = (completed - requested).total_seconds() / 3600
    if elapsed < 0:
        raise DomainValidationError("Completion cannot precede the known request.")
    return round(elapsed, 6)


@dataclass(frozen=True)
class Window:
    first: date
    last: date
    start: datetime
    end: datetime


def date_window(first: str | date, last: str | date) -> Window:
    try:
        first_date = date.fromisoformat(first) if isinstance(first, str) else first
        last_date = date.fromisoformat(last) if isinstance(last, str) else last
    except (ValueError, TypeError):
        raise DomainValidationError("Dates must use YYYY-MM-DD.") from None
    if not isinstance(first_date, date) or not isinstance(last_date, date):
        raise DomainValidationError("Both from and to dates are required.")
    if first_date > last_date:
        raise DomainValidationError("The from date must not exceed the to date.")
    if (last_date - first_date).days >= 366:
        raise DomainValidationError("The maximum reporting range is 366 days.")
    try:
        start = datetime.combine(first_date, time.min, MOSCOW).astimezone(UTC)
        end = datetime.combine(last_date + timedelta(days=1), time.min, MOSCOW).astimezone(UTC)
    except (ValueError, OverflowError):
        raise DomainValidationError("Date is outside the supported range.") from None
    return Window(first_date, last_date, start, end)


@dataclass(frozen=True)
class Event:
    event_id: str
    kind: str
    at: datetime
    time_basis: str
    state: str
    valid: bool
    open_delta: int

    def as_dict(self) -> dict:
        return {"event_id": self.event_id, "kind": self.kind,
                "occurred_at": iso(self.at), "time_basis": self.time_basis,
                "counted": self.valid}


def _events(events: Sequence[Mapping]) -> tuple[list[Event], datetime | None]:
    if not isinstance(events, (list, tuple)):
        raise DomainValidationError("Task events must be an array.")
    parsed = []
    ids: set[str] = set()
    for raw in events:
        if not isinstance(raw, Mapping):
            raise DomainValidationError("Each event must be an object.")
        event_id, kind = raw.get("event_id"), raw.get("kind")
        if not isinstance(event_id, str) or not event_id or event_id in ids:
            raise DomainValidationError("Event IDs must be nonempty unique strings.")
        if kind not in EVENT_ORDER or raw.get("time_basis") not in TIME_BASES:
            raise DomainValidationError("Unknown event kind or time basis.")
        ids.add(event_id)
        parsed.append((parse_timestamp(raw.get("occurred_at")), EVENT_ORDER[kind],
                       event_id, kind, raw["time_basis"]))
    parsed.sort(key=lambda item: item[:3])
    state, requested, seen_closure = "unknown", None, False
    result: list[Event] = []
    for at, _rank, event_id, kind, basis in parsed:
        previous, valid = state, True
        if kind in ("completed", "cancelled"):
            if state in TERMINAL_STATES:
                valid = False
            else:
                if kind == "completed":
                    duration_hours(requested, at)
                state = kind
                seen_closure = True
        elif kind == "reopened":
            if state in OPEN_STATES:
                raise DomainValidationError("An open task cannot be reopened again.")
            state = "open"
        else:
            if state in TERMINAL_STATES:
                raise DomainValidationError("Only reopened can open a terminal task.")
            if kind == "requested":
                if requested is None and seen_closure:
                    raise DomainValidationError("The first known request cannot follow a recorded closure.")
                requested = requested or at
                if state == "unknown":
                    state = "open"
            elif kind in ("observed_open", "assigned"):
                if state == "unknown":
                    state = "open"
            elif kind == "resumed":
                state = "open"
            elif kind in ("waiting", "work_ready"):
                state = kind
        delta = int(state in OPEN_STATES) - int(previous in OPEN_STATES)
        result.append(Event(event_id, kind, at, basis, state, valid, delta))
    return result, requested


def validate_event_sequence(events: Sequence[Mapping]) -> None:
    """Raise DomainValidationError for impossible known chronology.

    An initial reopened/assigned/completed event is allowed for partial history.
    Tied instants use EVENT_ORDER, then event_id, independent of input order.
    """
    _events(events)


class Timeline:
    def __init__(self, task: Mapping):
        if not isinstance(task.get("task_id"), str) or not task["task_id"]:
            raise DomainValidationError("Task IDs must be nonempty strings.")
        if task.get("category") not in CATEGORIES:
            raise DomainValidationError("Unknown task category.")
        self.task = task
        self.events, self.requested = _events(task.get("events", []))
        self.times = [event.at for event in self.events]
        self.completions = [e for e in self.events if e.valid and e.kind == "completed"]
        self.completion_times = [e.at for e in self.completions]

    def state_at(self, end: datetime) -> str:
        index = bisect_left(self.times, end) - 1
        return self.events[index].state if index >= 0 else "unknown"

    def events_in(self, window: Window) -> list[Event]:
        return self.events[bisect_left(self.times, window.start):bisect_left(self.times, window.end)]

    def completions_in(self, window: Window) -> list[Event]:
        return self.completions[bisect_left(self.completion_times, window.start):
                                bisect_left(self.completion_times, window.end)]

    def last_completion(self, end: datetime) -> Event | None:
        index = bisect_left(self.completion_times, end) - 1
        return self.completions[index] if index >= 0 else None


def _matches(task: Mapping, filters: Mapping | None) -> bool:
    filters = filters or {}
    for field in ("category", "client", "assignee"):
        selected = filters.get(field)
        if selected:
            if selected == "__unknown__":
                if task.get(field) is not None:
                    return False
            elif task.get(field) != selected:
                return False
    query = (filters.get("q") or "").strip().casefold()
    if query and not any(query in str(task.get(key) or "").casefold()
                         for key in ("task_id", "title", "client", "assignee")):
        return False
    return True


def timelines(dataset: Mapping, filters: Mapping | None = None) -> list[Timeline]:
    tasks = dataset.get("tasks", [])
    seen: set[str] = set()
    result = []
    for task in tasks:
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or task_id in seen:
            raise DomainValidationError("Task IDs must be unique strings.")
        seen.add(task_id)
        if _matches(task, filters):
            result.append(Timeline(task))
    return result


def coverage(dataset: Mapping) -> dict:
    supplied = dataset.get("coverage") or {}
    return {"status": supplied.get("status", "partial"),
            "note": supplied.get("note", "Only imported evidence is known.")}


def proposal_evidence_references(proposal: Mapping) -> list[dict]:
    """Unique exact revisions referenced at proposal or event level."""
    references = list(proposal.get("evidence", []))
    for event in proposal.get("events", []):
        references.extend(event.get("evidence", []))
    seen = set()
    result = []
    for reference in references:
        key = (reference.get("message_key"), reference.get("revision_hash"))
        if not all(isinstance(value, str) and value for value in key):
            raise DomainValidationError("Evidence references must identify an exact message revision.")
        if key not in seen:
            seen.add(key)
            result.append({"message_key": key[0], "revision_hash": key[1]})
    return result


def _evidence_details(references: Sequence[Mapping], messages: Mapping) -> list[dict]:
    result = []
    seen = set()
    for reference in references:
        key = (reference["message_key"], reference["revision_hash"])
        if key in seen:
            continue
        seen.add(key)
        message = messages.get(key)
        if message is None:
            raise DomainValidationError("Referenced evidence is unavailable.")
        result.append({field: message.get(field) for field in (
            "message_key", "revision_hash", "text_plain", "sent_at", "edited_at",
            "sender_name", "source_name", "source_url", "has_conflict", "media_missing",
        )})
    return result


def pending_proposal_count(dataset: Mapping) -> int:
    return sum(row.get("state", "proposed") == "proposed" for row in dataset.get("proposals", []))


def pending_proposals(dataset: Mapping) -> list[dict]:
    messages = {(row["message_key"], row["revision_hash"]): row for row in dataset.get("messages", [])}
    return [dict(row, evidence_details=_evidence_details(proposal_evidence_references(row), messages))
            for row in dataset.get("proposals", []) if row.get("state", "proposed") == "proposed"]


def metadata(dataset: Mapping, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise DomainValidationError("Current time must include a timezone.")
    today = now.astimezone(MOSCOW).date()
    dates = [e.at.astimezone(MOSCOW).date() for t in timelines(dataset) for e in t.events]
    minimum, maximum = (min(dates), max(dates)) if dates else (None, None)
    default_to = maximum or today
    default_from = max(minimum or default_to, default_to - timedelta(days=20))
    imports = [parse_timestamp(item["completed_at"]) for item in dataset.get("imports", [])
               if item.get("completed_at") and item.get("status") == "succeeded"]
    tasks = dataset.get("tasks", [])
    return {
        "timezone": TIMEZONE, "today": today.isoformat(),
        "min_date": minimum.isoformat() if minimum else None,
        "max_date": maximum.isoformat() if maximum else None,
        "default_from": default_from.isoformat(), "default_to": default_to.isoformat(),
        "categories": [{"value": value, "label": label} for value, label in CATEGORIES.items()],
        "clients": sorted({t["client"] for t in tasks if t.get("client")}),
        "assignees": sorted({t["assignee"] for t in tasks if t.get("assignee")}),
        "coverage": coverage(dataset), "demo": bool(dataset.get("demo", False)),
        "last_import_at": iso(max(imports)) if imports else None,
        "pending_proposals": pending_proposal_count(dataset),
    }


def _buckets(window: Window, bucket: str) -> list[dict]:
    if bucket not in ("day", "week"):
        raise DomainValidationError("Bucket must be day or week.")
    result = []
    cursor = window.first
    while cursor <= window.last:
        natural_first = cursor if bucket == "day" else cursor - timedelta(days=cursor.weekday())
        natural_last = natural_first if bucket == "day" else natural_first + timedelta(days=6)
        last = min(natural_last, window.last)
        result.append({"period_start": cursor.isoformat(), "period_end": last.isoformat(),
                       "partial": cursor != natural_first or last != natural_last,
                       "incoming": 0, "completed": 0, "completion_events": 0,
                       "reopened": 0, "cancelled": 0, "observed_backlog": 0,
                       "resolution_median_hours": None, "resolution_sample_size": 0})
        cursor = last + timedelta(days=1)
    return result


def report(dataset: Mapping, first: str | date, last: str | date,
           bucket: str = "day", filters: Mapping | None = None) -> dict:
    window = date_window(first, last)
    series = _buckets(window, bucket)
    all_tasks = timelines(dataset, filters)
    observed = [t for t in all_tasks if t.events and t.events[0].at < window.end]
    metric_names = ("incoming", "completed", "completion_events", "reopened", "cancelled",
                    "observed_backlog", "resolution_median_hours", "resolution_sample_size",
                    "missing_request_count")
    summary = {name: None for name in metric_names}
    summary["pending_proposals"] = pending_proposal_count(dataset)
    result = {"timezone": TIMEZONE, "from": window.first.isoformat(), "to": window.last.isoformat(),
              "bucket": bucket, "coverage": coverage(dataset), "demo": bool(dataset.get("demo", False)),
              "empty": not observed, "summary": summary, "series": [], "breakdown": []}
    if not observed:
        return result
    summary.update({name: 0 for name in metric_names if name != "resolution_median_hours"})
    bucket_starts = [date_window(row["period_start"], row["period_end"]).start for row in series]
    deltas, samples = [0] * len(series), [[] for _ in series]
    period_samples: list[float] = []
    baseline = 0
    by_category: dict[str, dict] = {}
    for timeline in observed:
        before = timeline.state_at(window.start)
        state = timeline.state_at(window.end)
        baseline += int(before in OPEN_STATES)
        in_period = timeline.events_in(window)
        completions = [e for e in in_period if e.valid and e.kind == "completed"]
        latest_per_bucket: dict[int, Event] = {}
        if timeline.requested is not None and window.start <= timeline.requested < window.end:
            index = bisect_right(bucket_starts, timeline.requested) - 1
            series[index]["incoming"] += 1
            summary["incoming"] += 1
        for event in in_period:
            index = bisect_right(bucket_starts, event.at) - 1
            deltas[index] += event.open_delta
            if not event.valid:
                continue
            if event.kind == "completed":
                series[index]["completion_events"] += 1
                summary["completion_events"] += 1
                latest_per_bucket[index] = event
            elif event.kind in ("reopened", "cancelled"):
                series[index][event.kind] += 1
                summary[event.kind] += 1
        for index, completion in latest_per_bucket.items():
            series[index]["completed"] += 1
            duration = duration_hours(timeline.requested, completion.at)
            if duration is not None:
                samples[index].append(duration)
        if completions:
            summary["completed"] += 1
            duration = duration_hours(timeline.requested, completions[-1].at)
            if duration is None:
                summary["missing_request_count"] += 1
            else:
                period_samples.append(duration)
        summary["observed_backlog"] += int(state in OPEN_STATES)
        relevant = any(e.valid for e in in_period) or state in OPEN_STATES
        if relevant:
            category = timeline.task["category"]
            row = by_category.setdefault(category, {"category": category, "label": CATEGORIES[category],
                                                    "tasks": 0, "completed": 0, "observed_backlog": 0})
            row["tasks"] += 1
            row["completed"] += int(bool(completions))
            row["observed_backlog"] += int(state in OPEN_STATES)
    running = baseline
    for index, row in enumerate(series):
        running += deltas[index]
        row["observed_backlog"] = running
        row["resolution_sample_size"] = len(samples[index])
        row["resolution_median_hours"] = round(median(samples[index]), 6) if samples[index] else None
    summary["resolution_sample_size"] = len(period_samples)
    summary["resolution_median_hours"] = round(median(period_samples), 6) if period_samples else None
    result["series"] = series
    result["breakdown"] = [by_category[key] for key in CATEGORIES if key in by_category]
    return result


def _task_row(timeline: Timeline, end: datetime) -> dict:
    task = timeline.task
    completed = timeline.last_completion(end)
    requested = timeline.requested if timeline.requested and timeline.requested < end else None
    return {
        "task_id": task["task_id"], "title": task.get("title", ""),
        "category": task["category"], "client": task.get("client"), "assignee": task.get("assignee"),
        "status": timeline.state_at(end), "requested_at": iso(requested),
        "completed_at": iso(completed.at) if completed else None,
        "resolution_hours": duration_hours(requested, completed.at) if completed else None,
        "evidence_count": len({(e["message_key"], e["revision_hash"]) for e in task.get("evidence", [])}),
    }


def task_rows(dataset: Mapping, first: str | date, last: str | date,
              status: str = "all", metric: str = "all", filters: Mapping | None = None) -> list[dict]:
    window = date_window(first, last)
    if status not in ("all", "open", "completed", "waiting"):
        raise DomainValidationError("Unsupported status filter.")
    if metric not in ("all", "incoming", "completed", "backlog"):
        raise DomainValidationError("Unsupported metric filter.")
    selected: list[tuple[datetime, str, dict]] = []
    for timeline in timelines(dataset, filters):
        state = timeline.state_at(window.end)
        if status == "open" and state not in OPEN_STATES:
            continue
        if status in ("completed", "waiting") and state != status:
            continue
        if metric == "backlog":
            keep = state in OPEN_STATES
        elif metric == "incoming":
            keep = timeline.requested is not None and window.start <= timeline.requested < window.end
        elif metric == "completed":
            keep = bool(timeline.completions_in(window))
        else:
            keep = state in OPEN_STATES or any(e.valid for e in timeline.events_in(window))
        if keep:
            index = bisect_left(timeline.times, window.end) - 1
            selected.append((timeline.events[index].at, timeline.task["task_id"], _task_row(timeline, window.end)))
    # Recent observed event first; stable task_id breaks ties without numeric coercion.
    selected.sort(key=lambda item: item[1])
    selected.sort(key=lambda item: item[0], reverse=True)
    return [item[2] for item in selected]


def task_page(dataset: Mapping, first: str | date, last: str | date, status: str = "all",
              metric: str = "all", page: int = 1, page_size: int = 50,
              filters: Mapping | None = None) -> dict:
    if not isinstance(page, int) or page < 1 or not isinstance(page_size, int) or not 1 <= page_size <= 200:
        raise DomainValidationError("Page must be positive and page_size must be between 1 and 200.")
    rows = task_rows(dataset, first, last, status, metric, filters)
    offset = (page - 1) * page_size
    return {"rows": rows[offset:offset + page_size], "total": len(rows), "page": page,
            "page_size": page_size, "has_more": offset + page_size < len(rows),
            "coverage": coverage(dataset), "demo": bool(dataset.get("demo", False))}


def task_detail(dataset: Mapping, task_id: str) -> dict | None:
    task = next((row for row in dataset.get("tasks", []) if row["task_id"] == task_id), None)
    if task is None:
        return None
    timeline = Timeline(task)
    messages = {(row["message_key"], row["revision_hash"]): row for row in dataset.get("messages", [])}
    evidence = _evidence_details(task.get("evidence", []), messages)
    final = timeline.events[-1].at + timedelta(microseconds=1) if timeline.events else datetime.now(UTC)
    row = _task_row(timeline, final)
    row.update({"external_refs": task.get("external_refs", {}), "revision": task.get("revision", 1)})
    return {"task": row, "events": [event.as_dict() for event in timeline.events],
            "evidence": evidence, "coverage": coverage(dataset), "demo": bool(dataset.get("demo", False))}

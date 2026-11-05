"""Read-only analytics over the canonical ticketing reporting views."""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from activity.repository import ConfigurationError, RepositoryError, positive_setting


MOSCOW = ZoneInfo("Europe/Moscow")
PROFILES = frozenset(("kalisto", "mephisto"))
PERIODS = frozenset((7, 30, 90))


def _json(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json(item) for item in value]
    return value


class TicketAnalyticsRepository:
    """Small, bounded reader. Request values never become SQL fragments."""

    def _configuration(self) -> dict[str, Any]:
        names = {"host": "DB_HOST", "dbname": "DB_NAME", "user": "DB_USER", "password": "DB_PASSWORD"}
        config = {key: os.environ.get(name, "") for key, name in names.items()}
        if any(not value for value in config.values()):
            raise ConfigurationError()
        config["port"] = positive_setting("DB_PORT", 5432, 65535)
        config.update(
            connect_timeout=5,
            application_name="ticket-analytics-reader",
            options="-c statement_timeout=8000 -c lock_timeout=2000 -c default_transaction_read_only=on",
        )
        return config

    @contextmanager
    def _transaction(self):
        try:
            import psycopg
            from psycopg.rows import dict_row

            with psycopg.connect(**self._configuration(), row_factory=dict_row) as connection:
                with connection.transaction():
                    connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                    yield connection
        except RepositoryError:
            raise
        except Exception:
            raise RepositoryError() from None

    @staticmethod
    def validate(period: int, profile: str) -> None:
        if period not in PERIODS or (profile != "all" and profile not in PROFILES):
            raise ValueError("Unsupported analytics filter")

    @staticmethod
    def _bounds(period: int) -> tuple[datetime, datetime]:
        today = datetime.now(MOSCOW).date()
        start = datetime.combine(today - timedelta(days=period - 1), datetime.min.time(), MOSCOW)
        end = datetime.combine(today + timedelta(days=1), datetime.min.time(), MOSCOW)
        return start, end

    @staticmethod
    def _profile_clause(profile: str, alias: str = "f") -> tuple[str, tuple[Any, ...]]:
        return (f" AND {alias}.profile_key=%s", (profile,)) if profile != "all" else ("", ())

    def health(self) -> None:
        with self._transaction() as connection:
            connection.execute("SELECT 1 FROM reporting.ticket_facts LIMIT 1").fetchone()

    def summary(self, period: int, profile: str) -> dict[str, Any]:
        self.validate(period, profile)
        start, end = self._bounds(period)
        facts_filter, facts_params = self._profile_clause(profile)
        with self._transaction() as connection:
            facts = connection.execute(
                "SELECT count(*) FILTER (WHERE f.status_code='open') AS open_now,"
                " count(*) FILTER (WHERE f.created_at >= %s AND f.created_at < %s) AS created "
                "FROM reporting.ticket_facts f WHERE true" + facts_filter,
                (start, end, *facts_params),
            ).fetchone()
            transitions = connection.execute(
                "SELECT count(*) AS completed_unique,"
                " percentile_cont(0.5) WITHIN GROUP (ORDER BY f.resolution_seconds)"
                " AS median_resolution_seconds"
                " FROM reporting.ticket_facts f WHERE f.status_code='completed'"
                " AND f.closed_at >= %s AND f.closed_at < %s" + facts_filter,
                (start, end, *facts_params),
            ).fetchone()
            flow = connection.execute(
                "SELECT activity_date,created_count,completed_count,reopened_count"
                " FROM reporting.ticket_daily_flow WHERE activity_date >= %s AND activity_date < %s" +
                (" AND profile_key=%s" if profile != "all" else "") +
                " ORDER BY activity_date",
                (start.date(), end.date(), *((profile,) if profile != "all" else ())),
            ).fetchall()
            age_rows = connection.execute(
                "SELECT CASE WHEN now()-f.created_at < interval '1 day' THEN 'lt_1d'"
                " WHEN now()-f.created_at < interval '3 days' THEN 'd1_3'"
                " WHEN now()-f.created_at < interval '7 days' THEN 'd3_7' ELSE 'gte_7d' END AS bucket,"
                " count(*) AS count FROM reporting.ticket_facts f"
                " WHERE f.status_code='open'" + facts_filter + " GROUP BY 1",
                facts_params,
            ).fetchall()
            profiles = connection.execute(
                "SELECT f.profile_key,max(f.profile_name) AS profile_name,"
                " count(*) FILTER (WHERE f.status_code='open') AS open_now,"
                " count(*) FILTER (WHERE f.created_at >= %s AND f.created_at < %s) AS created,"
                " count(*) FILTER (WHERE f.status_code='completed'"
                " AND f.closed_at >= %s AND f.closed_at < %s) AS completed"
                " FROM reporting.ticket_facts f"
                " GROUP BY f.profile_key ORDER BY f.profile_key",
                (start, end, start, end),
            ).fetchall()
            types = connection.execute(
                "SELECT f.ticket_type,f.ticket_type_name,count(*) AS count"
                " FROM reporting.ticket_facts f WHERE f.created_at >= %s AND f.created_at < %s" +
                facts_filter + " GROUP BY f.ticket_type,f.ticket_type_name ORDER BY count DESC",
                (start, end, *facts_params),
            ).fetchall()

        days = {start.date() + timedelta(days=index): {"created": 0, "completed": 0, "reopened": 0}
                for index in range(period)}
        for row in flow:
            item = days[row["activity_date"]]
            item["created"] += int(row["created_count"])
            item["completed"] += int(row["completed_count"])
            item["reopened"] += int(row["reopened_count"])
        completion_events = sum(item["completed"] for item in days.values())
        reopened = sum(item["reopened"] for item in days.values())
        median = transitions["median_resolution_seconds"]
        return _json({
            "period": period,
            "profile": profile,
            "from": start.date(),
            "to": (end - timedelta(days=1)).date(),
            "metrics": {
                "open_now": int(facts["open_now"]),
                "created": int(facts["created"]),
                "completed_unique": int(transitions["completed_unique"]),
                "completion_events": completion_events,
                "reopened": reopened,
                "median_resolution_seconds": int(median) if median is not None else None,
            },
            "flow": [{"date": key, **value} for key, value in days.items()],
            "age": {row["bucket"]: int(row["count"]) for row in age_rows},
            "profiles": profiles,
            "types": types,
        })

    def tickets(self, status: str, profile: str, page: int, page_size: int = 20) -> dict[str, Any]:
        if status not in {"open", "completed", "cancelled"}:
            raise ValueError("Unsupported status")
        self.validate(30, profile)
        if page < 1:
            raise ValueError("Unsupported page")
        clause, params = self._profile_clause(profile)
        offset = (page - 1) * page_size
        with self._transaction() as connection:
            total = connection.execute(
                "SELECT count(*) AS count FROM reporting.ticket_facts f WHERE f.status_code=%s" + clause,
                (status, *params),
            ).fetchone()["count"]
            rows = connection.execute(
                "SELECT f.ticket_id,f.profile_key,f.profile_name,f.status_code,f.status_name,"
                " f.ticket_type,f.ticket_type_name,f.title,f.description,f.created_at,f.updated_at,f.closed_at,"
                " f.resolution_seconds,f.reopen_count,f.public_message_count,f.internal_note_count"
                " FROM reporting.ticket_facts f WHERE f.status_code=%s" + clause +
                " ORDER BY coalesce(f.closed_at,f.updated_at) DESC,f.ticket_id DESC LIMIT %s OFFSET %s",
                (status, *params, page_size, offset),
            ).fetchall()
        return _json({"items": rows, "page": page, "total": int(total),
                      "has_previous": page > 1, "has_next": offset + len(rows) < total})

    def ticket(self, ticket_id: int) -> dict[str, Any] | None:
        if ticket_id < 1:
            return None
        with self._transaction() as connection:
            ticket = connection.execute(
                "SELECT f.ticket_id,f.profile_key,f.profile_name,f.status_code,f.status_name,"
                " f.ticket_type,f.ticket_type_name,f.title,f.description,f.created_at,f.updated_at,f.closed_at,"
                " f.resolution_seconds,f.reopen_count,f.public_message_count,f.internal_note_count"
                " FROM reporting.ticket_facts f WHERE f.ticket_id=%s",
                (ticket_id,),
            ).fetchone()
            if ticket is None:
                return None
            conversation = connection.execute(
                "SELECT visibility,author_role,body,sent_at FROM reporting.ticket_conversation"
                " WHERE ticket_id=%s ORDER BY sent_at,message_id LIMIT 200",
                (ticket_id,),
            ).fetchall()
        return _json({**ticket, "conversation": conversation})

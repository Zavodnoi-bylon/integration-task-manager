"""Reader-only PostgreSQL snapshot access.

No SQL is accepted from requests. Every response is built from a single READ ONLY
REPEATABLE READ transaction, including classifications, history and evidence.
Connections are short-lived; the HTTP layer bounds concurrent snapshots.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from .domain import proposal_evidence_references


class RepositoryError(RuntimeError):
    status_code = 503
    code = "database_unavailable"
    message = "The activity database is unavailable. Check the local service configuration."

    def __init__(self):
        super().__init__(self.message)


class ConfigurationError(RepositoryError):
    code = "configuration_error"
    message = "PostgreSQL reader configuration is missing or invalid. Configure DB_HOST, DB_NAME, DB_USER and DB_PASSWORD."


class SnapshotLimitError(RepositoryError):
    status_code = 413
    code = "snapshot_limit_exceeded"
    message = "The complete dataset exceeds the configured safety limit. No partial result was returned."


def positive_setting(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (ValueError, TypeError):
        raise ConfigurationError() from None
    if not 1 <= value <= maximum:
        raise ConfigurationError()
    return value


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


class PostgresRepository:
    mode = "postgres"

    def _configuration(self) -> dict:
        names = {"host": "DB_HOST", "dbname": "DB_NAME", "user": "DB_USER", "password": "DB_PASSWORD"}
        config = {key: os.environ.get(name, "") for key, name in names.items()}
        if any(not value for value in config.values()):
            raise ConfigurationError()
        config["port"] = positive_setting("DB_PORT", 5432, 65535)
        config.update(connect_timeout=5, application_name="team-activity-reader",
                      options="-c statement_timeout=10000 -c lock_timeout=3000 -c default_transaction_read_only=on")
        return config

    @contextmanager
    def _transaction(self):
        config = self._configuration()
        try:
            import psycopg
            from psycopg.rows import dict_row
            with psycopg.connect(**config, row_factory=dict_row) as connection:
                with connection.transaction():
                    connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                    yield connection
        except RepositoryError:
            raise
        except Exception:
            # Deliberately never expose exception text: drivers can include a DSN,
            # hostname, SQL fragment or credential in their diagnostic message.
            raise RepositoryError() from None

    def health(self) -> None:
        with self._transaction() as connection:
            connection.execute("SELECT 1").fetchone()

    @staticmethod
    def _bounded(connection, sql: str, maximum: int, params: tuple = ()) -> list[dict]:
        rows = connection.execute(sql + " LIMIT %s", (*params, maximum + 1)).fetchall()
        if len(rows) > maximum:
            raise SnapshotLimitError()
        return rows

    def snapshot(self) -> dict:
        task_limit = positive_setting("ACTIVITY_MAX_TASKS", 100_000, 1_000_000)
        event_limit = positive_setting("ACTIVITY_MAX_EVENTS", 500_000, 5_000_000)
        evidence_limit = positive_setting("ACTIVITY_MAX_EVIDENCE", 500_000, 5_000_000)
        proposal_limit = positive_setting("ACTIVITY_MAX_PROPOSALS", 10_000, 100_000)
        byte_limit = positive_setting("ACTIVITY_MAX_SNAPSHOT_BYTES", 128 * 1024 * 1024, 1024 * 1024 * 1024)
        with self._transaction() as connection:
            settings = connection.execute(
                "SELECT coverage_status, coverage_note FROM ops.dataset WHERE id = 1"
            ).fetchone()
            tasks = self._bounded(connection,
                "SELECT task_id, title, category, client, assignee, external_refs, revision "
                "FROM work.tasks ORDER BY task_id", task_limit)
            events = self._bounded(connection,
                "SELECT event_id, task_id, kind, occurred_at, time_basis "
                "FROM work.events ORDER BY task_id, occurred_at, event_id", event_limit)
            evidence = self._bounded(connection,
                "SELECT task_id, event_id, message_key, revision_hash "
                "FROM work.evidence ORDER BY task_id, evidence_id", evidence_limit)
            proposals = self._bounded(connection,
                "SELECT proposal_id, task_id, version, document, state, created_at "
                "FROM work.proposals WHERE state = 'proposed' ORDER BY created_at, proposal_id", proposal_limit)
            pairs = {(row["message_key"], row["revision_hash"]) for row in evidence}
            proposal_pairs = []
            for proposal in proposals:
                references = proposal_evidence_references(proposal["document"])
                keys = {(row["message_key"], row["revision_hash"]) for row in references}
                proposal_pairs.append(keys)
                pairs.update(keys)
                if len(pairs) > evidence_limit:
                    raise SnapshotLimitError()
            pair_json = json.dumps([{"message_key": key, "revision_hash": revision}
                                    for key, revision in sorted(pairs)])
            if len(pair_json.encode("utf-8")) > byte_limit:
                raise SnapshotLimitError()
            # Bound source text before materializing it; unrelated raw chat history
            # and raw_json never enter the web process or an API response. Pending
            # review uses the exact referenced revision, never the latest edit.
            reference_join = (
                "JOIN jsonb_to_recordset(%s::jsonb) AS refs(message_key text, revision_hash text) "
                "ON refs.message_key = r.message_key AND refs.revision_hash = r.revision_hash "
            )
            payload_size = connection.execute(
                "SELECT COALESCE(sum(octet_length(r.text_plain)), 0) AS bytes "
                "FROM raw.message_revisions r " + reference_join, (pair_json,)
            ).fetchone()["bytes"]
            if payload_size > byte_limit:
                raise SnapshotLimitError()
            messages = self._bounded(connection,
                "SELECT r.message_key, r.revision_hash, r.text_plain, r.sent_at, r.edited_at, "
                "r.sender_name, s.display_name AS source_name, r.source_url, "
                "m.has_conflict, r.media_missing "
                "FROM raw.message_revisions r " + reference_join +
                "JOIN raw.messages m ON m.message_key = r.message_key "
                "JOIN raw.sources s ON s.source_id = m.source_id "
                "ORDER BY r.message_key, r.revision_hash", evidence_limit, (pair_json,))
            if len(messages) != len(pairs):
                raise RepositoryError()
            # A revision shared by many candidates is fetched once, but is shown
            # in each candidate. Bound that expanded review response as well.
            message_sizes = {(row["message_key"], row["revision_hash"]):
                             len(json.dumps(_json_value(row), ensure_ascii=False).encode("utf-8"))
                             for row in messages}
            expanded_review_bytes = 0
            for proposal, keys in zip(proposals, proposal_pairs):
                expanded_review_bytes += len(json.dumps(_json_value(proposal), ensure_ascii=False).encode("utf-8"))
                expanded_review_bytes += sum(message_sizes[key] for key in keys)
                if expanded_review_bytes > byte_limit:
                    raise SnapshotLimitError()
            imported = connection.execute(
                "SELECT max(completed_at) AS completed_at FROM ops.import_runs WHERE status = 'succeeded'"
            ).fetchone()
            by_id = {task["task_id"]: dict(task, events=[], evidence=[]) for task in tasks}
            for event in events:
                by_id[event["task_id"]]["events"].append({key: value for key, value in event.items() if key != "task_id"})
            for reference in evidence:
                by_id[reference["task_id"]]["evidence"].append({key: value for key, value in reference.items() if key != "task_id"})
            pending = []
            for proposal in proposals:
                document = dict(proposal["document"])
                # Stored review metadata wins over any candidate document field.
                document.update({key: value for key, value in proposal.items() if key != "document"})
                pending.append(document)
            dataset = _json_value({
                "coverage": {"status": settings["coverage_status"] if settings else "partial",
                             "note": settings["coverage_note"] if settings else "Only imported evidence is known."},
                "demo": False, "tasks": list(by_id.values()), "proposals": pending,
                "messages": messages,
                "imports": [{"status": "succeeded", "completed_at": imported["completed_at"]}]
                           if imported and imported["completed_at"] else [],
            })
            if len(json.dumps(dataset, ensure_ascii=False).encode("utf-8")) > byte_limit:
                raise SnapshotLimitError()
            return dataset

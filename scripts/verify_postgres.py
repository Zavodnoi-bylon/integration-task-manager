from __future__ import annotations

import argparse
from pathlib import Path

import psycopg
from psycopg import ClientCursor


def apply_sql(connection: psycopg.Connection, path: Path) -> None:
    with ClientCursor(connection) as cursor:
        cursor.execute(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--canonical", type=Path, required=True)
    args = parser.parse_args()
    extension = Path(__file__).resolve().parents[1] / "migrations" / "0002_miniapp_workflow.sql"
    with psycopg.connect(args.dsn) as connection:
        installed = connection.execute(
            "SELECT to_regclass('ticketing.tickets')"
        ).fetchone()[0]
        if installed is None:
            apply_sql(connection, args.canonical)
        apply_sql(connection, extension)
        apply_sql(connection, extension)
        row = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM ticketing.ticket_statuses
               WHERE status_code='review') AS review_status,
              to_regclass('ticketing.miniapp_sessions') AS sessions,
              to_regclass('ticketing.miniapp_idempotency') AS idempotency,
              to_regclass('reporting.ticket_review_cycles') AS cycles,
              to_regclass('reporting.ticket_review_backlog') AS backlog
            """
        ).fetchone()
        if not row or row[0] != 1 or any(value is None for value in row[1:]):
            raise RuntimeError(f"PostgreSQL verification failed: {row!r}")
    print("PostgreSQL restore and repeat-apply verification passed")


if __name__ == "__main__":
    main()

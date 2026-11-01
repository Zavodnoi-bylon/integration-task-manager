"""Destructive verification for a disposable PostgreSQL database."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Jsonb

from migrate_postgres import apply_migrations


def main() -> None:
    dsn = os.getenv("DATABASE_MIGRATION_URL", "").strip()
    if not dsn:
        raise SystemExit("DATABASE_MIGRATION_URL is required")
    with psycopg.connect(dsn) as connection:
        database_name = connection.execute("SELECT current_database()").fetchone()[0]
    if not str(database_name).startswith("integration_task_manager_verify_"):
        raise SystemExit(
            "Refusing non-disposable database; expected name prefix "
            "integration_task_manager_verify_"
        )

    first = apply_migrations(dsn)
    second = apply_migrations(dsn)
    if first != ["0001_ticketing_schema.sql"] or second:
        raise AssertionError(f"Migration idempotence failed: first={first}, second={second}")

    now = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
    with psycopg.connect(dsn) as connection:
        with connection.transaction(force_rollback=True):
            register = "SELECT ticketing.register_member(%s,%s,%s,%s,%s,%s)"
            connection.execute(
                register,
                ("kalisto", 990000000001, "verify_admin", "Verify Admin", "admin", now),
            )
            connection.execute(
                register,
                ("kalisto", 990000000002, "verify_client", "Verify Client", "client", now),
            )
            ticket_id = connection.execute(
                "SELECT ticketing.create_ticket(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    "kalisto",
                    990000000002,
                    990000000001,
                    "Migration verification ticket",
                    now,
                    "migration-verification:ticket",
                    "telegram",
                    None,
                    Jsonb({}),
                    "destination",
                ),
            ).fetchone()[0]
            connection.execute(
                "SELECT ticketing.add_message(%s,%s,%s,%s,%s,%s,%s)",
                (
                    "kalisto",
                    ticket_id,
                    990000000002,
                    "public",
                    "Original offer",
                    now,
                    "migration-verification:message",
                ),
            )
            connection.execute(
                "SELECT ticketing.change_status(%s,%s,%s,%s,%s,%s)",
                (
                    "kalisto",
                    ticket_id,
                    990000000001,
                    "completed",
                    now,
                    "migration-verification:completed",
                ),
            )
            fact = connection.execute(
                "SELECT status_code,ticket_type,public_message_count "
                "FROM reporting.ticket_facts WHERE ticket_id=%s",
                (ticket_id,),
            ).fetchone()
            if fact != ("completed", "destination", 1):
                raise AssertionError(f"Unexpected reporting fact: {fact}")
            if connection.execute(
                "SELECT count(*) FROM ticketing.schema_migrations"
            ).fetchone()[0] != 1:
                raise AssertionError("Migration journal was not populated")

    print("POSTGRES_MIGRATIONS_VERIFIED")


if __name__ == "__main__":
    main()

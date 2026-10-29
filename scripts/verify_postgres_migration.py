"""Verify the one-time SQLite migration without printing ticket contents."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from migrate_sqlite_to_postgres import STATUS_MAP, conninfo


def verify(sqlite_path: Path) -> dict[str, int]:
    source = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        source_memberships = {
            (row["bot_key"], int(row["tg_id"]), row["role"])
            for row in source.execute("SELECT bot_key,tg_id,role FROM users")
        }
        source_tickets = source.execute(
            "SELECT * FROM ticket ORDER BY bot_key,number_ticket"
        ).fetchall()

        with psycopg.connect(conninfo(), row_factory=dict_row) as target:
            target_memberships = {
                (row["profile_key"], int(row["telegram_user_id"]), row["role"])
                for row in target.execute(
                    "SELECT m.profile_key,u.telegram_user_id,m.role "
                    "FROM ticketing.memberships m "
                    "JOIN ticketing.telegram_users u USING(user_id)"
                )
            }
            if not source_memberships.issubset(target_memberships):
                raise AssertionError("PostgreSQL memberships differ from SQLite")

            imported = target.execute(
                "SELECT t.ticket_id,t.profile_key,t.legacy_sqlite_id,t.description,"
                "t.status_code,t.metadata,requester.telegram_user_id AS requester_id,"
                "assigned.telegram_user_id AS assigned_id "
                "FROM ticketing.tickets t "
                "JOIN ticketing.telegram_users requester ON requester.user_id=t.requester_user_id "
                "LEFT JOIN ticketing.telegram_users assigned ON assigned.user_id=t.assigned_user_id "
                "WHERE t.source='import' ORDER BY t.profile_key,t.legacy_sqlite_id"
            ).fetchall()
            if len(imported) != len(source_tickets):
                raise AssertionError("PostgreSQL ticket count differs from SQLite")

            target_by_legacy = {
                (row["profile_key"], int(row["legacy_sqlite_id"])): row
                for row in imported
            }
            expected_message_keys: set[tuple[str, str]] = set()
            expected_status_keys: set[tuple[str, str]] = set()

            for ticket in source_tickets:
                profile = ticket["bot_key"]
                legacy_id = int(ticket["number_ticket"])
                row = target_by_legacy.get((profile, legacy_id))
                if row is None:
                    raise AssertionError(f"Missing imported ticket {profile}/{legacy_id}")
                legacy_assigned = (
                    int(ticket["assigned_to"]) if ticket["assigned_to"] is not None else None
                )
                assigned_role = next(
                    (role for profile_key, telegram_id, role in source_memberships
                     if profile_key == profile and telegram_id == legacy_assigned),
                    None,
                )
                expected_assigned = (
                    legacy_assigned if assigned_role in {"admin", "technician"} else None
                )
                expected = (
                    ticket["message_ticket"],
                    STATUS_MAP.get(ticket["state_ticket"], ticket["state_ticket"]),
                    int(ticket["tg_id_ticket"]),
                    expected_assigned,
                )
                actual = (
                    row["description"], row["status_code"], int(row["requester_id"]),
                    int(row["assigned_id"]) if row["assigned_id"] is not None else None,
                )
                if actual != expected:
                    raise AssertionError(f"Ticket fields differ for {profile}/{legacy_id}")
                if expected_assigned is None and legacy_assigned is not None:
                    if row["metadata"].get("migration_anomaly") != (
                        "assigned_user_not_staff_in_profile"
                    ) or int(row["metadata"].get("legacy_assigned_telegram_id", 0)) != legacy_assigned:
                        raise AssertionError(
                            f"Assignment anomaly was not preserved for {profile}/{legacy_id}"
                        )

                current_status = "open"
                has_comment = False
                events = source.execute(
                    "SELECT * FROM ticket_events WHERE ticket_id=? ORDER BY event_id",
                    (legacy_id,),
                ).fetchall()
                for event in events:
                    key = f"sqlite:{profile}:event:{event['event_id']}"
                    if event["event_type"] == "comment" and event["event_text"]:
                        has_comment = True
                        expected_message_keys.add((profile, key))
                    elif event["event_type"] == "status_changed":
                        to_status = STATUS_MAP.get(event["to_status"], event["to_status"])
                        if to_status != current_status:
                            expected_status_keys.add((profile, key))
                            current_status = to_status

                if ticket["ticket_comm"] and not has_comment:
                    expected_message_keys.add(
                        (profile, f"sqlite:{profile}:ticket:{legacy_id}:legacy-comment")
                    )
                desired_status = STATUS_MAP.get(ticket["state_ticket"], ticket["state_ticket"])
                if desired_status != current_status:
                    expected_status_keys.add(
                        (profile, f"sqlite:{profile}:ticket:{legacy_id}:final-status")
                    )

            actual_message_keys = {
                (row["profile_key"], row["idempotency_key"])
                for row in target.execute(
                    "SELECT profile_key,idempotency_key FROM ticketing.ticket_messages "
                    "WHERE idempotency_key LIKE 'sqlite:%'"
                )
            }
            if actual_message_keys != expected_message_keys:
                raise AssertionError("PostgreSQL message history differs from SQLite")

            actual_status_keys = {
                (row["profile_key"], row["idempotency_key"])
                for row in target.execute(
                    "SELECT profile_key,idempotency_key FROM ticketing.ticket_events "
                    "WHERE event_type='status_changed' AND idempotency_key LIKE 'sqlite:%'"
                )
            }
            if actual_status_keys != expected_status_keys:
                raise AssertionError("PostgreSQL status history differs from SQLite")

            created_count = target.execute(
                "SELECT count(*) AS count FROM ticketing.ticket_events "
                "WHERE event_type='created' AND idempotency_key LIKE 'ticket:sqlite:%'"
            ).fetchone()["count"]
            if created_count != len(source_tickets):
                raise AssertionError("Each imported ticket must have one creation event")

            facts_count = target.execute(
                "SELECT count(*) AS count FROM reporting.ticket_facts f "
                "JOIN ticketing.tickets t USING(ticket_id) WHERE t.source='import'"
            ).fetchone()["count"]
            if facts_count != len(source_tickets):
                raise AssertionError("Reporting facts do not match imported tickets")

            return {
                "memberships": len(source_memberships),
                "tickets": len(source_tickets),
                "messages": len(expected_message_keys),
                "status_events": len(expected_status_keys),
                "created_events": int(created_count),
            }
    finally:
        source.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sqlite_path", type=Path)
    args = parser.parse_args()
    if not args.sqlite_path.is_file():
        parser.error(f"SQLite database not found: {args.sqlite_path}")
    print("POSTGRES_MIGRATION_VERIFIED", verify(args.sqlite_path))


if __name__ == "__main__":
    main()

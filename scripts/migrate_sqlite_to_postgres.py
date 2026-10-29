"""One-time, idempotent migration from the MVP SQLite database to PostgreSQL."""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb


MSK = ZoneInfo("Europe/Moscow")
STATUS_MAP = {"В работе": "open", "Завершена": "completed"}


def timestamp(value: str | None) -> dt.datetime:
    if not value:
        return dt.datetime.now(MSK)
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=MSK) if parsed.tzinfo is None else parsed


def conninfo() -> str:
    required = (
        "TICKETING_DB_HOST", "TICKETING_DB_PORT", "TICKETING_DB_NAME",
        "TICKETING_DB_USER", "TICKETING_DB_PASSWORD",
    )
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing database settings: {', '.join(missing)}")
    return make_conninfo(
        host=os.environ["TICKETING_DB_HOST"],
        port=os.environ["TICKETING_DB_PORT"],
        dbname=os.environ["TICKETING_DB_NAME"],
        user=os.environ["TICKETING_DB_USER"],
        password=os.environ["TICKETING_DB_PASSWORD"],
        application_name="integration-task-manager-migration",
    )


def migrate(sqlite_path: Path) -> dict[str, int]:
    source = sqlite3.connect(sqlite_path)
    source.row_factory = sqlite3.Row
    counts = {"users": 0, "tickets": 0, "messages": 0, "status_events": 0}
    try:
        with psycopg.connect(conninfo()) as target:
            users = source.execute("SELECT * FROM users ORDER BY bot_key,tg_id").fetchall()
            membership_roles = {
                (user["bot_key"], int(user["tg_id"])): user["role"] for user in users
            }
            for user in users:
                username = user["user_name"] or f"ID {user['tg_id']}"
                target.execute(
                    "SELECT ticketing.register_member(%s,%s,%s,%s,%s,%s)",
                    (
                        user["bot_key"], user["tg_id"],
                        username[1:] if username.startswith("@") else None,
                        username, user["role"], timestamp(user["data_reg"]),
                    ),
                )
                counts["users"] += 1

            tickets = source.execute("SELECT * FROM ticket ORDER BY number_ticket").fetchall()
            for ticket in tickets:
                profile = ticket["bot_key"]
                legacy_id = int(ticket["number_ticket"])
                owner_id = int(ticket["tg_id_ticket"])
                legacy_assigned_id = ticket["assigned_to"]
                assigned_id = int(legacy_assigned_id) if legacy_assigned_id is not None else None
                assigned_role = membership_roles.get((profile, assigned_id))
                metadata: dict[str, object] = {}
                if assigned_id is not None and assigned_role not in {"admin", "technician"}:
                    metadata = {
                        "migration_anomaly": "assigned_user_not_staff_in_profile",
                        "legacy_assigned_telegram_id": assigned_id,
                    }
                    assigned_id = None
                request_key = f"sqlite:{profile}:ticket:{legacy_id}"
                new_ticket_id = target.execute(
                    "SELECT ticketing.create_ticket(%s,%s,%s,%s,%s,%s,'import',%s,%s)",
                    (
                        profile, owner_id, assigned_id, ticket["message_ticket"],
                        timestamp(ticket["time_ticket"]), request_key, legacy_id,
                        Jsonb(metadata),
                    ),
                ).fetchone()[0]
                counts["tickets"] += 1

                events = source.execute(
                    "SELECT * FROM ticket_events WHERE ticket_id=? ORDER BY event_id",
                    (legacy_id,),
                ).fetchall()
                has_comment = False
                current_status = "open"
                for event in events:
                    event_type = event["event_type"]
                    actor_id = int(event["actor_tg_id"] or 0)
                    occurred_at = timestamp(event["created_at"])
                    if event_type == "comment" and event["event_text"]:
                        has_comment = True
                        actor_id = actor_id or int(assigned_id or owner_id)
                        target.execute(
                            "SELECT ticketing.add_message(%s,%s,%s,'internal',%s,%s,%s)",
                            (
                                profile, new_ticket_id, actor_id, event["event_text"],
                                occurred_at, f"sqlite:{profile}:event:{event['event_id']}",
                            ),
                        )
                        counts["messages"] += 1
                    elif event_type == "status_changed":
                        to_status = STATUS_MAP.get(event["to_status"], event["to_status"])
                        if to_status == current_status:
                            continue
                        actor_id = actor_id or int(
                            assigned_id if to_status == "completed" and assigned_id else owner_id
                        )
                        target.execute(
                            "SELECT ticketing.change_status(%s,%s,%s,%s,%s,%s)",
                            (
                                profile, new_ticket_id, actor_id, to_status, occurred_at,
                                f"sqlite:{profile}:event:{event['event_id']}",
                            ),
                        )
                        current_status = to_status
                        counts["status_events"] += 1

                if ticket["ticket_comm"] and not has_comment:
                    actor_id = int(assigned_id or owner_id)
                    target.execute(
                        "SELECT ticketing.add_message(%s,%s,%s,'internal',%s,%s,%s)",
                        (
                            profile, new_ticket_id, actor_id, ticket["ticket_comm"],
                            timestamp(ticket["updated_at"]),
                            f"sqlite:{profile}:ticket:{legacy_id}:legacy-comment",
                        ),
                    )
                    counts["messages"] += 1

                desired_status = STATUS_MAP.get(ticket["state_ticket"], ticket["state_ticket"])
                if desired_status != current_status:
                    actor_id = int(
                        assigned_id if desired_status == "completed" and assigned_id else owner_id
                    )
                    target.execute(
                        "SELECT ticketing.change_status(%s,%s,%s,%s,%s,%s)",
                        (
                            profile, new_ticket_id, actor_id, desired_status,
                            timestamp(ticket["closed_at"] or ticket["updated_at"]),
                            f"sqlite:{profile}:ticket:{legacy_id}:final-status",
                        ),
                    )
                    counts["status_events"] += 1
    finally:
        source.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sqlite_path", type=Path)
    args = parser.parse_args()
    if not args.sqlite_path.is_file():
        parser.error(f"SQLite database not found: {args.sqlite_path}")
    print(migrate(args.sqlite_path))


if __name__ == "__main__":
    main()

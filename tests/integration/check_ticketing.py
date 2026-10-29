"""Synthetic ticketing contract checks; every write is rolled back."""
from datetime import datetime, timezone

import psycopg


DSN = "host=127.0.0.1 port=55432 dbname=postgres user=postgres"
NOW = datetime(2026, 10, 29, 9, 0, tzinfo=timezone.utc)


def rejects(conn, statement, params):
    try:
        with conn.transaction():
            conn.execute(statement, params)
    except psycopg.Error:
        return
    raise AssertionError("Expected PostgreSQL to reject the operation")


with psycopg.connect(DSN) as conn:
    with conn.transaction(force_rollback=True):
        register = "SELECT ticketing.register_member(%s,%s,%s,%s,%s,%s)"
        for profile, tg_id, username, role in (
            ("kalisto", 1001, "common_admin", "admin"),
            ("mephisto", 1001, "common_admin", "admin"),
            ("kalisto", 2001, "kalisto_tech", "technician"),
            ("mephisto", 2002, "mephisto_tech", "technician"),
            ("kalisto", 3001, "kalisto_client", "client"),
            ("mephisto", 3002, "mephisto_client", "client"),
        ):
            conn.execute(register, (profile, tg_id, username, f"@{username}", role, NOW))

        create = "SELECT ticketing.create_ticket(%s,%s,%s,%s,%s,%s)"
        ticket_id = conn.execute(
            create,
            ("kalisto", 3001, 2001, "Synthetic request", NOW, "update:1"),
        ).fetchone()[0]
        replay_id = conn.execute(
            create,
            ("kalisto", 3001, 2001, "Synthetic request", NOW, "update:1"),
        ).fetchone()[0]
        assert replay_id == ticket_id

        add_message = "SELECT ticketing.add_message(%s,%s,%s,%s,%s,%s,%s)"
        public_id = conn.execute(
            add_message,
            ("kalisto", ticket_id, 3001, "public", "Question", NOW, "message:1"),
        ).fetchone()[0]
        assert conn.execute(
            add_message,
            ("kalisto", ticket_id, 3001, "public", "Question", NOW, "message:1"),
        ).fetchone()[0] == public_id

        rejects(
            conn,
            add_message,
            ("kalisto", ticket_id, 3001, "internal", "Hidden", NOW, "message:2"),
        )
        conn.execute(
            add_message,
            ("kalisto", ticket_id, 2001, "internal", "Internal note", NOW, "message:3"),
        )
        conn.execute(
            add_message,
            ("kalisto", ticket_id, 2001, "public", "Answer", NOW, "message:4"),
        )

        change_status = "SELECT ticketing.change_status(%s,%s,%s,%s,%s,%s)"
        rejects(
            conn,
            change_status,
            ("kalisto", ticket_id, 3001, "completed", NOW, "status:1"),
        )
        assert conn.execute(
            change_status,
            ("kalisto", ticket_id, 2001, "completed", NOW, "status:2"),
        ).fetchone()[0] is True
        assert conn.execute(
            change_status,
            ("kalisto", ticket_id, 2001, "completed", NOW, "status:2"),
        ).fetchone()[0] is False
        assert conn.execute(
            change_status,
            ("kalisto", ticket_id, 3001, "open", NOW, "status:3"),
        ).fetchone()[0] is True

        fact = conn.execute(
            "SELECT profile_key,status_code,reopen_count,public_message_count,internal_note_count "
            "FROM reporting.ticket_facts WHERE ticket_id=%s",
            (ticket_id,),
        ).fetchone()
        assert fact == ("kalisto", "open", 1, 2, 1)
        assert conn.execute(
            "SELECT count(*) FROM ticketing.memberships m "
            "JOIN ticketing.telegram_users u USING(user_id) "
            "WHERE u.telegram_user_id=1001"
        ).fetchone()[0] == 2

        assert conn.execute(
            "SELECT ticketing.sync_configured_roles(%s,%s,%s)",
            ("kalisto", [1001], []),
        ).fetchone()[0] >= 1
        assert conn.execute(
            "SELECT m.role FROM ticketing.memberships m "
            "JOIN ticketing.telegram_users u USING(user_id) "
            "WHERE m.profile_key='kalisto' AND u.telegram_user_id=2001"
        ).fetchone()[0] == "client"

        conn.execute("SET LOCAL ROLE ticketing_writer")
        rejects(
            conn,
            "UPDATE ticketing.ticket_events SET event_type='created' WHERE ticket_id=%s",
            (ticket_id,),
        )

print("PASS: ticket profiles, shared admins, global ticket ID, idempotence, public/internal visibility, status audit, reporting views and writer permissions.")

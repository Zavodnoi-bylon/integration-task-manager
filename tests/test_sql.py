import os
import sqlite3
import tempfile
import unittest

from app import sql


class SqlRepositoryTests(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.original_db_path = sql.DB_PATH
        sql.DB_PATH = self.db_path
        sql.create_tables()

    def tearDown(self):
        sql.DB_PATH = self.original_db_path
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_ticket_id_and_event_history_are_atomic(self):
        sql.ensure_user(101, "2026-10-27 10:00:00", "@client", "client", "default")

        first_id = sql.add_ticket(
            101, "", "", "Первая задача", "2026-10-27 10:01:00",
            sql.STATUS_OPEN, "", assigned_to=201,
        )
        second_id = sql.add_ticket(
            101, "", "", "Вторая задача", "2026-10-27 10:02:00",
            sql.STATUS_OPEN, "", assigned_to=201,
        )

        self.assertEqual(second_id, first_id + 1)
        self.assertEqual(
            sql.get_ticket_events(first_id)[0]["event_type"], "created"
        )

    def test_ticket_type_is_persisted(self):
        ticket_id = sql.add_ticket(
            101,
            "",
            "",
            "Destination body",
            "2026-10-27 10:01:00",
            sql.STATUS_OPEN,
            "",
            ticket_type="destination",
        )

        self.assertEqual(
            sql.get_ticket_info(ticket_id)["ticket_type"], "destination"
        )

    def test_forwarded_draft_consumes_recent_comment_and_is_idempotent(self):
        sql.ensure_user(101, "2026-10-27 10:00:00", "@client", "client", "kalisto")
        sql.save_draft_intake(
            "kalisto", 101, 500, 10, "Комментарий клиента", "2026-10-27 10:01:00"
        )

        first = sql.create_forwarded_draft(
            "kalisto", 101, 500, 11, "Тело оффера", "user", "Advertiser",
            "2026-10-27 09:59:00", "2026-10-27 10:02:00",
        )
        second = sql.create_forwarded_draft(
            "kalisto", 101, 500, 11, "Тело оффера", "user", "Advertiser",
            "2026-10-27 09:59:00", "2026-10-27 10:02:00",
        )

        self.assertEqual(first["draft_id"], second["draft_id"])
        self.assertEqual(first["client_comment"], "Комментарий клиента")
        self.assertEqual(first["edited_body"], "Тело оффера")

    def test_comments_are_appended_instead_of_overwritten(self):
        ticket_id = sql.add_ticket(
            101, "", "", "Задача", "2026-10-27 10:00:00",
            sql.STATUS_OPEN, "", assigned_to=201,
        )
        sql.add_ticket_comment(ticket_id, 201, "Первый", "2026-10-27 10:05:00")
        sql.add_ticket_comment(ticket_id, 201, "Второй", "2026-10-27 10:06:00")

        comments = [
            event
            for event in sql.get_ticket_events(ticket_id)
            if event["event_type"] == "comment"
        ]
        self.assertEqual(
            [event["text"] for event in comments], ["Первый", "Второй"]
        )
        self.assertEqual(sql.read_ticket_comment(ticket_id), "Второй")

    def test_status_timestamps_and_reopen_history(self):
        ticket_id = sql.add_ticket(
            101, "", "", "Задача", "2026-10-27 10:00:00",
            sql.STATUS_OPEN, "", assigned_to=201,
        )

        self.assertTrue(sql.change_ticket_status(
            ticket_id, sql.STATUS_COMPLETED, 201, "2026-10-27 11:00:00"
        ))
        completed = sql.get_ticket_info(ticket_id)
        self.assertEqual(completed["status"], sql.STATUS_COMPLETED)
        self.assertEqual(completed["closed_at"], "2026-10-27 11:00:00")

        self.assertTrue(sql.change_ticket_status(
            ticket_id, sql.STATUS_OPEN, 101, "2026-10-27 11:05:00"
        ))
        reopened = sql.get_ticket_info(ticket_id)
        self.assertEqual(reopened["status"], sql.STATUS_OPEN)
        self.assertIsNone(reopened["closed_at"])

        transitions = [
            (event["from_status"], event["to_status"])
            for event in sql.get_ticket_events(ticket_id)
            if event["event_type"] == "status_changed"
        ]
        self.assertEqual(transitions, [
            (sql.STATUS_OPEN, sql.STATUS_COMPLETED),
            (sql.STATUS_COMPLETED, sql.STATUS_OPEN),
        ])

    def test_existing_schema_is_extended_without_data_loss(self):
        os.remove(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute('''
                CREATE TABLE users (
                    tg_id INTEGER PRIMARY KEY, data_reg TEXT, organization TEXT,
                    organization_adress TEXT, organization_inn TEXT,
                    organization_phone TEXT, history_ticket TEXT,
                    data_ticket TEXT, user_name TEXT
                )
            ''')
            conn.execute('''
                CREATE TABLE ticket (
                    number_ticket INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id_ticket INTEGER, organization TEXT, addres_ticket TEXT,
                    message_ticket TEXT, time_ticket TEXT,
                    state_ticket TEXT, ticket_comm TEXT
                )
            ''')
            conn.execute(
                "INSERT INTO users VALUES (1, 'date', '', '', '', '', '', '', '@old')"
            )
            conn.commit()
        finally:
            conn.close()

        sql.create_tables()
        migrated_user = sql.get_user_by_id(1)
        self.assertEqual(migrated_user["user_name"], "@old")
        self.assertEqual(migrated_user["role"], "client")
        conn = sqlite3.connect(self.db_path)
        try:
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(ticket)").fetchall()
            }
        finally:
            conn.close()
        self.assertIn("ticket_type", columns)


if __name__ == "__main__":
    unittest.main()

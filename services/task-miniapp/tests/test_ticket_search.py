import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace

from dest_miniapp.canonical import PostgresMiniAppGateway
from dest_miniapp.gateway import ResourceNotFound, TicketQuery
from dest_miniapp.models import Actor


class Connection:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, params):
        self.calls.append((sql, params))
        return self

    async def fetchone(self):
        return {"value": 73}

    async def fetchall(self):
        return []

    @asynccontextmanager
    async def connection(self):
        yield self


class TicketSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_is_bound_and_scoped_in_count_and_page(self):
        for profile in ("kalisto", "mephisto"):
            for role, scope in (("client", "mine"), ("supervisor", "team"), ("technician", "team")):
                with self.subTest(profile=profile, role=role):
                    db = Connection()
                    gateway = PostgresMiniAppGateway(SimpleNamespace(pool=db),
                        profile_key=profile, profile_name=profile,
                        advertiser_catalog=None, postback_service=None)
                    search = "Тест %_ ' OR 1=1 --"
                    result = await gateway.list_tickets(profile_key=profile,
                        actor=Actor(42, "Test", role, True),
                        query=TicketQuery(scope, "completed", False, 2, 50, search))
                    self.assertEqual(result.total, 73)
                    self.assertFalse(result.has_next)
                    for sql, params in db.calls:
                        self.assertIn("t.profile_key=%s", sql)
                        self.assertIn("t.status_code=%s", sql)
                        self.assertIn("strpos(lower(COALESCE(", sql)
                        self.assertIn("SELECT r.naming FROM ticketing.destination_results r", sql)
                        self.assertIn("r.profile_key=t.profile_key", sql)
                        self.assertIn("NULLIF(t.title, ''), left(t.description, 256)", sql)
                        self.assertNotIn(search, sql)
                        self.assertIn(search, params)
                        self.assertEqual(params[0], profile)
                        if role == "client":
                            self.assertIn("requester.telegram_user_id=%s", sql)
                        if role == "supervisor":
                            self.assertIn("ss.subordinate_user_id=t.requester_user_id", sql)
                    self.assertEqual(db.calls[1][1][-2:], (50, 50))

    async def test_search_does_not_allow_client_team_scope(self):
        db = Connection()
        gateway = PostgresMiniAppGateway(SimpleNamespace(pool=db),
            profile_key="kalisto", profile_name="Kalisto",
            advertiser_catalog=None, postback_service=None)
        with self.assertRaises(ResourceNotFound):
            await gateway.list_tickets(profile_key="kalisto",
                actor=Actor(42, "Test", "client", True),
                query=TicketQuery("team", None, False, 1, 50, "secret"))
        self.assertEqual(db.calls, [])

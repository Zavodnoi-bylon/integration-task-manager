from __future__ import annotations

import unittest
from typing import Any

from fastapi.testclient import TestClient

from dest_miniapp.api import create_app
from dest_miniapp.gateway import AdvertiserQuery, ResourceNotFound, TicketQuery
from dest_miniapp.models import Actor, TelegramIdentity
from dest_miniapp.sessions import MemorySessionStore
from dest_miniapp.settings import Settings
from test_auth import TOKEN, signed_init_data


class FakeGateway:
    def __init__(self, *, role: str = "client", active: bool = True) -> None:
        self.role = role
        self.active = active
        self.profiles_seen: list[str] = []
        self.users: dict[int, Actor] = {}

    def _actor(self, user_id: int, display_name: str = "Test") -> Actor:
        return Actor(user_id, display_name, self.role, self.active)  # type: ignore[arg-type]

    async def onboard_or_resolve(
        self, *, profile_key: str, identity: TelegramIdentity
    ) -> Actor:
        self.profiles_seen.append(profile_key)
        actor = self._actor(identity.user_id, identity.display_name)
        self.users[identity.user_id] = actor
        return actor

    async def resolve_actor(self, *, profile_key: str, user_id: int) -> Actor | None:
        self.profiles_seen.append(profile_key)
        return self.users.get(user_id)

    async def list_tickets(
        self, *, profile_key: str, actor: Actor, query: TicketQuery
    ) -> dict[str, Any]:
        self.profiles_seen.append(profile_key)
        return {"items": [], "total": 0, "page": query.page,
                "page_size": query.page_size, "has_next": False}

    async def get_ticket(
        self, *, profile_key: str, actor: Actor, ticket_id: str
    ) -> dict[str, Any]:
        self.profiles_seen.append(profile_key)
        if ticket_id == "foreign":
            raise ResourceNotFound
        return {"ticket_id": ticket_id, "conversation": []}

    async def list_advertisers(
        self, *, profile_key: str, actor: Actor, query: AdvertiserQuery
    ) -> dict[str, Any]:
        self.profiles_seen.append(profile_key)
        return {"items": [{"key": profile_key, "name": "Profile advertiser"}],
                "total": 1, "page": 1, "page_size": query.page_size,
                "has_next": False}

    async def list_postbacks(
        self, *, profile_key: str, actor: Actor
    ) -> list[dict[str, Any]]:
        self.profiles_seen.append(profile_key)
        return [{"key": profile_key, "name": "Template"}]

    async def get_postback(
        self, *, profile_key: str, actor: Actor, key: str
    ) -> dict[str, Any]:
        self.profiles_seen.append(profile_key)
        return {"key": key, "template": "https://example.invalid"}


def make_client(*, role: str = "client", active: bool = True):
    gateway = FakeGateway(role=role, active=active)
    settings = Settings(
        bot_token=TOKEN,
        profile_key="kalisto",
        profile_name="Kalisto",
        allowed_origin="https://miniapp.example",
        session_cookie_name="kalisto_session",
    )
    app = create_app(
        settings=settings, gateway=gateway, sessions=MemorySessionStore()
    )
    return TestClient(app, base_url="https://testserver"), gateway


def login(client: TestClient, *, origin: str = "https://miniapp.example"):
    return client.post(
        "/api/session",
        json={"init_data": signed_init_data()},
        headers={"Origin": origin},
    )


class ApiTests(unittest.TestCase):
    def test_login_sets_hardened_cookie_and_public_response(self) -> None:
        client, _ = make_client()
        response = login(client)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["profile"]["key"], "kalisto")
        self.assertNotIn("user_id", response.text)
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("secure", cookie)
        self.assertIn("samesite=lax", cookie)
        self.assertNotIn("domain=", cookie)

    def test_login_requires_exact_origin(self) -> None:
        client, _ = make_client()
        self.assertEqual(login(client, origin="https://evil.example").status_code, 403)

    def test_session_rechecks_inactive_membership(self) -> None:
        client, gateway = make_client()
        self.assertEqual(login(client).status_code, 201)
        gateway.users[42] = Actor(42, "Test", "client", False)
        self.assertEqual(client.get("/api/me").status_code, 403)
        self.assertEqual(client.get("/api/me").status_code, 401)

    def test_client_cannot_open_team_queue(self) -> None:
        client, _ = make_client()
        login(client)
        self.assertEqual(client.get("/api/tickets?scope=team").status_code, 403)

    def test_staff_can_open_team_queue(self) -> None:
        client, _ = make_client(role="technician")
        login(client)
        self.assertEqual(client.get("/api/tickets?scope=team").status_code, 200)

    def test_foreign_ticket_is_not_disclosed(self) -> None:
        client, _ = make_client()
        login(client)
        response = client.get("/api/tickets/foreign")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "resource not found"})

    def test_profile_is_only_taken_from_server_settings(self) -> None:
        client, gateway = make_client()
        login(client)
        response = client.get("/api/advertisers?mode=popular&profile=mephisto")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["key"], "kalisto")
        self.assertEqual(set(gateway.profiles_seen), {"kalisto"})

    def test_postback_path_traversal_is_rejected(self) -> None:
        client, _ = make_client()
        login(client)
        self.assertEqual(client.get("/api/postbacks/%2e%2e%5csecret").status_code, 404)

    def test_logout_requires_csrf_and_invalidates_session(self) -> None:
        client, _ = make_client()
        csrf = login(client).json()["csrf_token"]
        self.assertEqual(client.delete("/api/session").status_code, 403)
        self.assertEqual(
            client.delete("/api/session", headers={"X-CSRF-Token": csrf}).status_code,
            204,
        )
        self.assertEqual(client.get("/api/me").status_code, 401)


if __name__ == "__main__":
    unittest.main()

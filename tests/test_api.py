from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from dest_miniapp.api import create_app
from dest_miniapp.gateway import (
    AccessDenied,
    AdvertiserQuery,
    ResourceNotFound,
    TicketQuery,
)
from dest_miniapp.models import Actor, TelegramIdentity
from dest_miniapp.sessions import MemorySessionStore
from dest_miniapp.settings import Settings
from dest_miniapp.use_cases import MutationResult
from test_auth import TOKEN, signed_init_data


class FakeGateway:
    def __init__(self, *, role: str = "client", active: bool = True) -> None:
        self.role = role
        self.active = active
        self.profiles_seen: list[str] = []
        self.users: dict[int, Actor] = {}
        self.leak_ticket = False
        self.leak_advertiser = False
        self.leak_postback = False

    def _actor(self, user_id: int, display_name: str = "Test") -> Actor:
        return Actor(user_id, display_name, self.role, self.active)  # type: ignore[arg-type]

    async def onboard_or_resolve(
        self, *, profile_key: str, identity: TelegramIdentity
    ) -> Actor:
        self.profiles_seen.append(profile_key)
        actor = self._actor(identity.telegram_user_id, identity.display_name)
        self.users[identity.telegram_user_id] = actor
        return actor

    async def resolve_actor(
        self, *, profile_key: str, telegram_user_id: int
    ) -> Actor | None:
        self.profiles_seen.append(profile_key)
        return self.users.get(telegram_user_id)

    async def list_tickets(
        self, *, profile_key: str, actor: Actor, query: TicketQuery
    ) -> dict[str, Any]:
        self.profiles_seen.append(profile_key)
        return {"items": [], "total": 0, "page": query.page,
                "page_size": query.page_size, "has_next": False}

    async def get_ticket_view(
        self, *, profile_key: str, actor: Actor, ticket_id: int
    ) -> dict[str, Any]:
        self.profiles_seen.append(profile_key)
        if ticket_id == 404:
            raise ResourceNotFound
        if ticket_id == 403:
            raise AccessDenied
        if ticket_id == 405:
            raise ResourceNotFound
        result = ticket_detail(ticket_id)
        if self.leak_ticket:
            result["internal_note"] = "must never cross boundary"
            result["telegram_user_id"] = 999
            result["backend_metadata"] = {"secret": True}
        if actor.is_staff:
            result["internal_history"] = []
        return result

    async def list_advertisers(
        self, *, profile_key: str, actor: Actor, query: AdvertiserQuery
    ) -> dict[str, Any]:
        self.profiles_seen.append(profile_key)
        item = {"key": profile_key, "name": "Profile advertiser"}
        if self.leak_advertiser:
            item["suffix"] = "backend-only"
            item["technician_note"] = "backend-only"
        return {"items": [item],
                "total": 1, "page": 1, "page_size": query.page_size,
                "has_next": False}

    async def list_postbacks(
        self, *, profile_key: str, actor: Actor
    ) -> list[dict[str, Any]]:
        self.profiles_seen.append(profile_key)
        item = {"key": profile_key, "name": "Template"}
        if self.leak_postback:
            item["file_path"] = "backend-only"
        return [item]

    async def get_postback(
        self, *, profile_key: str, actor: Actor, key: str
    ) -> dict[str, Any]:
        self.profiles_seen.append(profile_key)
        return {"key": key, "name": "Template",
                "template": "https://example.invalid"}


class StubMutations:
    def __init__(self) -> None:
        self.last_command = None
        self.hidden_ids = {901, 902, 903}

    async def create_ticket(self, command):
        self.last_command = command
        return MutationResult(101, "open", command.ticket_type, 0)

    async def add_message(self, command):
        self.last_command = command
        if command.ticket_id in self.hidden_ids:
            raise ResourceNotFound
        return MutationResult(command.ticket_id, "open", "general", 0)

    async def transition(self, command):
        self.last_command = command
        if command.ticket_id in self.hidden_ids:
            raise ResourceNotFound
        status = {
            "submit_review": "review",
            "accept": "completed",
            "return": "open",
            "complete_destination": "completed",
            "reopen": "open",
        }[command.action.value]
        ticket_type = (
            "destination" if command.action.value == "complete_destination" else "general"
        )
        return MutationResult(command.ticket_id, status, ticket_type, 1)


def ticket_detail(ticket_id: int) -> dict[str, Any]:
    now = "2026-11-04T12:00:00Z"
    return {
        "ticket_id": ticket_id,
        "profile_name": "Kalisto",
        "status": "open",
        "ticket_type": "general",
        "title": "Test ticket",
        "description_preview": "Preview",
        "description": "Description",
        "created_at": now,
        "updated_at": now,
        "closed_at": None,
        "conversation": [],
    }


def make_client(*, role: str = "client", active: bool = True, clock=None):
    gateway = FakeGateway(role=role, active=active)
    settings = Settings(
        bot_token=TOKEN,
        profile_key="kalisto",
        profile_name="Kalisto",
        allowed_origin="https://miniapp.example",
        session_cookie_name="kalisto_session",
    )
    store = MemorySessionStore(clock=clock)
    gateway.session_store = store
    mutations = StubMutations()
    gateway.mutations = mutations
    app = create_app(
        settings=settings, gateway=gateway, sessions=store, mutations=mutations
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

    def test_login_rejects_init_data_from_other_bot(self) -> None:
        client, _ = make_client()
        response = client.post(
            "/api/session",
            json={"init_data": signed_init_data(token="other-token")},
            headers={"Origin": "https://miniapp.example"},
        )
        self.assertEqual(response.status_code, 401)

    def test_repeated_login_invalidates_previous_session(self) -> None:
        client, gateway = make_client()
        first = login(client)
        old_session = first.cookies.get("kalisto_session")
        self.assertIsNotNone(old_session)
        self.assertEqual(login(client).status_code, 201)
        old_record = asyncio.run(gateway.session_store.get(old_session))
        self.assertIsNone(old_record)

    def test_expired_session_is_rejected(self) -> None:
        now = [datetime(2026, 11, 4, 12, 0, tzinfo=UTC)]
        client, _ = make_client(clock=lambda: now[0])
        self.assertEqual(login(client).status_code, 201)
        now[0] += timedelta(seconds=43_201)
        self.assertEqual(client.get("/api/me").status_code, 401)

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

    def test_role_downgrade_is_applied_to_live_session(self) -> None:
        client, gateway = make_client(role="technician")
        login(client)
        self.assertEqual(client.get("/api/tickets?scope=team").status_code, 200)
        gateway.users[42] = Actor(42, "Test", "client", True)
        self.assertEqual(client.get("/api/tickets?scope=team").status_code, 403)

    def test_foreign_ticket_is_not_disclosed(self) -> None:
        client, _ = make_client()
        login(client)
        responses = [
            client.get("/api/tickets/403"),
            client.get("/api/tickets/405"),
            client.get("/api/tickets/404"),
        ]
        self.assertEqual({response.status_code for response in responses}, {404})
        self.assertEqual(
            {response.text for response in responses},
            {'{"detail":"resource not found"}'},
        )

    def test_client_ticket_projection_fails_closed(self) -> None:
        client, gateway = make_client()
        login(client)
        gateway.leak_ticket = True
        response = client.get("/api/tickets/1")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("internal_note", response.text)
        self.assertNotIn("telegram_user_id", response.text)

    def test_profile_is_only_taken_from_server_settings(self) -> None:
        client, gateway = make_client()
        login(client)
        response = client.get("/api/advertisers?mode=popular&profile=mephisto")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["key"], "kalisto")
        self.assertEqual(client.get("/api/tickets").status_code, 200)
        self.assertEqual(client.get("/api/tickets/1").status_code, 200)
        self.assertEqual(client.get("/api/postbacks").status_code, 200)
        self.assertEqual(client.get("/api/postbacks/kalisto").status_code, 200)
        self.assertEqual(set(gateway.profiles_seen), {"kalisto"})

    def test_catalog_projections_reject_backend_only_fields(self) -> None:
        client, gateway = make_client()
        login(client)
        gateway.leak_advertiser = True
        advertiser_response = client.get("/api/advertisers?mode=popular")
        self.assertEqual(advertiser_response.status_code, 503)
        self.assertNotIn("suffix", advertiser_response.text)
        gateway.leak_postback = True
        postback_response = client.get("/api/postbacks")
        self.assertEqual(postback_response.status_code, 503)
        self.assertNotIn("file_path", postback_response.text)

    def test_postback_path_traversal_is_rejected(self) -> None:
        client, _ = make_client()
        login(client)
        self.assertEqual(client.get("/api/postbacks/%2e%2e%5csecret").status_code, 404)

    def test_logout_requires_csrf_and_invalidates_session(self) -> None:
        client, _ = make_client()
        csrf = login(client).json()["csrf_token"]
        self.assertEqual(client.delete("/api/session").status_code, 403)
        self.assertEqual(
            client.delete(
                "/api/session", headers={"X-CSRF-Token": "wrong-token"}
            ).status_code,
            403,
        )
        self.assertEqual(
            client.delete("/api/session", headers={"X-CSRF-Token": csrf}).status_code,
            204,
        )
        self.assertEqual(client.get("/api/me").status_code, 401)

    def test_create_ticket_requires_csrf_and_idempotency(self) -> None:
        client, _ = make_client()
        csrf = login(client).json()["csrf_token"]
        payload = {"type": "general", "body": "Need help"}
        self.assertEqual(client.post("/api/tickets", json=payload).status_code, 422)
        response = client.post(
            "/api/tickets",
            json=payload,
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": "11111111-1111-4111-8111-111111111111",
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["ticket_type"], "general")

    def test_destination_payload_is_discriminated_and_profile_is_server_side(self) -> None:
        client, gateway = make_client(role="technician")
        csrf = login(client).json()["csrf_token"]
        response = client.post(
            "/api/tickets?profile=mephisto",
            json={
                "type": "destination",
                "advertiser": {"kind": "catalog", "key": "adv-1"},
                "cap": 25,
                "body": "Create destination",
            },
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": "22222222-2222-4222-8222-222222222222",
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(gateway.mutations.last_command.profile_key, "kalisto")
        self.assertEqual(gateway.mutations.last_command.advertiser.key, "adv-1")

    def test_workflow_endpoint_builds_typed_command(self) -> None:
        client, gateway = make_client(role="technician")
        csrf = login(client).json()["csrf_token"]
        response = client.post(
            "/api/tickets/7/submit-review",
            json={},
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": "33333333-3333-4333-8333-333333333333",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "review")
        self.assertEqual(gateway.mutations.last_command.action.value, "submit_review")

    def test_message_endpoint_builds_visibility_command(self) -> None:
        client, gateway = make_client(role="technician")
        csrf = login(client).json()["csrf_token"]
        response = client.post(
            "/api/tickets/7/messages",
            json={"visibility": "internal", "body": "Diagnostic detail"},
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": "55555555-5555-4555-8555-555555555555",
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(gateway.mutations.last_command.visibility, "internal")

    def test_return_and_reopen_require_non_blank_public_reason(self) -> None:
        client, _ = make_client()
        csrf = login(client).json()["csrf_token"]
        headers = {
            "X-CSRF-Token": csrf,
            "Idempotency-Key": "44444444-4444-4444-8444-444444444444",
        }
        self.assertEqual(
            client.post(
                "/api/tickets/7/return", json={"reason": "   "}, headers=headers
            ).status_code,
            422,
        )
        self.assertEqual(
            client.post(
                "/api/tickets/7/reopen", json={"reason": "Problem"}, headers=headers
            ).status_code,
            200,
        )

    def test_author_mutations_hide_absent_foreign_and_cross_profile_tickets(self) -> None:
        client, _ = make_client()
        csrf = login(client).json()["csrf_token"]
        headers = {
            "X-CSRF-Token": csrf,
            "Idempotency-Key": "66666666-6666-4666-8666-666666666666",
        }
        requests = []
        for ticket_id in (901, 902, 903):
            requests.extend(
                [
                    client.post(
                        f"/api/tickets/{ticket_id}/messages",
                        json={"visibility": "public", "body": "Probe"},
                        headers=headers,
                    ),
                    client.post(
                        f"/api/tickets/{ticket_id}/accept", json={}, headers=headers
                    ),
                    client.post(
                        f"/api/tickets/{ticket_id}/return",
                        json={"reason": "Probe"},
                        headers=headers,
                    ),
                    client.post(
                        f"/api/tickets/{ticket_id}/reopen",
                        json={"reason": "Probe"},
                        headers=headers,
                    ),
                ]
            )
        self.assertEqual({response.status_code for response in requests}, {404})
        self.assertEqual(
            {response.text for response in requests},
            {'{"detail":"resource not found"}'},
        )

    def test_staff_only_probes_reject_client_before_ticket_lookup(self) -> None:
        client, _ = make_client()
        csrf = login(client).json()["csrf_token"]
        headers = {
            "X-CSRF-Token": csrf,
            "Idempotency-Key": "77777777-7777-4777-8777-777777777777",
        }
        for ticket_id in (7, 901):
            responses = [
                client.post(
                    f"/api/tickets/{ticket_id}/submit-review", json={}, headers=headers
                ),
                client.post(
                    f"/api/tickets/{ticket_id}/complete", json={}, headers=headers
                ),
                client.post(
                    f"/api/tickets/{ticket_id}/messages",
                    json={"visibility": "internal", "body": "Probe"},
                    headers=headers,
                ),
            ]
            self.assertEqual({response.status_code for response in responses}, {403})


if __name__ == "__main__":
    unittest.main()

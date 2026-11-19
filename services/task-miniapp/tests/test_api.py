from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

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


ANALYTICS_SECRET_BYTES = b"test-analytics-secret-key-32byte!"
ANALYTICS_SECRET = base64.urlsafe_b64encode(
    ANALYTICS_SECRET_BYTES
).rstrip(b"=").decode("ascii")
ANALYTICS_BOT_UUID = "11111111-1111-4111-8111-111111111111"
ANALYTICS_PROFILE_ID = "22222222-2222-4222-8222-222222222222"


class FakeGateway:
    def __init__(self, *, role: str = "client", active: bool = True) -> None:
        self.role = role
        self.active = active
        self.profiles_seen: list[str] = []
        self.users: dict[int, Actor] = {}
        self.leak_ticket = False
        self.leak_advertiser = False
        self.leak_postback = False
        self.advertiser_refresh_status = "idle"
        self.last_ticket_query: TicketQuery | None = None
        self.buying_flags: dict[int, bool] = {}
        self.destination_comments = ["Only Dasha", "Do not publish in chat"]
        self.drafts = {
            7: {
                "draft_id": 7,
                "source_preview": "Forwarded offer",
                "source_body": "Forwarded offer",
                "client_comment": "Client note",
                "edited_body": "Forwarded offer",
                "advertiser_kind": None,
                "advertiser_key": None,
                "advertiser_name": None,
                "cap": None,
                "ticket_type": None,
                "important_comment": "",
                "details": None,
                "forward_origin_name": "Advertiser",
                "created_at": "2026-11-05T10:00:00Z",
                "updated_at": "2026-11-05T10:00:00Z",
            }
        }

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
        self.last_ticket_query = query
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

    async def set_buying_flag(
        self, *, profile_key, actor, ticket_id, issued
    ):
        self.profiles_seen.append(profile_key)
        if ticket_id == 404:
            raise ResourceNotFound
        self.buying_flags[ticket_id] = issued
        return issued

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

    async def request_advertiser_refresh(self, *, profile_key, actor):
        self.profiles_seen.append(profile_key)
        if self.advertiser_refresh_status not in {"pending", "running"}:
            self.advertiser_refresh_status = "pending"
        return {"status": self.advertiser_refresh_status}

    async def get_advertiser_refresh_status(self, *, profile_key, actor):
        self.profiles_seen.append(profile_key)
        return {"status": self.advertiser_refresh_status}

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

    async def list_drafts(self, *, profile_key, actor):
        self.profiles_seen.append(profile_key)
        return [
            {
                key: item[key]
                for key in (
                    "draft_id", "source_preview", "client_comment",
                    "created_at", "updated_at",
                )
            }
            for item in self.drafts.values()
        ]

    async def list_destination_comments(self, *, profile_key, actor):
        self.profiles_seen.append(profile_key)
        return list(self.destination_comments)

    async def get_draft(self, *, profile_key, actor, draft_id):
        self.profiles_seen.append(profile_key)
        if draft_id not in self.drafts:
            raise ResourceNotFound
        return dict(self.drafts[draft_id])

    async def save_draft(self, *, profile_key, actor, draft_id, payload):
        draft = await self.get_draft(
            profile_key=profile_key, actor=actor, draft_id=draft_id
        )
        draft.update(
            edited_body=payload.body,
            client_comment=payload.client_comment,
            advertiser_kind=payload.advertiser.kind if payload.advertiser else None,
            advertiser_key=(
                payload.advertiser.key
                if payload.advertiser and payload.advertiser.kind == "catalog"
                else None
            ),
            advertiser_name=(
                payload.advertiser.name
                if payload.advertiser and payload.advertiser.kind == "new"
                else None
            ),
            cap=payload.cap,
            cap_mode=payload.cap_mode,
            ticket_type=payload.type,
            important_comment=payload.important_comment,
            details=(payload.details.model_dump() if payload.details else None),
        )
        self.drafts[draft_id] = draft
        return dict(draft)

    async def cancel_draft(self, *, profile_key, actor, draft_id):
        await self.get_draft(profile_key=profile_key, actor=actor, draft_id=draft_id)
        self.drafts.pop(draft_id)

    async def mark_draft_submitted(
        self, *, profile_key, actor, draft_id, ticket_id
    ):
        await self.get_draft(profile_key=profile_key, actor=actor, draft_id=draft_id)
        self.drafts.pop(draft_id)
        self.submitted_draft = (draft_id, ticket_id)


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
        "ticket_number": ticket_id,
        "profile_name": "Kalisto",
        "status": "open",
        "ticket_type": "general",
        "title": "Test ticket",
        "description_preview": "Preview",
        "description": "Description",
        "is_author": True,
        "created_at": now,
        "updated_at": now,
        "closed_at": None,
        "issued_to_buying": False,
        "conversation": [],
    }


def kalisto_details() -> dict[str, object]:
    return {
        "naming": "Advertiser | Brand | FB | BR | Slots | 100 USD | cap 25",
        "geo": "BR",
        "rate": "100.00",
        "currency": "USD",
        "landing_url": "https://example.test/?offer=1",
        "clickid_subid": "",
        "additional": "",
    }


def make_client(
    *, role: str = "client", active: bool = True, clock=None,
    analytics_url: str | None = None,
    analytics_allowed_user_ids: frozenset[int] | None = None,
    profile_key: str = "kalisto",
):
    gateway = FakeGateway(role=role, active=active)
    settings = Settings(
        bot_token=TOKEN,
        profile_key=profile_key,
        profile_name="Kalisto" if profile_key == "kalisto" else "Mephisto",
        allowed_origin="https://miniapp.example",
        session_cookie_name="kalisto_session",
        analytics_url=analytics_url,
        analytics_issuer="theraw-task-manager" if analytics_url else None,
        analytics_bot_uuid=ANALYTICS_BOT_UUID if analytics_url else None,
        analytics_profile_id=ANALYTICS_PROFILE_ID if analytics_url else None,
        analytics_signing_secret=ANALYTICS_SECRET if analytics_url else None,
        analytics_allowed_user_ids=(
            frozenset({42})
            if analytics_url and analytics_allowed_user_ids is None
            else (analytics_allowed_user_ids or frozenset())
        ),
    )
    store = MemorySessionStore(clock=clock)
    gateway.session_store = store
    mutations = StubMutations()
    gateway.mutations = mutations
    packaged_help = Path("/app/help")
    help_directory = (
        packaged_help
        if packaged_help.is_dir()
        else Path(__file__).resolve().parents[3] / "docs"
    )
    app = create_app(
        settings=settings,
        gateway=gateway,
        sessions=store,
        mutations=mutations,
        help_directory=help_directory,
    )
    return TestClient(app, base_url="https://testserver"), gateway


def login(client: TestClient, *, origin: str = "https://miniapp.example"):
    return client.post(
        "/api/session",
        json={"init_data": signed_init_data()},
        headers={"Origin": origin},
    )


def _decode_token_part(value: str) -> dict[str, object]:
    raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    return json.loads(raw)


class ApiTests(unittest.TestCase):
    def test_me_exposes_only_configured_external_links(self) -> None:
        client, _ = make_client()
        login(client)
        self.assertEqual(client.get("/api/me").json()["links"], {})

        linked, _ = make_client(
            analytics_url="https://t.me/tiketanalitic_bot"
        )
        login(linked)
        self.assertEqual(
            linked.get("/api/me").json()["links"],
            {"analytics": "https://t.me/tiketanalitic_bot"},
        )

    def test_analytics_handoff_is_signed_short_lived_and_role_isolated(self) -> None:
        for source_role in ("client", "supervisor", "technician", "admin"):
            with self.subTest(source_role=source_role):
                client, _ = make_client(
                    role=source_role,
                    analytics_url="https://explorer.example/entry",
                )
                csrf = login(client).json()["csrf_token"]
                missing_csrf = client.post(
                    "/api/analytics/handoff", json={}
                )
                self.assertEqual(missing_csrf.status_code, 403)
                response = client.post(
                    "/api/analytics/handoff",
                    json={},
                    headers={"X-CSRF-Token": csrf},
                )
                self.assertEqual(response.status_code, 200)
                split_url = urlsplit(response.json()["url"])
                self.assertEqual(split_url.scheme, "https")
                self.assertEqual(split_url.netloc, "explorer.example")
                self.assertEqual(split_url.path, "/entry")
                self.assertEqual(split_url.query, "")
                token = parse_qs(split_url.fragment)["handoff"][0]
                encoded_header, encoded_payload, encoded_signature = token.split(".")
                header = _decode_token_part(encoded_header)
                payload = _decode_token_part(encoded_payload)
                expected_signature = hmac.new(
                    ANALYTICS_SECRET_BYTES,
                    f"{encoded_header}.{encoded_payload}".encode("ascii"),
                    hashlib.sha256,
                ).digest()
                actual_signature = base64.urlsafe_b64decode(
                    encoded_signature + "=" * (-len(encoded_signature) % 4)
                )
                self.assertTrue(hmac.compare_digest(actual_signature, expected_signature))
                self.assertEqual(
                    header,
                    {"alg": "HS256", "kid": ANALYTICS_BOT_UUID, "typ": "JWT"},
                )
                self.assertEqual(payload["iss"], "theraw-task-manager")
                self.assertEqual(payload["sub"], "telegram:42")
                self.assertEqual(payload["telegram_user_id"], 42)
                self.assertEqual(payload["bot_uuid"], ANALYTICS_BOT_UUID)
                self.assertEqual(payload["profile_id"], ANALYTICS_PROFILE_ID)
                self.assertEqual(payload["role"], "analytics_viewer")
                self.assertEqual(payload["exp"] - payload["iat"], 300)
                self.assertIsInstance(payload["jti"], str)
                self.assertNotIn("tenant_uuid", payload)
                self.assertNotIn("display_name", payload)
                self.assertNotIn("username", payload)
                self.assertEqual(response.json()["expires_at"], payload["exp"])

    def test_analytics_handoff_is_disabled_without_complete_configuration(self) -> None:
        client, _ = make_client()
        csrf = login(client).json()["csrf_token"]
        response = client.post(
            "/api/analytics/handoff",
            json={},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 404)

    def test_analytics_link_and_handoff_require_user_allowlist(self) -> None:
        client, _ = make_client(
            analytics_url="https://explorer.example",
            analytics_allowed_user_ids=frozenset({99}),
        )
        csrf = login(client).json()["csrf_token"]
        self.assertEqual(client.get("/api/me").json()["links"], {})
        response = client.post(
            "/api/analytics/handoff",
            json={},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "operation forbidden")

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

    def test_live_cookie_can_rotate_session_to_restore_csrf(self) -> None:
        client, _ = make_client()
        self.assertEqual(login(client).status_code, 201)
        recovered = login(client)
        self.assertEqual(recovered.status_code, 201)
        csrf = recovered.json()["csrf_token"]
        response = client.post(
            "/api/tickets",
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": "11111111-1111-4111-8111-111111111111",
            },
            json={"type": "general", "body": "Recovered"},
        )
        self.assertEqual(response.status_code, 201)

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

    def test_supervisor_has_team_queue_without_staff_capabilities(self) -> None:
        client, _ = make_client(role="supervisor")
        login(client)
        me = client.get("/api/me").json()
        self.assertTrue(me["capabilities"]["team_queue"])
        self.assertFalse(me["capabilities"]["internal_history"])
        self.assertFalse(me["capabilities"]["technical_actions"])
        self.assertTrue(me["capabilities"]["buying_flag"])
        self.assertEqual(client.get("/api/tickets?scope=team").status_code, 200)

    def test_buying_flag_is_kalisto_supervisor_only(self) -> None:
        supervisor, gateway = make_client(role="supervisor")
        csrf = login(supervisor).json()["csrf_token"]
        response = supervisor.post(
            "/api/tickets/7/buying-flag",
            json={"issued": True},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {"ticket_id": 7, "issued_to_buying": True}
        )
        self.assertTrue(gateway.buying_flags[7])

        for role in ("client", "technician", "admin"):
            with self.subTest(role=role):
                client, _ = make_client(role=role)
                role_csrf = login(client).json()["csrf_token"]
                self.assertFalse(
                    client.get("/api/me").json()["capabilities"]["buying_flag"]
                )
                denied = client.post(
                    "/api/tickets/7/buying-flag",
                    json={"issued": True},
                    headers={"X-CSRF-Token": role_csrf},
                )
                self.assertEqual(denied.status_code, 403)

        mephisto, _ = make_client(role="supervisor", profile_key="mephisto")
        mephisto_csrf = login(mephisto).json()["csrf_token"]
        self.assertFalse(
            mephisto.get("/api/me").json()["capabilities"]["buying_flag"]
        )
        self.assertEqual(
            mephisto.post(
                "/api/tickets/7/buying-flag",
                json={"issued": True},
                headers={"X-CSRF-Token": mephisto_csrf},
            ).status_code,
            403,
        )

    def test_buying_filter_is_forwarded_only_for_kalisto_supervisor(self) -> None:
        client, gateway = make_client(role="supervisor")
        login(client)
        response = client.get("/api/tickets", params={
            "scope": "team", "status": "completed", "page": 3,
            "page_size": 50, "search": "  Lucky Star %_  ",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(gateway.last_ticket_query.search, "Lucky Star %_")
        self.assertEqual(gateway.last_ticket_query.page, 3)
        self.assertEqual(gateway.last_ticket_query.status, "completed")
        self.assertEqual(client.get("/api/tickets", params={"search": "x" * 201}).status_code, 422)
        self.assertEqual(client.get("/api/tickets?page=0").status_code, 422)

    def test_buying_filter_access(self) -> None:
        client, gateway = make_client(role="supervisor")
        login(client)
        response = client.get(
            "/api/tickets?scope=team&issued_to_buying=true"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(gateway.last_ticket_query.issued_to_buying)

        staff, _ = make_client(role="technician")
        login(staff)
        self.assertEqual(
            staff.get(
                "/api/tickets?scope=team&issued_to_buying=true"
            ).status_code,
            403,
        )

    def test_buying_flag_requires_csrf_and_strict_boolean(self) -> None:
        client, _ = make_client(role="supervisor")
        csrf = login(client).json()["csrf_token"]
        self.assertEqual(
            client.post(
                "/api/tickets/7/buying-flag", json={"issued": True}
            ).status_code,
            403,
        )
        self.assertEqual(
            client.post(
                "/api/tickets/7/buying-flag",
                json={"issued": "true"},
                headers={"X-CSRF-Token": csrf},
            ).status_code,
            422,
        )

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

    def test_advertiser_refresh_is_profile_scoped_and_requires_csrf(self) -> None:
        client, gateway = make_client(profile_key="kalisto")
        csrf = login(client).json()["csrf_token"]
        self.assertEqual(
            client.post("/api/advertisers/refresh", json={}).status_code,
            403,
        )
        response = client.post(
            "/api/advertisers/refresh",
            json={},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "pending"})
        gateway.advertiser_refresh_status = "succeeded"
        status = client.get("/api/advertisers/refresh")
        self.assertEqual(status.json(), {"status": "succeeded"})
        self.assertEqual(set(gateway.profiles_seen), {"kalisto"})

    def test_help_is_selected_by_role(self) -> None:
        client, _ = make_client(role="client")
        login(client)
        client_help = client.get("/api/help")
        self.assertEqual(client_help.status_code, 200)
        self.assertEqual(client_help.json()["audience"], "client")
        self.assertIn("Как создать обычную задачу", client_help.json()["markdown"])
        self.assertNotIn("Внутренняя запись", client_help.json()["markdown"])

        staff, _ = make_client(role="technician")
        login(staff)
        staff_help = staff.get("/api/help")
        self.assertEqual(staff_help.status_code, 200)
        self.assertEqual(staff_help.json()["audience"], "staff")
        self.assertIn("Внутренняя запись", staff_help.json()["markdown"])

    def test_help_is_isolated_by_profile_and_role(self) -> None:
        for profile in ("kalisto", "mephisto"):
            for role in ("client", "supervisor", "technician", "admin"):
                with self.subTest(profile=profile, role=role):
                    client, _ = make_client(profile_key=profile, role=role)
                    login(client)
                    response = client.get("/api/help?profile=other&audience=staff")
                    self.assertEqual(response.status_code, 200)
                    content = response.json()
                    staff = role in {"technician", "admin"}
                    self.assertEqual(content["audience"], "staff" if staff else "client")
                    markdown = content["markdown"]
                    self.assertIn("Поиск по названию", markdown)
                    self.assertIn("Назад / Далее", markdown)
                    self.assertIn("Аналитика", markdown)
                    if profile == "kalisto":
                        self.assertIn("Kalisto", markdown)
                        self.assertIn("Выдано баингу", markdown)
                        self.assertNotIn("Важный комментарий", markdown)
                        self.assertNotIn("Новый рекламодатель", markdown)
                    else:
                        self.assertIn("TheRaw", markdown)
                        self.assertIn("Важный комментарий", markdown)
                        self.assertIn("Новый рекламодатель", markdown)
                        self.assertNotIn("Выдано баингу", markdown)
                    if staff:
                        self.assertIn("Внутренняя запись", markdown)
                    else:
                        self.assertNotIn("Внутренняя запись", markdown)

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
                "cap_mode": "daily",
                "body": "Create destination",
                "details": kalisto_details(),
            },
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": "22222222-2222-4222-8222-222222222222",
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(gateway.mutations.last_command.profile_key, "kalisto")
        self.assertEqual(gateway.mutations.last_command.advertiser.key, "adv-1")
        self.assertEqual(
            gateway.mutations.last_command.details.landing_url,
            "https://example.test/?offer=1",
        )

    def test_destination_accepts_uncap(self) -> None:
        client, gateway = make_client()
        csrf = login(client).json()["csrf_token"]
        response = client.post(
            "/api/tickets",
            json={
                "type": "destination",
                "advertiser": {"kind": "catalog", "key": "adv-1"},
                "cap": "uncap",
                "cap_mode": "unlim",
                "body": "Create destination",
                "details": {**kalisto_details(), "naming": (
                    "Advertiser | Brand | FB | BR | Slots | 100 USD | uncap"
                )},
            },
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": "23232323-2323-4232-8232-232323232323",
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(gateway.mutations.last_command.cap, "uncap")
        self.assertEqual(gateway.mutations.last_command.cap_mode, "unlim")

    def test_mephisto_important_comment_stays_out_of_source_body(self) -> None:
        client, gateway = make_client(profile_key="mephisto")
        csrf = login(client).json()["csrf_token"]
        response = client.post(
            "/api/tickets",
            json={
                "type": "destination",
                "advertiser": {"kind": "catalog", "key": "adv-1"},
                "cap": 20,
                "cap_mode": "lifetime",
                "body": "Offer source for OpenRouter",
                "important_comment": "Share only with Dasha",
            },
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": "26262626-2626-4262-8262-262626262626",
            },
        )
        self.assertEqual(response.status_code, 201)
        command = gateway.mutations.last_command
        self.assertEqual(command.body, "Offer source for OpenRouter")
        self.assertEqual(command.important_comment, "Share only with Dasha")
        history = client.get("/api/destination-comments")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json(), gateway.destination_comments)

    def test_kalisto_rejects_mephisto_important_comment(self) -> None:
        client, _ = make_client(profile_key="kalisto")
        csrf = login(client).json()["csrf_token"]
        response = client.post(
            "/api/tickets",
            json={
                "type": "destination",
                "advertiser": {"kind": "catalog", "key": "adv-1"},
                "cap": 20,
                "cap_mode": "lifetime",
                "body": "",
                "important_comment": "Must not be accepted",
                "details": kalisto_details(),
            },
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": "27272727-2727-4272-8272-272727272727",
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(client.get("/api/destination-comments").status_code, 404)

    def test_destination_rejects_ambiguous_cap_values(self) -> None:
        client, _ = make_client()
        csrf = login(client).json()["csrf_token"]
        headers = {
            "X-CSRF-Token": csrf,
            "Idempotency-Key": "24242424-2424-4242-8242-242424242424",
        }
        for value in (True, 0, -1, "25", "unlimited"):
            response = client.post(
                "/api/tickets",
                json={
                    "type": "destination",
                    "advertiser": {"kind": "catalog", "key": "adv-1"},
                    "cap": value,
                    "cap_mode": "daily",
                    "body": "Create destination",
                },
                headers=headers,
            )
            self.assertEqual(response.status_code, 422, value)

    def test_forwarded_draft_is_private_until_explicit_submit(self) -> None:
        client, gateway = make_client()
        csrf = login(client).json()["csrf_token"]

        listed = client.get("/api/drafts")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()[0]["draft_id"], 7)
        self.assertIsNone(gateway.mutations.last_command)

        saved = client.post(
            "/api/drafts/7/save",
            json={
                "advertiser": None,
                "cap": None,
                "body": "Edited offer",
                "client_comment": "Edited note",
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["edited_body"], "Edited offer")
        self.assertIsNone(gateway.mutations.last_command)

        submitted = client.post(
            "/api/drafts/7/submit",
            json={
                "type": "destination",
                "advertiser": {"kind": "catalog", "key": "adv-1"},
                "cap": 25,
                "cap_mode": "lifetime",
                "body": "Edited offer",
                "client_comment": "Edited note",
                "details": kalisto_details(),
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(submitted.status_code, 201)
        self.assertEqual(submitted.json()["ticket_id"], 101)
        self.assertEqual(gateway.submitted_draft, (7, 101))
        self.assertEqual(
            gateway.mutations.last_command.body,
            "Edited note\n\nEdited offer",
        )

    def test_forwarded_draft_preserves_uncap(self) -> None:
        client, gateway = make_client()
        csrf = login(client).json()["csrf_token"]
        saved = client.post(
            "/api/drafts/7/save",
            json={
                "type": "destination",
                "advertiser": {"kind": "catalog", "key": "adv-1"},
                "cap": "uncap",
                "cap_mode": "unlim",
                "body": "Edited offer",
                "client_comment": "",
                "details": {**kalisto_details(), "naming": (
                    "Advertiser | Brand | FB | BR | Slots | 100 USD | uncap"
                )},
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["cap"], "uncap")
        self.assertEqual(saved.json()["cap_mode"], "unlim")

        submitted = client.post(
            "/api/drafts/7/submit",
            json={
                "type": "destination",
                "advertiser": {"kind": "catalog", "key": "adv-1"},
                "cap": "uncap",
                "cap_mode": "unlim",
                "body": "Edited offer",
                "client_comment": "",
                "details": {**kalisto_details(), "naming": (
                    "Advertiser | Brand | FB | BR | Slots | 100 USD | uncap"
                )},
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(submitted.status_code, 201)
        self.assertEqual(gateway.mutations.last_command.cap, "uncap")
        self.assertEqual(gateway.mutations.last_command.cap_mode, "unlim")

    def test_destination_rejects_mismatched_cap_mode(self) -> None:
        client, _ = make_client()
        csrf = login(client).json()["csrf_token"]
        headers = {
            "X-CSRF-Token": csrf,
            "Idempotency-Key": "25252525-2525-4252-8252-252525252525",
        }
        for cap, mode in (("uncap", "daily"), (600, "unlim")):
            response = client.post(
                "/api/tickets",
                json={
                    "type": "destination",
                    "advertiser": {"kind": "catalog", "key": "adv-1"},
                    "cap": cap,
                    "cap_mode": mode,
                    "body": "Create destination",
                    "details": kalisto_details(),
                },
                headers=headers,
            )
            self.assertEqual(response.status_code, 422, (cap, mode))

    def test_forwarded_draft_can_submit_as_general(self) -> None:
        client, gateway = make_client()
        csrf = login(client).json()["csrf_token"]

        submitted = client.post(
            "/api/drafts/7/submit",
            json={
                "type": "general",
                "body": "Обычная задача",
                "client_comment": "Контекст",
            },
            headers={"X-CSRF-Token": csrf},
        )

        self.assertEqual(submitted.status_code, 201)
        self.assertEqual(gateway.mutations.last_command.ticket_type, "general")
        self.assertIsNone(gateway.mutations.last_command.advertiser)
        self.assertIsNone(gateway.mutations.last_command.cap)
        self.assertEqual(
            gateway.mutations.last_command.body,
            "Контекст\n\nОбычная задача",
        )

    def test_draft_delete_requires_csrf(self) -> None:
        client, gateway = make_client()
        csrf = login(client).json()["csrf_token"]
        self.assertEqual(client.delete("/api/drafts/7").status_code, 403)
        self.assertEqual(
            client.delete(
                "/api/drafts/7", headers={"X-CSRF-Token": csrf}
            ).status_code,
            204,
        )
        self.assertNotIn(7, gateway.drafts)

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

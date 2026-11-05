from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

import httpx


LOGGER = logging.getLogger(__name__)


class AsanaAuthorizationError(RuntimeError):
    """An Asana request was authenticated but not authorized."""

    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"Asana authorization failed with HTTP {status_code}")


class AsanaClient:
    BASE_URL = "https://app.asana.com/api/1.0"

    def __init__(self, token: str):
        self.client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=httpx.Timeout(20.0),
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def request(
        self, method: str, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if path.startswith("http"):
            parsed = urlparse(path)
            if parsed.netloc != "app.asana.com":
                raise RuntimeError("Refusing an unexpected pagination host")
            path = parsed.path
            if path.startswith("/api/1.0"):
                path = path.removeprefix("/api/1.0") or "/"
            if parsed.query and params is None:
                path = f"{path}?{parsed.query}"

        for attempt in range(1, 6):
            response = await self.client.request(method, path, params=params)
            if response.status_code == 429:
                wait_seconds = min(float(response.headers.get("Retry-After", "5")), 60.0)
                LOGGER.warning("Asana rate limited the request; retrying in %.1fs", wait_seconds)
                await asyncio.sleep(wait_seconds)
                continue
            if response.status_code >= 500 and attempt < 5:
                await asyncio.sleep(min(2**attempt, 20))
                continue
            if response.status_code in (401, 403):
                raise AsanaAuthorizationError(response.status_code)
            response.raise_for_status()
            return response.json()
        raise RuntimeError("Asana request failed after retries")

    async def list_all(
        self, path: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        next_path: str | None = path
        next_params = dict(params or {})
        next_params.setdefault("limit", 100)
        while next_path:
            payload = await self.request("GET", next_path, params=next_params)
            result.extend(payload.get("data", []))
            next_page = payload.get("next_page") or {}
            next_path = next_page.get("uri")
            next_params = None
        return result

    async def me(self) -> dict[str, Any]:
        payload = await self.request(
            "GET", "/users/me", params={"opt_fields": "gid,name,email"}
        )
        return payload["data"]

    async def workspace_users(self, workspace_gid: str) -> list[dict[str, Any]]:
        return await self.list_all(
            f"/workspaces/{workspace_gid}/users",
            params={"opt_fields": "gid,name,email"},
        )

    async def project_tasks(self, project_gid: str) -> list[dict[str, Any]]:
        fields = ",".join(
            [
                "gid",
                "name",
                "assignee.gid",
                "assignee.name",
                "completed",
                "completed_at",
                "due_on",
                "due_at",
                "modified_at",
                "permalink_url",
                "notes",
                "memberships.project.gid",
                "memberships.section.gid",
                "memberships.section.name",
                "custom_fields.gid",
                "custom_fields.enum_value.name",
                "custom_fields.multi_enum_values.name",
            ]
        )
        return await self.list_all(
            "/tasks",
            params={
                "project": project_gid,
                "completed_since": "now",
                "opt_fields": fields,
            },
        )

    async def task(self, task_gid: str) -> dict[str, Any]:
        fields = ",".join(
            [
                "gid",
                "name",
                "assignee.gid",
                "assignee.name",
                "completed",
                "completed_at",
                "due_on",
                "due_at",
                "modified_at",
                "permalink_url",
                "notes",
                "memberships.project.gid",
                "memberships.section.gid",
                "memberships.section.name",
                "custom_fields.gid",
                "custom_fields.enum_value.name",
                "custom_fields.multi_enum_values.name",
            ]
        )
        payload = await self.request(
            "GET", f"/tasks/{task_gid}", params={"opt_fields": fields}
        )
        return payload["data"]

    async def task_stories(self, task_gid: str) -> list[dict[str, Any]]:
        fields = ",".join(
            [
                "gid",
                "resource_subtype",
                "text",
                "created_at",
                "created_by.gid",
                "created_by.name",
            ]
        )
        return await self.list_all(
            f"/tasks/{task_gid}/stories", params={"opt_fields": fields}
        )


class TelegramClient:
    def __init__(self, token: str):
        self.client = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{token}",
            timeout=httpx.Timeout(35.0),
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def call(
        self, method: str, payload: dict[str, Any], attempts: int = 4
    ) -> dict[str, Any]:
        for attempt in range(1, attempts + 1):
            response = await self.client.post(f"/{method}", json=payload)
            if response.status_code == 429:
                retry = response.json().get("parameters", {}).get("retry_after", 3)
                await asyncio.sleep(min(float(retry), 30.0))
                continue
            if response.status_code >= 500 and attempt < attempts:
                await asyncio.sleep(min(2**attempt, 10))
                continue
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                raise RuntimeError(f"Telegram API rejected {method}: {data.get('description')}")
            return data
        raise RuntimeError(f"Telegram API {method} failed after retries")

    async def get_me(self) -> dict[str, Any]:
        return (await self.call("getMe", {}))["result"]

    async def get_updates(self, offset: int, timeout: int = 25) -> list[dict[str, Any]]:
        result = await self.call(
            "getUpdates",
            {
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": ["message"],
            },
            attempts=2,
        )
        return result["result"]

    async def send_message(self, chat_id: int, text: str) -> None:
        if len(text) > 4000:
            text = text[:3997] + "..."
        await self.call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )

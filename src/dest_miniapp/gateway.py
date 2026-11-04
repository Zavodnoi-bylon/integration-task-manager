from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from .models import Actor, TelegramIdentity


class GatewayError(RuntimeError):
    pass


class ResourceNotFound(GatewayError):
    pass


class AccessDenied(GatewayError):
    pass


class BackendUnavailable(GatewayError):
    pass


@dataclass(frozen=True)
class TicketQuery:
    scope: Literal["mine", "team"]
    status: Literal["open", "completed"] | None
    page: int
    page_size: int


@dataclass(frozen=True)
class AdvertiserQuery:
    mode: Literal["popular", "search", "page"]
    query: str | None
    page: int
    page_size: int


class MiniAppGateway(Protocol):
    """Actor-aware boundary implemented by the canonical application layer."""

    async def onboard_or_resolve(
        self, *, profile_key: str, identity: TelegramIdentity
    ) -> Actor: ...

    async def resolve_actor(self, *, profile_key: str, user_id: int) -> Actor | None: ...

    async def list_tickets(
        self, *, profile_key: str, actor: Actor, query: TicketQuery
    ) -> dict[str, Any]: ...

    async def get_ticket(
        self, *, profile_key: str, actor: Actor, ticket_id: str
    ) -> dict[str, Any]: ...

    async def list_advertisers(
        self, *, profile_key: str, actor: Actor, query: AdvertiserQuery
    ) -> dict[str, Any]: ...

    async def list_postbacks(
        self, *, profile_key: str, actor: Actor
    ) -> list[dict[str, Any]]: ...

    async def get_postback(
        self, *, profile_key: str, actor: Actor, key: str
    ) -> dict[str, Any]: ...

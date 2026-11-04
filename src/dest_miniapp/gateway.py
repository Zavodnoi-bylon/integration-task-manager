from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from .dto import (
    AdvertiserSummary,
    ClientTicketDetail,
    Page,
    PostbackDetail,
    PostbackSummary,
    StaffTicketDetail,
    TicketSummary,
)
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
    status: Literal["open", "review", "completed"] | None
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

    async def resolve_actor(
        self, *, profile_key: str, telegram_user_id: int
    ) -> Actor | None: ...

    async def list_tickets(
        self, *, profile_key: str, actor: Actor, query: TicketQuery
    ) -> Page[TicketSummary]: ...

    async def get_ticket_view(
        self, *, profile_key: str, actor: Actor, ticket_id: int
    ) -> ClientTicketDetail | StaffTicketDetail:
        """Return a visible view; absent/foreign/cross-profile all raise ResourceNotFound."""
        ...

    async def list_advertisers(
        self, *, profile_key: str, actor: Actor, query: AdvertiserQuery
    ) -> Page[AdvertiserSummary]: ...

    async def list_postbacks(
        self, *, profile_key: str, actor: Actor
    ) -> list[PostbackSummary]: ...

    async def get_postback(
        self, *, profile_key: str, actor: Actor, key: str
    ) -> PostbackDetail: ...

from __future__ import annotations

from typing import Any

from .dto import (
    ActorPublic,
    AdvertiserSummary,
    ClientTicketDetail,
    InternalConversationMessage,
    Page,
    PostbackDetail,
    PostbackSummary,
    PublicConversationMessage,
    StaffTicketDetail,
    TicketSummary,
)
from .gateway import AdvertiserQuery, ResourceNotFound, TicketQuery
from .models import Actor, TelegramIdentity
from .postgres import PostgresDatabase
from .use_cases import (
    AdvertiserCatalogPort,
    DestinationFormatRequest,
    DestinationFormatResult,
    DestinationFormattingError,
    DestinationFormatterPort,
    ResolvedAdvertiser,
)


class CanonicalAdvertiserCatalogAdapter(AdvertiserCatalogPort):
    def __init__(self, catalog: Any, *, profile_key: str) -> None:
        self._catalog = catalog
        self._profile_key = profile_key

    async def resolve(
        self, *, profile_key: str, catalog_key: str
    ) -> ResolvedAdvertiser:
        if profile_key != self._profile_key:
            raise ResourceNotFound
        entry = self._catalog.get(catalog_key)
        if entry is None:
            raise ResourceNotFound
        return ResolvedAdvertiser(
            name=entry.name,
            suffix=entry.url_parameters or None,
            technician_note=entry.technician_note,
        )


class CanonicalDestinationFormatterAdapter(DestinationFormatterPort):
    def __init__(self, destination_service: Any) -> None:
        self._service = destination_service

    async def format(self, request: DestinationFormatRequest) -> DestinationFormatResult:
        try:
            result = await self._service.format_offer(
                advertiser=request.advertiser_name,
                source_body=request.source_body,
                cap=request.cap,
                url_parameters=request.suffix or "",
            )
        except Exception as exc:
            if exc.__class__.__name__.endswith(("FormatterError", "ConfigurationError")):
                raise DestinationFormattingError from exc
            raise
        return DestinationFormatResult(
            formatted_ticket=result.text,
            internal_note=request.technician_note,
        )

    def fallback(self, request: DestinationFormatRequest) -> DestinationFormatResult:
        text = (
            f"Destination | {request.advertiser_name} | cap {request.cap}\n\n"
            "Заявка принята в работу."
        )
        return DestinationFormatResult(
            formatted_ticket=text,
            internal_note=(
                "Не удалось обработать Destination. Проверить заявку вручную"
            ),
            used_fallback=True,
        )


class PostgresMiniAppGateway:
    def __init__(
        self,
        database: PostgresDatabase,
        *,
        profile_key: str,
        profile_name: str,
        advertiser_catalog: Any,
        postback_service: Any,
        admin_ids: frozenset[int] = frozenset(),
        technician_ids: frozenset[int] = frozenset(),
    ) -> None:
        self._database = database
        self._profile_key = profile_key
        self._profile_name = profile_name
        self._advertisers = advertiser_catalog
        self._postbacks = postback_service
        self._admin_ids = admin_ids
        self._technician_ids = technician_ids

    def _profile(self, profile_key: str) -> None:
        if profile_key != self._profile_key:
            raise ResourceNotFound

    async def onboard_or_resolve(
        self, *, profile_key: str, identity: TelegramIdentity
    ) -> Actor:
        self._profile(profile_key)
        async with self._database.pool.connection() as connection:
            async with connection.transaction():
                row = await (
                    await connection.execute(
                        """
                        SELECT u.user_id,u.telegram_user_id,u.display_name,
                               m.role,m.active
                        FROM ticketing.telegram_users u
                        JOIN ticketing.memberships m USING (user_id)
                        WHERE u.telegram_user_id=%s AND m.profile_key=%s
                        FOR UPDATE OF m
                        """,
                        (identity.telegram_user_id, profile_key),
                    )
                ).fetchone()
                if row is not None:
                    if row["active"]:
                        await connection.execute(
                            "UPDATE ticketing.telegram_users SET username=%s,"
                            "display_name=%s,last_seen_at=now() WHERE user_id=%s",
                            (identity.username, identity.display_name, row["user_id"]),
                        )
                    return _actor(row)
                user = await (
                    await connection.execute(
                        """
                        INSERT INTO ticketing.telegram_users
                            (telegram_user_id,username,display_name,
                             first_seen_at,last_seen_at)
                        VALUES (%s,%s,%s,now(),now())
                        ON CONFLICT (telegram_user_id) DO UPDATE SET
                            username=EXCLUDED.username,
                            display_name=EXCLUDED.display_name,
                            last_seen_at=now()
                        RETURNING user_id
                        """,
                        (
                            identity.telegram_user_id,
                            identity.username,
                            identity.display_name,
                        ),
                    )
                ).fetchone()
                desired_role = (
                    "admin" if identity.telegram_user_id in self._admin_ids else
                    "technician" if identity.telegram_user_id in self._technician_ids else
                    "client"
                )
                await connection.execute(
                    """
                    INSERT INTO ticketing.memberships
                        (profile_key,user_id,role,active,joined_at,updated_at)
                    VALUES (%s,%s,%s,true,now(),now())
                    ON CONFLICT (profile_key,user_id) DO NOTHING
                    """,
                    (profile_key, user["user_id"], desired_role),
                )
                return Actor(
                    identity.telegram_user_id, identity.display_name, desired_role, True
                )

    async def resolve_actor(
        self, *, profile_key: str, telegram_user_id: int
    ) -> Actor | None:
        self._profile(profile_key)
        async with self._database.pool.connection() as connection:
            row = await (
                await connection.execute(
                    """
                    SELECT u.telegram_user_id,u.display_name,m.role,m.active
                    FROM ticketing.telegram_users u
                    JOIN ticketing.memberships m USING (user_id)
                    WHERE u.telegram_user_id=%s AND m.profile_key=%s
                    """,
                    (telegram_user_id, profile_key),
                )
            ).fetchone()
        return _actor(row) if row else None

    async def list_tickets(
        self, *, profile_key: str, actor: Actor, query: TicketQuery
    ) -> Page[TicketSummary]:
        self._profile(profile_key)
        where = ["t.profile_key=%s"]
        params: list[Any] = [profile_key]
        if query.scope == "mine":
            where.append("requester.telegram_user_id=%s")
            params.append(actor.telegram_user_id)
        elif not actor.is_staff:
            raise ResourceNotFound
        if query.status:
            where.append("t.status_code=%s")
            params.append(query.status)
        clause = " AND ".join(where)
        async with self._database.pool.connection() as connection:
            total = await (
                await connection.execute(
                    "SELECT count(*) AS value FROM ticketing.tickets t "
                    "JOIN ticketing.telegram_users requester "
                    "ON requester.user_id=t.requester_user_id WHERE " + clause,
                    params,
                )
            ).fetchone()
            rows = await (
                await connection.execute(
                    _SUMMARY_SQL + " WHERE " + clause
                    + " ORDER BY t.updated_at DESC,t.ticket_id DESC LIMIT %s OFFSET %s",
                    (*params, query.page_size, (query.page - 1) * query.page_size),
                )
            ).fetchall()
        items = [_summary(row, self._profile_name) for row in rows]
        count = int(total["value"])
        return Page(
            items=items, total=count, page=query.page, page_size=query.page_size,
            has_next=query.page * query.page_size < count,
        )

    async def get_ticket_view(
        self, *, profile_key: str, actor: Actor, ticket_id: int
    ) -> ClientTicketDetail | StaffTicketDetail:
        self._profile(profile_key)
        async with self._database.pool.connection() as connection:
            row = await (
                await connection.execute(
                    _SUMMARY_SQL + " WHERE t.profile_key=%s AND t.ticket_id=%s",
                    (profile_key, ticket_id),
                )
            ).fetchone()
            if row is None or (
                not actor.is_staff
                and row["requester_telegram_id"] != actor.telegram_user_id
            ):
                raise ResourceNotFound
            messages = await (
                await connection.execute(
                    """
                    SELECT m.message_id,m.visibility,m.body,m.sent_at,
                           u.display_name,m.author_role
                    FROM ticketing.ticket_messages m
                    JOIN ticketing.telegram_users u ON u.user_id=m.author_user_id
                    WHERE m.profile_key=%s AND m.ticket_id=%s
                    ORDER BY m.sent_at,m.message_id
                    """,
                    (profile_key, ticket_id),
                )
            ).fetchall()
        summary = _summary(row, self._profile_name).model_dump()
        public = [_message(item, internal=False) for item in messages
                  if item["visibility"] == "public"]
        base = {
            **summary,
            "description": row["description"],
            "is_author": row["requester_telegram_id"] == actor.telegram_user_id,
            "conversation": public,
        }
        if not actor.is_staff:
            return ClientTicketDetail.model_validate(base)
        internal = [_message(item, internal=True) for item in messages
                    if item["visibility"] == "internal"]
        return StaffTicketDetail.model_validate({**base, "internal_history": internal})

    async def list_advertisers(
        self, *, profile_key: str, actor: Actor, query: AdvertiserQuery
    ) -> Page[AdvertiserSummary]:
        self._profile(profile_key)
        if query.mode == "popular":
            entries = self._advertisers.popular(query.page_size)
            total = len(entries)
        elif query.mode == "search":
            entries = self._advertisers.search(query.query or "", query.page_size)
            total = len(entries)
        else:
            entries, _total_pages = self._advertisers.page(query.page - 1, query.page_size)
            total = self._advertisers.count
        return Page(
            items=[AdvertiserSummary(key=item.key, name=item.name) for item in entries],
            total=total, page=query.page, page_size=query.page_size,
            has_next=query.page * query.page_size < total,
        )

    async def list_postbacks(
        self, *, profile_key: str, actor: Actor
    ) -> list[PostbackSummary]:
        self._profile(profile_key)
        return [PostbackSummary(key=key, name=name)
                for key, name in self._postbacks.list_tenants().items()]

    async def get_postback(
        self, *, profile_key: str, actor: Actor, key: str
    ) -> PostbackDetail:
        self._profile(profile_key)
        tenants = self._postbacks.list_tenants()
        name = tenants.get(key)
        if name is None:
            raise ResourceNotFound
        try:
            template = self._postbacks.get_template(key)
        except (KeyError, FileNotFoundError):
            raise ResourceNotFound from None
        return PostbackDetail(key=key, name=name, template=template)


_SUMMARY_SQL = """
SELECT t.ticket_id,t.status_code,t.ticket_type_code,t.title,t.description,
       t.created_at,t.updated_at,t.closed_at,requester.telegram_user_id
       AS requester_telegram_id
FROM ticketing.tickets t
JOIN ticketing.telegram_users requester ON requester.user_id=t.requester_user_id
"""


def _actor(row: dict[str, Any]) -> Actor:
    return Actor(
        int(row["telegram_user_id"]), row["display_name"], row["role"], row["active"]
    )


def _summary(row: dict[str, Any], profile_name: str) -> TicketSummary:
    description = row["description"]
    return TicketSummary(
        ticket_id=row["ticket_id"], profile_name=profile_name,
        status=row["status_code"], ticket_type=row["ticket_type_code"],
        title=row["title"] or description[:256],
        description_preview=description[:512], created_at=row["created_at"],
        updated_at=row["updated_at"], closed_at=row["closed_at"],
    )


def _message(row: dict[str, Any], *, internal: bool):
    actor = ActorPublic(display_name=row["display_name"], role=row["author_role"])
    cls = InternalConversationMessage if internal else PublicConversationMessage
    return cls(
        message_id=row["message_id"], visibility=row["visibility"],
        body=row["body"], sent_at=row["sent_at"], actor=actor,
    )

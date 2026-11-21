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
    DraftSaveInput,
    TicketDraftDetail,
    TicketDraftSummary,
)
from .domain import InvalidTransition
from .gateway import AccessDenied, AdvertiserQuery, ResourceNotFound, TicketQuery
from .models import Actor, TelegramIdentity
from .postgres import PostgresDatabase
from .use_cases import (
    AdvertiserCatalogPort,
    DestinationFormatRequest,
    DestinationFormatResult,
    DestinationFormattingError,
    DestinationFormatterPort,
    DestinationResult,
    ExternalIdentifierPolicySpec,
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
            external_id_policy=tuple(
                ExternalIdentifierPolicySpec(
                    parameter=policy.parameter,
                    action=policy.action,
                    output_parameter=policy.output_parameter,
                )
                for policy in entry.external_id_policy
            ),
        )


class CanonicalDestinationFormatterAdapter(DestinationFormatterPort):
    def __init__(self, destination_service: Any) -> None:
        self._service = destination_service

    async def format(self, request: DestinationFormatRequest) -> DestinationFormatResult:
        try:
            result = await self._service.format_offer(
                advertiser=request.advertiser_name,
                source_body=(
                    _structured_destination_model_input(request.details)
                    if request.details is not None
                    else request.source_body
                ),
                cap=request.cap,
                cap_mode=request.cap_mode,
                url_parameters=request.suffix or "",
                external_id_policy=request.external_id_policy,
                expected_source_urls=(
                    (request.details.landing_url,) if request.details else None
                ),
                server_namings=(
                    (request.details.naming,) if request.details else None
                ),
            )
        except Exception as exc:
            if exc.__class__.__name__.endswith(("FormatterError", "ConfigurationError")):
                raise DestinationFormattingError(str(exc)) from exc
            raise
        return DestinationFormatResult(
            formatted_ticket=result.text,
            internal_note=request.technician_note,
            results=tuple(
                DestinationResult(item.naming, item.url, item.warnings)
                for item in result.results
            ),
        )

    def fallback(self, request: DestinationFormatRequest) -> DestinationFormatResult:
        text = (
            f"{request.details.naming if request.details else 'Destination'}\n\n"
            "Заявка принята в работу."
        )
        return DestinationFormatResult(
            formatted_ticket=text,
            internal_note=(
                "Не удалось обработать Destination. Проверить заявку вручную"
            ),
            used_fallback=True,
        )


def _structured_destination_model_input(
    details: DestinationRequestDetails,
) -> str:
    lines = [f"Landing URL: {details.landing_url}"]
    if details.clickid_subid:
        lines.append(f"ClickID / SubID: {details.clickid_subid}")
    if details.additional:
        lines.append(f"Additional: {details.additional}")
    return "\n".join(lines)


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
        elif actor.is_supervisor:
            where.append(
                """
                EXISTS (
                    SELECT 1
                    FROM ticketing.supervisor_subordinates ss
                    JOIN ticketing.memberships supervisor_membership
                      ON supervisor_membership.profile_key=ss.profile_key
                     AND supervisor_membership.user_id=ss.supervisor_user_id
                    JOIN ticketing.telegram_users supervisor_user
                      ON supervisor_user.user_id=ss.supervisor_user_id
                    JOIN ticketing.memberships subordinate_membership
                      ON subordinate_membership.profile_key=ss.profile_key
                     AND subordinate_membership.user_id=ss.subordinate_user_id
                    WHERE ss.profile_key=t.profile_key
                      AND ss.subordinate_user_id=t.requester_user_id
                      AND ss.active
                      AND supervisor_membership.active
                      AND supervisor_membership.role='supervisor'
                      AND subordinate_membership.active
                      AND supervisor_user.telegram_user_id=%s
                )
                """
            )
            params.append(actor.telegram_user_id)
        elif not actor.is_staff:
            raise ResourceNotFound
        if query.status:
            where.append("t.status_code=%s")
            params.append(query.status)
        if query.search:
            # Match the same visible title as _summary(), including Destination naming.
            where.append("""
                strpos(lower(COALESCE(
                    NULLIF((SELECT r.naming FROM ticketing.destination_results r
                        WHERE r.ticket_id=t.ticket_id AND r.profile_key=t.profile_key
                        ORDER BY r.result_order LIMIT 1), ''),
                    NULLIF(t.title, ''), left(t.description, 256)
                )), lower(%s)) > 0
            """)
            params.append(query.search)
        if query.issued_to_buying:
            where.append(
                "EXISTS (SELECT 1 FROM ticketing.ticket_buying_flags buying "
                "WHERE buying.ticket_id=t.ticket_id "
                "AND buying.profile_key=t.profile_key)"
            )
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
        items = [
            _summary(
                row,
                self._profile_name,
                include_buying_flag=actor.is_supervisor,
            )
            for row in rows
        ]
        count = int(total["value"])
        return Page(
            items=items, total=count, page=query.page, page_size=query.page_size,
            has_next=query.page * query.page_size < count,
        )

    async def set_buying_flag(
        self,
        *,
        profile_key: str,
        actor: Actor,
        ticket_id: int,
        issued: bool,
    ) -> bool:
        self._profile(profile_key)
        if profile_key != "kalisto" or not actor.is_supervisor:
            raise AccessDenied
        async with self._database.pool.connection() as connection:
            async with connection.transaction():
                row = await (
                    await connection.execute(
                        """
                        SELECT t.status_code,t.ticket_type_code
                        FROM ticketing.tickets t
                        JOIN ticketing.telegram_users requester
                          ON requester.user_id=t.requester_user_id
                        WHERE t.profile_key=%s AND t.ticket_id=%s
                          AND (
                            requester.telegram_user_id=%s
                            OR EXISTS (
                                SELECT 1
                                FROM ticketing.supervisor_subordinates ss
                                JOIN ticketing.memberships supervisor_membership
                                  ON supervisor_membership.profile_key=ss.profile_key
                                 AND supervisor_membership.user_id=ss.supervisor_user_id
                                JOIN ticketing.telegram_users supervisor_user
                                  ON supervisor_user.user_id=ss.supervisor_user_id
                                JOIN ticketing.memberships subordinate_membership
                                  ON subordinate_membership.profile_key=ss.profile_key
                                 AND subordinate_membership.user_id=ss.subordinate_user_id
                                WHERE ss.profile_key=t.profile_key
                                  AND ss.subordinate_user_id=t.requester_user_id
                                  AND ss.active
                                  AND supervisor_membership.active
                                  AND supervisor_membership.role='supervisor'
                                  AND subordinate_membership.active
                                  AND supervisor_user.telegram_user_id=%s
                            )
                          )
                        FOR UPDATE
                        """,
                        (
                            profile_key,
                            ticket_id,
                            actor.telegram_user_id,
                            actor.telegram_user_id,
                        ),
                    )
                ).fetchone()
                if row is None:
                    raise ResourceNotFound
                if row["ticket_type_code"] != "destination":
                    raise InvalidTransition
                if issued and row["status_code"] != "completed":
                    raise InvalidTransition
                if issued:
                    await connection.execute(
                        """
                        INSERT INTO ticketing.ticket_buying_flags (
                            profile_key,ticket_id
                        ) VALUES (%s,%s)
                        ON CONFLICT (profile_key,ticket_id) DO NOTHING
                        """,
                        (profile_key, ticket_id),
                    )
                else:
                    await connection.execute(
                        """
                        DELETE FROM ticketing.ticket_buying_flags
                        WHERE profile_key=%s AND ticket_id=%s
                        """,
                        (profile_key, ticket_id),
                    )
        return issued

    async def get_ticket_view(
        self, *, profile_key: str, actor: Actor, ticket_id: int
    ) -> ClientTicketDetail | StaffTicketDetail:
        self._profile(profile_key)
        visibility = ""
        params: list[Any] = [profile_key, ticket_id]
        if actor.is_supervisor:
            visibility = """
                AND (
                    requester.telegram_user_id=%s
                    OR EXISTS (
                        SELECT 1
                        FROM ticketing.supervisor_subordinates ss
                        JOIN ticketing.memberships supervisor_membership
                          ON supervisor_membership.profile_key=ss.profile_key
                         AND supervisor_membership.user_id=ss.supervisor_user_id
                        JOIN ticketing.telegram_users supervisor_user
                          ON supervisor_user.user_id=ss.supervisor_user_id
                        JOIN ticketing.memberships subordinate_membership
                          ON subordinate_membership.profile_key=ss.profile_key
                         AND subordinate_membership.user_id=ss.subordinate_user_id
                        WHERE ss.profile_key=t.profile_key
                          AND ss.subordinate_user_id=t.requester_user_id
                          AND ss.active
                          AND supervisor_membership.active
                          AND supervisor_membership.role='supervisor'
                          AND subordinate_membership.active
                          AND supervisor_user.telegram_user_id=%s
                    )
                )
            """
            params.extend((actor.telegram_user_id, actor.telegram_user_id))
        elif not actor.is_staff:
            visibility = " AND requester.telegram_user_id=%s"
            params.append(actor.telegram_user_id)
        async with self._database.pool.connection() as connection:
            row = await (
                await connection.execute(
                    _SUMMARY_SQL
                    + " WHERE t.profile_key=%s AND t.ticket_id=%s"
                    + visibility,
                    params,
                )
            ).fetchone()
            if row is None:
                raise ResourceNotFound
            message_visibility = "" if actor.is_staff else " AND m.visibility='public'"
            messages = await (
                await connection.execute(
                    """
                    SELECT m.message_id,m.visibility,m.body,m.sent_at,
                           u.display_name,m.author_role
                    FROM ticketing.ticket_messages m
                    JOIN ticketing.telegram_users u ON u.user_id=m.author_user_id
                    WHERE m.profile_key=%s AND m.ticket_id=%s
                    """ + message_visibility + """
                    ORDER BY m.sent_at,m.message_id
                    """,
                    (profile_key, ticket_id),
                )
            ).fetchall()
        summary = _summary(
            row,
            self._profile_name,
            include_buying_flag=actor.is_supervisor,
        ).model_dump()
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
        return StaffTicketDetail.model_validate({
            **base,
            "new_advertiser": bool(row["new_advertiser"]),
            "internal_history": internal,
        })

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

    async def request_advertiser_refresh(
        self, *, profile_key: str, actor: Actor
    ) -> dict[str, str]:
        self._profile(profile_key)
        async with self._database.pool.connection() as connection:
            async with connection.transaction():
                row = await (
                    await connection.execute(
                        """
                        INSERT INTO ticketing.aio_advertiser_refresh_requests (
                            profile_key,requested_at,started_at,completed_at,outcome
                        ) VALUES (%s,now(),NULL,NULL,NULL)
                        ON CONFLICT (profile_key) DO UPDATE SET
                            requested_at=now(),started_at=NULL,
                            completed_at=NULL,outcome=NULL
                        WHERE
                            ticketing.aio_advertiser_refresh_requests.started_at IS NULL
                            OR (
                                ticketing.aio_advertiser_refresh_requests.completed_at
                                    IS NOT NULL
                                AND ticketing.aio_advertiser_refresh_requests.completed_at
                                    <= now() - interval '60 seconds'
                            )
                        RETURNING requested_at,started_at,completed_at,outcome
                        """,
                        (profile_key,),
                    )
                ).fetchone()
                if row is None:
                    row = await (
                        await connection.execute(
                            """
                            SELECT requested_at,started_at,completed_at,outcome
                            FROM ticketing.aio_advertiser_refresh_requests
                            WHERE profile_key=%s
                            """,
                            (profile_key,),
                        )
                    ).fetchone()
        return _advertiser_refresh_status(row)

    async def get_advertiser_refresh_status(
        self, *, profile_key: str, actor: Actor
    ) -> dict[str, str]:
        self._profile(profile_key)
        async with self._database.pool.connection() as connection:
            row = await (
                await connection.execute(
                    """
                    SELECT requested_at,started_at,completed_at,outcome
                    FROM ticketing.aio_advertiser_refresh_requests
                    WHERE profile_key=%s
                    """,
                    (profile_key,),
                )
            ).fetchone()
        result = _advertiser_refresh_status(row)
        if result["status"] == "succeeded":
            self._advertisers.invalidate()
        return result

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

    async def list_drafts(
        self, *, profile_key: str, actor: Actor
    ) -> list[TicketDraftSummary]:
        self._profile(profile_key)
        async with self._database.pool.connection() as connection:
            rows = await (
                await connection.execute(
                    _DRAFT_SQL
                    + " WHERE d.profile_key=%s AND u.telegram_user_id=%s "
                    "AND d.status='draft' ORDER BY d.updated_at DESC,d.draft_id DESC",
                    (profile_key, actor.telegram_user_id),
                )
            ).fetchall()
        return [_draft_summary(row) for row in rows]

    async def get_draft(
        self, *, profile_key: str, actor: Actor, draft_id: int
    ) -> TicketDraftDetail:
        self._profile(profile_key)
        async with self._database.pool.connection() as connection:
            row = await (
                await connection.execute(
                    _DRAFT_SQL
                    + " WHERE d.profile_key=%s AND d.draft_id=%s "
                    "AND u.telegram_user_id=%s AND d.status='draft'",
                    (profile_key, draft_id, actor.telegram_user_id),
                )
            ).fetchone()
        if row is None:
            raise ResourceNotFound
        return _draft_detail(row)

    async def list_destination_comments(
        self, *, profile_key: str, actor: Actor
    ) -> list[str]:
        self._profile(profile_key)
        async with self._database.pool.connection() as connection:
            rows = await (
                await connection.execute(
                    """
                    SELECT recent.important_comment
                    FROM (
                        SELECT DISTINCT ON (normalized_comment)
                               t.important_comment, t.created_at, t.ticket_id,
                               lower(regexp_replace(btrim(t.important_comment),
                                                    '[[:space:]]+', ' ', 'g'))
                                   AS normalized_comment
                        FROM ticketing.tickets t
                        JOIN ticketing.telegram_users u
                          ON u.user_id=t.requester_user_id
                        WHERE t.profile_key=%s
                          AND u.telegram_user_id=%s
                          AND t.ticket_type_code='destination'
                          AND t.important_comment IS NOT NULL
                        ORDER BY normalized_comment,t.created_at DESC,t.ticket_id DESC
                    ) recent
                    ORDER BY recent.created_at DESC,recent.ticket_id DESC
                    LIMIT 20
                    """,
                    (profile_key, actor.telegram_user_id),
                )
            ).fetchall()
        return [row["important_comment"] for row in rows]

    async def save_draft(
        self,
        *,
        profile_key: str,
        actor: Actor,
        draft_id: int,
        payload: DraftSaveInput,
    ) -> TicketDraftDetail:
        self._profile(profile_key)
        advertiser_kind = payload.advertiser.kind if payload.advertiser else None
        advertiser_key = (
            payload.advertiser.key
            if payload.advertiser and payload.advertiser.kind == "catalog"
            else None
        )
        advertiser_name = (
            payload.advertiser.name
            if payload.advertiser and payload.advertiser.kind == "new"
            else None
        )
        details = payload.details
        important_comment = payload.important_comment.strip() or None
        if payload.type == "general":
            advertiser_kind = advertiser_key = advertiser_name = None
            cap = None
            cap_unlimited = False
            cap_mode = None
            details = None
            important_comment = None
        else:
            cap = payload.cap if isinstance(payload.cap, int) else None
            cap_unlimited = payload.cap == "uncap"
            cap_mode = payload.cap_mode
            if payload.type is None:
                important_comment = None
        async with self._database.pool.connection() as connection:
            async with connection.transaction():
                row = await (
                    await connection.execute(
                        """
                        UPDATE ticketing.ticket_drafts d SET
                            ticket_type=%s,edited_body=%s,client_comment=%s,
                            advertiser_kind=%s,
                            advertiser_key=%s,advertiser_name=%s,cap=%s,
                            cap_unlimited=%s,cap_mode=%s,naming=%s,
                            offer_name=%s,platform=%s,approach=%s,geo=%s,rate=%s,currency=%s,
                            landing_url=%s,clickid_subid=%s,additional=%s,
                            important_comment=%s,
                            updated_at=now()
                        FROM ticketing.telegram_users u
                        WHERE d.author_user_id=u.user_id AND d.profile_key=%s
                          AND d.draft_id=%s AND u.telegram_user_id=%s
                          AND d.status='draft'
                        RETURNING d.draft_id
                        """,
                        (
                            payload.type,
                            payload.body,
                            payload.client_comment,
                            advertiser_kind,
                            advertiser_key,
                            advertiser_name,
                            cap,
                            cap_unlimited,
                            cap_mode,
                            details.naming if details else None,
                            details.offer_name if details else None,
                            details.platform if details else None,
                            details.approach if details else None,
                            details.geo if details else None,
                            details.rate if details else None,
                            details.currency if details else None,
                            details.landing_url if details else None,
                            details.clickid_subid if details else "",
                            details.additional if details else "",
                            important_comment,
                            profile_key,
                            draft_id,
                            actor.telegram_user_id,
                        ),
                    )
                ).fetchone()
                if row is None:
                    raise ResourceNotFound
        return await self.get_draft(
            profile_key=profile_key, actor=actor, draft_id=draft_id
        )

    async def cancel_draft(
        self, *, profile_key: str, actor: Actor, draft_id: int
    ) -> None:
        self._profile(profile_key)
        async with self._database.pool.connection() as connection:
            row = await (
                await connection.execute(
                    """
                    UPDATE ticketing.ticket_drafts d SET
                        status='cancelled',cancelled_at=now(),updated_at=now()
                    FROM ticketing.telegram_users u
                    WHERE d.author_user_id=u.user_id AND d.profile_key=%s
                      AND d.draft_id=%s AND u.telegram_user_id=%s
                      AND d.status='draft'
                    RETURNING d.draft_id
                    """,
                    (profile_key, draft_id, actor.telegram_user_id),
                )
            ).fetchone()
        if row is None:
            raise ResourceNotFound

    async def mark_draft_submitted(
        self,
        *,
        profile_key: str,
        actor: Actor,
        draft_id: int,
        ticket_id: int,
    ) -> None:
        self._profile(profile_key)
        async with self._database.pool.connection() as connection:
            row = await (
                await connection.execute(
                    """
                    UPDATE ticketing.ticket_drafts d SET
                        status='submitted',ticket_id=%s,submitted_at=now(),updated_at=now()
                    FROM ticketing.telegram_users u
                    WHERE d.author_user_id=u.user_id AND d.profile_key=%s
                      AND d.draft_id=%s AND u.telegram_user_id=%s
                      AND (d.status='draft' OR
                           (d.status='submitted' AND d.ticket_id=%s))
                      AND EXISTS (
                          SELECT 1 FROM ticketing.tickets t
                          WHERE t.ticket_id=%s AND t.profile_key=d.profile_key
                            AND t.requester_user_id=d.author_user_id
                      )
                    RETURNING d.draft_id
                    """,
                    (
                        ticket_id,
                        profile_key,
                        draft_id,
                        actor.telegram_user_id,
                        ticket_id,
                        ticket_id,
                    ),
                )
            ).fetchone()
        if row is None:
            raise ResourceNotFound


_SUMMARY_SQL = """
SELECT t.ticket_id,t.ticket_number,t.status_code,t.ticket_type_code,t.title,t.description,
       t.important_comment,t.new_advertiser,
       t.created_at,t.updated_at,t.closed_at,requester.telegram_user_id
       AS requester_telegram_id,
       EXISTS (
           SELECT 1 FROM ticketing.ticket_buying_flags buying
           WHERE buying.ticket_id=t.ticket_id
             AND buying.profile_key=t.profile_key
       ) AS issued_to_buying,
       COALESCE((
           SELECT jsonb_agg(
               jsonb_build_object('naming',r.naming,'url',r.destination_url)
               ORDER BY r.result_order
           )
           FROM ticketing.destination_results r
           WHERE r.ticket_id=t.ticket_id AND r.profile_key=t.profile_key
       ), '[]'::jsonb) AS destination_results
FROM ticketing.tickets t
JOIN ticketing.telegram_users requester ON requester.user_id=t.requester_user_id
"""

def _advertiser_refresh_status(row: Any | None) -> dict[str, str]:
    if row is None:
        return {"status": "idle"}
    if row["completed_at"] is not None:
        return {
            "status": (
                "succeeded" if row["outcome"] == "succeeded" else "failed"
            )
        }
    if row["started_at"] is not None:
        return {"status": "running"}
    return {"status": "pending"}


_DRAFT_SQL = """
SELECT d.draft_id,d.source_body,d.client_comment,d.edited_body,
       d.advertiser_kind,d.advertiser_key,d.advertiser_name,d.cap,
       d.cap_unlimited,d.cap_mode,d.ticket_type,d.naming,d.offer_name,d.platform,
       d.approach,d.geo,d.rate,d.currency,
       d.landing_url,d.clickid_subid,d.additional,d.important_comment,
       d.forward_origin_name,d.created_at,d.updated_at
FROM ticketing.ticket_drafts d
JOIN ticketing.telegram_users u ON u.user_id=d.author_user_id
"""


def _actor(row: dict[str, Any]) -> Actor:
    return Actor(
        int(row["telegram_user_id"]), row["display_name"], row["role"], row["active"]
    )


def _summary(
    row: dict[str, Any],
    profile_name: str,
    *,
    include_buying_flag: bool = False,
) -> TicketSummary:
    description = row["description"]
    results = row.get("destination_results") or []
    first_naming = results[0]["naming"] if results else None
    return TicketSummary(
        ticket_id=row["ticket_id"], ticket_number=row["ticket_number"],
        profile_name=profile_name,
        status=row["status_code"], ticket_type=row["ticket_type_code"],
        title=first_naming or row["title"] or description[:256],
        description_preview=description[:512], created_at=row["created_at"],
        updated_at=row["updated_at"], closed_at=row["closed_at"],
        issued_to_buying=(
            bool(row["issued_to_buying"]) if include_buying_flag else False
        ),
        important_comment=row["important_comment"],
        destination_results=results,
    )


def _message(row: dict[str, Any], *, internal: bool):
    actor = ActorPublic(display_name=row["display_name"], role=row["author_role"])
    cls = InternalConversationMessage if internal else PublicConversationMessage
    return cls(
        message_id=row["message_id"], visibility=row["visibility"],
        body=row["body"], sent_at=row["sent_at"], actor=actor,
    )


def _draft_summary(row: dict[str, Any]) -> TicketDraftSummary:
    return TicketDraftSummary(
        draft_id=row["draft_id"],
        source_preview=row["edited_body"][:512],
        client_comment=row["client_comment"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _draft_detail(row: dict[str, Any]) -> TicketDraftDetail:
    details = None
    if any(
        row[key] is not None
        for key in (
            "naming", "offer_name", "platform", "approach", "geo", "rate",
            "currency", "landing_url",
        )
    ) or row["clickid_subid"] or row["additional"]:
        details = {
            "naming": row["naming"],
            "offer_name": row["offer_name"],
            "platform": row["platform"],
            "approach": row["approach"],
            "geo": row["geo"],
            "rate": row["rate"],
            "currency": row["currency"],
            "landing_url": row["landing_url"],
            "clickid_subid": row["clickid_subid"],
            "additional": row["additional"],
        }
    return TicketDraftDetail(
        **_draft_summary(row).model_dump(),
        source_body=row["source_body"],
        edited_body=row["edited_body"],
        advertiser_kind=row["advertiser_kind"],
        advertiser_key=row["advertiser_key"],
        advertiser_name=row["advertiser_name"],
        cap="uncap" if row["cap_unlimited"] else row["cap"],
        cap_mode=row["cap_mode"],
        forward_origin_name=row["forward_origin_name"],
        ticket_type=row["ticket_type"],
        important_comment=row["important_comment"] or "",
        details=details,
    )

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated, Any
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from .auth import InitDataError, verify_init_data
from .analytics_handoff import issue_analytics_handoff
from .dto import (
    AdvertiserSummary,
    BuyingFlagInput,
    BuyingFlagResponse,
    CreateTicketInput,
    ClientTicketDetail,
    DestinationTicketCreate,
    DestinationDraftSubmitInput,
    DraftMutationResponse,
    DraftSaveInput,
    DraftSubmitInput,
    EmptyInput,
    HelpContent,
    MessageInput,
    MutationResponse,
    Page,
    PostbackDetail,
    PostbackSummary,
    ReasonInput,
    StaffTicketDetail,
    TicketSummary,
    TicketDraftDetail,
    TicketDraftSummary,
)
from .domain import (
    ActionForbidden,
    InvalidTransition,
    ReasonRequired,
    TicketHidden,
    WorkflowAction,
)
from .gateway import (
    AccessDenied,
    AdvertiserQuery,
    BackendUnavailable,
    MiniAppGateway,
    ResourceNotFound,
    TicketQuery,
)
from .models import Actor
from .sessions import SessionRecord, SessionStore, csrf_matches
from .settings import Settings
from .use_cases import (
    AddMessageCommand,
    CatalogAdvertiser,
    CreateTicketCommand,
    DestinationRequestDetails,
    IdempotencyConflict,
    NewAdvertiser,
    TicketMutationService,
    TransitionCommand,
)


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    init_data: str = Field(min_length=1, max_length=16_384)


@dataclass(frozen=True)
class Authenticated:
    actor: Actor
    session: SessionRecord


def create_app(
    *,
    settings: Settings,
    gateway: MiniAppGateway,
    sessions: SessionStore,
    mutations: TicketMutationService,
    help_directory: Path | None = None,
    lifespan: Any = None,
) -> FastAPI:
    app = FastAPI(
        title="Destination Mini App API",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    web_root = Path(__file__).with_name("web")
    help_root = help_directory or web_root / "help"
    app.mount("/assets", StaticFiles(directory=web_root / "assets"), name="assets")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.allowed_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self' https://telegram.org; "
            "style-src 'self'; img-src 'self' data:; connect-src 'self'; "
            "frame-ancestors 'none'"
        )
        return response

    @app.get("/", include_in_schema=False)
    async def miniapp_index():
        return FileResponse(web_root / "index.html")

    async def authenticated(
        session_id: str | None = Cookie(default=None, alias=settings.session_cookie_name),
    ) -> Authenticated:
        if not session_id:
            raise HTTPException(status_code=401, detail="authentication required")
        record = await sessions.get(session_id)
        if record is None or record.profile_key != settings.profile_key:
            raise HTTPException(status_code=401, detail="session expired")
        actor = await gateway.resolve_actor(
            profile_key=settings.profile_key,
            telegram_user_id=record.telegram_user_id,
        )
        if actor is None or not actor.active:
            await sessions.invalidate(session_id)
            raise HTTPException(status_code=403, detail="membership inactive")
        return Authenticated(actor=actor, session=record)

    @app.exception_handler(ResourceNotFound)
    async def not_found_handler(_request: Request, _exc: ResourceNotFound):
        return _error_response(404, "resource not found")

    @app.exception_handler(AccessDenied)
    async def access_denied_handler(_request: Request, _exc: AccessDenied):
        return _error_response(403, "operation forbidden")

    @app.exception_handler(ActionForbidden)
    async def action_forbidden_handler(_request: Request, _exc: ActionForbidden):
        return _error_response(403, "operation forbidden")

    @app.exception_handler(TicketHidden)
    async def ticket_hidden_handler(_request: Request, _exc: TicketHidden):
        return _error_response(404, "resource not found")

    @app.exception_handler(InvalidTransition)
    @app.exception_handler(IdempotencyConflict)
    async def conflict_handler(_request: Request, _exc: Exception):
        return _error_response(409, "state or idempotency conflict")

    @app.exception_handler(ReasonRequired)
    async def reason_required_handler(_request: Request, _exc: ReasonRequired):
        return _error_response(422, "public reason is required")

    @app.exception_handler(BackendUnavailable)
    async def unavailable_handler(_request: Request, _exc: BackendUnavailable):
        return _error_response(503, "backend temporarily unavailable")

    @app.post("/api/session", status_code=201)
    async def create_session(payload: SessionRequest, request: Request, response: Response):
        if request.headers.get("origin") != settings.allowed_origin:
            raise HTTPException(status_code=403, detail="origin forbidden")
        try:
            verified = verify_init_data(
                payload.init_data,
                bot_token=settings.bot_token,
                max_age_seconds=settings.init_data_max_age_seconds,
            )
        except (InitDataError, ValueError):
            raise HTTPException(status_code=401, detail="invalid initData") from None
        actor = await gateway.onboard_or_resolve(
            profile_key=settings.profile_key, identity=verified.identity
        )
        if not actor.active:
            raise HTTPException(status_code=403, detail="membership inactive")
        issued = await sessions.rotate(
            telegram_user_id=actor.telegram_user_id,
            profile_key=settings.profile_key,
            ttl_seconds=settings.session_ttl_seconds,
        )
        response.set_cookie(
            key=settings.session_cookie_name,
            value=issued.session_id,
            max_age=settings.session_ttl_seconds,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
        return {
            "role": actor.role,
            "profile": {"key": settings.profile_key, "name": settings.profile_name},
            "csrf_token": issued.csrf_token,
        }

    @app.delete("/api/session", status_code=204)
    async def delete_session(
        response: Response,
        auth: Authenticated = Depends(authenticated),
        session_id: str | None = Cookie(default=None, alias=settings.session_cookie_name),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> None:
        if not csrf_matches(auth.session, csrf_token):
            raise HTTPException(status_code=403, detail="invalid CSRF token")
        assert session_id is not None
        await sessions.invalidate(session_id)
        response.delete_cookie(
            key=settings.session_cookie_name,
            path="/",
            secure=True,
            httponly=True,
            samesite="lax",
        )

    @app.get("/api/me")
    async def me(auth: Authenticated = Depends(authenticated)):
        actor = auth.actor
        return {
            "display_name": actor.display_name,
            "role": actor.role,
            "profile": {"key": settings.profile_key, "name": settings.profile_name},
            "links": {
                "analytics": settings.analytics_url,
            } if (
                settings.analytics_url
                and actor.telegram_user_id in settings.analytics_allowed_user_ids
            ) else {},
            "capabilities": {
                "team_queue": actor.is_staff or actor.is_supervisor,
                "buying_flag": (
                    settings.profile_key == "kalisto" and actor.is_supervisor
                ),
                "internal_history": actor.is_staff,
                "technical_actions": actor.is_staff,
                "submit_review": actor.is_staff,
                "complete_destination": actor.is_staff,
            },
        }

    @app.post("/api/analytics/handoff")
    async def analytics_handoff(
        _payload: EmptyInput,
        auth: Authenticated = Depends(authenticated),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        _require_csrf(auth, csrf_token)
        if not settings.analytics_url:
            raise HTTPException(status_code=404, detail="resource not found")
        if auth.actor.telegram_user_id not in settings.analytics_allowed_user_ids:
            raise HTTPException(status_code=403, detail="operation forbidden")
        handoff = issue_analytics_handoff(settings, auth.actor)
        return {"url": handoff.url, "expires_at": handoff.expires_at}

    @app.get("/api/tickets")
    async def tickets(
        auth: Authenticated = Depends(authenticated),
        scope: Annotated[str, Query(pattern="^(mine|team)$")] = "mine",
        status: Annotated[str | None, Query(pattern="^(open|review|completed)$")] = None,
        issued_to_buying: bool = False,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        search: Annotated[str, Query(max_length=200)] = "",
    ):
        if scope == "team" and not (
            auth.actor.is_staff or auth.actor.is_supervisor
        ):
            raise HTTPException(status_code=403, detail="team scope forbidden")
        if issued_to_buying and not (
            settings.profile_key == "kalisto" and auth.actor.is_supervisor
        ):
            raise HTTPException(status_code=403, detail="operation forbidden")
        result = await gateway.list_tickets(
            profile_key=settings.profile_key,
            actor=auth.actor,
            query=TicketQuery(
                scope=scope,
                status=status,
                issued_to_buying=issued_to_buying,
                search=search.strip(),
                page=page,
                page_size=page_size,
            ),  # type: ignore[arg-type]
        )
        return _validate_output(Page[TicketSummary], result)

    @app.get("/api/drafts")
    async def drafts(auth: Authenticated = Depends(authenticated)):
        result = await gateway.list_drafts(
            profile_key=settings.profile_key, actor=auth.actor
        )
        return _validate_output(TypeAdapter(list[TicketDraftSummary]), result)

    @app.get("/api/destination-comments")
    async def destination_comments(auth: Authenticated = Depends(authenticated)):
        if settings.profile_key != "mephisto":
            raise HTTPException(status_code=404, detail="resource not found")
        result = await gateway.list_destination_comments(
            profile_key=settings.profile_key, actor=auth.actor
        )
        return _validate_output(TypeAdapter(list[str]), result)

    @app.get("/api/drafts/{draft_id}")
    async def draft(
        draft_id: int, auth: Authenticated = Depends(authenticated)
    ):
        if draft_id <= 0:
            raise ResourceNotFound
        result = await gateway.get_draft(
            profile_key=settings.profile_key,
            actor=auth.actor,
            draft_id=draft_id,
        )
        return _validate_output(TicketDraftDetail, result)

    @app.post("/api/drafts/{draft_id}/save")
    async def save_draft(
        draft_id: int,
        payload: DraftSaveInput,
        auth: Authenticated = Depends(authenticated),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        if draft_id <= 0:
            raise ResourceNotFound
        _require_csrf(auth, csrf_token)
        _validate_draft_shape(settings.profile_key, payload)
        result = await gateway.save_draft(
            profile_key=settings.profile_key,
            actor=auth.actor,
            draft_id=draft_id,
            payload=payload,
        )
        return _validate_output(TicketDraftDetail, result)

    @app.delete("/api/drafts/{draft_id}", status_code=204)
    async def cancel_draft(
        draft_id: int,
        auth: Authenticated = Depends(authenticated),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> Response:
        if draft_id <= 0:
            raise ResourceNotFound
        _require_csrf(auth, csrf_token)
        await gateway.cancel_draft(
            profile_key=settings.profile_key,
            actor=auth.actor,
            draft_id=draft_id,
        )
        return Response(status_code=204)

    @app.post("/api/drafts/{draft_id}/submit", status_code=201)
    async def submit_draft(
        draft_id: int,
        payload: DraftSubmitInput,
        auth: Authenticated = Depends(authenticated),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        if draft_id <= 0:
            raise ResourceNotFound
        _require_csrf(auth, csrf_token)
        _validate_destination_submission(settings.profile_key, payload)
        await gateway.get_draft(
            profile_key=settings.profile_key,
            actor=auth.actor,
            draft_id=draft_id,
        )
        save_payload = DraftSaveInput(
            type=payload.type,
            advertiser=(
                payload.advertiser
                if isinstance(payload, DestinationDraftSubmitInput)
                else None
            ),
            cap=(
                payload.cap if isinstance(payload, DestinationDraftSubmitInput) else None
            ),
            cap_mode=(
                payload.cap_mode
                if isinstance(payload, DestinationDraftSubmitInput)
                else None
            ),
            body=payload.body,
            client_comment=payload.client_comment,
            important_comment=(
                payload.important_comment
                if isinstance(payload, DestinationDraftSubmitInput)
                else ""
            ),
            details=(
                payload.details.model_dump()
                if isinstance(payload, DestinationDraftSubmitInput)
                and payload.details is not None
                else None
            ),
        )
        _validate_draft_shape(settings.profile_key, save_payload)
        saved = _validate_output(
            TicketDraftDetail,
            await gateway.save_draft(
                profile_key=settings.profile_key,
                actor=auth.actor,
                draft_id=draft_id,
                payload=save_payload,
            ),
        )
        source_body = saved.edited_body
        if saved.client_comment:
            source_body = f"{saved.client_comment}\n\n{source_body}"
        advertiser = None
        cap = None
        cap_mode = None
        if isinstance(payload, DestinationDraftSubmitInput):
            advertiser = (
                CatalogAdvertiser("catalog", payload.advertiser.key)
                if payload.advertiser.kind == "catalog"
                else NewAdvertiser("new", payload.advertiser.name)
            )
            cap = payload.cap
            cap_mode = payload.cap_mode
        result = await mutations.create_ticket(
            CreateTicketCommand(
                profile_key=settings.profile_key,
                actor=auth.actor,
                idempotency_key=uuid5(
                    NAMESPACE_URL,
                    f"ticket-draft:{settings.profile_key}:{draft_id}",
                ),
                ticket_type=payload.type,
                body=source_body,
                advertiser=advertiser,
                cap=cap,
                cap_mode=cap_mode,
                details=(
                    _command_details(payload.details)
                    if isinstance(payload, DestinationDraftSubmitInput)
                    else None
                ),
                important_comment=(
                    saved.important_comment
                    if isinstance(payload, DestinationDraftSubmitInput)
                    else None
                ),
            )
        )
        await gateway.mark_draft_submitted(
            profile_key=settings.profile_key,
            actor=auth.actor,
            draft_id=draft_id,
            ticket_id=result.ticket_id,
        )
        return _validate_output(
            DraftMutationResponse,
            {
                "draft_id": draft_id,
                "status": "submitted",
                "ticket_id": result.ticket_id,
                "ticket_number": result.ticket_number,
                "replayed": result.replayed,
            },
        )

    @app.post("/api/tickets")
    async def create_ticket(
        payload: CreateTicketInput,
        response: Response,
        auth: Authenticated = Depends(authenticated),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
        idempotency_key: UUID = Header(alias="Idempotency-Key"),
    ):
        _require_csrf(auth, csrf_token)
        _validate_destination_submission(settings.profile_key, payload)
        advertiser = None
        cap = None
        cap_mode = None
        if isinstance(payload, DestinationTicketCreate):
            cap = payload.cap
            cap_mode = payload.cap_mode
            if payload.advertiser.kind == "catalog":
                advertiser = CatalogAdvertiser("catalog", payload.advertiser.key)
            else:
                advertiser = NewAdvertiser("new", payload.advertiser.name)
        result = await mutations.create_ticket(
            CreateTicketCommand(
                profile_key=settings.profile_key,
                actor=auth.actor,
                idempotency_key=idempotency_key,
                ticket_type=payload.type,
                body=payload.body,
                advertiser=advertiser,
                cap=cap,
                cap_mode=cap_mode,
                details=(
                    _command_details(payload.details)
                    if isinstance(payload, DestinationTicketCreate)
                    else None
                ),
                important_comment=(
                    payload.important_comment
                    if isinstance(payload, DestinationTicketCreate)
                    else None
                ),
            )
        )
        response.status_code = 200 if result.replayed else 201
        return _validate_output(MutationResponse, asdict(result))

    @app.get("/api/tickets/{ticket_id}")
    async def ticket(ticket_id: int, auth: Authenticated = Depends(authenticated)):
        if ticket_id <= 0:
            raise HTTPException(status_code=404, detail="resource not found")
        try:
            result = await gateway.get_ticket_view(
                profile_key=settings.profile_key, actor=auth.actor, ticket_id=ticket_id
            )
        except AccessDenied as exc:
            raise ResourceNotFound from exc
        detail_type = StaffTicketDetail if auth.actor.is_staff else ClientTicketDetail
        return _validate_output(detail_type, result)

    @app.post("/api/tickets/{ticket_id}/buying-flag")
    async def set_buying_flag(
        ticket_id: int,
        payload: BuyingFlagInput,
        auth: Authenticated = Depends(authenticated),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        _require_positive_ticket_id(ticket_id)
        _require_csrf(auth, csrf_token)
        if settings.profile_key != "kalisto" or not auth.actor.is_supervisor:
            raise HTTPException(status_code=403, detail="operation forbidden")
        issued = await gateway.set_buying_flag(
            profile_key=settings.profile_key,
            actor=auth.actor,
            ticket_id=ticket_id,
            issued=payload.issued,
        )
        return _validate_output(
            BuyingFlagResponse,
            {"ticket_id": ticket_id, "issued_to_buying": issued},
        )

    @app.post("/api/tickets/{ticket_id}/messages")
    async def add_message(
        ticket_id: int,
        payload: MessageInput,
        response: Response,
        auth: Authenticated = Depends(authenticated),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
        idempotency_key: UUID = Header(alias="Idempotency-Key"),
    ):
        _require_positive_ticket_id(ticket_id)
        _require_csrf(auth, csrf_token)
        if payload.visibility == "internal" and not auth.actor.is_staff:
            raise HTTPException(status_code=403, detail="operation forbidden")
        result = await mutations.add_message(
            AddMessageCommand(
                profile_key=settings.profile_key,
                actor=auth.actor,
                ticket_id=ticket_id,
                idempotency_key=idempotency_key,
                visibility=payload.visibility,
                body=payload.body,
            )
        )
        response.status_code = 200 if result.replayed else 201
        return _validate_output(MutationResponse, asdict(result))

    @app.post("/api/tickets/{ticket_id}/submit-review")
    async def submit_review(
        ticket_id: int,
        _payload: EmptyInput,
        auth: Authenticated = Depends(authenticated),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
        idempotency_key: UUID = Header(alias="Idempotency-Key"),
    ):
        if not auth.actor.is_staff:
            raise HTTPException(status_code=403, detail="operation forbidden")
        return await _transition(
            ticket_id, WorkflowAction.SUBMIT_REVIEW, auth, csrf_token,
            idempotency_key,
        )

    @app.post("/api/tickets/{ticket_id}/accept")
    async def accept_ticket(
        ticket_id: int,
        _payload: EmptyInput,
        auth: Authenticated = Depends(authenticated),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
        idempotency_key: UUID = Header(alias="Idempotency-Key"),
    ):
        return await _transition(
            ticket_id, WorkflowAction.ACCEPT, auth, csrf_token, idempotency_key,
        )

    @app.post("/api/tickets/{ticket_id}/return")
    async def return_ticket(
        ticket_id: int,
        payload: ReasonInput,
        auth: Authenticated = Depends(authenticated),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
        idempotency_key: UUID = Header(alias="Idempotency-Key"),
    ):
        return await _transition(
            ticket_id, WorkflowAction.RETURN, auth, csrf_token,
            idempotency_key, payload.reason,
        )

    @app.post("/api/tickets/{ticket_id}/complete")
    async def complete_destination(
        ticket_id: int,
        _payload: EmptyInput,
        auth: Authenticated = Depends(authenticated),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
        idempotency_key: UUID = Header(alias="Idempotency-Key"),
    ):
        if not auth.actor.is_staff:
            raise HTTPException(status_code=403, detail="operation forbidden")
        return await _transition(
            ticket_id, WorkflowAction.COMPLETE_DESTINATION, auth, csrf_token,
            idempotency_key,
        )

    @app.post("/api/tickets/{ticket_id}/reopen")
    async def reopen_ticket(
        ticket_id: int,
        payload: ReasonInput,
        auth: Authenticated = Depends(authenticated),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
        idempotency_key: UUID = Header(alias="Idempotency-Key"),
    ):
        return await _transition(
            ticket_id, WorkflowAction.REOPEN, auth, csrf_token,
            idempotency_key, payload.reason,
        )

    async def _transition(
        ticket_id: int,
        action: WorkflowAction,
        auth: Authenticated,
        csrf_token: str | None,
        idempotency_key: UUID,
        reason: str | None = None,
    ):
        _require_positive_ticket_id(ticket_id)
        _require_csrf(auth, csrf_token)
        result = await mutations.transition(
            TransitionCommand(
                profile_key=settings.profile_key,
                actor=auth.actor,
                ticket_id=ticket_id,
                idempotency_key=idempotency_key,
                action=action,
                reason=reason,
            )
        )
        return _validate_output(MutationResponse, asdict(result))

    @app.get("/api/advertisers")
    async def advertisers(
        auth: Authenticated = Depends(authenticated),
        mode: Annotated[str, Query(pattern="^(popular|search|page)$")] = "popular",
        q: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    ):
        if mode == "search" and q is None:
            raise HTTPException(status_code=422, detail="q is required for search")
        if mode != "search" and q is not None:
            raise HTTPException(status_code=422, detail="q is only valid for search")
        result = await gateway.list_advertisers(
            profile_key=settings.profile_key,
            actor=auth.actor,
            query=AdvertiserQuery(mode=mode, query=q, page=page, page_size=page_size),  # type: ignore[arg-type]
        )
        return _validate_output(Page[AdvertiserSummary], result)

    @app.get("/api/postbacks")
    async def postbacks(auth: Authenticated = Depends(authenticated)):
        result = await gateway.list_postbacks(
            profile_key=settings.profile_key, actor=auth.actor
        )
        return _validate_output(TypeAdapter(list[PostbackSummary]), result)

    @app.get("/api/postbacks/{key}")
    async def postback(key: str, auth: Authenticated = Depends(authenticated)):
        if not key or len(key) > 128 or "/" in key or "\\" in key or ".." in key:
            raise HTTPException(status_code=404, detail="resource not found")
        result = await gateway.get_postback(
            profile_key=settings.profile_key, actor=auth.actor, key=key
        )
        return _validate_output(PostbackDetail, result)

    @app.get("/api/help")
    async def help_content(auth: Authenticated = Depends(authenticated)):
        audience = "staff" if auth.actor.is_staff else "client"
        filename = "FAQ_TECHNICIAN.md" if auth.actor.is_staff else "FAQ_CLIENT.md"
        try:
            markdown = (help_root / "faq" / settings.profile_key / filename).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise BackendUnavailable("help content is unavailable") from exc
        return _validate_output(
            HelpContent,
            {"audience": audience, "markdown": markdown},
        )

    return app


def _error_response(status_code: int, detail: str):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status_code, content={"detail": detail})


def _validate_output(model, value):  # type: ignore[no-untyped-def]
    try:
        if isinstance(model, TypeAdapter):
            return model.validate_python(value)
        return model.model_validate(value)
    except ValidationError as exc:
        raise BackendUnavailable("invalid canonical gateway output") from exc


def _require_csrf(auth: Authenticated, candidate: str | None) -> None:
    if not csrf_matches(auth.session, candidate):
        raise HTTPException(status_code=403, detail="invalid CSRF token")


def _require_positive_ticket_id(ticket_id: int) -> None:
    if ticket_id <= 0:
        raise HTTPException(status_code=404, detail="resource not found")


def _validate_draft_shape(profile_key: str, payload: DraftSaveInput) -> None:
    if payload.type is None:
        if (
            payload.advertiser is not None
            or payload.cap is not None
            or payload.cap_mode is not None
            or payload.details
            or payload.important_comment
        ):
            raise HTTPException(
                status_code=422,
                detail="select ticket type before destination fields",
            )
        return
    if payload.type == "general":
        if (
            payload.advertiser is not None
            or payload.cap is not None
            or payload.cap_mode is not None
            or payload.details
            or payload.important_comment
        ):
            raise HTTPException(
                status_code=422,
                detail="general draft cannot contain destination fields",
            )
        return
    if payload.cap is not None and payload.cap_mode is not None:
        _validate_cap_pair(payload.cap, payload.cap_mode)
    if profile_key == "kalisto":
        if payload.important_comment:
            raise HTTPException(
                status_code=422,
                detail="important comment is only available in Mephisto",
            )
        if payload.advertiser is not None and payload.advertiser.kind != "catalog":
            raise HTTPException(
                status_code=422,
                detail="Kalisto requires a catalog advertiser",
            )
    elif payload.details is not None:
        raise HTTPException(
            status_code=422,
            detail="structured Destination fields are only available in Kalisto",
        )


def _validate_destination_submission(profile_key: str, payload: object) -> None:
    if not isinstance(payload, (DestinationTicketCreate, DestinationDraftSubmitInput)):
        return
    _validate_cap_pair(payload.cap, payload.cap_mode)
    if profile_key == "kalisto":
        if payload.important_comment:
            raise HTTPException(
                status_code=422,
                detail="important comment is only available in Mephisto",
            )
        if payload.advertiser.kind != "catalog":
            raise HTTPException(
                status_code=422,
                detail="Kalisto requires a catalog advertiser",
            )
        if payload.details is None:
            raise HTTPException(
                status_code=422,
                detail="Kalisto requires complete structured Destination fields",
            )
        return
    if payload.details is not None:
        raise HTTPException(
            status_code=422,
            detail="structured Destination fields are only available in Kalisto",
        )
    if not payload.body.strip():
        raise HTTPException(
            status_code=422,
            detail="Mephisto requires the source offer body",
        )


def _validate_cap_pair(cap: object, cap_mode: object) -> None:
    if cap_mode == "unlim" and cap == "uncap":
        return
    if (
        cap_mode in {"daily", "monthly", "lifetime"}
        and isinstance(cap, int)
        and not isinstance(cap, bool)
        and 1 <= cap <= 1_000_000
    ):
        return
    raise HTTPException(
        status_code=422,
        detail="cap value does not match cap mode",
    )


def _command_details(details: object) -> DestinationRequestDetails | None:
    if details is None:
        return None
    return DestinationRequestDetails(
        naming=details.naming,
        geo=details.geo,
        rate=details.rate,
        currency=details.currency,
        landing_url=details.landing_url,
        clickid_subid=details.clickid_subid,
        additional=details.additional,
    )

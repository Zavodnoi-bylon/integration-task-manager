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
from .dto import (
    AdvertiserSummary,
    CreateTicketInput,
    ClientTicketDetail,
    DestinationTicketCreate,
    DraftMutationResponse,
    DraftSaveInput,
    DraftSubmitInput,
    EmptyInput,
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
    lifespan: Any = None,
) -> FastAPI:
    app = FastAPI(
        title="Destination Mini App API",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    web_root = Path(__file__).with_name("web")
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
            "capabilities": {
                "team_queue": actor.is_staff,
                "internal_history": actor.is_staff,
                "submit_review": actor.is_staff,
                "complete_destination": actor.is_staff,
            },
        }

    @app.get("/api/tickets")
    async def tickets(
        auth: Authenticated = Depends(authenticated),
        scope: Annotated[str, Query(pattern="^(mine|team)$")] = "mine",
        status: Annotated[str | None, Query(pattern="^(open|review|completed)$")] = None,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    ):
        if scope == "team" and not auth.actor.is_staff:
            raise HTTPException(status_code=403, detail="team scope forbidden")
        result = await gateway.list_tickets(
            profile_key=settings.profile_key,
            actor=auth.actor,
            query=TicketQuery(scope=scope, status=status, page=page, page_size=page_size),  # type: ignore[arg-type]
        )
        return _validate_output(Page[TicketSummary], result)

    @app.get("/api/drafts")
    async def drafts(auth: Authenticated = Depends(authenticated)):
        result = await gateway.list_drafts(
            profile_key=settings.profile_key, actor=auth.actor
        )
        return _validate_output(TypeAdapter(list[TicketDraftSummary]), result)

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
        await gateway.get_draft(
            profile_key=settings.profile_key,
            actor=auth.actor,
            draft_id=draft_id,
        )
        saved = _validate_output(
            TicketDraftDetail,
            await gateway.save_draft(
                profile_key=settings.profile_key,
                actor=auth.actor,
                draft_id=draft_id,
                payload=DraftSaveInput(
                    advertiser=payload.advertiser,
                    cap=payload.cap,
                    body=payload.body,
                    client_comment=payload.client_comment,
                ),
            ),
        )
        advertiser = (
            CatalogAdvertiser("catalog", payload.advertiser.key)
            if payload.advertiser.kind == "catalog"
            else NewAdvertiser("new", payload.advertiser.name)
        )
        source_body = saved.edited_body
        if saved.client_comment:
            source_body = f"{saved.client_comment}\n\n{source_body}"
        result = await mutations.create_ticket(
            CreateTicketCommand(
                profile_key=settings.profile_key,
                actor=auth.actor,
                idempotency_key=uuid5(
                    NAMESPACE_URL,
                    f"ticket-draft:{settings.profile_key}:{draft_id}",
                ),
                ticket_type="destination",
                body=source_body,
                advertiser=advertiser,
                cap=payload.cap,
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
        advertiser = None
        cap = None
        if isinstance(payload, DestinationTicketCreate):
            cap = payload.cap
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

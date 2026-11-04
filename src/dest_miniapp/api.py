from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from .auth import InitDataError, verify_init_data
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


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    init_data: str = Field(min_length=1, max_length=16_384)


@dataclass(frozen=True)
class Authenticated:
    actor: Actor
    session: SessionRecord


def create_app(
    *, settings: Settings, gateway: MiniAppGateway, sessions: SessionStore
) -> FastAPI:
    app = FastAPI(title="Destination Mini App API", docs_url=None, redoc_url=None)
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
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response

    async def authenticated(
        session_id: str | None = Cookie(default=None, alias=settings.session_cookie_name),
    ) -> Authenticated:
        if not session_id:
            raise HTTPException(status_code=401, detail="authentication required")
        record = await sessions.get(session_id)
        if record is None or record.profile_key != settings.profile_key:
            raise HTTPException(status_code=401, detail="session expired")
        actor = await gateway.resolve_actor(
            profile_key=settings.profile_key, user_id=record.user_id
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
            user_id=actor.user_id,
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
                "complete": actor.is_staff,
            },
        }

    @app.get("/api/tickets")
    async def tickets(
        auth: Authenticated = Depends(authenticated),
        scope: Annotated[str, Query(pattern="^(mine|team)$")] = "mine",
        status: Annotated[str | None, Query(pattern="^(open|completed)$")] = None,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    ):
        if scope == "team" and not auth.actor.is_staff:
            raise HTTPException(status_code=403, detail="team scope forbidden")
        return await gateway.list_tickets(
            profile_key=settings.profile_key,
            actor=auth.actor,
            query=TicketQuery(scope=scope, status=status, page=page, page_size=page_size),  # type: ignore[arg-type]
        )

    @app.get("/api/tickets/{ticket_id}")
    async def ticket(ticket_id: str, auth: Authenticated = Depends(authenticated)):
        if not ticket_id or len(ticket_id) > 64:
            raise HTTPException(status_code=404, detail="resource not found")
        return await gateway.get_ticket(
            profile_key=settings.profile_key, actor=auth.actor, ticket_id=ticket_id
        )

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
        return await gateway.list_advertisers(
            profile_key=settings.profile_key,
            actor=auth.actor,
            query=AdvertiserQuery(mode=mode, query=q, page=page, page_size=page_size),  # type: ignore[arg-type]
        )

    @app.get("/api/postbacks")
    async def postbacks(auth: Authenticated = Depends(authenticated)):
        return await gateway.list_postbacks(
            profile_key=settings.profile_key, actor=auth.actor
        )

    @app.get("/api/postbacks/{key}")
    async def postback(key: str, auth: Authenticated = Depends(authenticated)):
        if not key or len(key) > 128 or "/" in key or "\\" in key or ".." in key:
            raise HTTPException(status_code=404, detail="resource not found")
        return await gateway.get_postback(
            profile_key=settings.profile_key, actor=auth.actor, key=key
        )

    return app


def _error_response(status_code: int, detail: str):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status_code, content={"detail": detail})

"""Telegram-authenticated, read-only Mini App for cross-profile ticket analytics."""
from __future__ import annotations

import os
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from activity.repository import RepositoryError
from activity.telegram_webapp_auth import AuthenticationError, AuthorizationError, TelegramWebAppAuth
from activity.ticket_analytics import TicketAnalyticsRepository


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "lead_static"
app = FastAPI(title="Ticket Analytics Mini App", docs_url=None, redoc_url=None, openapi_url=None)
_slots = threading.BoundedSemaphore(4)


def _allowed_users() -> frozenset[int]:
    try:
        values = frozenset(int(item.strip()) for item in os.environ.get("LEAD_ALLOWED_TELEGRAM_IDS", "").split(",") if item.strip())
    except ValueError:
        raise RuntimeError("Invalid LEAD_ALLOWED_TELEGRAM_IDS") from None
    return values


def auth() -> TelegramWebAppAuth:
    return TelegramWebAppAuth(os.environ.get("LEAD_BOT_TOKEN", ""), _allowed_users())


def authorize(authorization: str = Header(default="")) -> int:
    if not authorization.startswith("tma "):
        raise AuthenticationError("Telegram authentication required")
    return auth().verify(authorization[4:])


def repository() -> TicketAnalyticsRepository:
    return TicketAnalyticsRepository()


@app.exception_handler(AuthenticationError)
async def authentication_error(_request, _exc):
    return JSONResponse(status_code=401, content={"error": "Откройте аналитику из меню Telegram-бота."})


@app.exception_handler(AuthorizationError)
async def authorization_error(_request, _exc):
    return JSONResponse(status_code=403, content={"error": "Доступ разрешён только техлидам."})


@app.exception_handler(RepositoryError)
async def repository_error(_request, _exc):
    return JSONResponse(status_code=503, content={"error": "База аналитики временно недоступна."})


@app.exception_handler(StarletteHTTPException)
async def http_error(_request, exc):
    return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
        "frame-ancestors https://web.telegram.org https://*.telegram.org"
    )
    return response


@app.get("/health")
def health(repo=Depends(repository)):
    repo.health()
    return {"ok": 1, "mode": "read-only"}


@app.get("/api/summary")
def summary(period: int = Query(30), profile: str = Query("all"), _user=Depends(authorize), repo=Depends(repository)):
    if not _slots.acquire(timeout=0.1):
        raise HTTPException(503, "Сервис занят. Повторите запрос.")
    try:
        return repo.summary(period, profile)
    except ValueError:
        raise HTTPException(422, "Некорректный фильтр.") from None
    finally:
        _slots.release()


@app.get("/api/tickets")
def tickets(status: str = Query("open"), profile: str = Query("all"), page: int = Query(1, ge=1, le=100000),
            _user=Depends(authorize), repo=Depends(repository)):
    try:
        return repo.tickets(status, profile, page)
    except ValueError:
        raise HTTPException(422, "Некорректный фильтр.") from None


@app.get("/api/tickets/{ticket_id}")
def ticket(ticket_id: int, _user=Depends(authorize), repo=Depends(repository)):
    value = repo.ticket(ticket_id)
    if value is None:
        raise HTTPException(404, "Тикет не найден.")
    return value


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/{asset_name}")
def asset(asset_name: str):
    assets = {"app.js": "text/javascript", "styles.css": "text/css"}
    if asset_name not in assets:
        raise HTTPException(404, "Не найдено.")
    return FileResponse(STATIC / asset_name, media_type=assets[asset_name])

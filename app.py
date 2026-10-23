"""Read-only HTTP API and confined frontend serving for Team Activity Explorer."""
from __future__ import annotations

import csv
import io
import os
import threading
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from activity import domain, access
from datetime import datetime
from zoneinfo import ZoneInfo
from activity.demo import DemoRepository
from activity.repository import PostgresRepository, RepositoryError, positive_setting

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
app = FastAPI(title="Team Activity Explorer", docs_url=None, redoc_url=None, openapi_url=None)
_snapshot_slots = threading.BoundedSemaphore(4)


def repository():
    return DemoRepository() if os.environ.get("ACTIVITY_DEMO") == "1" else PostgresRepository()


def snapshot(repo=Depends(repository)) -> dict:
    if not _snapshot_slots.acquire(timeout=0.1):
        raise HTTPException(503, "The reader is busy. Retry shortly.")
    try:
        return repo.snapshot()
    finally:
        _snapshot_slots.release()


@app.exception_handler(RepositoryError)
async def repository_error(_request, exc: RepositoryError):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.code, "detail": exc.message},
                        headers={"Cache-Control": "no-store"})


@app.exception_handler(domain.DomainValidationError)
async def domain_error(_request, exc: domain.DomainValidationError):
    return JSONResponse(status_code=422, content={"error": "validation_error", "detail": str(exc)},
                        headers={"Cache-Control": "no-store"})


@app.exception_handler(RequestValidationError)
async def query_error(_request, _exc):
    # Do not reflect arbitrary request strings or source data into error pages.
    return JSONResponse(status_code=422, content={"error": "invalid_query", "detail": "Invalid or missing query parameters."})


@app.exception_handler(StarletteHTTPException)
async def http_error(_request, exc):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)


@app.middleware("http")
async def no_cache_api(request, call_next):
    if request.url.path.startswith("/api/") and request.url.path != "/api/health":
        try:
            request.state.person = access.principal(request.headers)
            access.authorize(request.state.person, request.url.path, request.method)
        except HTTPException as exc:
            body = exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}
            return JSONResponse(status_code=exc.status_code, content=body, headers={"Cache-Control": "no-store"})
    response = await call_next(request)
    if request.url.path == "/api" or request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/api/access")
def dashboard_access(request: Request):
    return {**request.state.person, "today": datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()}


@app.get("/api/layout")
def dashboard_layout(request: Request):
    result = access.read_layout()
    return {**result, "layout": {"version": 1, "widgets": [w for w in result["layout"]["widgets"] if w["id"] in request.state.person["panels"]]}}


@app.put("/api/layout")
async def update_layout(request: Request):
    # Cross-origin forms cannot supply this header or JSON without a preflight.
    # No CORS middleware is enabled. Basic-auth cookies alone are insufficient.
    if request.headers.get("x-activity-action") != "save-layout" or request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(403, "Invalid layout request.")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site writes are not allowed.")
    if len(await request.body()) > 8192:
        raise HTTPException(413, "Layout request too large.")
    try:
        value = await request.json()
    except ValueError:
        raise HTTPException(422, "Invalid JSON.") from None
    return access.save_layout(value)


@app.get("/api/schedule")
def dashboard_schedule():
    return {"people": access.TEAM_SCHEDULE}


def date_filters(
    first: str = Query(..., alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    last: str = Query(..., alias="to", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    category: str | None = Query(None, max_length=64),
    client: str | None = Query(None, max_length=500),
    assignee: str | None = Query(None, max_length=500),
    q: str | None = Query(None, max_length=500),
) -> dict:
    domain.date_window(first, last)
    if category and category not in domain.CATEGORIES:
        raise domain.DomainValidationError("Unknown category filter.")
    return {"first": first, "last": last,
            "filters": {"category": category, "client": client, "assignee": assignee, "q": q}}


@app.get("/api/health")
def health(repo=Depends(repository)):
    repo.health()
    return {"ok": 1, "mode": repo.mode}


@app.get("/api/meta")
def meta(request: Request, dataset=Depends(snapshot)):
    result = domain.metadata(dataset)
    if "registry" not in request.state.person["panels"]:
        result = {k: result[k] for k in ("timezone", "today", "default_from", "default_to", "demo", "coverage") if k in result}
    return result


@app.get("/api/report")
def report(request: Request, bucket: Literal["day", "week"] = "day", query=Depends(date_filters), dataset=Depends(snapshot)):
    panels = request.state.person["panels"]
    if "registry" not in panels and any(query["filters"].values()):
        raise HTTPException(403, "Detailed filters require registry access.")
    return access.restrict_report(domain.report(dataset, bucket=bucket, **query), panels)


@app.get("/api/tasks")
def tasks(
    status: Literal["all", "open", "completed", "waiting"] = "all",
    metric: Literal["all", "incoming", "completed", "backlog"] = "all",
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    query=Depends(date_filters), dataset=Depends(snapshot),
):
    return domain.task_page(dataset, status=status, metric=metric, page=page, page_size=page_size, **query)


@app.get("/api/proposals")
def proposals(dataset=Depends(snapshot)):
    rows = domain.pending_proposals(dataset)
    return {"rows": rows, "total": len(rows), "demo": bool(dataset.get("demo", False))}


CSV_FIELDS = ("task_id", "title", "category", "client", "assignee", "status", "requested_at",
              "completed_at", "resolution_hours", "evidence_count")


def csv_cell(value):
    if value is None:
        return ""
    if isinstance(value, str):
        # Spreadsheet programs can ignore leading whitespace before formulas.
        # Escape strings only; measured numeric values remain numeric CSV cells.
        stripped = value.lstrip(" \t\r\n\v\f")
        if (stripped and stripped[0] in "=+-@") or value[:1] in ("\t", "\r", "\n"):
            return "'" + value
    return value


def encode_csv(rows: list[dict], max_rows: int, max_bytes: int) -> bytes:
    if len(rows) > max_rows:
        raise HTTPException(413, "CSV row limit exceeded. No partial export was returned.")
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(CSV_FIELDS)
    # Complete the export before sending any bytes, and enforce the byte limit
    # incrementally so large source text cannot create an unbounded output.
    parts = ["\ufeff".encode("utf-8"), output.getvalue().encode("utf-8")]
    size = sum(map(len, parts))
    if size > max_bytes:
        raise HTTPException(413, "CSV byte limit exceeded. No partial export was returned.")
    for row in rows:
        output.seek(0)
        output.truncate(0)
        writer.writerow([csv_cell(row.get(field)) for field in CSV_FIELDS])
        part = output.getvalue().encode("utf-8")
        size += len(part)
        if size > max_bytes:
            raise HTTPException(413, "CSV byte limit exceeded. No partial export was returned.")
        parts.append(part)
    return b"".join(parts)


@app.get("/api/export/tasks")
def export_tasks(
    status: Literal["all", "open", "completed", "waiting"] = "all",
    metric: Literal["all", "incoming", "completed", "backlog"] = "all",
    query=Depends(date_filters), dataset=Depends(snapshot),
):
    rows = domain.task_rows(dataset, status=status, metric=metric, **query)
    payload = encode_csv(rows, positive_setting("ACTIVITY_MAX_EXPORT_ROWS", 100_000, 1_000_000),
                         positive_setting("ACTIVITY_MAX_EXPORT_BYTES", 16 * 1024 * 1024, 256 * 1024 * 1024))
    name = f"team-activity-{query['first']}-{query['last']}.csv"
    return Response(payload, media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{name}"',
        "X-Export-Count": str(len(rows)), "Cache-Control": "no-store",
    })


@app.get("/api/tasks/{task_id:path}")
def task(task_id: str, dataset=Depends(snapshot)):
    detail = domain.task_detail(dataset, task_id)
    if detail is None:
        raise HTTPException(404, "Task not found.")
    return detail


@app.get("/api")
@app.get("/api/{path:path}")
def unknown_api(path: str = ""):
    raise HTTPException(404, "Unknown API route.")


@app.get("/{path:path}")
def frontend(path: str):
    root = DIST.resolve()
    candidate = (root / path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(404, "File not found.") from None
    if candidate.is_file():
        return FileResponse(candidate)
    index = root / "index.html"
    if not index.is_file():
        raise HTTPException(503, "Frontend is not built. Build the local frontend or use the Vite preview.")
    return FileResponse(index, headers={"Cache-Control": "no-cache"})

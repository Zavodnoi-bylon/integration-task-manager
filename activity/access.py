"""Proxy-authenticated dashboard roles and persistent shared presentation settings.

Task data remains read-only. Only the administrator can write UI layout settings.
"""
import hmac
import json
import os
from pathlib import Path
import tempfile
import threading

from fastapi import HTTPException

PANELS = ("metrics", "flow", "backlog", "categories", "registry", "calendar")
SIZES = ((12, 6, 140), (7, 4, 300), (5, 4, 300), (12, 4, 140), (12, 6, 320), (3, 3, 372))
_lock = threading.Lock()


def principal(headers):
    token = os.environ.get("ACTIVITY_PROXY_TOKEN", "")
    if os.environ.get("ACTIVITY_DEMO") == "1" and not token:
        return {"user": "demo", "role": "admin", "panels": list(PANELS), "can_edit_layout": True}
    if len(token) < 32:
        raise HTTPException(503, {"error": "configuration_error"})
    supplied = headers.get("x-activity-proxy-token", "")
    if not hmac.compare_digest(supplied.encode(), token.encode()):
        raise HTTPException(401, "Authentication required.")
    try:
        roles = json.loads(os.environ.get("ACTIVITY_AUTH_ROLES", "{}"))
        user = headers.get("x-activity-user", "")
        role = roles.get(user)
        lead_panels = json.loads(os.environ.get("ACTIVITY_TECH_LEAD_PANELS", "[]"))
        if not isinstance(lead_panels, list) or any(p not in PANELS for p in lead_panels):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(503, "Access configuration unavailable.") from None
    if role not in ("admin", "viewer", "tech_lead"):
        raise HTTPException(403, "Access not assigned.")
    panels = list(PANELS) if role != "tech_lead" else [p for p in PANELS if p in lead_panels]
    return {"user": user, "role": role, "panels": panels, "can_edit_layout": role == "admin",
            "can_edit_status": role == "admin" and os.environ.get('ACTIVITY_STATUS_WRITES') == '1'}


def authorize(person, path, method):
    panels = person["panels"]
    if path == "/api/layout":
        if method == "PUT" and not person["can_edit_layout"]:
            raise HTTPException(403, "Only the administrator can change the shared layout.")
    elif path == "/api/schedule":
        if "calendar" not in panels:
            raise HTTPException(403, "Panel access denied.")
    elif path == "/api/meta":
        if not any(p in panels for p in PANELS[:-1]):
            raise HTTPException(403, "Report access denied.")
    elif path == "/api/report":
        if not any(p in panels for p in PANELS[:4]):
            raise HTTPException(403, "Report access denied.")
    elif path.startswith(("/api/tasks", "/api/proposals", "/api/export")):
        if method == 'POST' and path.startswith('/api/tasks/') and path.endswith('/status') and person['role'] != 'admin':
            raise HTTPException(403, 'Only the owner can change task status.')
        if "registry" not in panels:
            raise HTTPException(403, "Registry access denied.")


def normalize_layout(value):
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("widgets"), list):
        raise HTTPException(422, "Invalid layout.")
    rows = value["widgets"]
    if (len(rows) != len(PANELS) or any(not isinstance(r, dict) or not isinstance(r.get("id"), str) for r in rows)
            or {r["id"] for r in rows} != set(PANELS)):
        raise HTTPException(422, "Layout must contain each panel exactly once.")
    result = []
    for row in rows:
        span_default, span_min, height_min = SIZES[PANELS.index(row["id"])]
        span, height = row.get("span"), row.get("height")
        if type(span) is not int or not span_min <= span <= 12:
            raise HTTPException(422, "Invalid panel width.")
        if height is not None and (type(height) is not int or not height_min <= height <= 1200):
            raise HTTPException(422, "Invalid panel height.")
        item = {"id": row["id"], "span": span, "height": height}
        if "column" in row or "row" in row:
            column, track = row.get("column"), row.get("row")
            if type(column) is not int or not 1 <= column <= 13-span or type(track) is not int or not 0 <= track <= 10000:
                raise HTTPException(422, "Invalid panel position.")
            item.update(column=column, row=track)
        result.append(item)
    return {"version": 1, "widgets": result}


def default_layout():
    return {"version": 1, "widgets": [{"id": p, "span": size[0], "height": None} for p, size in zip(PANELS, SIZES)]}


def layout_path():
    folder = os.environ.get("ACTIVITY_UI_STATE_DIR")
    if not folder:
        raise HTTPException(503, "Shared layout storage is not configured.")
    return Path(folder) / "layout.json"


def read_layout():
    path = layout_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        layout = normalize_layout(value["layout"])
        revision = value["revision"]
        if type(revision) is not int or revision < 0:
            raise ValueError()
        return {"layout": layout, "revision": revision}
    except FileNotFoundError:
        return {"layout": default_layout(), "revision": 0}
    except (OSError, ValueError, KeyError, TypeError, HTTPException):
        raise HTTPException(503, "Shared layout storage is unavailable.") from None


def save_layout(value):
    if not isinstance(value, dict) or type(value.get("revision")) is not int:
        raise HTTPException(422, "Layout revision is required.")
    layout = normalize_layout(value.get("layout"))
    with _lock:
        current = read_layout()
        if value["revision"] != current["revision"]:
            raise HTTPException(409, "Раскладка уже изменена в другом окне. Обновите страницу.")
        result = {"layout": layout, "revision": current["revision"] + 1}
        path = layout_path()
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
                temporary = handle.name
                json.dump(result, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError:
            raise HTTPException(503, "Unable to save shared layout.") from None
        finally:
            if temporary and Path(temporary).exists():
                Path(temporary).unlink()
        return result


def restrict_report(result, panels):
    if all(p in panels for p in PANELS[:5]):
        return result
    summary = dict(result["summary"]) if "metrics" in panels else {}
    if "registry" not in panels:
        summary.pop("pending_proposals", None)
    keys = {"period_start", "period_end", "label", "partial"}
    if "flow" in panels:
        keys.update(("incoming", "completed"))
    if "backlog" in panels:
        keys.add("observed_backlog")
    # A whitelist prevents future report additions silently widening permissions.
    return {**{k: result[k] for k in ("from", "to", "bucket", "timezone", "demo", "empty", "coverage") if k in result},
            "summary": summary,
            "series": [{k: v for k, v in row.items() if k in keys} for row in result["series"]] if any(p in panels for p in ("flow", "backlog")) else [],
            "breakdown": result["breakdown"] if "categories" in panels else []}


# Confirmed anchors: first work day of a repeating 2-on/2-off cycle.
TEAM_SCHEDULE = [
    {"name": "AIex911", "anchor": "2026-10-23", "color": "alex", "initials": "AI"},
    {"name": "stanedgart", "anchor": "2026-10-22", "color": "stan", "initials": "st"},
    {"name": "Custodes112", "anchor": "2026-10-25", "color": "custodes", "initials": "Cu"},
    {"name": "Dmee911", "anchor": "2026-10-24", "color": "dmee", "initials": "Dm"},
]

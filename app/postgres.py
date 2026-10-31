"""PostgreSQL adapter for the canonical ticketing schema."""
from __future__ import annotations

import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

import config
from app.domain.constants import STATUS_COMPLETED, STATUS_OPEN


MOSCOW_TZ = ZoneInfo("Europe/Moscow")
STATUS_TO_CODE = {STATUS_OPEN: "open", STATUS_COMPLETED: "completed"}
CODE_TO_STATUS = {value: key for key, value in STATUS_TO_CODE.items()}
_pool: ConnectionPool | None = None


def _conninfo() -> str:
    if not config.TICKETING_DB_PASSWORD:
        raise RuntimeError("TICKETING_DB_PASSWORD is required for PostgreSQL")
    return make_conninfo(
        host=config.TICKETING_DB_HOST,
        port=config.TICKETING_DB_PORT,
        dbname=config.TICKETING_DB_NAME,
        user=config.TICKETING_DB_USER,
        password=config.TICKETING_DB_PASSWORD,
        application_name=f"integration-task-manager-{config.BOT_KEY}",
    )


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=_conninfo(),
            min_size=1,
            max_size=4,
            timeout=10,
            kwargs={"row_factory": dict_row},
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def parse_to_moscow_naive(
    dt_input: datetime.datetime | str | None,
) -> datetime.datetime:
    if dt_input is None:
        return datetime.datetime.now(MOSCOW_TZ).replace(tzinfo=None)
    if isinstance(dt_input, str):
        parsed = datetime.datetime.fromisoformat(dt_input.replace("Z", "+00:00"))
    else:
        parsed = dt_input
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MOSCOW_TZ)
    return parsed.astimezone(MOSCOW_TZ).replace(tzinfo=None)


def _aware(dt_input: datetime.datetime | str | None) -> datetime.datetime:
    value = parse_to_moscow_naive(dt_input)
    return value.replace(tzinfo=MOSCOW_TZ)


def moscow_timestamp(dt_input: datetime.datetime | str | None = None) -> str:
    return parse_to_moscow_naive(dt_input).strftime("%Y-%m-%d %H:%M:%S")


def _format_timestamp(value: datetime.datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S")


def create_tables() -> None:
    with _get_pool().connection() as conn:
        exists = conn.execute(
            "SELECT to_regclass('ticketing.tickets') AS name"
        ).fetchone()["name"]
        if exists is None:
            raise RuntimeError("PostgreSQL ticketing schema is not installed")


def ensure_user(tg_id: int, data_reg: str, user_name: str,
                role: str, bot_key: str) -> None:
    username = user_name[1:] if user_name.startswith("@") else None
    with _get_pool().connection() as conn:
        conn.execute(
            "SELECT ticketing.register_member(%s,%s,%s,%s,%s,%s)",
            (bot_key, tg_id, username, user_name, role, _aware(data_reg)),
        )


def sync_user_roles(bot_key: str, admin_ids: frozenset[int],
                    technician_ids: frozenset[int]) -> None:
    # An account explicitly configured as an administrator keeps the highest role
    # even if it is accidentally also present in the technician list.
    desired = {**{value: "technician" for value in technician_ids},
               **{value: "admin" for value in admin_ids}}
    with _get_pool().connection() as conn:
        if desired:
            rows = conn.execute(
                "SELECT telegram_user_id,username,display_name,last_seen_at "
                "FROM ticketing.telegram_users WHERE telegram_user_id=ANY(%s)",
                (list(desired),),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "SELECT ticketing.register_member(%s,%s,%s,%s,%s,%s)",
                    (bot_key, row["telegram_user_id"], row["username"],
                     row["display_name"], desired[row["telegram_user_id"]],
                     row["last_seen_at"]),
                )
        conn.execute(
            "SELECT ticketing.sync_configured_roles(%s,%s,%s)",
            (bot_key, list(admin_ids), list(technician_ids)),
        )


def get_user_by_id(tg_id: int, bot_key: str | None = None) -> dict | None:
    query = (
        "SELECT u.telegram_user_id AS tg_id,u.first_seen_at AS data_reg,"
        "u.display_name AS user_name,m.role,m.profile_key AS bot_key "
        "FROM ticketing.telegram_users u JOIN ticketing.memberships m USING(user_id) "
        "WHERE u.telegram_user_id=%s"
    )
    params: list[object] = [tg_id]
    if bot_key is not None:
        query += " AND m.profile_key=%s"
        params.append(bot_key)
    query += " ORDER BY m.profile_key LIMIT 1"
    with _get_pool().connection() as conn:
        row = conn.execute(query, params).fetchone()
    if not row:
        return None
    return {
        "tg_id": row["tg_id"],
        "data_reg": _format_timestamp(row["data_reg"]),
        "organization": "",
        "organization_adress": "",
        "organization_inn": "",
        "organization_phone": "",
        "history_ticket": "",
        "data_ticket": "",
        "user_name": row["user_name"],
        "role": row["role"],
        "bot_key": row["bot_key"],
    }


def update_user_field(tg_id: int, field_name: str, value: str,
                      bot_key: str | None = None) -> None:
    # Legacy denormalized fields are intentionally absent from PostgreSQL.
    if field_name not in {"history_ticket", "data_ticket"}:
        raise ValueError(f"Unsupported PostgreSQL user field: {field_name}")


def add_ticket(tg_id_ticket: int, organization: str, addres_ticket: str,
               message_ticket: str, time_ticket: str, state_ticket: str,
               ticket_comm: str, assigned_to: int | None = None,
               bot_key: str = "default", request_key: str | None = None,
               ticket_type: str = "general") -> int:
    if state_ticket != STATUS_OPEN:
        raise ValueError("New PostgreSQL tickets must start open")
    request_key = request_key or f"generated:{uuid4()}"
    with _get_pool().connection() as conn:
        row = conn.execute(
            "SELECT ticketing.create_ticket(%s,%s,%s,%s,%s,%s,'telegram',NULL,%s,%s)",
            (bot_key, tg_id_ticket, assigned_to, message_ticket,
             _aware(time_ticket), request_key, Jsonb({}), ticket_type),
        ).fetchone()
    return int(next(iter(row.values())))


_TICKET_SELECT = """
SELECT f.ticket_id,f.requester_telegram_id,f.description,f.created_at,
       f.status_code,f.assigned_telegram_id,f.updated_at,f.closed_at,f.profile_key,
       f.ticket_type,
       (SELECT m.body FROM ticketing.ticket_messages m
        WHERE m.ticket_id=f.ticket_id AND m.visibility='internal'
        ORDER BY m.sent_at DESC,m.message_id DESC LIMIT 1) AS latest_comment
FROM reporting.ticket_facts f
"""


def _legacy_ticket(row: dict) -> tuple:
    return (
        row["ticket_id"], row["requester_telegram_id"], "", "",
        row["description"], _format_timestamp(row["created_at"]),
        CODE_TO_STATUS.get(row["status_code"], row["status_code"]),
        row["latest_comment"] or "", row["assigned_telegram_id"],
        _format_timestamp(row["updated_at"]), _format_timestamp(row["closed_at"]),
        row["profile_key"], row["ticket_type"],
    )


def get_ticket_info(ticket_id: int, bot_key: str | None = None) -> tuple | None:
    query = _TICKET_SELECT + " WHERE f.ticket_id=%s"
    params: list[object] = [ticket_id]
    if bot_key is not None:
        query += " AND f.profile_key=%s"
        params.append(bot_key)
    with _get_pool().connection() as conn:
        row = conn.execute(query, params).fetchone()
    return _legacy_ticket(row) if row else None


def get_ticket_count(tg_id: int | None, status: str,
                     bot_key: str | None = None) -> int:
    query = "SELECT count(*) AS count FROM reporting.ticket_facts WHERE status_code=%s"
    params: list[object] = [STATUS_TO_CODE[status]]
    if tg_id is not None:
        query += " AND requester_telegram_id=%s"
        params.append(tg_id)
    if bot_key is not None:
        query += " AND profile_key=%s"
        params.append(bot_key)
    with _get_pool().connection() as conn:
        return int(conn.execute(query, params).fetchone()["count"])


def _list_tickets(where: str, params: list[object], limit: int,
                  offset: int) -> list[tuple]:
    query = _TICKET_SELECT + f" WHERE {where} ORDER BY f.ticket_id DESC LIMIT %s OFFSET %s"
    with _get_pool().connection() as conn:
        rows = conn.execute(query, (*params, limit, offset)).fetchall()
    return [_legacy_ticket(row) for row in rows]


def get_tickets_by_user(tg_id: int, status: str, limit: int = 8,
                        offset: int = 0, bot_key: str | None = None) -> list[tuple]:
    parts = ["f.requester_telegram_id=%s", "f.status_code=%s"]
    params: list[object] = [tg_id, STATUS_TO_CODE[status]]
    if bot_key is not None:
        parts.append("f.profile_key=%s")
        params.append(bot_key)
    return _list_tickets(" AND ".join(parts), params, limit, offset)


def get_all_tickets_in_progress(limit: int = 20, offset: int = 0,
                                bot_key: str | None = None) -> list[tuple]:
    return _all_by_status(STATUS_OPEN, limit, offset, bot_key)


def get_all_completed_tickets(limit: int = 20, offset: int = 0,
                              bot_key: str | None = None) -> list[tuple]:
    return _all_by_status(STATUS_COMPLETED, limit, offset, bot_key)


def _all_by_status(status: str, limit: int, offset: int,
                   bot_key: str | None) -> list[tuple]:
    parts = ["f.status_code=%s"]
    params: list[object] = [STATUS_TO_CODE[status]]
    if bot_key is not None:
        parts.append("f.profile_key=%s")
        params.append(bot_key)
    return _list_tickets(" AND ".join(parts), params, limit, offset)


def get_tickets_in_progress_by_user_id(tg_id: int) -> list[tuple]:
    return get_tickets_by_user(tg_id, STATUS_OPEN, limit=100)


def get_completed_tickets_by_user(tg_id: int) -> list[tuple]:
    return get_tickets_by_user(tg_id, STATUS_COMPLETED, limit=100)


def get_last_ticket_number() -> int:
    with _get_pool().connection() as conn:
        return int(conn.execute(
            "SELECT coalesce(max(ticket_id),0) AS value FROM ticketing.tickets"
        ).fetchone()["value"])


def add_ticket_comment(ticket_id: int, actor_tg_id: int, comment: str,
                       created_at: str | None = None,
                       bot_key: str | None = None,
                       idempotency_key: str | None = None) -> int:
    if bot_key is None:
        raise ValueError("bot_key is required")
    idempotency_key = idempotency_key or f"generated:{uuid4()}"
    with _get_pool().connection() as conn:
        row = conn.execute(
            "SELECT ticketing.add_message(%s,%s,%s,'internal',%s,%s,%s)",
            (bot_key, ticket_id, actor_tg_id, comment,
             _aware(created_at), idempotency_key),
        ).fetchone()
    return int(next(iter(row.values())))


def add_ticket_public_message(ticket_id: int, actor_tg_id: int, text: str,
                              created_at: str | None = None,
                              bot_key: str | None = None,
                              idempotency_key: str | None = None) -> int:
    if bot_key is None:
        raise ValueError("bot_key is required")
    idempotency_key = idempotency_key or f"generated:{uuid4()}"
    with _get_pool().connection() as conn:
        row = conn.execute(
            "SELECT ticketing.add_message(%s,%s,%s,'public',%s,%s,%s)",
            (bot_key, ticket_id, actor_tg_id, text,
             _aware(created_at), idempotency_key),
        ).fetchone()
    return int(next(iter(row.values())))


def change_ticket_status(ticket_id: int, new_status: str, actor_tg_id: int,
                         changed_at: str | None = None,
                         bot_key: str | None = None,
                         idempotency_key: str | None = None) -> bool:
    if bot_key is None:
        raise ValueError("bot_key is required")
    idempotency_key = idempotency_key or f"generated:{uuid4()}"
    with _get_pool().connection() as conn:
        row = conn.execute(
            "SELECT ticketing.change_status(%s,%s,%s,%s,%s,%s) AS changed",
            (bot_key, ticket_id, actor_tg_id, STATUS_TO_CODE[new_status],
             _aware(changed_at), idempotency_key),
        ).fetchone()
    return bool(row["changed"])


def update_ticket_status(ticket_id: int, new_status: str) -> None:
    raise RuntimeError("Use change_ticket_status with an actor and bot_key")


def read_ticket_comment(ticket_id: int, bot_key: str | None = None) -> str | None:
    query = (
        "SELECT m.body FROM ticketing.ticket_messages m "
        "WHERE m.ticket_id=%s AND m.visibility='internal'"
    )
    params: list[object] = [ticket_id]
    if bot_key is not None:
        query += " AND m.profile_key=%s"
        params.append(bot_key)
    query += " ORDER BY m.sent_at DESC,m.message_id DESC LIMIT 1"
    with _get_pool().connection() as conn:
        row = conn.execute(query, params).fetchone()
    return row["body"] if row else None


def get_ticket_events(ticket_id: int, limit: int = 20,
                      bot_key: str | None = None) -> list[tuple]:
    query = """
        SELECT e.event_id,e.ticket_id,u.telegram_user_id AS actor_tg_id,e.event_type,
               e.from_status,e.to_status,m.body,e.occurred_at
        FROM ticketing.ticket_events e
        LEFT JOIN ticketing.telegram_users u ON u.user_id=e.actor_user_id
        LEFT JOIN ticketing.ticket_messages m ON m.message_id=e.message_id
        WHERE e.ticket_id=%s
    """
    params: list[object] = [ticket_id]
    if bot_key is not None:
        query += " AND e.profile_key=%s"
        params.append(bot_key)
    query += " ORDER BY e.event_id DESC LIMIT %s"
    params.append(limit)
    with _get_pool().connection() as conn:
        rows = conn.execute(query, params).fetchall()
    result = []
    for row in reversed(rows):
        event_type = "comment" if row["event_type"] == "internal_note" else row["event_type"]
        result.append((
            row["event_id"], row["ticket_id"], row["actor_tg_id"] or 0,
            event_type,
            CODE_TO_STATUS.get(row["from_status"], row["from_status"]),
            CODE_TO_STATUS.get(row["to_status"], row["to_status"]),
            row["body"], _format_timestamp(row["occurred_at"]),
        ))
    return result

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


def save_draft_intake(bot_key: str, author_id: int, chat_id: int,
                      message_id: int, body: str, received_at: str) -> None:
    with _get_pool().connection() as conn:
        conn.execute(
            "SELECT ticketing.save_draft_intake(%s,%s,%s,%s,%s,%s)",
            (bot_key, author_id, chat_id, message_id, body, _aware(received_at)),
        )


def create_forwarded_draft(
    bot_key: str,
    author_id: int,
    source_chat_id: int,
    source_message_id: int,
    source_body: str,
    forward_origin_type: str | None,
    forward_origin_name: str | None,
    forward_origin_date: str | None,
    created_at: str,
) -> dict:
    with _get_pool().connection() as conn:
        result = conn.execute(
            "SELECT ticketing.create_forwarded_draft(%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "AS draft_id",
            (
                bot_key,
                author_id,
                source_chat_id,
                source_message_id,
                source_body,
                forward_origin_type,
                forward_origin_name,
                _aware(forward_origin_date) if forward_origin_date else None,
                _aware(created_at),
            ),
        ).fetchone()
        row = conn.execute(
            '''
            SELECT d.draft_id,d.profile_key,u.telegram_user_id AS author_id,
                   d.status,d.source_body,d.client_comment,d.edited_body,
                   d.source_chat_id,d.source_message_id,d.created_at
            FROM ticketing.ticket_drafts d
            JOIN ticketing.telegram_users u ON u.user_id=d.author_user_id
            WHERE d.profile_key=%s AND d.draft_id=%s
            ''',
            (bot_key, result["draft_id"]),
        ).fetchone()
    if row is None:
        raise RuntimeError("Unable to load forwarded draft")
    return {
        **row,
        "created_at": _format_timestamp(row["created_at"]),
    }


def append_forwarded_message(
    bot_key: str,
    author_id: int,
    chat_id: int,
    message_id: int,
    body: str,
    received_at: str,
    window_seconds: int,
    forward_origin_type: str | None,
    forward_origin_name: str | None,
    forward_origin_date: str | None,
) -> str:
    received = _aware(received_at)
    with _get_pool().connection() as conn:
        with conn.transaction():
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"forward:{bot_key}:{author_id}:{chat_id}",),
            )
            duplicate = conn.execute(
                """
                SELECT 1 FROM ticketing.forward_batch_messages
                WHERE profile_key=%s AND chat_id=%s AND telegram_message_id=%s
                """,
                (bot_key, chat_id, message_id),
            ).fetchone()
            if duplicate is not None:
                return "duplicate"
            author = conn.execute(
                """
                SELECT u.user_id
                FROM ticketing.telegram_users u
                JOIN ticketing.memberships m USING(user_id)
                WHERE u.telegram_user_id=%s AND m.profile_key=%s AND m.active
                """,
                (author_id, bot_key),
            ).fetchone()
            if author is None:
                raise RuntimeError("Active draft author membership is required")
            batch = conn.execute(
                """
                SELECT batch_id,due_at
                FROM ticketing.forward_batches
                WHERE profile_key=%s AND author_user_id=%s AND chat_id=%s
                  AND state='collecting'
                FOR UPDATE
                """,
                (bot_key, author["user_id"], chat_id),
            ).fetchone()
            state = "appended"
            if batch is not None and received >= batch["due_at"]:
                conn.execute(
                    "UPDATE ticketing.forward_batches SET state='ready' "
                    "WHERE batch_id=%s",
                    (batch["batch_id"],),
                )
                batch = None
            if batch is None:
                state = "new"
                batch = conn.execute(
                    """
                    INSERT INTO ticketing.forward_batches
                        (profile_key,author_user_id,chat_id,received_at,due_at,
                         window_seconds)
                    VALUES (%s,%s,%s,%s,%s + make_interval(secs => %s),%s)
                    RETURNING batch_id,due_at
                    """,
                    (
                        bot_key,
                        author["user_id"],
                        chat_id,
                        received,
                        received,
                        window_seconds,
                        window_seconds,
                    ),
                ).fetchone()
            conn.execute(
                """
                INSERT INTO ticketing.forward_batch_messages
                    (batch_id,profile_key,chat_id,telegram_message_id,body,
                     received_at,forward_origin_type,forward_origin_name,
                     forward_origin_date)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    batch["batch_id"],
                    bot_key,
                    chat_id,
                    message_id,
                    body.strip(),
                    received,
                    forward_origin_type,
                    forward_origin_name,
                    _aware(forward_origin_date) if forward_origin_date else None,
                ),
            )
            return state


def finalize_due_forward_batches(
    bot_key: str,
    now: str,
    limit: int = 20,
) -> list[dict]:
    completed: list[dict] = []
    due_now = _aware(now)
    with _get_pool().connection() as conn:
        while len(completed) < limit:
            with conn.transaction():
                batch = conn.execute(
                    """
                    SELECT b.*
                    FROM ticketing.forward_batches b
                    WHERE b.profile_key=%s
                      AND b.state IN ('collecting','ready')
                      AND b.due_at <= %s
                    ORDER BY b.due_at,b.batch_id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """,
                    (bot_key, due_now),
                ).fetchone()
                if batch is None:
                    break
                messages = conn.execute(
                    """
                    SELECT * FROM ticketing.forward_batch_messages
                    WHERE batch_id=%s
                    ORDER BY telegram_message_id,batch_message_id
                    """,
                    (batch["batch_id"],),
                ).fetchall()
                if not messages:
                    raise RuntimeError("Forward batch has no messages")
                source_body = "\n\n".join(row["body"] for row in messages)
                first = messages[0]
                intake = conn.execute(
                    """
                    SELECT body FROM ticketing.ticket_draft_intake
                    WHERE profile_key=%s AND author_user_id=%s AND chat_id=%s
                      AND consumed_at IS NULL
                      AND received_at BETWEEN %s - interval '10 minutes' AND %s
                    """,
                    (
                        bot_key,
                        batch["author_user_id"],
                        batch["chat_id"],
                        batch["received_at"],
                        batch["received_at"],
                    ),
                ).fetchone()
                draft = conn.execute(
                    """
                    INSERT INTO ticketing.ticket_drafts
                        (profile_key,author_user_id,status,source_body,
                         client_comment,edited_body,source_chat_id,
                         source_message_id,forward_origin_type,
                         forward_origin_name,forward_origin_date,created_at,updated_at,
                         ticket_type)
                    VALUES (%s,%s,'draft',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL)
                    ON CONFLICT (profile_key,source_chat_id,source_message_id)
                    DO NOTHING
                    RETURNING draft_id
                    """,
                    (
                        bot_key,
                        batch["author_user_id"],
                        source_body,
                        intake["body"] if intake else "",
                        source_body,
                        batch["chat_id"],
                        first["telegram_message_id"],
                        first["forward_origin_type"],
                        first["forward_origin_name"],
                        first["forward_origin_date"],
                        batch["received_at"],
                        due_now,
                    ),
                ).fetchone()
                if draft is None:
                    draft = conn.execute(
                        """
                        SELECT draft_id FROM ticketing.ticket_drafts
                        WHERE profile_key=%s AND source_chat_id=%s
                          AND source_message_id=%s
                        """,
                        (bot_key, batch["chat_id"], first["telegram_message_id"]),
                    ).fetchone()
                conn.execute(
                    """
                    UPDATE ticketing.forward_batches
                    SET state='closed',draft_id=%s,closed_at=%s
                    WHERE batch_id=%s
                    """,
                    (draft["draft_id"], due_now, batch["batch_id"]),
                )
                if intake is not None:
                    conn.execute(
                        """
                        UPDATE ticketing.ticket_draft_intake SET consumed_at=%s
                        WHERE profile_key=%s AND author_user_id=%s AND chat_id=%s
                          AND consumed_at IS NULL
                        """,
                        (
                            due_now,
                            bot_key,
                            batch["author_user_id"],
                            batch["chat_id"],
                        ),
                    )
                row = conn.execute(
                    """
                    SELECT d.draft_id,d.profile_key,u.telegram_user_id AS author_id,
                           d.status,d.source_body,d.client_comment,d.edited_body,
                           d.source_chat_id,d.source_message_id,d.created_at
                    FROM ticketing.ticket_drafts d
                    JOIN ticketing.telegram_users u ON u.user_id=d.author_user_id
                    WHERE d.draft_id=%s
                    """,
                    (draft["draft_id"],),
                ).fetchone()
                completed.append({
                    **row,
                    "created_at": _format_timestamp(row["created_at"]),
                })
    return completed


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
SELECT f.ticket_id,f.ticket_number,f.requester_telegram_id,f.description,f.created_at,
       f.status_code,f.assigned_telegram_id,f.updated_at,f.closed_at,f.profile_key,
       f.ticket_type,
       (SELECT m.body FROM ticketing.ticket_messages m
        WHERE m.ticket_id=f.ticket_id AND m.visibility='internal'
        ORDER BY m.sent_at DESC,m.message_id DESC LIMIT 1) AS latest_comment
FROM reporting.ticket_facts f
"""


def _ticket_mapping(row: dict) -> dict:
    return {
        "ticket_id": row["ticket_id"],
        "ticket_number": row["ticket_number"],
        "owner_id": row["requester_telegram_id"],
        "body": row["description"],
        "created_at": _format_timestamp(row["created_at"]),
        "status": CODE_TO_STATUS.get(row["status_code"], row["status_code"]),
        "latest_comment": row["latest_comment"] or "",
        "assigned_to": row["assigned_telegram_id"],
        "updated_at": _format_timestamp(row["updated_at"]),
        "closed_at": _format_timestamp(row["closed_at"]),
        "bot_key": row["profile_key"],
        "ticket_type": row["ticket_type"],
    }


def get_ticket_info(ticket_id: int, bot_key: str | None = None) -> dict | None:
    query = _TICKET_SELECT + " WHERE f.ticket_id=%s"
    params: list[object] = [ticket_id]
    if bot_key is not None:
        query += " AND f.profile_key=%s"
        params.append(bot_key)
    with _get_pool().connection() as conn:
        row = conn.execute(query, params).fetchone()
    return _ticket_mapping(row) if row else None


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
                  offset: int) -> list[dict]:
    query = _TICKET_SELECT + f" WHERE {where} ORDER BY f.ticket_id DESC LIMIT %s OFFSET %s"
    with _get_pool().connection() as conn:
        rows = conn.execute(query, (*params, limit, offset)).fetchall()
    return [_ticket_mapping(row) for row in rows]


def get_tickets_by_user(tg_id: int, status: str, limit: int = 8,
                        offset: int = 0, bot_key: str | None = None) -> list[dict]:
    parts = ["f.requester_telegram_id=%s", "f.status_code=%s"]
    params: list[object] = [tg_id, STATUS_TO_CODE[status]]
    if bot_key is not None:
        parts.append("f.profile_key=%s")
        params.append(bot_key)
    return _list_tickets(" AND ".join(parts), params, limit, offset)


def get_all_tickets_in_progress(limit: int = 20, offset: int = 0,
                                bot_key: str | None = None) -> list[dict]:
    return _all_by_status(STATUS_OPEN, limit, offset, bot_key)


def get_all_completed_tickets(limit: int = 20, offset: int = 0,
                              bot_key: str | None = None) -> list[dict]:
    return _all_by_status(STATUS_COMPLETED, limit, offset, bot_key)


def _all_by_status(status: str, limit: int, offset: int,
                   bot_key: str | None) -> list[dict]:
    parts = ["f.status_code=%s"]
    params: list[object] = [STATUS_TO_CODE[status]]
    if bot_key is not None:
        parts.append("f.profile_key=%s")
        params.append(bot_key)
    return _list_tickets(" AND ".join(parts), params, limit, offset)


def get_tickets_in_progress_by_user_id(tg_id: int) -> list[dict]:
    return get_tickets_by_user(tg_id, STATUS_OPEN, limit=100)


def get_completed_tickets_by_user(tg_id: int) -> list[dict]:
    return get_tickets_by_user(tg_id, STATUS_COMPLETED, limit=100)


def get_last_ticket_number(bot_key: str | None = None) -> int:
    query = "SELECT coalesce(max(ticket_number),0) AS value FROM ticketing.tickets"
    params: tuple[object, ...] = ()
    if bot_key is not None:
        query += " WHERE profile_key=%s"
        params = (bot_key,)
    with _get_pool().connection() as conn:
        return int(conn.execute(query, params).fetchone()["value"])


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
                      bot_key: str | None = None) -> list[dict]:
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
    result: list[dict] = []
    for row in reversed(rows):
        event_type = "comment" if row["event_type"] == "internal_note" else row["event_type"]
        result.append({
            "event_id": row["event_id"],
            "ticket_id": row["ticket_id"],
            "actor_id": row["actor_tg_id"] or 0,
            "event_type": event_type,
            "from_status": CODE_TO_STATUS.get(
                row["from_status"], row["from_status"]
            ),
            "to_status": CODE_TO_STATUS.get(row["to_status"], row["to_status"]),
            "text": row["body"],
            "created_at": _format_timestamp(row["occurred_at"]),
        })
    return result

import sqlite3
import datetime
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from app.domain.constants import STATUS_COMPLETED, STATUS_OPEN

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "database.db"
DB_PATH = os.getenv("HELPDESK_DB_PATH", str(DEFAULT_DB_PATH))
MOSCOW_TZ = ZoneInfo("Europe/Moscow")

def parse_to_moscow_naive(dt_input: datetime.datetime | str | None) -> datetime.datetime:
    """
    Преобразует входное значение во время Москвы (naive, без tzinfo).
    Если входное значение None или некорректно — возвращает текущее московское время.
    Строки парсятся автоматически; для naive datetime предполагается UTC; для aware — конвертация в Москву.
    """
    if not dt_input:
        return datetime.datetime.now(MOSCOW_TZ).replace(tzinfo=None)

    if isinstance(dt_input, str):
        try:
            # Если строка в формате "YYYY-MM-DD HH:MM:SS", считаем её MSK (naive)
            dt = datetime.datetime.strptime(dt_input, "%Y-%m-%d %H:%M:%S")
            return dt  # Возвращаем как есть, без преобразования
        except ValueError:
            try:
                # Пробуем ISO-формат с временной зоной
                dt = datetime.datetime.fromisoformat(dt_input.replace("Z", "+00:00"))
            except ValueError:
                return datetime.datetime.now(MOSCOW_TZ).replace(tzinfo=None)
    else:
        dt = dt_input

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(MOSCOW_TZ).replace(tzinfo=None)

def moscow_timestamp(dt_input: datetime.datetime | str | None = None) -> str:
    """Возвращает московское время в формате, используемом в SQLite."""
    return parse_to_moscow_naive(dt_input).strftime("%Y-%m-%d %H:%M:%S")

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn

def execute_query(query: str, params: tuple | None = None, fetch_one: bool = False) -> list[tuple] | tuple | None:
    """
    Выполняет SQL-запрос и возвращает результаты.
    Если fetch_one=True — возвращает одну строку или None. Иначе — все строки.
    Фиксирует изменения (commit) только для не-SELECT запросов.
    """
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(query, params or ())
        if query.strip().upper().startswith("SELECT"):
            result = cursor.fetchone() if fetch_one else cursor.fetchall()
        else:
            conn.commit()
            result = None
        return result
    finally:
        conn.close()

def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    """Добавляет колонку в старую таблицу, если её ещё нет."""
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _ensure_bot_scoped_users(conn: sqlite3.Connection) -> None:
    """Migrates the legacy tg_id primary key to a per-bot user identity."""
    table_info = conn.execute("PRAGMA table_info(users)").fetchall()
    primary_key = [
        row[1]
        for row in sorted(table_info, key=lambda row: row[5])
        if row[5]
    ]
    if primary_key == ["tg_id", "bot_key"]:
        return

    conn.execute('''
        CREATE TABLE users_bot_scoped (
            tg_id INTEGER NOT NULL,
            data_reg TEXT,
            organization TEXT,
            organization_adress TEXT,
            organization_inn TEXT,
            organization_phone TEXT,
            history_ticket TEXT,
            data_ticket TEXT,
            user_name TEXT,
            role TEXT NOT NULL DEFAULT 'client',
            bot_key TEXT NOT NULL DEFAULT 'default',
            PRIMARY KEY (tg_id, bot_key)
        )
    ''')
    conn.execute('''
        INSERT INTO users_bot_scoped (
            tg_id, data_reg, organization, organization_adress,
            organization_inn, organization_phone, history_ticket,
            data_ticket, user_name, role, bot_key
        )
        SELECT
            tg_id, data_reg, organization, organization_adress,
            organization_inn, organization_phone, history_ticket,
            data_ticket, user_name, role, bot_key
        FROM users
    ''')
    conn.execute("DROP TABLE users")
    conn.execute("ALTER TABLE users_bot_scoped RENAME TO users")

def create_tables():
    """
    Создает таблицы базы данных, если они еще не существуют.
    """
    users = '''
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            data_reg TEXT,
            organization TEXT,
            organization_adress TEXT,
            organization_inn TEXT,
            organization_phone TEXT,
            history_ticket TEXT,
            data_ticket TEXT,
            user_name TEXT
        )
    '''
    ticket = '''
        CREATE TABLE IF NOT EXISTS ticket (
            number_ticket INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id_ticket INTEGER,
            organization TEXT,
            addres_ticket TEXT,
            message_ticket TEXT,
            time_ticket TEXT,
            state_ticket TEXT,
            ticket_comm TEXT
        )
    '''
    conn = _connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        with conn:
            conn.execute(users)
            conn.execute(ticket)

            _ensure_column(conn, "users", "role", "TEXT NOT NULL DEFAULT 'client'")
            _ensure_column(conn, "users", "bot_key", "TEXT NOT NULL DEFAULT 'default'")
            _ensure_bot_scoped_users(conn)
            _ensure_column(conn, "ticket", "assigned_to", "INTEGER")
            _ensure_column(conn, "ticket", "updated_at", "TEXT")
            _ensure_column(conn, "ticket", "closed_at", "TEXT")
            _ensure_column(conn, "ticket", "bot_key", "TEXT NOT NULL DEFAULT 'default'")

            conn.execute('''
                CREATE TABLE IF NOT EXISTS ticket_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER NOT NULL,
                    actor_tg_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT,
                    event_text TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(ticket_id) REFERENCES ticket(number_ticket)
                )
            ''')
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ticket_owner_status "
                "ON ticket(tg_id_ticket, state_ticket)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ticket_events_ticket "
                "ON ticket_events(ticket_id, event_id)"
            )
            conn.execute(
                "UPDATE ticket SET updated_at = time_ticket WHERE updated_at IS NULL"
            )
    finally:
        conn.close()

def add_user(tg_id: int, data_reg: str, organization: str, organization_adress: str,
             organization_inn: str, organization_phone: str, history_ticket: str = "",
             data_ticket: str = "", user_name: str = "", role: str = "client",
             bot_key: str = "default") -> None:
    """Добавляет нового пользователя в базу данных."""
    query = '''INSERT INTO users (tg_id, data_reg, organization, organization_adress,
                                 organization_inn, organization_phone, history_ticket,
                                 data_ticket, user_name, role, bot_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'''
    execute_query(query, (tg_id, data_reg, organization, organization_adress,
                         organization_inn, organization_phone, history_ticket,
                         data_ticket, user_name, role, bot_key))

def ensure_user(tg_id: int, data_reg: str, user_name: str,
                role: str, bot_key: str) -> None:
    """Создаёт пользователя или актуализирует его Telegram-имя и роль."""
    query = '''
        INSERT INTO users (
            tg_id, data_reg, organization, organization_adress,
            organization_inn, organization_phone, history_ticket,
            data_ticket, user_name, role, bot_key
        ) VALUES (?, ?, '', '', '', '', '', '', ?, ?, ?)
        ON CONFLICT(tg_id, bot_key) DO UPDATE SET
            user_name = excluded.user_name,
            role = excluded.role
    '''
    execute_query(query, (tg_id, data_reg, user_name, role, bot_key))


def sync_user_roles(bot_key: str, admin_ids: frozenset[int],
                    technician_ids: frozenset[int]) -> None:
    """Synchronizes persisted audit roles with the active bot configuration."""
    conn = _connect()
    try:
        with conn:
            conn.execute(
                "UPDATE users SET role = 'client' WHERE bot_key = ?",
                (bot_key,),
            )
            if technician_ids:
                placeholders = ",".join("?" for _ in technician_ids)
                conn.execute(
                    f"UPDATE users SET role = 'technician' "
                    f"WHERE bot_key = ? AND tg_id IN ({placeholders})",
                    (bot_key, *sorted(technician_ids)),
                )
            if admin_ids:
                placeholders = ",".join("?" for _ in admin_ids)
                conn.execute(
                    f"UPDATE users SET role = 'admin' "
                    f"WHERE bot_key = ? AND tg_id IN ({placeholders})",
                    (bot_key, *sorted(admin_ids)),
                )
    finally:
        conn.close()

def get_user_by_id(tg_id: int, bot_key: str | None = None) -> dict | None:
    """Возвращает информацию о пользователе по Telegram ID."""
    query = "SELECT * FROM users WHERE tg_id = ?"
    params: tuple = (tg_id,)
    if bot_key is not None:
        query += " AND bot_key = ?"
        params = (tg_id, bot_key)
    row = execute_query(query, params, fetch_one=True)
    if row:
        return {
            'tg_id': row[0],
            'data_reg': row[1],
            'organization': row[2],
            'organization_adress': row[3],
            'organization_inn': row[4],
            'organization_phone': row[5],
            'history_ticket': row[6],
            'data_ticket': row[7],
            'user_name': row[8],
            'role': row[9],
            'bot_key': row[10]
        }
    return None

def update_user_field(tg_id: int, field_name: str, value: str,
                      bot_key: str | None = None) -> None:
    """Обновляет указанное поле пользователя."""
    allowed_fields = {
        'history_ticket', 'data_ticket', 'user_name', 'role', 'bot_key'
    }
    if field_name not in allowed_fields:
        raise ValueError(f"Unsupported user field: {field_name}")
    query = f"UPDATE users SET {field_name} = ? WHERE tg_id = ?"
    params: tuple = (value, tg_id)
    if bot_key is not None:
        query += " AND bot_key = ?"
        params = (value, tg_id, bot_key)
    execute_query(query, params)

def add_ticket(tg_id_ticket: int, organization: str, addres_ticket: str, message_ticket: str,
               time_ticket: str, state_ticket: str, ticket_comm: str,
               assigned_to: int | None = None, bot_key: str = "default",
               request_key: str | None = None) -> int:
    """Атомарно создаёт тикет и возвращает ID именно этой записи."""
    conn = _connect()
    try:
        with conn:
            cursor = conn.execute('''
                INSERT INTO ticket (
                    tg_id_ticket, organization, addres_ticket, message_ticket,
                    time_ticket, state_ticket, ticket_comm, assigned_to,
                    updated_at, closed_at, bot_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                tg_id_ticket, organization, addres_ticket, message_ticket,
                time_ticket, state_ticket, ticket_comm, assigned_to,
                time_ticket,
                time_ticket if state_ticket == STATUS_COMPLETED else None,
                bot_key,
            ))
            ticket_id = int(cursor.lastrowid)
            conn.execute('''
                INSERT INTO ticket_events (
                    ticket_id, actor_tg_id, event_type,
                    from_status, to_status, event_text, created_at
                ) VALUES (?, ?, 'created', NULL, ?, NULL, ?)
            ''', (ticket_id, tg_id_ticket, state_ticket, time_ticket))
            return ticket_id
    finally:
        conn.close()

def get_last_ticket_number() -> int:
    """Возвращает номер последнего тикета."""
    query = "SELECT number_ticket FROM ticket ORDER BY number_ticket DESC LIMIT 1"
    row = execute_query(query, fetch_one=True)
    return row[0] if row else 0

def get_ticket_count(tg_id: int | None, status: str,
                     bot_key: str | None = None) -> int:
    """Возвращает количество тикетов с указанным статусом. Если tg_id=None — для всех пользователей."""
    query = "SELECT COUNT(*) FROM ticket WHERE state_ticket = ?"
    params = (status,)
    if tg_id is not None:
        query += " AND tg_id_ticket = ?"
        params = (status, tg_id)
    if bot_key is not None:
        query += " AND bot_key = ?"
        params += (bot_key,)
    row = execute_query(query, params, fetch_one=True)
    return row[0] if row else 0

def get_tickets_in_progress_by_user_id(tg_id: int) -> list[tuple]:
    """Возвращает список тикетов пользователя в статусе "В работе"."""
    return get_tickets_by_user(tg_id, STATUS_OPEN, limit=100)

def get_tickets_by_user(tg_id: int, status: str,
                        limit: int = 8, offset: int = 0,
                        bot_key: str | None = None) -> list[tuple]:
    query = '''
        SELECT * FROM ticket
        WHERE tg_id_ticket = ? AND state_ticket = ?
    '''
    params: tuple = (tg_id, status)
    if bot_key is not None:
        query += " AND bot_key = ?"
        params += (bot_key,)
    query += " ORDER BY number_ticket DESC LIMIT ? OFFSET ?"
    params += (limit, offset)
    return execute_query(query, params)

def get_all_tickets_in_progress(limit: int = 20, offset: int = 0,
                                bot_key: str | None = None) -> list[tuple]:
    """Возвращает список всех тикетов в статусе "В работе"."""
    query = '''
        SELECT * FROM ticket
        WHERE state_ticket = ?
    '''
    params: tuple = (STATUS_OPEN,)
    if bot_key is not None:
        query += " AND bot_key = ?"
        params += (bot_key,)
    query += " ORDER BY number_ticket DESC LIMIT ? OFFSET ?"
    params += (limit, offset)
    return execute_query(query, params)

def get_all_completed_tickets(limit: int = 20, offset: int = 0,
                              bot_key: str | None = None) -> list[tuple]:
    """Возвращает последние завершённые тикеты для меню администратора."""
    query = '''
        SELECT *
        FROM ticket
        WHERE state_ticket = ?
    '''
    params: tuple = (STATUS_COMPLETED,)
    if bot_key is not None:
        query += " AND bot_key = ?"
        params += (bot_key,)
    query += " ORDER BY number_ticket DESC LIMIT ? OFFSET ?"
    params += (limit, offset)
    return execute_query(query, params)

def get_ticket_info(ticket_id: int, bot_key: str | None = None) -> tuple | None:
    """Возвращает информацию о тикете по его номеру."""
    query = "SELECT * FROM ticket WHERE number_ticket = ?"
    params: tuple = (ticket_id,)
    if bot_key is not None:
        query += " AND bot_key = ?"
        params = (ticket_id, bot_key)
    return execute_query(query, params, fetch_one=True)

def update_ticket_status(ticket_id: int, new_status: str) -> None:
    """Совместимость со старым кодом; новые обработчики передают реального автора."""
    change_ticket_status(ticket_id, new_status, actor_tg_id=0)

def change_ticket_status(ticket_id: int, new_status: str,
                         actor_tg_id: int,
                         changed_at: str | None = None,
                         bot_key: str | None = None,
                         idempotency_key: str | None = None) -> bool:
    """Атомарно меняет статус и записывает событие с автором и временем."""
    changed_at = changed_at or moscow_timestamp()
    conn = _connect()
    try:
        with conn:
            query = "SELECT state_ticket FROM ticket WHERE number_ticket = ?"
            params: tuple = (ticket_id,)
            if bot_key is not None:
                query += " AND bot_key = ?"
                params = (ticket_id, bot_key)
            row = conn.execute(query, params).fetchone()
            if not row or row[0] == new_status:
                return False

            old_status = row[0]
            closed_at = changed_at if new_status == STATUS_COMPLETED else None
            conn.execute('''
                UPDATE ticket
                SET state_ticket = ?, updated_at = ?, closed_at = ?
                WHERE number_ticket = ?
            ''', (new_status, changed_at, closed_at, ticket_id))
            conn.execute('''
                INSERT INTO ticket_events (
                    ticket_id, actor_tg_id, event_type,
                    from_status, to_status, event_text, created_at
                ) VALUES (?, ?, 'status_changed', ?, ?, NULL, ?)
            ''', (ticket_id, actor_tg_id, old_status, new_status, changed_at))
            return True
    finally:
        conn.close()

def get_completed_tickets_by_user(tg_id: int) -> list[tuple]:
    """Возвращает список завершенных тикетов пользователя."""
    return get_tickets_by_user(tg_id, STATUS_COMPLETED, limit=100)

def update_ticket_comment(ticket_id: int, ticket_comm: str) -> bool:
    """Совместимость со старым кодом; новый код использует add_ticket_comment."""
    add_ticket_comment(ticket_id, actor_tg_id=0, comment=ticket_comm)
    return True

def add_ticket_comment(ticket_id: int, actor_tg_id: int, comment: str,
                       created_at: str | None = None,
                       bot_key: str | None = None,
                       idempotency_key: str | None = None) -> int:
    """Добавляет комментарий, сохраняя все предыдущие записи."""
    created_at = created_at or moscow_timestamp()
    conn = _connect()
    try:
        with conn:
            query = "SELECT 1 FROM ticket WHERE number_ticket = ?"
            params: tuple = (ticket_id,)
            if bot_key is not None:
                query += " AND bot_key = ?"
                params = (ticket_id, bot_key)
            exists = conn.execute(query, params).fetchone()
            if not exists:
                raise ValueError(f"Ticket {ticket_id} does not exist")

            conn.execute('''
                UPDATE ticket
                SET ticket_comm = ?, updated_at = ?
                WHERE number_ticket = ?
            ''', (comment, created_at, ticket_id))
            cursor = conn.execute('''
                INSERT INTO ticket_events (
                    ticket_id, actor_tg_id, event_type,
                    from_status, to_status, event_text, created_at
                ) VALUES (?, ?, 'comment', NULL, NULL, ?, ?)
            ''', (ticket_id, actor_tg_id, comment, created_at))
            return int(cursor.lastrowid)
    finally:
        conn.close()


def add_ticket_public_message(ticket_id: int, actor_tg_id: int, text: str,
                              created_at: str | None = None,
                              bot_key: str | None = None,
                              idempotency_key: str | None = None) -> int:
    """Adds a client-visible message without overwriting internal notes."""
    created_at = created_at or moscow_timestamp()
    conn = _connect()
    try:
        with conn:
            query = "SELECT 1 FROM ticket WHERE number_ticket = ?"
            params: tuple = (ticket_id,)
            if bot_key is not None:
                query += " AND bot_key = ?"
                params = (ticket_id, bot_key)
            if not conn.execute(query, params).fetchone():
                raise ValueError(f"Ticket {ticket_id} does not exist")
            cursor = conn.execute('''
                INSERT INTO ticket_events (
                    ticket_id, actor_tg_id, event_type,
                    from_status, to_status, event_text, created_at
                ) VALUES (?, ?, 'public_message', NULL, NULL, ?, ?)
            ''', (ticket_id, actor_tg_id, text, created_at))
            conn.execute('''
                UPDATE ticket SET updated_at = ? WHERE number_ticket = ?
            ''', (created_at, ticket_id))
            return int(cursor.lastrowid)
    finally:
        conn.close()

def read_ticket_comment(ticket_id: int, bot_key: str | None = None) -> str | None:
    """Читает комментарий существующего тикета."""
    if bot_key is not None and get_ticket_info(ticket_id, bot_key) is None:
        return None
    row = execute_query('''
        SELECT event_text FROM ticket_events
        WHERE ticket_id = ? AND event_type = 'comment'
        ORDER BY event_id DESC LIMIT 1
    ''', (ticket_id,), fetch_one=True)
    if row:
        return row[0]
    legacy_row = execute_query(
        "SELECT ticket_comm FROM ticket WHERE number_ticket = ?",
        (ticket_id,),
        fetch_one=True,
    )
    return legacy_row[0] if legacy_row else None

def get_ticket_events(ticket_id: int, limit: int = 20,
                      bot_key: str | None = None) -> list[tuple]:
    """Возвращает хронологию последних событий задачи."""
    if bot_key is not None and get_ticket_info(ticket_id, bot_key) is None:
        return []
    rows = execute_query('''
        SELECT event_id, ticket_id, actor_tg_id, event_type,
               from_status, to_status, event_text, created_at
        FROM ticket_events
        WHERE ticket_id = ?
        ORDER BY event_id DESC
        LIMIT ?
    ''', (ticket_id, limit))
    return list(reversed(rows))

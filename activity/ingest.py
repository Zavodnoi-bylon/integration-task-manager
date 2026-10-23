"""Offline Telegram Desktop normalization and explicit PostgreSQL ingestion.

Importing this module never reads credentials, opens a database or uses the network.
Raw source records are immutable; importing an older export cannot roll back current
message text. Message identity is deliberately independent of filenames and names.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PARSER_VERSION = "telegram-desktop/1"
NAMESPACE = "telegram-desktop"
ADVISORY_LOCK = 814211970261022
MAX_INPUT_BYTES = 64 * 1024 * 1024


class ImportValidationError(ValueError):
    """A safe validation error: messages contain no source text or credentials."""


class StorageError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def read_json_file(path: str | Path, max_bytes: int = MAX_INPUT_BYTES):
    path = Path(path)
    if path.stat().st_size > max_bytes:
        raise ImportValidationError("input_too_large: no records were truncated")
    data = path.read_bytes()
    if len(data) > max_bytes:
        raise ImportValidationError("input_too_large: no records were truncated")
    try:
        document = json.loads(data.decode("utf-8-sig"),
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ImportValidationError("invalid_json") from error
    return document, hashlib.sha256(data).hexdigest()


def raw_id(value: Any, field: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ImportValidationError(f"{field}: integer/string identifier required")
    result = str(value)
    if not re.fullmatch(r"-?[0-9]{1,32}", result):
        raise ImportValidationError(f"{field}: invalid Telegram identifier")
    return result


def _timestamp(message: dict, epoch_key: str, date_key: str,
               fallback_timezone: str | None, required: bool) -> str | None:
    epoch = message.get(epoch_key)
    try:
        if epoch is not None and epoch != "":
            if isinstance(epoch, bool) or not re.fullmatch(r"-?[0-9]+", str(epoch)):
                raise ValueError()
            value = datetime.fromtimestamp(int(epoch), timezone.utc)
        else:
            text = message.get(date_key)
            if not text:
                if not required:
                    return None
                raise ImportValidationError(f"{date_key}: timestamp required")
            if not fallback_timezone:
                raise ImportValidationError(
                    f"{date_key}: missing Unix timestamp; explicit --timezone required")
            tz = ZoneInfo(fallback_timezone)
            value = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if value.tzinfo is None:
                value = value.replace(tzinfo=tz)
            value = value.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError, ZoneInfoNotFoundError) as error:
        if isinstance(error, ImportValidationError):
            raise
        raise ImportValidationError(f"{date_key}: invalid timestamp/timezone") from error
    return value.isoformat()


def flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        raise ImportValidationError("text: expected string or Telegram entity array")
    parts = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
        else:
            raise ImportValidationError("text: malformed Telegram entity")
    return "".join(parts)


def _entity_links(message: dict) -> list[dict]:
    links = []
    seen = set()
    for field in ("text", "text_entities"):
        for entity in message.get(field, []) if isinstance(message.get(field), list) else []:
            if not isinstance(entity, dict):
                continue
            href = entity.get("href")
            if not href and entity.get("type") == "link":
                href = entity.get("text")
            if isinstance(href, str):
                pair = (entity.get("text", ""), href)
                if pair not in seen:
                    links.append({"text": pair[0], "href": pair[1]})
                    seen.add(pair)
    return links


def _chats(document: Any) -> list[dict]:
    if not isinstance(document, dict):
        raise ImportValidationError("export: top-level object required")
    if isinstance(document.get("messages"), list):
        return [document]
    chats = document.get("chats")
    if isinstance(chats, dict) and isinstance(chats.get("list"), list):
        return chats["list"]
    raise ImportValidationError("export: expected one chat or chats.list")


def normalize_document(document: Any, *, source_name: str = "input.json",
                       content_hash: str | None = None,
                       fallback_timezone: str | None = None) -> dict:
    sources = {}
    records = {}
    seen_keys = set()
    raw_count = service_count = 0
    for chat in _chats(document):
        if not isinstance(chat, dict) or not isinstance(chat.get("messages"), list):
            raise ImportValidationError("chat: malformed messages array")
        chat_id = raw_id(chat.get("id"), "chat.id")
        source_id = f"{NAMESPACE}:{chat_id}"
        name = chat.get("name")
        if name is not None and not isinstance(name, str):
            raise ImportValidationError("chat.name: string required")
        sources[source_id] = {
            "source_id": source_id, "kind": NAMESPACE, "external_id": chat_id,
            "display_name": name or chat_id, "coverage_status": "partial",
            "chat_type": chat.get("type"),
        }
        for message in chat["messages"]:
            raw_count += 1
            if not isinstance(message, dict):
                raise ImportValidationError("message: object required")
            message_id = raw_id(message.get("id"), "message.id")
            key = f"{source_id}:{message_id}"
            sent_at = _timestamp(message, "date_unixtime", "date", fallback_timezone, True)
            edited_at = _timestamp(message, "edited_unixtime", "edited", fallback_timezone, False)
            if edited_at and datetime.fromisoformat(edited_at) < datetime.fromisoformat(sent_at):
                raise ImportValidationError("message.edited_at: earlier than sent_at")
            reply = message.get("reply_to_message_id")
            if reply is not None:
                reply = raw_id(reply, "reply_to_message_id")
            sender_id = message.get("from_id", message.get("actor_id"))
            if sender_id is not None:
                if not isinstance(sender_id, (str, int)) or isinstance(sender_id, bool):
                    raise ImportValidationError("sender_id: string/integer required")
                sender_id = str(sender_id)
            sender_name = message.get("from", message.get("actor"))
            if sender_name is not None and not isinstance(sender_name, str):
                raise ImportValidationError("sender_name: string required")
            revision_hash = sha256_json(message)
            media_missing = any(
                isinstance(message.get(field), str) and
                ("not included" in message[field].lower() or "exceeds maximum" in message[field].lower())
                for field in ("photo", "file", "thumbnail")
            )
            source_url = message.get("link")
            if not isinstance(source_url, str) or not source_url.startswith("https://t.me/"):
                source_url = None
            records[(key, revision_hash)] = {
                "message_key": key, "source_id": source_id, "external_id": message_id,
                "revision_hash": revision_hash, "sent_at": sent_at, "edited_at": edited_at,
                "source_updated_at": edited_at or sent_at,
                "text_plain": flatten_text(message.get("text", "")),
                "sender_id": sender_id, "sender_name": sender_name,
                "reply_to_external_id": reply, "source_url": source_url,
                "media_missing": media_missing, "raw_json": message,
                "entity_links": _entity_links(message),
            }
            seen_keys.add(key)
            service_count += message.get("type") == "service"
    return {
        "schema_version": 1, "kind": "telegram-desktop-normalized",
        "parser_version": PARSER_VERSION + (f";fallback={fallback_timezone}" if fallback_timezone else ""), "source_name": source_name,
        "content_hash": content_hash or sha256_json(document),
        "coverage": {"status": "partial", "note": "Only supplied export records are known"},
        "sources": list(sources.values()), "messages": list(records.values()),
        "counts": {"source_records": raw_count, "unique_messages": len(seen_keys),
                   "unique_revisions": len(records), "service_records": service_count},
    }


def normalize_file(path: str | Path, *, fallback_timezone: str | None = None) -> dict:
    document, digest = read_json_file(path)
    return normalize_document(document, source_name=Path(path).name,
                              content_hash=digest, fallback_timezone=fallback_timezone)


def choose_current(current_hash, current_updated_at, incoming_hash, incoming_updated_at,
                   has_conflict=False):
    """Pure comparison, including same-time conflicts and out-of-order imports."""
    if current_hash is None:
        return incoming_hash, False, "new"
    if current_hash == incoming_hash:
        return current_hash, bool(has_conflict), "unchanged"
    current = (datetime.fromisoformat(current_updated_at)
               if isinstance(current_updated_at, str) else current_updated_at)
    incoming = (datetime.fromisoformat(incoming_updated_at)
                if isinstance(incoming_updated_at, str) else incoming_updated_at)
    if current is None or incoming > current:
        return incoming_hash, False, "newer"
    if incoming < current:
        return current_hash, bool(has_conflict), "older"
    return current_hash, True, "conflict"


def writer_connection():
    """Explicit CLI-only writer configuration; never falls back to API credentials."""
    try:
        import psycopg
    except ImportError as error:
        raise StorageError("psycopg is required for database commands") from error
    dsn = os.getenv("ACTIVITY_WRITER_DATABASE_URL")
    try:
        if dsn:
            return psycopg.connect(dsn)
        user = os.getenv("ACTIVITY_WRITER_DB_USER")
        password = os.getenv("ACTIVITY_WRITER_DB_PASSWORD")
        if not user or not password:
            raise StorageError("Configure ACTIVITY_WRITER_DATABASE_URL or ACTIVITY_WRITER_DB_USER/PASSWORD")
        return psycopg.connect(
            host=os.getenv("ACTIVITY_WRITER_DB_HOST", "postgres"),
            port=int(os.getenv("ACTIVITY_WRITER_DB_PORT", "5432")),
            dbname=os.getenv("ACTIVITY_WRITER_DB_NAME", "activity"),
            user=user, password=password, connect_timeout=10,
        )
    except StorageError:
        raise
    except Exception as error:
        raise StorageError("writer_database_connection_failed") from error


def begin_import(cursor, kind: str, digest: str, source_name: str, parser_version: str):
    cursor.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK,))
    cursor.execute("SELECT id,status,parser_version FROM ops.import_runs WHERE kind=%s AND content_hash=%s FOR UPDATE",
                   (kind, digest))
    existing = cursor.fetchone()
    if existing and existing[1] == "succeeded":
        if existing[2] != parser_version:
            raise ImportValidationError("import_configuration_changed: same file was already imported with different parsing settings")
        return existing[0], True
    if existing:
        cursor.execute("UPDATE ops.import_runs SET status='running',started_at=now(),completed_at=NULL,error_code=NULL WHERE id=%s",
                       (existing[0],))
        return existing[0], False
    cursor.execute("INSERT INTO ops.import_runs(kind,content_hash,source_name,parser_version,status,started_at,counts) "
                   "VALUES (%s,%s,%s,%s,'running',now(),'{}'::jsonb) RETURNING id",
                   (kind, digest, source_name, parser_version))
    return cursor.fetchone()[0], False


def finish_import(cursor, run_id: int, counts: dict):
    cursor.execute("UPDATE ops.import_runs SET status='succeeded',completed_at=now(),counts=%s::jsonb,error_code=NULL WHERE id=%s",
                   (canonical_json(counts), run_id))


def record_failure(conn, kind, digest, source_name, parser_version, error_code):
    """Best-effort metadata only; never persist exception text or source messages."""
    try:
        with conn.transaction():
            with conn.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK,))
                cursor.execute(
                    "INSERT INTO ops.import_runs(kind,content_hash,source_name,parser_version,status,started_at,completed_at,counts,error_code) "
                    "VALUES (%s,%s,%s,%s,'failed',now(),now(),'{}'::jsonb,%s) "
                    "ON CONFLICT(kind,content_hash) DO UPDATE SET status='failed',completed_at=now(),error_code=EXCLUDED.error_code "
                    "WHERE ops.import_runs.status <> 'succeeded'",
                    (kind, digest, source_name, parser_version, error_code),
                )
    except Exception:
        pass


def ingest_bundle(conn, bundle: dict) -> dict:
    kind = "telegram-messages"
    parser_version = bundle["parser_version"]
    counts = {"messages_inserted": 0, "revisions_inserted": 0, "unchanged": 0,
              "current_updates": 0, "conflicts": 0, "older_revisions": 0}
    try:
        with conn.transaction():
            with conn.cursor() as cursor:
                run_id, skipped = begin_import(cursor, kind, bundle["content_hash"],
                                               bundle["source_name"], parser_version)
                if skipped:
                    return {"status": "skipped", "import_id": run_id, "reason": "same_successful_file_hash"}
                for source in bundle["sources"]:
                    cursor.execute(
                        "INSERT INTO raw.sources(source_id,kind,external_id,display_name,coverage_status) VALUES (%s,%s,%s,%s,%s) "
                        "ON CONFLICT(source_id) DO UPDATE SET display_name=EXCLUDED.display_name",
                        tuple(source[k] for k in ("source_id", "kind", "external_id", "display_name", "coverage_status")),
                    )
                for m in bundle["messages"]:
                    cursor.execute(
                        "INSERT INTO raw.messages(message_key,source_id,external_id,current_revision_hash,has_conflict,first_seen_at,last_seen_at) "
                        "VALUES (%s,%s,%s,NULL,false,now(),now()) ON CONFLICT(message_key) DO NOTHING",
                        (m["message_key"], m["source_id"], m["external_id"]),
                    )
                    counts["messages_inserted"] += cursor.rowcount
                    cursor.execute(
                        "INSERT INTO raw.message_revisions(message_key,revision_hash,sent_at,edited_at,source_updated_at,text_plain,sender_id,sender_name,"
                        "reply_to_external_id,source_url,media_missing,raw_json,first_import_id) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT(message_key,revision_hash) DO NOTHING",
                        tuple(m[k] for k in ("message_key", "revision_hash", "sent_at", "edited_at", "source_updated_at", "text_plain", "sender_id",
                                             "sender_name", "reply_to_external_id", "source_url", "media_missing")) + (canonical_json(m["raw_json"]), run_id),
                    )
                    inserted = cursor.rowcount
                    counts["revisions_inserted"] += inserted
                    counts["unchanged"] += not inserted
                    if not inserted:
                        cursor.execute("SELECT sent_at,edited_at FROM raw.message_revisions WHERE message_key=%s AND revision_hash=%s",
                                       (m["message_key"], m["revision_hash"]))
                        previous_sent, previous_edited = cursor.fetchone()
                        incoming_sent = datetime.fromisoformat(m["sent_at"])
                        incoming_edited = datetime.fromisoformat(m["edited_at"]) if m["edited_at"] else None
                        if previous_sent != incoming_sent or previous_edited != incoming_edited:
                            raise ImportValidationError("normalization_timestamp_conflict: same raw revision has different timezone interpretation")
                    cursor.execute(
                        "SELECT m.current_revision_hash,r.source_updated_at,m.has_conflict FROM raw.messages m "
                        "LEFT JOIN raw.message_revisions r ON r.message_key=m.message_key AND r.revision_hash=m.current_revision_hash "
                        "WHERE m.message_key=%s FOR UPDATE OF m", (m["message_key"],),
                    )
                    current_hash, current_time, conflict = cursor.fetchone()
                    selected, new_conflict, reason = choose_current(current_hash, current_time, m["revision_hash"], m["source_updated_at"], conflict)
                    counts["current_updates"] += selected != current_hash
                    counts["conflicts"] += reason == "conflict"
                    counts["older_revisions"] += reason == "older"
                    cursor.execute("UPDATE raw.messages SET current_revision_hash=%s,has_conflict=%s,last_seen_at=now() WHERE message_key=%s",
                                   (selected, new_conflict, m["message_key"]))
                finish_import(cursor, run_id, counts)
        return {"status": "succeeded", "import_id": run_id, **counts}
    except Exception as error:
        record_failure(conn, kind, bundle["content_hash"], bundle["source_name"], parser_version,
                       "validation_error" if isinstance(error, ImportValidationError) else "database_error")
        if isinstance(error, ImportValidationError):
            raise
        raise StorageError("message_import_failed: transaction rolled back") from error

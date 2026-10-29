"""Local Telegram ingestion. Message content is data; no AI or outbound forwarding."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

ROOT = Path(__file__).resolve().parent
HOME = Path(os.environ.get('ACTIVITY_HOME', ROOT))
DATA = HOME / 'data'


def extract_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ''.join(extract_text(v) for v in value)
    if isinstance(value, dict):
        return extract_text(value.get('text', ''))
    return ''


def timestamp(value=None, unix=None):
    if unix not in (None, ''):
        return datetime.fromtimestamp(int(unix), timezone.utc).isoformat()
    if not value:
        return None
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    # Preserve a Desktop timestamp with unknown timezone without inventing UTC.
    return dt.astimezone(timezone.utc).isoformat() if dt.tzinfo else dt.isoformat()


def peer_id(chat):
    value = str(chat.get('id', ''))
    if not value:
        raise ValueError('Chat ID required for deduplication')
    kind = chat.get('type', '')
    if value.isdigit():
        if 'supergroup' in kind or 'channel' in kind:
            return '-100' + value
        if kind in ('private_group', 'public_group', 'group'):
            return '-' + value
    return value


def normalize(chat, message):
    if not isinstance(message, dict) or message.get('type') != 'message':
        return None
    text = extract_text(message.get('text', '')).strip()
    if not text:
        return None
    cid = peer_id(chat)
    mid = int(message['id'])
    return {
        'source_key': f'telegram:{cid}:{mid}',
        'platform': 'telegram', 'chat': {'id': cid, 'name': chat.get('name', ''), 'type': chat.get('type', '')},
        'message_id': mid,
        'sent_at': timestamp(message.get('date'), message.get('date_unixtime')),
        'original_date': message.get('date'),
        'edited_at': timestamp(message.get('edited'), message.get('edited_unixtime')),
        'sender': {'id': str(message.get('from_id') or '').removeprefix('user').removeprefix('channel'), 'name': message.get('from') or 'Неизвестный автор'},
        'reply_to_message_id': message.get('reply_to_message_id'), 'text': text,
        'topic_id': message.get('topic_id'),
    }


def chats_from(data):
    if isinstance(data, dict) and isinstance(data.get('messages'), list):
        return [data]
    if isinstance(data, dict):
        chats = data.get('chats')
        if isinstance(chats, dict):
            chats = chats.get('list')
        if isinstance(chats, list) and all(isinstance(c, dict) and isinstance(c.get('messages'), list) for c in chats):
            return chats
    raise ValueError('Expected Desktop export with chat id and messages, or chats.list')


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.tmp-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def locked():
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (DATA / 'run.lock').open('a+') as lock:
        if os.name == 'posix':
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            import msvcrt
            lock.seek(0)
            lock.write('0')
            lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        yield


def connect():
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(DATA / 'activity.sqlite3')
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS messages (
        source_key TEXT PRIMARY KEY, chat_id TEXT NOT NULL, message_id INTEGER NOT NULL,
        version TEXT NOT NULL, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS imports (digest TEXT PRIMARY KEY, filename TEXT, imported_at TEXT);
    CREATE TABLE IF NOT EXISTS cursors (chat_id TEXT PRIMARY KEY, last_id INTEGER NOT NULL);
    ''')
    return db


def upsert(db, record):
    version = record['edited_at'] or record['sent_at'] or ''
    db.execute('''INSERT INTO messages VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(source_key) DO UPDATE SET version=excluded.version, payload=excluded.payload
        WHERE excluded.version >= messages.version''',
        (record['source_key'], record['chat']['id'], record['message_id'], version,
         json.dumps(record, ensure_ascii=False)))


def import_file(db, path):
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if db.execute('SELECT 1 FROM imports WHERE digest=?', (digest,)).fetchone():
        return 0
    count = 0
    with db:
        for chat in chats_from(json.loads(content.decode('utf-8-sig'))):
            for message in chat['messages']:
                record = normalize(chat, message)
                if record:
                    upsert(db, record)
                    count += 1
        db.execute('INSERT INTO imports VALUES (?, ?, ?)', (digest, path.name, datetime.now(timezone.utc).isoformat()))
    return count


def export(db):
    records = [json.loads(row[0]) for row in db.execute('SELECT payload FROM messages ORDER BY chat_id, message_id')]
    atomic_json(DATA / 'normalized' / 'messages.json', {
        'schema_version': 1, 'generated_at': datetime.now(timezone.utc).isoformat(),
        'message_count': len(records), 'messages': records})
    return len(records)


def import_inbox(db, paths=None):
    inbox = DATA / 'inbox'
    inbox.mkdir(parents=True, exist_ok=True, mode=0o700)
    failed = False
    for path in paths if paths else sorted(inbox.rglob('*.json')):
        try:
            count = import_file(db, path)
            print(f'Import: {count} records')
        except (ValueError, KeyError, TypeError, OSError, OverflowError):
            # Never print message contents, filenames, credentials or exception text.
            print('Import failed: invalid or unreadable export; original retained')
            failed = True
    return failed


def settings():
    path = HOME / 'config.json'
    if not path.exists():
        raise ValueError('Run setup first')
    config = json.loads(path.read_text(encoding='utf-8'))
    if not config.get('api_id') or not config.get('api_hash') or not config.get('chats'):
        raise ValueError('Incomplete Telegram configuration')
    return config


def target_scope(target):
    """Legacy scalar = entire explicitly selected chat; dict can restrict a topic."""
    if not isinstance(target, dict):
        return target, None
    if set(target) - {'chat_id', 'topic_id'} or 'chat_id' not in target:
        raise ValueError('Invalid source scope')
    cid = target['chat_id']
    if isinstance(cid, bool) or not str(cid).lstrip('-').isdigit():
        raise ValueError('Numeric chat ID required for scoped source')
    topic = target.get('topic_id')
    if topic is not None and (type(topic) is not int or topic <= 1):
        raise ValueError('Explicit non-General forum topic ID required')
    return cid, topic


def message_in_topic(message, topic):
    if message.id == topic:
        return True
    reply = getattr(message, 'reply_to', None)
    return bool(reply and getattr(reply, 'forum_topic', False)
                and (getattr(reply, 'reply_to_top_id', None) or getattr(reply, 'reply_to_msg_id', None)) == topic)


async def collect(db, config):
    from telethon import TelegramClient, utils
    from telethon.tl.types import Message
    session = DATA / 'session' / 'user'
    session.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    client = TelegramClient(str(session), int(config['api_id']), config['api_hash'],
                            receive_updates=False, flood_sleep_threshold=60)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise ValueError('Telegram login required: run setup')
        # Cache dialogs so numeric IDs also work for private chats already joined.
        entities = {utils.get_peer_id(d.entity): d.entity async for d in client.iter_dialogs()}
        for target in config['chats']:
            target, topic = target_scope(target)
            numeric = int(target) if str(target).lstrip('-').isdigit() else None
            entity = entities.get(numeric)
            if entity is None:
                entity = await client.get_entity(numeric if numeric is not None else target)
            cid = str(utils.get_peer_id(entity))
            if topic is not None and not getattr(entity, 'forum', False):
                raise ValueError('Scoped forum source is no longer a forum')
            chat = {'id': cid, 'name': utils.get_display_name(entity), 'type': 'telethon'}
            cursor_key = cid if topic is None else f'{cid}:topic:{topic}'
            row = db.execute('SELECT last_id FROM cursors WHERE chat_id=?', (cursor_key,)).fetchone()
            last = row[0] if row else 0
            overlap = max(0, last - int(config.get('edit_overlap_ids', 1000)))
            # Commits in batches: checkpoint advances only with durable records.
            pending = []
            seen = last
            query = {'min_id': overlap, 'reverse': True, 'limit': None}
            if topic is not None:
                query['reply_to'] = topic
            async for msg in client.iter_messages(entity, **query):
                # Fail closed if the remote response crosses the allowed topic.
                if topic is not None and not message_in_topic(msg, topic):
                    raise ValueError('Message outside allowed forum topic')
                seen = max(seen, msg.id)
                record = None
                if isinstance(msg, Message):
                    sender = await msg.get_sender()
                    record = normalize(chat, {
                        'type': 'message', 'id': msg.id, 'text': msg.message or '',
                        'date': msg.date.isoformat(),
                        'edited': msg.edit_date.isoformat() if msg.edit_date else None,
                        'from': utils.get_display_name(sender) if sender else msg.post_author,
                        'from_id': str(msg.sender_id or ''), 'reply_to_message_id': msg.reply_to_msg_id,
                        'topic_id': topic,
                    })
                pending.append((msg.id, record))
                if len(pending) >= 100:
                    save_batch(db, cid, pending, seen, cursor_key=cursor_key)
                    pending = []
            if pending:
                save_batch(db, cid, pending, seen, cursor_key=cursor_key)
    finally:
        await client.disconnect()


def save_batch(db, cid, pending, seen, cursor_key=None):
    with db:
        for mid, record in pending:
            if record:
                upsert(db, record)
            else:
                # A recent message edited to empty should leave the text dataset.
                db.execute('DELETE FROM messages WHERE source_key=?', (f'telegram:{cid}:{mid}',))
        db.execute('INSERT INTO cursors VALUES (?, ?) ON CONFLICT(chat_id) DO UPDATE SET last_id=MAX(last_id, excluded.last_id)', (cursor_key or cid, seen))


async def setup():
    from telethon import TelegramClient
    config = json.loads((HOME / 'config.json').read_text()) if (HOME / 'config.json').exists() else {}
    if not config.get('api_id'):
        config['api_id'] = int(getpass.getpass('TG_API_ID (hidden): '))
        config['api_hash'] = getpass.getpass('TG_API_HASH (hidden): ').strip()
    if not config.get('chats'):
        config['chats'] = json.loads((ROOT / 'targets.json').read_text(encoding='utf-8'))
    config.setdefault('edit_overlap_ids', 1000)
    if not config['chats']:
        raise ValueError('At least one target chat is required')
    atomic_json(HOME / 'config.json', config)
    session = DATA / 'session' / 'user'
    session.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    client = TelegramClient(str(session), config['api_id'], config['api_hash'])
    try:
        await client.start(phone=lambda: getpass.getpass('Phone number (hidden): '),
                           code_callback=lambda: getpass.getpass('Telegram code (hidden): '),
                           password=lambda: getpass.getpass('Telegram 2FA password (hidden): '))
        me = await client.get_me()
        if me.bot:
            raise ValueError('User account required')
        print('Telegram user account authorized')
    finally:
        await client.disconnect()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['import', 'run', 'setup', 'status', 'backup'])
    parser.add_argument('paths', nargs='*', type=Path)
    args = parser.parse_args()
    try:
        with locked():
            if args.command == 'setup':
                asyncio.run(setup())
                return 0
            db = connect()
            try:
                if args.command == 'status':
                    print(json.dumps({'messages': db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],
                                      'imports': db.execute('SELECT COUNT(*) FROM imports').fetchone()[0],
                                      'configured': (HOME / 'config.json').exists(),
                                      'last_run': json.loads((DATA / 'status.json').read_text()) if (DATA / 'status.json').exists() else None}))
                    return 0
                if args.command == 'backup':
                    folder = HOME / 'backups'
                    folder.mkdir(exist_ok=True, mode=0o700)
                    dest = sqlite3.connect(folder / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.sqlite3'))
                    try:
                        db.backup(dest)
                    finally:
                        dest.close()
                    print('Database backup complete')
                    return 0
                failed = import_inbox(db, args.paths)
                if args.command == 'run' and (HOME / 'config.json').exists():
                    asyncio.run(collect(db, settings()))
                count = export(db)
                atomic_json(DATA / 'status.json', {'finished_at': datetime.now(timezone.utc).isoformat(),
                    'messages': count, 'mode': 'telegram' if args.command == 'run' and (HOME / 'config.json').exists() else 'import_only',
                    'ok': not failed})
                print(f'Export: {count} records')
                return int(failed)
            finally:
                db.close()
    except Exception as exc:
        # Do not emit exception messages: Telegram errors can contain sensitive input.
        atomic_json(DATA / 'error.json', {'at': datetime.now(timezone.utc).isoformat(), 'error_type': type(exc).__name__})
        print(f'Failed ({type(exc).__name__}); check configuration, authorization or connection')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

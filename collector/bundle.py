"""Bounded, immutable Telegram API captures. No model calls or report DB writes."""
from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import date, datetime, time, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid
from zoneinfo import ZoneInfo

import accounts
from ingest import atomic_json, message_in_topic, target_scope

UTC = timezone.utc
MOSCOW = ZoneInfo('Europe/Moscow')
MAX_LINE_BYTES = 4 * 1024 * 1024
MAX_SOURCE_MESSAGES = 100_000


def now():
    return datetime.now(UTC).replace(microsecond=0)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def json_value(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError('Naive Telegram timestamp')
        return value.astimezone(UTC).isoformat()
    if isinstance(value, bytes):
        return {'_bytes_base64': base64.b64encode(value).decode('ascii')}
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError('Unsupported raw Telegram field')


def bounds(first, end):
    start = datetime.combine(date.fromisoformat(first), time(), MOSCOW).astimezone(UTC)
    if end.tzinfo is None or start >= end:
        raise ValueError('Invalid capture period')
    return start, end.astimezone(UTC)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def new_bundle(base, first, end):
    start, end = bounds(first, end)
    sources = []
    for slot in accounts.SLOTS:
        home = accounts.account_home(slot, base)
        registered = accounts.identity(home)
        if not registered:
            raise accounts.SetupError(f'{slot}: login required')
        config = read_json(home / 'config.json')
        if not config.get('chats'):
            raise ValueError('No explicitly configured sources')
        for target in config['chats']:
            cid, topic = target_scope(target)
            if not str(cid).lstrip('-').isdigit():
                raise ValueError('Resolved numeric chat IDs required')
            key = f'{slot}--{cid}' + (f'--topic-{topic}' if topic else '')
            if any(s['key'] == key for s in sources):
                raise ValueError('Duplicate source scope')
            sources.append({'key': key, 'account_id': slot, 'account_user_id': registered['telegram_user_id'],
                            'chat_id': str(cid), 'topic_id': topic})
            tenant = config.get('tenant_id')
            if tenant is not None:
                from partition import CONTEXT
                if tenant != CONTEXT[slot][0]:
                    raise ValueError('Configured account tenant mismatch')
                sources[-1]['tenant_id'] = tenant
    if len({accounts.identity(accounts.account_home(s, base))['telegram_user_id'] for s in accounts.SLOTS}) != 2:
        raise ValueError('Two distinct account identities required')
    run_id = end.strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:10]
    root = base / 'bundles' / run_id
    root.mkdir(parents=True, mode=0o700)
    (root / 'sources').mkdir(mode=0o700)
    request = {'schema_version': 1, 'kind': 'telegram-api-raw-bundle', 'bundle_id': run_id,
               'created_at': now().isoformat(), 'timezone': 'Europe/Moscow',
               'from_inclusive': start.isoformat(), 'to_exclusive': end.isoformat(),
               'sources': sources,
               'limitations': ['Only history currently accessible to these accounts is known.',
                               'Past edits/deletions cannot be reconstructed retroactively.',
                               'Current observed revisions; not an atomic historical Telegram snapshot.',
                               'Media metadata retained; attachment files not downloaded.',
                               'Protected original evidence, not a secret-redacted model view.',
                               'Raw hashes are not yet PostgreSQL evidence revision hashes.']}
    atomic_json(root / 'request.json', request)
    atomic_json(root / 'progress.json', {'request_sha256': digest(request), 'sources': {}, 'attempts': []})
    return root


def resume_bundle(base, run_id):
    if not run_id or any(c not in '0123456789TZabcdef-' for c in run_id):
        raise ValueError('Invalid bundle ID')
    root = base / 'bundles' / run_id
    if not root.is_dir():
        raise ValueError('Bundle does not exist')
    return root


def raw_record(msg, source):
    from telethon import utils
    raw = json_value(msg.to_dict())
    sender = getattr(msg, 'sender', None)
    return {'schema_version': 1, 'account_id': source['account_id'], 'chat_id': source['chat_id'],
            'topic_id': source['topic_id'], 'message_id': str(msg.id),
            'sent_at': json_value(msg.date), 'edited_at': json_value(getattr(msg, 'edit_date', None)),
            'observed_at': now().isoformat(),
            'sender_id': str(msg.sender_id) if getattr(msg, 'sender_id', None) is not None else None,
            'sender_name': utils.get_display_name(sender) if sender else getattr(msg, 'post_author', None),
            'raw_sha256': digest(raw), 'raw': raw}


def verify_source(root, source, summary, request):
    relative = Path(summary['path'])
    if relative.is_absolute() or len(relative.parts) != 2 or relative.parts[0] != 'sources' or '..' in relative.parts:
        raise ValueError('Invalid capture path')
    path = root / relative
    hasher = hashlib.sha256()
    seen = set()
    start = datetime.fromisoformat(request['from_inclusive'])
    end = datetime.fromisoformat(request['to_exclusive'])
    with path.open('rb') as handle:
        while line := handle.readline(MAX_LINE_BYTES + 1):
            if len(line) > MAX_LINE_BYTES:
                raise ValueError('Oversized raw record')
            hasher.update(line)
            row = json.loads(line)
            if (row['account_id'], row['chat_id'], row['topic_id']) != (source['account_id'], source['chat_id'], source['topic_id']):
                raise ValueError('Source scope mismatch')
            if not start <= datetime.fromisoformat(row['sent_at']) < end:
                raise ValueError('Message outside requested dates')
            if digest(row['raw']) != row['raw_sha256'] or row['message_id'] in seen:
                raise ValueError('Raw hash mismatch or duplicate message')
            if source['topic_id'] is not None:
                raw = row['raw']; reply = raw.get('reply_to') or {}
                actual = reply.get('reply_to_top_id') or reply.get('reply_to_msg_id')
                if int(row['message_id']) != source['topic_id'] and (not reply.get('forum_topic') or actual != source['topic_id']):
                    raise ValueError('Raw topic mismatch')
            seen.add(row['message_id'])
    if len(seen) != summary['messages'] or hasher.hexdigest() != summary['sha256']:
        raise ValueError('Capture checksum/count mismatch')


async def capture_source(client, source, request, path):
    from telethon import utils
    entity = await client.get_entity(int(source['chat_id']))
    if str(utils.get_peer_id(entity)) != source['chat_id']:
        raise ValueError('Resolved entity mismatch')
    topic = source['topic_id']
    if topic is not None and not getattr(entity, 'forum', False):
        raise ValueError('Expected a forum')
    start = datetime.fromisoformat(request['from_inclusive'])
    end = datetime.fromisoformat(request['to_exclusive'])
    query = {'offset_date': end, 'reverse': False, 'limit': None, 'wait_time': 1}
    if topic is not None:
        query['reply_to'] = topic
    count = edited = services = media = 0
    earliest = latest = None
    hasher = hashlib.sha256()
    stop = 'history_exhausted'
    with path.open('xb') as out:
        try:
            async for msg in client.iter_messages(entity, **query):
                if msg.date is None or msg.date.tzinfo is None:
                    raise ValueError('Message without an aware timestamp')
                if msg.date >= end:
                    continue
                if msg.date < start:
                    stop = 'before_start'
                    break
                if topic is not None and not message_in_topic(msg, topic):
                    raise ValueError('Message outside allowed topic')
                if count >= MAX_SOURCE_MESSAGES:
                    raise ValueError('Capture limit exceeded; no partial success')
                row = raw_record(msg, source)
                line = (canonical(row) + '\n').encode('utf-8')
                if len(line) > MAX_LINE_BYTES:
                    raise ValueError('Raw record too large')
                out.write(line); hasher.update(line)
                count += 1
                edited += bool(row['edited_at'])
                services += row['raw'].get('_') == 'MessageService'
                media += bool(row['raw'].get('media'))
                earliest = row['sent_at'] if earliest is None else min(earliest, row['sent_at'])
                latest = row['sent_at'] if latest is None else max(latest, row['sent_at'])
                if count % 100 == 0:
                    out.flush(); os.fsync(out.fileno())
                    print(f"{source['key']}: captured {count}", flush=True)
        finally:
            out.flush(); os.fsync(out.fileno())
    return {'path': str(Path('sources') / path.name).replace('\\', '/'),
            'messages': count, 'edited_messages': edited, 'service_messages': services,
            'media_metadata_messages': media, 'earliest_sent_at': earliest, 'latest_sent_at': latest,
            'sha256': hasher.hexdigest(), 'enumeration_stop': stop, 'truncated': False,
            'chat_title': utils.get_display_name(entity), 'finished_at': now().isoformat()}


def verify_bundle(root):
    request = read_json(root / 'request.json')
    manifest = read_json(root / 'manifest.json')
    if digest(request) != manifest['request_sha256'] or manifest['status'] != 'complete':
        raise ValueError('Invalid manifest')
    if set(manifest['sources']) != {s['key'] for s in request['sources']}:
        raise ValueError('Incomplete source set')
    for source in request['sources']:
        verify_source(root, source, manifest['sources'][source['key']], request)
    if manifest['total_messages'] != sum(s['messages'] for s in manifest['sources'].values()):
        raise ValueError('Invalid total count')
    return manifest


def publish_complete(root, base, manifest):
    """Recover the completion pointer after a crash without moving it backwards."""
    request = read_json(root / 'request.json')
    pointer = base / 'last-complete-bundle.json'
    if pointer.exists():
        current = read_json(pointer)
        if datetime.fromisoformat(current['to_exclusive']) > datetime.fromisoformat(request['to_exclusive']):
            return
    value = {'bundle_id': request['bundle_id'], 'to_exclusive': request['to_exclusive'],
             'manifest_sha256': hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest()}
    if not pointer.exists() or read_json(pointer) != value:
        atomic_json(pointer, value)


async def capture_bundle(root, base):
    request = read_json(root / 'request.json')
    if (root / 'manifest.json').exists():
        manifest = verify_bundle(root)
        publish_complete(root, base, manifest)
        print('Bundle already complete and verified; no Telegram calls needed.')
        return
    progress = read_json(root / 'progress.json')
    if digest(request) != progress['request_sha256']:
        raise ValueError('Capture request was changed')
    for slot in accounts.SLOTS:
        sources = [s for s in request['sources'] if s['account_id'] == slot]
        pending = []
        for source in sources:
            if source['key'] in progress['sources']:
                verify_source(root, source, progress['sources'][source['key']], request)
            else:
                pending.append(source)
        if not pending:
            continue
        home = accounts.account_home(slot, base)
        with accounts.locked_file(home / 'data/run.lock'):
            client = accounts.client_for(slot, base)
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    raise ValueError('Telegram login required')
                me = await client.get_me()
                accounts.validate_identity(slot, me, base)
                if any(str(me.id) != s['account_user_id'] for s in pending):
                    raise ValueError('Capture account was changed')
                for source in pending:
                    attempt_id = uuid.uuid4().hex
                    path = root / 'sources' / f"{source['key']}--{attempt_id}.jsonl"
                    attempt = {'source': source['key'], 'path': f'sources/{path.name}',
                               'started_at': now().isoformat(), 'status': 'running'}
                    progress['attempts'].append(attempt)
                    atomic_json(root / 'progress.json', progress)
                    try:
                        summary = await capture_source(client, source, request, path)
                        verify_source(root, source, summary, request)
                        progress['sources'][source['key']] = summary
                        attempt['status'] = 'complete'
                    except BaseException as exc:
                        attempt['status'] = 'failed'
                        attempt['error_type'] = type(exc).__name__
                        if type(exc).__name__ == 'FloodWaitError':
                            attempt['retry_after_seconds'] = int(exc.seconds)
                        raise
                    finally:
                        attempt['finished_at'] = now().isoformat()
                        atomic_json(root / 'progress.json', progress)
                    print(f"{source['key']}: complete, messages={summary['messages']}, edited={summary['edited_messages']}", flush=True)
            finally:
                await client.disconnect()
    manifest = {'schema_version': 1, 'status': 'complete', 'bundle_id': request['bundle_id'],
                'request_sha256': digest(request), 'completed_at': now().isoformat(),
                'sources': progress['sources'], 'total_messages': sum(s['messages'] for s in progress['sources'].values())}
    atomic_json(root / 'manifest.json', manifest)
    verify_bundle(root)
    publish_complete(root, base, manifest)
    print(f"Bundle verified: {request['bundle_id']}; messages={manifest['total_messages']}", flush=True)


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--from-date')
    group.add_argument('--resume')
    group.add_argument('--verify')
    args = parser.parse_args(argv)
    root = None
    try:
        with accounts.locked_file(accounts.BASE / 'bundle-capture.lock'):
            if args.verify:
                result = verify_bundle(resume_bundle(accounts.BASE, args.verify))
                print(f"Verified {result['bundle_id']}: {result['total_messages']} messages")
                return 0
            root = new_bundle(accounts.BASE, args.from_date, now()) if args.from_date else resume_bundle(accounts.BASE, args.resume)
            print(f'Bundle ID: {root.name}', flush=True)
            asyncio.run(capture_bundle(root, accounts.BASE))
            from partition import ensure_inputs
            ensure_inputs(root)
            print('Separate account-inputs/account-1.json and account-2.json verified.', flush=True)
        return 0
    except BaseException as exc:
        if isinstance(exc, SystemExit):
            raise
        error = {'error_type': type(exc).__name__, 'at': now().isoformat()}
        if type(exc).__name__ == 'FloodWaitError':
            error['retry_after_seconds'] = int(exc.seconds)
        if root is not None:
            atomic_json(root / 'last-error.json', error)
        print('Capture failed: ' + canonical(error) + '. Raw attempts retained; resume the same bundle.', flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())

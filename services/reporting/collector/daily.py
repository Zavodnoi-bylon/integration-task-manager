"""Daily raw collection for both accounts; no model, importer or task DB writes."""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import os
from pathlib import Path

import accounts
import bundle
import partition
from ingest import atomic_json, target_scope

FIRST_DATE = date(2026, 10, 1)
EDIT_LOOKBACK_DAYS = 7


def completed_day_end(instant):
    if instant.tzinfo is None:
        raise ValueError('Timezone required')
    return datetime.combine(instant.astimezone(bundle.MOSCOW).date(), time(),
                            bundle.MOSCOW).astimezone(timezone.utc)


def scope(base):
    """Fingerprint only identity/allowed sources, never credentials."""
    result = []
    for slot, (tenant, _, _) in partition.CONTEXT.items():
        home = accounts.account_home(slot, base)
        registered = accounts.identity(home)
        config = bundle.read_json(home / 'config.json')
        if not registered or config.get('tenant_id') != tenant or not config.get('chats'):
            raise ValueError('Account identity, tenant and allowed sources are required')
        for target in config['chats']:
            cid, topic = target_scope(target)
            result.append({'account_id': slot, 'account_user_id': registered['telegram_user_id'],
                           'tenant_id': tenant, 'chat_id': str(cid), 'topic_id': topic})
    return sorted(result, key=bundle.canonical)


def request_scope(request):
    return sorted([{key: source.get(key) for key in
                    ('account_id', 'account_user_id', 'tenant_id', 'chat_id', 'topic_id')}
                   for source in request['sources']], key=bundle.canonical)


def read_time(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('Checkpoint timezone required')
    return result.astimezone(timezone.utc)


def initial_state(base, configured_scope):
    state = {'schema_version': 1, 'scope_sha256': bundle.digest(configured_scope),
             'watermark': datetime.combine(FIRST_DATE, time(), bundle.MOSCOW).astimezone(
                 timezone.utc).isoformat(), 'active_bundle': None, 'last_success': None}
    pointer = base / 'last-complete-bundle.json'
    if pointer.exists():
        prior = bundle.read_json(pointer)
        root = bundle.resume_bundle(base, prior['bundle_id'])
        bundle.verify_bundle(root)
        request = bundle.read_json(root / 'request.json')
        if hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest() != prior['manifest_sha256']:
            raise ValueError('Previous bundle pointer checksum mismatch')
        if prior['to_exclusive'] != request['to_exclusive']:
            raise ValueError('Previous bundle pointer period mismatch')
        # A changed source set needs a fresh bounded capture from FIRST_DATE.
        if request_scope(request) == configured_scope:
            partition.ensure_inputs(root)
            state['watermark'] = prior['to_exclusive']
            state['last_success'] = root.name
    return state


async def run_daily(base, end):
    """Caller holds bundle-capture.lock. Resume first, then cover any missed days."""
    configured = scope(base)
    state_path = base / 'daily' / 'state.json'
    state = bundle.read_json(state_path) if state_path.exists() else initial_state(base, configured)
    if state.get('schema_version') != 1 or state['scope_sha256'] != bundle.digest(configured):
        raise ValueError('Source configuration changed; review coverage before resetting checkpoint')
    if read_time(state['watermark']) > bundle.now():
        raise ValueError('Checkpoint is in the future')
    if state.get('retry_not_before') and read_time(state['retry_not_before']) > bundle.now():
        raise ValueError('Telegram retry delay has not elapsed')
    # At most one previous interrupted interval and one catch-up interval per call.
    for _ in range(2):
        if state.get('active_bundle'):
            root = bundle.resume_bundle(base, state['active_bundle'])
            request = bundle.read_json(root / 'request.json')
            if request_scope(request) != configured:
                raise ValueError('Pending bundle scope differs from configured sources')
            if read_time(request['to_exclusive']) > end:
                raise ValueError('Pending bundle is beyond the last complete day')
        elif read_time(state['watermark']) >= end:
            atomic_json(state_path, state)
            print('DAILY_UP_TO_DATE: both accounts already covered; no Telegram calls.', flush=True)
            return state
        else:
            first = max(FIRST_DATE, read_time(state['watermark']).astimezone(bundle.MOSCOW).date()
                        - timedelta(days=EDIT_LOOKBACK_DAYS))
            root = bundle.new_bundle(base, first.isoformat(), end)
            state['active_bundle'] = root.name
            atomic_json(state_path, state)
            request = bundle.read_json(root / 'request.json')
        print(f'DAILY_BUNDLE: {root.name}', flush=True)
        try:
            await bundle.capture_bundle(root, base)
            outputs = partition.ensure_inputs(root)
            manifest = bundle.verify_bundle(root)
        except BaseException as exc:
            state['last_error'] = {'error_type': type(exc).__name__, 'at': bundle.now().isoformat()}
            if type(exc).__name__ == 'FloodWaitError':
                state['retry_not_before'] = (bundle.now() + timedelta(seconds=int(exc.seconds))).isoformat()
            atomic_json(state_path, state)
            raise
        receipt = {'schema_version': 1, 'bundle_id': root.name, 'status': 'collected',
                   'from_inclusive': request['from_inclusive'], 'to_exclusive': request['to_exclusive'],
                   'completed_at': bundle.now().isoformat(), 'accounts': outputs,
                   'manifest_sha256': hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest(),
                   'total_messages': manifest['total_messages'],
                   'analysis_status': 'not_started', 'report_database_modified': False}
        receipt_path = base / 'daily' / 'runs' / f'{root.name}.json'
        if not receipt_path.exists():
            atomic_json(receipt_path, receipt)
        state.update(watermark=request['to_exclusive'], active_bundle=None, last_success=root.name)
        state.pop('last_error', None)
        state.pop('retry_not_before', None)
        atomic_json(state_path, state)
        print(f"DAILY_COMPLETE: {root.name}; messages={manifest['total_messages']}; accounts=2", flush=True)
    return state


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', action='store_true', help='Read collection checkpoint without network calls')
    args = parser.parse_args(argv)
    try:
        if args.status:
            path = accounts.BASE / 'daily' / 'state.json'
            print(bundle.canonical(bundle.read_json(path) if path.exists() else {'status': 'not_started'}))
            return 0
        with accounts.locked_file(accounts.BASE / 'bundle-capture.lock'):
            asyncio.run(run_daily(accounts.BASE, completed_day_end(bundle.now())))
        return 0
    except Exception as exc:
        # Exception text can expose Telegram/account values; log the type only.
        print(f'DAILY_FAILED: {type(exc).__name__}; checkpoint retained.', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

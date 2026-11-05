"""Publish separate immutable account inputs, preserving the verified raw capture."""
import argparse
import hashlib
import json
import os
import uuid
from pathlib import Path
import accounts
import bundle

CONTEXT = {'account-1': ('traffic-angels', 'Traffic Angels', 'Kalisto'),
           'account-2': ('theraw', 'TheRaw', 'mephisto')}


def partition(root, output, manual=()):
    manifest = bundle.verify_bundle(root)
    request = bundle.read_json(root / 'request.json')
    inputs = {}
    for account, (tenant, name, alias) in CONTEXT.items():
        sources = [dict(s, display_name=manifest['sources'][s['key']].get('chat_title', s['chat_id']))
                   for s in request['sources'] if s['account_id'] == account]
        if not sources or any(s.get('tenant_id', tenant) != tenant for s in sources):
            raise ValueError('Account/tenant scope mismatch')
        rows = []
        for source in sources:
            info = manifest['sources'][source['key']]
            rows.extend(json.loads(line) for line in (root / info['path']).read_text(encoding='utf-8').splitlines())
        if any(r['account_id'] != account for r in rows):
            raise ValueError('Mixed accounts')
        inputs[account] = {'schema_version': 1, 'kind': 'telegram-account-input',
                           'tenant_id': tenant, 'tenant_name': name, 'account_id': account,
                           'account_alias': alias, 'bundle_id': manifest['bundle_id'],
                           'manifest_sha256': hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest(),
                           'from_inclusive': request['from_inclusive'], 'to_exclusive': request['to_exclusive'],
                           'api_sources': sources, 'api_messages': rows, 'manual_exports': []}
    for account, path in manual:
        if account not in inputs: raise ValueError('Unknown manual account')
        raw = path.read_bytes()
        document = json.loads(raw.decode('utf-8-sig'))
        if not isinstance(document.get('messages'), list): raise ValueError('Single-chat manual export required')
        inputs[account]['manual_exports'].append({'filename': path.name,
            'content_sha256': hashlib.sha256(raw).hexdigest(), 'collection_mode': 'manual-once', 'document': document})
    # Exclusive files in a fresh directory: never overwrite a previous analysis input.
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    result = {}
    for account, value in inputs.items():
        data = (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n').encode('utf-8')
        target = output / f'{account}.json'
        with target.open('xb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        result[account] = {'path': target.name, 'tenant_id': value['tenant_id'],
                           'sha256': hashlib.sha256(data).hexdigest(),
                           'api_messages': len(value['api_messages']),
                           'manual_messages': sum(len(x['document']['messages']) for x in value['manual_exports'])}
    bundle.atomic_json(output / 'manifest.json', {'schema_version': 1, 'status': 'complete', 'accounts': result})
    return result


def verify_inputs(root, output):
    manifest = bundle.verify_bundle(root)
    index = bundle.read_json(output / 'manifest.json')
    if index.get('status') != 'complete' or set(index.get('accounts', {})) != set(CONTEXT):
        raise ValueError('Incomplete account inputs')
    parent_hash = hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest()
    for account, (tenant, _, _) in CONTEXT.items():
        info = index['accounts'][account]
        if info['path'] != f'{account}.json': raise ValueError('Account input path mismatch')
        data = (output / info['path']).read_bytes()
        value = json.loads(data)
        if hashlib.sha256(data).hexdigest() != info['sha256']:
            raise ValueError('Account input checksum mismatch')
        if value['account_id'] != account or value['tenant_id'] != tenant or value['manifest_sha256'] != parent_hash or value['bundle_id'] != manifest['bundle_id']:
            raise ValueError('Account input identity mismatch')
    return index['accounts']


def ensure_inputs(root):
    output = root / 'account-inputs'
    if output.exists(): return verify_inputs(root, output)
    # Publish a complete pair atomically. Failed staging directories remain for audit.
    staging = root / ('.account-inputs-' + uuid.uuid4().hex)
    partition(root, staging)
    verify_inputs(root, staging)
    staging.rename(output)
    return verify_inputs(root, output)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle_id')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--manual-account-1', action='append', default=[], type=Path)
    parser.add_argument('--manual-account-2', action='append', default=[], type=Path)
    args = parser.parse_args()
    manual = [('account-1', p) for p in args.manual_account_1] + [('account-2', p) for p in args.manual_account_2]
    root = bundle.resume_bundle(accounts.BASE, args.bundle_id)
    print(json.dumps(partition(root, args.output, manual), ensure_ascii=False))


if __name__ == '__main__':
    main()

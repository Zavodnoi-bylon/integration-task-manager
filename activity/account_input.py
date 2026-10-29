"""Account-scoped raw API + explicit one-off Desktop inputs; no inference."""
from datetime import datetime
from .ingest import (ImportValidationError, normalize_document, raw_id,
                     sha256_json)
from .tenants import validate_context


def normalize_account_input(document, *, source_name, content_hash):
    tenant, account = validate_context(document.get('tenant_id'), document.get('account_id'))
    if document.get('schema_version') != 1:
        raise ImportValidationError('account_input_schema_version')
    sources, messages = {}, []
    declared = {}
    for source in document['api_sources']:
        if source.get('account_id') != account or source.get('tenant_id', tenant) != tenant:
            raise ImportValidationError('account_input_source_context')
        cid = raw_id(source['chat_id'], 'chat_id')
        declared[cid] = source.get('topic_id')
        sid = f'telegram-api:{account}:{cid}'
        sources[sid] = {'source_id': sid, 'external_id': cid, 'kind': 'telegram-api',
                        'display_name': source.get('display_name') or cid,
                        'coverage_status': 'partial', 'tenant_id': tenant, 'account_id': account}
    for row in document['api_messages']:
        raw = row['raw']
        cid = raw_id(row['chat_id'], 'chat_id')
        mid = raw_id(row['message_id'], 'message_id')
        if row['account_id'] != account or cid not in declared or row.get('topic_id') != declared[cid]:
            raise ImportValidationError('account_input_message_scope')
        if row['raw_sha256'] != sha256_json(raw) or str(raw['id']) != mid or raw['date'] != row['sent_at']:
            raise ImportValidationError('account_input_raw_mismatch')
        peer = raw['peer_id']
        signed = (str(peer['user_id']) if peer['_'] == 'PeerUser' else
                  str(-peer['chat_id']) if peer['_'] == 'PeerChat' else
                  str(-1000000000000-peer['channel_id']) if peer['_'] == 'PeerChannel' else None)
        if signed != cid:
            raise ImportValidationError('account_input_raw_peer_mismatch')
        reply = raw.get('reply_to') or {}
        topic = declared[cid]
        if topic is not None and int(mid) != topic and not (
            reply.get('forum_topic') and (reply.get('reply_to_top_id') or reply.get('reply_to_msg_id')) == topic
        ):
            raise ImportValidationError('account_input_raw_topic_mismatch')
        sent, edited = row['sent_at'], raw.get('edit_date')
        for timestamp in (sent, edited):
            if timestamp and datetime.fromisoformat(timestamp).tzinfo is None:
                raise ImportValidationError('account_input_timestamp_timezone')
        if edited and datetime.fromisoformat(edited) < datetime.fromisoformat(sent):
            raise ImportValidationError('account_input_edit_before_sent')
        when = datetime.fromisoformat(sent)
        if not datetime.fromisoformat(document['from_inclusive']) <= when < datetime.fromisoformat(document['to_exclusive']):
            raise ImportValidationError('account_input_timestamp_bounds')
        sid = f'telegram-api:{account}:{cid}'
        messages.append({'message_key': f'{sid}:{mid}', 'source_id': sid, 'external_id': mid,
            'revision_hash': row['raw_sha256'], 'sent_at': sent, 'edited_at': edited,
            'source_updated_at': edited or sent, 'text_plain': raw.get('message') or '',
            'sender_id': str(row['sender_id']) if row.get('sender_id') is not None else None,
            'sender_name': row.get('sender_name'),
            'reply_to_external_id': str(reply['reply_to_msg_id']) if reply.get('reply_to_msg_id') else None,
            'source_url': None, 'media_missing': raw.get('media') is not None, 'raw_json': raw,
            'entity_links': [{'href': e['url']} for e in raw.get('entities') or [] if e.get('url')]})
    for item in document.get('manual_exports', []):
        if item.get('collection_mode') != 'manual-once':
            raise ImportValidationError('manual_input_mode')
        normalized = normalize_document(item['document'])
        for source in normalized['sources']:
            # Existing Desktop keys are preserved. A later account collision must fail binding.
            source.update(tenant_id=tenant, account_id=account)
            if source['source_id'] in sources:
                raise ImportValidationError('duplicate_manual_source')
            sources[source['source_id']] = source
        messages.extend(normalized['messages'])
    if len({m['message_key'] for m in messages}) != len(messages):
        raise ImportValidationError('duplicate_account_input_message')
    return {'schema_version': 1, 'kind': 'telegram-account-normalized',
            'parser_version': f'telegram-account/1;tenant={tenant};account={account}',
            'tenant_id': tenant, 'account_id': account, 'source_name': source_name,
            'content_hash': content_hash, 'sources': list(sources.values()), 'messages': messages,
            'coverage': {'status': 'partial', 'note': 'Explicit account sources and one-off manual exports only'},
            'counts': {'source_records': len(messages), 'unique_messages': len(messages),
                       'unique_revisions': len(messages),
                       'service_records': sum(m['raw_json'].get('type') == 'service' or m['raw_json'].get('_') == 'MessageService' for m in messages)}}

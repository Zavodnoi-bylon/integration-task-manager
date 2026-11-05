"""Explicit collection identities. Names/brands never determine a tenant."""
from .ingest import ImportValidationError

TENANTS = {
    'traffic-angels': {'name': 'Traffic Angels', 'account_id': 'account-1', 'alias': 'Kalisto'},
    'theraw': {'name': 'TheRaw', 'account_id': 'account-2', 'alias': 'mephisto'},
}


def validate_context(tenant_id, account_id):
    if not isinstance(tenant_id, str) or tenant_id not in TENANTS or TENANTS[tenant_id]['account_id'] != account_id:
        raise ImportValidationError('tenant_account_mismatch: explicit registered tenant/account required')
    return tenant_id, account_id


def apply_context(bundle, tenant_id, account_id):
    validate_context(tenant_id, account_id)
    bundle['tenant_id'], bundle['account_id'] = tenant_id, account_id
    bundle['parser_version'] += f';tenant={tenant_id};account={account_id}'
    for source in bundle['sources']:
        source.update(tenant_id=tenant_id, account_id=account_id)
    return bundle


def bind_source(cursor, source):
    tenant_id, account_id = source.get('tenant_id'), source.get('account_id')
    if tenant_id is not None or account_id is not None:
        validate_context(tenant_id, account_id)
        cursor.execute('UPDATE raw.sources SET tenant_id=%s,account_id=%s WHERE source_id=%s '
                       'AND (tenant_id IS NULL OR (tenant_id=%s AND account_id=%s))',
                       (tenant_id, account_id, source['source_id'], tenant_id, account_id))
        if cursor.rowcount != 1:
            raise ImportValidationError('source_tenant_conflict: source already belongs to another account/tenant')

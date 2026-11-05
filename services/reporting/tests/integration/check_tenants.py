"""Synthetic checks on the separately restored tenant verification DB; rollback all probes."""
import copy
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'))
import psycopg
from activity.ingest import normalize_document,ingest_bundle,sha256_json,StorageError,ImportValidationError
from activity.tenants import apply_context
from activity.proposals import ingest_proposals,review_proposal
from test_proposals import fixture
from test_tenants import TenantTests
from activity.account_input import normalize_account_input
from activity.task_status import change_status
from uuid import uuid4

cfg=dict(line.split('=',1) for line in (ROOT/'.env.client').read_text().splitlines() if line and not line.startswith('#'))
with psycopg.connect(host='127.0.0.1',port=55432,dbname='activity_tenants_verify_20261024',user='activity_writer',password=cfg['ACTIVITY_WRITER_PASSWORD']) as conn:
    with conn.transaction(force_rollback=True):
        baseline=conn.execute('SELECT count(*) FROM work.tasks').fetchone()[0]
        api=normalize_account_input(TenantTests().account_input(),source_name='synthetic-account-input',content_hash='e'*64)
        assert ingest_bundle(conn,api)['messages_inserted']==1
        assert conn.execute('SELECT tenant_id,account_id FROM raw.sources WHERE source_id=%s',('telegram-api:account-1:123',)).fetchone()==('traffic-angels','account-1')
        docs=[]
        for i,(tenant,account) in enumerate([('traffic-angels','account-1'),('theraw','account-2')]):
            doc,b=fixture()
            raw={'id':999000001+i,'name':'Synthetic same advertiser','messages':[m['raw_json'] for m in b['messages']]}
            b=apply_context(normalize_document(raw),tenant,account)
            assert ingest_bundle(conn,b)['messages_inserted']==2
            assert ingest_bundle(conn,b)['status']=='skipped'
            p=doc['proposals'][0]
            p.update(task_id=f'synthetic:tenant:{i}',proposal_id=f'synthetic:tenant:{i}:v1')
            p['external_refs']['tenant_id']=tenant
            p['external_refs']['destination_id']=f'00012{i}'
            for e,m in zip(p['events'],b['messages']):
                e['event_id']=f"synthetic:tenant:{i}:{e['kind']}"
                e['evidence']=[{'message_key':m['message_key'],'revision_hash':m['revision_hash']}]
            assert ingest_proposals(conn,doc,content_hash=sha256_json(doc),source_name='synthetic-tenants',allow_demo=True)['proposals_inserted']==1
            review_proposal(conn,p['proposal_id'],decision='accept',actor='Synthetic migration check')
            assert conn.execute('SELECT tenant_id FROM work.tasks WHERE task_id=%s',(p['task_id'],)).fetchone()[0]==tenant
            docs.append((doc,b))
        action={'status':'open','expected_revision':1,'request_id':str(uuid4()),'reason':'Synthetic reopen'}
        changed=change_status(conn,'synthetic:tenant:0','synthetic-owner',action)
        assert changed['revision']==2
        assert change_status(conn,'synthetic:tenant:0','synthetic-owner',action)['replayed'] is True
        close={'status':'completed','expected_revision':2,'request_id':str(uuid4())}
        assert change_status(conn,'synthetic:tenant:0','synthetic-owner',close)['revision']==3
        try:change_status(conn,'synthetic:tenant:0','synthetic-owner',dict(close,request_id=str(uuid4())))
        except ImportValidationError:pass
        else:raise AssertionError('Stale manual status changed the task')
        assert conn.execute("SELECT count(*) FROM work.events WHERE task_id='synthetic:tenant:0'").fetchone()[0]==4
        assert conn.execute('SELECT count(*) FROM work.tasks').fetchone()[0]==baseline+2
        for missing in [False,True]:
            bad=copy.deepcopy(docs[0][0]); p=bad['proposals'][0]
            p.update(task_id=f'synthetic:bad:{missing}',proposal_id=f'synthetic:bad:{missing}:v1')
            if missing: p['external_refs'].pop('tenant_id')
            else: p['external_refs']['tenant_id']='theraw'
            try:
                ingest_proposals(conn,bad,content_hash=sha256_json(bad),source_name='synthetic-cross',allow_demo=True)
            except StorageError: pass
            else: raise AssertionError('Cross/missing tenant evidence unexpectedly imported')
            assert conn.execute('SELECT count(*) FROM work.proposals WHERE proposal_id=%s',(p['proposal_id'],)).fetchone()[0]==0
        wrong=apply_context(normalize_document({'id':999000001,'name':'Wrong context','messages':[]}),'theraw','account-2')
        try: ingest_bundle(conn,wrong)
        except ImportValidationError: pass
        else: raise AssertionError('Cross-account source unexpectedly rebound')
        assert conn.execute('SELECT display_name,tenant_id FROM raw.sources WHERE external_id=%s',('999000001',)).fetchone()==('Synthetic same advertiser','traffic-angels')
        try:
            with conn.transaction():
                conn.execute("UPDATE work.tasks SET external_refs=jsonb_set(external_refs,'{tenant_id}','\"theraw\"') WHERE task_id='synthetic:tenant:0'")
        except psycopg.errors.RaiseException: pass
        else: raise AssertionError('Accepted task tenant unexpectedly changed')
    assert conn.execute('SELECT count(*) FROM work.tasks').fetchone()[0]==baseline
print('PASS: restored DB, both tenants, same brand separation, leading-zero IDs, idempotence, evidence guards, context immutability, atomic rollback, common task counts.')

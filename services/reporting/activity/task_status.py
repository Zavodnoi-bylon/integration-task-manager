"""Audited owner decisions: immutable manual evidence, proposal and lifecycle event."""
import copy
from datetime import datetime,timezone,timedelta
import hashlib
from uuid import UUID
from .ingest import ADVISORY_LOCK,ImportValidationError,canonical_json,sha256_json,begin_import,finish_import,writer_connection
from .proposals import _snapshot,ingest_proposals,review_proposal,ReviewConflict
from .domain import Timeline,OPEN_STATES


def validate_action(value):
    if not isinstance(value,dict) or set(value)-{'status','expected_revision','request_id','reason'}:
        raise ImportValidationError('invalid_status_action')
    if not isinstance(value.get('status'),str) or value['status'] not in {'open','completed'} or type(value.get('expected_revision')) is not int or value['expected_revision']<1:
        raise ImportValidationError('invalid_status_action')
    try:
        if str(UUID(value['request_id']))!=value['request_id']:raise ValueError()
    except (KeyError,ValueError,TypeError,AttributeError):raise ImportValidationError('invalid_request_id') from None
    if not isinstance(value.get('reason',''),str) or len(value.get('reason',''))>1000 or '\x00' in value.get('reason',''):
        raise ImportValidationError('invalid_status_reason')
    return dict(value,reason=value.get('reason','').strip())


def change_status(conn,task_id,actor,value):
    action=validate_action(value)
    if not isinstance(task_id,str) or not 1<=len(task_id)<=300:raise ImportValidationError('invalid_task_id')
    digest=sha256_json({'task_id':task_id,'actor':actor,**action})
    pid='manual-status:'+hashlib.sha256((actor+':'+action['request_id']).encode()).hexdigest()
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(%s)',(ADVISORY_LOCK,))
            cur.execute('SELECT task_id,document,state FROM work.proposals WHERE proposal_id=%s',(pid,))
            replay=cur.fetchone()
            if replay:
                if replay[0]!=task_id or replay[1]['external_refs'].get('manual_action_hash')!=digest or replay[2]!='accepted':
                    raise ReviewConflict('request_id_conflict')
                cur.execute("SELECT after_snapshot->>'revision' FROM ops.review_log WHERE proposal_id=%s AND decision='accept'",(pid,))
                return {'task_id':task_id,'revision':int(cur.fetchone()[0]),'replayed':True}
            before=_snapshot(cur,task_id)
            if before is None:raise ImportValidationError('task_not_found')
            if before['revision']!=action['expected_revision']:raise ReviewConflict('revision_conflict')
            now=datetime.now(timezone.utc)
            state=Timeline(before).state_at(now+timedelta(microseconds=1))
            close=action['status']=='completed'
            if (close and state in {'completed','cancelled'}) or (not close and state in OPEN_STATES):
                return {'task_id':task_id,'revision':before['revision'],'unchanged':True}
            tenant=before['external_refs'].get('tenant_id')
            cur.execute('SELECT account_id FROM ops.tenants WHERE tenant_id=%s',(tenant,))
            row=cur.fetchone()
            if row is None:raise ImportValidationError('task_tenant_required')
            account=row[0]
            run,_=begin_import(cur,'manual-task-status',digest,'dashboard owner decision','manual-task-status/1')
            source=f"manual:{tenant}:{hashlib.sha256(actor.encode()).hexdigest()[:16]}"
            key=source+':'+action['request_id']
            raw={'type':'manual-task-status','actor':actor,'task_id':task_id,'status':action['status'],
                 'request_id':action['request_id'],'reason':action['reason'],'date':now.isoformat()}
            revision=sha256_json(raw)
            label='Закрыта вручную' if close else 'Возвращена в работу вручную'
            text=f'{label}. Пользователь: {actor}.'+(' Комментарий: '+action['reason'] if action['reason'] else '')
            cur.execute('INSERT INTO raw.sources(source_id,kind,external_id,display_name,tenant_id,account_id) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(source_id) DO NOTHING',
                        (source,'manual',actor,'Ручные решения владельца',tenant,account))
            cur.execute('INSERT INTO raw.messages(message_key,source_id,external_id) VALUES (%s,%s,%s)',(key,source,action['request_id']))
            cur.execute('INSERT INTO raw.message_revisions(message_key,revision_hash,sent_at,source_updated_at,text_plain,sender_id,sender_name,raw_json,first_import_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)',
                        (key,revision,now,now,text,'dashboard:'+actor,actor,canonical_json(raw),run))
            cur.execute('UPDATE raw.messages SET current_revision_hash=%s WHERE message_key=%s',(revision,key))
            finish_import(cur,run,{'manual_events':1})
            cur.execute('SELECT coalesce(max(version),0)+1 FROM work.proposals WHERE task_id=%s',(task_id,));version=cur.fetchone()[0]
        p={k:copy.deepcopy(before[k]) for k in ['task_id','title','category','client','assignee','external_refs','events','evidence']}
        p.update(proposal_id=pid,version=version,note='Явное ручное решение владельца через дашборд. Предыдущая хронология сохранена.')
        p['external_refs']['manual_action_hash']=digest
        for e in p['events']:
            e['evidence']=[{k:r[k] for k in ['message_key','revision_hash']} for r in before['evidence'] if r.get('event_id')==e['event_id']]
        p['events'].append({'event_id':pid+':event','kind':'completed' if close else 'reopened',
                            'occurred_at':now.isoformat(),'time_basis':'event','evidence':[{'message_key':key,'revision_hash':revision}]})
        doc={'schema_version':1,'proposals':[p]}
        ingest_proposals(conn,doc,content_hash=sha256_json(doc),source_name='dashboard manual decision')
        reviewed=review_proposal(conn,pid,decision='accept',actor=actor,reason=text,expected_revision=action['expected_revision'])
        return {'task_id':task_id,'revision':reviewed['task_revision'],'status':action['status'],'replayed':False}


def apply_status_change(task_id,actor,value):
    from .ingest import StorageError
    try:
        with writer_connection() as conn:return change_status(conn,task_id,actor,value)
    except ImportValidationError:raise
    except Exception:raise StorageError('manual_status_write_failed') from None

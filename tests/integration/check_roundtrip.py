"""Local synthetic QA only; no remote database or credentials."""
import copy
import json
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / 'tests'))
import psycopg
from activity.ingest import ImportValidationError, ingest_bundle, normalize_document, sha256_json
from activity.proposals import ingest_proposals, review_proposal, ProposalValidationError, ReviewConflict
from test_proposals import fixture

checks = []
def ok(name, condition):
    assert condition, name
    checks.append(name)

conn = psycopg.connect(host='127.0.0.1', port=55432, user='postgres', dbname='postgres', sslmode='disable', autocommit=True, prepare_threshold=None)
conn.execute('SET ROLE activity_writer')
document, bundle = fixture()
result = ingest_bundle(conn, bundle)
ok('message_insert', result['messages_inserted'] == 2 and result['revisions_inserted'] == 2)
ok('file_hash_idempotence', ingest_bundle(conn, bundle)['status'] == 'skipped')
overlap_raw = {'id':100,'name':'Fictional renamed chat','messages':[copy.deepcopy(m['raw_json']) for m in bundle['messages']]}
overlap = normalize_document(overlap_raw)
result = ingest_bundle(conn, overlap)
ok('overlapping_export_revision_idempotence', result['messages_inserted'] == 0 and result['revisions_inserted'] == 0 and result['unchanged'] == 2)
edited_raw = copy.deepcopy(overlap_raw)
edited_raw['messages'][0].update({'text':'Fictional edited request','edited_unixtime':'1792490000'})
result = ingest_bundle(conn, normalize_document(edited_raw))
ok('newer_edit_import', result['revisions_inserted'] == 1 and result['current_updates'] == 1)
old_raw = copy.deepcopy(overlap_raw); old_raw['name'] = 'Fictional old export'
result = ingest_bundle(conn, normalize_document(old_raw))
row = conn.execute('SELECT r.text_plain FROM raw.messages m JOIN raw.message_revisions r ON (r.message_key,r.revision_hash)=(m.message_key,m.current_revision_hash) WHERE m.external_id=%s', ('1',)).fetchone()
ok('older_export_does_not_revert', row[0] == 'Fictional edited request' and result['older_revisions'] == 1)
conflict_raw = copy.deepcopy(edited_raw); conflict_raw['messages'][0]['text']='Fictional same-time other text'
ingest_bundle(conn, normalize_document(conflict_raw))
ok('same_time_conflict_preserved', conn.execute('SELECT has_conflict FROM raw.messages WHERE external_id=%s', ('1',)).fetchone()[0] is True)

def submit(doc):
    return ingest_proposals(conn, doc, content_hash=sha256_json(doc), source_name='synthetic-qa.json', allow_demo=True)

submit(document)
ok('proposal_pending_only', conn.execute('SELECT count(*) FROM work.tasks').fetchone()[0] == 0)
ok('proposal_file_idempotence', submit(document)['status'] == 'skipped')
result = review_proposal(conn, document['proposals'][0]['proposal_id'], decision='accept', actor='Synthetic QA operator')
ok('accept_roundtrip', result['task_revision'] == 1 and conn.execute('SELECT count(*) FROM work.events').fetchone()[0] == 2)
base = copy.deepcopy(document)
new = copy.deepcopy(base); p = new['proposals'][0]
p.update({'proposal_id':'test:case:1:v2','version':2,'assignee':'Fictional employee'})
submit(new)
ok('pending_does_not_overwrite_accepted', conn.execute('SELECT assignee,revision FROM work.tasks').fetchone() == (None,1))
try:
    review_proposal(conn, p['proposal_id'], decision='accept', actor='Synthetic QA operator', expected_revision=0)
    raise AssertionError('stale revision unexpectedly accepted')
except ReviewConflict:
    ok('stale_review_rolls_back', conn.execute('SELECT state FROM work.proposals WHERE proposal_id=%s',(p['proposal_id'],)).fetchone()[0] == 'proposed')
review_proposal(conn, p['proposal_id'], decision='accept', actor='Synthetic QA operator', expected_revision=1)
ok('named_assignee_correction', conn.execute('SELECT assignee,revision FROM work.tasks').fetchone() == ('Fictional employee',2))
third = copy.deepcopy(new); p=third['proposals'][0]
p.update({'proposal_id':'test:case:1:v3','version':3,'assignee':None})
ref=copy.deepcopy(p['events'][1]['evidence'])
p['events'].extend([
 {'event_id':'test:case:1:reopen','kind':'reopened','occurred_at':'2026-10-21T11:00:00+03:00','time_basis':'message','evidence':ref},
 {'event_id':'test:case:1:second-close','kind':'completed','occurred_at':'2026-10-21T12:00:00+03:00','time_basis':'message','evidence':ref},
])
submit(third)
review_proposal(conn,p['proposal_id'],decision='accept',actor='Synthetic QA operator',expected_revision=2)
ok('explicit_null_and_reopen_history', conn.execute('SELECT assignee,revision FROM work.tasks').fetchone()==(None,3) and conn.execute('SELECT count(*) FROM work.events').fetchone()[0]==4)
fourth=copy.deepcopy(third); p=fourth['proposals'][0]
p.update({'proposal_id':'test:case:1:v4','version':4,'title':'Fictional rejected correction'})
submit(fourth)
review_proposal(conn,p['proposal_id'],decision='reject',actor='Synthetic QA operator',expected_revision=3)
ok('reject_preserves_existing_task', conn.execute('SELECT title,revision FROM work.tasks').fetchone()==('Fictional test case',3))
ok('append_only_review_snapshots', conn.execute('SELECT count(*) FROM ops.review_log').fetchone()[0]==4)

bad=copy.deepcopy(base); p=bad['proposals'][0]
p.update({'proposal_id':'test:bad:v1','task_id':'test:bad','version':1})
for event in p['events']:
    event['event_id']=event['event_id'].replace('case:1','bad')
    event['evidence'][0]['revision_hash']='f'*64
try:
    submit(bad)
    raise AssertionError('missing evidence unexpectedly accepted')
except ProposalValidationError:
    ok('missing_evidence_atomic_rollback', conn.execute('SELECT count(*) FROM work.proposals WHERE task_id=%s',('test:bad',)).fetchone()[0]==0)
ok('failed_import_recorded', conn.execute("SELECT count(*) FROM ops.import_runs WHERE status='failed'").fetchone()[0]==1)

def denied(sql, parameters=()):
    try:
        conn.execute(sql, parameters)
    except psycopg.errors.InsufficientPrivilege:
        return True
    return False

conn.execute('RESET ROLE');conn.execute('SET ROLE activity_reader')
ok('reader_select',conn.execute('SELECT count(*) FROM work.tasks').fetchone()[0]==1)
ok('reader_cannot_update',denied('UPDATE work.tasks SET title=%s',('bad',)))
ok('reader_cannot_delete_raw',denied('DELETE FROM raw.messages'))
conn.execute('RESET ROLE');conn.execute('SET ROLE activity_writer')
ok('writer_cannot_update_raw_revision',denied('UPDATE raw.message_revisions SET text_plain=%s',('forbidden synthetic mutation',)))
ok('writer_cannot_mutate_proposal_document',denied("UPDATE work.proposals SET document='{}'::jsonb"))
ok('writer_cannot_delete_tasks',denied('DELETE FROM work.tasks'))
ok('writer_cannot_mutate_review_audit',denied('UPDATE ops.review_log SET actor=%s',('bad',)))
try:
    with conn.transaction():
        conn.execute("INSERT INTO work.tasks(task_id,title,category,external_refs,revision) VALUES ('test:other','Synthetic','other','{}',1)")
        ref=base['proposals'][0]['events'][0]['evidence'][0]
        conn.execute('INSERT INTO work.evidence(evidence_id,task_id,event_id,message_key,revision_hash) VALUES (%s,%s,%s,%s,%s)',('test:bad-evidence','test:other','test:case:1:request',ref['message_key'],ref['revision_hash']))
    raise AssertionError('cross-task evidence unexpectedly accepted')
except psycopg.errors.ForeignKeyViolation:
    ok('composite_event_task_fk',conn.execute("SELECT count(*) FROM work.tasks WHERE task_id='test:other'").fetchone()[0]==0)
raw={'id':999,'name':'Fictional timezone guard','messages':[{'id':1,'type':'message','date':'2026-10-20T11:00:00','text':'Fictional timezone test'}]}
ingest_bundle(conn,normalize_document(raw,fallback_timezone='UTC'))
try:
    ingest_bundle(conn,normalize_document(raw,fallback_timezone='Europe/Moscow'))
    raise AssertionError('configuration change silently skipped')
except ImportValidationError as error:
    ok('changed_parser_configuration_rejected',str(error).startswith('import_configuration_changed'))
raw['name']='Fictional overlapping timezone export'
try:
    ingest_bundle(conn,normalize_document(raw,fallback_timezone='Europe/Moscow'))
    raise AssertionError('normalization conflict not rejected')
except ImportValidationError as error:
    ok('overlap_timezone_conflict_rejected',str(error).startswith('normalization_timestamp_conflict'))
raw['name']='Fictional overlapping same timezone'
ok('same_timezone_overlap_idempotent',ingest_bundle(conn,normalize_document(raw,fallback_timezone='UTC'))['unchanged']==1)
conn.close()
print(json.dumps({'status':'passed','checks':checks,'count':len(checks),'runtime':'PGlite memory via PostgreSQL wire protocol; not Docker/Postgres16 deployment'},indent=2))

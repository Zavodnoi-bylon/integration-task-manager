import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
import io

import accounts
import bundle as app


class FakeMessage:
    def __init__(self, mid, when, topic=None):
        self.id=mid; self.date=when; self.edit_date=when
        self.sender_id=42; self.sender=SimpleNamespace(name='Author')
        self.topic=topic
        self.reply_to=SimpleNamespace(forum_topic=True, reply_to_top_id=topic, reply_to_msg_id=topic) if topic else None
    def to_dict(self):
        return {'_':'Message','id':self.id,'date':self.date,'edit_date':self.edit_date,
                'message':'  original text\n', 'entities':[{'_':'MessageEntityTextUrl','url':'https://app.asana.com/0/1/2','offset':2,'length':8}],
                'media':{'file_reference':b'\x00\xff'},
                'reply_to':{'forum_topic':True,'reply_to_top_id':self.topic,'reply_to_msg_id':self.topic} if self.topic else None}


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.base=Path(self.tmp.name)
        self.end=datetime(2026,10,24,18,tzinfo=timezone.utc)
        for slot,uid in zip(accounts.SLOTS,('101','202')):
            home=accounts.account_home(slot,self.base)
            accounts.atomic_json(home/'identity.json',{'telegram_user_id':uid})
            accounts.atomic_json(home/'config.json',{'chats':[
                {'chat_id':-100123,'topic_id':9}, {'chat_id':123}]})
        module=ModuleType('telethon')
        module.utils=SimpleNamespace(get_peer_id=lambda e:e.id,get_display_name=lambda e:e.name)
        self.modules=patch.dict(sys.modules,{'telethon':module}); self.modules.start()

    def tearDown(self):
        self.modules.stop(); self.tmp.cleanup()

    def request(self):
        return {'from_inclusive':'2026-09-30T21:00:00+00:00','to_exclusive':self.end.isoformat()}

    def client(self, uid='101', wrong=False):
        when=datetime(2026,10,1,tzinfo=timezone.utc)
        end=self.end
        class Client:
            disconnected=False
            queries=[]
            async def connect(self): pass
            async def disconnect(self): self.disconnected=True
            async def is_user_authorized(self): return True
            async def get_me(self): return SimpleNamespace(id=int(uid),bot=False)
            async def get_entity(self,cid): return SimpleNamespace(id=cid,name='Allowed source',forum=True)
            async def iter_messages(self,entity,**query):
                self.queries.append(query)
                topic=query.get('reply_to')
                yield FakeMessage(30,end,topic)  # exclusive upper bound
                yield FakeMessage(20,when,8 if wrong else topic)
                yield FakeMessage(10,datetime(2026,9,30,21,tzinfo=timezone.utc),topic)  # inclusive lower bound
                yield FakeMessage(1,datetime(2026,9,29,tzinfo=timezone.utc),topic)
        return Client()

    def test_moscow_bounds_are_not_utc_midnight(self):
        start,end=app.bounds('2026-10-01',self.end)
        self.assertEqual(start.isoformat(),'2026-09-30T21:00:00+00:00')
        self.assertEqual(end,self.end)
        with self.assertRaises(ValueError): app.bounds('2026-11-01',self.end)

    def test_capture_retains_raw_links_topic_bytes_and_dates_without_truncation(self):
        source={'key':'one','account_id':'account-1','chat_id':'-100123','topic_id':9}
        root=self.base/'capture'; (root/'sources').mkdir(parents=True)
        path=root/'sources/one.jsonl'; client=self.client()
        summary=asyncio.run(app.capture_source(client,source,self.request(),path))
        app.verify_source(root,source,summary,self.request())
        self.assertEqual(summary['messages'],2)
        self.assertEqual(summary['enumeration_stop'],'before_start')
        self.assertEqual(client.queries[0]['reply_to'],9)
        self.assertEqual(client.queries[0]['offset_date'],self.end)
        rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(rows[0]['raw']['message'],'  original text\n')
        self.assertEqual(rows[0]['raw']['entities'][0]['url'],'https://app.asana.com/0/1/2')
        self.assertEqual(rows[0]['raw']['media']['file_reference'],{'_bytes_base64':'AP8='})
        self.assertEqual(rows[0]['topic_id'],9)
        with path.open('ab') as f: f.write(b'\n')
        with self.assertRaises(ValueError): app.verify_source(root,source,summary,self.request())

    def test_wrong_topic_never_becomes_success(self):
        source={'key':'one','account_id':'account-1','chat_id':'-100123','topic_id':9}
        path=self.base/'one.jsonl'
        with self.assertRaises(ValueError):
            asyncio.run(app.capture_source(self.client(wrong=True),source,self.request(),path))
        self.assertEqual(path.read_bytes(),b'')

    def test_failed_bundle_can_resume_without_recollecting_completed_sources(self):
        root=app.new_bundle(self.base,'2026-10-01',self.end)
        first=self.client(); second=self.client('202',wrong=True)
        with patch.object(accounts,'client_for',side_effect=[first,second]), redirect_stdout(io.StringIO()):
            with self.assertRaises(ValueError): asyncio.run(app.capture_bundle(root,self.base))
        self.assertTrue(first.disconnected and second.disconnected)
        self.assertFalse((root/'manifest.json').exists())
        progress=app.read_json(root/'progress.json')
        self.assertEqual(len(progress['sources']),2)
        original={s['path']:(root/s['path']).read_bytes() for s in progress['sources'].values()}
        failed=[a for a in progress['attempts'] if a['status']=='failed'][0]
        self.assertTrue((root/failed['path']).exists())
        with patch.object(accounts,'client_for',return_value=self.client('202')) as factory, redirect_stdout(io.StringIO()):
            asyncio.run(app.capture_bundle(root,self.base))
            self.assertEqual(factory.call_count,1)
            # A crash after manifest publication but before the completion pointer
            # must recover without a second history read.
            (self.base/'last-complete-bundle.json').unlink()
            with patch.object(accounts,'client_for',side_effect=AssertionError('Must not reconnect')):
                asyncio.run(app.capture_bundle(root,self.base))
        manifest=app.verify_bundle(root)
        self.assertEqual(manifest['total_messages'],8)
        for name,data in original.items(): self.assertEqual((root/name).read_bytes(),data)
        self.assertEqual(app.read_json(self.base/'last-complete-bundle.json')['bundle_id'],root.name)
        import hashlib
        self.assertEqual(app.read_json(self.base/'last-complete-bundle.json')['manifest_sha256'],
                         hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest())
        newer={'bundle_id':'newer','to_exclusive':'2026-10-25T00:00:00+00:00','manifest_sha256':'newer'}
        accounts.atomic_json(self.base/'last-complete-bundle.json',newer)
        app.publish_complete(root,self.base,manifest)
        self.assertEqual(app.read_json(self.base/'last-complete-bundle.json'),newer)

    def test_request_change_is_rejected_and_secrets_are_absent(self):
        root=app.new_bundle(self.base,'2026-10-01',self.end)
        request=app.read_json(root/'request.json')
        self.assertNotIn('api_hash',app.canonical(request))
        request['to_exclusive']='2026-10-24T19:00:00+00:00'
        accounts.atomic_json(root/'request.json',request)
        with self.assertRaises(ValueError): asyncio.run(app.capture_bundle(root,self.base))
        with self.assertRaises(ValueError): app.resume_bundle(self.base,'../../elsewhere')


if __name__=='__main__': unittest.main()

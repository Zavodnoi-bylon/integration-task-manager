import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace, ModuleType
from datetime import datetime, timezone
import asyncio
import sys

import ingest as app


def message(mid=1, text='test', **kwargs):
    return dict(type='message', id=mid, text=text, date='2026-10-01T12:00:00',
                date_unixtime='1790859600', **kwargs)


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = app.DATA
        app.DATA = Path(self.tmp.name)
        self.db = app.connect()
        self.chat = dict(id=123, type='personal_chat', name='Test')

    def tearDown(self):
        self.db.close()
        app.DATA = self.old
        self.tmp.cleanup()

    def test_old_clean_semantics_and_metadata(self):
        record = app.normalize(self.chat, message(text=[' a', {'text': 'b'}, '\n'], **{'from': 'Author', 'from_id': 'user42'}))
        self.assertEqual(record['text'], 'ab')
        self.assertEqual(record['sender']['name'], 'Author')
        self.assertIsNotNone(record['sent_at'])
        self.assertIsNone(app.normalize(self.chat, {'type': 'service', 'id': 5, 'text': 'event'}))
        self.assertIsNone(app.normalize(self.chat, message(text=' ', photo='x')))
        self.assertEqual(app.timestamp('2026-01-01T12:00:00'), '2026-01-01T12:00:00')

    def test_repeat_import_edit_and_stale_export(self):
        path = app.DATA / 'arbitrary filename(4).json'
        def write(msg):
            path.write_text(json.dumps(dict(self.chat, messages=[msg])), encoding='utf-8')
        original = message()
        write(original)
        self.assertEqual(app.import_file(self.db, path), 1)
        self.assertEqual(app.import_file(self.db, path), 0)
        changed = message(text='changed', edited_unixtime='1790869600')
        write(changed)
        app.import_file(self.db, path)
        # Different export containing an older version must not revert the edit.
        write(dict(original, extra=True))
        app.import_file(self.db, path)
        self.assertEqual(app.export(self.db), 1)
        output = json.loads((app.DATA / 'normalized/messages.json').read_text())
        self.assertEqual(output['messages'][0]['text'], 'changed')

    def test_invalid_file_transaction_rolls_back(self):
        path = app.DATA / 'bad.json'
        path.write_text(json.dumps(dict(self.chat, messages=[message(), message(mid='bad')])) )
        with self.assertRaises(ValueError):
            app.import_file(self.db, path)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM imports').fetchone()[0], 0)

    def test_peer_identity_and_checkpoint(self):
        self.assertEqual(app.peer_id(dict(id=12, type='private_supergroup')), '-10012')
        self.assertEqual(app.peer_id(dict(id=12, type='private_group')), '-12')
        record = app.normalize(self.chat, message())
        app.save_batch(self.db, '123', [(1, record)], 10)
        app.save_batch(self.db, '123', [], 5)
        self.assertEqual(self.db.execute('SELECT last_id FROM cursors').fetchone()[0], 10)
        app.save_batch(self.db, '123', [(1, None)], 10)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 0)

    def test_topic_scope_validation_matching_and_independent_cursors(self):
        self.assertEqual(app.target_scope({'chat_id':-100123,'topic_id':9}),(-100123,9))
        for bad in ({'chat_id':-100123,'topic_id':None,'unexpected':True}, {'chat_id':-100123,'topic_id':1}, {'topic_id':9}):
            with self.assertRaises(ValueError):
                app.target_scope(bad)
        direct=SimpleNamespace(id=10,reply_to=SimpleNamespace(forum_topic=True,reply_to_top_id=None,reply_to_msg_id=9))
        nested=SimpleNamespace(id=11,reply_to=SimpleNamespace(forum_topic=True,reply_to_top_id=9,reply_to_msg_id=10))
        other=SimpleNamespace(id=12,reply_to=SimpleNamespace(forum_topic=True,reply_to_top_id=8,reply_to_msg_id=9))
        self.assertTrue(app.message_in_topic(direct,9))
        self.assertTrue(app.message_in_topic(nested,9))
        self.assertFalse(app.message_in_topic(other,9))
        self.assertFalse(app.message_in_topic(SimpleNamespace(id=13),9))
        app.save_batch(self.db,'-100123',[],50,cursor_key='-100123:topic:9')
        app.save_batch(self.db,'-100123',[],100,cursor_key='-100123:topic:8')
        self.assertEqual(dict(self.db.execute('SELECT chat_id,last_id FROM cursors')),
                         {'-100123:topic:9':50,'-100123:topic:8':100})

    def test_topic_collection_requests_only_topic_and_rejects_other_messages(self):
        class FakeMessage:
            def __init__(self,mid,topic):
                self.id=mid
                self.date=datetime(2026,10,1,tzinfo=timezone.utc)
                self.edit_date=None; self.message='allowed text'; self.sender_id=42
                self.reply_to_msg_id=topic; self.post_author=None
                self.reply_to=SimpleNamespace(forum_topic=True,reply_to_top_id=topic,reply_to_msg_id=topic)
            async def get_sender(self):
                return SimpleNamespace(name='Author')
        class FakeClient:
            wrong=False
            calls=[]
            def __init__(self,*args,**kwargs): pass
            async def connect(self): pass
            async def disconnect(self): pass
            async def is_user_authorized(self): return True
            async def iter_dialogs(self):
                yield SimpleNamespace(entity=SimpleNamespace(id=-100123,name='Forum',forum=True))
            async def iter_messages(self,entity,**kwargs):
                self.calls.append(kwargs)
                yield FakeMessage(20,8 if self.wrong else 9)
        telethon=ModuleType('telethon'); telethon.TelegramClient=FakeClient
        telethon.utils=SimpleNamespace(get_peer_id=lambda e:e.id,get_display_name=lambda e:e.name)
        types=ModuleType('telethon.tl.types'); types.Message=FakeMessage
        config={'api_id':1,'api_hash':'fake','chats':[{'chat_id':-100123,'topic_id':9}]}
        with patch.dict(sys.modules,{'telethon':telethon,'telethon.tl.types':types}):
            asyncio.run(app.collect(self.db,config))
            stored=json.loads(self.db.execute('SELECT payload FROM messages').fetchone()[0])
            self.assertEqual(stored['topic_id'],9)
            self.assertEqual(FakeClient.calls[-1]['reply_to'],9)
            self.assertEqual(self.db.execute('SELECT chat_id FROM cursors').fetchone()[0],'-100123:topic:9')
            self.db.execute('DELETE FROM messages'); self.db.commit()
            FakeClient.wrong=True
            with self.assertRaises(ValueError):
                asyncio.run(app.collect(self.db,config))
            self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)

    def test_collector_resume_after_partial_network_failure(self):
        class FakeMessage:
            def __init__(self, mid):
                self.id = mid
                self.date = datetime(2026, 10, 1, tzinfo=timezone.utc)
                self.edit_date = None
                self.message = f'message {mid}'
                self.sender_id = 42
                self.reply_to_msg_id = None
                self.post_author = None
            async def get_sender(self):
                return SimpleNamespace(name='Author')

        class FakeClient:
            fail = True
            calls = []
            def __init__(self, *args, **kwargs):
                pass
            async def connect(self):
                pass
            async def disconnect(self):
                pass
            async def is_user_authorized(self):
                return True
            async def iter_dialogs(self):
                yield SimpleNamespace(entity=SimpleNamespace(id=123, name='Chat'))
            async def iter_messages(self, entity, **kwargs):
                self.calls.append(kwargs)
                for mid in range(kwargs['min_id'] + 1, 206):
                    if mid == 151 and self.fail:
                        raise ConnectionError('simulated')
                    yield FakeMessage(mid)

        telethon = ModuleType('telethon')
        telethon.TelegramClient = FakeClient
        telethon.utils = SimpleNamespace(get_peer_id=lambda e: e.id, get_display_name=lambda e: e.name)
        types = ModuleType('telethon.tl.types')
        types.Message = FakeMessage
        config = {'api_id': 1, 'api_hash': 'fake', 'chats': [123], 'edit_overlap_ids': 10}
        with patch.dict(sys.modules, {'telethon': telethon, 'telethon.tl.types': types}):
            with self.assertRaises(ConnectionError):
                asyncio.run(app.collect(self.db, config))
            self.assertEqual(self.db.execute('SELECT last_id FROM cursors').fetchone()[0], 100)
            FakeClient.fail = False
            asyncio.run(app.collect(self.db, config))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 205)
        self.assertEqual(self.db.execute('SELECT last_id FROM cursors').fetchone()[0], 205)
        self.assertEqual(FakeClient.calls[-1], {'min_id': 90, 'reverse': True, 'limit': None})


if __name__ == '__main__':
    unittest.main()

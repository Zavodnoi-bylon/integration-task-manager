import asyncio
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace, ModuleType
import sys
import unittest
from unittest.mock import patch

import accounts as app


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        app.atomic_json(self.base / 'telegram-app.json', {'api_id': 1, 'api_hash': 'a' * 32})

    def tearDown(self):
        self.temp.cleanup()

    def fake_client(self, uid, authorized=True, bot=False):
        class Client:
            disconnected = False
            async def connect(self):
                pass
            async def disconnect(self):
                self.disconnected = True
            async def is_user_authorized(self):
                return authorized
            async def get_me(self):
                return SimpleNamespace(id=uid, bot=bot)
        return Client()

    def test_both_logins_have_independent_storage_and_no_collection(self):
        clients = [self.fake_client(101), self.fake_client(202)]
        with patch.object(app, 'client_for', side_effect=clients), redirect_stdout(io.StringIO()):
            asyncio.run(app.run('login', app.SLOTS, self.base))
        self.assertTrue(all(c.disconnected for c in clients))
        for slot, uid in zip(app.SLOTS, (101, 202)):
            home = app.account_home(slot, self.base)
            self.assertEqual(app.identity(home)['telegram_user_id'], str(uid))
            self.assertEqual(json.loads((home / 'config.json').read_text())['chats'], [])
            self.assertFalse((home / 'data/activity.sqlite3').exists())
        self.assertNotEqual(app.account_home('account-1', self.base), app.account_home('account-2', self.base))
        with self.assertRaises(app.SetupError):
            app.account_home('../escape', self.base)

    def test_duplicate_account_cannot_register_second_slot(self):
        clients = [self.fake_client(101), self.fake_client(101)]
        with patch.object(app, 'client_for', side_effect=clients), redirect_stdout(io.StringIO()):
            asyncio.run(app.login_one('account-1', self.base))
            with self.assertRaises(app.SetupError):
                asyncio.run(app.login_one('account-2', self.base))
        self.assertFalse((app.account_home('account-2', self.base) / 'identity.json').exists())
        self.assertTrue(clients[1].disconnected)

    def test_identity_is_not_silently_replaced_and_bots_are_rejected(self):
        home = app.account_home('account-1', self.base)
        app.atomic_json(home / 'identity.json', {'telegram_user_id': '101'})
        for client in (self.fake_client(202), self.fake_client(101, bot=True)):
            with patch.object(app, 'client_for', return_value=client):
                with self.assertRaises(app.SetupError):
                    asyncio.run(app.login_one('account-1', self.base))
            self.assertTrue(client.disconnected)
        self.assertEqual(app.identity(home)['telegram_user_id'], '101')

    def test_status_does_not_expose_credentials_or_claim_live_authentication(self):
        app.atomic_json(app.account_home('account-1', self.base) / 'config.json', {
            'api_id': 1, 'api_hash': 'secret', 'phone': 'private', 'chats': [123],
        })
        status = app.local_status(self.base)
        serialized = json.dumps(status)
        self.assertNotIn('secret', serialized)
        self.assertNotIn('private', serialized)
        self.assertFalse(status['accounts'][0]['login_recorded'])
        self.assertEqual(status['accounts'][0]['target_count'], 1)

    def test_revoked_session_check_never_requests_interactive_login(self):
        home = app.account_home('account-1', self.base)
        app.atomic_json(home / 'identity.json', {'telegram_user_id': '101'})
        client = self.fake_client(101, authorized=False)
        with patch.object(app, 'client_for', return_value=client), patch.object(app, 'authorize_client') as authorize:
            with self.assertRaises(app.SetupError):
                asyncio.run(app.check_one('account-1', self.base))
        authorize.assert_not_called()
        self.assertTrue(client.disconnected)

    def test_parallel_setup_and_collection_share_a_lock(self):
        lock = app.account_home('account-1', self.base) / 'data/run.lock'
        with app.locked_file(lock):
            with self.assertRaises(OSError):
                with app.locked_file(lock):
                    self.fail('Second process entered the session')

    def test_first_login_uses_hidden_code_and_two_factor_password(self):
        class PasswordNeeded(Exception):
            pass
        errors = ModuleType('telethon.errors')
        errors.SessionPasswordNeededError = PasswordNeeded
        calls = []
        class Client:
            async def send_code_request(self, phone):
                calls.append(('phone', phone))
                return SimpleNamespace(phone_code_hash='temporary-hash')
            async def sign_in(self, **kwargs):
                calls.append(('sign_in', kwargs))
                if 'code' in kwargs:
                    raise PasswordNeeded()
        with patch.dict(sys.modules, {'telethon': ModuleType('telethon'), 'telethon.errors': errors}), \
             patch.object(app.getpass, 'getpass', side_effect=['+70000000000', '12345', 'private-password']):
            asyncio.run(app.authorize_client(Client()))
        self.assertEqual(calls[1][1]['phone_code_hash'], 'temporary-hash')
        self.assertEqual(calls[2], ('sign_in', {'password':'private-password'}))
        self.assertEqual(list((self.base / 'accounts').glob('**/*')) if (self.base / 'accounts').exists() else [], [])

    def test_noninteractive_login_is_rejected_before_any_network_access(self):
        with patch.object(app.sys.stdin, 'isatty', return_value=False), patch.object(app, 'run') as run, redirect_stdout(io.StringIO()):
            self.assertEqual(app.main(['login']), 1)
        run.assert_not_called()

    def test_targets_require_unique_matches_and_preserve_other_account(self):
        home = app.account_home('account-1', self.base)
        other = app.account_home('account-2', self.base)
        app.atomic_json(home / 'identity.json', {'telegram_user_id': '101'})
        app.atomic_json(home / 'config.json', {'api_id':1, 'api_hash':'a'*32, 'chats':[]})
        app.atomic_json(other / 'config.json', {'chats':[777]})
        client = self.fake_client(101)
        dialogs = [(10,' TECH   SUPPORT '), (20,'Alert from asana'), (30,'Unrelated private chat')]
        async def iter_dialogs():
            for cid, title in dialogs:
                yield SimpleNamespace(id=cid, name=title, entity=SimpleNamespace(forum=False))
        client.iter_dialogs = iter_dialogs
        with patch.object(app, 'client_for', return_value=client), redirect_stdout(io.StringIO()) as output:
            asyncio.run(app.configure_targets('account-1',['Tech support','Alert from asana'],self.base))
            self.assertNotIn('Unrelated', output.getvalue())
            self.assertEqual(json.loads((home/'config.json').read_text())['chats'],[10,20])
            dialogs.append((40,'Tech support'))
            with self.assertRaises(app.SetupError):
                asyncio.run(app.configure_targets('account-1',['Tech support','Alert from asana'],self.base))
            with self.assertRaises(app.SetupError):
                asyncio.run(app.configure_targets('account-1',['Missing'],self.base))
        self.assertEqual(json.loads((home/'config.json').read_text())['chats'],[10,20])
        self.assertEqual(json.loads((other/'config.json').read_text())['chats'],[777])
        self.assertTrue(client.disconnected)


if __name__ == '__main__':
    unittest.main()

import asyncio
from datetime import datetime, timezone
import hashlib
import unittest
from unittest.mock import patch

import accounts
import bundle
import daily
import partition
import test_bundle


class DailyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_bundle.BundleTests(methodName='runTest')
        self.fixture.setUp()
        self.base = self.fixture.base
        self.end = datetime(2026, 10, 25, 21, tzinfo=timezone.utc)
        for slot, (tenant, _, _) in partition.CONTEXT.items():
            path = accounts.account_home(slot, self.base) / 'config.json'
            config = bundle.read_json(path)
            config['tenant_id'] = tenant
            accounts.atomic_json(path, config)
        self.clock = patch('bundle.now', return_value=datetime(2026, 10, 30, tzinfo=timezone.utc))
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.fixture.tearDown()

    def client(self, slot, _base):
        return self.fixture.client('101' if slot == 'account-1' else '202')

    def run_capture(self, end=None):
        with patch('accounts.client_for', side_effect=self.client) as calls:
            result = asyncio.run(daily.run_daily(self.base, end or self.end))
        return result, calls

    def state(self):
        return bundle.read_json(self.base / 'daily/state.json')

    def test_cutoff_is_last_complete_moscow_day(self):
        now = datetime(2026, 10, 26, 21, 10, tzinfo=timezone.utc)
        self.assertEqual(daily.completed_day_end(now), datetime(2026, 10, 26, 21, tzinfo=timezone.utc))

    def test_success_publishes_separate_inputs_and_repeat_has_no_network_calls(self):
        state, calls = self.run_capture()
        self.assertEqual(calls.call_count, 2)
        root = self.base / 'bundles' / state['last_success']
        inputs = partition.verify_inputs(root, root / 'account-inputs')
        self.assertEqual(set(inputs), {'account-1', 'account-2'})
        self.assertEqual(self.state()['watermark'], self.end.isoformat())
        count = len(list((self.base / 'bundles').iterdir()))
        _, calls = self.run_capture()
        self.assertEqual(calls.call_count, 0)
        self.assertEqual(len(list((self.base / 'bundles').iterdir())), count)

    def fail_second_account(self):
        def factory(slot, base):
            if slot == 'account-2':
                raise ConnectionError('private details must not be logged')
            return self.client(slot, base)
        with patch('accounts.client_for', side_effect=factory):
            with self.assertRaises(ConnectionError):
                asyncio.run(daily.run_daily(self.base, self.end))

    def test_partial_failure_resumes_only_unfinished_account(self):
        self.fail_second_account()
        before = self.state()
        self.assertNotEqual(before['watermark'], self.end.isoformat())
        root = self.base / 'bundles' / before['active_bundle']
        completed = bundle.read_json(root / 'progress.json')['sources']
        hashes = {v['path']: hashlib.sha256((root / v['path']).read_bytes()).hexdigest()
                  for v in completed.values()}
        state, calls = self.run_capture()
        self.assertEqual([c.args[0] for c in calls.call_args_list], ['account-2'])
        self.assertIsNone(state['active_bundle'])
        for path, sha in hashes.items():
            self.assertEqual(hashlib.sha256((root / path).read_bytes()).hexdigest(), sha)

    def test_output_failure_does_not_advance_and_retry_needs_no_telegram(self):
        with patch('accounts.client_for', side_effect=self.client), \
                patch('partition.ensure_inputs', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                asyncio.run(daily.run_daily(self.base, self.end))
        self.assertNotEqual(self.state()['watermark'], self.end.isoformat())
        state, calls = self.run_capture()
        self.assertEqual(calls.call_count, 0)
        self.assertEqual(state['watermark'], self.end.isoformat())

    def test_missed_days_resume_then_cover_gap_with_edit_overlap(self):
        self.fail_second_account()
        later = datetime(2026, 10, 28, 21, tzinfo=timezone.utc)
        state, calls = self.run_capture(later)
        self.assertEqual([c.args[0] for c in calls.call_args_list], ['account-2','account-1','account-2'])
        request = bundle.read_json(self.base / 'bundles' / state['last_success'] / 'request.json')
        self.assertEqual(state['watermark'], later.isoformat())
        self.assertEqual(request['from_inclusive'], '2026-10-18T21:00:00+00:00')

    def test_scope_change_requires_review_and_does_not_collect(self):
        self.run_capture()
        path = accounts.account_home('account-1', self.base) / 'config.json'
        config = bundle.read_json(path)
        config['chats'].append(555)
        accounts.atomic_json(path, config)
        with patch('accounts.client_for') as calls, self.assertRaises(ValueError):
            asyncio.run(daily.run_daily(self.base, self.end))
        calls.assert_not_called()

    def test_bootstrap_validates_prior_pointer(self):
        state, _ = self.run_capture()
        initial = daily.initial_state(self.base, daily.scope(self.base))
        self.assertEqual(initial['watermark'], state['watermark'])
        path = self.base / 'last-complete-bundle.json'
        pointer = bundle.read_json(path)
        pointer['manifest_sha256'] = 'bad'
        accounts.atomic_json(path, pointer)
        with self.assertRaises(ValueError):
            daily.initial_state(self.base, daily.scope(self.base))

    def test_floodwait_preserves_request_and_blocks_early_retry(self):
        class FloodWaitError(Exception):
            seconds = 7200
        with patch('accounts.client_for', side_effect=FloodWaitError()):
            with self.assertRaises(FloodWaitError):
                asyncio.run(daily.run_daily(self.base, self.end))
        self.assertIn('retry_not_before', self.state())
        with patch('accounts.client_for') as calls, self.assertRaises(ValueError):
            asyncio.run(daily.run_daily(self.base, self.end))
        calls.assert_not_called()


if __name__ == '__main__':
    unittest.main()

import os,json,unittest
from uuid import uuid4
from unittest.mock import patch
from fastapi.testclient import TestClient
import app as web
from activity.task_status import validate_action
from activity.ingest import ImportValidationError


class TaskStatusTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'ACTIVITY_DEMO':'1','ACTIVITY_PROXY_TOKEN':'secret-'*8,'ACTIVITY_AUTH_ROLES':json.dumps({'owner':'admin','reader':'viewer','lead':'tech_lead'}),'ACTIVITY_STATUS_WRITES':'1'})
        self.env.start();self.client=TestClient(web.app)
        self.body={'status':'completed','expected_revision':1,'request_id':str(uuid4())}
    def tearDown(self):self.client.close();self.env.stop()
    def headers(self,user):return {'x-activity-user':user,'x-activity-proxy-token':'secret-'*8,'x-activity-action':'change-status'}
    def test_owner_identity_is_taken_from_proxy_not_body(self):
        with patch.object(web,'apply_status_change',return_value={'revision':2}) as write:
            response=self.client.post('/api/tasks/asana:1/status',json=self.body,headers=self.headers('owner'))
            self.assertEqual(response.status_code,200)
            self.assertEqual(write.call_args.args[:2],('asana:1','owner'))
    def test_viewers_cross_site_and_missing_action_cannot_write(self):
        with patch.object(web,'apply_status_change',side_effect=AssertionError('No DB access')):
            for user in ['reader','lead']:
                self.assertEqual(self.client.post('/api/tasks/a/status',json=self.body,headers=self.headers(user)).status_code,403)
            headers=self.headers('owner');headers.pop('x-activity-action')
            self.assertEqual(self.client.post('/api/tasks/a/status',json=self.body,headers=headers).status_code,403)
            self.assertEqual(self.client.post('/api/tasks/a/status',json=self.body,headers={**self.headers('owner'),'sec-fetch-site':'cross-site'}).status_code,403)
    def test_invalid_payload_cannot_choose_actor_or_fake_revision(self):
        for value in [dict(self.body,actor='other'),dict(self.body,expected_revision=True),dict(self.body,request_id='bad'),dict(self.body,status='waiting')]:
            with self.assertRaises(ImportValidationError):validate_action(value)

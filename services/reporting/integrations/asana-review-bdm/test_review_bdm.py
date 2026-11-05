import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx

from alerts.service import AlertService, in_review_bdm
from alerts.clients import AsanaAuthorizationError, AsanaClient


def task(section='In Progress', assignee='user1'):
    return {'gid':'123', 'name':'Test', 'assignee':{'gid':assignee}, 'completed':False,
            'modified_at':'2026-10-26T10:00:00Z', 'custom_fields':[],
            'memberships':[{'project':{'gid':'project1'}, 'section':{'name':section}}]}


class ReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_preserves_live_authorization_error_type(self):
        client=AsanaClient('synthetic-not-a-real-token')
        await client.client.aclose()
        client.client=httpx.AsyncClient(base_url=client.BASE_URL,
            transport=httpx.MockTransport(lambda request:httpx.Response(403,request=request)))
        try:
            with self.assertRaises(AsanaAuthorizationError) as result:
                await client.request('GET','/tasks/123')
            self.assertEqual(result.exception.status_code,403)
        finally:
            await client.close()

    async def test_preserves_skip_inaccessible_and_removed_tasks(self):
        for status in [403,404]:
            with self.subTest(status=status):
                s=self.service()
                s.db=SimpleNamespace(get_meta=lambda *args:'1',
                    list_active_tasks=lambda:[{'task_gid':'123','payload':task(),'last_seen_at':'2026-10-26T09:00:00Z'}],
                    mark_task_unavailable=lambda gid,reason: calls.append((gid,reason)))
                calls=[]
                s.asana.project_tasks=AsyncMock(return_value=[])
                error=AsanaAuthorizationError(403) if status==403 else httpx.HTTPStatusError(
                    'not found',request=httpx.Request('GET','https://example.org'),response=httpx.Response(404))
                s.asana.task=AsyncMock(side_effect=error)
                await s.poll_asana()
                self.assertEqual(calls,[('123',f'http_{status}')])

    def service(self):
        s=AlertService(SimpleNamespace(project_gids=('project1',)),None,SimpleNamespace(task_stories=AsyncMock()),None)
        s.notify_gid=AsyncMock(return_value=True)
        s.notify_assignee=AsyncMock(return_value=True)
        return s

    async def test_section_transition_alerts_once_and_does_not_read_comments(self):
        s=self.service();old=task();new=task('Review BDM')
        await s.process_task_changes(old,new,'2026-10-26T09:00:00Z')
        s.notify_gid.assert_awaited_once()
        self.assertIn('Задача передана в Review BDM',s.notify_gid.await_args.args[1])
        await s.process_task_changes(new,copy.deepcopy(new),'2026-10-26T09:00:00Z')
        self.assertEqual(s.notify_gid.await_count,1)
        s.asana.task_stories.assert_not_awaited()

    async def test_upgrade_baseline_and_comment_only_change_are_silent(self):
        s=self.service();old=task();old.pop('memberships');old.pop('custom_fields')
        await s.process_task_changes(old,task('Review BDM'),'2026-10-26T09:00:00Z')
        new=task();new['modified_at']='2026-10-26T10:01:00Z'
        await s.process_task_changes(task(),new,'2026-10-26T09:00:00Z')
        s.notify_gid.assert_not_awaited();s.asana.task_stories.assert_not_awaited()

    async def test_enum_transition_and_reentry(self):
        s=self.service();old=task();new=task()
        new['custom_fields']=[{'enum_value':{'name':'Review BDM'}}]
        await s.process_task_changes(old,new,'2026-10-26T09:00:00Z')
        await s.process_task_changes(new,old,'2026-10-26T09:00:00Z')
        await s.process_task_changes(old,new,'2026-10-26T09:00:00Z')
        self.assertEqual(s.notify_gid.await_count,2)

    def test_other_project_and_text_are_not_transition(self):
        t=task('Review BDM');t['memberships'][0]['project']['gid']='other'
        t['name']='Review BDM';t['notes']='Review BDM'
        self.assertFalse(in_review_bdm(t,('project1',)))

    async def test_removed_assignee_uses_previous_recipient(self):
        s=self.service();new=task('Review BDM');new['assignee']=None
        await s.process_task_changes(task(),new,'2026-10-26T09:00:00Z')
        self.assertEqual(s.notify_gid.await_args.args[0],'user1')
        self.assertIn('Review BDM',s.notify_gid.await_args.args[1])

    async def test_client_requests_structured_status_for_both_endpoints(self):
        client=AsanaClient('synthetic-not-a-real-token')
        try:
            client.list_all=AsyncMock(return_value=[])
            client.request=AsyncMock(return_value={'data':{}})
            await client.project_tasks('project1');await client.task('123')
            for method in [client.list_all,client.request]:
                fields=method.await_args.kwargs['params']['opt_fields']
                self.assertIn('memberships.section.name',fields)
                self.assertIn('custom_fields.enum_value.name',fields)
        finally:
            await client.close()

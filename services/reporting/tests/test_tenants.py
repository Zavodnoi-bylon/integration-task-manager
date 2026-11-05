import unittest
from activity.ingest import ImportValidationError, normalize_document
from activity.tenants import apply_context, validate_context
from activity.proposals import ProposalValidationError, validate_document, evidence_index
from test_proposals import fixture
from activity.ingest import sha256_json
from activity.account_input import normalize_account_input


class TenantTests(unittest.TestCase):
    def test_account_and_tenant_cannot_be_crossed_or_omitted(self):
        for pair in [('theraw','account-1'),('traffic-angels','account-2'),(None,'account-1'),('theraw',None)]:
            with self.subTest(pair=pair), self.assertRaises(ImportValidationError):
                validate_context(*pair)

    def test_context_preserves_evidence_identity_and_raw_revision(self):
        raw={'id':123,'messages':[{'id':1,'date_unixtime':'1792483200','text':'destination 000123'}]}
        bundle=normalize_document(raw)
        message=bundle['messages'][0].copy()
        apply_context(bundle,'traffic-angels','account-1')
        self.assertEqual(bundle['messages'][0],message)
        self.assertEqual(bundle['sources'][0]['tenant_id'],'traffic-angels')
        self.assertIn(';tenant=traffic-angels;account=account-1',bundle['parser_version'])

    def test_proposal_uses_registered_tenant_and_string_destination(self):
        document,_=fixture()
        p=document['proposals'][0]
        p['external_refs']['tenant_id']='theraw'
        self.assertEqual(validate_document(document)['proposals'][0]['external_refs']['destination_id'],'000123')
        p['external_refs']['tenant_id']='guessed-brand'
        with self.assertRaises(ProposalValidationError): validate_document(document)

    def account_input(self):
        raw={'_':'Message','id':1,'peer_id':{'_':'PeerUser','user_id':123},
             'date':'2026-10-20T08:00:00+00:00','message':'original destination 000123',
             'entities':[{'url':'https://app.asana.com/0/1/2'}]}
        return {'kind':'telegram-account-input','schema_version':1,'tenant_id':'traffic-angels','account_id':'account-1',
                'from_inclusive':'2026-10-01T00:00:00+03:00','to_exclusive':'2026-10-24T23:00:00+03:00',
                'api_sources':[{'chat_id':'123','topic_id':None,'account_id':'account-1','tenant_id':'traffic-angels'}],
                'api_messages':[{'account_id':'account-1','chat_id':'123','topic_id':None,'message_id':'1',
                                 'sent_at':raw['date'],'raw_sha256':sha256_json(raw),'raw':raw}]}

    def test_api_input_retains_raw_entities_and_scopes_identity_by_account(self):
        value=self.account_input()
        result=normalize_account_input(value,source_name='account-1.json',content_hash='a'*64)
        m=result['messages'][0]
        self.assertEqual(m['raw_json'],value['api_messages'][0]['raw'])
        self.assertEqual(m['revision_hash'],sha256_json(m['raw_json']))
        self.assertEqual(m['message_key'],'telegram-api:account-1:123:1')
        self.assertEqual(m['entity_links'][0]['href'],'https://app.asana.com/0/1/2')
        self.assertIn((m['message_key'],m['revision_hash']),evidence_index(result))

    def test_api_input_rejects_mixed_account_tampered_peer_hash_and_period(self):
        for change in ['account','peer','hash','date']:
            value=self.account_input(); row=value['api_messages'][0]
            if change=='account': row['account_id']='account-2'
            if change=='peer':
                row['raw']['peer_id']['user_id']=456
                row['raw_sha256']=sha256_json(row['raw'])
            if change=='hash': row['raw_sha256']='f'*64
            if change=='date': value['to_exclusive']='2026-10-19T00:00:00+03:00'
            with self.subTest(change=change),self.assertRaises(ImportValidationError):
                normalize_account_input(value,source_name='test',content_hash='a'*64)

    def test_manual_export_in_account_input_is_tagged_without_changing_raw(self):
        value=self.account_input()
        raw={'id':456,'messages':[{'id':1,'date_unixtime':'1792483200','text':'000123'}]}
        value['manual_exports']=[{'collection_mode':'manual-once','document':raw}]
        result=normalize_account_input(value,source_name='test',content_hash='a'*64)
        self.assertEqual(len(result['sources']),2)
        self.assertEqual(result['messages'][1]['raw_json'],raw['messages'][0])
        self.assertEqual(result['messages'][1]['message_key'],'telegram-desktop:456:1')

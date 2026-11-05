import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import partition


class PartitionTests(unittest.TestCase):
    def test_accounts_remain_separate_manual_is_once_and_original_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'capture'; root.mkdir()
            (root/'manifest.json').write_text('{}')
            sources=[]; manifest={'bundle_id':'example','sources':{}}
            for i,account in enumerate(('account-1','account-2')):
                key=f's{i}'
                sources.append({'key':key,'account_id':account,'chat_id':str(i)})
                (root/f'{key}.jsonl').write_text(json.dumps({'account_id':account,'raw':{'message':'original'},'raw_sha256':'unchanged'})+'\n')
                manifest['sources'][key]={'path':f'{key}.jsonl'}
            manual=Path(tmp)/'manual.json'
            manual.write_text('{"id":123,"messages":[{"id":1,"text":"000123"}]}')
            request={'sources':sources,'from_inclusive':'start','to_exclusive':'end'}
            output=Path(tmp)/'output'
            with patch.object(partition.bundle,'verify_bundle',return_value=manifest), patch.object(partition.bundle,'read_json',side_effect=lambda path: request if path.name=='request.json' else json.loads(path.read_text())):
                result=partition.partition(root,output,[('account-1',manual)])
                self.assertEqual(result['account-1']['manual_messages'],1)
                self.assertEqual(result['account-2']['manual_messages'],0)
                second=json.loads((output/'account-2.json').read_text())
                self.assertEqual(second['api_messages'][0]['account_id'],'account-2')
                first=json.loads((output/'account-1.json').read_text())
                self.assertEqual(first['api_messages'][0]['raw_sha256'],'unchanged')
                self.assertEqual(first['manual_exports'][0]['document']['messages'][0]['text'],'000123')
                self.assertEqual(partition.verify_inputs(root,output)['account-1']['manual_messages'],1)
                (output/'account-1.json').write_text('{}')
                with self.assertRaisesRegex(ValueError,'checksum'): partition.verify_inputs(root,output)
                with self.assertRaises(FileExistsError): partition.partition(root,output)
                (root/'s0.jsonl').write_text('{"account_id":"account-2"}\n')
                with self.assertRaisesRegex(ValueError,'Mixed accounts'): partition.partition(root,Path(tmp)/'bad')

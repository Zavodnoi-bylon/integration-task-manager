import copy
import unittest
from activity.ingest import api_same_content


class ApiRevisionTests(unittest.TestCase):
    def test_only_download_reference_is_transport_metadata(self):
        raw={'message':'original','media':{'_':'MessageMediaPhoto','photo':{'_':'Photo','id':123,'file_reference':{'_bytes_base64':'old'}}}}
        new=copy.deepcopy(raw)
        new['media']['photo']['file_reference']['_bytes_base64']='new'
        self.assertTrue(api_same_content(raw,new))
        self.assertEqual(raw['media']['photo']['file_reference']['_bytes_base64'],'old')
        new['message']='edited'
        self.assertFalse(api_same_content(raw,new))
        new['message']='original';new['media']['photo']['id']=456
        self.assertFalse(api_same_content(raw,new))

import base64,json,os,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'experiments'))
from refresh_oidc import refresh
class Response:
 def __init__(self,claims):self.value=json.dumps({'value':'header.'+base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')+'.signature'}).encode()
 def __enter__(self):return self
 def __exit__(self,*_):pass
 def read(self,*_):return self.value
class Rotation(unittest.TestCase):
 def test_correct_subject_rotates_private_file(self):
  with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'ACTIONS_ID_TOKEN_REQUEST_URL':'https://example.invalid/token','ACTIONS_ID_TOKEN_REQUEST_TOKEN':'not-a-real-token'}):
   p=Path(d)/'token';p.write_text('old')
   with patch('urllib.request.urlopen',return_value=Response({'sub':'branch','aud':'sts.amazonaws.com','exp':time.time()+300})):refresh(p,'branch')
   self.assertNotEqual(p.read_text(),'old');self.assertEqual(p.stat().st_mode&0o777,0o600)
 def test_wrong_branch_and_expired_token_leave_old_file(self):
  for claims in ({'sub':'wrong','aud':'sts.amazonaws.com','exp':time.time()+300},{'sub':'branch','aud':'sts.amazonaws.com','exp':time.time()}):
   with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'ACTIONS_ID_TOKEN_REQUEST_URL':'https://example.invalid/token','ACTIONS_ID_TOKEN_REQUEST_TOKEN':'not-a-real-token'}):
    p=Path(d)/'token';p.write_text('old')
    with patch('urllib.request.urlopen',return_value=Response(claims)),self.assertRaises(ValueError):refresh(p,'branch')
    self.assertEqual(p.read_text(),'old')
if __name__=='__main__':unittest.main()

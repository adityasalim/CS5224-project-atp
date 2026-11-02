"""Exercise actual pipeline entry points without AWS or a legacy model fallback."""
import ast,json,unittest
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[2]
source=ast.parse((ROOT/'agent/tp_travel_agent.py').read_text())
names={'Usage','converse','ask_json','reply_text'}
subset=ast.Module(body=[n for n in source.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in names],type_ignores=[])
def forbidden():raise AssertionError('Experiment reached legacy provider defaults')
namespace={'time':SimpleNamespace(sleep=lambda seconds:None),'json':json,'extract_json':json.loads,'effective_model':lambda x:x,'_clients':forbidden}
exec(compile(subset,'tp_travel_agent.py','exec'),namespace)
class RoutingTest(unittest.TestCase):
 def test_json_recovery_keeps_role_trip_and_actual_model(self):
  seen=[]
  def generate(**kw):
   seen.append(kw)
   return {'output':{'message':{'content':[{'text':'broken' if len(seen)==1 else '{"ok":true}'}]}},'stopReason':'end_turn','usage':{'inputTokens':10,'outputTokens':5},'_adapter':{'modelId':'actual-selected-model'}}
  usage=namespace['Usage'](SimpleNamespace(generate=generate,configuration=SimpleNamespace(selections={'planner':SimpleNamespace(model_id='actual-selected-model')})),'trip-controlled')
  result=namespace['ask_json']('legacy-expensive-model','system','question',usage,model_role='planner')
  self.assertTrue(result['ok']);self.assertEqual(len(seen),2)
  self.assertTrue(all(r['role']=='planner' and r['trip_id']=='trip-controlled' for r in seen))
  self.assertEqual(usage.calls,2);self.assertEqual(set(usage.by_model),{'actual-selected-model'})
 def test_unattributed_request_refused_before_client(self):
  usage=namespace['Usage'](SimpleNamespace(generate=forbidden),'trip-controlled')
  with self.assertRaises(ValueError):namespace['converse']('legacy','s',[],usage)
 def test_transient_retry_keeps_model_role_and_is_bounded(self):
  seen=[]
  class Transient(RuntimeError):code='ThrottlingException'
  def fail(**kw):seen.append(kw);raise Transient('capacity')
  usage=namespace['Usage'](SimpleNamespace(generate=fail),'trip-controlled')
  with self.assertRaises(Transient):namespace['converse']('legacy','s',[],usage,model_role='reviewer')
  self.assertEqual(len(seen),2)
  self.assertTrue(all(r['role']=='reviewer' for r in seen))
 def test_provider_failure_does_not_fall_back(self):
  def fail(**kw):raise RuntimeError('provider unavailable')
  usage=namespace['Usage'](SimpleNamespace(generate=fail),'trip-controlled')
  with self.assertRaisesRegex(RuntimeError,'unavailable'):
   namespace['converse']('legacy','s',[],usage,model_role='reviewer')
if __name__=='__main__':unittest.main()

import ast,copy,sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'agent'))
from public_protocol import PublicProtocolAdapter,remap,aliases_for
from model_adapter import ModelAdapter
from model_configuration import ModelConfiguration
ROOT=Path(__file__).resolve().parents[2]
class Protocol(unittest.TestCase):
 def adapter(self,revision='executor-v6'):
  config=ModelConfiguration.load(ROOT/'agent/public-model-config.json')
  return PublicProtocolAdapter(config,protocol_revision=revision,budget_authorizer=lambda *a:None,record_sink=lambda *a:None)
 def test_aliases_and_dedup_restore_ids_without_mutating_facts(self):
  a=self.adapter('long-trip-v8')
  spec={'startDate':'2026-12-10','endDate':'2026-12-20'};pool={'places':[{'placeId':'long-a'},{'placeId':'long-b'}]}
  seen=[]
  def request(s,p):
   seen.append((a.operation,p['places'][0]['placeId']))
   return {'days':[{'date':'a','placeIds':['P1','unknown']},{'date':'b','placeIds':['P1','P2']}]}
  plan=a.plan_request(request,spec,pool,operation='proposal')
  self.assertEqual(seen,[('proposal','P1')]);self.assertEqual(plan['days'][1]['placeIds'],['long-b'])
  self.assertEqual(plan['days'][0]['placeIds'],['long-a','unknown'])
  self.assertEqual(pool['places'][0]['placeId'],'long-a');self.assertIsNone(a.operation)
  self.assertEqual(len(a.deterministic_edits),1)
 def test_context_restored_on_error(self):
  a=self.adapter()
  with self.assertRaises(ValueError):
   with a.context('correction',{}):raise ValueError()
  self.assertIsNone(a.operation)
 def test_intake_does_not_receive_narrative_prompt(self):
  a=self.adapter();response={'ok':True}
  with patch.object(ModelAdapter,'generate',return_value=response) as invoke:
   a.generate(role='executor',system='Intake JSON',messages=[],trip_id='test')
  self.assertEqual(invoke.call_args.kwargs['system'],'Intake JSON')
 def test_narrative_receives_reviewer_handoff(self):
  a=self.adapter();a.feedback={'scores':{'fit':3},'suggestions':['Explain missing hours']}
  with patch.object(ModelAdapter,'generate',return_value={}) as invoke:
   with a.context('narrative',{}):a.generate(role='executor',system='JSON',messages=[],trip_id='test')
  self.assertIn('Explain missing hours',invoke.call_args.kwargs['system'])
 def test_correction_routes_to_executor_in_real_handler(self):
  tree=ast.parse((ROOT/'agent/tp_travel_agent.py').read_text())
  node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_repair_request')
  captured={}
  ns={'prepare_pool':lambda *a:None,'DAILY_SPEND':100,'is_multi':lambda s:False,'_special_rules':lambda s:'','planning_context':lambda *a:'facts','get_prompt':lambda n:'repairer','json':__import__('json'),'PLANNER_MODEL':'unused'}
  def ask(*args,**kw):captured.update(kw);return {}
  ns['ask_json']=ask
  exec(compile(ast.Module(body=[node],type_ignores=[]),'<repair>','exec'),ns)
  ns['_repair_request']({}, {}, {}, {}, {}, [],SimpleNamespace(adapter=SimpleNamespace(operation='correction')))
  self.assertEqual(captured['model_role'],'executor')
 def test_no_second_provider_or_json_request(self):
  source=(ROOT/'agent/tp_travel_agent.py').read_text();tree=ast.parse(source)
  node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='ask_json')
  calls=[];adapter=SimpleNamespace(strict_no_retry=True,configuration=SimpleNamespace(selections={'planner':SimpleNamespace(model_id='test')}))
  ns={'json':__import__('json'),'converse':lambda *a,**k:calls.append(1) or {},'reply_text':lambda r:'invalid','extract_json':lambda s:(_ for _ in ()).throw(ValueError()),'effective_model':lambda m:m}
  exec(compile(ast.Module(body=[node],type_ignores=[]),'<json>','exec'),ns)
  with self.assertRaises(ValueError):ns['ask_json']('test','','',SimpleNamespace(adapter=adapter),model_role='planner')
  self.assertEqual(len(calls),1)
 def test_transient_error_does_not_trigger_provider_retry(self):
  tree=ast.parse((ROOT/'agent/tp_travel_agent.py').read_text())
  node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='converse')
  class Failure(Exception):code='503'
  calls=[]
  def generate(**kw):calls.append(1);raise Failure()
  ns={};exec(compile(ast.Module(body=[node],type_ignores=[]),'<converse>','exec'),ns)
  usage=SimpleNamespace(adapter=SimpleNamespace(strict_no_retry=True,generate=generate),trip_id='test')
  with self.assertRaises(Failure):ns['converse']('test','',[],usage,model_role='planner')
  self.assertEqual(len(calls),1)
if __name__=='__main__':unittest.main()

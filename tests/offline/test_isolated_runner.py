"""Full runner contract with synthetic responses; never benchmark evidence."""
import sys,json,tempfile,copy
from pathlib import Path
import test_agent_single_city as fixture
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'experiments'))
from run_isolated import run,WEIGHTS
from model_configuration import ModelConfiguration
config=ModelConfiguration.load(ROOT/'experiments/model-configs/combo_16.json')
class SyntheticAdapter:
 configuration=config
 def __init__(self):self.records=[]
 def generate(self,**kw):
  if kw['role']=='independent_evaluator':
   result={'output':{'message':{'content':[{'text':json.dumps({'scores':{k:8 for k in WEIGHTS},'issues':[]})}]}},'usage':{'inputTokens':1,'outputTokens':1}}
  else:
   system=kw['system'];system=[system] if isinstance(system,str) else system
   result=fixture.converse(system=[{'text':s} for s in system],messages=kw['messages'])
  row={'role':kw['role'],'tripId':kw['trip_id'],'modelId':config.selections[kw['role']].model_id,'success':True}
  self.records.append(row);result['_adapter']=row;return result
state=copy.deepcopy(next(iter(fixture.A.SESSIONS.values())))
data={'datasetVersion':'SYNTHETIC-OFFLINE-CONTRACT-ONLY','scenarioId':'A','capturedAt':'2026-11-01','sources':['synthetic offline contract; not live evidence'],'fxSnapshotId':'SGD-reference','spec':state['spec'],'request':state['request'],'pool':state['pool']}
with tempfile.TemporaryDirectory() as directory:
 path=Path(directory)/'fixture.json';path.write_text(json.dumps(data))
 adapter=SyntheticAdapter();result=run(config,path,adapter)
 assert result.get('evaluator',{}).get('overallScore')==8,result
 assert result.get('hardChecksPassed'),result.get('verification')
 assert result['success']
 assert {'planner','executor','reviewer','independent_evaluator'}<=set(r['role'] for r in adapter.records),adapter.records
 assert all(r['tripId']==result['tripId'] for r in adapter.records)
 assert all(c['tool'] in ('sequence_day','verify_itinerary') for c in result['toolCalls'])
 data['spec']['budgetSGD']=1
 data['spec'].pop('budget',None)
 path.write_text(json.dumps(data))
 failed=run(config,path,SyntheticAdapter())
 assert failed['evaluator']['overallScore']==8
 assert failed['hardChecksPassed'] is False and failed['success'] is False
print('ISOLATED RUNNER CONTRACT OK: frozen candidates, unchanged hard checks, independent rubric, hard failures remain failures; synthetic test only')

"""Synthetic workflow integration; never included in model benchmark evidence."""
import copy,json,tempfile
from pathlib import Path
import test_isolated_runner as base
from run_optimized import optimized_run
agent=base.fixture.A
state=copy.deepcopy(next(iter(agent.SESSIONS.values())))
data={'datasetVersion':'SYNTHETIC-OFFLINE-CONTRACT-ONLY','scenarioId':'A','capturedAt':'2026-11-01','sources':['synthetic offline contract; not live evidence'],'fxSnapshotId':'SGD-reference','spec':state['spec'],'request':state['request'],'pool':state['pool']}
class MalformedFirst(base.SyntheticAdapter):
 def __init__(self):super().__init__();self.bad=False
 def generate(self,**kw):
  if kw['role']=='planner' and not self.bad:
   self.bad=True;row={'role':'planner','tripId':kw['trip_id'],'modelId':self.configuration.selections['planner'].model_id,'success':True};self.records.append(row)
   return {'output':{'message':{'content':[{'text':json.dumps({'flightOfferId':'f','stayId':'s','days':{'wrong':'shape'}})}]}},'usage':{'inputTokens':1,'outputTokens':1},'_adapter':row}
  return super().generate(**kw)
original=(agent.propose,agent.judge,agent.ask_json)
with tempfile.TemporaryDirectory() as directory:
 path=Path(directory)/'fixture.json';path.write_text(json.dumps(data))
 result=optimized_run(base.config,path,MalformedFirst())
 assert result.get('hardChecksPassed'),result
 assert any(e['optimization']=='bounded_schema_repair' for e in result['optimizationEvents'])
 assert result.get('evaluator',{}).get('overallScore')==8
 assert original==(agent.propose,agent.judge,agent.ask_json),'Optimization hooks must not leak into later runs'
# A 429 may be a short rate window. Retry once after a free-only cooldown,
# then keep quota deferral if the provider still refuses. No billing is enabled.
from unittest.mock import patch
from model_adapter import ModelAdapterError
from model_configuration import ModelConfiguration
class QuotaOnce(base.SyntheticAdapter):
 configuration=ModelConfiguration.load(base.ROOT/'experiments/model-configs/combo_12.json')
 def __init__(self):super().__init__();self.quota_exhausted=False;self.denied=False
 def generate(self,**kw):
  if kw['role']=='planner' and not self.denied:
   self.denied=True;self.quota_exhausted=True
   self.records.append({'role':'planner','tripId':kw['trip_id'],'modelId':self.configuration.selections['planner'].model_id,'success':False,'errorCode':429})
   raise ModelAdapterError('SyntheticQuota',429)
  return super().generate(**kw)
with tempfile.TemporaryDirectory() as directory:
 path=Path(directory)/'fixture.json';path.write_text(json.dumps(data));adapter=QuotaOnce()
 with patch('run_optimized.time.sleep') as sleep:
  result=optimized_run(adapter.configuration,path,adapter)
 assert result.get('hardChecksPassed'),result
 assert any(e['optimization']=='bounded_free_quota_cooldown' and e['billingChanged'] is False for e in result['optimizationEvents'])
 assert adapter.quota_exhausted is False and sleep.called
 assert sum(r.get('errorCode')==429 for r in adapter.records)==1
class QuotaAlways(QuotaOnce):
 def generate(self,**kw):
  self.quota_exhausted=True
  self.records.append({'role':kw['role'],'tripId':kw['trip_id'],'modelId':self.configuration.selections[kw['role']].model_id,'success':False,'errorCode':429})
  raise ModelAdapterError('SyntheticPersistentQuota',429)
with tempfile.TemporaryDirectory() as directory:
 path=Path(directory)/'fixture.json';path.write_text(json.dumps(data));adapter=QuotaAlways()
 with patch('run_optimized.time.sleep'):
  failed=optimized_run(adapter.configuration,path,adapter)
 assert failed.get('hardChecksPassed') is False and adapter.quota_exhausted is True
 assert len(adapter.records)==2,'Persistent quota must stop after one bounded cooldown'
print('OPTIMIZED RUNNER CONTRACT OK: semantic repair and bounded free-quota recovery accounted, hard checks retained, hooks restored; synthetic only')

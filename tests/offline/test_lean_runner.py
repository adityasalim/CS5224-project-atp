import sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'experiments'))
from run_lean import single_json,pick

class LeanTests(unittest.TestCase):
 def test_invalid_json_is_not_retried(self):
  agent=SimpleNamespace(converse=Mock(return_value={}),reply_text=lambda r:'invalid',extract_json=Mock(side_effect=ValueError('bad JSON')))
  with self.assertRaises(ValueError):single_json(agent,'model','system','message',object(),model_role='planner')
  self.assertEqual(agent.converse.call_count,1)
 def test_existing_completed_repairs_are_kept_and_maximum_six_selected(self):
  rows=[];configs={}
  for n in range(20):
   key=f'combo_{n+1:02d}';configs[key]={r:{'provider':'google'} for r in ('planner','executor','reviewer')}
   rows.append({'parentExperimentId':key,'scenarioId':'A','repairRounds':3,'hardChecksPassed':True,'review':{'scores':{'fit':4}},'narrative':{'summary':'Existing result'},'evaluator':{'overallScore':7-n*.01},'modelInvocations':[]})
  selected=pick(rows,configs)
  self.assertEqual(len(selected),6);self.assertIn('combo_01',selected)
  self.assertTrue(all(r['repairRounds']==3 for r in rows))
if __name__=='__main__':unittest.main()

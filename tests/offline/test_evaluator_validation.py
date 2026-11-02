import sys,unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'experiments'))
from run_isolated import evaluate,WEIGHTS
class EvaluatorValidation(unittest.TestCase):
 def agent(self,result):return SimpleNamespace(Usage=lambda *a:object(),ask_json=lambda *a,**k:result)
 def invoke(self,result):
  return evaluate(self.agent(result),None,'synthetic-offline-only',{}, {'days':[]},{'flights':[],'stays':[],'places':[]}, {'passed':True,'checks':[]},None)
 def test_requested_valid_rubric_is_not_rejected_by_unrequested_issue_bounds(self):
  verdict=self.invoke({'scores':{k:8 for k in WEIGHTS},'issues':['Detailed evidence. '*80]})
  self.assertEqual(verdict['overallScore'],8);self.assertGreater(len(verdict['issues'][0]),500)
 def test_invalid_score_or_non_string_issue_still_rejected(self):
  for value in ({'scores':{k:11 for k in WEIGHTS},'issues':[]},{'scores':{k:8 for k in WEIGHTS},'issues':[{}]}):
   with self.assertRaises(ValueError):self.invoke(value)
if __name__=='__main__':unittest.main()

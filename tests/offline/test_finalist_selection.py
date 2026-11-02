import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'experiments'))
from select_finalists import choose
class Finalists(unittest.TestCase):
 def test_free_and_price_candidates_are_distinct_and_incomplete_case_not_best(self):
  configs={};rows=[]
  for parent,provider,score,cost in [('combo_01','bedrock',8,.05),('combo_12','google',7,0),('combo_15','alibaba',7.8,.01)]:
   configs[parent]={r:{'provider':provider,'model':'test'} for r in ('planner','executor','reviewer')}
   for letter in 'ABCDEF':
    rows.append({'parentExperimentId':parent,'scenarioId':letter,'repetition':1,'hardChecksPassed':True,'review':{'scores':{'fit':4}},'narrative':{'summary':'Synthetic contract'},'packageRecommendations':{'status':'custom_preferred'} if letter=='F' else None,'evaluator':{'overallScore':score},'modelInvocations':[{'provider':provider,'role':'planner','estimatedPaidTierCostUSD':cost}]})
  ids,report=choose(rows,configs)
  self.assertEqual(ids[0],'combo_15');self.assertIn('combo_12',ids);self.assertEqual(len(set(ids)),len(ids));self.assertEqual(report['status'],'VALIDATION_CANDIDATES_ONLY')
 def test_no_scores_produces_no_invented_candidate(self):
  self.assertEqual(choose([],{}),([],{'status':'NO_SCORED_CANDIDATES','candidates':[]}))
if __name__=='__main__':unittest.main()

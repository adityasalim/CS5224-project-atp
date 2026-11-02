import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'experiments'))
from summarize_results import summarize,completed_plan,comparison_interval
class AccountingTest(unittest.TestCase):
 def test_unknown_usage_retains_reservation_interval(self):
  low,high=comparison_interval([{'provider':'google','estimatedPaidTierCostUSD':None},{'provider':'bedrock','estimatedPaidTierCostUSD':'0.1'},{'provider':'alibaba','estimatedPaidTierCostUSD':None,'budgetReservationUSD':'0.2'}])
  self.assertAlmostEqual(low,.1);self.assertAlmostEqual(high,.3)
 def test_package_review_failure_is_not_completed(self):
  row={'scenarioId':'F','hardChecksPassed':True,'review':{'scores':{'fit':4}},'narrative':{'summary':'Trip'},'packageRecommendations':{'status':'review_unavailable'}}
  self.assertFalse(completed_plan(row))
  row['packageRecommendations']['status']='custom_preferred'
  self.assertTrue(completed_plan(row))
 def test_failed_run_spend_included_and_unknowns_not_zero(self):
  def call(role,cost):return {'role':role,'actualCostUSD':None,'estimatedPaidTierCostUSD':cost,'inputTokens':10,'outputTokens':5,'cacheReadTokens':None,'cacheWriteTokens':None}
  runs=[{'experimentId':'combo_01','scenarioId':'A','success':True,'hardChecksPassed':True,'modelInvocations':[call('planner','1'),call('independent_evaluator','4')],'evaluator':{'overallScore':8}},
        {'experimentId':'combo_01','scenarioId':'B','success':False,'hardChecksPassed':False,'modelInvocations':[call('planner','2')]}]
  row=summarize(runs,ROOT/'experiments/model-configs')[0]
  self.assertEqual(row['paidEquivalentCostPerCompletedPlanUSD'],3)
  self.assertEqual(row['independentEvaluatorPaidEquivalentUSD'],4)
  self.assertEqual(row['failedRunPaidEquivalentUSD'],2)
  self.assertIsNone(row['actualCostPerCompletedPlanUSD']);self.assertIsNone(row['cacheReadTokens'])
  self.assertEqual(row['successRate'],.5)
  self.assertEqual(summarize(runs,ROOT/'experiments/model-configs')[1]['status'],'PENDING')
 def test_recovered_attempt_is_completed_and_all_spend_remains(self):
  calls=[{'role':'planner','success':False,'estimatedPaidTierCostUSD':'1','actualCostUSD':None,'inputTokens':1,'outputTokens':1},
         {'role':'planner','success':True,'estimatedPaidTierCostUSD':'2','actualCostUSD':None,'inputTokens':1,'outputTokens':1}]
  run={'experimentId':'combo_01','scenarioId':'A','success':False,'hardChecksPassed':True,'verification':{'passed':True},'review':{'scores':{'fit':4}},'narrative':{'summary':'A completed itinerary'},'evaluator':{'overallScore':8},'modelInvocations':calls}
  row=summarize([run],ROOT/'experiments/model-configs')[0]
  self.assertEqual(row['completedPlans'],1);self.assertEqual(row['paidEquivalentCostPerCompletedPlanUSD'],3)
  self.assertEqual(row['failedProviderAttempts'],1);self.assertEqual(row['strictAllAttemptsSuccessfulRuns'],0)
  self.assertEqual(row['failedRunPaidEquivalentUSD'],0)
 def test_independent_evaluation_failure_does_not_erase_completed_plan(self):
  run={'experimentId':'combo_01','scenarioId':'A','success':False,'hardChecksPassed':True,'verification':{'passed':True},'review':{'scores':{'fit':4}},'narrative':{'summary':'Completed trip'},'evaluator':None,'modelInvocations':[]}
  row=summarize([run],ROOT/'experiments/model-configs')[0]
  self.assertEqual(row['completedPlans'],1);self.assertIsNone(row['meanEvaluatorScore'])
  self.assertEqual(row['independentEvaluationFailures'],1)
 def test_replayed_trip_cannot_double_count_planning_spend(self):
  row={'tripId':'same-trip','experimentId':'combo_01','scenarioId':'A','modelInvocations':[]}
  with self.assertRaises(ValueError):summarize([row,row],ROOT/'experiments/model-configs')
if __name__=='__main__':unittest.main()

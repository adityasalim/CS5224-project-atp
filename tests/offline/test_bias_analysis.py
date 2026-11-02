import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'experiments'))
from check_evaluator_bias import analyze
class BiasAnalysis(unittest.TestCase):
 def test_six_consistent_matched_advantages_flagged(self):
  rows=[]
  for letter in 'ABCDEF':
   for combo,p,s in [('combo_01',6,7),('combo_18',8,7)]:rows.append({'experimentId':combo,'scenarioId':letter,'repetition':1,'primaryScore':p,'crossEvaluator':{'overallScore':s},'hardChecksPassed':True})
  result=analyze(rows);self.assertEqual(result['pairCount'],6);self.assertTrue(result['familyPreferenceFlagged']);self.assertEqual(result['twoSidedSignTestP'],.03125)
 def test_unmatched_or_equal_scores_do_not_assert_bias(self):
  self.assertFalse(analyze([])['familyPreferenceFlagged'])
  self.assertIsNone(analyze([])['meanRelativeFamilyAdvantage'])
if __name__=='__main__':unittest.main()

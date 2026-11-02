import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'experiments'))
from recover_evaluations import accept_rubric,WEIGHTS
class RecoveryContract(unittest.TestCase):
 def test_valid_score_not_lost_for_unrequested_issue_length_limit(self):
  r=accept_rubric({'scores':{k:7 for k in WEIGHTS},'issues':['evidence '*100]},'fixed')
  self.assertEqual(r['overallScore'],7)
 def test_no_missing_out_of_range_or_boolean_scores(self):
  for invalid in (None,{'scores':{k:11 for k in WEIGHTS},'issues':[]},{'scores':{k:True for k in WEIGHTS},'issues':[]}):
   self.assertIsNone(accept_rubric(invalid,'fixed'))
 def test_missing_criterion_rejected(self):
  scores={k:8 for k in WEIGHTS};scores.pop('feasibility')
  self.assertIsNone(accept_rubric({'scores':scores,'issues':[]},'fixed'))
if __name__=='__main__':unittest.main()

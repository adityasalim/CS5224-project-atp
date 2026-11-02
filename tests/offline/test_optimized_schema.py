import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'experiments'))
from run_optimized import plan_errors
class Schema(unittest.TestCase):
 spec={'startDate':'2026-12-10','endDate':'2026-12-11'}
 def test_missing_dates_and_string_days_rejected(self):
  self.assertTrue(plan_errors({'flightOfferId':'f','stayId':'s','days':{'2026-12-10':{}}},self.spec))
  self.assertTrue(plan_errors({'flightOfferId':'f','stayId':'s','days':[]},self.spec))
 def test_complete_schema_allowed_without_changing_candidate_ids(self):
  self.assertEqual(plan_errors({'flightOfferId':'f','stayId':'s','days':[{'date':'2026-12-10','placeIds':[]},{'date':'2026-12-11','placeIds':['p']}]},self.spec),[])
if __name__=='__main__':unittest.main()

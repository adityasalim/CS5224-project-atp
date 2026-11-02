import sys,json,tempfile,unittest
from pathlib import Path
from datetime import date
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'experiments'))
from scenarios import scenarios
from run_isolated import load_dataset,WEIGHTS
class BenchmarkContracts(unittest.TestCase):
 def test_six_scenarios_share_valid_fixed_dates(self):
  rows=scenarios();self.assertEqual([r['scenarioId'] for r in rows],list('ABCDEF'))
  self.assertEqual([(date.fromisoformat(r['spec']['endDate'])-date.fromisoformat(r['spec']['startDate'])).days for r in rows],[3,10,10,10,5,3])
  self.assertEqual(rows[4]['spec']['flightType'],'oneway')
  self.assertEqual(len(rows[3]['spec']['legs']),2)
  self.assertEqual(sum(WEIGHTS.values()),100)
 def test_dataset_identity_stable_and_mock_sources_rejected(self):
  row={**scenarios()[0],'datasetVersion':'unit-contract','capturedAt':'2026-11-01','sources':['provider-reference'],'fxSnapshotId':'identity','pool':{k:[{'id':'unit-only'}] for k in ('flights','stays','places')}}
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'data.json';path.write_text(json.dumps(row))
   _,digest=load_dataset(path);self.assertEqual(load_dataset(path)[1],digest)
   row['sources']=['search_stays:mock'];path.write_text(json.dumps(row))
   with self.assertRaises(ValueError):load_dataset(path)
if __name__=='__main__':unittest.main()

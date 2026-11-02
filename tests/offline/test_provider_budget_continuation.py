"""Provider-cap exhaustion must not prevent affordable other-provider cases."""
import contextlib,io,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'experiments'))
import run_optimized as runner
class Ledger:
 def __init__(self,*a,**k):pass
 def authorize(self,*a,**k):raise AssertionError('No live request allowed in this contract')
 def record(self,row):return row
class BudgetContinuation(unittest.TestCase):
 def execute(self,reason,directory):
  def fake(config,dataset,adapter):
   letter=Path(dataset).stem
   if config.experiment_id.endswith('15'):
    return {'experimentId':config.experiment_id,'scenarioId':letter,'datasetSHA256':'synthetic-contract','success':False,'hardChecksPassed':False,'errorType':'BudgetError','budgetStop':{'provider':'alibaba','modelId':'qwen3.7-plus','reason':reason},'modelInvocations':[],'toolCalls':[],'evaluator':None}
   return {'experimentId':config.experiment_id,'scenarioId':letter,'datasetSHA256':'synthetic-contract','success':False,'hardChecksPassed':False,'modelInvocations':[],'toolCalls':[],'evaluator':None}
  args=['run_optimized.py','--configs',str(ROOT/'experiments/model-configs/combo_15.json'),str(ROOT/'experiments/model-configs/combo_16.json'),'--prices',str(ROOT/'experiments/pricing/bedrock-verified-2026-11-01.json'),'--dataset-dir',str(ROOT/'experiments/datasets/live-2026-11-01-v1'),'--output-dir',directory,'--ledger','synthetic-offline-only','--cap-usd','20','--alibaba-cap-usd','4','--repetitions','1','--max-runs','12','--alibaba-account-cap-confirmed','--execute']
  with patch.object(sys,'argv',args),patch.object(runner,'S3BudgetStore',return_value=object()),patch.object(runner,'BudgetLedger',Ledger),patch.object(runner,'optimized_run',side_effect=fake),contextlib.redirect_stdout(io.StringIO()):runner.main()
 def test_provider_limit_defers_that_model_and_other_provider_continues(self):
  with tempfile.TemporaryDirectory() as d:
   self.execute('Provider spending cap would be exceeded',d)
   self.assertEqual(len(list(Path(d).glob('combo_16-?-r1.json'))),6)
   row=json.loads((Path(d)/'combo_15-B-r1.json').read_text())
   self.assertEqual(row['status'],'DEFERRED_BUDGET');self.assertEqual(row['modelInvocations'],[])
 def test_global_limit_stops_before_any_later_case(self):
  with tempfile.TemporaryDirectory() as d:
   self.execute('Global spending cap would be exceeded',d)
   self.assertFalse((Path(d)/'combo_16-A-r1.json').exists())
if __name__=='__main__':unittest.main()

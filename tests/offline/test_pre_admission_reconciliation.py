import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'experiments'))
from reconcile_pre_admission import apply,eligible
class Reconciliation(unittest.TestCase):
 def row(self):return {'invocationId':'call','budgetReservationId':'reservation','provider':'bedrock','modelId':'global.moonshotai.kimi-k3','success':False,'requestStarted':True,'errorCode':'ThrottlingException','errorType':'ThrottlingException'}
 def state(self):return {'capUSD':'20','spentEstimateUSD':'1.23','spentByProvider':{'bedrock':'1.23'},'halted':False,'settled':{},'reservations':{'reservation':{'provider':'bedrock','modelId':'global.moonshotai.kimi-k3','invocationId':'call','usageUnknown':True,'maximumUSD':'.10'}}}
 def test_non_admitted_request_is_classified_once_without_changing_spend_or_cap(self):
  state=self.state();row=self.row();original=copy.deepcopy(row)
  self.assertEqual(len(apply(state,[('reservation',row)],'2026-11-01')),1)
  self.assertEqual(state['spentEstimateUSD'],'1.23');self.assertEqual(state['capUSD'],'20')
  self.assertEqual(state['settled']['reservation'],'0');self.assertEqual(row,original)
  self.assertEqual(apply(state,[('reservation',row)],'2026-11-01'),[])
 def test_timeouts_other_providers_or_partial_usage_keep_reservations(self):
  for changes in ({'errorCode':'ModelTimeoutException'},{'provider':'alibaba'},{'outputTokens':1},{'actualCostUSD':'0.01'}):
   state=self.state();row={**self.row(),**changes};apply(state,[('reservation',row)],'2026-11-01')
   self.assertIn('reservation',state['reservations'])
 def test_active_request_is_never_released(self):
  state=self.state();state['reservations']['reservation']['usageUnknown']=False
  self.assertEqual(apply(state,[('reservation',self.row())],'2026-11-01'),[])
  self.assertIn('reservation',state['reservations'])
if __name__=='__main__':unittest.main()

import unittest,copy,io,json
from datetime import date
from types import SimpleNamespace as N
from model_budget import BudgetLedger,BudgetError,PriceQuote,S3BudgetStore

class MemoryStore:
    def __init__(self):self.state=None;self.records={}
    def initialize(self,value):
        if self.state is None:self.state=copy.deepcopy(value)
        return copy.deepcopy(self.state)
    def update(self,fn):
        value=copy.deepcopy(self.state);result=fn(value);self.state=value;return result
    def put_record(self,row):self.records[row['invocationId']]=copy.deepcopy(row)

def quote(**overrides):
    return PriceQuote.parse({'provider':'alibaba','modelId':'qwen3.8-flash','source':'https://www.alibabacloud.com/help/en/model-studio/model-pricing',
        'verified':True,'verifiedOn':'2026-11-01','validUntil':'2026-11-30','inputPerMillionUSD':'1','outputPerMillionUSD':'2',
        'inputIncludesCache':True,**overrides})

class TestBudget(unittest.TestCase):
    def ledger(self,store=None,cap='1',quotes=None):
        return BudgetLedger(store or MemoryStore(),cap_usd=cap,provider_caps={'alibaba':cap},quotes=quotes or [quote()],today=lambda:date(2026,11,2))
    def reserve(self,ledger,max_tokens=100):return ledger.authorize(N(provider='alibaba',model_id='qwen3.8-flash'),'System',[{'role':'user','content':[{'text':'Hi'}]}],max_tokens)
    def row(self,**extra):return {'invocationId':'inv-1','provider':'alibaba','requestStarted':True,'inputTokens':100,'outputTokens':50,'cacheReadTokens':0,'cacheWriteTokens':0,'actualCostUSD':None,**extra}
    def test_restart_preserves_spending_and_settlement_is_idempotent(self):
        store=MemoryStore();ledger=self.ledger(store)
        reservation=self.reserve(ledger);row=self.row();reservation.settle(row);reservation.settle(row)
        self.assertEqual(store.state['spentEstimateUSD'],'0.0002')
        self.assertEqual(row['estimatedPaidTierCostUSD'],'0.0002');self.assertIsNone(row['actualCostUSD'])
        resumed=self.ledger(store);self.assertEqual(resumed.store.state['spentEstimateUSD'],'0.0002')
    def test_unknown_failed_usage_retains_allowance(self):
        ledger=self.ledger();reservation=self.reserve(ledger)
        reservation.settle(self.row(inputTokens=None,outputTokens=None,success=False))
        self.assertIn(reservation.identifier,ledger.store.state['reservations'])
        self.assertTrue(ledger.store.state['reservations'][reservation.identifier]['usageUnknown'])
    def test_cap_blocks_before_call(self):
        ledger=self.ledger(cap='.000001')
        with self.assertRaises(BudgetError):self.reserve(ledger)
        self.assertFalse(ledger.store.state['reservations'])
    def test_existing_cap_cannot_silently_increase(self):
        store=MemoryStore();self.ledger(store)
        with self.assertRaises(BudgetError):self.ledger(store,cap='2')
    def test_stale_price_and_unknown_model_block(self):
        ledger=self.ledger(quotes=[quote(validUntil='2026-11-01')])
        with self.assertRaises(BudgetError):self.reserve(ledger)
        with self.assertRaises(BudgetError):ledger.authorize(N(provider='bedrock',model_id='unknown'),'S',[],10)
    def test_cache_rates_are_separate(self):
        q=quote(cacheReadPerMillionUSD='.1',cacheWritePerMillionUSD='1.25')
        cost,basis=q.estimate(self.row(inputTokens=1000,outputTokens=100,cacheReadTokens=800,cacheWriteTokens=50))
        self.assertEqual(str(cost),'0.0005425')
    def test_unexpected_cost_overrun_halts_future_requests(self):
        ledger=self.ledger();r=self.reserve(ledger)
        r.settle(self.row(inputTokens=999999,outputTokens=999999))
        self.assertTrue(ledger.store.state['halted'])
        with self.assertRaises(BudgetError):self.reserve(ledger)
    def test_nonrequest_failure_releases_reservation(self):
        ledger=self.ledger();r=self.reserve(ledger)
        r.settle(self.row(requestStarted=False,inputTokens=None,outputTokens=None))
        self.assertFalse(ledger.store.state['reservations'])
        self.assertEqual(ledger.store.state['spentEstimateUSD'],'0')

    def test_free_google_cost_keeps_paid_equivalent_separate(self):
        ledger=self.ledger(quotes=[quote(provider='google',modelId='gemini-3.5-flash-lite')])
        row=self.row(provider='google',modelId='gemini-3.5-flash-lite',actualCostUSD=0)
        result=ledger.record(row)
        self.assertEqual(result['actualCostUSD'],0)
        self.assertEqual(result['estimatedPaidTierCostUSD'],'0.0002')
        self.assertEqual(ledger.store.state['spentEstimateUSD'],'0')

if __name__=='__main__':unittest.main()

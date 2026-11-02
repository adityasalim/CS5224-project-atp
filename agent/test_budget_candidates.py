import unittest,copy
from budget_candidates import prune
class BudgetPruning(unittest.TestCase):
 def spec(self):return {'budget':{'amount':'1500','currency':'SGD'},'dailySpend':{'amount':'100','currency':'SGD'},'pax':1,'startDate':'2026-12-10','endDate':'2026-12-13'}
 def row(self,amount,**extra):return {'price':{'amount':str(amount),'currency':'SGD'},**extra}
 def test_impossible_candidates_removed_without_changing_prices_or_input(self):
  p={'flights':[self.row(675),self.row(800)],'stays':[self.row('424.13'),self.row(500)],'places':[{'id':'p'}],'transfers':[]};original=copy.deepcopy(p)
  q,r=prune(self.spec(),p)
  self.assertEqual(p,original);self.assertEqual(r['removed'],{'flights':1,'stays':1,'transfers':0})
  self.assertEqual(q['stays'][0]['price']['amount'],'424.13');self.assertEqual(q['places'],p['places'])
 def test_each_city_and_transfer_minimum_counted(self):
  s=self.spec();s['budget']['amount']='1600';s['legs']=[{},{}]
  p={'flights':[self.row(500)],'stays':[self.row(200,leg=0),self.row(300,leg=1),self.row(800,leg=1)],'transfers':[self.row(100,hop=0)],'places':[]}
  q,r=prune(s,p);self.assertEqual(r['knownMinimumTotal'],'1500');self.assertEqual(len(q['stays']),2)
 def test_booked_and_unknown_prices_are_not_pruned(self):
  for row in [self.row(2000,alreadyBooked=True),{'price':{'amount':None,'currency':'SGD'}}]:
   p={'flights':[self.row(500)],'stays':[row],'places':[]}
   q,r=prune(self.spec(),p);self.assertEqual(q,p)
if __name__=='__main__':unittest.main()

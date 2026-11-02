import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'experiments'))
from run_optimized import normalize_hotel_ratings
class HotelEvidence(unittest.TestCase):
 def test_review_score_relabelled_without_changing_quote_or_source(self):
  original={'stays':[{'stayId':'s','provider':'liteapi','starRating':8.1,'reviewScore':None,'price':{'amount':'300','currency':'SGD'}}]}
  pool=copy.deepcopy(original);changes=normalize_hotel_ratings(pool)
  self.assertEqual(pool['stays'][0]['reviewScore'],8.1);self.assertIsNone(pool['stays'][0]['starRating'])
  self.assertEqual(pool['stays'][0]['price'],original['stays'][0]['price']);self.assertEqual(original['stays'][0]['starRating'],8.1)
  self.assertEqual(len(changes),1);self.assertEqual(normalize_hotel_ratings(pool),[])
 def test_existing_review_score_and_other_providers_preserved(self):
  pool={'stays':[{'stayId':'a','provider':'liteapi','starRating':8.1,'reviewScore':9.2},{'stayId':'b','provider':'duffel','starRating':4}]}
  normalize_hotel_ratings(pool);self.assertEqual(pool['stays'][0]['reviewScore'],9.2);self.assertEqual(pool['stays'][1]['starRating'],4)
if __name__=='__main__':unittest.main()

import unittest
from datetime import date
from decimal import Decimal
from copy import deepcopy
from price_normalization import normalize_candidates

class PriceTests(unittest.TestCase):
    def test_native_quote_wins_and_legacy_values_cannot_be_reused(self):
        pool={"flights":[{"priceTotal":"100","currency":"USD","priceTotalSGD":999}],
              "stays":[{"priceTotal":"50","currency":"EUR"}],"transfers":[]}
        original=deepcopy(pool)
        result=normalize_candidates(pool,"SAR",{"USD":1,"EUR":Decimal(".8"),"SAR":Decimal("3.75")},"2030-01-01",date(2030,1,1))
        self.assertEqual(result["flights"][0]["price"],{"amount":"375.00","currency":"SAR"})
        self.assertEqual(Decimal(result["stays"][0]["price"]["amount"]),Decimal("234.375"))
        self.assertNotIn("priceTotalSGD",result["flights"][0])
        self.assertEqual(pool,original)
    def test_every_supported_target(self):
        rates={"SGD":1,"USD":Decimal(".8"),"SAR":3,"EUR":Decimal(".7"),"GBP":Decimal(".6"),"CNY":5,"JPY":120}
        for target,rate in rates.items():
            result=normalize_candidates({"flights":[{"priceTotal":"100","currency":"SGD"}]},target,rates,"2030-01-01",date(2030,1,1))
            self.assertEqual(result["flights"][0]["price"]["currency"],target)
            self.assertEqual(Decimal(result["flights"][0]["price"]["amount"]),100*rate)
    def test_incomplete_or_stale_conversion_fails(self):
        for pool,rates,day in [
            ({"stays":[{}]},{"USD":1},date(2030,1,1)),
            ({"flights":[{"priceTotal":"10","currency":"EUR"}]},{"USD":1},date(2030,1,1)),
            ({},{"USD":1},date(2030,1,4))]:
            with self.assertRaises(ValueError):
                normalize_candidates(pool,"USD",rates,"2030-01-01",day)
if __name__=="__main__": unittest.main()

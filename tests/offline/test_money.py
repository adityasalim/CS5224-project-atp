import sys
import unittest
from pathlib import Path
from datetime import date
from decimal import Decimal
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"shared"))
from money import Money, convert, total_and_check

class CurrencyTests(unittest.TestCase):
    rates={"SGD":Decimal("1"),"USD":Decimal(".8"),"SAR":Decimal("3"),
           "EUR":Decimal(".7"),"GBP":Decimal(".6"),"CNY":Decimal("5"),"JPY":Decimal("120")}
    def test_every_selected_currency_drives_calculations(self):
        for currency,rate in self.rates.items():
            normalized=convert(Money("100","SGD"),currency,self.rates,"2030-01-01",date(2030,1,1))
            self.assertEqual(normalized.amount,100*rate)
            self.assertEqual(normalized.currency,currency)
            result=total_and_check(Money(100*rate,currency),[normalized])
            self.assertTrue(result["withinBudget"])
            self.assertEqual(result["total"]["currency"],currency)
    def test_mixed_currencies_are_rejected(self):
        with self.assertRaises(ValueError):
            total_and_check(Money(100,"USD"),[Money(10,"SGD")])
    def test_stale_missing_and_invalid_rates(self):
        for rates,day in [({},date(2030,1,1)),(self.rates,date(2030,1,4)),(self.rates,date(2029,12,31))]:
            with self.assertRaises(ValueError):
                convert(Money(100,"USD"),"SAR",rates,"2030-01-01",day)
        for amount in (True,"NaN","-1"):
            with self.assertRaises(ValueError): Money(amount,"USD")

if __name__=="__main__": unittest.main()

import copy
import unittest
from decimal import Decimal
from lambda_function import search_packages, validate_request, lambda_handler
from unittest.mock import patch
from types import SimpleNamespace

REQUEST = {"destinations":["Tokyo"], "startDate":"2027-02-01", "endDate":"2027-02-04",
           "pax":2, "budgetSGD":2000, "dietary":["halal"], "preferenceScores":{"nature":8}}
AGENCY = {"agencyId":"a", "status":"APPROVED", "tradingName":"Example"}
PACKAGE = {"agencyId":"a", "packageId":"p", "status":"PUBLISHED",
           "destinations":[{"city":"Tokyo"}], "availability":[{"startDate":"2027-02-01","endDate":"2027-02-04"}],
           "minimumTravellers":1, "maximumTravellers":4, "durationDays":4,
           "dietaryOptions":["Halal"], "included":["hotel"], "excluded":["flights"],
           "price":{"amount":Decimal("700"),"currency":"SGD","basis":"per_person"},
           "preferenceScores":{"nature":Decimal("8")}}

class SearchTests(unittest.TestCase):
    def search(self, package=None, agency=None, request=None):
        return search_packages(request or REQUEST, [package or PACKAGE], [agency or AGENCY])

    def test_valid_and_total_price_is_not_total_trip_cost(self):
        row = self.search()[0]
        self.assertEqual(row["partyPackagePriceSGD"], "1400")
        self.assertEqual(row["matchScore"], .8)
        self.assertEqual(row["budgetStatus"], "unverified_total_trip_cost")

    def test_dynamodb_integral_numbers(self):
        p = dict(PACKAGE, durationDays=Decimal("4"), minimumTravellers=Decimal("1"), maximumTravellers=Decimal("4"))
        self.assertEqual(len(self.search(package=p)), 1)
        p["durationDays"] = Decimal("4.5")
        self.assertEqual(self.search(package=p), [])

    def test_denies_suspended_and_unpublished(self):
        for state in ("PENDING_REVIEW", "SUSPENDED", "REJECTED"):
            a = dict(AGENCY, status=state)
            self.assertEqual(self.search(agency=a), [])
        for state in ("DRAFT", "PAUSED", "EXPIRED"):
            p = dict(PACKAGE, status=state)
            self.assertEqual(self.search(package=p), [])

    def test_hard_requirements_and_incomplete_data(self):
        for key, value in [("destinations", [{"city":"Osaka"}]), ("maximumTravellers",1),
                           ("durationDays",3), ("dietaryOptions",[]), ("availability",[]),
                           ("price", {"amount":1200,"currency":"USD","basis":"per_person"})]:
            p = copy.deepcopy(PACKAGE)
            p[key] = value
            self.assertEqual(self.search(package=p), [], key)
        self.assertEqual(self.search(request=dict(REQUEST, budgetSGD=1000)), [])
        self.assertEqual(self.search(request=dict(REQUEST, accessibilityRequired=True)), [])
        self.assertEqual(self.search(request=dict(REQUEST, origin="Singapore")), [])

    def test_native_currency_prices_and_budget_rejection(self):
        import time
        rates={'SGD':1,'USD':.8,'SAR':3,'EUR':.7,'GBP':.6,'CNY':5,'JPY':120}
        quote={'base':'SGD','rates':rates,'timestamp':time.time()}
        for currency,rate in rates.items():
            request={k:v for k,v in REQUEST.items() if k!='budgetSGD'}
            request.update(budget={'amount':str(Decimal('2000')*Decimal(str(rate))),'currency':currency},fxQuote=quote)
            result=self.search(request=request)[0]
            self.assertEqual(result['currency'],currency)
            self.assertEqual(Decimal(result['partyPackagePrice']),Decimal('1400')*Decimal(str(rate)))
            self.assertNotIn('partyPackagePriceSGD',result)
            request['budget']['amount']='1'
            self.assertEqual(self.search(request=request),[])

    def test_native_included_flights_require_matching_origin_and_direction(self):
        import time
        req={k:v for k,v in REQUEST.items() if k!='budgetSGD'}
        req.update(budget={'amount':'2000','currency':'SGD'},fxQuote={'base':'SGD','rates':{'SGD':1},'timestamp':time.time()},origin='SIN',flightType='round')
        package=dict(PACKAGE,costInclusions={'flights':True},departureLocations=['SIN'],flightType='round')
        self.assertEqual(len(self.search(package=package,request=req)),1)
        self.assertEqual(self.search(package=package,request=dict(req,origin='LHR')),[])
        self.assertEqual(self.search(package=package,request=dict(req,flightType='oneway')),[])
        package.update(costInclusions={'flights':False},flightType='none',departureLocations=[])
        self.assertEqual(len(self.search(package=package,request=req)),1)

    def test_invalid_request_and_deduplication(self):
        with self.assertRaises(ValueError):
            validate_request(dict(REQUEST, pax=True))
        self.assertEqual(len(search_packages(REQUEST, [PACKAGE, PACKAGE], [AGENCY])), 1)

    def test_suspension_after_scan_is_excluded(self):
        class Table:
            def __init__(self, agency):
                self.agency = agency
            def scan(self, **kwargs):
                return {"Items": [AGENCY if self.agency else PACKAGE]}
            def get_item(self, **kwargs):
                return {"Item": dict(AGENCY, status="SUSPENDED") if self.agency else PACKAGE}
        db = SimpleNamespace(Table=lambda name: Table(name == "agencies"))
        fake = SimpleNamespace(resource=lambda name: db)
        with patch.dict("os.environ", {"AGENCIES_TABLE": "agencies", "PACKAGES_TABLE": "packages"}), patch.dict("sys.modules", {"boto3": fake}):
            result = lambda_handler(REQUEST, None)
        self.assertTrue(result["ok"])
        self.assertEqual(result["packages"], [])

if __name__ == "__main__":
    unittest.main()

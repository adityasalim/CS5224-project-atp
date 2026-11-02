import sys
import unittest
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lambdas" / "tp-ahmed-agency-api"))
from marketplace_domain import validate_package, validate_agency, validate_transition, require_owner, ValidationError

def package():
    return {"title":"Tokyo", "description":"A trip", "durationDays":4,
            "destinations":[{"city":"Tokyo","country":"Japan","nights":3}],
            "minimumTravellers":1,"maximumTravellers":4,
            "price":{"amount":"1200","currency":"SGD","basis":"per_person"},
            "included":["hotel"],"excluded":["flights"],
            "availability":[{"startDate":"2030-01-01","endDate":"2030-02-01"}]}

class Tests(unittest.TestCase):
    def test_persisted_integer_fields(self):
        p = package()
        p.update(durationDays=Decimal("4"), minimumTravellers=Decimal("1"), maximumTravellers=Decimal("4"))
        p["destinations"][0]["nights"] = Decimal("3")
        self.assertEqual(validate_package(p,date(2029,1,1))["durationDays"],4)
        p["durationDays"] = Decimal("4.5")
        with self.assertRaises(ValidationError):
            validate_package(p,date(2029,1,1))

    def test_valid(self):
        self.assertEqual(validate_package(package(),date(2029,1,1))["price"]["currency"],"SGD")
    def test_invalid_price_duration_capacity_dates(self):
        for mutate in (lambda x:x["price"].update(amount="-1"),
                       lambda x:x.update(durationDays=5),
                       lambda x:x.update(maximumTravellers=0),
                       lambda x:x.update(availability=[{"startDate":"2020-01-01","endDate":"2020-01-02"}])):
            p=package();mutate(p)
            with self.assertRaises(ValidationError): validate_package(p,date(2029,1,1))
    def test_owner_and_status(self):
        with self.assertRaises(PermissionError): require_owner("other",{"ownerSub":"owner"})
        with self.assertRaises(PermissionError): validate_transition("package","PENDING_REVIEW","PUBLISHED","agency","APPROVED")
        with self.assertRaises(PermissionError): validate_transition("package","PENDING_REVIEW","PUBLISHED","admin","SUSPENDED")
        self.assertEqual(validate_transition("package","PENDING_REVIEW","PUBLISHED","admin","APPROVED"),"PUBLISHED")
    def test_trusted_fields_not_copied(self):
        p=package();p.update(agencyId="fake",status="PUBLISHED")
        validated=validate_package(p,date(2029,1,1))
        self.assertNotIn("agencyId",validated);self.assertNotIn("status",validated)
    def test_agency_requires_terms(self):
        with self.assertRaises(ValidationError): validate_agency({})
    def test_dietary_accessibility_validation(self):
        p=package();p["accessible"]="yes"
        with self.assertRaises(ValidationError): validate_package(p,date(2029,1,1))

if __name__ == "__main__":
    unittest.main()

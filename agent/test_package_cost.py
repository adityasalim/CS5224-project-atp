import unittest
from package_cost import compare_package_cost

class CostTests(unittest.TestCase):
    def test_unknown_cost_never_claims_affordable(self):
        r = compare_package_cost("100", "per_person", 2, {}, {}, 1000)
        self.assertIsNone(r["withinBudget"])
        self.assertIsNone(r["estimatedTotalSGD"])
    def test_complete_cost_does_not_double_count(self):
        inc = {k: True for k in ("flights", "accommodation", "transfers", "meals", "activities", "mandatory_fees")}
        inc["flights"] = False
        r = compare_package_cost("100", "per_person", 2, inc, {"flights": "60", "meals": "900"}, 250)
        self.assertEqual(r["estimatedTotalSGD"], "260")
        self.assertFalse(r["withinBudget"])
    def test_invalid_numbers_are_rejected(self):
        for price in ("-1", "NaN"):
            with self.assertRaises(ValueError):
                compare_package_cost(price, "total", 1, {}, {}, 100)
if __name__ == "__main__":
    unittest.main()

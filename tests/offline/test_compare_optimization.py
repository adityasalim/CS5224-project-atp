import unittest
from compare_optimization import compare


class CompareTests(unittest.TestCase):
    def test_paired_means_and_unavailable_cost(self):
        records = [{"scenario": "tokyo", "trial": 1, "variant": "original", "modelCalls": 13, "hardChecksPassed": True},
                   {"scenario": "tokyo", "trial": 1, "variant": "new", "modelCalls": 8, "hardChecksPassed": False}]
        result = compare(records, "original", "new")
        self.assertEqual(result["measurements"]["modelCalls"]["meanDifference"], -5)
        self.assertEqual(result["measurements"]["hardChecksPassed"]["meanDifference"], -1)
        self.assertEqual(result["measurements"]["estimatedCostUSD"]["completePairs"], 0)

    def test_missing_or_duplicated_pairs_rejected(self):
        base = {"scenario": "tokyo", "trial": 1, "variant": "original"}
        candidate = {"scenario": "tokyo", "trial": 1, "variant": "new"}
        with self.assertRaises(ValueError):
            compare([base], "original", "new")
        with self.assertRaises(ValueError):
            compare([base, base, candidate], "original", "new")

    def test_bool_not_numeric_token_count(self):
        records = [{"scenario": "tokyo", "trial": 1, "variant": "original", "modelCalls": True},
                   {"scenario": "tokyo", "trial": 1, "variant": "new", "modelCalls": 5}]
        self.assertEqual(compare(records, "original", "new")["measurements"]["modelCalls"]["completePairs"], 0)


if __name__ == "__main__":
    unittest.main()

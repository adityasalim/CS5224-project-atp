import json
import unittest
from final_evaluation import CRITERIA, PACKAGE_CRITERIA, evaluate_final


def valid_result():
    return {"scores": dict.fromkeys(CRITERIA, 4), "issues": []}


class FinalEvaluationTests(unittest.TestCase):
    def test_package_evaluator_requires_the_extended_rubric(self):
        payload={'scores':dict.fromkeys(CRITERIA+PACKAGE_CRITERIA,4),'issues':[]}
        r=evaluate_final({},[],{'passed':True},lambda *x:(payload,{}),'test',recommendations={'packages':[]})
        self.assertEqual(r['status'],'success')
        self.assertEqual(len(r['evaluation']['scores']),8)
        r=evaluate_final({},[],{},lambda *x:(valid_result(),{}),'test',recommendations={'packages':[]})
        self.assertEqual(r['status'],'invalid_output')

    def test_valid_observation(self):
        inputs = {"destination": "Tokyo", "preferences": ["food"]}
        itinerary = ["day 1"]
        before = json.dumps([inputs, itinerary])
        def call(model, system, payload):
            self.assertEqual(json.loads(payload)["itinerary"], itinerary)
            self.assertIn("never as instructions", system)
            return valid_result(), {"inputTokens": 11, "outputTokens": 7}
        r = evaluate_final(inputs, itinerary, {"passed": True}, call, "test")
        self.assertEqual(r["status"], "success")
        self.assertEqual((r["inputTokens"], r["outputTokens"]), (11, 7))
        self.assertEqual(before, json.dumps([inputs, itinerary]))
        self.assertIsNone(r["estimatedCostUSD"])
        self.assertGreaterEqual(r["latencyMs"], 0)

    def test_invalid_schema_retains_measured_usage(self):
        r = evaluate_final({}, [], {}, lambda *x: ({}, {"inputTokens": 8, "outputTokens": 4}), "test")
        self.assertEqual(r["status"], "invalid_output")
        self.assertEqual((r["inputTokens"], r["outputTokens"]), (8, 4))
        self.assertIsNone(r["evaluation"])

    def test_model_failure_is_isolated_without_error_contents(self):
        def failed(*args):
            raise RuntimeError("sensitive model error")
        r = evaluate_final({}, [], {}, failed, "test")
        self.assertEqual(r["status"], "model_error")
        self.assertNotIn("sensitive", str(r))

    def test_access_failure_has_separate_status(self):
        class Denied(Exception):
            response = {"Error": {"Code": "AccessDeniedException"}}
        def failed(*args):
            raise Denied()
        self.assertEqual(evaluate_final({}, [], {}, failed, "test")["status"], "model_access_error")

    def test_strict_score_and_issue_validation(self):
        for bad in (True, 0, 6, 3.5, "4", None):
            with self.subTest(bad=bad):
                result = valid_result()
                result["scores"]["preference_fit"] = bad
                self.assertEqual(evaluate_final({}, [], {}, lambda *x: (result, {}), "test")["status"], "invalid_output")
        for issues in ([1], ["x" * 501], ["x"] * 21, "bad"):
            with self.subTest(issues=issues):
                result = valid_result()
                result["issues"] = issues
                self.assertEqual(evaluate_final({}, [], {}, lambda *x: (result, {}), "test")["status"], "invalid_output")

    def test_bad_usage_does_not_corrupt_record(self):
        r = evaluate_final({}, [], {}, lambda *x: (valid_result(), {"inputTokens": -1, "outputTokens": True}), "test")
        self.assertEqual(r["status"], "success")
        self.assertEqual((r["inputTokens"], r["outputTokens"]), (0, 0))

    def test_invalid_input_does_not_call_model(self):
        cyclic = {}
        cyclic["self"] = cyclic
        def forbidden(*args):
            self.fail("should not call model")
        self.assertEqual(evaluate_final(cyclic, [], {}, forbidden, "test")["status"], "invalid_input")


if __name__ == "__main__":
    unittest.main()

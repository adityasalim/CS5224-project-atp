"""Runs every Lambda with its console test event, in order (same as the Lambda README), in mock mode."""
import json, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from fakes import load, REPO

ORDER = ["tp-search-flights", "tp-search-stays", "tp-find-places", "tp-route-matrix", "tp-search-transfers",
         "tp-sequence-day", "tp-verify-itinerary", "tp-render-pdf"]
for name in ORDER:
    event = json.load(open(REPO / "lambdas" / "test-events" / f"{name}.json"))
    res = load(name).lambda_handler(event, None)
    print(f"{name:<22} ok={res.get('ok')} {res.get('source', '')} {res.get('error', '')}")
    assert res.get("ok"), res
    if name == "tp-verify-itinerary":
        assert res.get("passed"), res.get("violations")
print("LAMBDAS OK")

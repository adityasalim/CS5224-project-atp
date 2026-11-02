"""Direct gathering must not invoke a model or add a return flight to one-way trips."""
import copy
from types import SimpleNamespace
import test_agent_single_city as fixture
A = fixture.A
seen = []
def call(tool, args):
    seen.append((tool, copy.deepcopy(args)))
    return {"ok": True, "source": "live-reference", "offers": [{"offerId": "f"}],
            "stays": [{"stayId": "h"}], "places": [{"placeId": "p"}]}
def forbidden(*args, **kwargs):
    raise AssertionError("Direct gathering invoked a model")
A._clients = lambda: (None, None, SimpleNamespace(call=call))
A.converse = forbidden
spec = {"originAirport": "SIN", "destinationAirport": "HND", "destination": "Tokyo",
        "startDate": "2026-12-10", "endDate": "2026-12-15", "pax": 2,
        "flightType": "oneway", "interests": ["museum"], "activities": [],
        "preferences": {}, "mustInclude": [], "dayTrips": []}
metrics = {}
gen = A.gather(spec, A.Usage(), metrics)
while True:
    try: next(gen)
    except StopIteration as result:
        pool = result.value
        break
assert len(seen) == 3, seen
assert "returnDate" not in seen[0][1] and "returnFrom" not in seen[0][1]
assert "restaurant" in seen[2][1]["categories"] and "museum" in seen[2][1]["categories"]
assert metrics["gatherMode"] == "single-city (code)" and not metrics["fallbackCalls"]
assert all(pool[k] for k in ("flights", "stays", "places"))
print("DIRECT GATHER OK: zero model calls, three searches, one-way arguments and categories preserved")

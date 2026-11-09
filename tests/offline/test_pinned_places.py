"""A pinned place the traveler named is kept by sequence_day, and the verifier names a missing mustVisit place."""
import json
from fakes import load, STORE

seq, verify = load("tp-sequence-day"), load("tp-verify-itinerary")


def put(p):
    STORE["pool:" + p["id"]] = {"cacheKey": "pool:" + p["id"], "payload": json.dumps(p)}


# Staging run: a 09:00-21:00 day with Tokyo Disneyland (~65 min by taxi from the hotel) and three cafes next to it.
# Three cafes alone would make more stops; the park must win. Owner-approved (final tools round): the park has no
# hours in the pool, so it is open 09:00-21:00 and the visit runs from arrival to 21:00 minus the ride home: the
# cafes no longer fit after it.
put({"id": "t:hotel", "name": "Hotel Akasaka", "category": "stay", "lat": 35.6640, "lng": 139.7200})
put({"id": "t:tdl", "name": "Tokyo Disneyland", "kind": "place", "category": "requested", "lat": 35.6329, "lng": 139.8804})
cafes = ["t:cafe1", "t:cafe2", "t:cafe3"]
for k, (lat, lng) in enumerate([(35.6355, 139.8830), (35.6340, 139.8760), (35.6310, 139.8840)]):
    put({"id": cafes[k], "name": f"Cafe {k + 1}", "kind": "place", "category": "cafe", "lat": lat, "lng": lng})
event = {"date": "2026-12-11", "stayId": "t:hotel", "placeIds": cafes + ["t:tdl"],
         "dayStart": "09:00", "dayEnd": "21:00"}

for label, ev in [("category requested", event), ("pinned list", dict(event, pinned=["t:tdl"]))]:
    r = seq.lambda_handler(ev, None)
    ids = [s["placeId"] for s in r["stops"]]
    park = next(s for s in r["stops"] if s["placeId"] == "t:tdl") if "t:tdl" in ids else None
    print(label, ids, "unscheduled:", [u["placeId"] for u in r["unscheduled"]])
    assert r["ok"] and park, r
    # the park leaves its 3 cafes 60 min each after it (45 min visit + 15 min to get there), so it ends at 18:02
    # (21:00 - 62 min home - 180 min); two cafes fit after it and the farthest one is dropped
    assert 60 <= park["travelMinutesFromPrev"] <= 70 and park["departAt"] == "18:02", park
    assert len(r["unscheduled"]) == 1 and r["unscheduled"][0]["placeId"] in cafes, r["unscheduled"]

# An explicit empty pinned list turns the category fallback off; the park still opens the day (run 8: a theme park
# day starts with the park, above the most stops), with the cafes that fit after it.
r = seq.lambda_handler(dict(event, pinned=[]), None)
assert r["stops"][0]["placeId"] == "t:tdl" and len(r["unscheduled"]) == 1, r

# Verifier: a mustVisit place missing from the itinerary is named, not shown as its raw ID.
spec = {"startDate": "2026-12-11", "endDate": "2026-12-11", "pax": 2, "budgetSGD": 9000, "mustVisit": ["t:tdl"]}
it = {"stayId": "t:hotel", "days": [{"date": "2026-12-11", "stops": [
    {"placeId": "t:cafe1", "arriveAt": "10:30", "departAt": "11:15", "travelMinutesFromPrev": 65}]}]}
v = verify.lambda_handler({"tripSpec": spec, "itinerary": it}, None)
msgs = [x["message"] for x in v["violations"] if x["check"] == "must_visit"]
assert msgs == ["Tokyo Disneyland was requested by the user but is not planned"], v["violations"]
# a malformed pinned list (dicts, a string) never crashes the tool
for bad in ([{"placeId": "t:tdl"}], "t:tdl"):
    assert seq.lambda_handler(dict(event, pinned=bad), None)["ok"], bad
print("PINNED PLACES OK")

"""New tool features: named places, named/booked hotels, mustVisit and day-trip checks in the verifier."""
from fakes import load, STORE
import json
places, stays, verify, seq = load("tp-find-places"), load("tp-search-stays"), load("tp-verify-itinerary"), load("tp-sequence-day")

r = places.lambda_handler({"city": "Tokyo", "names": ["teamLab Planets", "Ichiran Shibuya"]}, None)
assert r["ok"] and [p["requested"] for p in r["places"]] == ["teamLab Planets", "Ichiran Shibuya"], r
assert all("requested by user" in p["tags"] for p in r["places"])
r2 = places.lambda_handler({"city": "Tokyo", "names": ["teamLab Planets"], "categories": ["museum"], "avoid": ["sample"]}, None)
assert r2["places"][0].get("requested") and len(r2["places"]) == 1, "named place must survive filters; others filtered"
named_id = r["places"][0]["placeId"]

b = stays.lambda_handler({"city": "Tokyo", "checkIn": "2026-12-10", "checkOut": "2026-12-12", "guests": 2,
                          "name": "Hotel Gracery Shinjuku", "alreadyBooked": True, "bookedPriceSGD": 900}, None)
assert b["source"] == "user-booking" and len(b["stays"]) == 1 and b["stays"][0]["priceTotalSGD"] == 900, b
n = stays.lambda_handler({"city": "Tokyo", "checkIn": "2026-12-10", "checkOut": "2026-12-15", "guests": 2,
                          "name": "Park Hyatt Tokyo", "maxPricePerNightSGD": 100}, None)
assert n["stays"][0]["name"] == "Park Hyatt Tokyo" and "requested hotel" in n["stays"][0]["matches"], n["stays"][:2]

# verifier: mustVisit + day trip (Nikko from a Tokyo hotel)
pl = places.lambda_handler({"city": "Nikko", "categories": ["temple"]}, None)["places"]
stay_id = b["stays"][0]["stayId"]
spec = {"startDate": "2026-12-10", "endDate": "2026-12-12", "pax": 2, "budgetSGD": 9000, "mustVisit": [named_id]}
fl = load("tp-search-flights").lambda_handler({"origin": "SIN", "destination": "HND", "departDate": "2026-12-10",
                                                "returnDate": "2026-12-12", "pax": 2}, None)["offers"][0]["offerId"]
trip = {"place": "Nikko", "lat": 36.7199, "lng": 139.6982, "travelMinutes": 130}
it = {"flightOfferId": fl, "stayId": stay_id, "days": [
    {"date": "2026-12-10", "stops": []},
    {"date": "2026-12-11", "dayTrip": trip, "stops": [
        {"placeId": pl[0]["placeId"], "arriveAt": "09:30", "departAt": "10:30"}]},
    {"date": "2026-12-12", "stops": []}]}
v = verify.lambda_handler({"tripSpec": spec, "itinerary": it}, None)
fails = {x["check"] for x in v["violations"]}
print("violations:", sorted(fails))
assert {"must_visit", "day_trip"} <= fails and "far_from_base" not in fails, v["violations"]
it["days"][1]["stops"] = [{"placeId": pl[0]["placeId"], "arriveAt": "11:30", "departAt": "12:30"},
                          {"placeId": named_id, "arriveAt": "13:00", "departAt": "14:00"}]
v = verify.lambda_handler({"tripSpec": spec, "itinerary": it}, None)
fails = {x["check"] for x in v["violations"]}
assert "must_visit" not in fails and "day_trip" not in fails and "far_from_base" in fails, v["violations"]  # Tokyo place on a Nikko day
far = dict(trip, lat=43.06, lng=141.35, place="Sapporo")
it["days"][1].update(dayTrip=far, stops=[{"placeId": pl[0]["placeId"], "arriveAt": "11:30", "departAt": "12:30"}])
v = verify.lambda_handler({"tripSpec": spec, "itinerary": it}, None)
assert any(x["check"] == "day_trip" and "Too far" in x["repairHint"] for x in v["violations"])
s = seq.lambda_handler({"date": "2026-12-11", "start": {"id": "daytrip", "name": "Nikko", "lat": 36.72, "lng": 139.70},
                        "placeIds": [p["placeId"] for p in pl[:2]], "dayStart": "11:10", "dayEnd": "18:50"}, None)
assert s["ok"] and s["stops"][0]["arriveAt"] >= "11:10", s
print("ROBUST TOOLS OK")

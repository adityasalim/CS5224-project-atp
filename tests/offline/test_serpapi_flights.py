"""tp-search-flights with SerpApi Google Flights, using canned SerpApi responses (no network, no key)."""
from fakes import load
import json, os
os.environ["MOCK_MODE"] = "false"
f = load("tp-search-flights")
f._secret_cache = {"SERPAPI_KEY": "test-key"}
CALLS = []
def seg(frm, to, dep, arr, num, airline="Singapore Airlines"):
    return {"departure_airport": {"id": frm, "time": dep}, "arrival_airport": {"id": to, "time": arr},
            "duration": 400, "airline": airline, "flight_number": num}
OUT = {"search_metadata": {"google_flights_url": "https://www.google.com/travel/flights?x=1"},
       "best_flights": [{"flights": [seg("SIN", "HND", "2026-12-10 08:05", "2026-12-10 16:00", "SQ 638")],
                         "total_duration": 415, "price": 1650, "departure_token": "tokA"}],
       "other_flights": [{"flights": [seg("SIN", "TPE", "2026-12-10 01:00", "2026-12-10 05:30", "CI 754", "China Airlines"),
                                      seg("TPE", "HND", "2026-12-10 08:00", "2026-12-10 12:10", "CI 220", "China Airlines")],
                          "total_duration": 610, "price": 1210, "departure_token": "tokB"}]}
def back(tok):
    return {"search_metadata": {"google_flights_url": "https://www.google.com/travel/flights?x=2"},
            "best_flights": [{"flights": [seg("HND", "SIN", "2026-12-15 11:00", "2026-12-15 17:30", "SQ 635" if tok == "tokA" else "CI 221")],
                              "total_duration": 450, "price": 1700 if tok == "tokA" else 1250}]}
class R:
    def __init__(s, d): s.status_code, s._d, s.text = 200, d, json.dumps(d)
    def json(s): return s._d
def fake_get(url, params, timeout):
    CALLS.append(params)
    assert params["api_key"] == "test-key" and params["currency"] == "SGD" and params["adults"] == 2
    return R(back(params["departure_token"]) if params.get("departure_token") else OUT)
f.requests.get = fake_get
ev = {"origin": "SIN", "destination": "HND", "departDate": "2026-12-10", "returnDate": "2026-12-15", "pax": 2}
r = f.lambda_handler(ev, None)
print(r["source"], [(o["airline"], o["priceTotalSGD"], [s["flightNumbers"] for s in o["slices"]]) for o in r["offers"]])
assert r["ok"] and r["source"] == "google_flights" and len(CALLS) == 3, (r, len(CALLS))
o = r["offers"][0]
assert o["priceTotalSGD"] == 1250 and o["slices"][0]["departAt"] == "2026-12-10T01:00:00" and o["slices"][0]["stops"] == 1
assert o["slices"][1]["from"] == "HND" and o["bookingUrl"].startswith("https://www.google.com/travel/flights")
assert o["offerId"].startswith("gf:") and o["slices"][0]["duration"] == "PT10H10M"
# preferences still work: nonstop only -> the SQ offer
r2 = f.lambda_handler({**ev, "maxStops": 0}, None)
assert r2["offers"][0]["airline"] == "Singapore Airlines" and r2["source"].endswith("cache") and len(CALLS) == 3, "cache not used"
# open-jaw uses multi-city search
CALLS.clear()
r3 = f.lambda_handler({**ev, "returnFrom": "KIX"}, None)
assert CALLS[0]["type"] == 3 and json.loads(CALLS[0]["multi_city_json"])[1]["departure_id"] == "KIX"
# no keys at all -> clear error
f._secret_cache = {}
r4 = f.lambda_handler({**ev, "departDate": "2026-12-11"}, None)
assert r4["error"] == "not_configured", r4
print("SERPAPI FLIGHTS OK")

# ---- tp-search-transfers: flights between cities from Google Flights
t = load("tp-search-transfers")
t._secret_cache = {"SERPAPI_KEY": "test-key"}
TC = []
def fake_get2(url, params, timeout):
    TC.append(params); assert params["type"] == 2 and params["departure_id"] == "CPH" and params["arrival_id"] == "BOO"
    return R({"search_metadata": {"google_flights_url": "https://www.google.com/travel/flights?y=1"},
              "best_flights": [{"flights": [seg("CPH", "OSL", "2027-02-13 07:00", "2027-02-13 08:10", "SK 1455", "SAS"),
                                            seg("OSL", "BOO", "2027-02-13 09:30", "2027-02-13 11:00", "SK 4110", "SAS")],
                                "total_duration": 240, "price": 640}]})
t.requests.get = fake_get2
r = t.lambda_handler({"from": {"city": "Copenhagen", "airport": "CPH"}, "to": {"city": "Lofoten", "airport": "BOO"},
                      "date": "2027-02-13", "pax": 2}, None)
fl = [o for o in r["options"] if o["mode"] == "flight"]
print(r["source"], [(o["summary"], o["priceTotalSGD"], o["departAt"]) for o in fl])
assert r["source"] == "google_flights+estimate" and fl[0]["priceTotalSGD"] == 640 and fl[0]["stops"] == 1
assert fl[0]["bookingUrl"] and fl[0]["transferId"].startswith("tgf:") and len(TC) == 1
print("SERPAPI TRANSFERS OK")

# ---- an old Duffel result in the cache is ignored once SERPAPI_KEY is set
f._secret_cache = {"SERPAPI_KEY": "test-key"}; f.requests.get = fake_get; CALLS.clear()
ev2 = {**ev, "departDate": "2026-12-12"}
f._cache_put(f._cache_key(f._api_query(f._validate(ev2)[0])), {"allOffers": [{"offerId": "off_duffel", "airline": "Duffel Airways",
             "priceTotal": 500, "currency": "SGD", "priceTotalSGD": 500, "slices": []}]})
r5 = f.lambda_handler(ev2, None)
assert r5["source"] == "google_flights" and "Duffel" not in json.dumps(r5["offers"]), r5["source"]
print("OLD CACHE IGNORED OK")

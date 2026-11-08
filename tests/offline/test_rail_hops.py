"""Country rail model and door-to-door rule in tp-search-transfers.
Offline: fake AWS, fake geocoder, fake SerpApi; nothing leaves the machine."""
import os
from fakes import load, STORE

t = load("tp-search-transfers")
DATE = "2027-03-10"


def hop(frm, to, **k):
    return {"from": frm, "to": to, "date": DATE, "pax": 1, **k}


def per_adult(o, cur, pax=1):
    return o["priceTotalSGD"] / pax / t.FX_TO_SGD[cur]


# ---- Tokyo -> Kyoto: Shinkansen from the JP profile, no flight search, rail recommended
r = t.lambda_handler(hop({"city": "Tokyo", "airport": "HND"}, {"city": "Kyoto", "airport": "KIX"}, pax=2), None)
assert [o["mode"] for o in r["options"]] == ["ground"], r["options"]
g = r["options"][0]
assert 120 <= g["durationMinutes"] <= 140 and g["durationMinutes"] == 128, g
assert 13500 <= per_adult(g, "JPY", 2) <= 14500 and round(per_adult(g, "JPY", 2)) == 13927, per_adult(g, "JPY", 2)
assert g["doorToDoorMinutes"] == 128 + 50 and g["recommended"] and g["estimate"], g
assert g["estimateBasis"].startswith("JP") and g["summary"] == "Train Tokyo → Kyoto (fare estimate)"
assert g["bookingUrl"].startswith("https://www.rome2rio.com/") and (g["departAt"][11:], g["arriveAt"][11:]) == ("09:00", "11:08")
print("TOKYO-KYOTO OK", g["durationMinutes"], "min", round(per_adult(g, "JPY", 2)), "JPY pp")

# prefer=flight still searches the flight, and the flight is then the recommended option
r = t.lambda_handler(hop({"city": "Tokyo", "airport": "HND"}, {"city": "Kyoto", "airport": "KIX"}, prefer="flight"), None)
fl = [o for o in r["options"] if o["mode"] == "flight"]
assert fl and fl[0]["recommended"] and fl[0]["doorToDoorMinutes"] == fl[0]["durationMinutes"] + 185, r["options"]
assert sum(o["recommended"] for o in r["options"]) == 1

# ---- a long hop inside Japan (Tokyo -> Sapporo, 270 min by the model) searches flights; the flight wins door to door
r = t.lambda_handler(hop({"city": "Tokyo", "airport": "HND"}, {"city": "Sapporo", "airport": "CTS"}), None)
by = {o["mode"]: o for o in r["options"]}
assert set(by) == {"flight", "ground"} and by["ground"]["durationMinutes"] > 180, r["options"]
assert by["flight"]["recommended"] and not by["ground"]["recommended"], r["options"]
assert by["flight"]["doorToDoorMinutes"] < by["ground"]["doorToDoorMinutes"]
print("TOKYO-SAPPORO OK", {m: o["doorToDoorMinutes"] for m, o in by.items()})

# ---- a country without a profile keeps the old estimate (Oslo -> Bergen, NO; Copenhagen -> Lofoten, DK -> NO)
for a, b in (("Oslo", "Bergen"), ("Copenhagen", "Lofoten")):
    q, _ = t._validate(hop({"city": a}, {"city": b}))
    g = t._ground(q)
    assert t._rail(q) is None and "estimateBasis" not in g and g["summary"].startswith("Train / bus / ferry"), g
    assert g["durationMinutes"] == int(q["km"] * 1.3 / 70 * 60 + 30) and g["doorToDoorMinutes"] == g["durationMinutes"]
assert t._validate(hop({"city": "Oslo"}, {"city": "Bergen"}))[0]["cc"] == "NO"
assert t._validate(hop({"city": "Copenhagen"}, {"city": "Lofoten"}))[0]["cc"] is None
# lat/lng given: the country still comes from the known city
assert t._validate(hop({"city": "Osaka, Japan", "lat": 34.69, "lng": 135.50}, {"city": "Tokyo"}))[0]["cc"] == "JP"
print("NO PROFILE OK")

# ---- live mode with a fake geocoder: Paris -> Lyon (FR profile, no flight search) and London -> Rome (two countries)
GEO = {"Paris": ([2.3744, 48.8443], "FR"), "Lyon": ([4.8597, 45.7606], "FR"),
       "London": ([-0.1238, 51.5308], "GB"), "Rome": ([12.4964, 41.9028], "IT")}


class FakeGeo:
    def geocode(self, QueryText, MaxResults):
        pos, cc = GEO[QueryText]
        return {"ResultItems": [{"Position": pos, "Address": {"Country": {"Code2": cc, "Name": "x"}}}]}


SERP = []


class Resp:
    status_code = 200
    def json(self):
        seg = lambda a, b, d, e: {"departure_airport": {"id": a, "time": d}, "arrival_airport": {"id": b, "time": e},
                                  "flight_number": "BA 548", "airline": "British Airways"}
        return {"search_metadata": {"google_flights_url": "https://www.google.com/travel/flights?x=1"},
                "best_flights": [{"flights": [seg("LHR", "FCO", f"{DATE} 08:00", f"{DATE} 11:30")],
                                  "total_duration": 150, "price": 210}]}


def fake_get(url, params, timeout):
    SERP.append(params["departure_id"] + "-" + params["arrival_id"])
    return Resp()


saved_client = t.boto3.client
t.boto3.client = lambda name, **k: FakeGeo() if name == "geo-places" else saved_client(name, **k)
t.requests.get = fake_get
t._secret_cache = {"SERPAPI_KEY": "test-key"}
os.environ["MOCK_MODE"] = "false"
try:
    r = t.lambda_handler(hop({"city": "Paris", "airport": "CDG"}, {"city": "Lyon", "airport": "LYS"}), None)
    assert r["ok"] and SERP == [] and [o["mode"] for o in r["options"]] == ["ground"], (SERP, r)
    g = r["options"][0]
    assert 105 <= g["durationMinutes"] <= 125 and 25 <= per_adult(g, "EUR") <= 60 and g["recommended"], g
    assert g["estimateBasis"].startswith("FR")
    assert any(k.startswith("transfers-v3:") for k in STORE), "cache key prefix bumped"
    print("PARIS-LYON OK", g["durationMinutes"], "min", round(per_adult(g, "EUR"), 2), "EUR pp")

    r = t.lambda_handler(hop({"city": "London", "airport": "LHR"}, {"city": "Rome", "airport": "FCO"}), None)
    by = {o["mode"]: o for o in r["options"]}
    assert SERP == ["LHR-FCO"] and set(by) == {"flight", "ground"}, (SERP, r["options"])
    assert "estimateBasis" not in by["ground"] and by["ground"]["summary"].startswith("Train / bus / ferry")
    assert by["flight"]["recommended"] and by["flight"]["doorToDoorMinutes"] == 150 + 185
    print("LONDON-ROME OK", {m: o["doorToDoorMinutes"] for m, o in by.items()})
finally:
    os.environ["MOCK_MODE"] = "true"
    t.boto3.client = saved_client

print("RAIL HOPS OK")

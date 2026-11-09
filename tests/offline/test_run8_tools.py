"""search_flights, sequence_day and find_places changes after staging run 7, with fakes (no network, no keys): the
nonstop-only search runs beside the main search and never takes the return lookups' time; a theme park opens its
day; a Google coffee_roastery is tagged "roaster"."""
import json, os, threading, time
from fakes import load, STORE
os.environ["MOCK_MODE"] = "false"

# ---------------------------------------------------------------- 1. flights: the nonstop search runs in parallel
fl = load("tp-search-flights")
fl._secret_cache = {"SERPAPI_KEY": "test-key"}
assert fl._cache_key({}).startswith("flights-v7:"), "a cached run-7 result (no nonstop) is not reused"


class R:
    status_code = 200

    def __init__(s, d):
        s._d, s.text = d, json.dumps(d)

    def json(s):
        return s._d


def seg(frm, to, dep, arr, num):
    return {"departure_airport": {"id": frm, "time": dep}, "arrival_airport": {"id": to, "time": arr},
            "duration": 300, "airline": "X", "flight_number": num, "travel_class": "Premium economy"}


def opt(tok, price, *segs):
    return {"flights": list(segs), "total_duration": 600, "price": price, "departure_token": tok}


ONESTOP = opt("cx", 900, seg("SIN", "HKG", "2026-12-15 01:00", "2026-12-15 05:00", "CX 690"),
              seg("HKG", "HND", "2026-12-15 08:00", "2026-12-15 13:00", "CX 548"))
NONSTOP = opt("sq", 1600, seg("SIN", "HND", "2026-12-15 09:00", "2026-12-15 17:00", "SQ 638"))
CALLS, LOCK = [], threading.Lock()
SLEEP = {"main": 0.0, "nonstop": 0.0}


def fake_get(url, params, timeout):
    kind = "lookup" if params.get("departure_token") else "nonstop" if params.get("stops") == 1 else "main"
    t0 = time.monotonic()
    time.sleep(SLEEP.get(kind, 0))
    with LOCK:
        CALLS.append({"kind": kind, "start": t0, "end": time.monotonic(), "timeout": timeout, **params})
    if kind == "lookup":
        return R({"best_flights": [{"flights": [seg("HND", "SIN", "2026-12-20 11:00", "2026-12-20 17:30", "ZZ 1")],
                                    "total_duration": 450, "price": 2000}]})
    return R({"best_flights": [NONSTOP]} if kind == "nonstop" else {"best_flights": [ONESTOP]})


fl.requests.get = fake_get
RT = {"origin": "SIN", "destination": "HND", "departDate": "2026-12-15", "returnDate": "2026-12-20", "pax": 2,
      "cabin": "premium_economy"}

# a fast nonstop search adds no time: it starts before the main search ends and is back first
SLEEP.update(main=0.6, nonstop=0.3)
t0 = time.monotonic()
r = fl.lambda_handler(RT, None)
took = time.monotonic() - t0
main = next(c for c in CALLS if c["kind"] == "main")
ns = next(c for c in CALLS if c["kind"] == "nonstop")
assert ns["start"] < main["end"] and main["start"] < ns["end"], "the two searches overlap"
assert took < 0.6 + 0.3, f"{took:.2f}s: no longer than the main search plus the (fast) lookups"
assert ns["timeout"] == 20, "one attempt, min(30 s, REQUEST_TIMEOUT_S 20, the budget left)"
lookups = [c for c in CALLS if c["kind"] == "lookup"]
assert min(c["start"] for c in lookups) >= max(main["end"], ns["end"]), "lookups are chosen after both searches"
assert [c["departure_token"] for c in lookups] == ["sq", "cx"] and lookups[0]["stops"] == 1 and "stops" not in lookups[1]
assert any(o["maxStops"] == 0 for o in r["offers"]), "the nonstop offer is merged in"
assert sum(c["kind"] == "nonstop" for c in CALLS) == 1, "one attempt only"

# a slow nonstop search (slower than the main one) is waited for, so its nonstop still gets priced
CALLS.clear()
SLEEP.update(main=0.1, nonstop=0.5)
r = fl.lambda_handler({**RT, "departDate": "2026-12-16"}, None)
assert any(o["maxStops"] == 0 for o in r["offers"]), r["offers"]

# a hung nonstop search is given up once only NONSTOP_LOOKUP_RESERVE_S is left: the lookups keep that time
CALLS.clear()
os.environ["PROVIDER_TIME_BUDGET_S"] = "18"   # 18 - 15 = 3 s for the nonstop search after the main one
fl.NONSTOP_MIN_LEFT_S, gate = 0, fl.NONSTOP_MIN_LEFT_S
SLEEP.update(main=0.1, nonstop=5)
t0 = time.monotonic()
r = fl.lambda_handler({**RT, "departDate": "2026-12-17"}, None)
took = time.monotonic() - t0
lookups = [c for c in CALLS if c["kind"] == "lookup"]
assert r["ok"] and lookups and not any(o["maxStops"] == 0 for o in r["offers"]), r
assert 2.5 < took < 4.5, f"{took:.2f}s: stopped waiting at the reserve, not at the nonstop's own timeout"
assert lookups[0]["timeout"] >= 13, f"the first lookup still has the reserve: {lookups[0]['timeout']}"
del os.environ["PROVIDER_TIME_BUDGET_S"]
fl.NONSTOP_MIN_LEFT_S = gate

# economy: no nonstop search, no thread
CALLS.clear()
SLEEP.update(main=0, nonstop=0)
fl.lambda_handler({**RT, "cabin": "economy", "departDate": "2026-12-18"}, None)
assert not any(c["kind"] == "nonstop" for c in CALLS), CALLS
print("PARALLEL NONSTOP OK")

# ---------------------------------------------------------------- 2. sequence_day: a theme park opens its day
sd = load("tp-sequence-day")
HOTEL = {"id": "hotel", "lat": 35.6329, "lng": 139.8804}
ALL_DAY = {"open": "08:00", "close": "22:00"}


def stop(pid, name, cat, lat, lng, hours=ALL_DAY, **extra):
    return {"placeId": pid, "name": name, "category": cat, "lat": lat, "lng": lng, "openSimple": hours, **extra}


SEA = stop("sea", "Tokyo DisneySea", "attraction", 35.6267, 139.8851, {"open": "09:00", "close": "21:00"})
GAZEBO = stop("gazebo", "The Gazebo", "cafe", 35.6270, 139.8846)
GONDOLIER = stop("gondolier", "Gondolier Snacks", "cafe", 35.6262, 139.8857)


def day(*stops_, **kw):
    return sd.lambda_handler({"date": "2026-12-16", "start": HOTEL, "stops": list(stops_), "dayStart": "09:00",
                              "dayEnd": "21:00", "mode": "walk", **kw}, None)


r = day(GAZEBO, GONDOLIER, SEA)
names = [s["name"] for s in r["stops"]]
assert names[0] == "Tokyo DisneySea", names
assert r["stops"][0]["visitMinutes"] == 480 and r["stops"][0]["arriveAt"] <= "09:30", r["stops"][0]
assert set(names[1:]) == {"The Gazebo", "Gondolier Snacks"} and not r["unscheduled"], r
# a theme_park category park opens the day too, with its own visit time
FUJI = stop("q", "Fuji-Q Highland", "theme_park", 35.4875, 138.7801)
r = sd.lambda_handler({"date": "2026-12-16", "start": {"id": "inn", "lat": 35.4980, "lng": 138.7680},
                       "stops": [stop("c", "Cafe", "cafe", 35.4880, 138.7800), FUJI], "dayStart": "09:00",
                       "dayEnd": "21:00"}, None)
assert [s["name"] for s in r["stops"]] == ["Fuji-Q Highland", "Cafe"] and r["stops"][0]["visitMinutes"] == 480, r
# stops that no longer fit after the park are dropped as before (the day ends at 18:45: room for one cafe)
r = day(GAZEBO, GONDOLIER, SEA, dayEnd="18:45")
assert [s["name"] for s in r["stops"]][0] == "Tokyo DisneySea" and len(r["stops"]) == 2 and len(r["unscheduled"]) == 1, r
# pinned first still wins: a pinned breakfast cafe that closes at 10:00 keeps its morning slot before the park
BREAKFAST = stop("bk", "Breakfast Cafe", "cafe", 35.6300, 139.8820, {"open": "08:00", "close": "10:00"})
r = day(BREAKFAST, SEA, pinned=["bk"])
assert [s["name"] for s in r["stops"]] == ["Breakfast Cafe", "Tokyo DisneySea"], r
# a day with no park is ordered as before
r = day(GAZEBO, GONDOLIER)
assert [s["name"] for s in r["stops"]] == ["The Gazebo", "Gondolier Snacks"], r
print("PARK OPENS THE DAY OK")

# ---------------------------------------------------------------- 3. find_places: a Google coffee_roastery is a "roaster"
fp = load("tp-find-places")
fp._secret_cache = {"GOOGLE_MAPS_KEY": "test-key"}


def gp(pid, kind, name):
    return {"id": pid, "displayName": {"text": name}, "location": {"latitude": 35.66, "longitude": 139.70},
            "rating": 4.6, "userRatingCount": 900, "businessStatus": "OPERATIONAL", "primaryType": kind}


CAFES = [gp("glitch", "coffee_roastery", "Glitch Coffee"), gp("sbux", "coffee_shop", "Starbucks Coffee")]
fp.requests.post = lambda url, headers=None, json=None, timeout=None: type(
    "R", (), {"status_code": 200, "json": lambda s: {"places": CAFES}})()
zone, _ = fp._google_zone(fp._validate({"lat": 35.66, "lng": 139.70, "categories": ["cafe"],
                                        "foodRadiusKm": 1.2})[0], time.time() + 30)
text, _ = fp._google(fp._validate({"lat": 35.66, "lng": 139.70, "categories": ["cafe"], "foodRadiusKm": 4})[0],
                     time.time() + 30)
for found in (zone, text):
    tags = {p["name"]: p["tags"] for p in found}
    assert "roaster" in tags["Glitch Coffee"] and "roaster" not in tags["Starbucks Coffee"], tags
print("ROASTER TAG OK")

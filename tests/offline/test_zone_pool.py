"""The zone map in a trimmed planner pool, far theme parks as day trips under the traveler's day-trip limit, and the
zone day check on park days. Fakes only, no network."""
import json, math, sys, types
import fakes                                    # fake boto3 and fpdf, MOCK_MODE
from fakes import REPO
bac = types.ModuleType("bedrock_agentcore")
class App:
    def entrypoint(self, f): return f
bac.BedrockAgentCoreApp = App
sys.modules["bedrock_agentcore"] = bac
sys.path.insert(0, str(REPO / "agent"))
import tp_travel_agent as A


def offset(lat, lng, dn_km, de_km):
    return lat + dn_km / 111.0, lng + de_km / (111.0 * math.cos(math.radians(lat)))


def city(leg, c_lat, c_lng, n_zones, multi):
    """n_zones zones 3 km apart, each with 2 sights and 12 restaurants, already built (every place has its zone)."""
    zones, places = [], []
    for k in range(n_zones):
        zid = f"Z{leg * 100 + k + 1}"
        lat, lng = offset(c_lat, c_lng, 3 * (k // 4), 3 * (k % 4))
        sights = [f"s{leg}_{k}_{j}" for j in range(2)]
        zones.append({"id": zid, "city": f"C{leg}", "leg": leg, "kind": "walk", "lat": lat, "lng": lng, "label": f"near {zid}",
                      "radiusKm": 1.2, "sights": sights, "score": 9.0 - k * 0.1})
        where = {"city": f"C{leg}", "leg": leg} if multi else {}
        for j, pid in enumerate(sights):
            places.append({"placeId": pid, "name": f"Sight {pid}", "category": "museum", "rating": 4.6, "zone": zid,
                           "lat": lat + 0.001 * j, "lng": lng, **where})
        for j in range(12):
            places.append({"placeId": f"f{leg}_{k}_{j}", "name": f"Diner {leg} {k} {j} with a long enough name",
                           "category": "restaurant", "rating": 4.2, "reviews": 300, "zone": zid,
                           "lat": lat - 0.001 * j, "lng": lng, **where})
    return zones, places


def check(view, shown):
    """Every listed zone has a shown place; its sight and food counts are those of the shown places."""
    assert view, "no zones listed"
    for v in view:
        mine = [p for p in shown if p.get("zone") == v["id"]]
        assert mine, f"zone {v['id']} is listed with no shown place"
        assert v["food"] == sum(p["category"] == "restaurant" for p in mine), (v, len(mine))
        assert v["sights"] == sum(p["category"] == "museum" for p in mine), (v, len(mine))
        assert all(o in {x["id"] for x in view} for o in v.get("rideMin") or {}), v


# ---- single city: a pool over the size limit loses whole trailing zones, and the zone map follows
saved = A.MAX_POOL_CHARS
zones, places = city(0, 35.68, 139.76, 13, False)
spec = {"destination": "Tokyo", "startDate": "2026-12-01", "endDate": "2026-12-08", "pax": 2, "budgetSGD": 8000,
        "pace": "moderate", "preferences": {}}
pool = {"flights": [], "stays": [], "places": places, "zones": zones}
full = json.loads(A.compact_pool(json.loads(json.dumps(pool)), spec))
check(full["zones"], full["places"])
assert len(full["zones"]) == 13
A.MAX_POOL_CHARS = len(json.dumps(full, separators=(",", ":"))) * 2 // 3
cut = json.loads(A.compact_pool(json.loads(json.dumps(pool)), spec))
assert len(cut["places"]) < len(full["places"]) and len(cut["zones"]) < 13, (len(cut["places"]), len(cut["zones"]))
check(cut["zones"], cut["places"])
assert any(v["food"] < 12 for v in cut["zones"]) or len({p["zone"] for p in cut["places"]}) < 13
A.MAX_POOL_CHARS = saved

# ---- multi-city: the same per city after each trim
legs = [{"city": "C0", "airport": "HND", "nights": 4, "checkIn": "2026-12-01", "checkOut": "2026-12-05"},
        {"city": "C1", "airport": "KIX", "nights": 3, "checkIn": "2026-12-05", "checkOut": "2026-12-08"}]
z0, p0 = city(0, 35.68, 139.76, 10, True)
z1, p1 = city(1, 35.01, 135.77, 8, True)
spec_m = {"legs": legs, "startDate": "2026-12-01", "endDate": "2026-12-08", "pax": 2, "budgetSGD": 8000,
          "pace": "moderate", "preferences": {}}
pool_m = {"flights": [], "stays": [], "transfers": [], "places": p0 + p1, "zones": z0 + z1}
full = json.loads(A.compact_pool_multi(spec_m, json.loads(json.dumps(pool_m))))
for c in full["cities"]:
    check(c["zones"], c["places"])
A.MAX_POOL_CHARS = len(json.dumps(full, separators=(",", ":"))) * 2 // 3
cut = json.loads(A.compact_pool_multi(spec_m, json.loads(json.dumps(pool_m))))
assert sum(len(c["places"]) for c in cut["cities"]) < sum(len(c["places"]) for c in full["cities"])
for c in cut["cities"]:
    check(c["zones"], c["places"])
assert sum(len(c["zones"]) for c in cut["cities"]) < 18
A.MAX_POOL_CHARS = saved
print("ZONE MAP TRIM OK")

# ---- a far pinned theme park is a day trip that the day-trip limit never cuts (chat changes rerun normalize_special)
for limit, kept in (("none", []), ("1", ["Nara"])):
    sights = [{"placeId": f"k{i}", "name": f"Temple {i}", "category": "temple", "rating": 4.6,
               "lat": 35.01 + 0.002 * i, "lng": 135.77} for i in range(6)]
    lat, lng = offset(35.01, 135.77, -30, -35)   # about 46 km away
    park = {"placeId": "usj", "name": "Universal Studios Japan", "category": "requested", "requested": "Universal Studios Japan",
            "lat": lat, "lng": lng}
    spec_p = A.normalize_special({"destination": "Kyoto", "startDate": "2026-12-01", "endDate": "2026-12-06",
                                  "pace": "moderate", "dayTrips": ["Nara", "Kobe"],
                                  "preferences": {"days": {"dayTripLimit": limit}}})
    assert [t["place"] for t in spec_p["dayTrips"]] == kept, spec_p["dayTrips"]
    pool_p = {"places": sights + [park], "stays": [], "flights": []}
    A.build_zones(spec_p, pool_p)
    assert park.get("dayTrip") == "Universal Studios Japan" and spec_p["dayTrips"][-1]["park"] is True, spec_p["dayTrips"]
    A.normalize_special(spec_p)
    assert [t["place"] for t in spec_p["dayTrips"]] == kept + ["Universal Studios Japan"], (limit, spec_p["dayTrips"])
    assert spec_p["dayTrips"][-1]["park"] is True and spec_p["dayTrips"][-1]["lat"] == lat
# a listed trip without the park flag still counts toward the limit
assert [t["place"] for t in A.normalize_special({"dayTrips": [{"place": "Nara", "park": "yes"}, "Kobe"],
        "preferences": {"days": {"dayTripLimit": "1"}}})["dayTrips"]] == ["Nara"]
assert len(A.normalize_special({"dayTrips": ["Nara", "Kobe", "Uji", "Otsu", {"place": "USJ", "park": True}]})["dayTrips"]) == 4
print("PARK DAY TRIP LIMIT OK")

# ---- the zone day check knows park days by park zone or theme_park category, not only by name
mk = lambda pid, cat, zone: {"placeId": pid, "name": pid.title(), "category": cat, "lat": 35.0, "lng": 139.0, "zone": zone,
                             "rating": 4.5}
z_pool = {"zones": [{"id": "Z1", "kind": "walk"}, {"id": "Z9", "kind": "park"}],
          "places": [mk("legoland", "requested", "Z9"), mk("everland", "theme_park", "Z1"), mk("museum", "museum", "Z1")]}
z_spec = {"startDate": "2026-12-01", "endDate": "2026-12-09", "preferences": {"food": {"perDay": 1}}}
z_pool["places"].append(mk("park_cafe", "cafe", "Z9"))
for pid in ("legoland", "everland"):   # a park day needs no meals or zone shape, only the requested cafes
    diag = A._deterministic_quality_eval(z_spec, z_pool, {"days": [{"date": "2026-12-03", "stops": [{"placeId": pid}]}]})
    assert [i["message"] for i in diag["issues"] if i["status"] == "zones"] == [
        "2026-12-03 needs 1 cafe stops near that day's park or town."], (pid, diag["issues"])
    diag = A._deterministic_quality_eval(z_spec, z_pool, {"days": [{"date": "2026-12-03", "stops": [
        {"placeId": pid}, {"placeId": "park_cafe"}]}]})
    assert not [i for i in diag["issues"] if i["status"] == "zones"], (pid, diag["issues"])
diag = A._deterministic_quality_eval(z_spec, z_pool, {"days": [{"date": "2026-12-03", "stops": [{"placeId": "museum"}]}]})
assert len([i for i in diag["issues"] if i["status"] == "zones"]) == 2, diag["issues"]   # a city day still needs meals and cafes
print("PARK DAY CHECK OK")

# ---- day trips are looked up near the trip's city, and the trip point is where its places are (not a summit)
SENT_DT = []
class DayTripGw:
    def call(self, tool, args):
        SENT_DT.append(args)
        return {"ok": True, "query": {"lat": 35.36, "lng": 138.73}, "places": [   # summit centre, places in the towns
            {"placeId": "f1", "name": "Chureito Pagoda", "category": "viewpoint", "lat": 35.50, "lng": 138.80},
            {"placeId": "f2", "name": "Kawaguchi Lake", "category": "nature", "lat": 35.51, "lng": 138.76},
            {"placeId": "f3", "name": "Oishi Park", "category": "park", "lat": 35.52, "lng": 138.75}]}
tokyo = [{"placeId": f"t{i}", "name": f"Sight {i}", "category": "temple", "lat": 35.66 + 0.01 * i, "lng": 139.70 + 0.01 * i}
         for i in range(5)]
spec_d = {"destination": "Tokyo", "startDate": "2026-12-15", "endDate": "2026-12-20", "pax": 2, "interests": [],
          "dayTrips": [{"place": "Mount Fuji"}], "mustInclude": []}
pool_d = {"places": [dict(p) for p in tokyo], "stays": [], "flights": []}
saved_clients, A._clients = A._clients, lambda: (None, None, DayTripGw())
list(A.gather_specials(spec_d, pool_d, [], set(), only={"dayTrips"}))
A._clients = saved_clients
args = SENT_DT[0]
assert args["near"] == {"lat": 35.68, "lng": 139.72} and args["maxKm"] == 200 and args["radiusKm"] == 18, args
assert (spec_d["dayTrips"][0]["lat"], spec_d["dayTrips"][0]["lng"]) == (35.51, 138.76), spec_d["dayTrips"][0]
# multi-city: the centre is the trip's own city (matched like leg_of), and an unknown city sends no centre
hiro = [{"placeId": f"h{i}", "name": f"H {i}", "category": "temple", "lat": 34.39, "lng": 132.45, "leg": 1, "city": "Hiroshima"}
        for i in range(3)]
for from_city, want in (("hiroshima", {"lat": 34.39, "lng": 132.45}), ("", None)):
    SENT_DT.clear()
    spec_m = {"legs": [{"city": "Tokyo", "nights": 3}, {"city": "Hiroshima", "nights": 2}], "startDate": "2026-12-15",
              "endDate": "2026-12-20", "pax": 2, "interests": [], "mustInclude": [],
              "dayTrips": [{"place": "Miyajima", "fromCity": from_city}]}
    pool_m = {"places": [dict(p, leg=0, city="Tokyo") for p in tokyo] * 3 + hiro, "stays": [], "flights": []}
    A._clients = lambda: (None, None, DayTripGw())
    list(A.gather_specials(spec_m, pool_m, [], set(), only={"dayTrips"}))
    A._clients = saved_clients
    assert SENT_DT[0].get("near") == want, (from_city, SENT_DT[0])

# ---- each flight shows its usable hours on the trip's edge dates, and "best" weighs time at the destination
jal = {"offerId": "jal", "priceTotalSGD": 7994, "matches": ["nonstop"], "slices": [
    {"from": "SIN", "to": "HND", "departAt": "2026-12-15T22:25:00", "arriveAt": "2026-12-16T05:55:00", "stops": 0, "durationMinutes": 450},
    {"from": "HND", "to": "SIN", "departAt": "2026-12-20T10:50:00", "arriveAt": "2026-12-20T17:25:00", "stops": 0, "durationMinutes": 455}]}
aax = {"offerId": "aax", "priceTotalSGD": 3440, "slices": [
    {"from": "SIN", "to": "HND", "departAt": "2026-12-15T12:10:00", "arriveAt": "2026-12-15T22:40:00", "stops": 1, "durationMinutes": 570},
    {"from": "HND", "to": "SIN", "departAt": "2026-12-20T23:55:00", "arriveAt": "2026-12-21T09:20:00", "stops": 1, "durationMinutes": 625}]}
spec_f = {"destination": "Tokyo", "startDate": "2026-12-15", "endDate": "2026-12-20", "pax": 2,
          "preferences": {"flight": {"sortBy": "best"}}}
shown = {f["id"]: f["usableHours"] for f in json.loads(A.compact_pool({"flights": [jal, aax], "stays": [], "places": []},
                                                                      spec_f))["flights"]}
assert shown == {"jal": {"2026-12-15": "none", "2026-12-16": "09:00-21:00", "2026-12-20": "none"},
                 "aax": {"2026-12-15": "none", "2026-12-20": "09:00-20:55"}}, shown
assert [f["offerId"] for f in A._order_flights([jal, aax], spec_f)] == ["aax", "jal"]
same_price = [{**jal, "priceTotalSGD": 5000}, {**aax, "priceTotalSGD": 5100, "slices": [
    {**aax["slices"][0], "stops": 0, "durationMinutes": 450}, {**aax["slices"][1], "stops": 0, "durationMinutes": 455}]}]
assert [f["offerId"] for f in A._order_flights(same_price, spec_f)] == ["aax", "jal"], "a day more on the ground"
no_times = {"offerId": "nt", "priceTotalSGD": 4000, "slices": [{"stops": 0}, {"stops": 0}]}
assert [f["offerId"] for f in A._order_flights([no_times, same_price[0]], spec_f)] == ["nt", "jal"],     "unknown times are neutral, not a whole stay lost"
assert A._usable_hours(no_times, spec_f) == {}, "no invented full days when times are unknown"
assert [f["offerId"] for f in A._order_flights([jal, aax], {**spec_f, "preferences": {"flight": {}}})] == ["jal", "aax"], \
    "outside \"best\", a nonstop match still ranks first, as before"
print("DAY TRIPS AND FLIGHT TIME OK")

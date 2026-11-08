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
print("PARK DAY TRIP LIMIT OK")

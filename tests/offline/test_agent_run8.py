"""Agent changes after staging run 7, with fakes (no network): a city with two stays names the stay dates in its
hotel upgrade chip; with specialty coffee or pour-over wished, roasters and specialty cafes rank above the coffee
chains in what the planner sees, and the city-wide "specialty coffee" search tags its cafes."""
import copy, json, sys, types
import fakes                                    # fake boto3 and fpdf, MOCK_MODE
from fakes import REPO
bac = types.ModuleType("bedrock_agentcore")
class App:
    def entrypoint(self, f): return f
bac.BedrockAgentCoreApp = App
sys.modules["bedrock_agentcore"] = bac
sys.path.insert(0, str(REPO / "agent"))
import tp_travel_agent as A

# ---------------------------------------------------------------- 1. hotel upgrade chips name the stay dates
base = {"startDate": "2026-12-15", "endDate": "2026-12-23", "pax": 2, "budget": {"amount": "20000", "currency": "SGD"},
        "destination": "Tokyo", "preferences": {}}
legs = [{"city": "Tokyo", "airport": "HND", "nights": 4, "checkIn": "2026-12-15", "checkOut": "2026-12-19"},
        {"city": "Kyoto", "airport": "KIX", "nights": 2, "checkIn": "2026-12-19", "checkOut": "2026-12-21"},
        {"city": "Tokyo", "airport": "HND", "nights": 2, "checkIn": "2026-12-21", "checkOut": "2026-12-23"}]
spec = {**base, "legs": legs}


def hotel(sid, leg, name, review, price):
    return {"stayId": sid, "leg": leg, "city": legs[leg]["city"], "name": name, "starRating": 5, "reviewScore": review,
            "priceTotalSGD": price}


pool = {"flights": [], "stays": [hotel("hil0", 0, "Hilton Tokyo", 8.6, 2000), hotel("imp0", 0, "Imperial Hotel, Tokyo", 9.6, 3719.80),
                                 hotel("k", 1, "Kyoto Inn", 8.0, 800), hotel("kb", 1, "Kyoto Palace", 9.1, 1200),
                                 hotel("hil2", 2, "Hilton Tokyo", 8.6, 1000), hotel("imp2", 2, "Imperial Hotel, Tokyo", 9.6, 1657.13)]}
seq = {"stays": [{"stayId": s, "checkIn": l["checkIn"], "checkOut": l["checkOut"], "city": l["city"]}
                 for s, l in zip(("hil0", "k", "hil2"), legs)], "days": []}
chips = A._upgrade_choices(spec, pool, seq, {"totals": {"total": "8000"}})
assert chips == ["Stay at Imperial Hotel, Tokyo for 15-19 Dec (rated 9.6), +SGD 1,719.80",
                 "Stay at Kyoto Palace in Kyoto (rated 9.1), +SGD 400.00",
                 "Stay at Imperial Hotel, Tokyo for 21-23 Dec (rated 9.6), +SGD 657.13"], chips
# a stay across two months names both
legs_x = [dict(legs[0], checkIn="2026-12-29", checkOut="2027-01-02"), legs[1], legs[2]]
seq_x = {**seq, "stays": [dict(seq["stays"][0], checkIn="2026-12-29", checkOut="2027-01-02")] + seq["stays"][1:]}
assert A._upgrade_choices({**spec, "legs": legs_x}, pool, seq_x, {"totals": {"total": "8000"}})[0] == \
    "Stay at Imperial Hotel, Tokyo for 29 Dec-2 Jan (rated 9.6), +SGD 1,719.80"
# one stay per city keeps " in <city>"; a single-city trip has no place words
two = {**spec, "legs": legs[:2]}
assert A._upgrade_choices(two, pool, {**seq, "stays": seq["stays"][:2]}, {"totals": {"total": "8000"}}) == [
    "Stay at Imperial Hotel, Tokyo in Tokyo (rated 9.6), +SGD 1,719.80", "Stay at Kyoto Palace in Kyoto (rated 9.1), +SGD 400.00"]
single = {**base, "legs": []}
assert A._upgrade_choices(single, {"flights": [], "stays": [dict(s, leg=None) for s in pool["stays"][:2]]},
                          {"stayId": "hil0", "days": []}, {"totals": {"total": "8000"}}) == [
    "Stay at Imperial Hotel, Tokyo (rated 9.6), +SGD 1,719.80"]
print("STAY DATE CHIPS OK")

# ---------------------------------------------------------------- 2. specialty coffee above the chains
coffee_spec = {"startDate": "2026-12-15", "endDate": "2026-12-20", "pax": 2, "destination": "Tokyo",
               "budget": {"amount": "9000", "currency": "SGD"},
               "preferences": {"food": {"coffee": True, "coffeeStyle": "pour_over", "perDay": 2}}}
A.normalize_prefs(coffee_spec)


def cafe(pid, name, rating=4.4, tags=(), zone="Z1"):
    return {"placeId": pid, "name": name, "category": "cafe", "rating": rating, "reviews": 900, "zone": zone,
            "lat": 35.66, "lng": 139.70, "tags": list(tags)}


cafes = [cafe("sb", "Starbucks Coffee Shibuya", 4.6), cafe("dt", "Doutor Coffee Shop", 4.5),
         cafe("tl", "Tully’s Coffee", 4.5), cafe("sm", "St. Marc Café", 4.5),
         cafe("gl", "Glitch Coffee", 4.3, tags=["roaster"]), cafe("on", "Onibus Coffee", 4.2, tags=["specialty coffee"]),
         cafe("ro", "Single O Roastery", 4.1), cafe("pl", "Plain Cafe", 4.4)]
order = [p["placeId"] for p in A._order_places(copy.deepcopy(cafes), None, spec=coffee_spec)]
assert order[:3] == ["gl", "on", "ro"] and set(order[-4:]) == {"sb", "dt", "tl", "sm"}, order
assert order[3] == "pl", "a plain cafe sits between the specialty cafes and the chains"
# without the wish the ranking is unchanged (the chains are not pushed down)
plain = {**coffee_spec, "preferences": {**coffee_spec["preferences"], "food": {"tea": True}}}
assert [p["placeId"] for p in A._order_places(copy.deepcopy(cafes), None, spec=plain)][0] == "sb"
# the planner's zone list (compact_pool) shows the same order
z_pool = {"flights": [], "stays": [], "zones": [{"id": "Z1", "city": "Tokyo", "leg": 0, "kind": "walk", "lat": 35.66,
                                                 "lng": 139.70, "label": "near Shibuya", "radiusKm": 1.2, "sights": ["s"],
                                                 "score": 9.0}],
          "places": [{"placeId": "s", "name": "Meiji Jingu", "category": "shrine", "rating": 4.6, "zone": "Z1",
                      "lat": 35.676, "lng": 139.699}] + copy.deepcopy(cafes)}
shown = [p["id"] for p in json.loads(A.compact_pool(z_pool, coffee_spec))["places"] if p["category"] == "cafe"]
assert shown[:3] == ["gl", "on", "ro"] and set(shown[-4:]) == {"sb", "dt", "tl", "sm"}, shown


# the city-wide "specialty coffee" search tags its cafes, also the copy a zone search found first
class Gw:
    def call(self, tool, args):
        if args.get("foodRadiusKm", 0) > 2:   # the city-wide coffee search
            return {"ok": True, "source": "google", "places": [cafe("on", "Onibus Coffee", 4.2, zone=None),
                                                               cafe("fu", "Fuglen", 4.3, zone=None)]}
        return {"ok": True, "source": "google-zone", "places": [cafe("on", "Onibus Coffee", 4.2, zone=None),
                                                                cafe("sb", "Starbucks Coffee Shibuya", 4.6, zone=None)]}


A._clients = lambda: (None, None, Gw())
f_pool = {"flights": [], "stays": [], "zones": copy.deepcopy(z_pool["zones"]), "places": copy.deepcopy(z_pool["places"][:1])}
gen = A.zone_food(coffee_spec, f_pool, [], set())
try:
    while True:
        next(gen)
except StopIteration:
    pass
tags = {p["placeId"]: p.get("tags") or [] for p in f_pool["places"]}
assert "specialty coffee" in tags["on"] and "specialty coffee" in tags["fu"] and "specialty coffee" not in tags["sb"], tags
print("SPECIALTY COFFEE OK")

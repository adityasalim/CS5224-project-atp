"""Planner-guided walkable zones, offline: zone building (200 seeded synthetic cities plus the owner's Tokyo-Kyoto
fixture), the Google food budget per plan, meal balancing, the cafe count, the sequence_day zone lock, the verifier
ride warning and the zone mode of find_places. Fakes only: no AWS, Google or model calls."""
import ast, copy, json, math, os, random, re, sys
from pathlib import Path
from fakes import load, STORE

REPO = Path(__file__).resolve().parents[2]
SOURCE = (REPO / "agent/tp_travel_agent.py").read_text(encoding="utf-8")
tree = ast.parse(SOURCE)
top, imports = {}, {}
for node in tree.body:
    if isinstance(node, ast.FunctionDef):
        top[node.name] = node
    elif isinstance(node, ast.Assign):
        for t in node.targets:
            for n in ([t] if isinstance(t, ast.Name) else t.elts if isinstance(t, ast.Tuple) else []):
                top[n.id] = node
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            imports[(alias.asname or alias.name).split(".")[0]] = node


def agent(roots, stubs=None):
    """The named agent functions plus everything they need from the module. A name from a non-stdlib import must be
    stubbed, else AssertionError (never a skip, so a skip can never hide a failure)."""
    stubs = stubs or {}
    seen, stack = set(), list(roots)
    while stack:
        name = stack.pop()
        if name in seen or name in stubs or name not in top:
            continue
        seen.add(name)
        stack += [n.id for n in ast.walk(top[name]) if isinstance(n, ast.Name)]
    used = {n.id for name in seen for n in ast.walk(top[name]) if isinstance(n, ast.Name)}
    body = []
    for name in sorted((used & set(imports)) - set(stubs)):
        mod = imports[name]
        root = (mod.module if isinstance(mod, ast.ImportFrom) else mod.names[0].name).split(".")[0]
        assert root in sys.stdlib_module_names, f"{name} comes from {root}; stub it"
        body.append(mod)
    nodes = []
    for n in sorted({id(top[n]): top[n] for n in seen}.values(), key=lambda n: n.lineno):
        nodes.append(n)
    ns = dict(stubs)
    exec(compile(ast.Module(body=body + nodes, type_ignores=[]), "<agent>", "exec"), ns)
    return ns


A = agent(["build_zones", "_zone_view", "_zone_places", "_balance_meals", "_cafes_per_day", "_day_trip_for",
           "_deterministic_quality_eval", "STOPS_PER_DAY", "normalize_prefs", "_ride_max"])
build_zones, zone_view, zone_places = A["build_zones"], A["_zone_view"], A["_zone_places"]
km = A["_km"]


def offset(lat, lng, dn_km, de_km):
    return lat + dn_km / 111.0, lng + de_km / (111.0 * math.cos(math.radians(lat)))


# ---------------------------------------------------------------- 200 seeded synthetic cities
def synthetic(seed):
    rnd = random.Random(seed)
    c_lat, c_lng = 35.0, 139.0
    blobs = []
    while len(blobs) < rnd.randint(3, 8):
        b = offset(c_lat, c_lng, rnd.uniform(-9, 9), rnd.uniform(-9, 9))
        if all(2 <= km({"lat": b[0], "lng": b[1]}, {"lat": o[0], "lng": o[1]}) <= 12 for o in blobs) or not blobs:
            blobs.append(b)
    places = []
    for i in range(rnd.randint(10, 120)):
        b = rnd.choice(blobs)
        lat, lng = offset(b[0], b[1], rnd.gauss(0, 0.4), rnd.gauss(0, 0.4))
        places.append({"placeId": f"s{seed}_{i}", "name": f"Sight {i}", "lat": lat, "lng": lng, "rating": round(rnd.uniform(3.5, 5), 1),
                       "category": rnd.choice(["museum", "temple", "park", "attraction", "gallery", "shrine", "market"])})
    for p in rnd.sample(places, rnd.randint(0, 2)):
        p["requested"] = p["name"]
    far = rnd.choice([17, 46])
    lat, lng = offset(c_lat, c_lng, far * 0.6, far * 0.8)
    places.append({"placeId": f"park{seed}", "name": "Sample Disney Resort", "category": "requested", "lat": lat, "lng": lng,
                   "requested": "Sample Disney Resort"})
    for i in range(20):
        b = rnd.choice(blobs)
        lat, lng = offset(b[0], b[1], rnd.gauss(0, 0.8), rnd.gauss(0, 0.8))
        places.append({"placeId": f"f{seed}_{i}", "name": f"Diner {i}", "category": "restaurant", "lat": lat, "lng": lng, "rating": 4.3})
    nights = rnd.randint(2, 10)
    spec = {"destination": "City", "startDate": "2026-12-01", "endDate": f"2026-12-{1 + nights:02d}", "legs": [],
            "pace": "moderate", "preferences": {}, "dayTrips": []}
    return spec, {"places": places, "stays": [], "flights": []}, nights, far


for seed in range(200):
    spec, pool, nights, far = synthetic(seed)
    spec2, pool2 = copy.deepcopy(spec), copy.deepcopy(pool)
    random.Random(seed + 1000).shuffle(pool2["places"])
    build_zones(spec, pool)
    build_zones(spec2, pool2)
    tags = lambda pl: {p["placeId"]: p.get("zone") for p in pl["places"]}
    assert pool["zones"] == pool2["zones"] and tags(pool) == tags(pool2), f"seed {seed}: input order changed the zones"
    byid = {p["placeId"]: p for p in pool["places"]}
    zones = pool["zones"]
    for z in zones:
        if z["kind"] in ("walk", "solo"):
            for p in pool["places"]:
                if p.get("zone") == z["id"]:
                    assert km(z, p) <= z["radiusKm"] + 1e-9, f"seed {seed}: {p['placeId']} is outside {z['id']}"
    member_of = {}
    for z in zones:
        for pid in z["sights"]:
            assert pid not in member_of, f"seed {seed}: {pid} is in two zones"
            member_of[pid] = z["id"]
    park, kept = byid[f"park{seed}"], {z["id"] for z in zones}
    for p in pool["places"]:
        if p.get("requested"):
            assert p.get("zone") in kept, f"seed {seed}: pinned {p['placeId']} has no zone"
    if far < 25:
        pz = next(z for z in zones if z["id"] == park["zone"])
        assert pz["kind"] == "park" and [p["placeId"] for p in pool["places"] if p.get("zone") == pz["id"]] == [park["placeId"]]
    else:
        assert park.get("dayTrip") and any(t["place"] == park["dayTrip"] for t in spec["dayTrips"]), f"seed {seed}"
    walk = [z for z in zones if z["kind"] in ("walk", "solo")]
    away = sum(z["kind"] in ("park", "daytrip") for z in zones)
    k, n = 2 * max(1, nights - 1 - away) + 2, min(max(5 * nights, 10), 50)
    pinned_zones = sum(any(byid[s].get("requested") for s in z["sights"]) for z in walk)
    assert len(walk) <= k + pinned_zones, (seed, len(walk), k)
    shown = [p for p in pool["places"] if p.get("zone") and not A["_is_food_place"](p) and not p.get("dayTrip")
             and next(z for z in zones if z["id"] == p["zone"])["kind"] in ("walk", "solo")]
    assert len(shown) <= n + sum(bool(p.get("requested")) for p in pool["places"]), (seed, len(shown), n)
    for v in zone_view(zones, [], pool["places"]):
        if "rideMin" in v and len(v["rideMin"]) > 1:
            assert max(v["rideMin"].values()) <= 30, (seed, v)
    assert all(p.get("zone") in kept for p in zone_places(pool, spec)), seed
    before = json.dumps([pool, spec], sort_keys=True)
    assert build_zones(spec, pool) == [] and json.dumps([pool, spec], sort_keys=True) == before, f"seed {seed}: not idempotent"
    if walk:
        lat, lng = offset(35.0, 139.0, 30, -30)   # 5 km or more from every zone
        assert all(km({"lat": lat, "lng": lng}, z) > 5 for z in zones)
        pool["places"].append({"placeId": "late", "name": "Late Museum", "category": "requested", "requested": "Late Museum",
                               "lat": lat, "lng": lng})
        ids = [z["id"] for z in zones]
        top_id = max(int(i[1:]) for i in ids)
        assert build_zones(spec, pool) == [f"Z{top_id + 1}"] and [z["id"] for z in pool["zones"]][:len(ids)] == ids
print("zone properties hold for 200 seeded cities")

# ---------------------------------------------------------------- the owner's trip: Tokyo 7 nights, Kyoto 4 nights
TOKYO = [("Senso-ji", 35.7148, 139.7967, "temple"), ("Asakusa Shrine", 35.7153, 139.7975, "shrine"),
         ("Tokyo Skytree", 35.7101, 139.8107, "viewpoint"), ("Sumida Park", 35.7120, 139.8020, "park"),
         ("Ueno Park", 35.7156, 139.7745, "park"), ("Tokyo National Museum", 35.7188, 139.7765, "museum"),
         ("National Museum of Western Art", 35.7154, 139.7758, "museum"), ("Ueno Zoo", 35.7165, 139.7713, "zoo"),
         ("Ameyoko", 35.7101, 139.7745, "market"), ("Meiji Jingu", 35.6764, 139.6993, "shrine"),
         ("Yoyogi Park", 35.6717, 139.6949, "park"), ("Takeshita Street", 35.6716, 139.7031, "shopping"),
         ("Omotesando", 35.6652, 139.7123, "shopping"), ("Shibuya Crossing", 35.6595, 139.7005, "attraction"),
         ("Shibuya Sky", 35.6584, 139.7022, "viewpoint"), ("Hachiko Statue", 35.6590, 139.7006, "attraction"),
         ("Shinjuku Gyoen", 35.6852, 139.7100, "garden"), ("Tokyo Metropolitan Building", 35.6896, 139.6917, "viewpoint"),
         ("Omoide Yokocho", 35.6930, 139.6995, "market"), ("Golden Gai", 35.6938, 139.7046, "attraction"),
         ("Imperial Palace East Gardens", 35.6852, 139.7528, "garden"), ("Tokyo Station", 35.6812, 139.7671, "historic"),
         ("Ginza", 35.6717, 139.7650, "shopping"), ("Tsukiji Outer Market", 35.6655, 139.7707, "market"),
         ("Hamarikyu Gardens", 35.6600, 139.7633, "garden"), ("Tokyo Tower", 35.6586, 139.7454, "viewpoint"),
         ("Zojo-ji", 35.6575, 139.7483, "temple"), ("teamLab Planets", 35.6491, 139.7898, "museum"),
         ("Odaiba Seaside Park", 35.6300, 139.7750, "park"), ("Mori Art Museum", 35.6604, 139.7292, "museum"),
         ("Akihabara", 35.6984, 139.7731, "shopping"), ("Nezu Shrine", 35.7202, 139.7607, "shrine")]
KYOTO = [("Kiyomizu-dera", 34.9949, 135.7850, "temple"), ("Sannenzaka", 34.9960, 135.7810, "historic"),
         ("Yasaka Shrine", 35.0037, 135.7788, "shrine"), ("Gion Hanamikoji", 35.0018, 135.7754, "historic"),
         ("Kennin-ji", 35.0007, 135.7737, "temple"), ("Fushimi Inari", 34.9671, 135.7727, "shrine"),
         ("Tofuku-ji", 34.9767, 135.7740, "temple"), ("Kinkaku-ji", 35.0394, 135.7292, "temple"),
         ("Ryoan-ji", 35.0345, 135.7183, "temple"), ("Ninna-ji", 35.0310, 135.7139, "temple"),
         ("Arashiyama Bamboo Grove", 35.0094, 135.6668, "park"), ("Tenryu-ji", 35.0158, 135.6738, "temple"),
         ("Togetsukyo Bridge", 35.0128, 135.6778, "attraction"), ("Nijo Castle", 35.0142, 135.7481, "historic"),
         ("Nishiki Market", 35.0050, 135.7649, "market"), ("Kyoto Imperial Palace", 35.0254, 135.7621, "historic"),
         ("Ginkaku-ji", 35.0270, 135.7982, "temple"), ("Philosopher's Path", 35.0230, 135.7945, "attraction"),
         ("Nanzen-ji", 35.0110, 135.7944, "temple"), ("Heian Shrine", 35.0160, 135.7824, "shrine"),
         ("Kyoto Tower", 34.9875, 135.7593, "viewpoint"), ("Sanjusangendo", 34.9879, 135.7718, "temple")]
PINS = [("Tokyo Disneyland", 35.6329, 139.8804, 0), ("Tokyo DisneySea", 35.6267, 139.8851, 0),
        ("Universal Studios Japan", 34.6654, 135.4323, 1)]


def owner_trip():
    places = []
    for li, rows in ((0, TOKYO), (1, KYOTO)):
        city = ("Tokyo", "Kyoto")[li]
        places += [{"placeId": f"osm:{city}{i}", "name": n, "lat": la, "lng": ln, "category": c, "rating": 4.5,
                    "city": city, "leg": li} for i, (n, la, ln, c) in enumerate(rows)]
    places += [{"placeId": f"pin:{i}", "name": n, "lat": la, "lng": ln, "category": "requested", "requested": n,
                "city": ("Tokyo", "Kyoto")[li], "leg": li} for i, (n, la, ln, li) in enumerate(PINS)]
    places += [{"placeId": f"fuji:{i}", "name": n, "lat": la, "lng": ln, "category": "viewpoint", "dayTrip": "Kawaguchiko",
                "city": "Tokyo", "leg": 0} for i, (n, la, ln) in enumerate([("Oishi Park", 35.5181, 138.7520),
                                                                            ("Chureito Pagoda", 35.5011, 138.8013)])]
    spec = {"destination": "Tokyo → Kyoto", "startDate": "2026-12-01", "endDate": "2026-12-12", "pace": "packed",
            "legs": [{"city": "Tokyo", "nights": 7, "checkIn": "2026-12-01", "checkOut": "2026-12-08"},
                     {"city": "Kyoto", "nights": 4, "checkIn": "2026-12-08", "checkOut": "2026-12-12"}],
            "dayTrips": [{"place": "Kawaguchiko", "date": "", "fromCity": "Tokyo", "lat": 35.4975, "lng": 138.7631}],
            "pinned": [{"name": n, "placeId": f"pin:{i}"} for i, (n, *_r) in enumerate(PINS)],
            "cuisines": ["italian"], "cuisine": "italian",
            "preferences": {"food": {"coffee": True, "dessert": True, "perDay": 2, "coffeeStyle": "pour_over"}}}
    A["normalize_prefs"](spec)
    stays = [{"stayId": "kyoto-st", "name": "Kyoto Station Hotel", "lat": 34.9858, "lng": 135.7588, "city": "Kyoto", "leg": 1},
             {"stayId": "tokyo-st", "name": "Ginza Hotel", "lat": 35.6717, "lng": 139.7640, "city": "Tokyo", "leg": 0}]
    return spec, {"places": places, "stays": stays, "flights": [], "transfers": []}


spec, pool = owner_trip()
build_zones(spec, pool)
zones, byid = pool["zones"], {p["placeId"]: p for p in pool["places"]}
kinds = lambda li, *k: [z for z in zones if z["leg"] == li and z["kind"] in k]
assert [byid["pin:0"]["zone"], byid["pin:1"]["zone"]] == [z["id"] for z in kinds(0, "park")], kinds(0, "park")
assert byid["pin:2"]["dayTrip"] == "Universal Studios Japan" and kinds(1, "daytrip")[0]["dayTrip"] == "Universal Studios Japan"
trip = A["_day_trip_for"](spec, pool, {"dayTrip": "Universal Studios Japan"}, ["pin:2"], {"stayId": "kyoto-st"})
assert trip and 480 + 2 * trip["travelMinutes"] <= 12 * 60, trip   # a full park day fits as a day trip from Kyoto
assert len(kinds(0, "walk", "solo")) <= 8 and len(kinds(1, "walk", "solo")) <= 6, [len(kinds(i, "walk", "solo")) for i in (0, 1)]
assert len(zones) == 18, [(z["id"], z["kind"], z["label"]) for z in zones]
print(f"owner trip: {len(zones)} zones, USJ day trip {trip['travelMinutes']} min each way")

# Google budget: one Nearby Search per zone with food, one coffee Text Search per city, nothing in parks, cap 20
SENT = []


class FakeGateway:
    def call(self, tool, args):
        SENT.append((tool, args))
        z_lat, z_lng = args["lat"], args["lng"]
        return {"ok": True, "source": "google-zone", "places": [
            {"placeId": f"g:{z_lat:.4f}:{z_lng:.4f}:{c}", "name": f"{c} near {z_lat:.3f}", "category": c,
             "lat": z_lat + 0.001, "lng": z_lng, "rating": 4.5, "reviews": 300} for c in args["categories"]]}


Z = agent(["zone_food"], stubs={"_clients": lambda: (None, None, FakeGateway()), "GatewayError": RuntimeError})
z_spec, z_pool = copy.deepcopy(spec), copy.deepcopy(pool)
def drain(gen):
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            return stop.value
used = drain(Z["zone_food"](z_spec, z_pool, [], set()))
nearby = [a for _, a in SENT if a["foodRadiusKm"] <= 2]
city_coffee = [a for _, a in SENT if a["foodRadiusKm"] > 2]
assert len(city_coffee) == 2 and all(a["categories"] == ["cafe"] and not a["dietary"] for a in city_coffee), city_coffee
fed = {(round(a["lat"], 5), round(a["lng"], 5)) for a in nearby}
assert all(((z["lat"], z["lng"]) in fed) == (z["kind"] in ("walk", "solo") or z.get("dayTrip") == "Kawaguchiko")
           for z in zones), "every zone but the parks gets one food search"
assert len(SENT) == 17 == used, (len(SENT), used)   # the web picks get the 3 requests left of the cap
assert all(set(a["categories"]) == ({"restaurant", "cafe", "dessert"} if a["foodRadiusKm"] == 1.2 else {"restaurant", "cafe"})
           for a in nearby), "walk zones ask for every wished food kind, day trips skip desserts"
assert all(p.get("zone") is not None for p in z_pool["places"]), "the city-wide cafes join a zone or are hidden"
SENT.clear()
big_spec, big_pool = copy.deepcopy(spec), copy.deepcopy(pool)
big_pool["zones"] = [dict(zones[2], id=f"Z{100 + i}", kind="walk") for i in range(30)]
assert drain(Z["zone_food"](big_spec, big_pool, [], set())) == 20 and drain(Z["zone_food"](spec, {"places": []}, [], set())) == 0
assert len(SENT) == 20, f"hard cap of 20 Google requests per plan, sent {len(SENT)}"
callers = {f.name for f in top.values() if isinstance(f, ast.FunctionDef)
           for n in ast.walk(f) if isinstance(n, ast.Name) and n.id == "zone_food"}
assert callers == {"gather", "gather_multi"}, f"chat changes reuse the session pool (0 requests); zone_food in {callers}"
print("Google food requests for the owner trip: 17 (cap 20 holds with 30 zones)")

# the rules the planner sees agree with the verifier's stop cap
sys.path.insert(0, str(REPO / "agent"))
import prompts
for pace, cap in A["STOPS_PER_DAY"].items():
    assert re.search(rf"{cap} \({pace}\)", prompts.ZONE_RULES), (pace, cap)
assert "{" not in prompts.ZONE_RULES and "rideMin" in prompts.ZONE_RULES

# ---------------------------------------------------------------- meals and cafes
SENSO, UENO = (35.7148, 139.7967), (35.7156, 139.7745)
mk = lambda pid, cat, ll, zone, **k: {"placeId": pid, "name": pid, "category": cat, "lat": ll[0], "lng": ll[1], "zone": zone,
                                       "rating": 4.5, **k}
lunch_near = mk("lunch", "restaurant", offset(*SENSO, 0, 0.3), "Z1")
far_dinner = mk("dinner", "restaurant", offset(*UENO, 0, -1.8), "Z2")
cand = mk("cand", "restaurant", offset(*UENO, 0.4, 0), "Z2")
m_pool = {"zones": [{"id": "Z1"}, {"id": "Z2"}], "places": [mk("senso", "temple", SENSO, "Z1"), mk("ueno", "park", UENO, "Z2"),
                                                            lunch_near, far_dinner, cand]}
assert km(lunch_near, {"lat": (SENSO[0] + UENO[0]) / 2, "lng": (SENSO[1] + UENO[1]) / 2}) > 1.0
plan = {"days": [{"date": "2026-12-03", "placeIds": ["senso", "lunch", "ueno", "dinner"]}]}
A["_balance_meals"]({}, m_pool, plan)
assert plan["days"][0]["placeIds"] == ["senso", "lunch", "ueno", "cand"], plan
both = mk("both", "restaurant", offset(*SENSO, 0.2, 0), "Z1")
m_pool["places"].append(both)
plan = {"days": [{"date": "2026-12-04", "placeIds": ["senso", "lunch", "ueno", "both"]}]}
A["_balance_meals"]({}, m_pool, plan)
assert plan["days"][0]["placeIds"] == ["senso", "lunch", "ueno", "cand"], "the second meal moves to the other zone"
# a place outside every kept zone (zone "") is hidden from the planner, so no meal swap or commute hint names it
hidden = mk("hidden", "restaurant", offset(*UENO, 0.3, 0), "")
h_pool = {"zones": m_pool["zones"], "places": [mk("senso", "temple", SENSO, "Z1"), mk("ueno", "park", UENO, "Z2"),
                                               lunch_near, far_dinner, hidden]}
plan = {"days": [{"date": "2026-12-05", "placeIds": ["senso", "lunch", "ueno", "dinner"]}]}
A["_balance_meals"]({}, h_pool, plan)
assert plan["days"][0]["placeIds"] == ["senso", "lunch", "ueno", "dinner"], plan
h_pool["places"].append(cand)
E = agent(["_enrich_violations"], {n: lambda *a, **k: 0 for n in ("_acceptance_violation_priority", "currency_code",
                                                                  "known_price", "budget_amount", "trip_daily")})
hint = E["_enrich_violations"]({}, h_pool, {"days": [{"date": "2026-12-05", "placeIds": ["senso", "ueno"]}]},
                               {"violations": [{"check": "commute", "day": "2026-12-05", "message": "long ride"}]},
                               [])[0]["repairHint"]
assert "cand" in hint and "hidden" not in hint, hint
assert A["_cafes_per_day"]({"preferences": {"food": {"perDay": 2}}}) == 2
assert A["_cafes_per_day"]({"activities": ["two coffees daily"], "preferences": {"notes": "coffee lover"}}) == 0
diag = A["_deterministic_quality_eval"](
    {"startDate": "2026-12-01", "endDate": "2026-12-09", "preferences": {"food": {"perDay": 1}}},
    {"zones": [{"id": "Z1"}], "places": [mk("a", "museum", SENSO, "Z1"), mk("b", "museum", UENO, "Z2"),
                                          mk("c", "museum", UENO, "Z3"), mk("l", "restaurant", UENO, "Z2")]},
    {"days": [{"date": "2026-12-03", "stops": [{"placeId": x} for x in "abcl"]}]})
z_msgs = [i["message"] for i in diag["issues"] if i["status"] == "zones"]
assert len(z_msgs) == 3 and "Z1, Z2, Z3" in z_msgs[0], z_msgs

# ---------------------------------------------------------------- sequence_day: walk a zone, one ride, farther zone first
sd = load("tp-sequence-day")
from hours_util import hours_for
ASAKUSA, SHIBUYA, HOTEL = (35.712, 139.795), (35.660, 139.700), {"id": "hotel", "lat": 35.690, "lng": 139.760}
spots = []
for i, (base, cat) in enumerate([(ASAKUSA, "temple"), (SHIBUYA, "attraction"), (ASAKUSA, "museum"), (SHIBUYA, "restaurant"),
                                 (ASAKUSA, "restaurant"), (SHIBUYA, "viewpoint"), (ASAKUSA, "garden")]):
    lat, lng = offset(*base, 0.2 * (i % 3) - 0.2, 0.15 * (i % 2))
    spots.append({"placeId": f"{'A' if base == ASAKUSA else 'S'}{i}", "name": str(i), "category": cat, "lat": lat, "lng": lng,
                  "hours": hours_for({}), "visitMinutes": 60})
pts = [HOTEL] + spots
walk_m = [[sd._estimate(a, b, "walk") for b in pts] for a in pts]
car_m = [[sd._estimate(a, b, "car") for b in pts] for a in pts]
out, unscheduled, _, _ = sd.schedule(HOTEL, spots, walk_m, car_m, 9 * 60, 21 * 60, "walk", True)
order = [o["placeId"][0] for o in out]
assert not unscheduled and order == ["S"] * 3 + ["A"] * 4, order          # Shibuya is farther from the hotel
assert [o["travelBy"] for o in out[1:]].count("taxi") == 1, [o["travelBy"] for o in out]
meals = [o for o in out if o["category"] == "restaurant"]
assert [m["placeId"][0] for m in meals] == ["S", "A"], meals                # each group's meal is eaten in that group
# two zones 2 km apart are one walkable group (about 35 min on foot), so a late opening interleaves them; with zone ids
# the day finishes the farther zone, takes one ride, then does the other
Z_A, Z_B = (35.000, 135.760), offset(35.000, 135.760, 0, 2.0)
z_hotel = dict(zip(("lat", "lng"), offset(*Z_A, 0, -0.3)), id="hotel")
z_stops = [{"placeId": pid, "name": pid, "category": "museum", "lat": ll[0], "lng": ll[1], "visitMinutes": 60, "zone": zid,
            "hours": hours_for({"openSimple": {"open": op, "close": "18:00"}})}
           for pid, ll, zid, op in [("a1", offset(*Z_A, 0.1, 0), "ZA", "09:00"), ("a2", offset(*Z_A, -0.1, 0), "ZA", "13:00"),
                                    ("b1", offset(*Z_B, 0.1, 0), "ZB", "09:00"), ("b2", offset(*Z_B, -0.1, 0), "ZB", "09:00")]]
z_pts = [z_hotel] + z_stops
z_walk = [[sd._estimate(a, b, "walk") for b in z_pts] for a in z_pts]
z_car = [[sd._estimate(a, b, "car") for b in z_pts] for a in z_pts]
assert len(set(sd._walk_groups(z_walk, 4)[1:])) == 1, z_walk
run = lambda st: [o["placeId"] for o in sd.schedule(z_hotel, st, z_walk, z_car, 9 * 60, 21 * 60, "walk", True)[0]]
old = run([{k: v for k, v in s_.items() if k != "zone"} for s_ in z_stops])
assert old == ["a1", "b1", "b2", "a2"], old                                  # without zone ids: back and forth
by_zone = run(z_stops)
assert [x[0] for x in by_zone] == ["b", "b", "a", "a"], by_zone              # each zone contiguous, farther zone first
r = sd.lambda_handler({"date": "2027-03-10", "start": z_hotel, "lunch": False, "stops": [
    {**{k: v for k, v in s_.items() if k != "hours"}, "openSimple": {"open": "13:00" if s_["placeId"] == "a2" else "09:00",
                                                                     "close": "18:00"}} for s_ in z_stops]}, None)
assert [o["placeId"][0] for o in r["stops"]] == ["b", "b", "a", "a"], r["stops"]
assert [o["travelBy"] for o in r["stops"][1:]].count("taxi") <= 1, r["stops"]
# when the farther zone would be left for a long wait and visited again later, the other zone goes first instead
lock = [{**s_, "hours": hours_for({"openSimple": {"open": "13:00" if s_["placeId"] == "b2" else "09:00", "close": "18:00"}})}
        for s_ in z_stops]
far_first = [o["placeId"] for o in sd.schedule(z_hotel, lock, z_walk, z_car, 9 * 60, 21 * 60, "walk", False)[0]]
assert far_first == ["b1", "a1", "a2", "b2"], far_first                       # the wait lock leaves B and comes back
r = sd.lambda_handler({"date": "2027-03-10", "start": z_hotel, "lunch": False, "stops": [
    {**{k: v for k, v in s_.items() if k != "hours"}, "openSimple": {"open": "13:00" if s_["placeId"] == "b2" else "09:00",
                                                                     "close": "18:00"}} for s_ in z_stops]}, None)
assert [o["placeId"] for o in r["stops"]] == ["a1", "a2", "b1", "b2"], r["stops"]
seq_src = ast.get_source_segment(SOURCE, top["sequence"])
assert '"zones": {i: zone_of[i] for i in ordered_ids if i in zone_of}' in seq_src, "the agent passes each stop's zone"
print("sequence_day groups by zone: two zones 2 km apart stay contiguous", by_zone)

# ---------------------------------------------------------------- verifier: more than one ride between stops
vf = load("tp-verify-itinerary")
v_pool = {"stay": {"kind": "stay", "lat": 35.690, "lng": 139.760, "name": "Hotel", "priceTotalSGD": 100},
          "fl": {"kind": "flight", "priceTotalSGD": 100}}
stops = []
for i, (ll, t0, by) in enumerate([(ASAKUSA, "09:30", "taxi"), (ASAKUSA, "10:45", "walk"), (SHIBUYA, "12:30", "taxi"),
                                  (ASAKUSA, "14:40", "taxi")]):
    v_pool[f"p{i}"] = {"kind": "place", "name": f"P{i}", "lat": ll[0], "lng": ll[1]}
    stops.append({"placeId": f"p{i}", "arriveAt": t0, "departAt": f"{int(t0[:2]) + 1}:{t0[3:]}",
                  "travelMinutesFromPrev": 0 if i == 1 else 40, "travelBy": by})
res = vf.verify({"startDate": "2026-12-01", "endDate": "2026-12-03", "pax": 1, "budgetSGD": 5000},
                {"flightOfferId": "fl", "stayId": "stay", "days": [{"date": "2026-12-02", "stops": stops}]}, v_pool, 100)
ride = [w for w in res["warnings"] if w["check"] == "commute"]
assert ride and "2 taxi rides between stops" in ride[0]["message"] and "longest ride 40 min" in ride[0]["message"], res["warnings"]
# the traveler's longest ride between areas (form: 15, 30 or 45 min) sets the ride limit and the zone pairs shown
one = stops[:3]
v_spec = {"startDate": "2026-12-01", "endDate": "2026-12-03", "pax": 1, "budgetSGD": 5000}
commute = lambda hc: [w for w in vf.verify({**v_spec, "hardConstraints": hc}, {"flightOfferId": "fl", "stayId": "stay",
                      "days": [{"date": "2026-12-02", "stops": one}]}, v_pool, 100)["warnings"] if w["check"] == "commute"]
assert commute({}) and "(limit 30)" in commute({})[0]["message"] and not commute({"maxRideMinutes": 45}), commute({})
assert A["_ride_max"]({}) == 30 and A["_ride_max"]({"preferences": {"days": {"rideMaxMin": 15}}}) == 15
r_zones = [{"id": f"R{i}", "leg": 0, "kind": "walk", "label": "", "sights": [], "lat": 35.68 + 0.04 * i, "lng": 139.76}
           for i in range(3)]   # 4.4 km apart: about 22 min by taxi, 8.9 km: about 39 min
assert [sorted(zone_view(r_zones, [], [], m)[0]["rideMin"]) for m in (15, 30, 45)] == [["R1"], ["R1"], ["R1", "R2"]]

# ---------------------------------------------------------------- find_places zone mode
os.environ["MOCK_MODE"] = "false"
fp = load("tp-find-places")
fp._secret_cache = {"GOOGLE_MAPS_KEY": "test-key"}
POSTS = []


class Resp:
    status_code = 200

    def __init__(s, body):
        s.body = body

    def json(s):
        return s.body


def g(pid, rating, n, kind, status="OPERATIONAL"):
    return {"id": pid, "displayName": {"text": pid}, "location": {"latitude": 35.0, "longitude": 135.77}, "rating": rating,
            "userRatingCount": n, "businessStatus": status, "primaryType": kind}


def fake_post(url, headers=None, json=None, timeout=None):
    POSTS.append({"url": url, "mask": headers["X-Goog-FieldMask"], **json})
    return Resp({"places": [g("thin", 4.6, 40, "italian_restaurant"), g("low", 3.9, 900, "restaurant"),
                            g("shut", 4.8, 900, "restaurant", "CLOSED_PERMANENTLY"), g("young", 4.9, 60, "restaurant"),
                            g("solid", 4.6, 900, "ramen_restaurant"), g("brew", 4.7, 500, "coffee_shop"),
                            g("bun", 4.5, 300, "bakery")]})


fp.requests.post = fake_post
fp._overpass = lambda q, d: (_ for _ in ()).throw(AssertionError("zone mode never asks Overpass"))
STORE.clear()
ev = {"lat": 35.0, "lng": 135.77, "categories": ["restaurant", "cafe", "dessert"], "foodRadiusKm": 1.2,
      "maxPerCategory": 4, "cuisine": "italian|ramen", "dietary": ["vegan"]}
r = fp.lambda_handler(ev, None)
assert r["source"] == "google-zone" and len(POSTS) == 1 and POSTS[0]["url"].endswith("places:searchNearby"), POSTS
body = POSTS[0]
assert {"italian_restaurant", "ramen_restaurant", "vegan_restaurant", "restaurant", "cafe", "coffee_shop", "coffee_roastery",
        "tea_house", "bakery", "dessert_shop"} <= set(body["includedPrimaryTypes"]), body["includedPrimaryTypes"]
assert body["locationRestriction"]["circle"]["radius"] == 1200 and body["rankPreference"] == "POPULARITY"
assert body["maxResultCount"] == 20 and "places.primaryType" in body["mask"]
names = [p["name"] for p in r["places"]]
assert names == ["brew", "bun", "solid", "young"], names     # floor, then 4.6 x 900 reviews before 4.9 x 60
assert {p["name"]: p["category"] for p in r["places"]} == {"solid": "restaurant", "young": "restaurant", "brew": "cafe",
                                                           "bun": "dessert"}
solid = next(p for p in r["places"] if p["name"] == "solid")
assert solid["cuisine"] == "ramen" and solid["reviews"] == 900
assert not any(k.startswith("places-v5:") for k in STORE), "zone answers are never cached"
POSTS.clear()
fp.lambda_handler({**ev, "categories": ["restaurant"], "cuisine": "", "dietary": []}, None)
assert sorted(POSTS[0]["includedPrimaryTypes"]) == ["restaurant"], POSTS[0]["includedPrimaryTypes"]
fp.requests.post = lambda *a, **k: (_ for _ in ()).throw(fp.requests.RequestException("down"))
fp._amazon_location = lambda q, d: ([fp._place("aloc:1", "Corner Diner", "restaurant", 35.001, 135.77, q)], None)
r = fp.lambda_handler(ev, None)
assert r["source"] == "amazon_location-zone" and [p["name"] for p in r["places"]] == ["Corner Diner"], r
print("ZONES OK")

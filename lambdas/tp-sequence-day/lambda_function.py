"""
tp-sequence-day — Lambda tool (pure code, no LLM): order one day's chosen places into a
feasible schedule that respects opening hours and travel times, starting from the hotel.

Travel times come from the tp-route-matrix function (Amazon Location) when
ROUTE_MATRIX_FUNCTION is set; otherwise from the same distance estimate it uses.

Opening hours are read for the planned date (hours_util): weekday rules, lunch breaks and closed days are
respected, and a place that is closed that day is returned in "unscheduled".

Meals (when "lunch" is true, the default): the first restaurant or street-food stop of the day starts no earlier
than 11:30 and is nudged into the 12:00-14:00 window; any later restaurant or street-food stop starts no earlier
than 17:30 (dinner). A keyword result such as "halal restaurant" (category "activity") counts as a restaurant.
Cafes and desserts have no time floor, so a morning coffee is possible.

Optional limits (0 or missing = no limit): "maxStops" and "minFreeMinutes" keep the day within the verifier's
pacing and free-time rules; stops that do not fit are returned in "unscheduled".

Environment variables:
  CACHE_TABLE            DynamoDB cache table — used to look up places/stays by ID (candidate pool)
  ROUTE_MATRIX_FUNCTION  optional, e.g. "tp-route-matrix" (role needs lambda:InvokeFunction on it)

Input event (places can be given by ID only; details are read from the pool):
  {"date": "2026-12-11", "stayId": "mock:stay_1",
   "placeIds": ["osm:way/123", "osm:node/456"],
   "visitMinutes": {"osm:way/123": 90},          # optional overrides
   "zones": {"osm:way/123": "Z1"},               # optional zone id per place: the day finishes a zone, then rides on
   "dayStart": "09:00", "dayEnd": "21:00", "mode": "walk", "lunch": true,
   "maxStops": 6, "minFreeMinutes": 60,
   "pinned": ["osm:way/123"]}                    # optional: places the traveler named; default = category "requested"
  Instead of stayId/placeIds you can pass full objects:
  "start": {"id": "hotel", "lat": .., "lng": ..},
  "stops": [{"placeId": "..", "name": "..", "lat": .., "lng": .., "visitMinutes": 90,
             "openSimple": {"open": "09:00", "close": "17:00"}, "zone": "Z1"}]
"""
import json
import math
import os

import boto3

from hours_util import fit_visit, hours_for

DEFAULT_VISIT = {"museum": 120, "gallery": 90, "temple": 60, "shrine": 45, "church": 45, "attraction": 90,
                 "viewpoint": 45, "park": 75, "garden": 75, "zoo": 150, "market": 75, "shopping": 90,
                 "restaurant": 75, "cafe": 45, "bar": 75,
                 "climbing": 150, "hiking": 240, "nature": 150, "onsen": 120, "beach": 150, "activity": 120,
                 "requested": 90,
                 # categories that used to fall back to 60 minutes
                 "theme_park": 240, "aquarium": 120, "historic": 75, "spa": 90, "dessert": 30,
                 "street_food": 45, "nightlife": 90, "bookshop": 30, "playground": 45}
# Meal stops: the first one of the day is lunch (not before 11:30), a later one is dinner (not before 17:30).
# Cafes and desserts are not meals, so they have no time floor.
MEAL_CATEGORIES = ("restaurant", "street_food")
# Keyword searches that really mean "a place to eat" (e.g. "halal restaurant"). Food tours and cooking classes
# are not in this list on purpose: they stay long activities.
FOOD_KEYWORDS = ("restaurant", "eatery", "dining")
LUNCH_FLOOR = 11 * 60 + 30
DINNER_FLOOR = 17 * 60 + 30
WALK_KMH, WALK_DETOUR = 4.5, 1.3
CAR_KMH, CAR_DETOUR, CAR_OVERHEAD_MIN = 22.0, 1.4, 5
# Longer walks switch to the car/taxi time for that hop (mode "walk"). 45 keeps a planner zone (1.2 km radius, so
# members at most 2.4 km apart: ceil(2.4 x 1.3 / 4.5 x 60) = 42 min) on foot; pairs of a sparse 1.6 km zone more
# than about 2.6 km apart take a short taxi.
MAX_WALK_MINUTES = 45
LOCK_MAX_WAIT_MIN = 45   # leave a walkable group early only if its next stop would wait longer

_dynamodb = boto3.resource("dynamodb")


def _err(code, message, **extra):
    return {"ok": False, "error": code, "message": message, "source": "sequence-day", **extra}


def _hm(s):
    h, m = str(s).split(":")
    return int(h) * 60 + int(m)


def _fmt(mins):
    mins = int(round(mins))
    return f"{mins // 60:02d}:{mins % 60:02d}"


def _km(a, b):
    r = 6371.0
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lng"] - a["lng"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _estimate(a, b, mode):
    km = _km(a, b)
    if km < 0.01:
        return 0
    if mode == "car":
        return math.ceil(km * CAR_DETOUR / CAR_KMH * 60 + CAR_OVERHEAD_MIN)
    return math.ceil(km * WALK_DETOUR / WALK_KMH * 60)


def _pool_get(ids):
    table = os.environ.get("CACHE_TABLE")
    if not table or not ids:
        return {}
    keys = [{"cacheKey": "pool:" + i} for i in dict.fromkeys(ids)]
    found = {}
    for start in range(0, len(keys), 100):
        req = {table: {"Keys": keys[start:start + 100]}}
        for _ in range(5):
            resp = _dynamodb.batch_get_item(RequestItems=req)
            for item in resp["Responses"].get(table, []):
                data = json.loads(item["payload"])
                found[data["id"]] = data
            req = resp.get("UnprocessedKeys") or {}
            if not req:
                break
    return found


def _matrix(points, mode):
    """Returns (minutes matrix, source). Uses tp-route-matrix if configured."""
    fn = os.environ.get("ROUTE_MATRIX_FUNCTION")
    if fn:
        try:
            resp = boto3.client("lambda").invoke(
                FunctionName=fn, Payload=json.dumps({"points": points, "mode": mode}).encode())
            data = json.loads(resp["Payload"].read())
            if data.get("ok"):
                return data["minutes"], data.get("source", "route-matrix")
            print("route matrix error:", data)
        except Exception as e:  # noqa: BLE001
            print("route matrix invoke failed:", e)
    return [[_estimate(a, b, mode) for b in points] for a in points], "estimate"


def _hop(walk_m, car_m, i, j, mode):
    """Minutes and transport for one hop. In walk mode, long walks become a taxi hop."""
    if mode == "car":
        return car_m[i][j], "taxi"
    w = walk_m[i][j]
    if w > MAX_WALK_MINUTES and car_m is not None and car_m[i][j] < w:
        return car_m[i][j], "taxi"
    return w, "walk"


def _is_meal(s):
    """A restaurant / street-food stop, or a keyword result such as 'halal restaurant' (category 'activity')."""
    if s.get("category") in MEAL_CATEGORIES:
        return True
    return s.get("category") == "activity" and any(w in (s.get("activity") or "").lower() for w in FOOD_KEYWORDS)


def _default_visit(p):
    """Typical visit length; free-text activities guess from their keyword."""
    if p.get("category") == "activity":
        kw = (p.get("activity") or "").lower()
        if any(w in kw for w in FOOD_KEYWORDS):
            return DEFAULT_VISIT["restaurant"]          # a meal, not a 2-hour activity
        if any(w in kw for w in ("hike", "hiking", "trek", "trail", "mountain")):
            return 240
        if any(w in kw for w in ("climb", "boulder")):
            return 150
    if _full_day_park(p):
        return 480
    return DEFAULT_VISIT.get(p.get("category"), 60)


def _full_day_park(p):
    """A big theme park that takes the whole day (Google can list it as an attraction or a theme park)."""
    return p.get("category") in ("requested", "attraction", "theme_park") and any(
        w in (p.get("name") or "").lower() for w in ("disney", "universal studios", "fuji-q"))


def _opens_day(p):
    """A theme park (category theme_park, or a full-day park listed as an attraction) opens its day."""
    return p.get("category") == "theme_park" or _full_day_park(p)


def _resolve(event):
    """Builds start + stops from IDs (via the pool) and/or full objects."""
    ids = list(event.get("placeIds") or [])
    if event.get("stayId"):
        ids.append(event["stayId"])
    pool = _pool_get(ids)

    if event.get("start"):
        s = event["start"]
        start = {"id": str(s.get("id", "start")), "name": s.get("name", "Hotel"),
                 "lat": float(s["lat"]), "lng": float(s["lng"])}
    elif event.get("stayId"):
        st = pool.get(event["stayId"])
        if not st or st.get("lat") is None:
            return None, None, _err("not_in_pool", f"stayId {event['stayId']} not found; search stays again")
        start = {"id": st["id"], "name": st["name"], "lat": st["lat"], "lng": st["lng"]}
    else:
        return None, None, _err("bad_input", "give stayId or start {lat, lng}")

    overrides = event.get("visitMinutes") or {}
    zones = event.get("zones") or {}
    stops, missing = [], []
    for pid in event.get("placeIds") or []:
        p = pool.get(pid)
        if not p:
            missing.append(pid)
            continue
        stops.append({"placeId": pid, "name": p["name"], "category": p.get("category"),
                      "activity": p.get("activity"), "zone": zones.get(pid),
                      "lat": p["lat"], "lng": p["lng"],
                      "hours": hours_for(p, event.get("date")),
                      "visitMinutes": int(overrides.get(pid, _default_visit(p)))})
    for s in event.get("stops") or []:
        stops.append({"placeId": str(s["placeId"]), "name": s.get("name", s["placeId"]),
                      "category": s.get("category"), "activity": s.get("activity"), "zone": s.get("zone"),
                      "lat": float(s["lat"]), "lng": float(s["lng"]),
                      "hours": hours_for({"openSimple": s.get("openSimple")}),
                      "visitMinutes": int(s.get("visitMinutes") or _default_visit(s))})
    if missing:
        return None, None, _err("not_in_pool", f"placeIds not found (expired or never searched): {missing}",
                                missing=missing)
    if not stops:
        return None, None, _err("bad_input", "no stops to sequence")
    if len(stops) > 12:
        return None, None, _err("bad_input", "at most 12 stops per day")
    return start, stops, None


def _walk_groups(walk_m, n):
    """Group id per matrix index (0 = start): stops joined by a chain of walks of MAX_WALK_MINUTES or less."""
    g = list(range(n + 1))

    def root(i):
        while g[i] != i:
            i = g[i]
        return i
    for i in range(1, n + 1):
        for j in range(i + 1, n + 1):
            if max(walk_m[i][j], walk_m[j][i]) <= MAX_WALK_MINUTES:
                g[root(j)] = root(i)
    return [root(i) for i in range(n + 1)]


def _zone_groups(stops):
    """Group id per matrix index (0 = start): stops that share a zone id; a stop without one is a group of its own."""
    first = {}
    return [0] + [first.setdefault(s["zone"], i) if s.get("zone") else i for i, s in enumerate(stops, 1)]


def schedule(start, stops, walk_m, car_m, day_start, day_end, mode, lunch, long_first=False,
             max_stops=0, min_free=0, first=None, park_first=False):
    """Greedy: from the current spot, pick the stop that can be finished earliest (waiting for
    opening time if needed). With lunch on, the first restaurant / street-food stop is the lunch (not before
    11:30, preferred around 12:00-14:00) and a later one is dinner (not before 17:30); cafes are unrestricted.
    max_stops / min_free (0 = no limit) keep the day within the verifier's pacing and free-time rules.
    In walk mode the day starts in the walkable group farthest from the hotel and finishes a group before it takes a
    ride to the next one, unless the group's best next stop would wait more than LOCK_MAX_WAIT_MIN. A group is a zone
    when the stops carry zone ids, else a chain of walks of MAX_WALK_MINUTES or less. first (a group id) starts the
    day in that group instead. park_first starts the day with a theme park (_opens_day) when one fits."""
    t, cur = day_start, 0                       # index 0 = start (hotel)
    remaining = list(range(1, len(stops) + 1))  # matrix index i -> stops[i-1]
    out = []
    meals_done = 0                              # meal stops (restaurant / street food) already scheduled
    busy = 0                                    # visit + travel minutes used so far

    def pick(cands):
        best = None
        for i in cands:
            s = stops[i - 1]
            travel, how = _hop(walk_m, car_m, cur, i, mode)
            arrive = t + travel
            is_meal = lunch and _is_meal(s)
            floor = (DINNER_FLOOR if meals_done else LUNCH_FLOOR) if is_meal else 0
            begin = fit_visit(s["hours"], max(arrive, floor), s["visitMinutes"])
            if begin is None:                   # closed that day, or never fits an opening interval
                continue
            end = begin + s["visitMinutes"]
            back, _ = _hop(walk_m, car_m, i, 0, mode)
            if end + back > day_end:
                continue
            if min_free and busy + travel + s["visitMinutes"] + back > (day_end - day_start) - min_free:
                continue                        # would leave less free time than required
            score = end
            if long_first and s["visitMinutes"] >= 120:
                score -= 10000 + s["visitMinutes"] * 10   # long activities (hikes, climbing) first
            if is_meal and not meals_done and 12 * 60 <= begin <= 14 * 60:
                score -= 60                     # nudge the lunch into the lunch window
            if best is None or score < best[0]:
                best = (score, i, travel, how, arrive, begin, end)
        return best

    group = None if mode != "walk" else (_zone_groups(stops) if any(s.get("zone") for s in stops)
                                         else _walk_groups(walk_m, len(stops)))
    while remaining:
        if max_stops and len(out) >= max_stops:
            break
        parks = [i for i in remaining if park_first and not out and _opens_day(stops[i - 1])]
        best = pick(parks) if parks else None   # the park opens the day, the other stops come after it
        if best is None:
            if group and not out:   # start in the walkable group farthest from the hotel: one longer ride in, a short one home
                far = first if first is not None else max(
                    {group[i] for i in remaining}, key=lambda g_: (min(car_m[0][i] for i in remaining if group[i] == g_), -g_))
                here = [i for i in remaining if group[i] == far]
            else:
                here = [i for i in remaining if group and group[i] == group[cur]]
            best = pick(here) if here else None
            if best is None or best[5] - best[4] > LOCK_MAX_WAIT_MIN:   # nothing in this walkable group fits now
                best = pick(remaining)
        if best is None:
            break
        _, i, travel, how, arrive, begin, end = best
        s = stops[i - 1]
        out.append({"placeId": s["placeId"], "name": s["name"], "category": s.get("category"),
                    "lat": s["lat"], "lng": s["lng"], "travelMinutesFromPrev": travel, "travelBy": how,
                    "arriveAt": _fmt(begin), "departAt": _fmt(end),
                    "waitMinutes": max(begin - arrive, 0), "visitMinutes": s["visitMinutes"]})
        if lunch and _is_meal(s):
            meals_done += 1
        busy += travel + s["visitMinutes"]
        t, cur = end, i
        remaining.remove(i)
    back_minutes = _hop(walk_m, car_m, cur, 0, mode)[0] if out else 0
    busy_total = sum(o["visitMinutes"] + o["travelMinutesFromPrev"] for o in out) + back_minutes
    unscheduled = [{"placeId": stops[i - 1]["placeId"], "name": stops[i - 1]["name"],
                    "reason": ("closed on this weekday" if stops[i - 1]["hours"]["closed"]
                               else "does not fit opening hours, the day window or the free-time limit")}
                   for i in remaining]
    return out, unscheduled, back_minutes, max(day_end - day_start - busy_total, 0)


def lambda_handler(event, context):
    if isinstance(event, str):
        event = json.loads(event)
    event = event or {}
    try:
        day_start, day_end = _hm(event.get("dayStart", "09:00")), _hm(event.get("dayEnd", "21:00"))
    except ValueError:
        return _err("bad_input", "dayStart/dayEnd must be HH:MM")
    if day_end - day_start < 60:
        return _err("bad_input", "day window must be at least 1 hour")
    mode = str(event.get("mode", "walk")).lower()
    if mode not in ("walk", "car"):
        return _err("bad_input", "mode must be 'walk' or 'car'")

    start, stops, error = _resolve(event)
    if error:
        return error

    points = [{"id": start["id"], "lat": start["lat"], "lng": start["lng"]}] + \
             [{"id": s["placeId"], "lat": s["lat"], "lng": s["lng"]} for s in stops]
    if mode == "car":
        car_m, src = _matrix(points, "car")
        walk_m = car_m
    else:
        walk_m, src = _matrix(points, "walk")
        car_m, _ = _matrix(points, "car")

    lunch = bool(event.get("lunch", True))
    max_stops = int(event.get("maxStops") or 0)
    min_free = int(event.get("minFreeMinutes") or 0)
    # Try "earliest finish first" and "long activities first", and with zone ids each zone as the first one (on a day
    # with a theme park each of these also with the park first); keep the one that fits the most pinned
    # places, then opens with the park, then the most stops, then never goes back to a zone it left, then starts in the
    # farther zone, then has the least waiting.
    pinned = ({x for x in event["pinned"] if isinstance(x, str)} if isinstance(event.get("pinned"), list)
              else {s["placeId"] for s in stops if s.get("category") == "requested"})
    firsts = [None] + (sorted(set(_zone_groups(stops)[1:])) if mode == "walk" and any(s.get("zone") for s in stops)
                       else [])
    zone_of = {s["placeId"]: s.get("zone") or s["placeId"] for s in stops}

    def returns(o):
        z = [zone_of[x["placeId"]] for x in o[0]]
        runs = [a for k, a in enumerate(z) if k == 0 or a != z[k - 1]]
        return len(runs) - len(set(runs))
    parks = {s["placeId"] for s in stops if _opens_day(s)}
    options = [(f, schedule(start, stops, walk_m, car_m, day_start, day_end, mode, lunch, long_first=lf,
                            max_stops=max_stops, min_free=min_free, first=f, park_first=pf))
               for pf in ((False, True) if parks else (False,)) for lf in (False, True) for f in firsts]
    ordered, unscheduled, back, free = max(options, key=lambda fo: (
        sum(x["placeId"] in pinned for x in fo[1][0]), bool(fo[1][0]) and fo[1][0][0]["placeId"] in parks,
        len(fo[1][0]), -returns(fo[1]), fo[0] is None,
        -sum(x["waitMinutes"] for x in fo[1][0]), fo[1][3]))[1]
    return {"ok": True, "date": event.get("date"), "start": start, "stops": ordered,
            "returnToStartMinutes": back, "freeTimeMinutes": free, "unscheduled": unscheduled,
            "dayStart": _fmt(day_start), "dayEnd": _fmt(day_end), "travelTimeSource": src}

"""
tp-sequence-day — Lambda tool (pure code, no LLM): order one day's chosen places into a
feasible schedule that respects opening hours and travel times, starting from the hotel.

Travel times come from the tp-route-matrix function (Amazon Location) when
ROUTE_MATRIX_FUNCTION is set; otherwise from the same distance estimate it uses.

Meals (when "lunch" is true, the default): the first restaurant or street-food stop of the day starts no earlier
than 11:30 and is nudged into the 12:00-14:00 window; any later restaurant or street-food stop starts no earlier
than 17:30 (dinner). Cafes and desserts have no time floor, so a morning coffee is possible.

Environment variables:
  CACHE_TABLE            DynamoDB cache table — used to look up places/stays by ID (candidate pool)
  ROUTE_MATRIX_FUNCTION  optional, e.g. "tp-route-matrix" (role needs lambda:InvokeFunction on it)

Input event (places can be given by ID only; details are read from the pool):
  {"date": "2026-12-11", "stayId": "mock:stay_1",
   "placeIds": ["osm:way/123", "osm:node/456"],
   "visitMinutes": {"osm:way/123": 90},          # optional overrides
   "dayStart": "09:00", "dayEnd": "21:00", "mode": "walk", "lunch": true}
  Instead of stayId/placeIds you can pass full objects:
  "start": {"id": "hotel", "lat": .., "lng": ..},
  "stops": [{"placeId": "..", "name": "..", "lat": .., "lng": .., "visitMinutes": 90,
             "openSimple": {"open": "09:00", "close": "17:00"}}]
"""
import json
import math
import os
from hours_util import hours_for, fit_visit
import boto3

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
LUNCH_FLOOR = 11 * 60 + 30
DINNER_FLOOR = 17 * 60 + 30
WALK_KMH, WALK_DETOUR = 4.5, 1.3
CAR_KMH, CAR_DETOUR, CAR_OVERHEAD_MIN = 22.0, 1.4, 5
MAX_WALK_MINUTES = 30  # longer walks switch to the car/taxi time for that hop (mode "walk")

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


def _default_visit(p):
    """Typical visit length; free-text activities guess from their keyword."""
    if p.get("category") == "activity":
        kw = (p.get("activity") or "").lower()
        if any(w in kw for w in ("hike", "hiking", "trek", "trail", "mountain")):
            return 240
        if any(w in kw for w in ("climb", "boulder")):
            return 150
    return DEFAULT_VISIT.get(p.get("category"), 60)


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
    stops, missing = [], []
    for pid in event.get("placeIds") or []:
        p = pool.get(pid)
        if not p:
            missing.append(pid)
            continue
        stops.append({"placeId": pid, "name": p["name"], "category": p.get("category"),
                      "lat": p["lat"], "lng": p["lng"],
                      "hours": hours_for(p, event.get("date")),
                      "visitMinutes": int(overrides.get(pid, _default_visit(p)))})
    for s in event.get("stops") or []:
        stops.append({"placeId": str(s["placeId"]), "name": s.get("name", s["placeId"]),
                      "category": s.get("category"), "lat": float(s["lat"]), "lng": float(s["lng"]),
                      "hours": hours_for({"openSimple": s.get("openSimple")}),
                      "visitMinutes": int(s.get("visitMinutes") or DEFAULT_VISIT.get(s.get("category"), 60))})
    if missing:
        return None, None, _err("not_in_pool", f"placeIds not found (expired or never searched): {missing}",
                                missing=missing)
    if not stops:
        return None, None, _err("bad_input", "no stops to sequence")
    if len(stops) > 12:
        return None, None, _err("bad_input", "at most 12 stops per day")
    return start, stops, None

def schedule(start, stops, walk_m, car_m, day_start, day_end, mode, lunch, long_first=False,
             max_stops=0, min_free=0):
    """Greedy: from the current spot, pick the stop that can be finished earliest (waiting for
    opening time if needed). With lunch on, the first restaurant / street-food stop is the lunch (not before
    11:30, preferred around 12:00-14:00) and a later one is dinner (not before 17:30); cafes are unrestricted.
    max_stops / min_free (0 = no limit) keep the day within the verifier's pacing and free-time rules."""
    t, cur = day_start, 0                       # index 0 = start (hotel)
    remaining = list(range(1, len(stops) + 1))  # matrix index i -> stops[i-1]
    out = []
    meals_done = 0                              # meal stops (restaurant / street food) already scheduled
    busy = 0                                    # visit + travel minutes used so far
    while remaining:
        if max_stops and len(out) >= max_stops:
            break
        best = None
        for i in remaining:
            s = stops[i - 1]
            travel, how = _hop(walk_m, car_m, cur, i, mode)
            arrive = t + travel
            is_meal = lunch and s.get("category") in MEAL_CATEGORIES
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
        if best is None:
            break
        _, i, travel, how, arrive, begin, end = best
        s = stops[i - 1]
        out.append({"placeId": s["placeId"], "name": s["name"], "category": s.get("category"),
                    "lat": s["lat"], "lng": s["lng"], "travelMinutesFromPrev": travel, "travelBy": how,
                    "arriveAt": _fmt(begin), "departAt": _fmt(end),
                    "waitMinutes": max(begin - arrive, 0), "visitMinutes": s["visitMinutes"]})
        if lunch and s.get("category") in MEAL_CATEGORIES:
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
    # Try "earliest finish first" and "long activities first"; keep the one that fits the most stops,
    # then the one with the least waiting.
    max_stops = int(event.get("maxStops") or 0)
    min_free = int(event.get("minFreeMinutes") or 0)
    options = [schedule(start, stops, walk_m, car_m, day_start, day_end, mode, lunch, long_first=lf,
                        max_stops=max_stops, min_free=min_free)
               for lf in (False, True)]
    ordered, unscheduled, back, free = max(
        options, key=lambda o: (len(o[0]), -sum(x["waitMinutes"] for x in o[0]), o[3]))
    return {"ok": True, "date": event.get("date"), "start": start, "stops": ordered,
            "returnToStartMinutes": back, "freeTimeMinutes": free, "unscheduled": unscheduled,
            "dayStart": _fmt(day_start), "dayEnd": _fmt(day_end), "travelTimeSource": src}

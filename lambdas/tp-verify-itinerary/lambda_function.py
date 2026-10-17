"""
tp-verify-itinerary — Lambda tool (pure code, no LLM): check a draft itinerary against the
trip's hard constraints. Prices are always re-read from the candidate pool by ID, never
taken from the model's draft. Returns pass/fail per check plus machine-readable violations
the repair step can act on.

Environment variables:
  CACHE_TABLE                  DynamoDB cache table holding pool:<id> items
  DAILY_SPEND_SGD_PER_PERSON   estimate for food, local transport and entry fees (default 100)

Input event:
  {"tripSpec": {"startDate": "2026-12-10", "endDate": "2026-12-15", "pax": 2, "budgetSGD": 5000,
                "dayStart": "09:00", "dayEnd": "21:00",
                "hardConstraints": {"maxStopsPerDay": 6, "minFreeMinutesPerDay": 60},
                "mustVisit": ["<placeId the user asked for by name>", ...]},             # optional
   "itinerary": {"flightOfferId": "...", "stayId": "...",       # single city
                 # multi-city instead: "stays": [{"stayId", "checkIn", "checkOut"}, ...],
                 #                     "transfers": [{"transferId", "date"}, ...]   (one per change of city)
                 "days": [{"date": "2026-12-10",
                           # optional: a day spent in another town, returning to the same hotel at night
                           "dayTrip": {"place": "Nikko", "lat": 36.75, "lng": 139.6, "travelMinutes": 110},
                           "stops": [{"placeId": "...", "arriveAt": "15:00", "departAt": "16:30",
                                      "travelMinutesFromPrev": 20}]}]}}
"""
import json
import math
import os
from datetime import date, datetime, timedelta

import boto3

_dynamodb = boto3.resource("dynamodb")
FASTEST_CITY_KMH = 25.0      # sanity lower bound for travel when no travel time is given
ARRIVAL_BUFFER_MIN = 120     # landing -> immigration -> hotel
DEPARTURE_BUFFER_MIN = 180   # leave for the airport this long before take-off
TRANSFER_BUFFER_MIN = 60     # after a transfer arrives: get to the hotel / drop bags
MAX_KM_FROM_BASE = 120       # a place further than this from that night's hotel is in the wrong city
DAY_TRIP_MAX_KM = 450        # a day trip further than this from the hotel is not a day trip
DAY_TRIP_MAX_ONE_WAY_MIN = 210  # more than 3.5 h each way leaves no time to see anything
DAY_TRIP_RADIUS_KM = 40      # on a day trip, places must be around the day-trip town


def _err(code, message, **extra):
    return {"ok": False, "error": code, "message": message, "source": "verify", **extra}


def _hm(s):
    h, m = str(s).split(":")
    return int(h) * 60 + int(m)


def _km(a, b):
    r = 6371.0
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lng"] - a["lng"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


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


class Report:
    def __init__(self):
        self.checks, self.violations = {}, []

    def ok(self, name, detail=""):
        self.checks.setdefault(name, {"name": name, "passed": True, "detail": detail})

    def fail(self, name, message, hint, **where):
        self.checks[name] = {"name": name, "passed": False, "detail": message}
        self.violations.append({"check": name, "message": message, "repairHint": hint, **where})


def verify(spec, it, pool, daily_spend):
    rep = Report()
    start, end = date.fromisoformat(spec["startDate"]), date.fromisoformat(spec["endDate"])
    pax = int(spec.get("pax", 1))
    budget = float(spec["budgetSGD"])
    hc = spec.get("hardConstraints") or {}
    max_stops = int(hc.get("maxStopsPerDay", 6))
    min_free = int(hc.get("minFreeMinutesPerDay", 0))
    day_start, day_end = _hm(spec.get("dayStart", "09:00")), _hm(spec.get("dayEnd", "21:00"))
    n_days = (end - start).days + 1
    days = it.get("days") or []

    # Normalise: single-city plans give "stayId"; multi-city plans give "stays" (+ "transfers").
    stays_in = it.get("stays") or ([{"stayId": it["stayId"], "checkIn": spec["startDate"], "checkOut": spec["endDate"]}]
                                   if it.get("stayId") else [])
    transfers_in = it.get("transfers") or []
    stays = []
    for st in stays_in:
        p = pool.get(st.get("stayId"))
        stays.append({**(p or {}), "stayId": st.get("stayId"), "found": bool(p),
                      "checkIn": st.get("checkIn") or (p or {}).get("checkIn"),
                      "checkOut": st.get("checkOut") or (p or {}).get("checkOut")})
    stays.sort(key=lambda x: x.get("checkIn") or "")
    transfers = [{**(pool.get(t.get("transferId")) or {}), "transferId": t.get("transferId"),
                  "found": bool(pool.get(t.get("transferId"))), "date": t.get("date") or
                  (pool.get(t.get("transferId")) or {}).get("date")} for t in transfers_in]

    def base_stay(dt):
        """Hotel you sleep in the night before / start the day from on date dt."""
        cur = None
        for st in stays:
            if st.get("checkIn") and st["checkIn"] <= dt:
                cur = st
        return cur

    # 1. IDs exist in the candidate pool (no invented flights, hotels, transfers or places)
    flight = pool.get(it.get("flightOfferId")) if it.get("flightOfferId") else None
    if it.get("flightOfferId") and not flight:
        rep.fail("ids_exist", f"flightOfferId {it['flightOfferId']} is not in the candidate pool",
                 "Use an offerId returned by search_flights, or search flights again.", item=it["flightOfferId"])
    for st in stays:
        if not st["found"]:
            rep.fail("ids_exist", f"stayId {st['stayId']} is not in the candidate pool",
                     "Use a stayId returned by search_stays, or search stays again.", item=st["stayId"])
    for t in transfers:
        if not t["found"]:
            rep.fail("ids_exist", f"transferId {t['transferId']} is not in the candidate pool",
                     "Use a transferId returned by search_transfers.", item=t["transferId"])
    for d in days:
        for s_ in d.get("stops") or []:
            p = pool.get(s_.get("placeId"))
            if not p or p.get("kind") != "place":
                rep.fail("ids_exist", f"placeId {s_.get('placeId')} is not in the candidate pool",
                         "Replace it with a placeId returned by find_places.", day=d.get("date"),
                         item=s_.get("placeId"))
    if not it.get("flightOfferId"):
        rep.fail("ids_exist", "no flight chosen", "Choose one offerId from search_flights.")
    if not stays:
        rep.fail("ids_exist", "no hotel chosen", "Choose a stayId from search_stays for each city.")
    rep.ok("ids_exist", "all IDs found in the candidate pool")

    # 2. Budget — totals computed here from pool prices only
    def price(x):
        return float(x["priceTotalSGD"]) if x and x.get("priceTotalSGD") is not None else 0.0
    flight_sgd = price(flight)
    stay_sgd = round(sum(price(st) for st in stays if st["found"]), 2)
    transfer_sgd = round(sum(price(t) for t in transfers if t["found"]), 2)
    spend_sgd = daily_spend * pax * n_days
    total = round(flight_sgd + stay_sgd + transfer_sgd + spend_sgd, 2)
    totals = {"flightSGD": flight_sgd, "staySGD": stay_sgd, "transferSGD": transfer_sgd,
              "dailySpendEstimateSGD": spend_sgd, "totalSGD": total, "budgetSGD": budget,
              "remainingSGD": round(budget - total, 2),
              "staysSGD": [{"stayId": st["stayId"], "name": st.get("name"), "sgd": price(st)} for st in stays],
              "transfersSGD": [{"transferId": t["transferId"], "sgd": price(t), "estimate": t.get("estimate", False)}
                               for t in transfers],
              "dailySpendAssumption": f"SGD {daily_spend:g} per person per day (food, local transport, entry fees)"}
    if total > budget:
        over = round(total - budget, 2)
        rep.fail("budget", f"estimated total SGD {total:,.2f} is over budget by SGD {over:,.2f}",
                 f"Cut at least SGD {over:,.0f}: pick cheaper stayIds (largest saving usually), cheaper transfers "
                 f"(ground instead of flights) or a cheaper flightOfferId.", overBySGD=over)
    else:
        rep.ok("budget", f"SGD {total:,.2f} of SGD {budget:,.2f}")

    # 3. Day count and dates
    expected = [(start + timedelta(days=i)).isoformat() for i in range(n_days)]
    got = [d.get("date") for d in days]
    if got != expected:
        rep.fail("dates", f"itinerary days {got} do not match trip dates {expected[0]}..{expected[-1]} "
                          f"({n_days} days)", "Return exactly one day object per trip date, in order.")
    else:
        rep.ok("dates", f"{n_days} days, {expected[0]} to {expected[-1]}")

    # 4. Flights, hotels and transfers line up (a continuous chain from start to end)
    if flight and flight.get("slices"):
        out_dep = flight["slices"][0].get("departAt", "")[:10]
        if out_dep and out_dep not in (spec["startDate"], (start - timedelta(days=1)).isoformat()):
            rep.fail("travel_dates", f"outbound flight departs {out_dep}, trip starts {spec['startDate']}",
                     "Pick a flight whose outbound leg departs on the start date (or the night before).")
        if len(flight["slices"]) > 1:
            ret_dep = flight["slices"][-1].get("departAt", "")[:10]
            if ret_dep and ret_dep != spec["endDate"]:
                rep.fail("travel_dates", f"return flight departs {ret_dep}, trip ends {spec['endDate']}",
                         "Pick a flight whose return leg departs on the end date.")
    if stays:
        if stays[0].get("checkIn") != spec["startDate"]:
            rep.fail("travel_dates", f"first hotel starts {stays[0].get('checkIn')}, trip starts {spec['startDate']}",
                     "The first stay must check in on the start date.")
        if stays[-1].get("checkOut") != spec["endDate"]:
            rep.fail("travel_dates", f"last hotel ends {stays[-1].get('checkOut')}, trip ends {spec['endDate']}",
                     "The last stay must check out on the end date.")
        for a_, b_ in zip(stays, stays[1:]):
            if a_.get("checkOut") != b_.get("checkIn"):
                rep.fail("travel_dates", f"gap or overlap between hotels: {a_.get('name')} ends {a_.get('checkOut')}, "
                                         f"{b_.get('name')} starts {b_.get('checkIn')}",
                         "Each stay must check in on the day the previous one checks out.")
        for st in stays:
            if st["found"] and int(st.get("guests", pax)) < pax:
                rep.fail("travel_dates", f"{st.get('name')} is booked for {st.get('guests')} guests, trip has {pax}",
                         "Search stays with the right number of guests.")
            if st["found"] and (st.get("checkIn"), st.get("checkOut")) != \
                    ((pool.get(st["stayId"]) or {}).get("checkIn"), (pool.get(st["stayId"]) or {}).get("checkOut")):
                rep.fail("travel_dates", f"{st.get('name')} was priced for {(pool.get(st['stayId']) or {}).get('checkIn')}"
                                         f"..{(pool.get(st['stayId']) or {}).get('checkOut')}, planned "
                                         f"{st.get('checkIn')}..{st.get('checkOut')}",
                         "Search stays again for the planned dates and use that stayId.")
    if len(stays) > 1:
        if len(transfers) != len(stays) - 1:
            rep.fail("transfers", f"{len(stays)} hotels need {len(stays) - 1} transfers, plan has {len(transfers)}",
                     "Add one transferId from search_transfers for each move between cities.")
        for k, t in enumerate(transfers[:len(stays) - 1]):
            move_day = stays[k].get("checkOut")
            if t.get("date") and move_day and t["date"] != move_day:
                rep.fail("transfers", f"transfer to {t.get('toCity')} is on {t['date']}, but you move on {move_day}",
                         "Use a transfer searched for the day you check out.", item=t["transferId"])
            if t.get("overnight"):
                rep.fail("transfers", f"{t.get('summary')} arrives too late or overnight ({t.get('arriveAt')})",
                         "Pick a flight instead, or reorder the cities.", item=t["transferId"])
    rep.ok("travel_dates", "flights, hotels and dates form one continuous trip")
    rep.ok("transfers", "one transfer per change of city")

    # 5. Duplicates
    seen = {}
    for d in days:
        for s_ in d.get("stops") or []:
            pid = s_.get("placeId")
            if pid in seen:
                rep.fail("no_duplicates", f"{pool.get(pid, {}).get('name', pid)} appears on {seen[pid]} and {d.get('date')}",
                         "Replace one occurrence with a different place.", day=d.get("date"), item=pid)
            else:
                seen[pid] = d.get("date")
    rep.ok("no_duplicates", "each place appears once")

    # 5b. Places the user asked for by name must be in the plan
    for pid in spec.get("mustVisit") or []:
        if pid not in seen:
            rep.fail("must_visit", f"{pool.get(pid, {}).get('name', pid)} was requested by the user but is not planned",
                     f"Add placeId {pid} to a suitable day (check its opening hours and city).", item=pid)
    rep.ok("must_visit", "every place the user asked for is planned")

    # 6. Per-day feasibility: timings, travel, opening hours, flight/transfer times, base, pacing, free time
    arrival_floor = departure_ceiling = None
    if flight and flight.get("slices"):
        arr = flight["slices"][0].get("arriveAt")
        if arr and arr[:10] == spec["startDate"]:
            arrival_floor = _hm(arr[11:16]) + ARRIVAL_BUFFER_MIN
        if len(flight["slices"]) > 1:
            dep = flight["slices"][-1].get("departAt")
            if dep and dep[:10] == spec["endDate"]:
                departure_ceiling = _hm(dep[11:16]) - DEPARTURE_BUFFER_MIN
    transfer_floor = {}
    for t in transfers:
        arr = t.get("arriveAt") or ""
        if t.get("date") and arr[:10] == t["date"]:
            transfer_floor[t["date"]] = (_hm(arr[11:16]) + TRANSFER_BUFFER_MIN, t)

    for d in days:
        stops = d.get("stops") or []
        dt = d.get("date")
        if len(stops) > max_stops:
            rep.fail("pacing", f"{dt} has {len(stops)} stops (max {max_stops})",
                     "Remove the lowest-priority stops from this day.", day=dt)
        base = base_stay(dt) if dt else None
        base_pt = base if base and base.get("lat") is not None else None
        hotel_pt, trip_min, busy = base_pt, 0, 0
        dtrip = d.get("dayTrip") if isinstance(d.get("dayTrip"), dict) else None
        if dtrip and dtrip.get("lat") is not None and dtrip.get("lng") is not None:
            tp = {"lat": float(dtrip["lat"]), "lng": float(dtrip["lng"]), "name": dtrip.get("place", "day trip")}
            km = _km(hotel_pt, tp) if hotel_pt is not None else 0.0
            trip_min = int(dtrip.get("travelMinutes") or math.ceil(km / 60 * 60) + 15)
            if km > DAY_TRIP_MAX_KM or trip_min > DAY_TRIP_MAX_ONE_WAY_MIN:
                rep.fail("day_trip", f"{dt}: day trip to {tp['name']} is {km:.0f} km / about {trip_min} min each way "
                                     f"from the hotel", "Too far for a day trip: stay a night there instead "
                                     "(add it as a city), or pick a closer town.", day=dt)
            if dt in transfer_floor or dt in (spec["startDate"], spec["endDate"]):
                rep.fail("day_trip", f"{dt}: a day trip cannot be on an arrival, departure or city-change day",
                         "Move the day trip to a full day in that city.", day=dt)
            base_pt = tp
            busy += 2 * trip_min
        prev_end, prev_place = None, base_pt
        for idx, s_ in enumerate(stops):
            p = pool.get(s_.get("placeId"), {})
            try:
                a, b = _hm(s_["arriveAt"]), _hm(s_["departAt"])
            except (KeyError, ValueError):
                rep.fail("timing", f"{dt} stop {idx + 1} needs arriveAt and departAt as HH:MM",
                         "Run sequence_day for this day and use its times.", day=dt, item=s_.get("placeId"))
                continue
            name = p.get("name", s_.get("placeId"))
            if b <= a:
                rep.fail("timing", f"{dt}: {name} departs before it arrives", "Run sequence_day for this day.",
                         day=dt, item=s_.get("placeId"))
            if prev_end is not None and a < prev_end:
                rep.fail("timing", f"{dt}: {name} overlaps the previous stop", "Run sequence_day for this day.",
                         day=dt, item=s_.get("placeId"))
            limit = DAY_TRIP_RADIUS_KM if trip_min else MAX_KM_FROM_BASE
            if base_pt is not None and p.get("lat") is not None and _km(base_pt, p) > limit:
                rep.fail("far_from_base", f"{dt}: {name} is {_km(base_pt, p):.0f} km from "
                                          + (f"{base_pt.get('name')} (the day trip)" if trip_min else
                                             f"that night's hotel ({base_pt.get('name')})"),
                         f"On this day trip, only plan places in {base_pt.get('name')}." if trip_min else
                         "Pick places in the city you are staying in that day.", day=dt, item=s_.get("placeId"))
            if prev_place is not None and p.get("lat") is not None:
                need = s_.get("travelMinutesFromPrev")
                if need is None:
                    need = math.floor(_km(prev_place, p) / FASTEST_CITY_KMH * 60)
                if idx == 0 and trip_min:
                    need = 0    # the journey to the day-trip town is checked just below
                gap = a - (prev_end if prev_end is not None else day_start)
                if prev_end is not None and gap < int(need):
                    rep.fail("travel_time", f"{dt}: only {gap} min to reach {name}, travel needs about {need} min",
                             "Run sequence_day for this day, or move this stop to another day.",
                             day=dt, item=s_.get("placeId"))
                busy += int(need)
            hours = p.get("openSimple")
            if idx == 0 and trip_min and a < day_start + trip_min:
                rep.fail("day_trip", f"{dt}: {name} at {s_['arriveAt']} is before you can reach "
                                     f"{dtrip.get('place')} ({trip_min} min from the hotel)",
                         "Start the day-trip stops later (run sequence_day with the day-trip start).",
                         day=dt, item=s_.get("placeId"))
            if idx == len(stops) - 1 and trip_min and b + trip_min > day_end + 90:
                rep.fail("day_trip", f"{dt}: leaving {dtrip.get('place')} at {s_['departAt']} gets you back after "
                                     f"the day ends", "End the day-trip stops earlier.", day=dt, item=s_.get("placeId"))
            if hours and (a < _hm(hours["open"]) or b > _hm(hours["close"])):
                rep.fail("opening_hours", f"{dt}: {name} is open {hours['open']}-{hours['close']}, "
                                          f"planned {s_['arriveAt']}-{s_['departAt']}",
                         "Move the visit inside opening hours, or to another day.", day=dt, item=s_.get("placeId"))
            if dt == spec["startDate"] and arrival_floor is not None and a < arrival_floor:
                rep.fail("flight_times", f"{dt}: {name} at {s_['arriveAt']} is before you can reach the city "
                                         f"after landing", "Start day 1 later or drop this stop.",
                         day=dt, item=s_.get("placeId"))
            if dt in transfer_floor and a < transfer_floor[dt][0]:
                rep.fail("flight_times", f"{dt}: {name} at {s_['arriveAt']} is before you arrive in "
                                         f"{transfer_floor[dt][1].get('toCity')}",
                         "On travel days, only plan places after arrival.", day=dt, item=s_.get("placeId"))
            if dt == spec["endDate"] and departure_ceiling is not None and b > departure_ceiling:
                rep.fail("flight_times", f"{dt}: {name} ends {s_['departAt']}, too close to the return flight",
                         "End the last day earlier or drop this stop.", day=dt, item=s_.get("placeId"))
            if a < day_start - 60 or b > day_end + 60:
                rep.fail("timing", f"{dt}: {name} at {s_['arriveAt']}-{s_['departAt']} is outside the day window",
                         "Keep stops between dayStart and dayEnd.", day=dt, item=s_.get("placeId"))
            busy += max(b - a, 0)
            prev_end, prev_place = b, (p if p.get("lat") is not None else prev_place)
        free = (day_end - day_start) - busy
        if stops and free < min_free and dt not in transfer_floor:
            rep.fail("free_time", f"{dt} has about {max(free, 0)} min free (needs {min_free})",
                     "Remove a stop or shorten visits on this day.", day=dt)
    for name in ("pacing", "timing", "travel_time", "opening_hours", "flight_times", "far_from_base", "free_time",
                 "day_trip"):
        rep.ok(name)

    checks = list(rep.checks.values())
    return {"ok": True, "passed": all(c["passed"] for c in checks), "checks": checks,
            "violations": rep.violations, "totals": totals}


def lambda_handler(event, context):
    if isinstance(event, str):
        event = json.loads(event)
    event = event or {}
    spec, it = event.get("tripSpec"), event.get("itinerary")
    if not isinstance(spec, dict) or not isinstance(it, dict):
        return _err("bad_input", "need tripSpec and itinerary objects")
    try:
        start, end = date.fromisoformat(spec["startDate"]), date.fromisoformat(spec["endDate"])
        float(spec["budgetSGD"])
    except (KeyError, ValueError, TypeError):
        return _err("bad_input", "tripSpec needs startDate, endDate (YYYY-MM-DD) and budgetSGD")
    if end < start:
        return _err("bad_input", "endDate is before startDate")

    ids = [x for x in (it.get("flightOfferId"), it.get("stayId")) if x]
    ids += [st.get("stayId") for st in it.get("stays") or [] if st.get("stayId")]
    ids += [t.get("transferId") for t in it.get("transfers") or [] if t.get("transferId")]
    ids += [s.get("placeId") for d in it.get("days") or [] for s in d.get("stops") or [] if s.get("placeId")]
    pool = _pool_get(ids)
    daily = float(os.environ.get("DAILY_SPEND_SGD_PER_PERSON", "100"))
    result = verify(spec, it, pool, daily)
    result["checkedAt"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    return result

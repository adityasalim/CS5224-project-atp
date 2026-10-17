"""
tp-route-matrix — Lambda tool: travel time in minutes between every pair of points,
using Amazon Location Service (Routes), with a distance-based estimate as fallback.

Environment variables:
  CACHE_TABLE  DynamoDB cache table (e.g. tp-dev-api-cache)
  MOCK_MODE    "true" = always use the distance estimate, no Location calls (default "true")
  CACHE_TTL_SECONDS  default 604800 (7 days)

Input event:
  {"points": [{"id": "hotel", "lat": 35.68, "lng": 139.76},
              {"id": "osm:way/123", "lat": 35.71, "lng": 139.79}],
   "mode": "walk"}          # "walk" or "car"

Output: {"ok": true, "ids": [...], "minutes": [[0, 23], [24, 0]], "source": "amazon-location" | "estimate"}
Public transit is not modelled; the estimate uses walking or taxi speeds. State this in the report.
"""
import hashlib
import json
import math
import os
import time
from decimal import Decimal

import boto3

MAX_POINTS = 25           # keeps Location calls small; larger sets use the estimate
WALK_KMH, WALK_DETOUR = 4.5, 1.3
CAR_KMH, CAR_DETOUR, CAR_OVERHEAD_MIN = 22.0, 1.4, 5  # city traffic, plus pickup time

_dynamodb = boto3.resource("dynamodb")


def _err(code, message, **extra):
    return {"ok": False, "error": code, "message": message, "source": "route-matrix", **extra}


def _km(a, b):
    r = 6371.0
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lng"] - a["lng"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def estimate_minutes(a, b, mode):
    """Shared formula (sequence_day and verify_itinerary use the same numbers)."""
    km = _km(a, b)
    if km < 0.01:
        return 0
    if mode == "car":
        return math.ceil(km * CAR_DETOUR / CAR_KMH * 60 + CAR_OVERHEAD_MIN)
    return math.ceil(km * WALK_DETOUR / WALK_KMH * 60)


def _estimate(points, mode):
    return [[estimate_minutes(a, b, mode) for b in points] for a in points]


def _location(points, mode):
    client = boto3.client("geo-routes")
    pos = [{"Position": [p["lng"], p["lat"]]} for p in points]
    resp = client.calculate_route_matrix(
        Origins=pos, Destinations=pos,
        TravelMode="Car" if mode == "car" else "Pedestrian",
        RoutingBoundary={"Unbounded": True})
    est = _estimate(points, mode)
    out, filled = [], 0
    for i, row in enumerate(resp["RouteMatrix"]):
        out_row = []
        for j, cell in enumerate(row):
            if i == j:
                out_row.append(0)
            elif cell.get("Error") or cell.get("Duration") is None:
                out_row.append(est[i][j])   # fill gaps with the estimate
                filled += 1
            else:
                out_row.append(math.ceil(cell["Duration"] / 60) + (CAR_OVERHEAD_MIN if mode == "car" else 0))
        out.append(out_row)
    return out, filled


def _validate(event):
    pts = event.get("points") or []
    if not 2 <= len(pts) <= 60:
        return None, _err("bad_input", "points must have 2-60 entries")
    clean = []
    for i, p in enumerate(pts):
        try:
            lat, lng = float(p["lat"]), float(p["lng"])
        except (KeyError, TypeError, ValueError):
            return None, _err("bad_input", f"points[{i}] needs numeric lat and lng")
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return None, _err("bad_input", f"points[{i}] has out-of-range coordinates")
        clean.append({"id": str(p.get("id", i)), "lat": round(lat, 5), "lng": round(lng, 5)})
    mode = str(event.get("mode", "walk")).lower()
    if mode not in ("walk", "car"):
        return None, _err("bad_input", "mode must be 'walk' or 'car'")
    return {"points": clean, "mode": mode}, None


def _cache_get(key):
    table = os.environ.get("CACHE_TABLE")
    if not table:
        return None
    item = _dynamodb.Table(table).get_item(Key={"cacheKey": key}).get("Item")
    if item and int(item.get("expiresAt", 0)) > time.time():
        return json.loads(item["payload"])
    return None


def _cache_put(key, payload):
    table = os.environ.get("CACHE_TABLE")
    if not table:
        return
    _dynamodb.Table(table).put_item(Item={
        "cacheKey": key, "payload": json.dumps(payload), "tool": "route_matrix",
        "expiresAt": Decimal(int(time.time()) + int(os.environ.get("CACHE_TTL_SECONDS", "604800")))})


def lambda_handler(event, context):
    if isinstance(event, str):
        event = json.loads(event)
    q, error = _validate(event or {})
    if error:
        return error
    pts, mode = q["points"], q["mode"]
    ids = [p["id"] for p in pts]

    if os.environ.get("MOCK_MODE", "true").lower() == "true" or len(pts) > MAX_POINTS:
        reason = "mock mode" if len(pts) <= MAX_POINTS else f"more than {MAX_POINTS} points"
        return {"ok": True, "source": "estimate", "reason": reason, "mode": mode, "ids": ids,
                "minutes": _estimate(pts, mode)}

    key = "route:" + hashlib.sha256(json.dumps(q, sort_keys=True).encode()).hexdigest()[:32]
    cached = _cache_get(key)
    if cached:
        return {**cached, "source": cached["source"] + "-cache"}

    try:
        minutes, filled = _location(pts, mode)
        result = {"ok": True, "source": "amazon-location", "mode": mode, "ids": ids, "minutes": minutes,
                  "cellsEstimated": filled}
    except Exception as e:  # noqa: BLE001 — never fail the plan because of routing
        print("location failed:", e)
        result = {"ok": True, "source": "estimate", "reason": f"location error: {str(e)[:150]}",
                  "mode": mode, "ids": ids, "minutes": _estimate(pts, mode)}
        return result  # don't cache errors
    _cache_put(key, result)
    return result

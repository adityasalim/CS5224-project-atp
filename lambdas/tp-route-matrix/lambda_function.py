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

Output: {"ok": true, "ids": [...], "minutes": [[0, 23], [24, 0]], "source": "amazon-location" | "estimate",
         "cellsEstimated": n, "cellsTotal": n, "estimatedShare": 0.0-1.0, "requests": n}
Large matrices are requested in blocks of 10 x 10 (Amazon Location allows at most 15 origins and 100
origin-destination pairs per request with an unbounded routing boundary). A block that fails is filled with
the estimate. The cache ignores the order of the points. Public transit is not modelled (Amazon Location
offers Car, Pedestrian, Scooter and Truck only); the estimate uses walking or taxi speeds. State this in the report.
"""
import hashlib
import json
import math
import os
import time
from decimal import Decimal

import boto3

MAX_POINTS = 25           # larger sets use the estimate
BLOCK = 10                # origins and destinations per request: 10 x 10 = 100 pairs, the unbounded-boundary limit
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
    """Route matrix from Amazon Location in blocks of BLOCK x BLOCK. A block that fails is filled with the
    estimate. Returns (minutes, cells filled with the estimate, requests made, error messages)."""
    client = boto3.client("geo-routes")
    n = len(points)
    pos = [{"Position": [p["lng"], p["lat"]]} for p in points]
    est = _estimate(points, mode)
    out = [[0] * n for _ in range(n)]
    filled, requests_made, errors = 0, 0, []
    for r0 in range(0, n, BLOCK):
        for c0 in range(0, n, BLOCK):
            rows, cols = pos[r0:r0 + BLOCK], pos[c0:c0 + BLOCK]
            try:
                resp = client.calculate_route_matrix(
                    Origins=rows, Destinations=cols,
                    TravelMode="Car" if mode == "car" else "Pedestrian",
                    RoutingBoundary={"Unbounded": True})
                requests_made += 1
                matrix = resp["RouteMatrix"]
            except Exception as e:  # noqa: BLE001 — fill this block with the estimate
                errors.append(f"{type(e).__name__}: {str(e)[:120]}")
                matrix = None
            for ri in range(len(rows)):
                for ci in range(len(cols)):
                    i, j = r0 + ri, c0 + ci
                    if i == j:
                        out[i][j] = 0
                        continue
                    try:   # a short row, a cell that is not an object or a Duration that is not a number: estimate
                        cell = matrix[ri][ci] if matrix else None
                        seconds = float(cell["Duration"]) if cell and not cell.get("Error") else None
                    except (IndexError, KeyError, TypeError, ValueError, AttributeError):
                        seconds = None
                    if seconds is None:
                        out[i][j] = est[i][j]   # fill gaps with the estimate
                        filled += 1
                    else:
                        out[i][j] = math.ceil(seconds / 60) + (CAR_OVERHEAD_MIN if mode == "car" else 0)
    return out, filled, requests_made, errors


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


def _canonical_order(points):
    """Original indexes sorted by (id, lat, lng): the same set of points always gives the same cache key,
    whatever order the caller lists them in."""
    return sorted(range(len(points)), key=lambda i: (points[i]["id"], points[i]["lat"], points[i]["lng"], i))


def _reorder(matrix, order):
    """Matrix computed for the canonical order -> matrix for the caller's original order."""
    pos = {orig: k for k, orig in enumerate(order)}
    n = len(order)
    return [[matrix[pos[i]][pos[j]] for j in range(n)] for i in range(n)]


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
    """The tool; a request it cannot read is a bad_input error, never an exception."""
    try:
        return _handle(event)
    except (TypeError, ValueError, KeyError, AttributeError, IndexError) as e:
        return _err("bad_input", f"could not read the request: {type(e).__name__}: {str(e)[:200]}")


def _handle(event):
    if isinstance(event, str):
        event = json.loads(event)
    q, error = _validate(event or {})
    if error:
        return error
    pts, mode = q["points"], q["mode"]
    ids = [p["id"] for p in pts]
    n = len(pts)

    if os.environ.get("MOCK_MODE", "true").lower() == "true" or n > MAX_POINTS:
        reason = "mock mode" if n <= MAX_POINTS else f"more than {MAX_POINTS} points"
        return {"ok": True, "source": "estimate", "reason": reason, "mode": mode, "ids": ids,
                "minutes": _estimate(pts, mode)}

    order = _canonical_order(pts)
    cpts = [pts[i] for i in order]
    key = "route-v2:" + hashlib.sha256(json.dumps({"points": cpts, "mode": mode}, sort_keys=True).encode()).hexdigest()[:32]
    cached = _cache_get(key)
    if cached:
        return {"ok": True, "source": cached["source"] + "-cache", "mode": mode, "ids": ids,
                "minutes": _reorder(cached["minutes"], order), "cellsEstimated": cached.get("cellsEstimated", 0),
                "cellsTotal": cached.get("cellsTotal", n * (n - 1)),
                "estimatedShare": cached.get("estimatedShare", 0.0)}

    minutes, filled, requests_made, errors = _location(cpts, mode)
    total = n * (n - 1)
    if requests_made == 0:     # nothing worked: say why and use the estimate for everything
        print("location failed:", errors[:3])
        return {"ok": True, "source": "estimate", "reason": f"location error: {'; '.join(errors[:2])[:200]}",
                "mode": mode, "ids": ids, "minutes": _estimate(pts, mode)}
    share = round(filled / total, 3) if total else 0.0
    result = {"ok": True, "source": "amazon-location", "mode": mode, "minutes": minutes,
              "cellsEstimated": filled, "cellsTotal": total, "estimatedShare": share}
    if errors:                 # some blocks failed: don't cache a partly estimated matrix
        print("location blocks failed:", errors[:3])
        result["blocksFailed"] = len(errors)
    else:
        _cache_put(key, result)
    return {**result, "ids": ids, "minutes": _reorder(minutes, order), "requests": requests_made}

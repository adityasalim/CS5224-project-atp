"""
tp-api-trips — REST API backend (behind API Gateway + Cognito authorizer) for trip history,
PDF links, feedback and stated preferences.

Routes (API Gateway REST API, Lambda proxy integration):
  GET  /trips                       list my trips, newest first
  GET  /trips/{tripId}              one trip + its latest itinerary
  GET  /trips/{tripId}/pdf          fresh 15-minute download link for the latest PDF
                                    (?version=N for a specific version)
  POST /trips/{tripId}/feedback     {"rating": 1-5, "comments": "...", "version": 2, "swaps": [...],
                                     optional 1-5: usefulness, personalization, trust, easeOfUse, satisfaction}
  GET  /me/preferences              my stated preferences
  PUT  /me/preferences              {"homeCity": "...", "homeAirport": "SIN", ...}

The user is ALWAYS taken from the Cognito token (requestContext.authorizer.claims.sub),
never from the request body, so nobody can read another user's trips.

Environment variables:
  TRIPS_TABLE        tp-dev-trips        (PK userId, SK tripId)
  ITINERARIES_TABLE  tp-dev-itineraries  (PK tripId, SK version)
  FEEDBACK_TABLE     tp-dev-feedback     (PK tripId, SK createdAt)
  USERS_TABLE        tp-dev-users        (PK userId)
  PLANS_BUCKET       tp-dev-plans-<suffix>
  ALLOWED_ORIGINS    comma-separated, e.g. "https://dxxxx.cloudfront.net,http://localhost:5173"
"""
import json
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

_dynamodb = boto3.resource("dynamodb")
_s3 = None
PREF_FIELDS = {
    "homeCity": str, "homeAirport": str, "budgetStyle": str, "pace": str,
    "dietary": list, "interests": list, "notes": str,
}
MAX_TEXT = 1000


# ---------------------------------------------------------------- response helpers
class _Enc(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return int(o) if o == o.to_integral_value() else float(o)
        return super().default(o)


def _origin(event):
    allowed = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
    req = (event.get("headers") or {}).get("origin") or (event.get("headers") or {}).get("Origin")
    if req and req in allowed:
        return req
    return allowed[0] if allowed else "*"


def _resp(event, status, body):
    return {"statusCode": status,
            "headers": {"Access-Control-Allow-Origin": _origin(event),
                        "Access-Control-Allow-Headers": "Authorization,Content-Type",
                        "Access-Control-Allow-Methods": "GET,POST,PUT,OPTIONS",
                        "Content-Type": "application/json"},
            "body": json.dumps(body, cls=_Enc)}


def _error(event, status, code, message):
    return _resp(event, status, {"error": code, "message": message})


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _table(env):
    return _dynamodb.Table(os.environ[env])


def _body(event):
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode()
    return json.loads(raw)


# ---------------------------------------------------------------- data helpers
def _get_trip(user_id, trip_id):
    return _table("TRIPS_TABLE").get_item(Key={"userId": user_id, "tripId": trip_id}).get("Item")


def _latest_itinerary(trip_id):
    items = _table("ITINERARIES_TABLE").query(KeyConditionExpression=Key("tripId").eq(trip_id)).get("Items", [])
    if not items:
        return None

    def ver(i):  # versions are stored as strings ("1", "2", ..., "10"); compare as numbers
        try:
            return int(i.get("version", 0))
        except (TypeError, ValueError):
            return 0
    return max(items, key=ver)


def _presign(key):
    global _s3
    if _s3 is None:
        region = os.environ.get("AWS_REGION", "ap-southeast-1")
        _s3 = boto3.client("s3", region_name=region, endpoint_url=f"https://s3.{region}.amazonaws.com",
                           config=Config(signature_version="s3v4"))
    return _s3.generate_presigned_url("get_object", Params={"Bucket": os.environ["PLANS_BUCKET"], "Key": key},
                                      ExpiresIn=900)


# ---------------------------------------------------------------- route handlers
def list_trips(event, user_id):
    resp = _table("TRIPS_TABLE").query(KeyConditionExpression=Key("userId").eq(user_id),
                                       ScanIndexForward=False, Limit=50)
    trips = [{k: t.get(k) for k in ("tripId", "title", "destination", "startDate", "endDate", "status",
                                     "createdAt", "budgetSGD", "budget", "currency")} for t in resp.get("Items", [])]
    trips.sort(key=lambda t: t.get("createdAt") or "", reverse=True)
    return _resp(event, 200, {"trips": trips})


def get_trip(event, user_id, trip_id):
    trip = _get_trip(user_id, trip_id)
    if not trip:
        return _error(event, 404, "not_found", "trip not found")
    itin = _latest_itinerary(trip_id)
    return _resp(event, 200, {"trip": trip, "itinerary": itin})


def get_pdf(event, user_id, trip_id):
    if not _get_trip(user_id, trip_id):
        return _error(event, 404, "not_found", "trip not found")
    version = str((event.get("queryStringParameters") or {}).get("version") or "").strip()
    if version.isdigit():
        itin = _table("ITINERARIES_TABLE").get_item(Key={"tripId": trip_id, "version": version}).get("Item")
    else:
        itin = _latest_itinerary(trip_id)
    if not itin or not itin.get("pdfKey"):
        return _error(event, 404, "no_pdf", "no PDF has been generated for this trip yet")
    return _resp(event, 200, {"url": _presign(itin["pdfKey"]), "expiresInSeconds": 900})


def post_feedback(event, user_id, trip_id):
    if not _get_trip(user_id, trip_id):
        return _error(event, 404, "not_found", "trip not found")
    try:
        body = _body(event)
        rating = int(body.get("rating"))
    except (ValueError, TypeError, json.JSONDecodeError):
        return _error(event, 400, "bad_input", "rating must be a whole number from 1 to 5")
    if not 1 <= rating <= 5:
        return _error(event, 400, "bad_input", "rating must be a whole number from 1 to 5")
    swaps = body.get("swaps") or []
    if not isinstance(swaps, list) or len(swaps) > 50:
        return _error(event, 400, "bad_input", "swaps must be a list (max 50)")
    item = {"tripId": trip_id, "createdAt": _now(), "userId": user_id, "rating": rating,
            "comments": str(body.get("comments", ""))[:MAX_TEXT], "swaps": json.dumps(swaps)[:5000]}
    if isinstance(body.get("version"), int) and not isinstance(body.get("version"), bool):
        item["version"] = body["version"]          # which itinerary version was rated
    for dim in ("easeOfUse", "usefulness", "personalization", "trust", "satisfaction"):  # §5.2 user study
        if body.get(dim) is not None:
            try:
                v = int(body[dim])
            except (TypeError, ValueError):
                return _error(event, 400, "bad_input", f"{dim} must be 1-5")
            if not 1 <= v <= 5:
                return _error(event, 400, "bad_input", f"{dim} must be 1-5")
            item[dim] = v
    _table("FEEDBACK_TABLE").put_item(Item=item)
    return _resp(event, 201, {"saved": True, "createdAt": item["createdAt"]})


def get_prefs(event, user_id):
    item = _table("USERS_TABLE").get_item(Key={"userId": user_id}).get("Item") or {"userId": user_id}
    return _resp(event, 200, {"preferences": item})


def put_prefs(event, user_id):
    try:
        body = _body(event)
    except json.JSONDecodeError:
        return _error(event, 400, "bad_input", "body must be JSON")
    updates = {}
    for field, typ in PREF_FIELDS.items():
        if field not in body:
            continue
        val = body[field]
        if typ is str:
            updates[field] = str(val)[:MAX_TEXT]
        elif isinstance(val, list):
            updates[field] = [str(v)[:100] for v in val][:30]
        else:
            return _error(event, 400, "bad_input", f"{field} must be a list")
    unknown = set(body) - set(PREF_FIELDS)
    if not updates:
        return _error(event, 400, "bad_input", f"no recognised fields; use {sorted(PREF_FIELDS)}")
    updates["updatedAt"] = _now()
    expr = "SET " + ", ".join(f"#{k} = :{k}" for k in updates)
    _table("USERS_TABLE").update_item(
        Key={"userId": user_id}, UpdateExpression=expr,
        ExpressionAttributeNames={f"#{k}": k for k in updates},
        ExpressionAttributeValues={f":{k}": v for k, v in updates.items()})
    return _resp(event, 200, {"saved": sorted(updates), "ignored": sorted(unknown)})


# ---------------------------------------------------------------- entry point
def lambda_handler(event, context):
    method = event.get("httpMethod", "")
    resource = event.get("resource", "")
    if method == "OPTIONS":
        return _resp(event, 204, {})

    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
    user_id = claims.get("sub")
    if not user_id:
        return _error(event, 401, "unauthorized", "missing or invalid sign-in token")
    trip_id = (event.get("pathParameters") or {}).get("tripId")

    try:
        if resource == "/trips" and method == "GET":
            return list_trips(event, user_id)
        if resource == "/trips/{tripId}" and method == "GET":
            return get_trip(event, user_id, trip_id)
        if resource == "/trips/{tripId}/pdf" and method == "GET":
            return get_pdf(event, user_id, trip_id)
        if resource == "/trips/{tripId}/feedback" and method == "POST":
            return post_feedback(event, user_id, trip_id)
        if resource == "/me/preferences" and method == "GET":
            return get_prefs(event, user_id)
        if resource == "/me/preferences" and method == "PUT":
            return put_prefs(event, user_id)
        return _error(event, 404, "no_route", f"{method} {resource} is not a known route")
    except KeyError as e:  # usually a missing environment variable
        print("config error:", repr(e))
        return _error(event, 500, "config", f"server is missing setting {e}")
    except Exception as e:  # noqa: BLE001 — log it, don't leak internals to the browser
        print("unhandled error:", repr(e))
        return _error(event, 500, "server_error", "something went wrong; check CloudWatch logs")

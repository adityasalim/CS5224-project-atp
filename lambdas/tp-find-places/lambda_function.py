"""
tp-find-places — Lambda tool: find attractions, restaurants and cafes near a city or point
using OpenStreetMap Overpass (cached), with Google Places as a fallback.

Fallback chain: Overpass (main, then mirror) -> Amazon Location Places -> Google Places
(if GOOGLE_MAPS_KEY in secret) -> mock. The whole chain is bounded by TIME_BUDGET_SECONDS (default 40),
so the tool always answers before the 60 s Lambda timeout.

Environment variables:
  SECRET_ARN   secret with optional {"GOOGLE_MAPS_KEY": "..."}
  CACHE_TABLE  DynamoDB cache table (e.g. tp-dev-api-cache)
  MOCK_MODE    "true" = sample data only (default "true")
  CONTACT_EMAIL  optional; added to the User-Agent as Overpass etiquette asks
  CACHE_TTL_SECONDS  default 604800 (7 days);  POOL_TTL_SECONDS  default 86400 (24 h)

Input event:
  {"city": "Tokyo", "categories": ["temple", "museum", "restaurant", "climbing", "hiking"],
   "keywords": ["bouldering gym", "day hike"], "radiusKm": 4, "cuisine": "japanese", "maxPerCategory": 8}
  ("lat" and "lng" can be given instead of "city".)
  - categories: fixed place types (see CATEGORY_TAGS). Unknown words are treated as keywords.
  - keywords: free-text activities for anything else; searched with Amazon Location / Google text search
    and returned with category "activity" and "activity": <keyword>.
  - radius: city categories use up to 15 km, food 1.5 km, outdoor categories and keywords up to 50 km
    (good for day trips such as hiking outside the city).
  Personalisation (all optional):
  - vibe: "classic" (famous sights first), "balanced" (default, mix), "hidden_gems" (lesser-known local places first)
  - dietary: ["vegetarian", "vegan", "halal", "kosher", "gluten_free"] -> food places tagged with it first,
    plus a wider search for them
  - accessibility: ["wheelchair"] -> drops places marked not wheelchair accessible, accessible ones first
  - audience: "family" (kid-friendly first, no bars/nightlife) | "adults" | "any"
  - indoorOnly: true (rainy days, heat) -> only indoor kinds of places
  - freeOnly: true -> drops places known to charge entry, free ones first
  - avoid: words to exclude, matched against name, category, cuisine (e.g. "casino", "chain", "seafood")
  - names: ["Ichiran Shibuya", "teamLab Planets"] -> look up specific places the user mentioned by name
    (up to 6). Each found one is returned first with "requested": <name> and tag "requested by user"; it is
    never filtered out by the preferences above. Names that cannot be found are listed in "notFound".
  Each place returns "tags" (e.g. "wheelchair", "vegetarian", "free entry", "indoor", "family", "well-known",
  "local pick"), "matches" (which requested preferences it meets) and a short "description" when known.
"""
import hashlib
import json
import math
import os
import re
import time
from decimal import Decimal
from urllib.parse import quote

import boto3
import requests  # from the tp-deps layer
from botocore.config import Config

# Whole-tool time budget (seconds). Keep well under the Lambda timeout (60 s) so the tool
# always answers, falling back to the next source instead of timing out.
TIME_BUDGET_S = float(os.environ.get("TIME_BUDGET_SECONDS", "40"))
OVERPASS_TIMEOUT_S = 18          # per Overpass request
OVERPASS_SHARE_S = 24            # Overpass may use at most this much of the budget
FOOD_RADIUS_KM = 1.5             # restaurants/cafes/bars are dense in cities; search closer in
_GEO_CFG = Config(connect_timeout=4, read_timeout=8, retries={"max_attempts": 2})

OVERPASS_URLS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
CATEGORY_TAGS = {
    "temple": ['["amenity"="place_of_worship"]["religion"="buddhist"]'],
    "shrine": ['["amenity"="place_of_worship"]["religion"="shinto"]'],
    "church": ['["amenity"="place_of_worship"]["religion"="christian"]'],
    "museum": ['["tourism"="museum"]'],
    "gallery": ['["tourism"="gallery"]'],
    "attraction": ['["tourism"="attraction"]'],
    "viewpoint": ['["tourism"="viewpoint"]'],
    "park": ['["leisure"="park"]'],
    "garden": ['["leisure"="garden"]'],
    "zoo": ['["tourism"="zoo"]', '["tourism"="aquarium"]'],
    "market": ['["amenity"="marketplace"]'],
    "shopping": ['["shop"="mall"]', '["shop"="department_store"]'],
    "restaurant": ['["amenity"="restaurant"]'],
    "cafe": ['["amenity"="cafe"]'],
    "bar": ['["amenity"="bar"]'],
    "climbing": ['["sport"="climbing"]'],
    "hiking": ['["route"="hiking"]', '["natural"="peak"]'],
    "nature": ['["leisure"="nature_reserve"]'],
    "onsen": ['["amenity"="public_bath"]["bath:type"="onsen"]', '["natural"="hot_spring"]'],
    "beach": ['["natural"="beach"]'],
    "aquarium": ['["tourism"="aquarium"]'],
    "theme_park": ['["tourism"="theme_park"]'],
    "historic": ['["historic"="castle"]', '["historic"="monument"]', '["historic"="ruins"]',
                 '["historic"="archaeological_site"]'],
    "nightlife": ['["amenity"="nightclub"]', '["amenity"="pub"]'],
    "dessert": ['["amenity"="ice_cream"]', '["shop"="pastry"]'],
    "street_food": ['["amenity"="food_court"]'],
    "spa": ['["leisure"="sauna"]', '["shop"="massage"]'],
    "bookshop": ['["shop"="books"]'],
    "playground": ['["leisure"="playground"]'],
}
FOOD = {"restaurant", "cafe", "bar", "dessert", "street_food"}
INDOOR = {"museum", "gallery", "shopping", "aquarium", "restaurant", "cafe", "bar", "nightlife", "dessert",
          "street_food", "spa", "bookshop", "onsen", "climbing", "temple", "shrine", "church"}
FAMILY = {"zoo", "aquarium", "theme_park", "park", "playground", "museum", "beach", "garden", "dessert"}
ADULT_ONLY = {"bar", "nightlife"}
VIBES = {"classic", "balanced", "hidden_gems"}
DIETS = {"vegetarian", "vegan", "halal", "kosher", "gluten_free"}
DIET_TAG = {"vegetarian": "diet:vegetarian", "vegan": "diet:vegan", "halal": "diet:halal", "kosher": "diet:kosher",
            "gluten_free": "diet:gluten_free"}
OUTDOOR = {"climbing", "hiking", "nature", "onsen", "beach"}
CITY_MAX_KM, OUTDOOR_MAX_KM = 15, 50
KNOWN_CITIES = {"tokyo": (35.6812, 139.7671), "kyoto": (35.0116, 135.7681), "osaka": (34.6937, 135.5023),
                "singapore": (1.2903, 103.8520), "seoul": (37.5665, 126.9780), "bangkok": (13.7563, 100.5018),
                "bali": (-8.6500, 115.2167), "hong kong": (22.3193, 114.1694), "taipei": (25.0330, 121.5654),
                # Scandinavia (countries map to their capital)
                "copenhagen": (55.6761, 12.5683), "denmark": (55.6761, 12.5683),
                "oslo": (59.9139, 10.7522), "norway": (59.9139, 10.7522), "bergen": (60.3913, 5.3221),
                "tromso": (69.6492, 18.9553), "tromsø": (69.6492, 18.9553),
                "lofoten": (68.2342, 14.5683), "lofoten islands": (68.2342, 14.5683), "svolvaer": (68.2342, 14.5683),
                "svolvær": (68.2342, 14.5683), "reine": (67.9324, 13.0896),
                "stockholm": (59.3293, 18.0686), "sweden": (59.3293, 18.0686),
                "abisko": (68.3495, 18.8312), "kiruna": (67.8558, 20.2253),
                # popular day-trip towns
                "nikko": (36.7199, 139.6982), "kamakura": (35.3192, 139.5467), "hakone": (35.2324, 139.1069),
                "nara": (34.6851, 135.8048), "kobe": (34.6901, 135.1955), "busan": (35.1796, 129.0756),
                "ayutthaya": (14.3532, 100.5689), "sapporo": (43.0618, 141.3545), "fukuoka": (33.5902, 130.4017)}

_secret_cache = None
_dynamodb = boto3.resource("dynamodb")


# ---------------------------------------------------------------- helpers
def _err(code, message, **extra):
    return {"ok": False, "error": code, "message": message, "source": "places", **extra}


def _secret(name):
    global _secret_cache
    if _secret_cache is None:
        if not os.environ.get("SECRET_ARN"):
            return None
        sm = boto3.client("secretsmanager")
        _secret_cache = json.loads(sm.get_secret_value(SecretId=os.environ["SECRET_ARN"])["SecretString"])
    return _secret_cache.get(name)


def _mock_mode():
    return os.environ.get("MOCK_MODE", "true").lower() == "true"


def _geocode(city):
    parts = [x.strip().lower() for x in city.split(",") if x.strip()]
    known = KNOWN_CITIES.get(city.strip().lower()) or (KNOWN_CITIES.get(parts[-1]) if parts else None)
    if _mock_mode():
        return known
    try:
        r = boto3.client("geo-places", config=_GEO_CFG).geocode(QueryText=city, MaxResults=1)
        items = r.get("ResultItems", [])
        if items:
            lng, lat = items[0]["Position"]
            return lat, lng
    except Exception as e:  # noqa: BLE001
        print("geocode failed:", e)
    return known


def _km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _simple_hours(raw):
    """Very rough parse of an OSM opening_hours string into one daily open/close window.
    Returns None when unknown. Good enough for sequencing; the raw string is kept too."""
    if not raw:
        return None
    if raw.strip() == "24/7":
        return {"open": "00:00", "close": "23:59"}
    m = re.search(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", raw)
    if not m:
        return None
    oh, om, ch, cm = map(int, m.groups())
    if ch >= 24 or (ch * 60 + cm) <= (oh * 60 + om):  # past midnight -> clamp to end of day
        ch, cm = 23, 59
    return {"open": f"{oh:02d}:{om:02d}", "close": f"{ch:02d}:{cm:02d}"}


def _validate(event):
    raw = [str(c).lower().strip() for c in (event.get("categories") or []) if str(c).strip()]
    cats = [c for c in raw if c in CATEGORY_TAGS]
    # Anything that isn't a known category becomes a free-text keyword ("bouldering", "kayaking", ...)
    kws = [c for c in raw if c not in CATEGORY_TAGS] + [str(k).strip() for k in (event.get("keywords") or [])]
    kws = list(dict.fromkeys(re.sub(r"[^\w\s&'-]", "", k)[:40].strip().lower() for k in kws if k))[:5]
    kws = [k for k in kws if k]
    names = list(dict.fromkeys(re.sub(r"[^\w\s&'.,()-]", "", str(n))[:60].strip()
                               for n in (event.get("names") or []) if str(n).strip()))[:6]
    names = [n for n in names if n]
    if not cats and not kws and not names:
        cats = ["attraction", "museum", "restaurant"]
    if event.get("lat") is not None and event.get("lng") is not None:
        lat, lng = float(event["lat"]), float(event["lng"])
    elif event.get("city"):
        coords = _geocode(str(event["city"]))
        if not coords:
            return None, _err("unknown_location", f"could not find '{event['city']}'; pass lat and lng")
        lat, lng = coords
    else:
        return None, _err("bad_input", "give either city, or lat and lng")
    cuisine = re.sub(r"[^a-z_|]", "", str(event.get("cuisine", "")).lower())

    def words(v, allowed=None, n=8):
        items = v if isinstance(v, list) else [v] if v else []
        out = [re.sub(r"[^\w\s-]", "", str(x)).strip().lower().replace("-", "_").replace(" ", "_")
               for x in items if str(x).strip()]
        return sorted({x for x in out if x and (allowed is None or x in allowed)})[:n]

    vibe = str(event.get("vibe") or "balanced").lower().replace(" ", "_").replace("-", "_")
    vibe = {"famous": "classic", "popular": "classic", "local": "hidden_gems", "off_the_beaten_path": "hidden_gems",
            "hidden": "hidden_gems", "offbeat": "hidden_gems"}.get(vibe, vibe)
    audience = str(event.get("audience") or "any").lower()
    audience = {"kids": "family", "children": "family", "family_friendly": "family"}.get(audience, audience)
    prefs = {"vibe": vibe if vibe in VIBES else "balanced",
             "dietary": words(event.get("dietary"), DIETS),
             "accessibility": ["wheelchair"] if words(event.get("accessibility")) else [],
             "audience": audience if audience in ("family", "adults") else "any",
             "indoorOnly": bool(event.get("indoorOnly")), "freeOnly": bool(event.get("freeOnly")),
             "avoid": [a.replace("_", " ") for a in words(event.get("avoid"))]}
    cats = sorted(set(cats))
    if prefs["indoorOnly"]:
        cats = [c for c in cats if c in INDOOR] or ["museum", "gallery", "shopping", "aquarium"]
    if prefs["audience"] == "family":
        cats = [c for c in cats if c not in ADULT_ONLY]
    if prefs["dietary"] and any(c in ("restaurant", "cafe") for c in cats):
        # text search providers find diet-specific places by name/description
        kws = (kws + [f'{prefs["dietary"][0].replace("_", " ")} restaurant'])[:5]
    return {"lat": round(lat, 4), "lng": round(lng, 4), "categories": cats, "keywords": kws,
            "radiusKm": min(max(float(event.get("radiusKm", 4)), 0.5), OUTDOOR_MAX_KM), "cuisine": cuisine or None,
            "maxPerCategory": min(max(int(event.get("maxPerCategory", 8)), 1), 20), "prefs": prefs,
            "names": names}, None


def _radius_km(cat, q):
    """Search radius per category: food close by, city sights within 15 km, outdoors/keywords up to 50 km."""
    if cat in FOOD:
        return min(q["radiusKm"], FOOD_RADIUS_KM)
    if cat in ("hiking", "nature"):
        return min(max(q["radiusKm"], 40.0), OUTDOOR_MAX_KM)     # trails are usually outside the city
    if cat in OUTDOOR or cat.startswith("kw:"):
        return min(max(q["radiusKm"], 20.0), OUTDOOR_MAX_KM)
    return min(q["radiusKm"], CITY_MAX_KM)


def _cache_key(q):
    return "places:" + hashlib.sha256(json.dumps(q, sort_keys=True).encode()).hexdigest()[:32]


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
        "cacheKey": key, "payload": json.dumps(payload), "tool": "find_places",
        "expiresAt": Decimal(int(time.time()) + int(os.environ.get("CACHE_TTL_SECONDS", "604800")))})


def _pool_put(places):
    table = os.environ.get("CACHE_TABLE")
    if not table or not places:
        return
    exp = Decimal(int(time.time()) + int(os.environ.get("POOL_TTL_SECONDS", "86400")))
    with _dynamodb.Table(table).batch_writer(overwrite_by_pkeys=["cacheKey"]) as bw:
        for p in places:
            bw.put_item(Item={"cacheKey": "pool:" + p["placeId"],
                              "payload": json.dumps({**p, "kind": "place", "id": p["placeId"]}),
                              "expiresAt": exp, "tool": "find_places"})


def _place(pid, name, cat, lat, lng, q, hours_raw=None, simple=None, cuisine=None, website=None, activity=None,
           url=None, notable=False, rating=None, osm_tags=None, description=None, fame_known=True):
    """osm_tags: raw OpenStreetMap tags when known; turned into readable "tags" the planner can match on."""
    t = osm_tags or {}
    tags = (["well-known" if notable else "local pick"] if fame_known
            else ["well-known"] if (rating or 0) >= 4.6 else [])
    if cat in INDOOR:
        tags.append("indoor")
    if cat in FAMILY:
        tags.append("family")
    if t.get("wheelchair") in ("yes", "designated"):
        tags.append("wheelchair")
    elif t.get("wheelchair") == "limited":
        tags.append("wheelchair limited")
    elif t.get("wheelchair") == "no":
        tags.append("not wheelchair accessible")
    for diet, key in DIET_TAG.items():
        if t.get(key) in ("yes", "only"):
            tags.append(diet.replace("_", "-") + (" only" if t.get(key) == "only" else ""))
    if t.get("fee") == "no":
        tags.append("free entry")
    elif t.get("fee") == "yes":
        tags.append("paid entry")
    desc = description or t.get("description:en") or t.get("description")
    return {"placeId": pid, "name": name, "category": cat, "activity": activity, "lat": round(lat, 6), "lng": round(lng, 6),
            "distanceKm": round(_km(q["lat"], q["lng"], lat, lng), 2),
            "openingHours": hours_raw, "openSimple": simple if simple is not None else _simple_hours(hours_raw),
            "cuisine": cuisine, "website": website, "notable": notable, "rating": rating,
            "tags": tags, "description": desc[:160] if desc else None,
            "url": url, "mapsUrl": f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"}


# ---------------------------------------------------------------- providers
def _overpass(q, deadline):
    deadline = min(deadline, time.time() + OVERPASS_SHARE_S)   # leave time for the fallbacks
    if not q["categories"]:
        return [], None
    parts = []
    for cat in q["categories"]:
        r = int(_radius_km(cat, q) * 1000)
        for tag in CATEGORY_TAGS[cat]:
            extra = f'["cuisine"~"{q["cuisine"]}",i]' if (q["cuisine"] and cat == "restaurant") else ""
            parts.append(f'nwr{tag}{extra}["name"](around:{r},{q["lat"]},{q["lng"]});')
            if cat in ("restaurant", "cafe"):
                for diet in q.get("prefs", {}).get("dietary", []):   # rarer, so look 4x further out
                    parts.append(f'nwr{tag}["{DIET_TAG[diet]}"~"yes|only"]["name"]'
                                 f'(around:{min(r * 4, 6000)},{q["lat"]},{q["lng"]});')
    query = f'[out:json][timeout:{OVERPASS_TIMEOUT_S - 3}];({"".join(parts)});out center tags 300;'
    ua = "tp-group10-travel-planner/0.1 (academic prototype"
    ua += f"; {os.environ['CONTACT_EMAIL']})" if os.environ.get("CONTACT_EMAIL") else ")"

    last = "not tried"
    for url in OVERPASS_URLS:          # main server, then mirror; no retries on the same server
        left = deadline - time.time()
        if left < 8:
            last = "time budget used up"
            break
        try:
            r = requests.post(url, data={"data": query}, headers={"User-Agent": ua},
                              timeout=(4, min(OVERPASS_TIMEOUT_S, left - 2)))
        except requests.RequestException as e:
            last = f"{type(e).__name__} at {url}"
            continue
        if r.status_code in (429, 502, 503, 504):
            last = f"http {r.status_code} at {url}"
            continue
        if r.status_code >= 400:
            return None, f"overpass http {r.status_code}: {r.text[:200]}"
        try:
            data = r.json()
        except ValueError:
            last = f"non-JSON reply at {url}"   # Overpass returns an HTML error page when overloaded
            continue
        if data.get("remark") and not data.get("elements"):
            last = f"overpass remark: {data['remark'][:120]}"
            continue
        return _parse_overpass(data.get("elements", []), q), None
    return None, f"overpass {last}"


def _category_of(tags, wanted):
    for cat in wanted:
        for tag in CATEGORY_TAGS[cat]:
            pairs = re.findall(r'\["([^"]+)"="([^"]+)"\]', tag)
            if all(tags.get(k) == v for k, v in pairs):
                return cat
    return None


def _parse_overpass(elements, q):
    out, seen = [], set()
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name:en") or tags.get("name")
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lng = el.get("lon") or (el.get("center") or {}).get("lon")
        cat = _category_of(tags, q["categories"])
        if not name or lat is None or not cat:
            continue
        key = (name.lower(), round(lat, 3), round(lng, 3))
        if key in seen:
            continue
        seen.add(key)
        osm = f"{el['type']}/{el['id']}"
        out.append(_place("osm:" + osm, name, cat, lat, lng, q, hours_raw=tags.get("opening_hours"),
                          cuisine=tags.get("cuisine"), website=tags.get("website"),
                          url=f"https://www.openstreetmap.org/{osm}",
                          notable=bool(tags.get("wikidata") or tags.get("wikipedia")), osm_tags=tags))
    return out


# Search words for Amazon Location text search, per category.
LOCATION_QUERY = {"temple": "temple", "shrine": "shrine", "church": "church", "museum": "museum",
                  "gallery": "art gallery", "attraction": "tourist attraction", "viewpoint": "observation deck",
                  "park": "park", "garden": "garden", "zoo": "zoo aquarium", "market": "market",
                  "shopping": "shopping mall", "restaurant": "restaurant", "cafe": "cafe", "bar": "bar",
                  "climbing": "climbing gym", "hiking": "hiking trail", "nature": "nature reserve",
                  "onsen": "onsen hot spring", "beach": "beach", "aquarium": "aquarium",
                  "theme_park": "theme park", "historic": "historic site castle", "nightlife": "nightclub pub",
                  "dessert": "dessert ice cream", "street_food": "food court street food", "spa": "spa sauna",
                  "bookshop": "bookshop", "playground": "playground"}


def _text_targets(q):
    """(category label, search text, radius km, activity keyword) for text-search providers."""
    out = []
    for cat in q["categories"]:
        text = LOCATION_QUERY[cat]
        if q["cuisine"] and cat == "restaurant":
            text = f'{q["cuisine"]} restaurant'
        out.append((cat, text, _radius_km(cat, q), None))
    for kw in q.get("keywords", []):
        out.append(("activity", kw, _radius_km("kw:" + kw, q), kw))
    return out


def _amazon_location(q, deadline):
    """Amazon Location Service Places (search_text inside a circle). Uses the Lambda role
    (geo-places:*), no API key. Opening hours are not requested, so openSimple stays None
    and the verifier skips the hours check for these places."""
    client = boto3.client("geo-places", config=_GEO_CFG)
    out, errors = [], []
    for cat, text, radius_km, kw in _text_targets(q):
        if deadline - time.time() < 4:
            errors.append("time budget used up")
            break
        radius = min(radius_km, 50) * 1000
        try:
            r = client.search_text(QueryText=text, MaxResults=min(q["maxPerCategory"] + 4, 20),
                                   Filter={"Circle": {"Center": [q["lng"], q["lat"]], "Radius": int(radius)}})
        except Exception as e:  # noqa: BLE001
            errors.append(f"{cat}: {type(e).__name__}: {str(e)[:120]}")
            continue
        for it in r.get("ResultItems", []):
            pos = it.get("Position")
            if not pos or not it.get("Title"):
                continue
            lng, lat = pos
            out.append(_place("aloc:" + it["PlaceId"][:200], it["Title"], cat, lat, lng, q, activity=kw, fame_known=False,
                              url=f"https://www.google.com/maps/search/?api=1&query={quote(it['Title'])}%20{lat},{lng}"))
    if not out and errors:
        return None, "amazon location " + "; ".join(errors[:3])
    return out, None


def _google(q, deadline=None):
    key = _secret("GOOGLE_MAPS_KEY")
    if not key:
        return None, "no GOOGLE_MAPS_KEY in secret"
    headers = {"X-Goog-Api-Key": key, "Content-Type": "application/json",
               "X-Goog-FieldMask": "places.id,places.displayName,places.location,places.googleMapsUri,"
                                   "places.regularOpeningHours.weekdayDescriptions,places.rating,places.websiteUri"}
    out = []
    for cat, text, radius_km, kw in _text_targets(q):
        if deadline and deadline - time.time() < 5:
            break
        body = {"textQuery": text, "maxResultCount": q["maxPerCategory"],
                "locationBias": {"circle": {"center": {"latitude": q["lat"], "longitude": q["lng"]},
                                            "radius": min(radius_km, 50) * 1000}}}
        try:
            r = requests.post("https://places.googleapis.com/v1/places:searchText",
                              headers=headers, json=body, timeout=15)
        except requests.RequestException as e:
            return None, f"google {type(e).__name__}"
        if r.status_code >= 400:
            return None, f"google http {r.status_code}: {r.text[:200]}"
        for p in r.json().get("places", []):
            loc = p.get("location", {})
            days = (p.get("regularOpeningHours") or {}).get("weekdayDescriptions") or []
            raw = "; ".join(days) or None
            out.append(_place("gplace:" + p["id"], p.get("displayName", {}).get("text", "Unnamed"), cat,
                              loc.get("latitude"), loc.get("longitude"), q, hours_raw=raw, activity=kw,
                              website=p.get("websiteUri"), url=p.get("googleMapsUri"), rating=p.get("rating"),
                              fame_known=False))
    return out, None


def _mock(q):
    out = []
    tag = f"{q['lat']:.2f},{q['lng']:.2f}"   # different IDs for different cities
    for cat in q["categories"]:
        for i in range(3):
            lat = q["lat"] + 0.01 * math.cos(i + len(cat))
            lng = q["lng"] + 0.01 * math.sin(i + len(cat))
            hours = {"open": "11:00", "close": "22:00"} if cat in FOOD else {"open": "09:00", "close": "17:00"}
            osm = [{"fee": "yes"}, {"wheelchair": "yes", "fee": "no"}, {"fee": "no"}][i] if cat not in FOOD else \
                [{}, {"wheelchair": "yes"}, {}][i]
            if cat in FOOD:
                osm.update([{}, {"diet:vegetarian": "yes"}, {"diet:vegan": "only", "diet:halal": "yes"}][i])
            out.append(_place(f"mock:{tag}:{cat}_{i + 1}", f"Sample {cat.title()} {chr(65 + i)}", cat, lat, lng, q,
                              hours_raw=f'{hours["open"]}-{hours["close"]}', simple=hours, notable=(i == 0),
                              osm_tags=osm, description=f"Sample {cat} description ({'famous' if i == 0 else 'local favourite'})."))
    for j, kw in enumerate(q.get("keywords", [])):
        for i in range(2):
            lat = q["lat"] + 0.05 * math.cos(i + j + 1)
            lng = q["lng"] + 0.05 * math.sin(i + j + 1)
            out.append(_place(f"mock:{tag}:kw{j + 1}_{i + 1}", f"Sample {kw.title()} {chr(65 + i)}", "activity", lat, lng, q,
                              hours_raw="08:00-20:00", simple={"open": "08:00", "close": "20:00"}, activity=kw))
    return out


NAMED_MAX_KM = 80   # a named place further than this from the city is probably a different place


def _named(q, deadline):
    """Look up places the user named. Returns (places, notFound). Amazon Location first, then Google."""
    found, missing = [], []
    gkey = None
    for name in q.get("names", []):
        hit = None
        if _mock_mode():
            i = len(found)
            hit = _place(f"mock:{q['lat']:.2f},{q['lng']:.2f}:named_{re.sub(r'[^a-z0-9]', '', name.lower())[:24]}",
                         f"{name} (sample)", "requested", q["lat"] + 0.006 * (i + 1), q["lng"] - 0.004 * (i + 1), q,
                         hours_raw="10:00-20:00", simple={"open": "10:00", "close": "20:00"}, fame_known=False,
                         description=f"Sample entry for the requested place {name}.")
        if hit is None and deadline - time.time() > 3:
            try:
                r = boto3.client("geo-places", config=_GEO_CFG).search_text(
                    QueryText=name, MaxResults=3, BiasPosition=[q["lng"], q["lat"]])
                for it in r.get("ResultItems", []):
                    pos = it.get("Position")
                    if pos and it.get("Title") and _km(q["lat"], q["lng"], pos[1], pos[0]) <= NAMED_MAX_KM:
                        hit = _place("aloc:" + it["PlaceId"][:200], it["Title"], "requested", pos[1], pos[0], q,
                                     fame_known=False, url="https://www.google.com/maps/search/?api=1&query="
                                     f"{quote(it['Title'])}%20{pos[1]},{pos[0]}")
                        break
            except Exception as e:  # noqa: BLE001
                print("named lookup (amazon) failed:", e)
        if hit is None and deadline - time.time() > 3:
            gkey = gkey if gkey is not None else (_secret("GOOGLE_MAPS_KEY") or "")
            if gkey:
                try:
                    r = requests.post("https://places.googleapis.com/v1/places:searchText", timeout=10, json={
                        "textQuery": name, "maxResultCount": 1,
                        "locationBias": {"circle": {"center": {"latitude": q["lat"], "longitude": q["lng"]},
                                                    "radius": 50000}}},
                        headers={"X-Goog-Api-Key": gkey, "Content-Type": "application/json",
                                 "X-Goog-FieldMask": "places.id,places.displayName,places.location,places.googleMapsUri,"
                                                     "places.regularOpeningHours.weekdayDescriptions,places.rating"})
                    for gp in (r.json().get("places") or []) if r.status_code < 400 else []:
                        loc = gp.get("location", {})
                        if loc and _km(q["lat"], q["lng"], loc["latitude"], loc["longitude"]) <= NAMED_MAX_KM:
                            raw = "; ".join((gp.get("regularOpeningHours") or {}).get("weekdayDescriptions") or []) or None
                            hit = _place("gplace:" + gp["id"], gp.get("displayName", {}).get("text", name), "requested",
                                         loc["latitude"], loc["longitude"], q, hours_raw=raw,
                                         url=gp.get("googleMapsUri"), rating=gp.get("rating"), fame_known=False)
                except Exception as e:  # noqa: BLE001
                    print("named lookup (google) failed:", e)
        if hit:
            hit["requested"] = name
            hit["tags"] = ["requested by user"] + hit["tags"]
            hit["matches"] = ["requested"]
            found.append(hit)
        else:
            missing.append(name)
    return found, missing


def _matches(p, prefs):
    """Which requested preferences this place meets (shown to the planner)."""
    tags, m = p.get("tags") or [], []
    if prefs.get("vibe") == "hidden_gems" and "local pick" in tags:
        m.append("hidden gem")
    if prefs.get("vibe") == "classic" and "well-known" in tags:
        m.append("classic sight")
    for diet in prefs.get("dietary", []):
        ok = (diet.replace("_", "-"),) + (("vegan",) if diet == "vegetarian" else ())   # vegan suits vegetarians
        named = diet.replace("_", " ") in f'{p.get("name", "")} {p.get("activity") or ""}'.lower()
        if named or any(t.startswith(ok) for t in tags):
            m.append(diet.replace("_", "-"))
    if prefs.get("accessibility") and "wheelchair" in tags:
        m.append("wheelchair")
    if prefs.get("audience") == "family" and "family" in tags:
        m.append("family")
    if prefs.get("indoorOnly") and "indoor" in tags:
        m.append("indoor")
    if prefs.get("freeOnly") and "free entry" in tags:
        m.append("free entry")
    return m


def _allowed(p, prefs):
    tags = p.get("tags") or []
    text = " ".join(str(p.get(k) or "") for k in ("name", "category", "cuisine", "activity", "description")).lower()
    if any(a and a in text for a in prefs.get("avoid", [])):
        return False
    if prefs.get("accessibility") and "not wheelchair accessible" in tags:
        return False
    if prefs.get("freeOnly") and "paid entry" in tags:
        return False
    if prefs.get("audience") == "family" and p.get("category") in ADULT_ONLY:
        return False
    if prefs.get("indoorOnly") and p.get("category") != "activity" and "indoor" not in tags:
        return False
    return True


def _rank(places, q):
    """Per category: preference matches first, then by vibe (famous vs local), known hours/website, distance."""
    prefs = q.get("prefs") or {}
    vibe = prefs.get("vibe", "balanced")
    for p in places:
        p["matches"] = _matches(p, prefs)

    def best(group):
        group = [p for p in group if _allowed(p, prefs)]
        fame = {"classic": lambda p: -int(p["notable"]), "hidden_gems": lambda p: int(p["notable"]),
                "balanced": lambda p: 0}[vibe]
        group.sort(key=lambda p: (-len(p["matches"]), fame(p), -int(p["openSimple"] is not None),
                                  -(p["rating"] or 0), p["distanceKm"]))
        if vibe == "balanced":   # alternate famous and local picks so both kinds reach the planner
            famous = [p for p in group if p["notable"]]
            local = [p for p in group if not p["notable"]]
            mixed = []
            for a, b in zip(famous, local):
                mixed += [a, b]
            group = mixed + famous[len(mixed) // 2:] + local[len(mixed) // 2:]
        return group[:q["maxPerCategory"]]
    out = []
    for cat in q["categories"]:
        out.extend(best([p for p in places if p["category"] == cat]))
    for kw in q.get("keywords", []):
        out.extend(best([p for p in places if p["category"] == "activity" and p.get("activity") == kw]))
    return out


# ---------------------------------------------------------------- entry point
def lambda_handler(event, context):
    if isinstance(event, str):
        event = json.loads(event)
    q, error = _validate(event or {})
    if error:
        return error

    deadline = time.time() + TIME_BUDGET_S
    named, not_found = _named(q, deadline) if q["names"] else ([], [])
    extra = {"notFound": not_found} if not_found else {}
    only_named = q["names"] and not event.get("categories") and not event.get("keywords")
    if only_named:   # just the named places, no general search
        _pool_put(named)
        return {"ok": True, "source": "mock" if _mock_mode() else "named", "query": q, "places": named, **extra}

    if _mock_mode():
        places = named + _rank(_mock(q), q)
        _pool_put(places)
        return {"ok": True, "source": "mock", "query": q, "places": places,
                "note": "Mock data — not real places.", **extra}

    key = _cache_key({**q, "names": []})
    cached = _cache_get(key)
    if cached:
        _pool_put(named + cached.get("places", []))
        return {**cached, "places": named + cached.get("places", []),
                "source": cached.get("source", "") + "-cache", **extra}

    attempts, places, sources = [], [], []
    if q["categories"]:
        qc = {**q, "keywords": []}
        for provider in (_overpass, _amazon_location, _google):
            if deadline - time.time() < 3:
                attempts.append(f"{provider.__name__.strip('_')} skipped: time budget used up")
                continue
            found, err = provider(qc, deadline)
            if err:
                attempts.append(err)
                print("provider failed:", err)
                continue
            if found:
                places += found
                sources.append(provider.__name__.strip("_"))
                break
            attempts.append(f"{provider.__name__.strip('_')} returned no results")
    if q["keywords"]:   # free-text activities need a text search (Overpass can't do that)
        qk = {**q, "categories": []}
        for provider in (_amazon_location, _google):
            if deadline - time.time() < 3:
                attempts.append(f"keywords: {provider.__name__.strip('_')} skipped: time budget used up")
                continue
            found, err = provider(qk, deadline)
            if err:
                attempts.append("keywords: " + err)
                print("keyword provider failed:", err)
                continue
            if found:
                places += found
                sources.append(provider.__name__.strip("_") + "-keywords")
                break

    if places:
        places = _rank(places, q)
        attribution = []
        if "overpass" in sources:
            attribution.append("Place data © OpenStreetMap contributors")
        if any(s_.startswith("amazon_location") for s_ in sources):
            attribution.append("Place data © Amazon Location Service / HERE")
        result = {"ok": True, "source": "+".join(sources), "query": q, "places": places,
                  "fallbacksTried": attempts, "attribution": "; ".join(attribution) or None}
        missing = [k for k in q["keywords"] if not any(p.get("activity") == k for p in places)]
        if missing:
            result["note"] = f"No places found for: {', '.join(missing)}."
        _cache_put(key, result)
        _pool_put(named + places)
        return {**result, "places": named + places, **extra}

    places = named + _rank(_mock(q), q)
    _pool_put(places)
    return {"ok": True, "source": "mock-fallback", "query": q, "places": places, "fallbacksTried": attempts, **extra,
            "note": "Live providers failed; showing mock data. Tell the user these are not real places."}

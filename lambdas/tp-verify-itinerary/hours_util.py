
"""hours_util.py - shared opening-hours parser: one place + one date -> usable intervals (minutes)."""
import re
from datetime import date

_D = ["mo", "tu", "we", "th", "fr", "sa", "su"]
_G = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DAY = r"(?:mo|tu|we|th|fr|sa|su)\b"
_SEL = re.compile(rf"^\s*({_DAY}(?:\s*-\s*{_DAY})?(?:\s*,\s*{_DAY}(?:\s*-\s*{_DAY})?)*)\s*:?\s*(.*)$", re.I)
_SPAN = re.compile(r"(\d{1,2}):(\d{2})\s*([ap]m)?\s*[-\u2013\u2014]\s*(\d{1,2}):(\d{2})\s*([ap]m)?", re.I)
_UNSUPPORTED = re.compile(r"sunrise|sunset|dawn|dusk|\bweek\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b", re.I)


def _hm(s):
    h, m = str(s).split(":")
    return int(h) * 60 + int(m)


def _mins(h, m, ap=None):
    h, m = int(h), int(m)
    if ap:
        h = h % 12 + (12 if ap.lower() == "pm" else 0)
    return h * 60 + m


def _intervals(text):
    out = []
    for m in _SPAN.finditer(text):
        h1, m1, a1, h2, m2, a2 = m.groups()
        a1 = a1 or a2
        o, c = _mins(h1, m1, a1), _mins(h2, m2, a2)
        if c <= o or c >= 1440:
            c = 1440
        out.append((o, c))
    return out


def _day_range(tok):
    tok = tok.lower().replace(" ", "")
    if "-" in tok:
        a, b = tok.split("-")
        i, j = _D.index(a[:2]), _D.index(b[:2])
        return [(i + k) % 7 for k in range(((j - i) % 7) + 1)]
    return [_D.index(tok[:2])]


def parse_week(raw):
    """-> list of 7 interval lists (Mon..Sun; [] = closed), or None when unknown/unsupported."""
    if not raw or not isinstance(raw, str):
        return None
    s, low = raw.strip(), raw.strip().lower()
    if low == "24/7":
        return [[(0, 1440)] for _ in range(7)]
    if _UNSUPPORTED.search(low):
        return None
    if low.split(":")[0].strip() in _G:                      # Google weekdayDescriptions
        week = [None] * 7
        for part in re.split(r";\s*", s):
            name, _, rest = part.partition(":")
            name = name.strip().lower()
            if name in _G:
                week[_G.index(name)] = [(0, 1440)] if "24 hours" in rest.lower() else _intervals(rest)
        return week if all(w is not None for w in week) else None
    week, touched = [None] * 7, False                        # OSM opening_hours
    for rule in s.split(";"):
        rule = rule.strip()
        if not rule or re.search(r"\bph\b", rule, re.I):
            continue
        days, body = range(7), rule
        m = _SEL.match(rule)
        if m:
            days = [d for tok in m.group(1).split(",") for d in _day_range(tok)]
            body = m.group(2)
        bl = body.lower()
        if "24/7" in bl or "open 24" in bl:
            val = [(0, 1440)]
        elif "off" in bl or "closed" in bl:
            val = []
        else:
            val = _intervals(body)
            if not val:
                return None
        for d in days:
            week[d] = val
        touched = True
    if not touched:
        return None
    return [w if w is not None else [] for w in week]


def hours_for(place, iso_date=None):
    """-> {"known": bool, "closed": bool, "intervals": [(open, close), ...]} for that date."""
    week = parse_week(place.get("openingHours"))
    if week is not None and iso_date:
        iv = week[date.fromisoformat(str(iso_date)[:10]).weekday()]
        return {"known": True, "closed": not iv, "intervals": iv}
    s = place.get("openSimple")
    if s:
        return {"known": True, "closed": False, "intervals": [(_hm(s["open"]), _hm(s["close"]))]}
    return {"known": False, "closed": False, "intervals": []}


def fit_visit(info, earliest, minutes):
    """Earliest start >= earliest with the whole visit inside one interval; None if it never fits."""
    if not info["known"]:
        return earliest
    for o, c in sorted(info["intervals"]):
        begin = max(earliest, o)
        if begin + minutes <= c:
            return begin
    return None

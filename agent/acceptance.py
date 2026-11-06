"""
acceptance — single source of truth for the travel agent's delivery rules.

Everything that decides whether a plan is delivered, and in what state, lives here:
verifier-violation triage, judge-score validation, the four delivery states and the
loop stop reasons. Pure code, no model calls, no AWS — every rule has an offline test.

Delivery states:
  accepted        verified + every judge criterion >= threshold + nothing unscheduled
                  + no mandatory findings + no unknown-price bookings. The only state
                  where qualityAccepted may be true.
  budget_warning  the plan fails on budget and nothing else; delivered with an
                  explicit shortfall warning.
  caveated        non-critical issues (judge below threshold or unavailable,
                  supermarkets as meal stops, unverified dietary/accessibility gaps,
                  unknown booking prices, soft unknown verifier checks (no mandatory wording), infra
                  verify_error); delivered with each finding named, never accepted.
  blocked         a confirmed mandatory-constraint violation: a priority-1 verifier
                  violation other than budget/verify_error, a deterministic
                  dietary/accessibility/avoid *conflict*, or an unknown verifier
                  check whose message uses mandatory wording. Delivered only as
                  a refused itinerary event (status: "refused").
"""
import re

JUDGE_CRITERIA = ("preferenceFit", "theme", "pacing", "variety")

STATE_ACCEPTED = "accepted"
STATE_BUDGET_WARNING = "budget_warning"
STATE_CAVEATED = "caveated"
STATE_BLOCKED = "blocked"

STOP_ACCEPTED = "accepted"
STOP_BUDGET_WARNING = "budget_only_warning"
STOP_BLOCKED_MANDATORY = "blocked_mandatory"
STOP_ROUND_LIMIT = "round_limit"
STOP_NO_PROGRESS = "no_progress"
STOP_JUDGE_UNAVAILABLE_CLEAN = "judge_unavailable_clean"
STOP_REPAIR_FAILED = "repair_failed"
STOP_MALFORMED_PLAN = "malformed_plan"
STOP_LOOP_DISABLED = "loop_disabled"

# Known verifier checks and their priority tiers (moved from the agent).
_VIOLATION_PRIORITY = {
    "verify_error": 1, "must_visit": 1, "dietary": 1, "accessibility": 1, "avoid": 1,
    "dates": 1, "missing_day": 1, "unknown_id": 1, "unknown_place": 1, "unknown_stay": 1,
    "unknown_flight": 1, "far_from_base": 1, "city_mismatch": 1, "transfer": 1,
    "budget": 2,
    "day_trip": 3, "requested_activity": 3, "hotel_wish": 3, "flight_wish": 3,
    "preference_fit": 4, "high_priority_preference": 4, "cuisine": 4, "vibe": 4,
    "irrelevant_stop": 4,
    "opening_hours": 5, "hours": 5, "flight_window": 5, "transfer_window": 5,
    "time_window": 5, "overlap": 5, "did_not_fit": 5, "max_stops": 5, "min_free_time": 5,
    "pacing": 5,
    "variety": 6, "theme": 6, "duplicate_category": 6,
}

_MANDATORY_HINTS = ("must visit", "pinned", "dietary", "halal", "kosher", "vegetarian",
                    "vegan", "gluten", "wheelchair", "accessibility", "avoid", "unknown",
                    "missing date", "invalid", "safety", "allerg")
_SOFT_HINTS = ("variety", "theme", "duplicate", "pacing", "style", "vibe", "preference",
               "cuisine", "suggestion", "repetit", "filler", "diversity", "balance")
_TIMING_HINTS = ("hour", "closed", "fit", "flight", "transfer", "stops", "free time", "pace")


def violation_priority(v):
    """Priority tier 1-6 for one verifier violation.

    Unknown check names are triaged by message evidence. Mandatory wording (dietary,
    accessibility, allergy, avoid, must-visit, invalid/unknown ids ...) is tier 1 and
    blocks delivery. An unknown check with no mandatory wording is tier 4 (soft): the
    plan is not verified, so it can never be accepted, but a verifier that adds a new
    check name cannot cause good plans to be refused. Such checks are listed in
    metrics via unknown_verifier_findings.
    """
    chk = str((v or {}).get("check") or "").lower().strip()
    if chk in _VIOLATION_PRIORITY:
        return _VIOLATION_PRIORITY[chk]
    msg = str((v or {}).get("message") or "").lower()
    if any(k in msg for k in _MANDATORY_HINTS):
        return 1
    if "budget" in msg or "cost" in msg:
        return 2
    if "day trip" in msg or "requested" in msg:
        return 3
    if any(k in msg for k in _SOFT_HINTS):
        return 4
    if any(k in msg for k in _TIMING_HINTS):
        return 5
    return 4  # no mandatory wording -> soft: never accepted (plan is unverified) but not refused; surfaced in metrics

# Place IDs such as aloc:AQAA..., osm:node/123, gplace:..., mock:... must never reach the traveller.
_ID_RE = re.compile(r"\b(?:aloc|osm|gplace|mock|booked|lite|duffel):[^\s)\],;]+", re.I)
# "Priority 1 [day 2026-12-17]: " or "Priority 1 day 2026-12-17 " at the start of a finding.
_PREFIX_RE = re.compile(r"^\s*Priority\s+\d+\s*(?:\[?\s*day\s+\d{4}-\d{2}-\d{2}\s*\]?)?\s*:?\s*", re.I)
# "food stop (Matto) has unverified halal compliance (no explicitly tagged halal restaurant in pool)."
_UNVERIFIED_RE = re.compile(
    r"(?:food\s+)?stop\s+\((?P<name>[^()]+)\)\s+has unverified\s+(?P<what>.+?)(?:\s+compliance)?"
    r"\s*(?:\((?P<why>[^()]*)\))?\s*\.?$", re.I)
MAX_NOTES = 5
MAX_NAMES_SHOWN = 6


def _readable(name):
    """A stop name for the traveller: never a raw ID."""
    name = str(name or "").strip()
    return "A planned stop" if (not name or _ID_RE.fullmatch(name)) else name


def _tidy_reasons(reasons, limit=MAX_NOTES):
    """Notes for the traveller: no place IDs, no 'Priority 1 [day ...]' prefixes, identical notes once, all
    'unverified ...' notes of one kind merged into one sentence, and at most `limit` notes."""
    groups, order = {}, []
    for r in reasons:
        text = re.sub(r"\s{2,}", " ", _PREFIX_RE.sub("", _ID_RE.sub("", str(r or "")))).strip()
        if not text:
            continue
        m = _UNVERIFIED_RE.search(text)
        if m:
            key = (m.group("what").strip().lower(), (m.group("why") or "").strip().rstrip("."))
            if key not in groups:
                groups[key] = []
                order.append(("group", key))
            name = m.group("name").strip()
            if name not in groups[key]:
                groups[key].append(name)
            continue
        text = text[0].upper() + text[1:]
        if text[-1] not in ".!?":
            text += "."
        if ("text", text) not in order:
            order.append(("text", text))
    notes = []
    for kind, value in order:
        if kind == "text":
            notes.append(value)
            continue
        what, why = value
        names = groups[value]
        shown = ", ".join(names[:MAX_NAMES_SHOWN])
        if len(names) > MAX_NAMES_SHOWN:
            shown += f" and {len(names) - MAX_NAMES_SHOWN} more"
        sentence = (f"{what[:1].upper() + what[1:]} could not be verified for {len(names)} "
                    f"stop{'s' if len(names) != 1 else ''} ({shown}).")
        if why:
            sentence += f" {why[:1].upper() + why[1:]}."
        notes.append(sentence)
    if len(notes) > limit:
        notes = notes[:limit - 1] + [f"{len(notes) - limit + 1} more minor notes are not shown."]
    return notes
  
def classify_unknown_check(v):
    """'mandatory_likely' | 'soft' for a violation whose check name is not in the known table."""
    return "mandatory_likely" if violation_priority(v) == 1 else "soft"


def unknown_verifier_findings(vres):
    """All violations with unrecognized check names, with their triage class (metrics/refusal audit)."""
    out = []
    for v in (vres or {}).get("violations") or []:
        if not isinstance(v, dict):
            out.append({"check": "", "message": str(v), "class": "mandatory_likely"})
            continue
        chk = str(v.get("check") or "").lower().strip()
        if chk and chk not in _VIOLATION_PRIORITY:
            out.append({"check": chk, "message": str(v.get("message") or ""),
                        "class": classify_unknown_check(v)})
    return out


def is_verified(vres):
    """A verifier result counts only when the call succeeded AND passed is True."""
    return isinstance(vres, dict) and vres.get("ok") is True and vres.get("passed") is True


def is_budget_only_failure(vres):
    """True when the plan fails verification on budget checks and nothing else."""
    violations = (vres or {}).get("violations")
    return ((vres or {}).get("ok") is True and (vres or {}).get("passed") is False
            and isinstance(violations, list) and bool(violations)
            and all(isinstance(v, dict) and str(v.get("check") or "").lower().strip() == "budget"
                    for v in violations))


def budget_shortfall(vres, budget_amount):
    """Amount by which the verifier's total exceeds the budget, or None."""
    totals = (vres or {}).get("totals") or {}
    total = float(totals.get("total", totals.get("totalSGD", 0)) or 0)
    if budget_amount and total > budget_amount > 0:
        return round(total - budget_amount, 2)
    return None


def violation_severity(vres, budget_amount=0.0):
    """(critical_count, unknown_check_count, weighted_severity, total_violations).

    Unknown checks triaged as possibly mandatory count as critical; every violation
    with an unrecognized check name is counted in unknown_check_count.
    """
    violations = (vres or {}).get("violations") or []
    crit = unknown = total = 0
    sev = 0.0
    for v in violations:
        total += 1
        if not isinstance(v, dict):
            unknown += 1
            sev += 100.0          # malformed violation: possibly mandatory
            continue
        chk = str(v.get("check") or "").lower().strip()
        if chk and chk not in _VIOLATION_PRIORITY:
            unknown += 1
        prio = violation_priority(v)
        if prio == 1:
            crit += 1
            sev += 100.0
        elif prio == 2:
            sev += 50.0
        elif prio in (3, 4):
            sev += 30.0
        elif prio == 5:
            sev += 20.0
        else:
            sev += 10.0
    totals = (vres or {}).get("totals") or {}
    tot = float(totals.get("total", totals.get("totalSGD", 0)) or 0)
    if budget_amount and tot > budget_amount > 0:
        sev += min(40.0, ((tot - budget_amount) / budget_amount) * 40.0)
    return crit, unknown, round(sev, 2), total


def parse_judge_score(val):
    """Strict 1..5 integer score; rejects booleans, floats, out-of-range and bad strings."""
    if isinstance(val, bool) or val is None:
        return None
    if isinstance(val, int):
        return val if 1 <= val <= 5 else None
    if isinstance(val, str):
        s = val.strip()
        return int(s) if re.fullmatch(r"[1-5]", s) else None
    return None


def is_valid_judge_result(judge_res):
    if not isinstance(judge_res, dict) or judge_res.get("unavailable") or judge_res.get("evaluated") is False:
        return False
    scores = judge_res.get("scores")
    return isinstance(scores, dict) and all(parse_judge_score(scores.get(k)) is not None
                                            for k in JUDGE_CRITERIA)


def judge_scores_pass(judge_res, threshold=4):
    """All four criteria hold valid scores and EVERY score >= threshold (never an average)."""
    if not is_valid_judge_result(judge_res):
        return False
    return all(parse_judge_score(judge_res["scores"].get(k)) >= threshold for k in JUDGE_CRITERIA)


def judge_min(judge_res):
    return min(parse_judge_score(judge_res["scores"][k]) for k in JUDGE_CRITERIA) \
        if is_valid_judge_result(judge_res) else 0


def split_diag_issues(diag):
    """Split deterministic-eval issues into (mandatory, caveat).

    mandatory: priority-1 conflicts (dietary/accessibility/avoid) -> block delivery.
    caveat:    resolved conflicts of lower priority (e.g. supermarket survivors) and
               'unknown' statuses (unverified dietary/accessibility) -> caveat delivery.
    Other diagnostic issues (gaps, repetition) inform repair but do not demote acceptance.
    """
    mandatory, caveat = [], []
    for i in (diag or {}).get("issues") or []:
        if not isinstance(i, dict):
            continue
        status = i.get("status")
        if status == "conflict":
            (mandatory if i.get("priority") == 1 else caveat).append(i)
        elif status == "unknown":
            caveat.append(i)
    return mandatory, caveat


def _finding_text(v):
    if not isinstance(v, dict):
        return str(v).strip()
    text = str(v.get("message") or v.get("check") or "").strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text + ("" if text.endswith(".") else ".")


def classify_acceptance(verify_res, judge_res, diag, unscheduled, unpriced_bookings=(),
                        loop_enabled=True, judge_pass=4, budget_amount=0.0, currency=""):
    """Classify the best plan into a delivery state.

    Returns (state, reasons): blocking findings for 'blocked', one caveat per finding
    for 'budget_warning'/'caveated', and ([], 'accepted') only when every hard check
    passed. verify_error (verification infrastructure failure) is never accepted and
    never blocked: it is delivered as caveated with the failure disclosed.
    The reasons are written for the traveller: no IDs, similar notes merged, at most MAX_NOTES.
    """
    violations = [v for v in ((verify_res or {}).get("violations") or []) if isinstance(v, dict)]
    mandatory = [v for v in violations if violation_priority(v) == 1
                 and str(v.get("check") or "").lower().strip() not in ("budget", "verify_error")]
    diag_mandatory, diag_caveat = split_diag_issues(diag)
    mandatory += diag_mandatory
    if mandatory:
        return STATE_BLOCKED, _tidy_reasons([_finding_text(v) for v in mandatory])

    verified = is_verified(verify_res)
    if not verified and is_budget_only_failure(verify_res):
        shortfall = budget_shortfall(verify_res, budget_amount)
        label = f"{currency} {shortfall:,.2f}" if (shortfall and currency) else (
            f"{shortfall:,.2f}" if shortfall else "")
        text = "The estimated total exceeds your trip budget"
        if label:
            text += f" by about {label}"
        return STATE_BUDGET_WARNING, [text + "."]

    reasons = []
    if not verified:
        for v in violations[:6]:
            t = _finding_text(v)
            if t:
                reasons.append(t)
        reasons.append("This itinerary did not pass every automatic check, so it is not fully verified.")
    if loop_enabled:
        if judge_scores_pass(judge_res, judge_pass):
            pass
        elif is_valid_judge_result(judge_res):
            reasons.append(f"The preference review scored this plan below the required {judge_pass} "
                           f"out of 5 on at least one criterion (lowest score {judge_min(judge_res)}).")
        else:
            reasons.append("The preference-quality review was unavailable for this itinerary, "
                           "so its fit to your preferences is not confirmed.")
    judge_ok = (judge_res is None and not loop_enabled) or judge_scores_pass(judge_res, judge_pass)
    for i in diag_caveat:
        t = _finding_text(i)
        if t:
            reasons.append(t)
    left_out = [f"{_readable(u.get('name') or u.get('placeId'))} ({u.get('date')})" for u in (unscheduled or [])]
    if len(left_out) == 1:
        u = unscheduled[0]
        reasons.append(f"{_readable(u.get('name') or u.get('placeId'))} didn't fit on {u.get('date')} "
                       f"and was left out.")
    elif left_out:
        reasons.append(f"{len(left_out)} stops didn't fit their day and were left out: "
                       + ", ".join(left_out[:MAX_NAMES_SHOWN])
                       + (f" and {len(left_out) - MAX_NAMES_SHOWN} more" if len(left_out) > MAX_NAMES_SHOWN else "") + ".")
    for b in (unpriced_bookings or []):
        kind = {"flight": "flight", "stay": "hotel", "transfer": "transfer"}.get(b.get("kind"), "booking")
        name = _readable(b.get("name") or b.get("id") or "selected")
        reasons.append(f"The price of the {kind} '{name}' is not available, so the total shown may be "
                       "understated and your budget fit could not be verified.")

    if verified and judge_ok and not unscheduled and not unpriced_bookings and not reasons:
        return STATE_ACCEPTED, []
    return STATE_CAVEATED, _tidy_reasons(reasons) or ["This itinerary could not be fully verified."]

"""Pure marketplace validation and authorization rules; no AWS access."""
from datetime import date
from decimal import Decimal, InvalidOperation

class ValidationError(ValueError):
    pass

AGENCY_TRANSITIONS = {
    "PENDING_REVIEW": {"APPROVED", "REJECTED"},
    "APPROVED": {"SUSPENDED"},
    "SUSPENDED": {"APPROVED"},
    "REJECTED": {"PENDING_REVIEW"},
}
PACKAGE_TRANSITIONS = {
    "DRAFT": {"PENDING_REVIEW", "WITHDRAWN"},
    "PENDING_REVIEW": {"PUBLISHED", "DRAFT", "WITHDRAWN"},
    "PUBLISHED": {"PAUSED", "EXPIRED", "WITHDRAWN"},
    "PAUSED": {"PUBLISHED", "EXPIRED", "WITHDRAWN"},
    "EXPIRED": {"DRAFT", "WITHDRAWN"},
    "WITHDRAWN": set(),
}
AGENCY_FIELDS = ("legalName", "tradingName", "contactPerson", "businessEmail", "telephone",
                 "country", "businessAddress", "businessDescription", "supportedDestinations")

def _text(value, field, limit=300):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValidationError("invalid " + field)
    return value.strip()

def validate_agency(body):
    if not isinstance(body, dict):
        raise ValidationError("invalid agency")
    provider_type=body.get('providerType','agency')
    if provider_type not in ('agency','local_guide'): raise ValidationError('invalid providerType')
    result = {'providerType':provider_type}
    if provider_type=='local_guide':
        languages=body.get('languages',[])
        if not isinstance(languages,list) or not 1<=len(languages)<=30: raise ValidationError('languages required')
        result['languages']=[_text(x,'language',80) for x in languages]
        result['experienceDescription']=_text(body.get('experienceDescription'),'experienceDescription',2000)
    for field in AGENCY_FIELDS:
        if field == "supportedDestinations":
            val = body.get(field)
            if not isinstance(val, list) or not 1 <= len(val) <= 50:
                raise ValidationError("invalid supportedDestinations")
            result[field] = [_text(item, field, 100) for item in val]
        else:
            result[field] = _text(body.get(field), field, 2000 if field == "businessDescription" else 300)
    email = result["businessEmail"]
    if email.count("@") != 1 or any(c.isspace() for c in email) or "." not in email.split("@")[1]:
        raise ValidationError("invalid businessEmail")
    if body.get("termsAccepted") is not True:
        raise ValidationError("terms must be accepted")
    result["termsAccepted"] = True
    for field in ("website", "registrationNumber", "contactPreferences"):
        if field in body:
            value = body[field]
            if field == "contactPreferences":
                if not isinstance(value, list) or len(value) > 10:
                    raise ValidationError("invalid contactPreferences")
                result[field] = [_text(x, field, 100) for x in value]
            else:
                result[field] = _text(value, field, 300)
    return result

def _integer(value, field, minimum, maximum):
    if isinstance(value, Decimal) and value.is_finite() and value == value.to_integral_value():
        value = int(value)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValidationError("invalid " + field)
    return value

def _day(value, field):
    if not isinstance(value, str):
        raise ValidationError("invalid " + field)
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
        return parsed
    except ValueError as exc:
        raise ValidationError("invalid " + field) from exc

def validate_package(body, today=None):
    if not isinstance(body, dict):
        raise ValidationError("invalid package")
    result = {"title": _text(body.get("title"), "title", 200),
              "description": _text(body.get("description"), "description", 5000)}
    days = _integer(body.get("durationDays"), "durationDays", 1, 366)
    result["durationDays"] = days
    destinations = body.get("destinations")
    if not isinstance(destinations, list) or not 1 <= len(destinations) <= 30:
        raise ValidationError("invalid destinations")
    result["destinations"] = []
    for dest in destinations:
        if not isinstance(dest, dict):
            raise ValidationError("invalid destination")
        result["destinations"].append({"city": _text(dest.get("city"), "city", 100),
             "country": _text(dest.get("country"), "country", 100),
             "nights": _integer(dest.get("nights"), "nights", 0, 365)})
    if sum(d["nights"] for d in result["destinations"]) != days - 1:
        raise ValidationError("duration must equal total nights plus one")
    low = _integer(body.get("minimumTravellers"), "minimumTravellers", 1, 1000)
    high = _integer(body.get("maximumTravellers"), "maximumTravellers", low, 1000)
    result.update(minimumTravellers=low, maximumTravellers=high)
    price = body.get("price")
    if not isinstance(price, dict) or isinstance(price.get("amount"), bool):
        raise ValidationError("invalid price")
    try:
        amount = Decimal(str(price.get("amount")))
        if not amount.is_finite() or amount < 0 or amount > 100000000:
            raise ValueError()
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError("invalid price") from exc
    currency = price.get("currency")
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isascii() or not currency.isalpha() or currency != currency.upper():
        raise ValidationError("invalid currency")
    basis = price.get("basis")
    if basis not in ("per_person", "per_group"):
        raise ValidationError("invalid price basis")
    result["price"] = {"amount": amount, "currency": currency, "basis": basis}
    for field in ("included", "excluded"):
        val = body.get(field)
        if not isinstance(val, list) or not 1 <= len(val) <= 100:
            raise ValidationError("invalid " + field)
        result[field] = [_text(x, field, 300) for x in val]
    availability = body.get("availability")
    if not isinstance(availability, list) or not 1 <= len(availability) <= 100:
        raise ValidationError("invalid availability")
    current = today or date.today()
    result["availability"] = []
    for period in availability:
        if not isinstance(period, dict):
            raise ValidationError("invalid availability")
        start, end = _day(period.get("startDate"), "startDate"), _day(period.get("endDate"), "endDate")
        if start > end or end < current:
            raise ValidationError("invalid availability dates")
        result["availability"].append({"startDate": start.isoformat(), "endDate": end.isoformat()})
    for field in ("categories", "cuisines", "dietaryOptions"):
        val = body.get(field, [])
        if not isinstance(val, list) or len(val) > 50:
            raise ValidationError("invalid " + field)
        result[field] = [_text(x, field, 100) for x in val]
    for field in ("activityLevel", "hotelComfortLevel"):
        if field in body:
            result[field] = _integer(body[field], field, 1, 10)
    for field in ("departureLocations",):
        val = body.get(field, [])
        if not isinstance(val, list) or len(val) > 50:
            raise ValidationError("invalid " + field)
        result[field] = [_text(x, field, 100) for x in val]
    if "flightType" in body:
        if body["flightType"] not in ("round", "one_way", "none"):
            raise ValidationError("invalid flightType")
        result["flightType"] = body["flightType"]
    if "accessible" in body:
        if type(body["accessible"]) is not bool:
            raise ValidationError("invalid accessible")
        result["accessible"] = body["accessible"]
    if "mandatoryExtraSGD" in body:
        try:
            extra = Decimal(str(body["mandatoryExtraSGD"]))
            if isinstance(body["mandatoryExtraSGD"], bool) or not extra.is_finite() or extra < 0 or extra > 100000000:
                raise ValueError()
        except (InvalidOperation, ValueError) as exc:
            raise ValidationError("invalid mandatoryExtraSGD") from exc
        result["mandatoryExtraSGD"] = extra
    if "preferenceScores" in body:
        scores = body["preferenceScores"]
        allowed = ("culture", "food", "nature", "comfort", "intensity")
        if not isinstance(scores, dict) or any(k not in allowed for k in scores):
            raise ValidationError("invalid preferenceScores")
        result["preferenceScores"] = {k: _integer(v, k, 1, 10) for k, v in scores.items()}
    if 'costInclusions' in body:
        coverage=body['costInclusions']
        categories=('flights','accommodation','transfers','meals','activities','mandatory_fees')
        if not isinstance(coverage,dict) or any(k not in categories or type(v) is not bool for k,v in coverage.items()):
            raise ValidationError('invalid costInclusions')
        result['costInclusions']=dict(coverage)
    if 'additionalCosts' in body:
        extras=body['additionalCosts']
        categories=('flights','accommodation','transfers','meals','activities','mandatory_fees')
        if not isinstance(extras,dict) or any(k not in categories for k in extras):
            raise ValidationError('invalid additionalCosts')
        result['additionalCosts']={}
        for key,money in extras.items():
            if result.get('costInclusions',{}).get(key) is not False:
                raise ValidationError('additional cost requires an explicitly excluded category')
            if not isinstance(money,dict) or isinstance(money.get('amount'),bool): raise ValidationError('invalid additional cost')
            try: value=Decimal(str(money['amount']))
            except (InvalidOperation,KeyError,TypeError): raise ValidationError('invalid additional cost')
            if not value.is_finite() or not 0<=value<=100000000 or money.get('currency') not in ('SGD','USD','SAR','EUR','GBP','CNY','JPY') or money.get('basis') not in ('per_person','per_group'):
                raise ValidationError('invalid additional cost')
            result['additionalCosts'][key]={'amount':value,'currency':money['currency'],'basis':money['basis']}
    result['listingType']=body.get('listingType','package')
    if result['listingType'] not in ('package','guided_tour'): raise ValidationError('invalid listingType')
    if result['listingType']=='guided_tour':
        result['durationHours']=_integer(body.get('durationHours'),'durationHours',1,24)
        result['meetingPoint']=_text(body.get('meetingPoint'),'meetingPoint',300)
        result['serviceArea']=_text(body.get('serviceArea'),'serviceArea',200)
        languages=body.get('languages')
        if not isinstance(languages,list) or not languages: raise ValidationError('tour languages required')
        result['languages']=[_text(x,'language',80) for x in languages]
        if days!=1: raise ValidationError('Guided tours must use one day and zero nights')
    return result

def validate_transition(kind, current, target, actor_role, agency_status):
    if actor_role not in ("agency", "admin"):
        raise PermissionError("unauthorized")
    transitions = AGENCY_TRANSITIONS if kind == "agency" else PACKAGE_TRANSITIONS if kind == "package" else {}
    if target not in transitions.get(current, set()):
        raise ValidationError("invalid transition")
    if kind == "agency" and actor_role != "admin":
        raise PermissionError("administrator required")
    if kind == "package":
        if actor_role == "agency" and (agency_status != "APPROVED" or target in {"PUBLISHED", "EXPIRED"}):
            raise PermissionError("transition not permitted")
        if target == "PUBLISHED" and agency_status != "APPROVED":
            raise PermissionError("agency approval required")
    return target

def require_owner(user_sub, agency):
    if not user_sub or not agency or agency.get("ownerSub") != user_sub:
        raise PermissionError("agency ownership required")

"""Native trip-currency contracts. Prices are normalized before any trip budget arithmetic.

pool_price(row) keeps its legacy contract (float, 0.0 when the price is absent). Budget and ranking code must use
known_price(row) instead: float, or None when the price is unknown. Unknown is never free."""
import copy
import json
import math
import time
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

CURRENCIES = frozenset(('SGD','USD','SAR','EUR','GBP','CNY','JPY'))
_QUOTES = {}

def amount(value):
    if isinstance(value, bool):
        raise ValueError('Invalid money amount')
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError('Invalid money amount') from exc
    if not number.is_finite() or number < 0:
        raise ValueError('Money must be nonnegative and finite')
    return number

def budget(spec):
    value = spec.get('budget')
    if value is None:
        if spec.get('budgetSGD') is None:
            raise ValueError('Trip budget is missing')
        value = {'amount': spec['budgetSGD'], 'currency': 'SGD'}
    if not isinstance(value, dict) or value.get('currency') not in CURRENCIES:
        raise ValueError('Unsupported budget currency')
    if 'amount' not in value:
        raise ValueError('Trip budget has no amount')
    return {'amount': str(amount(value['amount'])), 'currency': value['currency']}

def code(spec):
    return budget(spec)['currency']

def budget_amount(spec):
    return float(budget(spec)['amount'])

def load_quote(target):
    if target not in CURRENCIES:
        raise ValueError('Unsupported currency')
    cached = _QUOTES.get(target)
    if cached and time.time() - cached[0] < 86400:
        return copy.deepcopy(cached[1])
    url = 'https://open.er-api.com/v6/latest/' + target
    with urllib.request.urlopen(url, timeout=8) as response:
        data = json.load(response)
    if data.get('result') != 'success' or data.get('base_code') != target:
        raise ValueError('Exchange-rate service unavailable')
    epoch = data.get('time_last_update_unix')
    if not isinstance(epoch, (int,float)) or not math.isfinite(epoch) or not 0 <= time.time()-epoch <= 172800:
        raise ValueError('Exchange-rate quote is stale')
    quote = {'base':target,'rates':data['rates'], 'asOf':datetime.fromtimestamp(epoch,timezone.utc).date().isoformat(),
             'timestamp':epoch, 'provider':'https://www.exchangerate-api.com'}
    validate_quote(quote)
    _QUOTES[target] = (time.time(),quote)
    return copy.deepcopy(quote)

def validate_quote(quote):
    if not isinstance(quote,dict) or not isinstance(quote.get('rates'),dict):
        raise ValueError('Missing exchange-rate quote')
    epoch = quote.get('timestamp')
    if not isinstance(epoch,(int,float)) or not math.isfinite(epoch) or not 0 <= time.time()-epoch <= 172800:
        raise ValueError('Exchange-rate quote is stale')
    if quote.get('base') not in CURRENCIES or amount(quote['rates'].get(quote['base'])) != 1:
        raise ValueError('Invalid exchange-rate base')
    return quote

def convert(value, source, target, quote):
    value = amount(value)
    if source == target:
        return value
    validate_quote(quote)
    source_rate = amount(quote['rates'].get(source))
    target_rate = amount(quote['rates'].get(target))
    if not source_rate or not target_rate:
        raise ValueError('Missing exchange rate')
    return value * target_rate / source_rate

def price(record, target, quote):
    if not record:
        return Decimal(0)
    native = record.get('sourcePrice')
    if isinstance(native,dict):
        value, source = native['amount'],native['currency']
    elif record.get('priceTotal') is not None and record.get('currency'):
        value, source = record['priceTotal'],record['currency']
    elif record.get('priceTotalSGD') is not None:
        value, source = record['priceTotalSGD'],'SGD'
    elif isinstance(record.get('price'),dict):
        value, source = record['price']['amount'],record['price']['currency']
    else:
        raise ValueError('Candidate has no verifiable price')
    return convert(value,source,target,quote)

def normalize_record(record,target,quote):
    out = copy.deepcopy(record)
    if isinstance(out.get('sourcePrice'),dict):
        native = out['sourcePrice']
    elif out.get('priceTotal') is not None and out.get('currency'):
        native = {'amount':str(amount(out['priceTotal'])), 'currency':out['currency']}
    elif out.get('priceTotalSGD') is not None:
        native = {'amount':str(amount(out['priceTotalSGD'])), 'currency':'SGD'}
    elif isinstance(out.get('price'),dict):
        native = out['price']
    else:
        raise ValueError('Missing candidate price')
    out['price']={'amount':str(price(record,target,quote)), 'currency':target}
    out['sourcePrice']=native
    out['fxAsOf']=quote.get('asOf')
    out.pop('priceTotalSGD',None)
    out.pop('pricePerNightSGD',None)
    return out

def prepare_pool(spec, pool, daily_estimate=100):
    """Normalize priced candidates into the trip currency.

    Unpriced candidates are kept visible and marked priceUnknown (never dropped, never
    treated as free); per-category unknown counts are stored under pool['_priceUnknown']
    for metrics. Raises only on budget/FX problems, never on a single unpriced row.
    """
    counts = {'flights': 0, 'stays': 0, 'transfers': 0}
    if 'budget' not in spec:
        for key in counts:                                    # legacy SGD path
            counts[key] = sum(1 for row in pool.get(key, []) if known_price(row) is None)
        pool['_priceUnknown'] = counts
        return pool
    spec['budget'] = budget(spec)
    target = code(spec)
    quote = spec.get('_fx')
    try:
        validate_quote(quote)
    except ValueError:
        quote = load_quote(target)
    spec['_fx'] = quote
    validate_quote(quote)
    spec['dailySpend'] = {'amount': str(convert(daily_estimate, 'SGD', target, quote)), 'currency': target}
    for key in counts:
        normalized = []
        for row in pool.get(key, []):
            try:
                normalized.append(normalize_record(row, target, quote))
            except ValueError:
                normalized.append({**row, 'priceUnknown': True} if isinstance(row, dict) else row)
                counts[key] += 1
        pool[key] = normalized
    pool['_priceUnknown'] = counts
    return pool

def pool_price(row):
    return float(amount(row['price']['amount'])) if isinstance(row.get('price'),dict) else float(row.get('priceTotalSGD') or 0)


def known_price(row):
    """Known non-negative price as a float, else None. Missing or invalid is unknown, never zero.

    Reads the normalized 'price' dict first, then legacy priceTotalSGD only when the key
    exists. An explicit stored 0 is returned as 0.0; absence returns None.
    """
    if not isinstance(row, dict):
        return None
    try:
        if isinstance(row.get('price'), dict):
            return float(amount(row['price'].get('amount')))
        if row.get('priceTotalSGD') is not None:
            return float(amount(row['priceTotalSGD']))
    except ValueError:
        return None
    return None

def daily(spec,legacy=100):
    return float(amount(spec['dailySpend']['amount'])) if 'budget' in spec else legacy

def money_text(spec,value):
    return f'{code(spec)} {float(value):,.2f}'

def normalize_cached_pool(spec, pool):
    """Normalize cached candidate rows to the trip currency.

    Backward compatible: accepts the standard pool shape (dict of candidate lists) as
    well as a flat mapping of individual rows; reads the FX quote from 'fxQuote'
    (legacy key) or '_fx' (current key); rows without a verifiable price pass through
    unchanged instead of raising.
    """
    if 'budget' not in spec:
        return pool
    quote = spec.get('fxQuote') or spec.get('_fx')
    validate_quote(quote)
    target = code(spec)

    def norm(row):
        if not isinstance(row, dict):
            return row
        if (row.get('priceTotalSGD') is None and row.get('priceTotal') is None
                and not isinstance(row.get('sourcePrice'), dict)
                and not isinstance(row.get('price'), dict)):
            return row
        try:
            return normalize_record(row, target, quote)
        except ValueError:
            return row

    if any(isinstance(v, list) for v in pool.values()):
        return {key: ([norm(r) for r in rows] if isinstance(rows, list) else rows)
                for key, rows in pool.items()}
    return {key: norm(row) for key, row in pool.items()}

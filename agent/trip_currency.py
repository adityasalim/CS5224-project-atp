"""Native trip-currency contracts. Prices are normalized before any trip budget arithmetic."""
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
        value = {'amount': spec['budgetSGD'], 'currency': 'SGD'}
    if not isinstance(value, dict) or value.get('currency') not in CURRENCIES:
        raise ValueError('Unsupported budget currency')
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

def prepare_pool(spec,pool,daily_estimate=100):
    if 'budget' not in spec:
        return pool
    spec['budget']=budget(spec)
    target = code(spec)
    quote = spec.get('_fx')
    try:
        validate_quote(quote)
    except ValueError:
        quote=load_quote(target)
        spec['_fx']=quote
    validate_quote(quote)
    spec['dailySpend']={'amount':str(convert(daily_estimate,'SGD',target,quote)), 'currency':target}
    for key in ('flights','stays','transfers'):
        pool[key]=[normalize_record(row,target,quote) for row in pool.get(key,[])]
    return pool

def pool_price(row):
    return float(amount(row['price']['amount'])) if isinstance(row.get('price'),dict) else float(row.get('priceTotalSGD') or 0)

def daily(spec,legacy=100):
    return float(amount(spec['dailySpend']['amount'])) if 'budget' in spec else legacy

def money_text(spec,value):
    return f'{code(spec)} {float(value):,.2f}'

def normalize_cached_pool(spec,pool):
    if 'budget' not in spec:
        return pool
    quote=spec['fxQuote']
    validate_quote(quote)
    target=code(spec)
    return {key: normalize_record(row,target,quote) if row.get('kind') in ('flight','stay','transfer') or row.get('priceTotalSGD') is not None or row.get('priceTotal') is not None else row
            for key,row in pool.items()}

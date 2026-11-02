"""Native currency arithmetic, provider provenance and fail-closed FX contracts."""
import sys, pathlib, time, copy
from decimal import Decimal
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/'shared'))
import trip_currency as m
q={'base':'SGD','rates':{'SGD':1,'USD':.8,'SAR':3,'EUR':.7,'GBP':.6,'CNY':5,'JPY':120},'timestamp':time.time(),'asOf':'fixture'}
for c,r in q['rates'].items():
    s={'budget':{'amount':'800','currency':c}}
    p={'flights':[{'offerId':'f','priceTotal':100,'currency':'USD','priceTotalSGD':999}], 'stays':[], 'transfers':[]}
    m.load_quote=lambda target:copy.deepcopy(q)
    m.prepare_pool(s,p)
    assert s['budget']=={'amount':'800','currency':c}
    assert Decimal(p['flights'][0]['price']['amount'])==Decimal('125')*Decimal(str(r))
    assert p['flights'][0]['sourcePrice']=={'amount':'100','currency':'USD'}
    assert 'priceTotalSGD' not in p['flights'][0]
    m.prepare_pool(s,p)
    assert Decimal(p['flights'][0]['price']['amount'])==Decimal('125')*Decimal(str(r))
    assert m.normalize_cached_pool(dict(s,fxQuote=q),{'f':p['flights'][0]})['f']['price']['currency']==c
for value in (True,-1,'NaN','Infinity'):
    try: m.amount(value)
    except ValueError: pass
    else: raise AssertionError(value)
for broken in ({},dict(q,timestamp=time.time()-200000),dict(q,rates={'SGD':2})):
    try: m.convert(100,'USD','SAR',broken)
    except ValueError: pass
    else: raise AssertionError(broken)
print('NATIVE MONEY OK: all currencies, unchanged budgets, original provider prices, no double conversion, invalid/stale FX rejected')

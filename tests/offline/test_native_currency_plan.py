"""Frozen-provider full agent regression: selected currency drives planner, tools, saved totals and PDF."""
import json
import re
import time
from decimal import Decimal
import sys
import importlib
fixture=importlib.import_module("test_agent_multi_city" if "--multi" in sys.argv else "test_agent_single_city")
MULTI="--multi" in sys.argv

import os
os.environ["AGENCY_PACKAGE_SEARCH_ENABLED"]="true"
A=fixture.A
import trip_currency
rates={'SGD':1,'USD':0.8,'SAR':3,'EUR':0.7,'GBP':0.6,'CNY':5,'JPY':120}
quote={'base':'SGD','rates':rates,'timestamp':time.time(),'asOf':'2026-11-01','provider':'fixture'}
trip_currency.load_quote=lambda target:dict(quote)
original=fixture.converse
seen=[]
def converse(**kwargs):
    system=fixture.sys_text(kwargs)
    if system.startswith(('ROLE: itinerary planner','ROLE: itinerary repairer','ROLE: repairer for a MULTI-CITY')):
        match=re.search(r'Candidate pool(?:, grouped by city in travel order)? \(the only valid IDs\):\n(\{.*?\})\n',system,re.S)
        pool=json.loads(match.group(1))
        def assert_money(value):
            if isinstance(value,list):
                for v in value: assert_money(v)
            elif isinstance(value,dict):
                assert 'priceSGD' not in value and 'priceTotalSGD' not in value,value
                for v in value.values(): assert_money(v)
        assert_money(pool)
        seen.append(pool)
        # The historical fake model chooses by priceSGD; translate only inside the fake,
        # after asserting the actual model request contains the native currency contract.
        def legacy(value):
            if isinstance(value,list): return [legacy(v) for v in value]
            if isinstance(value,dict):
                out={k:legacy(v) for k,v in value.items()}
                if isinstance(out.get('price'),dict): out['priceSGD']=float(out['price']['amount'])
                return out
            return value
        replacement=json.dumps(legacy(pool),separators=(',',':'))
        kwargs={**kwargs,'system':[{**b,'text':b['text'].replace(match.group(1),replacement)} if 'text' in b else b for b in kwargs['system']]}
    if system.startswith('ROLE: itinerary repairer') and 'over budget' not in system and 'cheaper hotel' not in system:
        plan=json.loads(re.search(r'Current plan:\n(\{.*?\})\n',system,re.S).group(1))
        return {'output':{'message':{'role':'assistant','content':[{'text':json.dumps(plan)}]}},'usage':{'inputTokens':100,'outputTokens':50},'stopReason':'end_turn'}
    return original(**kwargs)
class NativeBedrock:
    converse=staticmethod(converse)
A._bedrock=NativeBedrock()
for index,currency in enumerate(rates):
    native_budget=str(Decimal('100000')*Decimal(str(rates[currency])))
    class Context:
        session_id='native-currency-'+str(index)
        request_headers=fixture.Ctx.request_headers
    events=list(A.invoke({'prompt':'Six-day Tokyo trip for two, 10 to 15 December 2026, budget '+currency+' '+native_budget+'.',
                          'budget':{'amount':native_budget,'currency':currency}},Context()))
    errors=[e for e in events if e['type']=='error']
    assert not errors,errors
    plans=[e['itinerary'] for e in events if e['type']=='itinerary']
    assert plans,events
    plan=plans[0]
    assert plan['currency']==currency,plan
    assert plan['totals']['budget']==float(native_budget),plan
    assert plan['verified'],plan
    spec=A.SESSIONS[Context.session_id]['spec']
    assert spec['budget']['currency']==currency and 'budgetSGD' not in spec,spec
    assert A.SESSIONS[Context.session_id]['verify']['totals']['currency']==currency
    assert plan['pdfUrl'],plan
    assert A.SESSIONS[Context.session_id]['pool']['agencyPackages']==[]
    if MULTI:
        follow=list(A.invoke({'prompt':'skip abisko, just copenhagen and lofoten'},Context()))
        revisions=[e['itinerary'] for e in follow if e['type']=='itinerary']
        assert revisions and revisions[0]['verified'] and revisions[0]['currency']==currency,follow
    assert all('priceTotalSGD' not in row for group in ('flights','stays','transfers') for row in A.SESSIONS[Context.session_id]['pool'][group])
assert len(seen)>=7
print('NATIVE CURRENCY FULL PLANNING OK:', 'multi-city + follow-up' if MULTI else 'single-city', 'seven currencies, no SGD budget conversion')

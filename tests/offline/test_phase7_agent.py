"""Connected agent regression: native currency, fresh package gathering, recommendation and final evaluator."""
import os,json,copy
import test_native_currency_plan as native
A=native.A
from types import SimpleNamespace
from package_recommendations import CATEGORIES
from final_evaluation import CRITERIA,PACKAGE_CRITERIA
os.environ.update(PACKAGE_RECOMMENDATIONS_ENABLED='true',FINAL_EVALUATOR_ENABLED='true')
real_clients=A._clients
br,ddb,gateway=real_clients()
row={'packageId':'phase7-p','agencyId':'phase7-a','title':'Cultural Tokyo','agencyName':'Example agency','eligibilityChecked':True,'matchScore':.9,'preferenceScores':dict.fromkeys(('culture','food','nature','comfort','intensity'),9),'price':{'amount':'500','currency':'USD','basis':'per_person'},'currency':'USD','costInclusions':dict.fromkeys(CATEGORIES,True),'additionalCosts':{},'included':['Hotel and meals'],'excluded':['Optional purchases'],'contactEmail':'agency@example.test'}
seen=[]
def call(name,args):
    if name=='search_agency_packages':
        seen.append(copy.deepcopy(args))
        return {'ok':True,'packages':[row]}
    return gateway.call(name,args)
proxy=SimpleNamespace(call=call,tools=gateway.tools)
A._clients=lambda:(A._bedrock,ddb,proxy)
old_converse=A._bedrock.converse
def converse(**kwargs):
    system=native.fixture.sys_text(kwargs)
    if system.startswith('ROLE: agency package recommendation'):
        result={'selected':[{'packageId':'phase7-p','reasons':['Strong cultural fit'],'tradeoffs':['Fixed agency schedule']}]}
    elif system.startswith('Review this travel itinerary independently'):
        result={'scores':dict.fromkeys(CRITERIA+PACKAGE_CRITERIA,4),'issues':[]}
    else: return old_converse(**kwargs)
    return {'output':{'message':{'role':'assistant','content':[{'text':json.dumps(result)}]}},'usage':{'inputTokens':100,'outputTokens':50},'stopReason':'end_turn'}
A._bedrock=SimpleNamespace(converse=converse)
class Ctx:
    session_id='phase7-connected'
    request_headers=native.fixture.Ctx.request_headers
items=list(A.invoke({'prompt':'Six-day Tokyo trip for two, 10 to 15 December 2026, budget USD 80000.','budget':{'amount':'80000','currency':'USD'},'packageMode':'compare'},Ctx()))
it=[e['itinerary'] for e in items if e['type']=='itinerary'][-1]
assert it['verified'] and it['pdfUrl'] and it['currency']=='USD',items
assert it['packageRecommendations']['status']=='recommended',it
assert it['packageRecommendations']['packages'][0]['comparison']['estimatedTotal']=='1000',it
assert set(it['finalEvaluation']['scores'])==set(CRITERIA+PACKAGE_CRITERIA),it
assert len(seen)>=2 and all('budgetSGD' not in q for q in seen)
assert 'Agency package facts' in A._summary(A.SESSIONS[Ctx.session_id]) and 'phase7-p' in A._summary(A.SESSIONS[Ctx.session_id])
# A later change repeats the catalogue read; currency and package review stay attached to the new version.
follow=list(A.invoke({'prompt':'Find a cheaper hotel'},Ctx()))
changed=[e['itinerary'] for e in follow if e['type']=='itinerary'][-1]
assert changed['version']==2 and changed['packageRecommendations']['packages'] and len(seen)>=3,follow
print('PHASE 7 CONNECTED AGENT OK: fresh packages, native totals, recommendation, evaluator, PDF, follow-up')

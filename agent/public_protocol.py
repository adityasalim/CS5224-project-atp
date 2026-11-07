"""Session-scoped optimized website protocol. No global agent-function patching."""
import copy,os,time,threading
from contextlib import contextmanager
from model_adapter import ModelAdapter

def _seconds(name,default):
 try:return max(10,int(os.environ.get(name,default)))
 except ValueError:return default

# Read timeout for Bedrock calls on the website path (Kimi K3 reviewer/evaluator use non-streaming invoke_model and
# exceeded both the old 45 s and 120 s). Override with PUBLIC_BEDROCK_READ_TIMEOUT_SECONDS.
BEDROCK_READ_TIMEOUT_SECONDS=_seconds('PUBLIC_BEDROCK_READ_TIMEOUT_SECONDS',240)
PLANNING="""Before returning the existing JSON schema, silently audit your choices:
1. Copy candidate IDs exactly. Respect the selected flight's actual local arrival DATE and departure DATE. Days before arrival have zero stops. Allow immigration, airport transport and rest before sightseeing; protect the final airport departure buffer.
2. Cover every requested interest with actual suitable candidates. A generic historic site is not a temple. Prefer well-reviewed suitable venues over low-rated supermarkets for a food experience. Do not invent a romantic evening, nightlife, cuisine, hotel stars or accessibility unsupported by the selected items.
3. Group nearby venues; avoid backtracking and long taxi day trips unless necessary for an explicit request. Do not claim transit fares or verified opening hours when absent. Budget estimates are assumptions, not itemized quotes.
4. Choose travel duration and usable days as well as price when flights fit the budget. Arrival and moving days must be realistic. Themes must describe the actual stops.
5. Treat explicit numeric pax and dated legs as authoritative over inconsistent descriptive labels, but disclose conflicts rather than silently altering the traveller's supplied facts.
6. Check a global list of used IDs across ALL days: never repeat a restaurant, market or other place. Explicitly include a suitable temple-category candidate when temple is requested and available. Do not substitute a historic category for a temple.\n7. Dates before actual landing are outbound-travel days, not arrival days. For overnight arrivals leave the actual arrival morning for rest. Prefer a daytime arrival if within budget. Avoid grocery stores as food experiences and avoid treating a castle and its same-site museum as two independent attractions.\nReturn only the unchanged plan JSON; do not output the audit."""
REVIEW="""Identify concrete defects using supplied evidence only. Distinguish itinerary problems from wording problems. Suggestions must name the day and affected choice. Missing evidence is unknown, never proof of suitability. Keep the original scoring scale; never increase a score to reach a target."""
WRITING="""Use the supplied verified itinerary and reviewer feedback as an immutable handoff. Correct wording and explain limitations only; do not claim to fix a flight, stop, route or timing by changing prose. Do not invent evening activities, venue quality, opening hours, transfer prices, reservations or preferences met. Mention relevant evidence gaps concisely. Preserve the original narrative JSON schema."""

LONG_TRIP_GUIDANCE="For long single-city trips: choose usable travel days and comfort within the FULL budget, comparing actual flight durations/times rather than choosing price alone. Do not call an overnight option comfortable. Hotels have review scores, not verified luxury/romance attributes. Plan a varied mix of the requested cultural and food interests: avoid grocery/convenience shops as food experiences; a temple explicitly labeled Jain, Hindu or Sai Baba does not evidence Japanese temple culture. Use the available Japanese temple candidates. Never put two full meals back-to-back; normally at most one meal venue per day in this compact plan, alternating museum/temple days with nearby food. Do not fill every date with poor matches to use all candidates; transparent free time is preferable. Explain unsupported romance, hours, transport fares and limited candidate choices rather than inventing them."
_KIMI_LOCK=threading.Lock()
_KIMI_LAST=0.

def long_trip(spec):
 from datetime import date
 return not spec.get('legs') and (date.fromisoformat(spec['endDate'])-date.fromisoformat(spec['startDate'])).days>=7

def remap(value,mapping):
 if isinstance(value,str):return mapping.get(value,value)
 if isinstance(value,list):return [remap(v,mapping) for v in value]
 if isinstance(value,dict):return {mapping.get(k,k):remap(v,mapping) for k,v in value.items()}
 return value

def aliases_for(pool):
 return {item[field]:prefix+str(n) for category,field,prefix in [('flights','offerId','F'),('stays','stayId','H'),('places','placeId','P'),('transfers','transferId','T')] for n,item in enumerate(pool.get(category,[]),1)}

class PublicProtocolAdapter(ModelAdapter):
 strict_no_retry=True
 def __init__(self,*args,protocol_revision='executor-v6',**kwargs):
  super().__init__(*args,**kwargs)
  if protocol_revision not in ('executor-v6','long-trip-v8'):raise ValueError('Unsupported website protocol')
  self.protocol_revision=protocol_revision
  self.operation=None;self.spec={};self.feedback={};self.deterministic_edits=[]
 @contextmanager
 def context(self,operation,spec):
  previous=self.operation,self.spec
  self.operation,self.spec=operation,spec
  try:yield
  finally:self.operation,self.spec=previous
 def _client(self,provider):
  if provider in self.clients:return self.clients[provider]
  if provider=='bedrock':
   import boto3
   from botocore.config import Config
   client=boto3.client('bedrock-runtime',region_name='ap-southeast-1',config=Config(connect_timeout=10,read_timeout=BEDROCK_READ_TIMEOUT_SECONDS,retries={'total_max_attempts':1}))
  elif provider=='alibaba':client=super()._client(provider).with_options(timeout=45,max_retries=0)
  else:client=super()._client(provider)
  self.clients[provider]=client
  return client
 def plan_request(self,request,spec,pool,*facts,operation):
  mapping=aliases_for(pool)
  with self.context(operation,spec):
   result=request(remap(spec,mapping),remap(copy.deepcopy(pool),mapping),*(remap(f,mapping) for f in facts))
  result=remap(result,{v:k for k,v in mapping.items()})
  if self.protocol_revision=='long-trip-v8' and long_trip(spec) and isinstance(result,dict):
   result=copy.deepcopy(result);seen=set()
   for day in result.get('days',[]):
    ids=[]
    for pid in day.get('placeIds',[]):
     if pid in seen:self.deterministic_edits.append({'date':day.get('date'),'placeId':pid,'action':'remove_duplicate'})
     else:seen.add(pid);ids.append(pid)
    day['placeIds']=ids
  return result
 def generate(self,**kwargs):
  global _KIMI_LAST
  role=kwargs['role'];selected=self.configuration.selections[role]
  if selected.provider=='bedrock' and selected.model_id.endswith('moonshotai.kimi-k3'):
   with _KIMI_LOCK:
    delay=max(0.,45-(time.monotonic()-_KIMI_LAST))
    if delay:time.sleep(delay)
    _KIMI_LAST=time.monotonic()
  extra=PLANNING if self.operation=='proposal' else REVIEW if self.operation=='review' else "Implement the supplied itinerary corrections using only exact pool IDs. Preserve unrelated choices and the existing plan JSON schema. Never repeat any venue ID. This is a plan editing task, not narrative writing." if self.operation=='correction' else WRITING if self.operation=='narrative' else ''
  if self.protocol_revision=='long-trip-v8' and self.operation in ('proposal','correction') and long_trip(self.spec):extra+='\n'+LONG_TRIP_GUIDANCE
  if self.operation=='narrative':extra+='\nReviewer handoff: '+__import__('json').dumps({'scores':self.feedback.get('scores'),'suggestions':self.feedback.get('suggestions',[])})
  if extra:
   system=kwargs['system'];kwargs['system']=(list(system)+[extra]) if isinstance(system,list) else system+'\n'+extra
  if self.operation=='proposal':
   kwargs['messages']=copy.deepcopy(kwargs['messages'])
   kwargs['messages'][-1]['content'].append({'text':'Produce one feasible, preference-matched plan. Check actual flight dates, exact candidate IDs, neighborhood grouping and supported claims before answering.'})
  kwargs['cache_system_prefix']=False
  return super().generate(**kwargs)

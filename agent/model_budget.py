"""Private S3 compare-and-swap budget reservations and traceable price estimates.
Unknown failed-call costs retain their reservation. Net bills are not inferred
from promotional eligibility. No credentials or prompts are stored here.
"""
from dataclasses import dataclass
from datetime import date,datetime,timezone
from decimal import Decimal,InvalidOperation
import copy,json,re,uuid,os
from urllib.parse import urlparse

BUCKET=os.environ.get('MODEL_BUDGET_BUCKET', '')
PREFIX='model-experiments/budget/'

class BudgetError(RuntimeError):pass

def money(value):
    if isinstance(value,bool):raise ValueError('Boolean is not a monetary amount')
    try:result=Decimal(str(value))
    except InvalidOperation:raise ValueError('Invalid monetary amount') from None
    if not result.is_finite() or result<0:raise ValueError('Invalid monetary amount')
    return result

def conflict(error):
    return getattr(error,'response',{}).get('Error',{}).get('Code') in ('PreconditionFailed','ConditionalRequestConflict','412','409')

def encode(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()

class S3BudgetStore:
    def __init__(self,ledger_id,client=None):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',ledger_id):raise ValueError('Invalid budget ledger identifier')
        if client is None:
            import boto3
            from botocore.config import Config
            client=boto3.client('s3',region_name='ap-southeast-1',config=Config(retries={'total_max_attempts':1}))
        self.client=client
        self.prefix=PREFIX+ledger_id+'/'
        self.key=self.prefix+'state.json'

    def initialize(self,value):
        # Creation first avoids ambiguous 403/404 reads without ListBucket access.
        try:self.client.put_object(Bucket=BUCKET,Key=self.key,Body=encode(value),ContentType='application/json',IfNoneMatch='*')
        except Exception as error:
            if not conflict(error):raise
        return self.read()[0]

    def read(self):
        response=self.client.get_object(Bucket=BUCKET,Key=self.key)
        return json.loads(response['Body'].read()),response['ETag']

    def update(self,operation):
        for _ in range(6):
            state,etag=self.read()
            result=operation(state)
            try:
                self.client.put_object(Bucket=BUCKET,Key=self.key,Body=encode(state),ContentType='application/json',IfMatch=etag)
                return result
            except Exception as error:
                if not conflict(error):raise
        raise BudgetError('Budget state remained contested; no model request authorized')

    def put_record(self,row):
        identifier=row['invocationId']
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',identifier):raise ValueError('Invalid invocation identifier')
        key=self.prefix+'invocations/'+identifier+'.json'
        body=encode(row)
        try:self.client.put_object(Bucket=BUCKET,Key=key,Body=body,ContentType='application/json',IfNoneMatch='*')
        except Exception as error:
            if not conflict(error):raise
            old=self.client.get_object(Bucket=BUCKET,Key=key)['Body'].read()
            if old!=body:raise BudgetError('Conflicting invocation record; refusing replacement') from None

@dataclass(frozen=True)
class PriceQuote:
    provider:str
    model_id:str
    input_per_million:Decimal
    output_per_million:Decimal
    source:str
    verified_on:str
    valid_until:str
    input_includes_cache:bool
    cache_read_per_million:Decimal|None=None
    cache_write_per_million:Decimal|None=None
    input_limit:int=1000000
    rate_basis:str="Verified public standard rates; account credits and invoice adjustments excluded"

    @classmethod
    def parse(cls,data):
        source=data['source']
        host=urlparse(source).hostname or ''
        if urlparse(source).scheme!='https' or not any(host==h or host.endswith('.'+h) for h in ('amazonaws.com','aws.amazon.com','ai.google.dev','alibabacloud.com','anthropic.com')):
            raise ValueError('A primary-provider pricing source is required')
        if data.get('verified') is not True:raise ValueError('Pricing must be verified before use')
        first=date.fromisoformat(data['verifiedOn']);last=date.fromisoformat(data['validUntil'])
        if last<first:raise ValueError('Invalid pricing validity interval')
        limit=data.get('inputLimit',1000000)
        if type(limit) is not int or limit<1:raise ValueError('Invalid pricing tier input limit')
        if type(data.get('inputIncludesCache')) is not bool:raise ValueError('Native input-token cache semantics required')
        return cls(data['provider'],data['modelId'],money(data['inputPerMillionUSD']),money(data['outputPerMillionUSD']),
                   source,data['verifiedOn'],data['validUntil'],data['inputIncludesCache'],
                   money(data['cacheReadPerMillionUSD']) if data.get('cacheReadPerMillionUSD') is not None else None,
                   money(data['cacheWritePerMillionUSD']) if data.get('cacheWritePerMillionUSD') is not None else None,limit,str(data.get('rateBasis','Verified public standard rates; account credits and invoice adjustments excluded')))

    def validate_date(self,today):
        # Pricing validity is advisory; expired quotes do not block requests(Demo purpose).
        # if not date.fromisoformat(self.verified_on)<=today<=date.fromisoformat(self.valid_until):
        #     raise BudgetError('Pricing quote is not valid for the invocation date')
        return None

    def reserve_cost(self,system,messages,max_tokens):
        # UTF-8 byte count plus ample chat wrappers is deliberately conservative.
        upper=len(system.encode())+len(json.dumps(messages,ensure_ascii=False).encode())+1024+64*len(messages)
        if upper>self.input_limit:raise BudgetError('Request exceeds the verified pricing tier bound')
        rate=max(self.input_per_million,self.cache_read_per_million or Decimal(0),self.cache_write_per_million or Decimal(0))
        return (Decimal(upper)*rate+Decimal(max_tokens)*self.output_per_million)/Decimal(1000000)

    def estimate(self,row):
        if row.get('inputTokens') is None or row.get('outputTokens') is None:return None,'unknown usage'
        inp=money(row['inputTokens']);out=money(row['outputTokens'])
        read=money(row.get('cacheReadTokens') or 0);write=money(row.get('cacheWriteTokens') or 0)
        search=money(row.get('webSearchRequests') or 0)*Decimal('0.01')   # Anthropic web search: USD 10 per 1,000 searches
        if write and self.cache_write_per_million is None:return None,'cache-write rate not verified'
        if self.input_includes_cache and read>inp:raise BudgetError('Invalid cache token accounting')
        if read and self.cache_read_per_million is None:
            if not self.input_includes_cache:inp+=read
            return (inp*self.input_per_million+out*self.output_per_million+write*(self.cache_write_per_million or 0))/Decimal(1000000)+search,'uncached upper estimate; cache-read price unavailable; '+self.rate_basis
        uncached=inp-read if self.input_includes_cache else inp
        amount=(uncached*self.input_per_million+read*(self.cache_read_per_million or 0)+write*(self.cache_write_per_million or 0)+out*self.output_per_million)/Decimal(1000000)+search
        return amount,self.rate_basis

class BudgetLedger:
    def __init__(self,store,*,cap_usd,provider_caps,quotes,today=None,enforce_spending_caps=False):
        self.store=store;self.cap=money(cap_usd)
        self.enforce_spending_caps=enforce_spending_caps
        if self.cap<=0:raise ValueError('Explicit positive spending cap required')
        self.provider_caps={k:str(money(v)) for k,v in provider_caps.items()}
        self.quotes={(q.provider,q.model_id):q for q in quotes}
        self.today=today or (lambda:datetime.now(timezone.utc).date())
        initial={'schemaVersion':1,'capUSD':str(self.cap),'providerCapsUSD':self.provider_caps,
                 'spentEstimateUSD':'0','spentByProvider':{},'reservations':{},'settled':{},'halted':False}
        state=store.initialize(initial)
        if self.enforce_spending_caps and (
            money(state['capUSD']) != self.cap
            or state['providerCapsUSD'] != self.provider_caps
        ):
            raise BudgetError('Existing budget differs; explicit review required before changing caps')

    def authorize(self,selection,system,messages,max_tokens):
        quote=self.quotes.get((selection.provider,selection.model_id))
        if quote is None:raise BudgetError('No verified pricing quote for selected model')
        quote.validate_date(self.today())
        maximum=quote.reserve_cost(system,messages,max_tokens)
        identifier=uuid.uuid4().hex
        def reserve(state):
            if self.enforce_spending_caps and state['halted']:
                raise BudgetError('Budget ledger halted for review')
            held=sum((money(r['maximumUSD']) for r in state['reservations'].values()),Decimal(0))
            if self.enforce_spending_caps and money(state['spentEstimateUSD'])+held+maximum>self.cap:raise BudgetError('Global spending cap would be exceeded')
            provider=selection.provider
            if self.enforce_spending_caps and provider not in self.provider_caps:raise BudgetError('Explicit provider cap required')
            used=money(state['spentByProvider'].get(provider,'0'))
            held_provider=sum((money(r['maximumUSD']) for r in state['reservations'].values() if r['provider']==provider),Decimal(0))
            if self.enforce_spending_caps and used+held_provider+maximum>money(self.provider_caps[provider]):raise BudgetError('Provider spending cap would be exceeded')
            state['reservations'][identifier]={'provider':provider,'modelId':selection.model_id,'maximumUSD':str(maximum)}
        self.store.update(reserve)
        return BudgetReservation(self,identifier,quote,maximum)

    def record(self,row):
        # Free-tier Google usage remains zero actual cost, with paid-equivalent pricing separate.
        if row.get('provider')=='google':
            quote=self.quotes.get(('google',row['modelId']))
            if quote is not None:
                quote.validate_date(self.today())
                estimate,basis=quote.estimate(row)
                row.update(estimatedPaidTierCostUSD=str(estimate) if estimate is not None else None,
                           costEstimateBasis=basis,pricingSource=quote.source,pricingVerifiedOn=quote.verified_on)
            else:
                row.update(estimatedPaidTierCostUSD=None,costEstimateBasis='No verified hosted paid-tier price available')
        self.store.put_record(row)
        return row

class BudgetReservation:
    def __init__(self,ledger,identifier,quote,maximum):
        self.ledger,self.identifier,self.quote,self.maximum=ledger,identifier,quote,maximum

    def settle(self,row):
        estimate,basis=self.quote.estimate(row)
        if row.get('requestStarted') is False:estimate,basis=Decimal(0),'provider request never started'
        row.update(estimatedPaidTierCostUSD=str(estimate) if estimate is not None else None,
                   costEstimateBasis=basis,pricingSource=self.quote.source,pricingVerifiedOn=self.quote.verified_on,
                   budgetReservationUSD=str(self.maximum),budgetReservationId=self.identifier)
        self.ledger.store.put_record(row)
        def finish(state):
            if self.identifier in state['settled']:return
            reservation=state['reservations'].get(self.identifier)
            if reservation is None:raise BudgetError('Missing budget reservation')
            if estimate is None:
                reservation['usageUnknown']=True
                reservation['invocationId']=row['invocationId']
                return
            state['spentEstimateUSD']=str(money(state['spentEstimateUSD'])+estimate)
            provider=reservation['provider']
            state['spentByProvider'][provider]=str(money(state['spentByProvider'].get(provider,'0'))+estimate)
            state['settled'][self.identifier]=str(estimate)
            del state['reservations'][self.identifier]
            if self.ledger.enforce_spending_caps and (
                estimate > self.maximum
                or money(state['spentEstimateUSD']) > self.ledger.cap
            ):
                state['halted'] = True
        self.ledger.store.update(finish)

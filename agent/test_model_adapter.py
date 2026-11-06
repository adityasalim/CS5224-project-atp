import unittest
from types import SimpleNamespace as N
from model_configuration import ModelConfiguration
from model_adapter import ModelAdapter,ModelAdapterError
from google_transport import GoogleResult

class Reservation:
    def __init__(self):self.rows=[]
    def settle(self,row):self.rows.append(dict(row))

def config(provider='bedrock',model='test-model'):
    return ModelConfiguration.parse({'experimentId':'isolated-test','googleBillingDisabled':True,
        **{role:{'provider':provider,'model':model} for role in ('planner','executor','reviewer','independent_evaluator')}})

class TestAdapter(unittest.TestCase):
    def call(self,adapter,role='planner'):
        return adapter.generate(role=role,trip_id='trip-1',system=['Context','Role'],messages=[{'role':'user','content':[{'text':'Question'}]}])
    def test_bedrock_role_and_accounting(self):
        seen=[];reservation=Reservation()
        def converse(**kwargs):
            seen.append(kwargs)
            return {'output':{'message':{'content':[{'text':'{"ok":true}'}]}},'usage':{'inputTokens':10,'outputTokens':5},'stopReason':'end_turn'}
        adapter=ModelAdapter(config(),clients={'bedrock':N(converse=converse)},budget_authorizer=lambda *args:reservation)
        response=self.call(adapter,'reviewer')
        row=adapter.records[0]
        self.assertEqual(row['role'],'reviewer');self.assertEqual(row['experimentId'],'isolated-test')
        self.assertEqual(row['tripId'],'trip-1');self.assertEqual(row['inputTokens'],10)
        self.assertFalse(row['fallbackUsed']);self.assertEqual(seen[0]['system'],[{'text':'Context\n\nRole'}])
        self.assertEqual(response['usage']['outputTokens'],5);self.assertEqual(len(reservation.rows),1)
    def test_persisted_cost_enrichment_is_visible_in_result(self):
        def converse(**kwargs):return {'output':{'message':{'content':[{'text':'OK'}]}},'usage':{'inputTokens':10,'outputTokens':5}}
        adapter=ModelAdapter(config(),clients={'bedrock':N(converse=converse)},budget_authorizer=lambda *args:Reservation(),record_sink=lambda row:{**row,'estimatedPaidTierCostUSD':'0.123'})
        result=self.call(adapter)
        self.assertEqual(result['_adapter']['estimatedPaidTierCostUSD'],'0.123')
        self.assertEqual(adapter.records[0]['estimatedPaidTierCostUSD'],'0.123')
    def test_kimi_native_effort_and_cache_accounting(self):
        import io,json
        seen=[]
        def invoke_model(**kwargs):
            seen.append(json.loads(kwargs['body']))
            return {'body':io.BytesIO(json.dumps({'choices':[{'message':{'content':'OK'},'finish_reason':'stop'}],'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120,'prompt_tokens_details':{'cached_tokens':80}}}).encode())}
        adapter=ModelAdapter(config('bedrock','global.moonshotai.kimi-k3'),clients={'bedrock':N(invoke_model=invoke_model)},budget_authorizer=lambda *args:Reservation())
        self.call(adapter,'independent_evaluator')
        self.assertNotIn('temperature',seen[0]);self.assertEqual(seen[0]['reasoning_effort'],'low')
        self.assertEqual(adapter.records[0]['inputTokens'],20);self.assertEqual(adapter.records[0]['cacheReadTokens'],80)
    def test_glm_low_effort_is_explicit_and_non_thinking_rejected(self):
        values={'experimentId':'glm-low','googleBillingDisabled':True,**{r:{'provider':'alibaba','model':'glm-5.3','options':{'reasoningEffort':'low'}} for r in ('planner','executor','reviewer','independent_evaluator')}}
        seen=[]
        def create(**kwargs):
            seen.append(kwargs)
        
            return iter([
                N(
                    model='glm-5.3',
                    choices=[
                        N(
                            delta=N(content='{}'),
                            finish_reason='stop'
                        )
                    ],
                    usage=None
                ),
                N(
                    model='glm-5.3',
                    choices=[],
                    usage=N(
                        prompt_tokens=10,
                        completion_tokens=5,
                        total_tokens=15,
                        prompt_tokens_details=None,
                        completion_tokens_details=None
                    )
                )
            ])
        adapter=ModelAdapter(ModelConfiguration.parse(values),clients={'alibaba':N(chat=N(completions=N(create=create)))},budget_authorizer=lambda *args:Reservation())
        self.call(adapter);self.assertEqual(seen[0]['reasoning_effort'],'low');self.assertNotIn('extra_body',seen[0])
        values['planner']['options']={'enableThinking':False}
        with self.assertRaises(ValueError):ModelConfiguration.parse(values)
    def test_budget_guard_before_paid_call(self):
        adapter=ModelAdapter(config(),clients={'bedrock':None})
        with self.assertRaises(ValueError):self.call(adapter)
        self.assertFalse(adapter.records)
    def test_no_implicit_model_on_missing_role(self):
        with self.assertRaises(ValueError):self.call(ModelAdapter(config()),'unassigned')
    def test_failed_attempt_is_retained_without_fake_tokens(self):
        reservation=Reservation()
        def converse(**kwargs):raise TimeoutError('private provider response')
        adapter=ModelAdapter(config(),clients={'bedrock':N(converse=converse)},budget_authorizer=lambda *args:reservation)
        with self.assertRaises(ModelAdapterError) as error:self.call(adapter)
        self.assertEqual(str(error.exception),'TimeoutError')
        self.assertIsNone(adapter.records[0]['inputTokens']);self.assertFalse(adapter.records[0]['success'])
        self.assertEqual(len(reservation.rows),1)
    def test_quota_error_defers_later_calls_before_budget_or_provider(self):
        class QuotaError(Exception):status_code=429
        def fail(**kwargs):raise QuotaError()
        reservations=[]
        def authorize(*args):reservations.append(1);return Reservation()
        adapter=ModelAdapter(config(),clients={'bedrock':N(converse=fail)},budget_authorizer=authorize)
        with self.assertRaises(ModelAdapterError):self.call(adapter)
        with self.assertRaisesRegex(ModelAdapterError,'Deferred'):self.call(adapter)
        self.assertEqual(len(reservations),1);self.assertEqual(len(adapter.records),1)
    def test_google_thinking_counted_once(self):
        class Google:
            def generate(self,**kwargs):
                return GoogleResult(kwargs['model_id'],'OK','stream',{'inputTokens':5,'outputTokens':1,'thinkingTokens':53,'cacheReadTokens':0,'totalTokens':59},10)
        adapter=ModelAdapter(config('google','gemini-3.8-flash'),clients={'google':Google()})
        result=self.call(adapter)
        self.assertEqual(result['usage']['outputTokens'],54)
        self.assertEqual(adapter.records[0]['nativeOutputTokens'],1)
        self.assertEqual(adapter.records[0]['actualCostUSD'],0)
    def test_glm_compatible_minimal_request(self):
        seen=[];reservation=Reservation()
        def create(**kwargs):
            seen.append(kwargs)
            return N(model='glm-5.3',choices=[N(message=N(content='OK'),finish_reason='stop')],
                     usage=N(prompt_tokens=10,completion_tokens=3,total_tokens=13,prompt_tokens_details=None,completion_tokens_details=None))
        adapter=ModelAdapter(config('alibaba','glm-5.3'),clients={'alibaba':N(chat=N(completions=N(create=create)))},budget_authorizer=lambda *args:reservation)
        self.call(adapter,'executor')
        self.assertNotIn('temperature',seen[0]);self.assertNotIn('response_format',seen[0]);self.assertNotIn('extra_body',seen[0])
    def test_configuration_rejects_missing_role_and_credentials(self):
        with self.assertRaises(ValueError):ModelConfiguration.parse({'experimentId':'x'})
        raw={'experimentId':'x','googleBillingDisabled':True,**{r:{'provider':'bedrock','model':'x'} for r in ('planner','executor','reviewer','independent_evaluator')}}
        raw['api_key']='not-a-real-key'
        with self.assertRaises(ValueError):ModelConfiguration.parse(raw)


class TestExplicitPrefixCache(unittest.TestCase):
    def test_sonnet_context_checkpoint_and_actual_usage_are_preserved(self):
        seen=[]
        def converse(**kwargs):
            seen.append(kwargs)
            return {'output':{'message':{'content':[{'text':'OK'}]}},'usage':{'inputTokens':20,'outputTokens':2,'cacheWriteInputTokens':2000,'cacheReadInputTokens':0}}
        adapter=ModelAdapter(config(model='global.anthropic.claude-sonnet-4-6'),clients={'bedrock':N(converse=converse)},budget_authorizer=lambda *args:Reservation())
        response=adapter.generate(role='planner',trip_id='cache-contract',system=['Fixed pool','Planner instructions'],messages=[{'role':'user','content':[{'text':'Plan'}]}],cache_system_prefix=True)
        self.assertEqual(seen[0]['system'],[{'text':'Fixed pool'},{'cachePoint':{'type':'default'}},{'text':'Planner instructions'}])
        self.assertEqual(response['_adapter']['cacheWriteTokens'],2000)
        self.assertTrue(response['_adapter']['requestOptions']['cacheSystemPrefix'])
    def test_unverified_model_cache_request_is_rejected_without_provider_call(self):
        adapter=ModelAdapter(config(model='test-model'))
        with self.assertRaises(ValueError):
            adapter.generate(role='planner',trip_id='cache-contract',system=['Pool','Role'],messages=[{'role':'user','content':[{'text':'Plan'}]}],cache_system_prefix=True)
        self.assertEqual(adapter.records,[])

if __name__=='__main__':unittest.main()

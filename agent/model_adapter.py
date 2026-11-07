"""Role-based text generation through supported SDKs, isolated from the legacy default.
No model substitution. Budget authorization is a required callback for paid-capable
providers; verified pricing and persistent spending limits belong in that callback.
"""
import json,time,uuid,hashlib,os
from datetime import datetime,timezone
from google_transport import GoogleTransport,GoogleTransportError
from model_configuration import ModelConfiguration, ALIBABA_MODELS   # ALIBABA_MODELS: one list, defined in model_configuration
import logging

ALIBABA_SECRET=os.environ.get('ALIBABA_SECRET_ID', '')
# Request timeouts. A planner reply can be up to 8000 tokens, which takes longer than the old 90 s on a large trip.
ALIBABA_TIMEOUT_SECONDS=float(os.environ.get('ALIBABA_TIMEOUT_SECONDS', '180'))
BEDROCK_READ_TIMEOUT_SECONDS=int(os.environ.get('BEDROCK_READ_TIMEOUT_SECONDS', '180'))

class ModelAdapterError(RuntimeError):
    def __init__(self,kind,code=None):
        super().__init__(kind)
        self.code=code

def system_text(system):
    if isinstance(system,str):return system
    if isinstance(system,list) and all(isinstance(s,str) for s in system):return '\n\n'.join(system)
    raise ValueError('System must be text or a list of text sections')

class ModelAdapter:
    def __init__(self,configuration,*,clients=None,budget_authorizer=None,record_sink=None):
        if not isinstance(configuration,ModelConfiguration):raise TypeError('ModelConfiguration required')
        self.configuration=configuration
        self.configuration_sha256=hashlib.sha256(json.dumps({role:{'provider':s.provider,'modelId':s.model_id,'options':s.options} for role,s in configuration.selections.items()},sort_keys=True).encode()).hexdigest()
        self.clients=dict(clients or {})
        self.budget_authorizer=budget_authorizer
        self.record_sink=record_sink
        self.records=[]
        self.quota_exhausted=False

    def _client(self,provider):
        if provider in self.clients:return self.clients[provider]
        if provider=='google':
            result=GoogleTransport.from_secret(billing_disabled=self.configuration.google_billing_disabled)
        elif provider=='bedrock':
            import boto3
            from botocore.config import Config
            result=boto3.client('bedrock-runtime',region_name='ap-southeast-1',
                config=Config(connect_timeout=10,read_timeout=BEDROCK_READ_TIMEOUT_SECONDS,retries={'total_max_attempts':1}))
        else:
            import boto3
            from openai import OpenAI
            raw=boto3.client('secretsmanager',region_name='ap-southeast-1').get_secret_value(SecretId=ALIBABA_SECRET)['SecretString']
            try:value=json.loads(raw)
            except ValueError:value={'api_key':raw}
            key=next((value.get(k) for k in ('api_key','API_KEY','DASHSCOPE_API_KEY','ALIBABA_API_KEY') if value.get(k)),None)
            if not isinstance(key,str) or not key:raise ValueError('Alibaba secret lacks a supported API-key field')
            result=OpenAI(api_key=key,base_url='https://dashscope-intl.aliyuncs.com/compatible-mode/v1',timeout=ALIBABA_TIMEOUT_SECONDS,max_retries=0)
            def log_request(request):
                logging.getLogger(__name__).warning(
                    "Alibaba HTTP request: timeout=%s",
                    request.extensions.get("timeout")
                )
            
            result._client.event_hooks["request"].append(log_request)
            logging.getLogger(__name__).warning(
                "Alibaba timeout diagnostics: configured=%s client=%s http=%s",
                ALIBABA_TIMEOUT_SECONDS,
                result.timeout,
                result._client.timeout,
            )
        self.clients[provider]=result
        return result

    def generate(self,*,role,trip_id,system,messages,max_tokens=3000,temperature=.2,tools=None,cache_system_prefix=False):
        if self.quota_exhausted:raise ModelAdapterError('ExperimentDeferredAfterQuotaExhaustion',429)
        if role not in self.configuration.selections:raise ValueError('Unknown or unattributed model role')
        if not isinstance(trip_id,str) or not trip_id:raise ValueError('trip_id is required')
        if tools:raise ValueError('Text adapter does not silently discard tool schemas; explicit tool conversion is required')
        selected=self.configuration.selections[role]
        if selected.provider=='alibaba' and selected.model_id not in ALIBABA_MODELS:
            raise ValueError('Alibaba model lacks verified experiment-pool eligibility')
        if type(max_tokens) is not int or not 1<=max_tokens<=8000:raise ValueError('Invalid bounded output limit')
        sections=list(system) if isinstance(system,list) else [system]
        system=system_text(system)
        if cache_system_prefix and (selected.provider!='bedrock' or selected.model_id!='global.anthropic.claude-sonnet-4-6' or len(sections)<2):
            raise ValueError('Prefix caching requires the explicitly supported Sonnet profile and separate context/instruction sections')
        system_blocks=([{'text':sections[0]},{'cachePoint':{'type':'default'}},{'text':'\n\n'.join(sections[1:])}] if cache_system_prefix else [{'text':system}])
        for message in messages:
            if message.get('role') not in ('user','assistant') or not message.get('content') or any(
                    set(block)!={'text'} or not isinstance(block['text'],str) for block in message['content']):
                raise ValueError('An explicit text message format is required')
        row={'invocationId':str(uuid.uuid4()),'experimentId':self.configuration.experiment_id,
             'tripId':trip_id,'role':role,'provider':selected.provider,'modelId':selected.model_id,
             'requestedModelId':selected.model_id,'fallbackUsed':False,'startedAt':datetime.now(timezone.utc).isoformat(),'inputTokens':None,'outputTokens':None,
             'thinkingTokens':None,'cacheReadTokens':None,'cacheWriteTokens':None,'latencyMs':None,
             'actualCostUSD':None,'estimatedPaidTierCostUSD':None,'success':False,'requestStarted':False,
             'requestOptions':dict(selected.options),'maxOutputTokens':max_tokens,'configurationSHA256':self.configuration_sha256,
             'temperatureRequested':temperature,'temperatureApplied':None}
        if cache_system_prefix:row['requestOptions']['cacheSystemPrefix']=True
        reservation=None
        if selected.provider!='google':
            if self.budget_authorizer is None:
                raise ValueError('Verified pricing and budget authorization required before paid-capable calls')
            reservation=self.budget_authorizer(selected,system,messages,max_tokens)
            if reservation is None or not hasattr(reservation,'settle'):
                raise ValueError('A spending reservation is required before provider invocation')
        start=time.monotonic()
        try:
            client=self._client(selected.provider)
            row['requestStarted']=True
            if selected.provider=='google':
                result=client.generate(model_id=selected.model_id,system_prompt=system,messages=messages,
                    max_tokens=max_tokens,thinking_level=selected.options.get('thinkingLevel'))
                text=result.text;usage=result.usage
                row.update(inputTokens=usage.get('inputTokens'),outputTokens=usage.get('outputTokens'),
                           thinkingTokens=usage.get('thinkingTokens'),cacheReadTokens=usage.get('cacheReadTokens'),
                           actualCostUSD=result.actual_cost_usd,transport=result.transport,totalTokens=usage.get('totalTokens'))
            elif selected.provider=='bedrock' and selected.model_id.endswith('moonshotai.kimi-k3'):
                native=[{'role':'system','content':system}]+[{'role':m['role'],'content':'\n'.join(b['text'] for b in m['content'])} for m in messages]
                effort=selected.options.get('reasoningEffort','low')
                response=client.invoke_model(modelId=selected.model_id,body=json.dumps({'messages':native,'max_tokens':max_tokens,'reasoning_effort':effort}))
                result=json.loads(response['body'].read())
                text=result['choices'][0]['message'].get('content') or ''
                usage=result.get('usage',{})
                cached=(usage.get('prompt_tokens_details') or {}).get('cached_tokens')
                native_input=usage.get('prompt_tokens')
                row.update(inputTokens=native_input-cached if native_input is not None and cached is not None else native_input,
                           nativeInputTokens=native_input,nativeInputIncludesCache=True,outputTokens=usage.get('completion_tokens'),
                           cacheReadTokens=cached,thinkingTokens=(usage.get('completion_tokens_details') or {}).get('reasoning_tokens'),
                           totalTokens=usage.get('total_tokens'),finishReason=result['choices'][0].get('finish_reason'),transport='bedrock.InvokeModel',reasoningEffort=effort)
            elif selected.provider=='bedrock':
                config={'maxTokens':max_tokens}
                value=selected.options.get('temperature',None if selected.model_id.endswith('moonshotai.kimi-k3') else temperature)
                if value is not None:config['temperature']=value
                row['temperatureApplied']=value
                result=client.converse(modelId=selected.model_id,system=system_blocks,messages=messages,inferenceConfig=config)
                usage=result.get('usage',{})
                text=''.join(b.get('text','') for b in result['output']['message']['content'])
                row.update(inputTokens=usage.get('inputTokens'),outputTokens=usage.get('outputTokens'),
                           cacheReadTokens=usage.get('cacheReadInputTokens'),cacheWriteTokens=usage.get('cacheWriteInputTokens'),
                           totalTokens=usage.get('totalTokens'),finishReason=result.get('stopReason'))
            else:
                native=[{'role':'system','content':system}]+[
                    {'role':m['role'],'content':'\n'.join(b['text'] for b in m['content'])} for m in messages]
                kwargs={'model':selected.model_id,'messages':native,'max_tokens':max_tokens}
                value=selected.options.get('temperature',None if selected.model_id=='glm-5.3' else temperature)
                if value is not None:kwargs['temperature']=value
                row['temperatureApplied']=value
                # GLM's minimal request is the currently verified compatible shape.
                extra_body={}
                if 'enableThinking' in selected.options:extra_body['enable_thinking']=selected.options['enableThinking']
                if 'thinkingBudget' in selected.options:
                    # Separate reasoning cap: with thinking_budget set, max_tokens limits only the final answer.
                    # Qwen 3.8 rejects reasoning_effort together with thinking_budget, so effort is skipped below.
                    extra_body['enable_thinking']=True
                    extra_body['thinking_budget']=selected.options['thinkingBudget']
                    row['thinkingBudget']=selected.options['thinkingBudget']
                if extra_body:kwargs['extra_body']=extra_body
                if 'reasoningEffort' in selected.options and 'thinkingBudget' not in selected.options:kwargs['reasoning_effort']=selected.options['reasoningEffort']
                logging.getLogger(__name__).warning(
                    "Alibaba request diagnostics: model=%s timeout=%s",
                    selected.model_id,
                    getattr(client, "timeout", None),
                )
                
                # Stream responses for both Alibaba models.
                kwargs['stream'] = True
                kwargs['stream_options'] = {'include_usage': True}

                stream = client.chat.completions.create(**kwargs)

                content_parts = []
                usage = None
                finish_reason = None
                response_model = selected.model_id
                received_finish = False
                received_usage = False

                for chunk in stream:
                    if chunk.model:
                        response_model = chunk.model

                    if chunk.usage is not None:
                        usage = chunk.usage
                        received_usage = True

                    if not chunk.choices:
                        continue

                    choice = chunk.choices[0]
                    delta = choice.delta

                    if delta.content:
                        content_parts.append(delta.content)

                    # Do not collect or log reasoning_content.
                    if choice.finish_reason is not None:
                        finish_reason = choice.finish_reason
                        received_finish = True

                # A partial stream must not be treated as a
                # successful complete response.
                if not received_finish or not received_usage:
                    raise ModelAdapterError(
                        'IncompleteStreamingResponse'
                    )

                text = ''.join(content_parts)

                if usage is not None:
                    row.update(
                        inputTokens=usage.prompt_tokens,
                        outputTokens=usage.completion_tokens,
                        totalTokens=usage.total_tokens,
                        cacheReadTokens=getattr(
                            getattr(usage, 'prompt_tokens_details', None),
                            'cached_tokens', None
                        ),
                        thinkingTokens=getattr(
                            getattr(usage, 'completion_tokens_details', None),
                            'reasoning_tokens', None
                        )
                    )

                row.update(
                    finishReason=finish_reason,
                    responseModelId=response_model,
                    transport='alibaba.streaming'
                )

            if not text.strip():
                # A reasoning model can spend its whole output budget thinking and return no text. That is a
                # truncation (the caller can retry with a larger limit), not a hard failure.
                if row.get('finishReason') not in ('length','max_tokens'):raise ModelAdapterError('EmptyModelOutput')
                row['emptyBecauseTruncated']=True
            if row['inputTokens'] is None or row['outputTokens'] is None:
                raise ModelAdapterError('MissingTokenUsage')
            if selected.provider=='google':
                row['nativeOutputTokens']=row['outputTokens']
                thoughts=row['thinkingTokens'] or 0
                if thoughts:
                    total=row.get('totalTokens')
                    if total==row['inputTokens']+row['outputTokens']+thoughts:
                        row['outputTokens']+=thoughts
                    elif total!=row['inputTokens']+row['outputTokens']:
                        raise ModelAdapterError('AmbiguousGoogleTokenUsage')
            row['success']=True
            return {'output':{'message':{'role':'assistant','content':[{'text':text}]}},
                    'usage':{'inputTokens':row['inputTokens'] or 0,'outputTokens':row['outputTokens'] or 0,
                             'cacheReadInputTokens':row['cacheReadTokens'] or 0,'cacheWriteInputTokens':row['cacheWriteTokens'] or 0},
                    'stopReason':'max_tokens' if row.get('finishReason') in ('length','max_tokens') else 'end_turn',
                    '_adapter':row}
        except Exception as error:
            logger = logging.getLogger(__name__)
            logger.error(
                "Model request failed: provider=%s model=%s "
                "role=%s latency=%.1fs error=%s cause=%r",
                selected.provider,
                selected.model_id,
                role,
                time.monotonic() - start,
                type(error).__name__,
                error.__cause__ or error.__context__,
                exc_info=True,
            )

            if isinstance(error, GoogleTransportError) and error.usage:
                row.update({k:v for k,v in error.usage.items() if k in row})

            detail = (
                getattr(error, 'response', {}).get('Error', {})
                if isinstance(getattr(error, 'response', None), dict)
                else {}
            )

            kind = (
                str(error)
                if isinstance(error, ModelAdapterError)
                else type(error).__name__
            )

            row.update(
                errorType=kind,
                errorCode=(
                    getattr(error, 'code', None)
                    or getattr(error, 'status_code', None)
                    or detail.get('Code')
                )
            )

            if str(row['errorCode']) in (
                '429', 'RESOURCE_EXHAUSTED', 'ResourceExhausted'
            ):
                self.quota_exhausted = True

            raise ModelAdapterError(kind, row['errorCode']) from None
        finally:
            row['latencyMs']=round((time.monotonic()-start)*1000)
            try:
                if reservation is not None:reservation.settle(row)
            finally:
                self.records.append(row)
                if self.record_sink is not None:
                    enriched=self.record_sink(dict(row))
                    if isinstance(enriched,dict):row.update(enriched)

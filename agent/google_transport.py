"""Google SDK text transport for isolated experiments; not enabled on Runtime 9.
No automatic model substitution, tool execution, retries, or billing changes.
"""
from dataclasses import dataclass, asdict
import time
import os

MODELS = {'gemini-3.8-flash', 'gemini-3.5-flash-lite', 'gemma-4-31b-it'}
SECRET = os.environ.get('GOOGLE_SECRET_ID', '')

class GoogleTransportError(RuntimeError):
    def __init__(self, kind, code=None, usage=None):
        super().__init__(kind)
        self.code, self.usage = code, usage

@dataclass
class GoogleResult:
    model_id: str
    text: str
    transport: str
    usage: dict
    latency_ms: int
    actual_cost_usd: int = 0

    def to_dict(self):
        return asdict(self)

def _interaction_usage(value):
    if value is None:
        return _usage(None)
    return {"inputTokens": getattr(value, "total_input_tokens", None),
            "outputTokens": getattr(value, "total_output_tokens", None),
            "thinkingTokens": getattr(value, "total_thought_tokens", None),
            "cacheReadTokens": getattr(value, "total_cached_tokens", None),
            "totalTokens": getattr(value, "total_tokens", None)}

def _usage(value):
    if value is None:
        return {'inputTokens': None, 'outputTokens': None, 'thinkingTokens': None,
                'cacheReadTokens': None, 'totalTokens': None}
    return {'inputTokens': getattr(value, 'prompt_token_count', None),
            'outputTokens': getattr(value, 'candidates_token_count', None),
            'thinkingTokens': getattr(value, 'thoughts_token_count', None),
            'cacheReadTokens': getattr(value, 'cached_content_token_count', None),
            'totalTokens': getattr(value, 'total_token_count', None)}

def interactions_without_retries(resource):
    """Pinned SDK compatibility guard: its two API stacks interpret attempts differently.
    Change only this client instance and fail closed if its configuration changes.
    The real-SDK mock-HTTP contract verifies exactly one outbound request.
    """
    configuration = getattr(resource, 'sdk_configuration', None)
    retry = getattr(configuration, 'retry_config', None)
    if not hasattr(retry, 'max_retries') or not hasattr(retry, 'retry_connection_errors'):
        raise GoogleTransportError('UnsupportedSDKRetryConfiguration')
    retry.max_retries = 0
    retry.retry_connection_errors = False
    return resource

class GoogleTransport:
    def __init__(self, client, *, billing_disabled, timeout_seconds=90):
        if billing_disabled is not True:
            raise ValueError('Verified billing-disabled project required for Google experiments')
        if type(timeout_seconds) not in (int, float) or not 10 <= timeout_seconds <= 300:
            raise ValueError('A bounded Google request timeout is required')
        self.client = client
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_secret(cls, *, billing_disabled, timeout_seconds=90):
        if billing_disabled is not True:
            raise ValueError('Billing-disabled project confirmation required before credential access')
        import json
        from importlib.metadata import version
        if version('google-genai') != '2.34.0':
            raise ValueError('Google SDK version must pass the pinned request-count contract before use')
        import boto3
        from google import genai
        value = boto3.client('secretsmanager', region_name='ap-southeast-1').get_secret_value(SecretId=SECRET)
        key = json.loads(value['SecretString']).get('api_key')
        if not isinstance(key, str) or not key:
            raise ValueError('Google secret requires api_key')
        if type(timeout_seconds) not in (int, float) or not 10 <= timeout_seconds <= 300:
            raise ValueError('A bounded Google request timeout is required')
        client = genai.Client(api_key=key, http_options={
            'api_version': 'v1beta', 'timeout': int(timeout_seconds*1000), 'retry_options': {'attempts': 0}})
        return cls(client, billing_disabled=True, timeout_seconds=timeout_seconds)

    def generate(self, *, model_id, system_prompt, messages, max_tokens=3000,
                 thinking_level=None, tools=None):
        if model_id not in MODELS:
            raise ValueError('Model is outside the verified Google experiment pool')
        if tools:
            raise ValueError('This text transport does not implement tool calling; use an explicit tool adapter')
        if type(max_tokens) is not int or not 1 <= max_tokens <= 8000:
            raise ValueError('Output limit must be between 1 and 8000 tokens')
        if thinking_level is not None:
            levels = {'minimal', 'high'} if model_id.startswith('gemma') else {'low', 'medium', 'high'}
            if model_id == 'gemini-3.5-flash-lite' or thinking_level not in levels:
                raise ValueError('Unsupported or unverified thinking configuration for this model')
        if not isinstance(system_prompt, str):
            raise ValueError('System prompt must be text')
        contents = []
        for message in messages:
            if message.get('role') not in ('user', 'assistant'):
                raise ValueError('Only user and assistant text messages are supported')
            parts = []
            for block in message.get('content', []):
                if set(block) != {'text'} or not isinstance(block['text'], str):
                    raise ValueError('Non-text message requires an explicit provider conversion')
                parts.append({'text': block['text']})
            if not parts:
                raise ValueError('Message content cannot be empty')
            contents.append({'role': 'model' if message['role'] == 'assistant' else 'user', 'parts': parts})
        if not contents:
            raise ValueError('At least one message is required')
        config = {'max_output_tokens': max_tokens, 'automatic_continuation': False}
        if system_prompt:
            if not isinstance(system_prompt, str):
                raise ValueError('System prompt must be text')
            config['system_instruction'] = system_prompt
        if thinking_level is not None:
            config['thinking_config'] = {'thinking_level': thinking_level}
        if model_id == 'gemma-4-31b-it':
            return self._interactions(model_id, system_prompt, messages, max_tokens, thinking_level or 'minimal')
        started = time.monotonic()
        text, usage = [], None
        try:
            for chunk in self.client.models.generate_content_stream(model=model_id, contents=contents, config=config):
                if chunk.usage_metadata is not None:
                    usage = chunk.usage_metadata  # Cumulative snapshot; never sum repeated chunks.
                for candidate in chunk.candidates or []:
                    for part in (candidate.content.parts if candidate.content else []) or []:
                        if getattr(part, 'function_call', None):
                            raise GoogleTransportError('UnexpectedFunctionCall', usage=_usage(usage))
                        if part.text and not part.thought:
                            text.append(part.text)
        except GoogleTransportError:
            raise
        except Exception as error:
            # Preserve useful partial usage without exposing SDK errors, prompts or credentials.
            raise GoogleTransportError(type(error).__name__, getattr(error, 'code', None) or getattr(error, 'status_code', None), _usage(usage)) from None
        answer = ''.join(text)
        if not answer.strip():
            raise GoogleTransportError('EmptyModelOutput', usage=_usage(usage))
        return GoogleResult(model_id, answer, 'streamGenerateContent/v1beta',
                            _usage(usage), round((time.monotonic()-started)*1000))

    def _interactions(self, model_id, system_prompt, messages, max_tokens, thinking_level):
        history = [{'type': 'model_output' if m['role'] == 'assistant' else 'user_input',
                    'content': [{'type': 'text', 'text': b['text']} for b in m['content']]}
                   for m in messages]
        native_input = messages[0]['content'][0]['text'] if len(messages)==1 and messages[0]['role']=='user' and len(messages[0]['content'])==1 else history
        kwargs = {'model': model_id, 'input': native_input, 'store': False, 'automatic_continuation': False, 'timeout': self.timeout_seconds,
                  'generation_config': {'max_output_tokens': max_tokens, 'thinking_level': thinking_level}}
        if system_prompt:
            kwargs['system_instruction'] = system_prompt
        started = time.monotonic()
        try:
            resource = interactions_without_retries(self.client.interactions)
            response = resource.create(**kwargs)
        except Exception as error:
            raise GoogleTransportError(type(error).__name__, getattr(error, 'code', None) or getattr(error, 'status_code', None), _usage(None)) from None
        usage = _interaction_usage(getattr(response, 'usage', None))
        answer = getattr(response, 'output_text', '') or ''
        if not answer.strip():
            raise GoogleTransportError('EmptyModelOutput', usage=usage)
        return GoogleResult(model_id, answer, 'interactions.create/v1beta', usage,
                            round((time.monotonic()-started)*1000))


"""Explicit per-role experiment selections; no implicit default or model fallback."""
from dataclasses import dataclass
import json

ROLES = ('planner', 'executor', 'reviewer', 'independent_evaluator')
PROVIDERS = {'bedrock', 'google', 'alibaba'}
# Single source of truth for the Alibaba (DashScope) models allowed in an experiment or on the website.
# model_adapter.py imports this set, and parse() rejects any other Alibaba model when the config is loaded,
# so a mismatch fails at start-up instead of on the first planner call.
ALIBABA_MODELS = {'qwen3.8-max', 'qwen3.8-max-0902', 'qwen3.8-flash', 'qwen3.7-plus',
                  'deepseek-v4.1-flash-1017', 'deepseek-v4-pro-0813', 'glm-5.3'}

@dataclass(frozen=True)
class ModelSelection:
    provider: str
    model_id: str
    options: dict

@dataclass(frozen=True)
class ModelConfiguration:
    experiment_id: str
    selections: dict
    google_billing_disabled: bool

    @classmethod
    def load(cls, path):
        with open(path, encoding='utf-8') as source:
            return cls.parse(json.load(source))

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict) or not isinstance(data.get('experimentId'), str) or not data['experimentId'].strip():
            raise ValueError('experimentId is required')
        if set(data) - {'experimentId', 'googleBillingDisabled', *ROLES}:
            raise ValueError('Unexpected configuration fields; credentials and defaults are forbidden')
        selections = {}
        for role in ROLES:
            row = data.get(role)
            if not isinstance(row, dict) or row.get('provider') not in PROVIDERS:
                raise ValueError('Explicit provider selection required for ' + role)
            model = row.get('model')
            if not isinstance(model, str) or not model.strip():
                raise ValueError('Explicit model required for ' + role)
            if set(row) - {'provider', 'model', 'options'}:
                raise ValueError('Unexpected selection fields; credentials and implicit fallbacks are forbidden')
            if row['provider'] == 'alibaba' and model not in ALIBABA_MODELS:
                raise ValueError(f'Alibaba model {model} for {role} is not in the verified experiment pool')
            options = row.get('options', {})
            if not isinstance(options, dict) or set(options) - {'thinkingLevel', 'enableThinking', 'temperature','reasoningEffort'}:
                raise ValueError('Unsupported provider options')
            if 'temperature' in options and options['temperature'] is not None and (
                    type(options['temperature']) not in (int, float) or not 0 <= options['temperature'] <= 1):
                raise ValueError('temperature must be null or between zero and one')
            if row['provider'] == 'google' and options.get('temperature') is not None:
                raise ValueError('Custom Google sampling settings are not enabled in this adapter')
            if 'thinkingLevel' in options and row['provider'] != 'google':
                raise ValueError('thinkingLevel is a Google-specific option')
            if 'thinkingLevel' in options and (not isinstance(options['thinkingLevel'], str) or not options['thinkingLevel'].strip()):
                raise ValueError('thinkingLevel must be a non-empty string')
            if 'enableThinking' in options and (row['provider'] != 'alibaba' or type(options['enableThinking']) is not bool):
                raise ValueError('enableThinking must be an Alibaba boolean option')
            if row['provider'] == 'google' and data.get('googleBillingDisabled') is not True:
                raise ValueError('Google experiments require billing-disabled confirmation')
            if row['provider']=='bedrock' and model.endswith('moonshotai.kimi-k3') and options.get('temperature') is not None:
                raise ValueError('Kimi sampling is not enabled on the verified native request shape')
            if row['provider']=='alibaba' and model=='glm-5.3' and options.get('enableThinking') is False:
                raise ValueError('GLM 5.3 thinking cannot be disabled; use a supported reasoningEffort')
            if 'reasoningEffort' in options:
                supported=(('none','low','medium','high','xhigh','max') if row['provider']=='bedrock' and model.endswith('moonshotai.kimi-k3') else ('low','high','max') if row['provider']=='alibaba' and model=='glm-5.3' else ())
                if options['reasoningEffort'] not in supported:raise ValueError('Unsupported provider reasoning effort')
            selections[role] = ModelSelection(row['provider'], model, dict(options))
        return cls(data['experimentId'].strip(), selections, data.get('googleBillingDisabled') is True)

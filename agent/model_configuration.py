
"""Explicit per-role experiment selections; no implicit default or model fallback."""
from dataclasses import dataclass
import json

ROLES = ('planner', 'executor', 'reviewer', 'independent_evaluator')
PROVIDERS = {'bedrock', 'google', 'alibaba', 'anthropic'}

# Single source of truth for Alibaba (DashScope) models.
ALIBABA_MODELS = {
    'qwen3.8-max',
    'qwen3.8-max-0902',
    'qwen3.8-flash',
    'qwen3.7-plus',
    'deepseek-v4.1-flash-1017',
    'deepseek-v4-pro-0813',
    'glm-5.3',
    'deepseek-v4.1-flash',
}

QWEN38_MODELS = {
    'qwen3.8-max',
    'qwen3.8-max-0902',
    'qwen3.8-flash',
}

DEEPSEEK_V41_MODELS = {
    'deepseek-v4.1-flash',
    'deepseek-v4.1-flash-1017',
}

REASONING_EFFORTS = {
    'qwen38': {'low', 'medium', 'high', 'xhigh'},
    'deepseek_v41': {'low', 'medium', 'high', 'max'},
    'glm53': {'low', 'high', 'max'},
    'kimi_k3': {'none', 'low', 'medium', 'high', 'xhigh', 'max'},
}

# Upper bound accepted for a separate reasoning (thinking) token budget.
MAX_THINKING_BUDGET = 16384


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
        if (
            not isinstance(data, dict)
            or not isinstance(data.get('experimentId'), str)
            or not data['experimentId'].strip()
        ):
            raise ValueError('experimentId is required')

        if set(data) - {'experimentId', 'googleBillingDisabled', *ROLES}:
            raise ValueError(
                'Unexpected configuration fields; credentials and defaults are forbidden'
            )

        selections = {}

        for role in ROLES:
            row = data.get(role)

            if not isinstance(row, dict) or row.get('provider') not in PROVIDERS:
                raise ValueError('Explicit provider selection required for ' + role)

            provider = row['provider']
            model = row.get('model')

            if not isinstance(model, str) or not model.strip():
                raise ValueError('Explicit model required for ' + role)

            if set(row) - {'provider', 'model', 'options'}:
                raise ValueError(
                    'Unexpected selection fields; credentials and implicit fallbacks are forbidden'
                )

            if provider == 'alibaba' and model not in ALIBABA_MODELS:
                raise ValueError(
                    f'Alibaba model {model} for {role} is not in the verified experiment pool'
                )

            options = row.get('options', {})

            if (
                not isinstance(options, dict)
                or set(options) - {
                    'thinkingLevel',
                    'enableThinking',
                    'thinkingBudget',
                    'temperature',
                    'reasoningEffort',
                }
            ):
                raise ValueError('Unsupported provider options')

            if (
                'temperature' in options
                and options['temperature'] is not None
                and (
                    type(options['temperature']) not in (int, float)
                    or not 0 <= options['temperature'] <= 1
                )
            ):
                raise ValueError(
                    'temperature must be null or between zero and one'
                )

            if (
                provider == 'google'
                and options.get('temperature') is not None
            ):
                raise ValueError(
                    'Custom Google sampling settings are not enabled in this adapter'
                )

            if 'thinkingLevel' in options and provider != 'google':
                raise ValueError('thinkingLevel is a Google-specific option')

            if (
                'thinkingLevel' in options
                and (
                    not isinstance(options['thinkingLevel'], str)
                    or not options['thinkingLevel'].strip()
                )
            ):
                raise ValueError('thinkingLevel must be a non-empty string')

            if 'enableThinking' in options and (
                provider != 'alibaba'
                or type(options['enableThinking']) is not bool
            ):
                raise ValueError(
                    'enableThinking must be an Alibaba boolean option'
                )

            if 'thinkingBudget' in options:
                if provider != 'alibaba' or model not in QWEN38_MODELS:
                    raise ValueError(
                        'thinkingBudget is supported only for Qwen 3.8 models on Alibaba'
                    )

                budget = options['thinkingBudget']

                if (
                    type(budget) is not int
                    or not 1 <= budget <= MAX_THINKING_BUDGET
                ):
                    raise ValueError(
                        f'thinkingBudget must be an integer from 1 to {MAX_THINKING_BUDGET}'
                    )

                if 'reasoningEffort' in options:
                    raise ValueError(
                        'thinkingBudget and reasoningEffort cannot be combined'
                    )

                if options.get('enableThinking') is False:
                    raise ValueError(
                        'thinkingBudget requires thinking to be enabled'
                    )

            if (
                provider == 'google'
                and data.get('googleBillingDisabled') is not True
            ):
                raise ValueError(
                    'Google experiments require billing-disabled confirmation'
                )

            if (
                provider == 'bedrock'
                and model.endswith('moonshotai.kimi-k3')
                and options.get('temperature') is not None
            ):
                raise ValueError(
                    'Kimi sampling is not enabled on the verified native request shape'
                )

            if (
                provider == 'alibaba'
                and model == 'glm-5.3'
                and options.get('enableThinking') is False
            ):
                raise ValueError(
                    'GLM 5.3 thinking cannot be disabled; use a supported reasoningEffort'
                )

            if 'reasoningEffort' in options:
                supported = set()

                if provider == 'bedrock' and model.endswith('moonshotai.kimi-k3'):
                    supported = REASONING_EFFORTS['kimi_k3']

                elif provider == 'alibaba':
                    if model in QWEN38_MODELS:
                        supported = REASONING_EFFORTS['qwen38']

                    elif model in DEEPSEEK_V41_MODELS:
                        supported = REASONING_EFFORTS['deepseek_v41']

                    elif model == 'glm-5.3':
                        supported = REASONING_EFFORTS['glm53']

                elif provider == 'anthropic':
                    supported = {'low', 'medium', 'high', 'xhigh', 'max'}

                if options['reasoningEffort'] not in supported:
                    raise ValueError(
                        'Unsupported provider reasoning effort'
                    )

            selections[role] = ModelSelection(
                provider,
                model,
                dict(options)
            )

        return cls(
            data['experimentId'].strip(),
            selections,
            data.get('googleBillingDisabled') is True
        )

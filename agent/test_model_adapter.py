
import unittest
from types import SimpleNamespace as N

from model_configuration import ModelConfiguration
from model_adapter import ModelAdapter, ModelAdapterError
from google_transport import GoogleResult


class Reservation:
    def __init__(self):
        self.rows = []

    def settle(self, row):
        self.rows.append(dict(row))


def config(provider='bedrock', model='test-model', options=None):
    return ModelConfiguration.parse({
        'experimentId': 'isolated-test',
        'googleBillingDisabled': True,
        **{
            role: {
                'provider': provider,
                'model': model,
                **({'options': dict(options)} if options is not None else {})
            }
            for role in (
                'planner',
                'executor',
                'reviewer',
                'independent_evaluator'
            )
        }
    })


def stream_chunk(
    content=None,
    finish_reason=None,
    model='test-model',
    reasoning_content=None
):
    """Simulate a streamed Alibaba chat completion chunk."""
    return N(
        model=model,
        choices=[
            N(
                delta=N(
                    content=content,
                    reasoning_content=reasoning_content
                ),
                finish_reason=finish_reason
            )
        ],
        usage=None
    )


def usage_chunk(
    model='test-model',
    input_tokens=100,
    output_tokens=20,
    thinking_tokens=8,
    cache_read_tokens=0
):
    """Simulate Alibaba's final streaming usage chunk."""
    return N(
        model=model,
        choices=[],
        usage=N(
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            prompt_tokens_details=N(
                cached_tokens=cache_read_tokens
            ),
            completion_tokens_details=N(
                reasoning_tokens=thinking_tokens
            )
        )
    )


def completed_stream(
    text='OK',
    model='test-model',
    input_tokens=100,
    output_tokens=20,
    thinking_tokens=8
):
    """Return an iterable with content, finish, and usage."""
    return iter([
        stream_chunk(
            reasoning_content='internal reasoning',
            model=model
        ),
        stream_chunk(
            content=text,
            model=model
        ),
        stream_chunk(
            content=None,
            finish_reason='stop',
            model=model
        ),
        usage_chunk(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            thinking_tokens=thinking_tokens
        )
    ])


class TestAdapter(unittest.TestCase):

    def call(self, adapter, role='planner'):
        return adapter.generate(
            role=role,
            trip_id='trip-1',
            system=['Context', 'Role'],
            messages=[
                {
                    'role': 'user',
                    'content': [{'text': 'Question'}]
                }
            ]
        )

    def test_bedrock_role_and_accounting(self):
        seen = []
        reservation = Reservation()

        def converse(**kwargs):
            seen.append(kwargs)
            return {
                'output': {
                    'message': {
                        'content': [{'text': '{"ok":true}'}]
                    }
                },
                'usage': {
                    'inputTokens': 10,
                    'outputTokens': 5
                },
                'stopReason': 'end_turn'
            }

        adapter = ModelAdapter(
            config(),
            clients={'bedrock': N(converse=converse)},
            budget_authorizer=lambda *args: reservation
        )

        response = self.call(adapter, 'reviewer')
        row = adapter.records[0]

        self.assertEqual(row['role'], 'reviewer')
        self.assertEqual(row['experimentId'], 'isolated-test')
        self.assertEqual(row['tripId'], 'trip-1')
        self.assertEqual(row['inputTokens'], 10)
        self.assertFalse(row['fallbackUsed'])

        self.assertEqual(
            seen[0]['system'],
            [{'text': 'Context\n\nRole'}]
        )

        self.assertEqual(response['usage']['outputTokens'], 5)
        self.assertEqual(len(reservation.rows), 1)

    def test_persisted_cost_enrichment_is_visible_in_result(self):

        def converse(**kwargs):
            return {
                'output': {
                    'message': {
                        'content': [{'text': 'OK'}]
                    }
                },
                'usage': {
                    'inputTokens': 10,
                    'outputTokens': 5
                }
            }

        adapter = ModelAdapter(
            config(),
            clients={'bedrock': N(converse=converse)},
            budget_authorizer=lambda *args: Reservation(),
            record_sink=lambda row: {
                **row,
                'estimatedPaidTierCostUSD': '0.123'
            }
        )

        result = self.call(adapter)

        self.assertEqual(
            result['_adapter']['estimatedPaidTierCostUSD'],
            '0.123'
        )
        self.assertEqual(
            adapter.records[0]['estimatedPaidTierCostUSD'],
            '0.123'
        )

    def test_kimi_native_effort_and_cache_accounting(self):
        import io
        import json

        seen = []

        def invoke_model(**kwargs):
            seen.append(json.loads(kwargs['body']))

            return {
                'body': io.BytesIO(
                    json.dumps({
                        'choices': [{
                            'message': {'content': 'OK'},
                            'finish_reason': 'stop'
                        }],
                        'usage': {
                            'prompt_tokens': 100,
                            'completion_tokens': 20,
                            'total_tokens': 120,
                            'prompt_tokens_details': {
                                'cached_tokens': 80
                            }
                        }
                    }).encode()
                )
            }

        adapter = ModelAdapter(
            config('bedrock', 'global.moonshotai.kimi-k3'),
            clients={'bedrock': N(invoke_model=invoke_model)},
            budget_authorizer=lambda *args: Reservation()
        )

        self.call(adapter, 'independent_evaluator')

        self.assertNotIn('temperature', seen[0])
        self.assertEqual(seen[0]['reasoning_effort'], 'low')
        self.assertEqual(adapter.records[0]['inputTokens'], 20)
        self.assertEqual(adapter.records[0]['cacheReadTokens'], 80)

    def test_glm_low_effort_is_explicit_and_non_thinking_rejected(self):
        values = {
            'experimentId': 'glm-low',
            'googleBillingDisabled': True,
            **{
                r: {
                    'provider': 'alibaba',
                    'model': 'glm-5.3',
                    'options': {'reasoningEffort': 'low'}
                }
                for r in (
                    'planner',
                    'executor',
                    'reviewer',
                    'independent_evaluator'
                )
            }
        }

        seen = []

        def create(**kwargs):
            seen.append(kwargs)
            return completed_stream(
                text='{}',
                model='glm-5.3',
                input_tokens=10,
                output_tokens=5
            )

        adapter = ModelAdapter(
            ModelConfiguration.parse(values),
            clients={
                'alibaba': N(
                    chat=N(
                        completions=N(create=create)
                    )
                )
            },
            budget_authorizer=lambda *args: Reservation()
        )

        self.call(adapter)

        self.assertEqual(seen[0]['reasoning_effort'], 'low')
        self.assertNotIn('extra_body', seen[0])
        self.assertTrue(seen[0]['stream'])
        self.assertEqual(
            seen[0]['stream_options'],
            {'include_usage': True}
        )

        values['planner']['options'] = {
            'enableThinking': False
        }

        with self.assertRaises(ValueError):
            ModelConfiguration.parse(values)

    def test_budget_guard_before_paid_call(self):
        adapter = ModelAdapter(
            config(),
            clients={'bedrock': None}
        )

        with self.assertRaises(ValueError):
            self.call(adapter)

        self.assertFalse(adapter.records)

    def test_no_implicit_model_on_missing_role(self):
        with self.assertRaises(ValueError):
            self.call(
                ModelAdapter(config()),
                'unassigned'
            )

    def test_failed_attempt_is_retained_without_fake_tokens(self):
        reservation = Reservation()

        def converse(**kwargs):
            raise TimeoutError('private provider response')

        adapter = ModelAdapter(
            config(),
            clients={'bedrock': N(converse=converse)},
            budget_authorizer=lambda *args: reservation
        )

        with self.assertRaises(ModelAdapterError) as error:
            self.call(adapter)

        self.assertEqual(str(error.exception), 'TimeoutError')
        self.assertIsNone(adapter.records[0]['inputTokens'])
        self.assertFalse(adapter.records[0]['success'])
        self.assertEqual(len(reservation.rows), 1)

    def test_quota_error_defers_later_calls_before_budget_or_provider(self):

        class QuotaError(Exception):
            status_code = 429

        def fail(**kwargs):
            raise QuotaError()

        reservations = []

        def authorize(*args):
            reservations.append(1)
            return Reservation()

        adapter = ModelAdapter(
            config(),
            clients={'bedrock': N(converse=fail)},
            budget_authorizer=authorize
        )

        with self.assertRaises(ModelAdapterError):
            self.call(adapter)

        with self.assertRaisesRegex(ModelAdapterError, 'Deferred'):
            self.call(adapter)

        self.assertEqual(len(reservations), 1)
        self.assertEqual(len(adapter.records), 1)

    def test_google_thinking_counted_once(self):

        class Google:
            def generate(self, **kwargs):
                return GoogleResult(
                    kwargs['model_id'],
                    'OK',
                    'stream',
                    {
                        'inputTokens': 5,
                        'outputTokens': 1,
                        'thinkingTokens': 53,
                        'cacheReadTokens': 0,
                        'totalTokens': 59
                    },
                    10
                )

        adapter = ModelAdapter(
            config('google', 'gemini-3.8-flash'),
            clients={'google': Google()}
        )

        result = self.call(adapter)

        self.assertEqual(result['usage']['outputTokens'], 54)
        self.assertEqual(
            adapter.records[0]['nativeOutputTokens'],
            1
        )
        self.assertEqual(
            adapter.records[0]['actualCostUSD'],
            0
        )

    def test_glm_compatible_minimal_request(self):
        seen = []
        reservation = Reservation()

        def create(**kwargs):
            seen.append(kwargs)

            return completed_stream(
                text='OK',
                model='glm-5.3',
                input_tokens=10,
                output_tokens=3
            )

        adapter = ModelAdapter(
            config('alibaba', 'glm-5.3'),
            clients={
                'alibaba': N(
                    chat=N(
                        completions=N(create=create)
                    )
                )
            },
            budget_authorizer=lambda *args: reservation
        )

        self.call(adapter, 'executor')

        self.assertNotIn('temperature', seen[0])
        self.assertNotIn('response_format', seen[0])
        self.assertNotIn('extra_body', seen[0])

        self.assertTrue(seen[0]['stream'])
        self.assertEqual(
            seen[0]['stream_options'],
            {'include_usage': True}
        )

        self.assertEqual(len(reservation.rows), 1)

    def test_configuration_rejects_missing_role_and_credentials(self):
        with self.assertRaises(ValueError):
            ModelConfiguration.parse({
                'experimentId': 'x'
            })

        raw = {
            'experimentId': 'x',
            'googleBillingDisabled': True,
            **{
                r: {
                    'provider': 'bedrock',
                    'model': 'x'
                }
                for r in (
                    'planner',
                    'executor',
                    'reviewer',
                    'independent_evaluator'
                )
            }
        }

        raw['api_key'] = 'not-a-real-key'

        with self.assertRaises(ValueError):
            ModelConfiguration.parse(raw)


class TestAlibabaStreaming(unittest.TestCase):
    """Offline streaming tests for Qwen and DeepSeek."""

    def make_adapter(
        self,
        model,
        options=None,
        create=None,
        reservation=None
    ):
        if reservation is None:
            reservation = Reservation()

        if create is None:
            def create(**kwargs):
                return completed_stream(
                    text='{"ok":true}',
                    model=model
                )

        client = N(
            chat=N(
                completions=N(create=create)
            )
        )

        adapter = ModelAdapter(
            config(
                provider='alibaba',
                model=model,
                options=options
            ),
            clients={'alibaba': client},
            budget_authorizer=lambda *args: reservation
        )

        return adapter, reservation

    def call(self, adapter, role='planner'):
        return adapter.generate(
            role=role,
            trip_id='streaming-test',
            system='You are a travel planner.',
            messages=[{
                'role': 'user',
                'content': [{'text': 'Create a plan'}]
            }],
            max_tokens=1000
        )

    def test_qwen_streaming_with_medium_thinking(self):
        seen = []

        def create(**kwargs):
            seen.append(kwargs)
            return completed_stream(
                text='{"days":[1,2,3]}',
                model='qwen3.8-max-0902',
                input_tokens=150,
                output_tokens=40,
                thinking_tokens=15
            )

        adapter, reservation = self.make_adapter(
            model='qwen3.8-max-0902',
            options={
                'enableThinking': True,
                'reasoningEffort': 'medium'
            },
            create=create
        )

        result = self.call(adapter, 'planner')
        row = adapter.records[0]

        self.assertTrue(seen[0]['stream'])
        self.assertEqual(
            seen[0]['stream_options'],
            {'include_usage': True}
        )

        self.assertEqual(
            seen[0]['extra_body'],
            {'enable_thinking': True}
        )

        self.assertEqual(
            seen[0]['reasoning_effort'],
            'medium'
        )

        self.assertEqual(
            result['output']['message']['content'][0]['text'],
            '{"days":[1,2,3]}'
        )

        self.assertEqual(row['inputTokens'], 150)
        self.assertEqual(row['outputTokens'], 40)
        self.assertEqual(row['thinkingTokens'], 15)
        self.assertEqual(row['transport'], 'alibaba.streaming')
        self.assertTrue(row['success'])
        self.assertEqual(len(reservation.rows), 1)

    def test_deepseek_streaming_with_low_thinking(self):
        seen = []

        def create(**kwargs):
            seen.append(kwargs)
            return completed_stream(
                text='{"status":"done"}',
                model='deepseek-v4.1-flash',
                input_tokens=80,
                output_tokens=30,
                thinking_tokens=10
            )

        adapter, reservation = self.make_adapter(
            model='deepseek-v4.1-flash',
            options={
                'enableThinking': True,
                'reasoningEffort': 'low'
            },
            create=create
        )

        result = self.call(adapter, 'executor')
        row = adapter.records[0]

        self.assertTrue(seen[0]['stream'])
        self.assertEqual(
            seen[0]['stream_options'],
            {'include_usage': True}
        )

        self.assertEqual(
            seen[0]['reasoning_effort'],
            'low'
        )

        self.assertEqual(
            seen[0]['extra_body'],
            {'enable_thinking': True}
        )

        self.assertEqual(
            result['output']['message']['content'][0]['text'],
            '{"status":"done"}'
        )

        self.assertEqual(row['inputTokens'], 80)
        self.assertEqual(row['outputTokens'], 30)
        self.assertEqual(row['thinkingTokens'], 10)
        self.assertTrue(row['success'])
        self.assertEqual(len(reservation.rows), 1)

    def test_stream_combines_multiple_content_chunks(self):

        def create(**kwargs):
            return iter([
                stream_chunk(
                    content='{"days":',
                    model='qwen3.8-max-0902'
                ),
                stream_chunk(
                    content='[1,2,3]}',
                    model='qwen3.8-max-0902',
                    finish_reason='stop'
                ),
                usage_chunk(
                    model='qwen3.8-max-0902'
                )
            ])

        adapter, _ = self.make_adapter(
            model='qwen3.8-max-0902',
            create=create
        )

        result = self.call(adapter)

        text = result['output']['message']['content'][0]['text']

        self.assertEqual(text, '{"days":[1,2,3]}')
        self.assertTrue(adapter.records[0]['success'])

    def test_reasoning_content_is_not_in_final_response(self):

        def create(**kwargs):
            return iter([
                stream_chunk(
                    reasoning_content='private reasoning text',
                    model='qwen3.8-max-0902'
                ),
                stream_chunk(
                    content='FINAL ANSWER',
                    finish_reason='stop',
                    model='qwen3.8-max-0902'
                ),
                usage_chunk(
                    model='qwen3.8-max-0902'
                )
            ])

        adapter, _ = self.make_adapter(
            model='qwen3.8-max-0902',
            create=create
        )

        result = self.call(adapter)

        text = result['output']['message']['content'][0]['text']

        self.assertEqual(text, 'FINAL ANSWER')
        self.assertNotIn('private reasoning', text)

    def test_stream_missing_final_usage_is_rejected(self):
        reservation = Reservation()

        def create(**kwargs):
            return iter([
                stream_chunk(
                    content='Partial answer',
                    finish_reason='stop',
                    model='qwen3.8-max-0902'
                )
            ])

        adapter, _ = self.make_adapter(
            model='qwen3.8-max-0902',
            create=create,
            reservation=reservation
        )

        with self.assertRaisesRegex(
            ModelAdapterError,
            'IncompleteStreamingResponse'
        ):
            self.call(adapter)

        row = adapter.records[0]

        self.assertFalse(row['success'])
        self.assertIsNone(row['inputTokens'])
        self.assertEqual(len(reservation.rows), 1)

    def test_stream_missing_finish_reason_is_rejected(self):

        def create(**kwargs):
            return iter([
                stream_chunk(
                    content='Partial answer',
                    model='deepseek-v4.1-flash'
                ),
                usage_chunk(
                    model='deepseek-v4.1-flash'
                )
            ])

        adapter, reservation = self.make_adapter(
            model='deepseek-v4.1-flash',
            create=create
        )

        with self.assertRaisesRegex(
            ModelAdapterError,
            'IncompleteStreamingResponse'
        ):
            self.call(adapter, 'executor')

        self.assertFalse(adapter.records[0]['success'])
        self.assertEqual(len(reservation.rows), 1)

    def test_interrupted_stream_is_not_accepted(self):
        reservation = Reservation()

        def create(**kwargs):
            def chunks():
                yield stream_chunk(
                    content='Partial output',
                    model='qwen3.8-max-0902'
                )
                raise TimeoutError('Stream interrupted')

            return chunks()

        adapter, _ = self.make_adapter(
            model='qwen3.8-max-0902',
            create=create,
            reservation=reservation
        )

        with self.assertRaisesRegex(
            ModelAdapterError,
            'TimeoutError'
        ):
            self.call(adapter)

        row = adapter.records[0]

        self.assertFalse(row['success'])
        self.assertEqual(row['errorType'], 'TimeoutError')
        self.assertEqual(len(reservation.rows), 1)

    def test_empty_stream_is_rejected(self):

        def create(**kwargs):
            return iter([])

        adapter, _ = self.make_adapter(
            model='qwen3.8-max-0902',
            create=create
        )

        with self.assertRaisesRegex(
            ModelAdapterError,
            'IncompleteStreamingResponse'
        ):
            self.call(adapter)

        self.assertFalse(adapter.records[0]['success'])

    def test_stream_without_output_text_is_rejected(self):

        def create(**kwargs):
            return iter([
                stream_chunk(
                    reasoning_content='thinking only',
                    finish_reason='stop',
                    model='qwen3.8-max-0902'
                ),
                usage_chunk(
                    model='qwen3.8-max-0902'
                )
            ])

        adapter, _ = self.make_adapter(
            model='qwen3.8-max-0902',
            create=create
        )

        with self.assertRaisesRegex(
            ModelAdapterError,
            'EmptyModelOutput'
        ):
            self.call(adapter)

        self.assertFalse(adapter.records[0]['success'])

    def test_stream_truncated_by_output_limit(self):

        def create(**kwargs):
            return iter([
                stream_chunk(
                    content='',
                    finish_reason='length',
                    model='qwen3.8-max-0902'
                ),
                usage_chunk(
                    model='qwen3.8-max-0902'
                )
            ])

        adapter, _ = self.make_adapter(
            model='qwen3.8-max-0902',
            create=create
        )

        result = self.call(adapter)

        self.assertTrue(
            result['_adapter']['emptyBecauseTruncated']
        )
        self.assertTrue(result['_adapter']['success'])
        self.assertEqual(result['stopReason'], 'max_tokens')


class TestExplicitPrefixCache(unittest.TestCase):

    def test_sonnet_context_checkpoint_and_actual_usage_are_preserved(self):
        seen = []

        def converse(**kwargs):
            seen.append(kwargs)
            return {
                'output': {
                    'message': {
                        'content': [{'text': 'OK'}]
                    }
                },
                'usage': {
                    'inputTokens': 20,
                    'outputTokens': 2,
                    'cacheWriteInputTokens': 2000,
                    'cacheReadInputTokens': 0
                }
            }

        adapter = ModelAdapter(
            config(
                model='global.anthropic.claude-sonnet-4-6'
            ),
            clients={'bedrock': N(converse=converse)},
            budget_authorizer=lambda *args: Reservation()
        )

        response = adapter.generate(
            role='planner',
            trip_id='cache-contract',
            system=['Fixed pool', 'Planner instructions'],
            messages=[{
                'role': 'user',
                'content': [{'text': 'Plan'}]
            }],
            cache_system_prefix=True
        )

        self.assertEqual(
            seen[0]['system'],
            [
                {'text': 'Fixed pool'},
                {'cachePoint': {'type': 'default'}},
                {'text': 'Planner instructions'}
            ]
        )

        self.assertEqual(
            response['_adapter']['cacheWriteTokens'],
            2000
        )

        self.assertTrue(
            response['_adapter']['requestOptions']['cacheSystemPrefix']
        )

    def test_unverified_model_cache_request_is_rejected_without_provider_call(self):
        adapter = ModelAdapter(
            config(model='test-model')
        )

        with self.assertRaises(ValueError):
            adapter.generate(
                role='planner',
                trip_id='cache-contract',
                system=['Pool', 'Role'],
                messages=[{
                    'role': 'user',
                    'content': [{'text': 'Plan'}]
                }],
                cache_system_prefix=True
            )

        self.assertEqual(adapter.records, [])


if __name__ == '__main__':
    unittest.main()

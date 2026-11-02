import unittest
from types import SimpleNamespace as N
from google_transport import GoogleTransport, GoogleTransportError

class TestGoogleTransport(unittest.TestCase):
    def make(self, chunks):
        seen=[]
        def generate(**kwargs):
            seen.append(kwargs)
            yield from chunks
        return GoogleTransport(N(models=N(generate_content_stream=generate)), billing_disabled=True), seen
    def chunk(self,text,usage=None,thought=False):
        return N(usage_metadata=usage,candidates=[N(content=N(parts=[N(text=text,thought=thought,function_call=None)]))])
    def call(self,transport,**kwargs):
        return transport.generate(model_id='gemini-3.8-flash',system_prompt='System',messages=[{'role':'user','content':[{'text':'Hello'}]}],**kwargs)
    def test_stream_usage_and_thoughts(self):
        first=N(prompt_token_count=10,candidates_token_count=1,thoughts_token_count=3,cached_content_token_count=0,total_token_count=14)
        last=N(prompt_token_count=10,candidates_token_count=2,thoughts_token_count=3,cached_content_token_count=0,total_token_count=15)
        t,seen=self.make([self.chunk('private thought',first,True),self.chunk('O'),self.chunk('K',last)])
        result=self.call(t,thinking_level='low')
        self.assertEqual(result.text,'OK')
        self.assertEqual(result.usage['totalTokens'],15)
        self.assertEqual(result.usage['thinkingTokens'],3)
        self.assertEqual(seen[0]['config']['thinking_config'],{'thinking_level':'low'})
        self.assertNotIn('temperature',seen[0]['config'])
    def test_flash_rejects_unsupported_minimal_before_call(self):
        t,seen=self.make([])
        with self.assertRaises(ValueError):self.call(t,thinking_level='minimal')
        self.assertFalse(seen)
    def test_missing_usage_stays_unknown(self):
        t,_=self.make([self.chunk('OK')])
        self.assertIsNone(self.call(t).usage['inputTokens'])
    def test_no_tools_silently_dropped(self):
        t,seen=self.make([])
        with self.assertRaises(ValueError):self.call(t,tools=[{'name':'tool'}])
        self.assertFalse(seen)
    def test_project_guard(self):
        with self.assertRaises(ValueError):GoogleTransport(None,billing_disabled=False)
    def test_partial_failure_preserves_usage(self):
        usage=N(prompt_token_count=7,candidates_token_count=1,thoughts_token_count=2,cached_content_token_count=0,total_token_count=10)
        def chunks():
            yield self.chunk('partial',usage)
            raise TimeoutError('sensitive provider message')
        t,_=self.make(chunks())
        with self.assertRaises(GoogleTransportError) as error:self.call(t)
        self.assertEqual(str(error.exception),'TimeoutError')
        self.assertEqual(error.exception.usage['inputTokens'],7)
    def test_gemma_uses_stateless_interactions(self):
        seen=[]
        def create(**kwargs):
            seen.append(kwargs)
            return N(output_text='OK', usage=N(total_input_tokens=5,total_output_tokens=1,total_thought_tokens=0,total_cached_tokens=0,total_tokens=6))
        transport=GoogleTransport(N(interactions=N(create=create,sdk_configuration=N(retry_config=N(max_retries=4,retry_connection_errors=True)))),billing_disabled=True)
        result=transport.generate(model_id='gemma-4-31b-it',system_prompt='System',messages=[{'role':'user','content':[{'text':'Hello'}]}])
        self.assertEqual(result.text,'OK')
        self.assertEqual(result.transport,'interactions.create/v1beta')
        self.assertFalse(seen[0]['store'])
        self.assertEqual(seen[0]['generation_config']['thinking_level'],'minimal')
        self.assertEqual(seen[0]['input'],'Hello')
        self.assertFalse(seen[0]['automatic_continuation'])
        self.assertEqual(seen[0]['timeout'],90)
        self.assertEqual(result.usage['totalTokens'],6)
        self.assertEqual(transport.client.interactions.sdk_configuration.retry_config.max_retries,0)
        self.assertFalse(transport.client.interactions.sdk_configuration.retry_config.retry_connection_errors)
    def test_empty_thought_only_is_not_success(self):
        t,_=self.make([self.chunk('reasoning',thought=True)])
        with self.assertRaises(GoogleTransportError):self.call(t)

if __name__=='__main__':unittest.main()

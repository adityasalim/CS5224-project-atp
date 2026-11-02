"""Real pinned SDK + mock HTTP: prove no hidden retries or remote requests."""
import httpx,json,inspect,sys,pathlib
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/'agent'))
from google_transport import interactions_without_retries
from google import genai
from google.genai import types
from importlib.metadata import version
assert version('google-genai')=='2.34.0'
for method in ('streamGenerateContent','interactions'):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(500,json={'error':{'code':500,'status':'INTERNAL','message':'Offline forced error'}},request=request)
    client=genai.Client(api_key='offline-test-key',http_options=types.HttpOptions(
        base_url='https://offline.invalid',timeout=3000,retry_options=types.HttpRetryOptions(attempts=0),
        client_args={'transport':httpx.MockTransport(handler)}))
    try:
        if method=='interactions':
            interactions_without_retries(client.interactions).create(model='gemma-4-31b-it',input='OK',store=False,
                generation_config={'max_output_tokens':16},automatic_continuation=False,timeout=3)
        else:
            list(client.models.generate_content_stream(model='gemini-3.8-flash',contents='OK',config={'max_output_tokens':16,'automatic_continuation':False}))
    except Exception as error:
        error_type=type(error).__name__
    else:raise AssertionError('Forced error was not raised')
    print('SDK_DIAGNOSTIC',json.dumps({'method':method,'paths':[(r.method,r.url.path) for r in calls], 'interactionType':str(type(client.interactions)), 'signature':str(inspect.signature(client.interactions.create)), 'publicWithOptions':hasattr(client.interactions,'with_options')}))
    assert len(calls)==1,(method,len(calls),error_type)
    assert calls[0].url.host=='offline.invalid'
    print('SDK_CONTRACT',json.dumps({'sdkVersion':version('google-genai'),'method':method,'httpRequests':len(calls),'remoteRequests':0,'errorType':error_type}))
    client.close()
print('PASS Google SDK contract: one wire request per attempt, no hidden retries')

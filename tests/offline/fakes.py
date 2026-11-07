"""Fake AWS (DynamoDB, S3) and fpdf so the Lambda tools run offline in MOCK_MODE."""
import sys, types, json, importlib.util, os, pathlib
REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "shared"))
# ---- fake DynamoDB shared store ----
STORE = {}
class Table:
    def __init__(s, name): s.name = name
    def get_item(s, Key): 
        it = STORE.get(Key["cacheKey"]); return {"Item": it} if it else {}
    def put_item(s, Item): STORE[Item["cacheKey"]] = Item
    def update_item(s, **k): STORE.setdefault("upd", []).append(k)
    def batch_writer(s, **k):
        t = s
        class BW:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def put_item(self, Item): t.put_item(Item)
        return BW()
class Resource:
    def Table(s, n): return Table(n)
    def batch_get_item(s, RequestItems):
        (tname, req), = RequestItems.items()
        return {"Responses": {tname: [STORE[k["cacheKey"]] for k in req["Keys"] if k["cacheKey"] in STORE]}, "UnprocessedKeys": {}}
class S3:
    def put_object(s, **k): STORE["pdf"] = k
    def generate_presigned_url(s, *a, **k): return "https://example-presigned/url"
def client(name, **k):
    if name == "s3": return S3()
    raise RuntimeError("no client " + name)
boto3 = types.ModuleType("boto3"); boto3.resource = lambda *a, **k: Resource(); boto3.client = client
sys.modules["boto3"] = boto3
bc = types.ModuleType("botocore"); bcc = types.ModuleType("botocore.config"); bcc.Config = lambda **k: None
sys.modules["botocore"] = bc; sys.modules["botocore.config"] = bcc
# ---- fake fpdf that checks text is latin-1 and records calls ----
f = types.ModuleType("fpdf")
class FPDF:
    def __init__(s, **k): s.l_margin=18; s.r_margin=18; s.w=210; s.calls=0; s._p=0
    def __getattr__(s, n):
        def m(*a, **k):
            s.calls += 1
            for x in a:
                if isinstance(x, str): x.encode("latin-1")
            return 0
        return m
    def add_page(s): s._p += 1; s.footer()
    def page_no(s): return s._p
    def output(s): return bytearray(b"%PDF-fake " + str(s.calls).encode())
f.FPDF = FPDF; sys.modules["fpdf"] = f

def load(name):
    lam_dir = REPO / "lambdas" / name
    if str(lam_dir) not in sys.path:
        sys.path.append(str(lam_dir))   # append, so shared/ still wins for names it already has
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), lam_dir / "lambda_function.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
os.environ.update(MOCK_MODE="true", CACHE_TABLE="tp-dev-api-cache", PLANS_BUCKET="b", ITINERARIES_TABLE="tp-dev-itineraries")


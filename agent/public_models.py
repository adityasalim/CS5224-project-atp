"""Explicit website role selection; disabled unless PUBLIC_MODEL_CONFIGURATION=combo_19."""
import json,os,uuid
from pathlib import Path

def public_adapter(ledger_factory=None,adapter_type=None):
    selected=os.environ.get('PUBLIC_MODEL_CONFIGURATION','')
    if not selected:return None
    if selected!='combo_19':raise ValueError('Only approved quality/cost configuration may serve the website')
    from model_configuration import ModelConfiguration
    from model_budget import BudgetLedger,S3BudgetStore,PriceQuote
    from public_protocol import PublicProtocolAdapter
    root=Path(__file__).resolve().parent
    config=ModelConfiguration.load(root/'public-model-config.json')
    expected=('qwen3.8-max-0902','deepseek-v4.1-flash-1017','global.moonshotai.kimi-k3')
    if tuple(config.selections[r].model_id for r in ('planner','executor','reviewer'))!=expected:
        raise ValueError('Website configuration does not match approved quality/cost roles')
    quotes=[PriceQuote.parse(q) for q in json.loads((root/'public-model-prices.json').read_text())['quotes']]
    if ledger_factory is None:
        ledger=BudgetLedger(S3BudgetStore(os.environ['MODEL_BUDGET_LEDGER']),cap_usd='20',provider_caps={'bedrock':'20','alibaba':'4'},quotes=quotes,enforce_spending_caps=False)
    else:ledger=ledger_factory(quotes)
    return (adapter_type or PublicProtocolAdapter)(config,budget_authorizer=ledger.authorize,record_sink=ledger.record)

def public_usage(usage_type):
    adapter=public_adapter()
    return usage_type() if adapter is None else usage_type(adapter=adapter,trip_id='public_'+uuid.uuid4().hex)

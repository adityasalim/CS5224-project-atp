"""Explicit website role selection; disabled unless PUBLIC_MODEL_CONFIGURATION=combo_19."""
import json,os,uuid
from pathlib import Path

# The approved website roles (planner, executor, reviewer) and their independent evaluator: Claude, or configuration 19
# restored by switching public-model-config.json back. PUBLIC_MODEL_CONFIGURATION stays combo_19 for both. Every
# Alibaba model here must also be in model_configuration.ALIBABA_MODELS (the configuration is rejected at load otherwise).
APPROVED={('claude-opus-5-5','claude-haiku-5-5','claude-haiku-5-5'):'claude-haiku-5-5',
          ('qwen3.8-max-0902','deepseek-v4.1-flash','global.moonshotai.kimi-k3'):'global.moonshotai.kimi-k3'}

def public_adapter(ledger_factory=None,adapter_type=None):
    selected=os.environ.get('PUBLIC_MODEL_CONFIGURATION','')
    if not selected:return None
    if selected!='combo_19':raise ValueError('Only approved quality/cost configuration may serve the website')
    from model_configuration import ModelConfiguration
    from model_budget import BudgetLedger,S3BudgetStore,PriceQuote
    from public_protocol import PublicProtocolAdapter
    root=Path(__file__).resolve().parent
    config=ModelConfiguration.load(root/'public-model-config.json')
    actual=tuple(config.selections[r].model_id for r in ('planner','executor','reviewer'))
    if actual not in APPROVED:
        raise ValueError(f'Website configuration does not match approved quality/cost roles: found {actual}, approved {list(APPROVED)}')
    if config.selections['independent_evaluator'].model_id!=APPROVED[actual]:
        raise ValueError('Website configuration does not match the approved independent evaluator')
    quotes=[PriceQuote.parse(q) for q in json.loads((root/'public-model-prices.json').read_text())['quotes']]
    if ledger_factory is None:
        store=os.environ.get('MODEL_BUDGET_LEDGER','')
        if not store:raise ValueError('MODEL_BUDGET_LEDGER must name the S3 location of the spending ledger')
        # Caps are recorded either way; set MODEL_ENFORCE_SPENDING_CAPS=true to make the ledger refuse calls over a cap.
        enforce=os.environ.get('MODEL_ENFORCE_SPENDING_CAPS','false').strip().lower()=='true'
        # About 50 Claude plans at up to USD 1 each (Opus planner with one correction, Haiku for the small jobs).
        ledger=BudgetLedger(S3BudgetStore(store),cap_usd='50',provider_caps={'bedrock':'20','alibaba':'4','anthropic':'50'},quotes=quotes,enforce_spending_caps=enforce)
    else:ledger=ledger_factory(quotes)
    return (adapter_type or PublicProtocolAdapter)(config,budget_authorizer=ledger.authorize,record_sink=ledger.record)

def public_usage(usage_type):
    adapter=public_adapter()
    return usage_type() if adapter is None else usage_type(adapter=adapter,trip_id='public_'+uuid.uuid4().hex)

"""Explicit website role selection; disabled unless PUBLIC_MODEL_CONFIGURATION names an approved configuration."""
import json,os,uuid
from pathlib import Path

# PUBLIC_MODEL_CONFIGURATION -> (file, website roles planner/executor/reviewer, independent evaluator). combo_19 is the
# website configuration; claude_5_5 is the optional demonstration configuration. Each name loads its own file and must
# match its own roles, so a name never runs another configuration's models. Every Alibaba model here must also be in
# model_configuration.ALIBABA_MODELS (the configuration is rejected at load otherwise).
APPROVED={'combo_19':('public-model-config.json',('qwen3.8-max-0902','deepseek-v4.1-flash','global.moonshotai.kimi-k3'),'global.moonshotai.kimi-k3'),
          'claude_5_5':('public-model-config-claude.json',('claude-opus-5-5','claude-sonnet-5-5','claude-sonnet-5-5'),'claude-sonnet-5-5')}

def public_adapter(ledger_factory=None,adapter_type=None):
    selected=os.environ.get('PUBLIC_MODEL_CONFIGURATION','')
    if not selected:return None
    if selected not in APPROVED:raise ValueError('Only approved quality/cost configuration may serve the website')
    file,roles,evaluator=APPROVED[selected]
    from model_configuration import ModelConfiguration
    from model_budget import BudgetLedger,S3BudgetStore,PriceQuote
    from public_protocol import PublicProtocolAdapter
    root=Path(__file__).resolve().parent
    config=ModelConfiguration.load(root/file)
    actual=tuple(config.selections[r].model_id for r in ('planner','executor','reviewer'))
    if actual!=roles:
        raise ValueError(f'Website configuration does not match approved quality/cost roles: found {actual}, approved {roles}')
    if config.selections['independent_evaluator'].model_id!=evaluator:
        raise ValueError('Website configuration does not match the approved independent evaluator')
    quotes=[PriceQuote.parse(q) for q in json.loads((root/'public-model-prices.json').read_text())['quotes']]
    if ledger_factory is None:
        store=os.environ.get('MODEL_BUDGET_LEDGER','')
        if not store:raise ValueError('MODEL_BUDGET_LEDGER must name the S3 location of the spending ledger')
        # Caps are recorded either way; set MODEL_ENFORCE_SPENDING_CAPS=true to make the ledger refuse calls over a cap.
        enforce=os.environ.get('MODEL_ENFORCE_SPENDING_CAPS','false').strip().lower()=='true'
        # Caps per ledger: USD 50 in total, with a limit per provider.
        ledger=BudgetLedger(S3BudgetStore(store),cap_usd='50',provider_caps={'bedrock':'20','alibaba':'4','anthropic':'50'},quotes=quotes,enforce_spending_caps=enforce)
    else:ledger=ledger_factory(quotes)
    return (adapter_type or PublicProtocolAdapter)(config,budget_authorizer=ledger.authorize,record_sink=ledger.record)

def public_usage(usage_type):
    adapter=public_adapter()
    return usage_type() if adapter is None else usage_type(adapter=adapter,trip_id='public_'+uuid.uuid4().hex)

import os,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'agent'))
from public_models import public_adapter,public_usage
class Public(unittest.TestCase):
 def test_disabled_path_makes_no_adapter(self):
  with patch.dict(os.environ,{'PUBLIC_MODEL_CONFIGURATION':''}):self.assertIsNone(public_adapter())
 def test_report_only_configuration_rejected(self):
  with patch.dict(os.environ,{'PUBLIC_MODEL_CONFIGURATION':'combo_13'}):
   with self.assertRaises(ValueError):public_adapter()
 def test_disabled_usage_keeps_legacy_constructor(self):
  with patch.dict(os.environ,{'PUBLIC_MODEL_CONFIGURATION':''}):self.assertEqual(public_usage(lambda **kw:kw),{})
 def test_enabled_roles_are_exact_without_provider_requests(self):
  from types import SimpleNamespace
  with patch.dict(os.environ,{'PUBLIC_MODEL_CONFIGURATION':'combo_19'}):
   adapter=public_adapter(ledger_factory=lambda quotes:SimpleNamespace(authorize=lambda *a:None,record=lambda row:row))
   self.assertEqual([adapter.configuration.selections[r].model_id for r in ['planner','executor','reviewer','independent_evaluator']],['claude-opus-5-5','claude-sonnet-5-5','claude-sonnet-5-5','claude-sonnet-5-5'])
   self.assertEqual(adapter.records,[])
 def test_configuration_19_stays_usable_by_switching_the_config_back(self):
  from types import SimpleNamespace
  from model_configuration import ModelConfiguration
  c19={'experimentId':'public_combo_19','googleBillingDisabled':True,
       'planner':{'provider':'alibaba','model':'qwen3.8-max-0902','options':{'enableThinking':True,'thinkingBudget':4000}},
       'executor':{'provider':'alibaba','model':'deepseek-v4.1-flash','options':{'enableThinking':True,'reasoningEffort':'low'}},
       'reviewer':{'provider':'bedrock','model':'global.moonshotai.kimi-k3','options':{'reasoningEffort':'medium'}},
       'independent_evaluator':{'provider':'bedrock','model':'global.moonshotai.kimi-k3','options':{'reasoningEffort':'low'}}}
  with patch.dict(os.environ,{'PUBLIC_MODEL_CONFIGURATION':'combo_19'}),patch.object(ModelConfiguration,'load',return_value=ModelConfiguration.parse(c19)):
   adapter=public_adapter(ledger_factory=lambda quotes:SimpleNamespace(authorize=lambda *a:None,record=lambda row:row))
   self.assertEqual(adapter.configuration.selections['planner'].model_id,'qwen3.8-max-0902')
  c19['independent_evaluator']['model']='claude-haiku-5-5';c19['independent_evaluator']['provider']='anthropic'
  with patch.dict(os.environ,{'PUBLIC_MODEL_CONFIGURATION':'combo_19'}),patch.object(ModelConfiguration,'load',return_value=ModelConfiguration.parse(c19)):
   with self.assertRaises(ValueError):public_adapter(ledger_factory=lambda quotes:None)
 def test_claude_roles_need_the_sonnet_reviewer_and_evaluator(self):
  import json
  from model_configuration import ModelConfiguration
  root=Path(__file__).resolve().parents[2]/'agent'
  for role in ('reviewer','independent_evaluator'):
   c=json.loads((root/'public-model-config.json').read_text());c[role]['model']='claude-haiku-5-5'
   with patch.dict(os.environ,{'PUBLIC_MODEL_CONFIGURATION':'combo_19'}),patch.object(ModelConfiguration,'load',return_value=ModelConfiguration.parse(c)):
    with self.assertRaises(ValueError):public_adapter(ledger_factory=lambda quotes:None)
if __name__=='__main__':unittest.main()


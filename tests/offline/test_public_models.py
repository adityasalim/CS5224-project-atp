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
   self.assertEqual([adapter.configuration.selections[r].model_id for r in ['planner','executor','reviewer']],['qwen3.8-max-0902','deepseek-v4.1-flash-1017','global.moonshotai.kimi-k3'])
   self.assertEqual(adapter.records,[])
if __name__=='__main__':unittest.main()


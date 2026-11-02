"""Validate the approved 20-way matrix without invoking models."""
import unittest
from pathlib import Path
from model_configuration import ModelConfiguration
class MatrixTest(unittest.TestCase):
 def test_all_selections_explicit_and_evaluator_fixed(self):
  paths=sorted((Path(__file__).resolve().parents[1]/'experiments/model-configs').glob('combo_*.json'))
  self.assertEqual(len(paths),20)
  combinations=set()
  for number,path in enumerate(paths,1):
   config=ModelConfiguration.load(path)
   self.assertEqual(config.experiment_id,f'combo_{number:02d}')
   self.assertTrue(config.google_billing_disabled)
   evaluator=config.selections['independent_evaluator']
   self.assertEqual((evaluator.provider,evaluator.model_id),('bedrock','global.moonshotai.kimi-k3'))
   combinations.add(tuple((config.selections[r].provider,config.selections[r].model_id) for r in ('planner','executor','reviewer')))
   self.assertFalse(any('5-5' in s.model_id or 'gpt' in s.model_id for s in config.selections.values()))
  self.assertEqual(len(combinations),20)
if __name__=='__main__':unittest.main()

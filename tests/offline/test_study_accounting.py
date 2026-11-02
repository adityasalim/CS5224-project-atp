import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'experiments'))
from study_report import prompt_tokens,output_tokens,overlay
class StudyAccounting(unittest.TestCase):
 def test_cache_components_not_double_counted_across_providers(self):
  self.assertEqual(prompt_tokens({'provider':'bedrock','inputTokens':13,'cacheReadTokens':3213,'cacheWriteTokens':0,'totalTokens':3231,'outputTokens':5}),3226)
  self.assertEqual(prompt_tokens({'provider':'alibaba','inputTokens':3226,'cacheReadTokens':3213,'outputTokens':5}),3226)
  self.assertEqual(prompt_tokens({'provider':'bedrock','inputTokens':7,'nativeInputTokens':3666,'cacheReadTokens':3659}),3666)
 def test_google_reasoning_is_included_once_in_billable_output(self):
  self.assertEqual(output_tokens({'provider':'google','inputTokens':1000,'outputTokens':300,'thinkingTokens':200,'totalTokens':1500}),500)
  self.assertIsNone(output_tokens({'provider':'google','inputTokens':None,'outputTokens':None}))
 def test_overlay_keeps_original_sdk_record_and_failed_attempt(self):
  rows=[{'modelInvocations':[{'invocationId':'i','provider':'bedrock','errorCode':'ThrottlingException','success':False,'estimatedPaidTierCostUSD':None}]}]
  updated=overlay(rows,{'i':{'estimatedPaidTierCostUSD':'0'}})
  self.assertIsNone(rows[0]['modelInvocations'][0]['estimatedPaidTierCostUSD'])
  self.assertEqual(updated[0]['modelInvocations'][0]['estimatedPaidTierCostUSD'],'0')
  self.assertFalse(updated[0]['modelInvocations'][0]['success'])
if __name__=='__main__':unittest.main()

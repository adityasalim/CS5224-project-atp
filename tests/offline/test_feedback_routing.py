import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'agent'))
from feedback_routing import revise_narrative
class Routing(unittest.TestCase):
 def setUp(self):
  self.story={'title':'Trip','summary':'Original','dayNotes':{}}
  self.facts={'days':[{'date':'2026-12-10','stops':[]}]}
 def test_text_only_calls_executor_once_without_mutating_inputs(self):
  calls=[]
  def revise(s,f,i):
   calls.append(1);f['days'].clear()
   return {'title':'Trip','summary':'Clearer','dayNotes':{}}
  result,status=revise_narrative(self.story,self.facts,lambda s,f:{'passed':False,'issues':[{'scope':'narrative','instruction':'Clarify the summary'}]},revise)
  self.assertEqual(calls,[1]);self.assertEqual(result['summary'],'Clearer')
  self.assertEqual(status['status'],'revised_not_rejudged');self.assertEqual(len(self.facts['days']),1)
 def test_plan_or_ambiguous_feedback_never_calls_executor(self):
  for issues in ([{'scope':'itinerary','instruction':'Change flight'}],[],[{'scope':'unknown','instruction':'Fix'}]):
   result,status=revise_narrative(self.story,self.facts,lambda s,f:{'passed':False,'issues':issues},lambda *a:self.fail('Unexpected executor call'))
   self.assertEqual(result,self.story);self.assertEqual(status['target'],'planner')
 def test_pass_has_no_extra_revision(self):
  _,status=revise_narrative(self.story,self.facts,lambda s,f:{'passed':True},lambda *a:self.fail('Unexpected revision'))
  self.assertEqual(status['status'],'passed')
 def test_invalid_schema_cannot_change_itinerary(self):
  result,status=revise_narrative(self.story,self.facts,lambda s,f:{'passed':False,'issues':[{'scope':'narrative','instruction':'Explain'}]},lambda *a:{'plan':{}})
  self.assertEqual(result,self.story);self.assertEqual(status['status'],'invalid_revision')
 def test_review_failure_does_not_retry(self):
  def broken(*a):raise ValueError()
  _,status=revise_narrative(self.story,self.facts,broken,lambda *a:self.fail('Unexpected revision'))
  self.assertEqual(status['status'],'review_unavailable')
if __name__=='__main__':unittest.main()

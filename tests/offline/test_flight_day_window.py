import ast,unittest
from pathlib import Path
source=Path(__file__).resolve().parents[2]/'agent/tp_travel_agent.py'
tree=ast.parse(source.read_text())
fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_flight_day_window')
ns={'_hm':lambda s:int(s[:2])*60+int(s[3:]),'ARRIVAL_BUFFER_MIN':120,'DEPARTURE_BUFFER_MIN':180}
exec(compile(ast.Module(body=[fn],type_ignores=[]),str(source),'exec'),ns)
window=ns['_flight_day_window']
class Windows(unittest.TestCase):
 def setUp(self):
  self.pool={'flights':[{'offerId':'f','slices':[{'arriveAt':'2026-12-11T01:00:00'},{'departAt':'2026-12-14T02:15:00'}]}]}
 def check(self,day,expected):self.assertEqual(window({},self.pool,{'flightOfferId':'f'},day,540,1260),expected)
 def test_before_arrival(self):self.check('2026-12-10',(540,540))
 def test_actual_arrival_day(self):self.check('2026-12-11',(540,1260))
 def test_daytime_arrival_buffer(self):
  self.pool['flights'][0]['slices'][0]['arriveAt']='2026-12-11T14:00:00'
  self.check('2026-12-11',(960,1260))
 def test_early_departure_has_no_sightseeing(self):self.check('2026-12-14',(540,540))
 def test_after_departure(self):self.check('2026-12-15',(540,540))
 def test_missing_flight_evidence_unchanged(self):self.assertEqual(window({}, {},{},'2026-12-11',540,1260),(540,1260))
if __name__=='__main__':unittest.main()

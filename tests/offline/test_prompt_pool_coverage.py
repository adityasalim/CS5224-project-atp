import ast,unittest
from pathlib import Path
tree=ast.parse((Path(__file__).resolve().parents[2]/'agent/tp_travel_agent.py').read_text())
ns={'OUTDOOR':set()}
for name in ['_order_places','_c_place']:
 fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
 exec(compile(ast.Module(body=[fn],type_ignores=[]),'<test>','exec'),ns)
class Pool(unittest.TestCase):
 def test_categories_survive_cap(self):
  rows=[{'placeId':str(i),'category':'museum','rating':3} for i in range(30)]+[{'placeId':'t','category':'temple','rating':4.8}]
  self.assertIn('t',[p['placeId'] for p in ns['_order_places'](rows,22)])
 def test_pinned_never_dropped(self):
  rows=[{'placeId':str(i),'category':'museum','requested':True} for i in range(4)]
  self.assertEqual(len(ns['_order_places'](rows,2)),4)
 def test_actual_ratings_exposed_missing_unknown(self):
  self.assertEqual(ns['_c_place']({'placeId':'t','rating':4.8})['rating'],4.8)
  self.assertIsNone(ns['_c_place']({'placeId':'x'})['rating'])
if __name__=='__main__':unittest.main()

import sys, unittest, copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent'))
from test_package_recommendations import SPEC, ROW, VERIFY, ANSWER
from package_recommendations import recommend
class Tests(unittest.TestCase):
    def test_same_eligible_package_visible_in_both_modes(self):
        compare=recommend(SPEC,[ROW],VERIFY,lambda *a:ANSWER,mode='compare')
        preferred=recommend(SPEC,[ROW],VERIFY,lambda *a:self.fail('Prefer must not be vetoed by reviewer'),mode='packages_only')
        self.assertEqual(compare['packages'][0]['packageId'],preferred['packages'][0]['packageId'])
        self.assertEqual(preferred['selectionMethod'],'eligibility_ranked')
        self.assertEqual(preferred['packages'][0]['comparison'],compare['packages'][0]['comparison'])
    def test_eligibility_rejections_and_custom_only_remain(self):
        for change in ({'matchScore':.4},{'eligibilityChecked':False},{'price':{'amount':'1500','currency':'USD','basis':'per_person'}}):
            r=recommend(SPEC,[dict(ROW,**change)],VERIFY,None,mode='packages_only')
            self.assertEqual(r['packages'],[])
        self.assertEqual(recommend(SPEC,[ROW],VERIFY,None,mode='custom_only')['packages'],[])
    def test_ranked_capped_and_unknown_costs_preserved(self):
        rows=[dict(ROW,packageId=str(i),matchScore=.7+i*.02) for i in range(5)]
        r=recommend(SPEC,rows,VERIFY,None,mode='packages_only')
        self.assertEqual([p['packageId'] for p in r['packages']],['4','3','2'])
        row=dict(ROW,costInclusions={})
        p=recommend(SPEC,[row],VERIFY,None,mode='packages_only')['packages'][0]
        self.assertFalse(p['comparison']['costComplete']);self.assertIsNone(p['comparison']['withinBudget'])
        self.assertEqual(recommend(SPEC,[ROW],VERIFY,lambda *a:{'selected':[]},mode='compare')['status'],'custom_preferred')
if __name__=='__main__':unittest.main()

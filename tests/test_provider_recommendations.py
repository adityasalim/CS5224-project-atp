"""Form -> API validation -> search eligibility -> recommendation modes, without AWS/model calls."""
import importlib.util, json, subprocess, sys, time, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
sys.path.insert(0,str(ROOT/'lambdas/tp-ahmed-agency-api'))
from marketplace_domain import validate_package
from package_recommendations import recommend, CATEGORIES
module=importlib.util.spec_from_file_location('search',ROOT/'lambdas/tp-ahmed-search-packages/lambda_function.py')
search=importlib.util.module_from_spec(module);module.loader.exec_module(search)
class Tests(unittest.TestCase):
    def test_portal_to_recommendations_in_both_modes(self):
        body=json.loads(subprocess.check_output(['node',str(ROOT/'tests/provider_roundtrip.js')]))
        package=dict(validate_package(body),packageId='p',agencyId='a',status='PUBLISHED')
        spec={'destination':'Tokyo','startDate':'2030-01-10','endDate':'2030-01-14','pax':1,
              'budget':{'amount':'5000','currency':'SGD'},'preferenceScores':dict.fromkeys(('culture','food','nature','comfort','intensity'),5)}
        request={**spec,'destinations':['Tokyo'],'origin':'SIN','flightType':'round',
                 'fxQuote':{'base':'SGD','rates':{'SGD':1},'timestamp':time.time()}}
        rows=search.search_packages(request,[package],[{'agencyId':'a','status':'APPROVED','tradingName':'Agency'}])
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['matchScore'],.8)
        verify={'passed':True,'totals':{'currency':'SGD','total':'2500'}}
        for mode in ('compare','packages_only'):
            def reviewer(system,prompt):
                self.assertEqual(prompt['mode'],mode)
                self.assertIn('a valid custom itinerary is not a reason to hide a suitable package',system)
                return {'selected':[{'packageId':'p','reasons':['Strong culture and food fit'],'tradeoffs':[]}]}
            result=recommend(spec,rows,verify,reviewer,mode=mode)
            self.assertEqual(result['status'],'recommended');self.assertEqual(result['packages'][0]['packageId'],'p')
            self.assertTrue(result['packages'][0]['comparison']['costComplete'])
        self.assertEqual(recommend(spec,rows,verify,lambda *a:self.fail('custom only must skip reviewer'),mode='custom_only')['packages'],[])
        for status in ('DRAFT','PAUSED','WITHDRAWN'):
            self.assertEqual(search.search_packages(request,[dict(package,status=status)],[{'agencyId':'a','status':'APPROVED'}]),[])
if __name__=='__main__': unittest.main()

import copy, importlib.util, sys, time, unittest
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'agent'))
from package_gather import gather_packages
from package_recommendations import recommend, CATEGORIES
spec=importlib.util.spec_from_file_location('search',ROOT/'lambdas/tp-ahmed-search-packages/lambda_function.py');search=importlib.util.module_from_spec(spec);spec.loader.exec_module(search)
TRIP={'destination':'Tokyo','legs':[{'city':'Tokyo','nights':5,'checkIn':'2030-01-16','checkOut':'2030-01-21'},{'city':'Osaka','nights':4,'checkIn':'2030-01-21','checkOut':'2030-01-25'}], 'startDate':'2030-01-16','endDate':'2030-01-25','pax':2,'originAirport':'SIN','flightType':'oneway','budget':{'currency':'SGD','amount':'7000'},'_fx':{'base':'SGD','rates':{'SGD':1},'timestamp':time.time()},'preferenceScores':dict.fromkeys(('culture','food','nature','comfort','intensity'),5)}
PACKAGE={'agencyId':'a','packageId':'p','title':'Tokyo 6 Days','status':'PUBLISHED','destinations':[{'city':'Tokyo','nights':5}],'durationDays':6,'minimumTravellers':1,'maximumTravellers':4,'availability':[{'startDate':'2030-01-01','endDate':'2030-01-31'}],'flightType':'one_way','departureLocations':['SIN'],'price':{'amount':'1800','currency':'SGD','basis':'per_person'},'costInclusions':dict.fromkeys(CATEGORIES,True),'preferenceScores':dict.fromkeys(('culture','food','nature','comfort','intensity'),8),'included':['Inbound flight and Tokyo services'],'excluded':['Personal shopping'],'additionalCosts':{}}
AGENCY={'agencyId':'a','status':'APPROVED'}
VERIFY={'passed':True,'totals':{'currency':'SGD','flight':'1200','stay':'1000','total':'6400'}}
class Tests(unittest.TestCase):
 def gather(self,trip=TRIP,package=PACKAGE):
  def call(name,query):
   self.assertEqual(name,'search_agency_packages');return {'ok':True,'packages':search.search_packages(query,[package],[AGENCY])}
  rows,status=gather_packages(trip,SimpleNamespace(call=call));self.assertEqual(status,'success');return rows
 def test_inbound_partial_in_both_modes_without_double_counting(self):
  rows=self.gather();self.assertEqual(len(rows),1);scope=rows[0]['coverageScope'];self.assertEqual(scope['city'],'Tokyo');self.assertEqual(scope['flightCoverage'],'inbound_only');self.assertEqual(scope['endDate'],'2030-01-21')
  for mode in ('compare','packages_only'):
   def reviewer(system,prompt):
    self.assertEqual(prompt['eligiblePackages'][0]['coverageScope'],scope)
    return {'selected':[{'packageId':'p','reasons':['Culture fit is strong'],'tradeoffs':[]}]}
   result=recommend(TRIP,rows,VERIFY,reviewer,mode);self.assertEqual(result['status'],'recommended');cost=result['packages'][0]['comparison']
   self.assertEqual(cost['packageTotal'],'3600');self.assertEqual(cost['knownExtras'],'0');self.assertFalse(cost['costComplete']);self.assertIsNone(cost['estimatedTotal']);self.assertIsNone(cost['withinBudget']);self.assertIsNone(cost['differenceFromCustom']);self.assertIn('Remaining trip',cost['missingCosts'][-1])
 def test_wrong_origin_return_bundle_dates_duration_and_status_are_rejected(self):
  changes=[{'departureLocations':['LHR']},{'flightType':'round'},{'durationDays':5},{'status':'DRAFT'},{'availability':[{'startDate':'2030-01-22','endDate':'2030-01-31'}]},{'maximumTravellers':1},{'price':{'amount':'4000','currency':'SGD','basis':'per_person'}}]
  for change in changes:self.assertEqual(self.gather(package={**PACKAGE,**change}),[],change)
 def test_later_city_only_accepts_explicit_land_only(self):
  osaka={**PACKAGE,'destinations':[{'city':'Osaka','nights':4}],'durationDays':5}
  self.assertEqual(self.gather(package=osaka),[])
  osaka={**osaka,'costInclusions':{**PACKAGE['costInclusions'],'flights':False},'flightType':'none','departureLocations':[]}
  rows=self.gather(package=osaka);self.assertEqual(rows[0]['coverageScope']['city'],'Osaka');self.assertEqual(rows[0]['coverageScope']['flightCoverage'],'none')
  cost=recommend(TRIP,rows,VERIFY,None,'packages_only')['packages'][0]['comparison'];self.assertEqual(cost['knownExtras'],'0')
 def test_whole_trip_rules_remain_and_bad_legs_fail_closed(self):
  full={**PACKAGE,'destinations':[{'city':'Tokyo'},{'city':'Osaka'}],'durationDays':10}
  rows=self.gather(package=full);self.assertEqual(rows[0]['coverageScope']['type'],'whole_trip');self.assertTrue(recommend(TRIP,rows,VERIFY,None,'packages_only')['packages'][0]['comparison']['costComplete'])
  self.assertEqual(self.gather({**TRIP,'flightType':'round'},full),[])
  malformed=copy.deepcopy(TRIP);malformed['legs'][1]['checkIn']='2030-01-22'
  self.assertEqual(gather_packages(malformed,SimpleNamespace(call=lambda n,q:{'ok':True,'packages':search.search_packages(q,[PACKAGE],[AGENCY])}))[0],[])
 def test_return_visits_match_separate_stays_and_availability(self):
  trip=copy.deepcopy(TRIP);trip['endDate']='2030-01-27';trip['legs'].append({'city':'Tokyo','nights':2,'checkIn':'2030-01-25','checkOut':'2030-01-27'})
  land={**PACKAGE,'durationDays':3,'flightType':'none','departureLocations':[],'costInclusions':{**PACKAGE['costInclusions'],'flights':False}}
  scope=self.gather(trip,land)[0]['coverageScope'];self.assertEqual(scope['legIndex'],2);self.assertEqual(scope['startDate'],'2030-01-25')
 def test_partial_exclusions_use_the_city_and_flight_scope(self):
  package={**PACKAGE,'excluded':['Return flight to Singapore','Osaka accommodation']}
  rows=self.gather(package=package)
  result=recommend(TRIP,rows,VERIFY,None,'packages_only');self.assertEqual(result['status'],'recommended')
  for text in ('Inbound flights','International flights','Inbound and return flights','Tokyo accommodation'):
   rows=self.gather(package={**PACKAGE,'excluded':[text]})
   self.assertEqual(recommend(TRIP,rows,VERIFY,None,'packages_only')['packages'],[],text)
 def test_three_modes_and_comparison_reviewer_decline(self):
  rows=self.gather()
  def forbidden(*a):self.fail('Custom-only must never ask reviewer')
  result=recommend(TRIP,rows,VERIFY,forbidden,'custom_only')
  self.assertEqual(result['status'],'custom_only');self.assertEqual(result['packages'],[])
  compared=recommend(TRIP,rows,VERIFY,lambda *a:{'selected':[]},'compare')
  self.assertEqual(compared['status'],'eligible_options');self.assertEqual(compared['reviewStatus'],'custom_preferred')
  self.assertEqual(compared['packages'][0]['packageId'],'p')
  preferred=recommend(TRIP,rows,VERIFY,None,'packages_only')
  self.assertEqual(preferred['packages'][0]['packageId'],compared['packages'][0]['packageId'])
  self.assertEqual(preferred['packages'][0]['comparison'],compared['packages'][0]['comparison'])
  self.assertEqual(recommend(TRIP,rows,VERIFY,forbidden,'custom_only')['packages'],[])
  self.assertNotIn('reasons',rows[0]) # Selection must not mutate cached search records.
if __name__=='__main__':unittest.main()

import copy, unittest
from package_recommendations import recommend, eligible, comparison, CATEGORIES
SPEC={'destination':'Tokyo','startDate':'2030-01-01','endDate':'2030-01-04','pax':2,'budget':{'amount':'2000','currency':'USD'},'preferenceScores':{'culture':9,'food':8}}
VERIFY={'passed':True,'totals':{'currency':'USD','flight':'200','stay':'300','total':'1400'}}
ROW={'packageId':'p','agencyId':'a','title':'Cultural Tokyo','agencyName':'Agency','eligibilityChecked':True,'matchScore':.9,'preferenceScores':{'culture':9,'food':9},'price':{'amount':'500','currency':'USD','basis':'per_person'},'currency':'USD','costInclusions':dict.fromkeys(CATEGORIES,True),'additionalCosts':{},'included':['All categories'],'excluded':[],'contactEmail':'agency@example.test'}
ANSWER={'selected':[{'packageId':'p','reasons':['Strong culture and food fit'],'tradeoffs':['Agency schedule is less flexible']}]}
class Tests(unittest.TestCase):
    def test_valid_all_currencies_and_no_double_count(self):
        for currency in ('USD','SGD','SAR','EUR','GBP','CNY','JPY'):
            spec=copy.deepcopy(SPEC);spec['budget']['currency']=currency
            row=copy.deepcopy(ROW);row['currency']=currency;row['price']['currency']=currency
            verify=copy.deepcopy(VERIFY);verify['totals']['currency']=currency
            r=recommend(spec,[row],verify,lambda *args:ANSWER)
            self.assertEqual(r['status'],'recommended');c=r['packages'][0]['comparison']
            self.assertEqual(c['currency'],currency);self.assertEqual(c['estimatedTotal'],'1000')
            self.assertEqual(c['knownExtras'],'0');self.assertEqual(c['differenceFromCustom'],'-400')
            self.assertNotIn('estimatedTotalSGD',c)
    def test_unknowns_never_claim_affordability(self):
        row=copy.deepcopy(ROW);row['costInclusions']={}
        c=comparison(row,SPEC,VERIFY)
        self.assertFalse(c['costComplete']);self.assertIsNone(c['estimatedTotal']);self.assertIsNone(c['withinBudget']);self.assertIsNone(c['differenceFromCustom'])
    def test_excluded_flight_uses_verified_candidate_cost(self):
        row=copy.deepcopy(ROW);row['costInclusions']['flights']=False
        self.assertEqual(comparison(row,SPEC,VERIFY)['estimatedTotal'],'1200')
        self.assertIsNone(comparison(row,SPEC,dict(VERIFY,passed=False))['estimatedTotal'])
    def test_additional_cost_basis_and_mandatory_legacy_extra(self):
        row=copy.deepcopy(ROW);row['costInclusions']['meals']=False
        row['additionalCosts']={'meals':{'amount':'100','currency':'USD','basis':'per_person'}}
        row['mandatoryExtra']='50'
        self.assertEqual(comparison(row,SPEC,VERIFY)['estimatedTotal'],'1250')
    def test_reject_weak_missing_provenance_overbudget_and_wrong_currency(self):
        for change in ({'matchScore':.4},{'eligibilityChecked':False},{'preferenceScores':{'culture':2,'food':9}},{'currency':'JPY'},{'price':{'amount':'1500','currency':'USD','basis':'per_person'}}):
            self.assertEqual(eligible(SPEC,[dict(ROW,**change)],VERIFY),[])
    def test_extra_requires_explicitly_excluded_category(self):
        row=copy.deepcopy(ROW);row['additionalCosts']={'meals':{'amount':'100','currency':'USD','basis':'per_group'}}
        self.assertEqual(eligible(SPEC,[row],VERIFY),[])

    def test_cuisine_evidence_reaches_model(self):
        row=copy.deepcopy(ROW);row['cuisines']=['Japanese'];row['cuisineMatches']=['japanese'];row['description']='Cultural visits'
        def call(system,payload):
            self.assertEqual(payload['eligiblePackages'][0]['cuisines'],['Japanese'])
            return ANSWER
        self.assertEqual(recommend(SPEC,[row],VERIFY,call)['status'],'recommended')

    def test_inclusion_conflict_cannot_claim_complete_cost(self):
        row=copy.deepcopy(ROW);row['excluded']=['International flights']
        self.assertEqual(eligible(SPEC,[row],VERIFY),[])

    def test_empty_tradeoffs_do_not_force_invented_disadvantages(self):
        answer=copy.deepcopy(ANSWER);answer['selected'][0]['tradeoffs']=[]
        self.assertEqual(recommend(SPEC,[ROW],VERIFY,lambda *x:answer)['status'],'recommended')

    def test_valid_preference_ratings_and_bounded_reasons(self):
        answer=copy.deepcopy(ANSWER);answer['selected'][0]['reasons']=['Culture fit is 9/10','Food fit is high','Comfort fit is high','Nature fit is high']
        r=recommend(SPEC,[ROW],VERIFY,lambda *x:answer)
        self.assertEqual(r['status'],'recommended');self.assertEqual(len(r['packages'][0]['reasons']),3)

    def test_empty_and_custom_only_skip_model(self):
        def fail(*args): self.fail('model must not run')
        self.assertEqual(recommend(SPEC,[],VERIFY,fail)['status'],'no_suitable_packages')
        self.assertEqual(recommend(SPEC,[ROW],VERIFY,fail,mode='custom_only')['status'],'custom_only')
    def test_none_selection_hallucinated_ids_malformed_claims_and_failure(self):
        self.assertEqual(recommend(SPEC,[ROW],VERIFY,lambda *x:{'selected':[]})['status'],'eligible_options')
        for answer in ({'selected':[{'packageId':'invented'}]}, {}):
            r=recommend(SPEC,[ROW],VERIFY,lambda *x:answer)
            self.assertEqual(r['status'],'review_unavailable');self.assertEqual(r['packages'],[])
        def failure(*x): raise RuntimeError('model down')
        self.assertEqual(recommend(SPEC,[ROW],VERIFY,failure)['status'],'review_unavailable')
    def test_unsafe_explanations_preserve_valid_packages_in_both_modes(self):
        for mode in ('compare','packages_only'):
            for phrase in ('Guaranteed cheaper at USD 100','Availability is not guaranteed','Not confirmed within budget','Culture fit costs $10'):
                answer={'selected':[{'packageId':'p','reasons':[phrase],'tradeoffs':[phrase]}]}
                r=recommend(SPEC,[ROW],VERIFY,lambda *x:answer,mode=mode)
                self.assertEqual(r['status'],'recommended')
                self.assertEqual(r['packages'][0]['packageId'],'p')
                self.assertEqual(r['packages'][0]['reasons'],['Package suitability match score: 90%.'])
                self.assertEqual(r['packages'][0]['tradeoffs'],[])
                self.assertEqual(r['packages'][0]['comparison']['estimatedTotal'],'1000')

    def test_safe_explanations_are_retained_while_unsafe_ones_are_removed(self):
        answer={'selected':[{'packageId':'p','reasons':['Culture fit is 9/10','Cheaper','Food fit is high'],'tradeoffs':['Availability is not guaranteed','Fixed departure schedule']}]}
        r=recommend(SPEC,[ROW],VERIFY,lambda *x:answer)
        self.assertEqual(r['packages'][0]['reasons'],['Culture fit is 9/10','Food fit is high'])
        self.assertEqual(r['packages'][0]['tradeoffs'],['Fixed departure schedule'])

    def test_sanitized_prose_never_bypasses_eligibility_or_unknown_costs(self):
        answer={'selected':[{'packageId':'p','reasons':['Guaranteed cheaper'],'tradeoffs':[]}]}
        def fail(*x): self.fail('Ineligible packages must not reach reviewer')
        for row in (dict(ROW,matchScore=.4),dict(ROW,eligibilityChecked=False),dict(ROW,price={'amount':'1500','currency':'USD','basis':'per_person'})):
            self.assertEqual(recommend(SPEC,[row],VERIFY,fail)['packages'],[])
        row=dict(ROW,costInclusions={})
        r=recommend(SPEC,[row],VERIFY,lambda *x:answer)
        self.assertEqual(r['status'],'recommended')
        self.assertFalse(r['packages'][0]['comparison']['costComplete'])
        self.assertIsNone(r['packages'][0]['comparison']['withinBudget'])

if __name__=='__main__': unittest.main()

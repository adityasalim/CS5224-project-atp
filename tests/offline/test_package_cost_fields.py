"""Package authoring validates optional currency-explicit coverage and extras."""
import importlib.util,pathlib,copy
p=pathlib.Path(__file__).resolve().parents[2]/'lambdas/tp-ahmed-agency-api/marketplace_domain.py'
s=importlib.util.spec_from_file_location('domain',p);d=importlib.util.module_from_spec(s);s.loader.exec_module(d)
body={'title':'Example','description':'Fixture','durationDays':4,'destinations':[{'city':'Tokyo','country':'Japan','nights':3}],'minimumTravellers':1,'maximumTravellers':4,'price':{'amount':'1200','currency':'USD','basis':'per_person'},'included':['hotel'],'excluded':['flights'],'availability':[{'startDate':'2030-01-01','endDate':'2030-02-01'}],'costInclusions':{'flights':False,'accommodation':True},'additionalCosts':{'flights':{'amount':'200','currency':'USD','basis':'per_group'}}}
r=d.validate_package(body);assert r['costInclusions']['accommodation'] and r['additionalCosts']['flights']['amount']==200
for change in ({'costInclusions':{'flights':'yes'}},{'costInclusions':{'flights':True}},{'costInclusions':{}},{'costInclusions':{'invented':True}},{'additionalCosts':{'flights':{'amount':True,'currency':'USD','basis':'per_person'}}},{'additionalCosts':{'flights':{'amount':'NaN','currency':'USD','basis':'per_person'}}},{'additionalCosts':{'flights':{'amount':'1','currency':'USD','basis':'unknown'}}}):
    try: d.validate_package(dict(body,**change))
    except d.ValidationError: pass
    else: raise AssertionError(change)
print('PACKAGE COST FIELDS OK: explicit currencies, coverage, fee basis, invalid inputs')

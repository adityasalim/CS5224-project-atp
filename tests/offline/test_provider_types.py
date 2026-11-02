import importlib.util,pathlib
p=pathlib.Path(__file__).resolve().parents[2]/'lambdas/tp-ahmed-agency-api/marketplace_domain.py'
s=importlib.util.spec_from_file_location('domain',p);d=importlib.util.module_from_spec(s);s.loader.exec_module(d)
profile={k:'Test' for k in d.AGENCY_FIELDS if k!='supportedDestinations'}
profile.update(businessEmail='test@example.invalid',supportedDestinations=['Tokyo'],termsAccepted=True,providerType='local_guide',languages=['English'],experienceDescription='Test experience')
assert d.validate_agency(profile)['providerType']=='local_guide'
for bad in ('invalid',None):
    try:d.validate_agency(dict(profile,providerType=bad))
    except d.ValidationError:pass
    else:raise AssertionError()
print('PROVIDER TYPES OK: local guides and unsupported provider rejection')

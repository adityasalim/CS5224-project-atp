import unittest
from types import SimpleNamespace
from package_gather import gather_packages
class Tests(unittest.TestCase):
    def test_optional_search_failure_and_provenance(self):
        spec={"destination":"Tokyo","startDate":"2030-01-01","endDate":"2030-01-04","pax":2,"budgetSGD":3000}
        def fail(*args): raise RuntimeError()
        self.assertEqual(gather_packages(spec,SimpleNamespace(call=fail)),([],"unavailable"))
        self.assertEqual(gather_packages(spec,SimpleNamespace(call=lambda *args: {"ok":True,"packages":[{}]})),([],"invalid_result"))
        record={"packageId":"p","agencyId":"a"}
        self.assertEqual(gather_packages(spec,SimpleNamespace(call=lambda *args: {"ok":True,"packages":[record]})),([record],"success"))
if __name__=="__main__": unittest.main()

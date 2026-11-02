import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.update(AWS_DEFAULT_REGION="ap-southeast-1", AWS_ACCESS_KEY_ID="test",
                  AWS_SECRET_ACCESS_KEY="test", AGENCIES_TABLE="agencies", PACKAGES_TABLE="packages")
path = Path(__file__).resolve().parents[2] / "lambdas" / "tp-ahmed-agency-api"
sys.path.insert(0, str(path))
spec = importlib.util.spec_from_file_location("agency_handler", path / "lambda_function.py")
handler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handler)

class Transactions(unittest.TestCase):
    def test_approved_owner_check_is_in_same_transaction_as_put(self):
        with patch.object(handler.boto3, "client") as client:
            handler.atomic_package_write("a", {"Put": {"TableName": "packages", "Item": {"agencyId": "a", "packageId": "p"}}}, "user")
            tx = client.return_value.transact_write_items.call_args.kwargs["TransactItems"]
            self.assertEqual(tx[0]["ConditionCheck"]["ExpressionAttributeValues"][":approved"], {"S": "APPROVED"})
            self.assertEqual(tx[0]["ConditionCheck"]["ExpressionAttributeValues"][":owner"], {"S": "user"})
            self.assertEqual(tx[1]["Put"]["Item"]["packageId"], {"S": "p"})
    def test_admin_check_does_not_trust_owner(self):
        with patch.object(handler.boto3, "client") as client:
            handler.atomic_package_write("a", {"Update": {"TableName": "packages", "Key": {"agencyId": "a", "packageId": "p"}, "ExpressionAttributeValues": {":old": "DRAFT"}}})
            tx = client.return_value.transact_write_items.call_args.kwargs["TransactItems"]
            self.assertNotIn("ownerSub", tx[0]["ConditionCheck"]["ConditionExpression"])
            self.assertEqual(tx[1]["Update"]["ExpressionAttributeValues"][":old"], {"S": "DRAFT"})

if __name__ == "__main__":
    unittest.main()

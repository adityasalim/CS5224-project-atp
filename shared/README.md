# Shared utilities

Pure Python helpers used by both the agent and the Lambda functions, so money and time are handled the same way
everywhere. No AWS calls and no model calls.

| File | What it does |
| --- | --- |
| `money.py` | Currency-explicit arithmetic: every amount carries its currency, with no implicit base currency |
| `trip_currency.py` | Converts provider prices into the trip currency before any budget arithmetic; an unknown price is `None`, never free |
| `hours_util.py` | Parses opening hours for one place on one date into usable time intervals |

`trip_currency.py` and `hours_util.py` also have copies inside the agent and the Lambda folders that use them,
because each is deployed as its own bundle. Change the file here first, then update the copies, and run `bash tests/offline/run_all.sh`
(`test_money.py`, `test_native_money.py` and `test_verifier_currency.py` cover them).

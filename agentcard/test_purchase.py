import json
import sys

from purchase import purchase

ADDRESS = {"street": "1900 Jefferson St", "city": "San Francisco", "state": "CA", "zip": "94123",
           "phone": "+14155550100", "name": "Ada Lovelace"}
ASK = "a 16 oz bag of Colombian ground coffee from Amazon"
CASES = [("within budget", 50.0, "sandbox_mode"), ("over budget", 1.0, "over_budget")]

ok = True
for name, cap, expected in CASES:
    r = purchase(ASK, cap, ADDRESS)
    passed = r.get("reason") == expected
    ok &= passed
    print(f"{'PASS' if passed else 'FAIL'} {name} (cap ${cap}, expected {expected})\n{json.dumps(r, indent=2)}\n")
sys.exit(0 if ok else 1)

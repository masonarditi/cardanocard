"""Check local AgentCard configuration. --authenticate makes one OAuth request."""
import argparse
from pathlib import Path

import httpx
from dotenv import dotenv_values

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--authenticate', action='store_true')
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
# Use the matching sandbox credentials supplied with Mason's user token file.
# Do not silently mix root credentials with tokens for a different organization.
values = dotenv_values(root / 'agentcard/.env')
print('Credential source: ../agentcard/.env (Mason sandbox handoff)')
keys = ('AGENTCARD_CLIENT_ID', 'AGENTCARD_CLIENT_SECRET')
for key in keys:
    print(key + ': ' + ('set' if values.get(key) else 'missing'))
print('Linked user tokens: ' + ('present' if (root / 'agentcard/.agentcard_tokens.json').exists() else 'missing'))
if not args.authenticate:
    raise SystemExit(0)
if not all(values.get(key) for key in keys):
    raise SystemExit('Configure organization credentials first')
try:
    response = httpx.post('https://api.agentcard.sh/api/v2/oauth/token', timeout=30, data={
        'grant_type':'client_credentials', 'client_id':values[keys[0]], 'client_secret':values[keys[1]]})
    print('Authentication HTTP status:', response.status_code)
    data = response.json()
    print('Access token issued:', bool(data.get('access_token')))
    print('Sandbox flag:', data.get('test_mode') if isinstance(data.get('test_mode'), bool) else 'not supplied')
    if response.status_code != 200 or not data.get('access_token'):
        raise SystemExit(1)
except Exception:
    raise SystemExit('Authentication check failed; no credentials or provider payload printed') from None

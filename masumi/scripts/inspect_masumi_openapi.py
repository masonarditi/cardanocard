"""Save a read-only local node OpenAPI snapshot for compatibility review.

Pass the actual JSON specification URL shown by the running node's /docs page.
This script never sets MASUMI_V1_COMPATIBLE or creates payments/wallets.
"""
import argparse
import hashlib
import json
from pathlib import Path

import httpx

from cardano_card.preprod_buyer import loopback_url

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('url', help='Actual local OpenAPI JSON URL')
parser.add_argument('--output', default='work/masumi-openapi.json')
args = parser.parse_args()
url = loopback_url(args.url)
try:
    response = httpx.get(url, timeout=15)
    response.raise_for_status()
    spec = response.json()
    if not spec.get('openapi') or not isinstance(spec.get('paths'), dict):
        raise ValueError('Not OpenAPI JSON')
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(spec, indent=2).encode()
    output.write_bytes(data)
    print('Saved OpenAPI snapshot:', output)
    print('SHA256:', hashlib.sha256(data).hexdigest())
    print('API version:', spec.get('info', {}).get('version', 'unspecified'))
    for path, methods in spec['paths'].items():
        if any(word in path for word in ('payment', 'purchase', 'registry', 'wallet')):
            print(','.join(k.upper() for k in methods if k in {'get','post','patch','delete'}), path)
    print('Review contracts, auth, hashes, timestamps, fee assets and real Preprod evidence before enabling the adapter.')
except Exception:
    raise SystemExit('Could not inspect the local OpenAPI document. No compatibility flag was changed.') from None

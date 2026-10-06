"""Read-only terminal inspection of the local Masumi node and Preprod wallets.

Never requests wallet mnemonics or prints API keys. --balances reads Blockfrost.
"""
import argparse
import json
from decimal import Decimal
from pathlib import Path

import httpx
from dotenv import dotenv_values

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--balances', action='store_true')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
values = dotenv_values(root / 'infra/masumi/.env')
wallets = []
try:
    with httpx.Client(base_url='http://127.0.0.1:3001/api/v1/', headers={'token':values.get('ADMIN_KEY','')}, timeout=20) as node:
        response = node.get('health/')
        response.raise_for_status()
        print('MASUMI NODE | local | health=' + str(response.json().get('status')))
        response = node.get('api-key-status/')
        response.raise_for_status()
        if response.json().get('status') != 'success':
            raise ValueError('Authentication failed')
        print('Authentication: verified')
        response = node.get('payment-source/')
        response.raise_for_status()
        sources = response.json()['data']['PaymentSources']
        for source in sources:
            if source['network'] != 'Preprod':
                continue
            print('Payment contract:', source['paymentType'])
            print('Contract address:', source['smartContractAddress'])
            for kind in ('PurchasingWallets','SellingWallets'):
                for wallet in source[kind]:
                    item = {key: wallet[key] for key in ('id','walletVkey','walletAddress','collectionAddress')}
                    item['type'] = 'Purchasing' if kind == 'PurchasingWallets' else 'Selling'
                    wallets.append(item)
                    print(item['type'] + ' wallet:', item['walletAddress'])
        if not wallets:
            raise ValueError('No Preprod wallets found')
    path = root / 'work/preprod-wallets.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({'network':'Preprod','wallets':wallets}, indent=2))
    path.chmod(0o600)
    if args.balances:
        key=values.get('BLOCKFROST_API_KEY_PREPROD','')
        if not key.startswith('preprod'):
            raise ValueError('Preprod key missing')
        with httpx.Client(base_url='https://cardano-preprod.blockfrost.io/api/v0/', headers={'project_id':key}, timeout=20) as chain:
            for wallet in wallets:
                response=chain.get('addresses/'+wallet['walletAddress'])
                if response.status_code == 404:
                    amount=0
                else:
                    response.raise_for_status()
                    amount=sum(int(x['quantity']) for x in response.json()['amount'] if x['unit']=='lovelace')
                print(wallet['type']+' balance:',str(Decimal(amount)/1000000),'test ADA')
    print('No payment, purchase, registration or token refresh was performed.')
except Exception:
    raise SystemExit('Node/wallet inspection incomplete. Check local setup; no raw provider error or secret printed.') from None

"""Execute-path smoke tests with all HTTP redirected to offline fixtures."""
import copy
import json
import sys
from types import ModuleType
from argparse import Namespace
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from cardano_card import acceptance, chain_evidence, preprod_setup
from cardano_card.models import digest
from test_acceptance import settings_and_snapshot
from test_lifecycle import PAYLOAD
from test_chain_evidence import (COLLECTION, REAL_BUYER, REAL_SELLER, REAL_CONTRACT, REAL_BUYER_KEY, REAL_SELLER_KEY)


@pytest.mark.parametrize('settlement_kind,expected_code', [('payout', 0), ('refund', 2)])
async def test_execute_preflight_to_proof_and_resume_without_repeating_writes(
        tmp_path, monkeypatch, settlement_kind, expected_code):
    # Keep the base test extra self-contained: the CLI wires the real escrow
    # adapter, while pinned-SDK hashing is replaced by deterministic fixtures.
    sdk = ModuleType('masumi')
    helpers = ModuleType('masumi.helper_functions')
    helpers.create_masumi_input_hash = lambda value, caller: digest({'input': value, 'caller': caller})
    helpers.create_masumi_output_hash = lambda value, caller: digest({'output': value, 'caller': caller})
    monkeypatch.setitem(sys.modules, 'masumi', sdk)
    monkeypatch.setitem(sys.modules, 'masumi.helper_functions', helpers)
    settings, snapshot = settings_and_snapshot()
    settings.update(NETWORK='Preprod', PAYMENT_SERVICE_URL='http://localhost/api/v1',
                    BUYER_PAYMENT_SERVICE_URL='http://localhost/api/v1',
                    PAYMENT_API_KEY='fixture-seller-key', BUYER_PAYMENT_API_KEY='fixture-buyer-key')
    policy = {'feePermille': 100, 'minFee': 1000000, 'minDeposit': 2000000,
              'note': 'opaque fixture forwarded unchanged to independent verifier'}
    snapshot['settlement_policy'] = policy
    settings.update(PAYOUT_ADDRESS=COLLECTION, BUYER_ADDRESS=REAL_BUYER, SELLER_ADDRESS=REAL_SELLER,
                    BUYER_VKEY=REAL_BUYER_KEY, SELLER_VKEY=REAL_SELLER_KEY, MASUMI_CONTRACT_ADDRESS=REAL_CONTRACT)
    snapshot['contract_address']=REAL_CONTRACT
    snapshot['registry'][0]['seller_vkey']=REAL_SELLER_KEY
    for role in ('buyer','seller'):
        snapshot['wallets'][role]['walletAddress']=settings[role.upper()+'_ADDRESS']
        snapshot['wallets'][role]['walletVkey']=settings[role.upper()+'_VKEY']
    snapshot['wallets']['seller']['collectionAddress']=COLLECTION
    env = tmp_path / '.env.preprod'
    env.write_text(''.join(f'{name}={value}\n' for name, value in settings.items()))
    node_env = tmp_path / '.env.node'
    node_env.write_text('ADMIN_KEY=fixture-admin\nBLOCKFROST_API_KEY_PREPROD=preprod-fixture\n')
    request = tmp_path / 'request.json'
    request.write_text(json.dumps(PAYLOAD['input_data']))
    args = Namespace(env=str(env), node_env=str(node_env), case='payout', execute=True,
                     request=str(request), request_id=PAYLOAD['identifier_from_purchaser'],
                     database=str(tmp_path / 'acceptance.db'), ascii=True, timeout=5,
                     poll_seconds=0, evidence_dir=str(tmp_path / 'evidence'))
    node_state = {'state': 'AwaitingPayment', 'terms': None, 'result_hash': None}
    calls, inspections, verifications = [], [], []

    async def inspect(self, seller_vkey=None, buyer_vkey=None):
        assert seller_vkey == settings['SELLER_VKEY']
        assert buyer_vkey == settings['BUYER_VKEY']
        inspections.append((seller_vkey, buyer_vkey))
        return copy.deepcopy(snapshot)

    monkeypatch.setattr(preprod_setup.PreprodSetup, 'inspect', inspect)

    def response(request, data):
        return httpx.Response(200, json={'status': 'success', 'data': data}, request=request)

    def handler(request):
        route = request.url.path.rstrip('/')
        calls.append((request.method, route))
        assert request.url.host == 'localhost', 'Unexpected external provider request'
        if route == '/api/v1/api-key-status':
            assert request.headers['token'] in (settings['PAYMENT_API_KEY'], settings['BUYER_PAYMENT_API_KEY'])
            return response(request, {'permission': 'ReadAndPay', 'networkLimit': ['Preprod'],
                                      'usageLimited': True, 'status': 'Active',
                                      'RemainingUsageCredits': [{'unit': '', 'amount': '10000000'}]})
        if route == '/api/v1/payment-source':
            return response(request, {'PaymentSources':[{'network':'Preprod','paymentType':'Web3CardanoV1',
                'smartContractAddress':REAL_CONTRACT,'SellingWallets':[{'walletVkey':REAL_SELLER_KEY,
                    'walletAddress':REAL_SELLER,'collectionAddress':COLLECTION}]}]})
        payload = json.loads(request.content)
        assert payload['network'] == 'Preprod'
        if route == '/api/v1/payment':
            assert request.headers['token'] == settings['PAYMENT_API_KEY']
            times = {key: str(int(datetime.fromisoformat(payload[key].replace('Z', '+00:00')).timestamp() * 1000))
                     for key in ('payByTime', 'submitResultTime', 'unlockTime', 'externalDisputeUnlockTime')}
            node_state['terms'] = dict(blockchainIdentifier='fixture-escrow', inputHash=payload['inputHash'],
                                       RequestedFunds=[{'unit': '', 'amount': '10000000'}], **times)
            return response(request, {**node_state['terms'], 'PaymentSource': {
                'network': 'Preprod', 'paymentType': 'Web3CardanoV1', 'smartContractAddress': REAL_CONTRACT},
                'SmartContractWallet': {'walletVkey': REAL_SELLER_KEY, 'walletAddress':REAL_SELLER, 'collectionAddress':COLLECTION}})
        if route == '/api/v1/purchase':
            assert request.headers['token'] == settings['BUYER_PAYMENT_API_KEY']
            assert payload['blockchainIdentifier'] == node_state['terms']['blockchainIdentifier']
            node_state['state'] = 'FundsLocked'
            return response(request, {})
        if route == '/api/v1/payment/submit-result':
            assert node_state['state'] == 'FundsLocked'
            node_state['result_hash'] = payload['submitResultHash']
            node_state['state'] = 'ResultSubmitted'
            return response(request, {})
        if route in ('/api/v1/payment/resolve-blockchain-identifier', '/api/v1/purchase/resolve-blockchain-identifier'):
            buyer_side = '/purchase/' in route
            if not buyer_side and node_state['state'] == 'ResultSubmitted':
                node_state['state'] = 'Withdrawn'
            return response(request, {**node_state['terms'], 'onChainState': node_state['state'],
                'resultHash': node_state['result_hash'],
                'PaymentSource': {'network': 'Preprod', 'paymentType': 'Web3CardanoV1', 'smartContractAddress': REAL_CONTRACT},
                'SmartContractWallet': {'walletVkey': REAL_BUYER_KEY if buyer_side else REAL_SELLER_KEY,
                                        'walletAddress':REAL_BUYER if buyer_side else REAL_SELLER,
                                        'collectionAddress':None if buyer_side else COLLECTION},
                'SellerWallet': {'walletVkey': REAL_SELLER_KEY}, 'PaidFunds': [{'unit': '', 'amount': '10000000'}],
                'CurrentTransaction': {'txHash': 'a' * 64}})
        raise AssertionError(f'Unexpected offline fixture route: {route}')

    original_client = httpx.AsyncClient

    def offline_client(*args, **kwargs):
        kwargs['transport'] = httpx.MockTransport(handler)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, 'AsyncClient', offline_client)

    class FixtureVerifier:
        def __init__(self, key, *, provider='blockfrost'):
            assert key == 'preprod-fixture' and provider == 'blockfrost'

        async def verify(self, job, *, buyer_vkey, seller_vkey, buyer_address, seller_address, payout_address, settlement_policy):
            assert job['phase'] == 'paid'
            assert buyer_vkey == REAL_BUYER_KEY and seller_vkey == REAL_SELLER_KEY
            assert buyer_address == REAL_BUYER and seller_address == REAL_SELLER
            assert payout_address == COLLECTION
            assert job['payment']['payoutAddress'] == COLLECTION
            assert settlement_policy == policy
            verifications.append(job['id'])
            return {'settlement_verified': True, 'settlement_kind': settlement_kind,
                    'funding_verified': True, 'result_verified': True}

        async def close(self):
            pass

    monkeypatch.setattr(chain_evidence, 'BlockfrostEvidence', FixtureVerifier)
    assert await acceptance.run(args) == expected_code
    files = list(Path(args.evidence_dir).glob('*.json'))
    assert len(files) == 1
    evidence = json.loads(files[0].read_text())
    assert evidence['node_complete'] is True
    assert evidence['acceptance_passed'] is (expected_code == 0)
    assert evidence['independent_chain_evidence']['settlement_kind'] == settlement_kind
    assert evidence['funding_attempt']['state'] == 'accepted_by_node'

    # A complete rerun revalidates configuration and rechecks proof, while neither
    # escrow creation nor funding/result submission is repeated. Lower balances
    # exercise the recovery branch after funds have already entered escrow.
    for wallet in snapshot['wallets'].values():
        wallet['balance_lovelace'] = 0
    assert await acceptance.run(args) == expected_code
    assert len(inspections) == len(verifications) == 2
    for route in ('/api/v1/payment', '/api/v1/purchase', '/api/v1/payment/submit-result'):
        assert calls.count(('POST', route)) == 1
    assert len(list(Path(args.evidence_dir).glob('*.json'))) == 1

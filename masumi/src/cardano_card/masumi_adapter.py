"""Preprod V1 adapter for Masumi node 0.22.0; live acceptance remains separate.

Wire formats are checked against the pinned node source. Hashes use the pinned
Python SDK helpers. Every side effect is checkpointed by the job coordinator.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx

from .chain_evidence import _address_bytes


class MasumiEscrow:
    # Pinned V1 collects no-result refunds after submitResultTime + its buffer.
    # authorize-refund is only valid for Disputed payments.
    automatic_requested_refund = True
    simulated = False

    def __init__(self, url, api_key, agent_identifier, seller_vkey, payout_address=None, allow_remote=False):
        parsed = urlparse(url)
        local = parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost'}
        # allow_remote: the node is reached through a tunnel (hosted runtime -> self-hosted V1 node); HTTPS only.
        remote_ok = allow_remote and parsed.scheme == 'https' and parsed.hostname
        if (not local and not remote_ok) or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('This Preprod adapter requires a local Payment Service URL (or MASUMI_ALLOW_REMOTE_NODE=true with HTTPS)')
        if not all((api_key, agent_identifier, seller_vkey)):
            raise ValueError('Preprod requires a Payment API key, registered agent, and seller verification key')
        if payout_address is not None:
            _address_bytes(payout_address)
        self.payout_address = payout_address
        from masumi.helper_functions import create_masumi_input_hash, create_masumi_output_hash
        self.input_hash, self.output_hash = create_masumi_input_hash, create_masumi_output_hash
        self.url, self.api_key = url.rstrip('/') + '/', api_key
        self.agent_identifier, self.seller_vkey = agent_identifier, seller_vkey
        for name in ('masumi.payment', 'masumi.purchase', 'masumi.helper_functions'):
            logging.getLogger(name).setLevel(logging.CRITICAL)

    async def post(self, route, payload):
        async with httpx.AsyncClient(base_url=self.url, headers={'token':self.api_key}, timeout=30) as client:
            response = await client.post(route.lstrip('/'), json=payload)
            response.raise_for_status()
            data = response.json()
            if data.get('status') != 'success' or not isinstance(data.get('data'), dict):
                raise ValueError('Unexpected node response')
            return data['data']

    async def get(self, route, params=None):
        async with httpx.AsyncClient(base_url=self.url, headers={'token': self.api_key}, timeout=30,
                                     follow_redirects=False) as client:
            response = await client.get(route.lstrip('/'), params=params)
            response.raise_for_status()
            data = response.json()
            if data.get('status') != 'success' or not isinstance(data.get('data'), dict):
                raise ValueError('Unexpected node response')
            return data['data']

    async def validate_payout(self, payment=None):
        """Read current public routing and bind it to configured/saved terms.

        A bounded single page intentionally fails closed when it may be
        truncated. No wallet setting is changed. Call again immediately before
        merchant checkout; observation and result submission also recheck it.
        """
        payment = payment or {}
        configured = getattr(self, 'payout_address', None)
        snapshot = payment.get('payoutAddress')
        if snapshot is not None:
            _address_bytes(snapshot)
        if configured is not None:
            _address_bytes(configured)
        if configured is not None and snapshot is not None and configured != snapshot:
            raise ValueError('Configured payout recipient differs from saved payment terms')
        expected = snapshot if snapshot is not None else configured
        if expected is None:
            return None  # Existing unconfigured service-fee jobs retain their behavior.
        if payment and (payment.get('sellerVKey') != self.seller_vkey or
                        not payment.get('smartContractAddress')):
            raise ValueError('Payment identity does not match payout routing')
        contract = payment.get('smartContractAddress')
        data = await self.get('payment-source/', {'take': 100})
        sources = data.get('PaymentSources')
        if not isinstance(sources, list) or len(sources) >= 100:
            raise ValueError('Payout source query is malformed or may be truncated')
        matches = []
        for source in sources:
            if not isinstance(source, dict):
                raise ValueError('Invalid payout source')
            if source.get('network') != 'Preprod' or source.get('paymentType') != 'Web3CardanoV1':
                continue
            if contract is not None and source.get('smartContractAddress') != contract:
                continue
            wallets = source.get('SellingWallets')
            if not isinstance(wallets, list):
                raise ValueError('Invalid selling wallet list')
            for wallet in wallets:
                if not isinstance(wallet, dict):
                    raise ValueError('Invalid selling wallet')
                if wallet.get('walletVkey') == self.seller_vkey:
                    matches.append((source, wallet))
        if len(matches) != 1:
            raise ValueError('Payout seller/source routing is missing or ambiguous')
        source, wallet = matches[0]
        contract = source.get('smartContractAddress')
        if _address_bytes(contract)[0] >> 4 != 7:
            raise ValueError('Payout source is not the expected testnet script address')
        signing_address = wallet.get('walletAddress')
        signing_bytes = _address_bytes(signing_address)
        if signing_bytes[0] >> 4 not in (0, 6) or signing_bytes[1:29].hex() != self.seller_vkey:
            raise ValueError('Seller wallet address does not match its verification key')
        # Mirrors pinned 0.22 collection-handler fallback exactly.
        actual = wallet.get('collectionAddress')
        if actual is None or actual == '':
            actual = signing_address
        _address_bytes(actual)
        if actual != expected:
            raise ValueError('Current seller collection address differs from saved payout recipient')
        return {'payoutAddress': actual, 'smartContractAddress': contract}

    @staticmethod
    def seconds(value):
        stamp = int(value)
        return stamp // 1000 if stamp > 100_000_000_000 else stamp

    @staticmethod
    def funds(value):
        if not isinstance(value, list) or not value:
            raise ValueError('Payment amounts are missing')
        result = []
        for amount in value:
            if not isinstance(amount['unit'], str) or not isinstance(amount['amount'], str) or not amount['amount'].isdigit() or int(amount['amount']) <= 0:
                raise ValueError('Invalid payment amount')
            result.append({'unit':amount['unit'], 'amount':str(int(amount['amount']))})
        if len({x['unit'] for x in result}) != len(result):
            raise ValueError('Duplicate payment assets')
        return sorted(result, key=lambda x:x['unit'])

    @staticmethod
    def deadlines(now):
        # Node 0.22.0 requires result >= now+15m, payBy <= result-5m,
        # unlock >= result+15m, dispute >= unlock+15m. Keep a margin.
        return {key:(now + timedelta(minutes=minutes)).isoformat(timespec='milliseconds').replace('+00:00','Z')
                for key, minutes in (('payByTime',5), ('submitResultTime',20), ('unlockTime',36), ('externalDisputeUnlockTime',52))}

    async def create(self, job):
        route = await self.validate_payout()
        expected = self.input_hash(job['wire_input'], job['caller_id'])
        data = await self.post('payment/', {'network':'Preprod', 'paymentType':'Web3CardanoV1',
            'agentIdentifier':self.agent_identifier, 'identifierFromPurchaser':job['caller_id'],
            'inputHash':expected, **self.deadlines(datetime.now(timezone.utc))})
        source = data.get('PaymentSource') or {}
        wallet = data.get('SmartContractWallet') or {}
        if (not data.get('blockchainIdentifier') or data.get('inputHash') != expected or
            source.get('network') != 'Preprod' or source.get('paymentType') != 'Web3CardanoV1' or
            wallet.get('walletVkey') != self.seller_vkey or
            (route is not None and source.get('smartContractAddress') != route['smartContractAddress'])):
            raise ValueError('Payment response identity does not match')
        result = {key:data[key] for key in ('blockchainIdentifier','payByTime','submitResultTime','unlockTime','externalDisputeUnlockTime')}
        result['rawTimes'] = {key:result[key] for key in result if key.endswith('Time')}
        for key in result['rawTimes']:
            result[key] = self.seconds(result[key])
        if not result['payByTime'] < result['submitResultTime'] <= result['unlockTime'] <= result['externalDisputeUnlockTime']:
            raise ValueError('Unexpected escrow deadline ordering')
        result.update(agentIdentifier=self.agent_identifier, sellerVKey=self.seller_vkey, inputHash=expected,
                      RequestedFunds=self.funds(data.get('RequestedFunds')), smartContractAddress=source['smartContractAddress'])
        if route is not None:
            result['payoutAddress'] = route['payoutAddress']
        return result

    async def observe(self, job):
        try:
            await self.validate_payout(job['payment'])
        except ValueError:
            return 'FundsOrDatumInvalid'
        data = await self.post('payment/resolve-blockchain-identifier', {'network':'Preprod',
            'blockchainIdentifier':job['payment']['blockchainIdentifier'], 'includeHistory':'true'})
        payment = job['payment']
        source = data.get('PaymentSource') or {}
        wallet = data.get('SmartContractWallet') or {}
        if (data.get('blockchainIdentifier') != payment['blockchainIdentifier'] or
            data.get('inputHash') != payment['inputHash'] or
            source.get('network') != 'Preprod' or source.get('paymentType') != 'Web3CardanoV1' or
            source.get('smartContractAddress') != payment['smartContractAddress'] or
            wallet.get('walletVkey') != self.seller_vkey or
            self.funds(data.get('RequestedFunds')) != payment['RequestedFunds']):
            return 'FundsOrDatumInvalid'
        state = data.get('onChainState') or 'AwaitingPayment'
        if state in {'ResultSubmitted', 'Withdrawn'} and job.get('result'):
            expected = self.output_hash(job['result'], job['caller_id'])
            if data.get('resultHash') != expected:
                return 'FundsOrDatumInvalid'
            job['result_hash'] = expected
        transactions = {item['tx_hash']: item for item in job.get('chain_transactions', [])}
        records = (data.get('TransactionHistory') or []) + [data.get('CurrentTransaction') or {}]
        for record in records:
            value = record.get('txHash')
            if isinstance(value, str) and re.fullmatch('[0-9a-fA-F]{64}', value):
                transactions.setdefault(value, {'tx_hash':value, 'network':'Preprod',
                    'reported_by':'masumi-node', 'verified_on_chain':False})
        job['chain_transactions'] = list(transactions.values())
        job['node_action'] = (data.get('NextAction') or {}).get('requestedAction')
        return state

    async def submit(self, job, result):
        await self.validate_payout(job['payment'])
        await self.post('payment/submit-result', {'network':'Preprod',
            'blockchainIdentifier':job['payment']['blockchainIdentifier'],
            'submitResultHash':self.output_hash(result, job['caller_id'])})

    async def authorize_refund(self, job):
        await self.post('payment/authorize-refund', {'network':'Preprod',
            'blockchainIdentifier':job['payment']['blockchainIdentifier']})

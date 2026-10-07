"""Preprod escrow through Masumi's hosted payment service (app.masumi.network, Web3CardanoV2).

Same engine contract as the local V1 adapter, different rail: HTTPS + `x-api-key`, the V2 contract, Dynamic pricing
(so RequestedFunds travels with the payment request) and `sellerReturnAddress` as the payout binding. The hosted
listing hides selling wallets, so identity is bound to the configured seller key and payout address on every read.
Request/response shapes come from the live hosted OpenAPI (work/hosted-openapi.json, 2026-10-07).
"""
import re
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal
from urllib.parse import urlparse

import httpx

from .chain_evidence import _address_bytes
from .masumi_adapter import MasumiEscrow

HOSTED_URL = 'https://app.masumi.network/pay/api/v1/'
SOURCE_TYPE = 'Web3CardanoV2'


class HostedMasumiEscrow(MasumiEscrow):
    # V2 exposes explicit refund authorization states; until a live run shows the hosted node collecting
    # requested refunds on its own, the engine asks for authorization (retried only while the node is idle).
    automatic_requested_refund = False
    simulated = False

    def __init__(self, url, api_key, agent_identifier, seller_vkey, payout_address, fee_lovelace, source_index=0,
                 lovelace_per_usd=None):
        parsed = urlparse(url)
        if url.rstrip('/') + '/' != HOSTED_URL or parsed.scheme != 'https':
            raise ValueError('The hosted adapter only talks to Masumi\'s hosted Preprod payment service')
        if not all((api_key, agent_identifier, seller_vkey, payout_address)):
            raise ValueError('Hosted escrow requires the SaaS API key, agent identifier, seller key and payout address')
        if not re.fullmatch('[0-9a-f]{56}', seller_vkey):
            raise ValueError('Seller verification key must be 56 hex characters')
        _address_bytes(payout_address)
        if not str(fee_lovelace).isdigit() or not 0 < int(fee_lovelace) <= 100_000_000:
            raise ValueError('Service fee must be a positive lovelace amount of at most 100 test ADA')
        from masumi.helper_functions import create_masumi_input_hash, create_masumi_output_hash
        self.input_hash, self.output_hash = create_masumi_input_hash, create_masumi_output_hash
        self.url, self.api_key = HOSTED_URL, api_key
        self.agent_identifier, self.seller_vkey = agent_identifier, seller_vkey
        self.payout_address, self.fee_lovelace = payout_address, str(int(fee_lovelace))
        # Index into the agent's registered supportedPaymentSources (the hosted V2 API requires it).
        self.source_index, self.contract = int(source_index), None
        # Dynamic pricing: escrow per job = the buyer's USD budget at a fixed demo rate, so the payout reimburses the
        # card that fronted the purchase. fee_lovelace is the floor (and the amount when no rate is configured).
        if lovelace_per_usd is not None and str(lovelace_per_usd).lower() != 'fixed' and (
                not str(lovelace_per_usd).isdigit() or int(lovelace_per_usd) <= 0):
            raise ValueError('MASUMI_LOVELACE_PER_USD must be a positive integer or "fixed"')
        self.lovelace_per_usd = (int(lovelace_per_usd) if lovelace_per_usd is not None and str(lovelace_per_usd).isdigit()
                                 else None)
        # Fixed-price agents (what Sokosumi can bill): the registry carries the price, so the request must not send
        # RequestedFunds; we accept the service's amount if it is a single positive ADA amount.
        self.fixed_price = str(lovelace_per_usd).lower() == 'fixed' if lovelace_per_usd is not None else False
        if self.fixed_price:
            self.lovelace_per_usd = None

    def client(self):
        # No redirects: a credential-bearing redirect off the fixed host must fail, never follow.
        return httpx.AsyncClient(base_url=self.url, headers={'x-api-key': self.api_key}, timeout=30,
                                 follow_redirects=False)

    async def post(self, route, payload):
        async with self.client() as client:
            response = await client.post(route.strip('/'), json=payload)
            response.raise_for_status()
            data = response.json()
            if data.get('status') != 'success' or not isinstance(data.get('data'), dict):
                raise ValueError('Unexpected hosted payment-service response')
            return data['data']

    async def get(self, route, params=None):
        async with self.client() as client:
            response = await client.get(route.strip('/'), params=params)
            response.raise_for_status()
            data = response.json()
            if data.get('status') != 'success' or not isinstance(data.get('data'), dict):
                raise ValueError('Unexpected hosted payment-service response')
            return data['data']

    async def validate_payout(self, payment=None):
        """Bind the hosted V2 Preprod source to the saved contract; the payout address is fixed by configuration."""
        payment = payment or {}
        snapshot = payment.get('payoutAddress')
        if snapshot is not None and snapshot != self.payout_address:
            raise ValueError('Configured payout recipient differs from saved payment terms')
        if payment and payment.get('sellerVKey') != self.seller_vkey:
            raise ValueError('Payment identity does not match payout routing')
        data = await self.get('payment-source', {'take': 100})
        sources = data.get('PaymentSources')
        if not isinstance(sources, list) or len(sources) >= 100:
            raise ValueError('Payout source query is malformed or may be truncated')
        matches = [s for s in sources if isinstance(s, dict) and s.get('network') == 'Preprod'
                   and s.get('paymentSourceType') == SOURCE_TYPE]
        contract = payment.get('smartContractAddress') or self.contract
        if contract is not None:
            matches = [s for s in matches if s.get('smartContractAddress') == contract]
        if len(matches) != 1:
            raise ValueError('Hosted Preprod V2 payment source is missing or ambiguous')
        contract = matches[0].get('smartContractAddress')
        if _address_bytes(contract)[0] >> 4 != 7:
            raise ValueError('Payout source is not a testnet script address')
        self.contract = contract
        return {'payoutAddress': self.payout_address, 'smartContractAddress': contract}

    def _identity_ok(self, data, expected_input_hash, contract):
        source = data.get('PaymentSource') or {}
        wallet = data.get('SmartContractWallet') or {}
        return (bool(data.get('blockchainIdentifier')) and data.get('inputHash') == expected_input_hash and
                source.get('network') == 'Preprod' and source.get('paymentSourceType') == SOURCE_TYPE and
                (contract is None or source.get('smartContractAddress') == contract) and
                wallet.get('walletVkey') == self.seller_vkey and
                data.get('agentIdentifier') in (None, self.agent_identifier) and
                data.get('sellerReturnAddress') in (None, self.payout_address))

    MAX_LOVELACE = 100_000_000  # 100 test ADA per job, same cap as the setup/acceptance tooling

    def escrow_lovelace(self, job):
        """Lovelace to lock for this job: budget x rate, floored at fee_lovelace, capped at 100 test ADA."""
        if self.lovelace_per_usd is None:
            return self.fee_lovelace
        budget = Decimal(str((job.get('input') or {}).get('max_total_usd') or '0'))
        amount = int((budget * self.lovelace_per_usd).to_integral_value(rounding=ROUND_CEILING))
        return str(min(max(amount, int(self.fee_lovelace)), self.MAX_LOVELACE))

    async def create(self, job):
        route = await self.validate_payout()
        expected = self.input_hash(job['wire_input'], job['caller_id'])
        lovelace = None if self.fixed_price else self.escrow_lovelace(job)
        payload = {'network': 'Preprod', 'paymentSourceType': SOURCE_TYPE, 'agentIdentifier': self.agent_identifier,
                   'identifierFromPurchaser': job['caller_id'], 'inputHash': expected,
                   'supportedPaymentSourceIndex': self.source_index,
                   'sellerReturnAddress': self.payout_address, **self.deadlines(datetime.now(timezone.utc))}
        if lovelace is not None:
            payload['RequestedFunds'] = [{'unit': '', 'amount': lovelace}]
        data = await self.post('payment', payload)
        if not self._identity_ok(data, expected, route['smartContractAddress']):
            raise ValueError('Payment response identity does not match')
        result = {key: data[key] for key in ('blockchainIdentifier', 'payByTime', 'submitResultTime', 'unlockTime',
                                              'externalDisputeUnlockTime')}
        result['rawTimes'] = {key: result[key] for key in result if key.endswith('Time')}
        for key in result['rawTimes']:
            result[key] = self.seconds(result[key])
        if not result['payByTime'] < result['submitResultTime'] <= result['unlockTime'] <= result['externalDisputeUnlockTime']:
            raise ValueError('Unexpected escrow deadline ordering')
        funds = self.funds(data.get('RequestedFunds'))
        if lovelace is not None and funds != [{'unit': '', 'amount': lovelace}]:
            raise ValueError('Hosted service changed the requested escrow amount')
        if lovelace is None and (len(funds) != 1 or funds[0]['unit'] != '' or int(funds[0]['amount']) > self.MAX_LOVELACE):
            raise ValueError('Fixed-price agent must resolve to a single ADA amount of at most 100 test ADA')
        result.update(agentIdentifier=self.agent_identifier, sellerVKey=self.seller_vkey, inputHash=expected,
                      RequestedFunds=funds, smartContractAddress=route['smartContractAddress'],
                      payoutAddress=self.payout_address, paymentSourceType=SOURCE_TYPE, rail='hosted-v2',
                      supportedPaymentSourceIndex=self.source_index)
        return result

    async def observe(self, job):
        payment = job['payment']
        try:
            await self.validate_payout(payment)
        except ValueError:
            return 'FundsOrDatumInvalid'
        data = await self.post('payment/resolve-blockchain-identifier', {
            'network': 'Preprod', 'blockchainIdentifier': payment['blockchainIdentifier'], 'includeHistory': 'true'})
        if (data.get('blockchainIdentifier') != payment['blockchainIdentifier'] or
                not self._identity_ok(data, payment['inputHash'], payment['smartContractAddress']) or
                self.funds(data.get('RequestedFunds')) != payment['RequestedFunds']):
            return 'FundsOrDatumInvalid'
        state = data.get('onChainState') or 'AwaitingPayment'
        if state in {'ResultSubmitted', 'Withdrawn', 'WithdrawAuthorized'} and job.get('result'):
            expected = self.output_hash(job['result'], job['caller_id'])
            if data.get('resultHash') != expected:
                return 'FundsOrDatumInvalid'
            job['result_hash'] = expected
        transactions = {item['tx_hash']: item for item in job.get('chain_transactions', [])}
        records = (data.get('TransactionHistory') or []) + [data.get('CurrentTransaction') or {}]
        for record in records:
            value = record.get('txHash') if isinstance(record, dict) else None
            if isinstance(value, str) and re.fullmatch('[0-9a-fA-F]{64}', value):
                transactions.setdefault(value, {'tx_hash': value, 'network': 'Preprod',
                                                'reported_by': 'masumi-hosted', 'verified_on_chain': False})
        job['chain_transactions'] = list(transactions.values())
        job['node_action'] = (data.get('NextAction') or {}).get('requestedAction')
        # V2 authorization states are intermediate: the engine treats them like their settled neighbours.
        return {'WithdrawAuthorized': 'ResultSubmitted', 'RefundAuthorized': 'RefundRequested'}.get(state, state)

    async def submit(self, job, result):
        await self.validate_payout(job['payment'])
        await self.post('payment/submit-result', {'network': 'Preprod',
            'blockchainIdentifier': job['payment']['blockchainIdentifier'],
            'submitResultHash': self.output_hash(result, job['caller_id'])})

    async def authorize_refund(self, job):
        await self.post('payment/authorize-refund', {'network': 'Preprod',
            'blockchainIdentifier': job['payment']['blockchainIdentifier']})

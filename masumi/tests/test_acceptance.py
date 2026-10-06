"""Offline acceptance orchestration tests. No provider or wallet calls."""
import copy
import time
from argparse import Namespace

import httpx
import pytest

from cardano_card.acceptance import AcceptanceRunner, NodeRoutes, assert_sandbox_reconciled, run, validate_preflight
from cardano_card.agentcard_bridge import AgentCardPurchaser, ReplayTransport
from cardano_card.engine import Engine
from cardano_card.models import StartRequest, digest
from cardano_card.preprod_buyer import PreprodBuyer
from cardano_card.providers import FakeEscrow
from cardano_card.store import Store
from test_lifecycle import PAYLOAD


class TestEscrow(FakeEscrow):
    __test__ = False
    simulated = False  # Synthetic node fixture; exercises the real-mode gate only.

    def __init__(self, store):
        super().__init__(store)
        self.submissions = 0

    async def create(self, job):
        payment = await super().create(job)
        payment.update(blockchainIdentifier='fixture-' + job['id'], agentIdentifier='agent', sellerVKey='seller',
                       inputHash=digest({'input':job['wire_input'],'caller':job['caller_id']}),
                       RequestedFunds=[{'unit':'','amount':'10000000'}], smartContractAddress='addr_test1contract')
        payment['rawTimes'] = {k:str(payment[k]*1000) for k in ('payByTime','submitResultTime','unlockTime','externalDisputeUnlockTime')}
        return payment

    async def submit(self, job, result):
        self.submissions += 1
        await super().submit(job, result)


class TestNode:
    __test__ = False
    def __init__(self, store):
        self.store, self.calls, self.lose_funding = store, [], False

    async def post(self, route, json):
        self.calls.append(route)
        job = next(j for j in self.store.jobs() if j['payment']['blockchainIdentifier']==json['blockchainIdentifier'])
        self.store.put('escrow',job['id'],{'state':'FundsLocked' if route=='/purchase/' else 'RefundRequested'})
        if route=='/purchase/' and self.lose_funding:
            raise httpx.ReadTimeout('fixture lost response after acceptance')
        return httpx.Response(200,json={'status':'success','data':{}},request=httpx.Request('POST','http://localhost/'))

    async def observe(self, job):
        return {'state':self.store.get('escrow',job['id'])['state'],'tx_hashes':[], 'node_action':None}


def make_runner(store, case='payout', identity='fixture-node'):
    escrow, node = TestEscrow(store), TestNode(store)
    transport = ReplayTransport(store,'success' if case=='payout' else 'declined')
    buyer = PreprodBuyer(store,node,'agent','seller',lambda data,caller:digest({'input':data,'caller':caller}),
                        identity,expected_funds=[{'unit':'','amount':'10000000'}])
    engine = Engine(store,escrow,AgentCardPurchaser(store,transport))
    runner = AcceptanceRunner(engine,buyer,case=case,identity=identity,observe_buyer=node.observe)
    return runner, node, escrow


async def test_payout_and_evidence_require_independent_correct_branch_proof(tmp_path):
    store=Store(str(tmp_path/'jobs.db'))
    try:
        runner,node,escrow=make_runner(store)
        job=await runner.start(StartRequest.model_validate(PAYLOAD))
        for _ in range(3):job=await runner.step(job['id'])
        assert job['phase']=='result_submitted'
        assert escrow.submissions==1 and node.calls==['/purchase/']
        assert store.get('agentcard_replay',job['id'])['confirms']==1
        store.put('escrow',job['id'],{'state':'Withdrawn'})
        job=await runner.step(job['id'])
        assert job['phase']=='paid'
        assert runner.evidence(job)['node_complete']
        assert not runner.evidence(job)['acceptance_passed']
        wrong={'settlement_verified':True,'settlement_kind':'refund','funding_verified':True,'result_verified':True}
        assert not runner.evidence(job,wrong)['acceptance_passed']
        right={**wrong,'settlement_kind':'payout'}
        assert runner.evidence(job,right)['acceptance_passed']
    finally:store.close()


async def test_refund_writes_once_and_never_submits_result(tmp_path):
    store=Store(str(tmp_path/'jobs.db'))
    try:
        runner,node,escrow=make_runner(store,'refund')
        request=StartRequest.model_validate(PAYLOAD)
        job=await runner.start(request)
        for _ in range(4):job=await runner.step(job['id'])
        assert job['phase']=='refunded'
        await runner.start(request)
        await runner.step(job['id'])
        assert node.calls==['/purchase/','/purchase/request-refund']
        assert escrow.submissions==0
    finally:store.close()


async def test_restart_reconciles_unknown_funding_without_second_post(tmp_path):
    path=str(tmp_path/'jobs.db')
    store=Store(path)
    runner,node,_=make_runner(store)
    node.lose_funding=True
    job=await runner.start(StartRequest.model_validate(PAYLOAD))
    assert store.get('buyer_writes','fund:'+job['id'])['state']=='unknown'
    store.close()
    store=Store(path)
    try:
        resumed,resumed_node,_=make_runner(store)
        await resumed.start(StartRequest.model_validate(PAYLOAD))
        job=await resumed.step(job['id'])
        assert job['purchase_started']
        assert resumed_node.calls==[]
    finally:store.close()


async def test_changed_request_or_caller_cannot_start_second_job(tmp_path):
    store=Store(str(tmp_path/'jobs.db'))
    try:
        runner,node,_=make_runner(store)
        await runner.start(StartRequest.model_validate(PAYLOAD))
        changed=copy.deepcopy(PAYLOAD);changed['input_data']['ask']='different'
        with pytest.raises(ValueError,match='changed'):await runner.start(StartRequest.model_validate(changed))
        changed=copy.deepcopy(PAYLOAD);changed['identifier_from_purchaser']='b'*26
        with pytest.raises(ValueError,match='another request'):await runner.start(StartRequest.model_validate(changed))
        assert node.calls==['/purchase/'] and len(store.jobs())==1
        with pytest.raises(ValueError,match='original case'):make_runner(store,'refund')
        with pytest.raises(ValueError,match='original case'):make_runner(store,identity='other-node')
    finally:store.close()


async def test_expired_job_observes_late_funding_and_refunds_without_purchase(tmp_path):
    store=Store(str(tmp_path/'jobs.db'))
    try:
        runner,node,escrow=make_runner(store,'refund')
        job=await runner.start(StartRequest.model_validate(PAYLOAD))
        job['phase']='expired';store.put('jobs',job['id'],job)
        job=await runner.step(job['id'])
        assert job['phase']=='refund_due' and not job['purchase_started']
        assert node.calls==['/purchase/','/purchase/request-refund']
        assert escrow.submissions==0
    finally:store.close()


async def test_missing_buyer_observation_never_starts_checkout(tmp_path):
    store=Store(str(tmp_path/'jobs.db'))
    try:
        runner,_,_=make_runner(store)
        job=await runner.start(StartRequest.model_validate(PAYLOAD))
        async def absent(_):return None
        runner.observe_buyer=absent
        job=await runner.step(job['id'])
        assert not job['purchase_started']
    finally:store.close()


def test_prior_sandbox_uncertainty_is_not_bypassed_by_new_database(tmp_path):
    path=tmp_path/'prior.db'
    with pytest.raises(ValueError,match='Restore'):assert_sandbox_reconciled(path)
    store=Store(str(path));store.put('jobs','job',{'simulated_purchase':False,'phase':'reconciling'});store.close()
    with pytest.raises(ValueError,match='reconciliation'):assert_sandbox_reconciled(path)
    store=Store(str(path));store.put('jobs','job',{'simulated_purchase':False,'phase':'refunded'});store.close()
    assert_sandbox_reconciled(path)


def settings_and_snapshot():
    settings={'AGENT_IDENTIFIER':'agent','SELLER_VKEY':'seller','BUYER_VKEY':'buyer','MASUMI_FEE_LOVELACE':'10000000',
              'MASUMI_CONTRACT_ADDRESS':'contract','BUYER_ADDRESS':'buyer-address','SELLER_ADDRESS':'seller-address',
              'PAYOUT_ADDRESS':'seller-address'}
    snapshot={'schema_verified':True,'contract_address':'contract',
              'buyer_usage_credits':[{'unit':'','amount':'10000000'}],
              'registry':[{'agent_identifier':'agent','state':'RegistrationConfirmed','seller_vkey':'seller',
                           'fee':{'Pricing':[{'unit':'','amount':'10000000'}]}}],
              'wallets':{role:{'walletVkey':role,'walletAddress':role+'-address','balance_lovelace':20000000} for role in ('seller','buyer')}}
    return settings,snapshot


def test_balance_floor_blocks_new_job_but_not_saved_funding_recovery():
    settings,snapshot=settings_and_snapshot()
    validate_preflight(snapshot,settings)
    snapshot['wallets']['buyer']['balance_lovelace']=0
    with pytest.raises(ValueError,match='Fund both'):validate_preflight(snapshot,settings)
    validate_preflight(snapshot,settings,require_funding=False)


@pytest.mark.parametrize('change',['schema','contract','registry','fee','seller','buyer'])
def test_preflight_mismatches_block_even_during_resume(change):
    settings,snapshot=settings_and_snapshot()
    if change=='schema':snapshot['schema_verified']=False
    elif change=='contract':snapshot['contract_address']='other'
    elif change=='registry':snapshot['registry'][0]['state']='RegistrationRequested'
    elif change=='fee':snapshot['registry'][0]['fee']['Pricing'][0]['amount']='20000000'
    else:snapshot['wallets'][change]['walletVkey']='wrong'
    with pytest.raises(ValueError):validate_preflight(snapshot,settings,require_funding=False)


async def test_default_preflight_missing_credentials_has_no_provider_calls(tmp_path,capsys):
    args=Namespace(env=str(tmp_path/'missing'),node_env=str(tmp_path/'missing-node'),case='payout',execute=False)
    assert await run(args)==2
    assert 'BLOCKED' in capsys.readouterr().out


@pytest.mark.parametrize('wrong',['buyer','seller','hash','network','amount','deadline','pending_wallet','missing_confirmed_wallet',None])
async def test_buyer_observation_identity_before_purchase(wrong):
    payment={'blockchainIdentifier':'escrow','inputHash':'input','sellerVKey':'seller','smartContractAddress':'contract',
             'RequestedFunds':[{'unit':'','amount':'10000000'}], 'rawTimes':{'payByTime':'2000000000000'}}
    data={'blockchainIdentifier':'escrow','inputHash':'input','SellerWallet':{'walletVkey':'seller'},
          'SmartContractWallet':{'walletVkey':'buyer'},'PaidFunds':payment['RequestedFunds'],
          'PaymentSource':{'network':'Preprod','paymentType':'Web3CardanoV1','smartContractAddress':'contract'},
          'payByTime':'2000000000000','CurrentTransaction':{'txHash':'a'*64}}
    if wrong=='buyer':data['SmartContractWallet']['walletVkey']='other'
    elif wrong=='seller':data['SellerWallet']['walletVkey']='other'
    elif wrong=='hash':data['inputHash']='other'
    elif wrong=='network':data['PaymentSource']['network']='Mainnet'
    elif wrong=='amount':data['PaidFunds']=[{'unit':'','amount':'999'}]
    elif wrong=='deadline':data['payByTime']='1000000000000'
    elif wrong=='pending_wallet':data['SmartContractWallet']=None
    elif wrong=='missing_confirmed_wallet':
        data['SmartContractWallet']=None
        data['onChainState']='FundsLocked'
    def handler(req):
        assert req.url.path=='/api/v1/purchase/resolve-blockchain-identifier'
        return httpx.Response(200,json={'status':'success','data':data})
    async with httpx.AsyncClient(base_url='http://localhost/api/v1/',transport=httpx.MockTransport(handler)) as client:
        node=NodeRoutes(client)
        if wrong=='pending_wallet':
            assert await node.observe_buyer({'payment':payment},'buyer') is None
        elif wrong:
            with pytest.raises(ValueError):await node.observe_buyer({'payment':payment},'buyer')
        else:
            assert (await node.observe_buyer({'payment':payment},'buyer'))['tx_hashes']==['a'*64]


def test_preflight_binds_explicit_collection_recipient_even_during_resume():
    settings,snapshot=settings_and_snapshot()
    snapshot['wallets']['seller']['collectionAddress']='collection-address'
    with pytest.raises(ValueError,match='payout address'):
        validate_preflight(snapshot,settings,require_funding=False)
    settings['PAYOUT_ADDRESS']='collection-address'
    validate_preflight(snapshot,settings,require_funding=False)
    settings['PAYOUT_ADDRESS']='other-address'
    with pytest.raises(ValueError,match='payout address'):
        validate_preflight(snapshot,settings,require_funding=False)


def test_legacy_preflight_fallback_only_matches_seller_recipient():
    settings,snapshot=settings_and_snapshot()
    del settings['PAYOUT_ADDRESS']
    validate_preflight(snapshot,settings)
    snapshot['wallets']['seller']['collectionAddress']='collection-address'
    with pytest.raises(ValueError,match='payout address'):
        validate_preflight(snapshot,settings)


async def test_unconfirmed_funding_without_buyer_record_stops_before_checkout(tmp_path):
    from cardano_card.acceptance import FundingNeedsReconciliation
    store = Store(str(tmp_path / 'jobs.db'))
    try:
        runner, node, _ = make_runner(store)
        async def rejected(route, json):
            node.calls.append(route)
            return httpx.Response(400, request=httpx.Request('POST', 'http://localhost/purchase/'))
        async def absent(job):
            return None
        node.post = rejected
        runner.observe_buyer = absent
        job = await runner.start(StartRequest.model_validate(PAYLOAD))
        with pytest.raises(FundingNeedsReconciliation):
            await runner.step(job['id'])
        assert not runner.engine.get(job['id'])['purchase_started']
        evidence = runner.evidence(job)
        assert evidence['funding_attempt']['http_status'] == 400
        assert not evidence['acceptance_passed']
        await runner.start(StartRequest.model_validate(PAYLOAD))
        assert node.calls == ['/purchase/']
    finally:
        store.close()


@pytest.mark.parametrize('credits', [None, [], [{'unit':'','amount':'9999999'}], [{'unit':'other','amount':'10000000'}]])
def test_key_allowance_blocks_new_funding_but_allows_reconciliation(credits):
    settings, snapshot = settings_and_snapshot()
    snapshot['buyer_usage_credits'] = credits
    with pytest.raises(ValueError, match='usage credits'):
        validate_preflight(snapshot, settings)
    validate_preflight(snapshot, settings, require_funding=False)


class FakeMasonModule:
    """Mason's v1 contract, sandbox flavour: confirm is declined with sandbox_mode."""
    def __init__(self, result):
        self.result, self.calls = result, []

    def purchase(self, ask, max_total_usd, address, request_id=None):
        self.calls.append(request_id)
        return self.result

    def inspect_purchase(self, request_id):
        return self.result


def make_mason_runner(store, case, result):
    from cardano_card.providers import ModulePurchaser
    escrow, node = TestEscrow(store), TestNode(store)
    buyer = PreprodBuyer(store,node,'agent','seller',lambda data,caller:digest({'input':data,'caller':caller}),
                        'fixture-node',expected_funds=[{'unit':'','amount':'10000000'}])
    purchaser = ModulePurchaser.__new__(ModulePurchaser)
    purchaser.module = FakeMasonModule(result)
    engine = Engine(store,escrow,purchaser)
    return AcceptanceRunner(engine,buyer,case=case,identity='fixture-node',observe_buyer=node.observe), node, escrow, purchaser.module


async def test_mason_sandbox_decline_refunds_through_real_module_path(tmp_path):
    store=Store(str(tmp_path/'jobs.db'))
    try:
        runner,node,escrow,module=make_mason_runner(store,'mason-sandbox-refund',
            {'status':'failed','reason':'sandbox_mode','conversation_id':'c1'})
        job=await runner.start(StartRequest.model_validate(PAYLOAD))
        for _ in range(4):job=await runner.step(job['id'])
        assert job['phase']=='refunded' and module.calls==[job['id']] and escrow.submissions==0
        assert runner.evidence(job)['node_complete']
    finally:store.close()


async def test_mason_payout_pays_only_for_a_real_order(tmp_path):
    store=Store(str(tmp_path/'jobs.db'))
    try:
        runner,node,escrow,module=make_mason_runner(store,'mason-payout',
            {'status':'success','order_id':'ord_1','total_usd':1.32,'merchant':'Amazon','items':[{'name':'gum','qty':1}]})
        job=await runner.start(StartRequest.model_validate(PAYLOAD))
        for _ in range(3):job=await runner.step(job['id'])
        assert job['phase']=='result_submitted' and escrow.submissions==1
        store.put('escrow',job['id'],{'state':'Withdrawn'})
        job=await runner.step(job['id'])
        proof={'settlement_verified':True,'settlement_kind':'payout','funding_verified':True,'result_verified':True}
        assert runner.evidence(job,proof)['acceptance_passed']
    finally:store.close()


async def test_mason_pending_never_refunds_or_pays(tmp_path):
    store=Store(str(tmp_path/'jobs.db'))
    try:
        runner,node,escrow,module=make_mason_runner(store,'mason-sandbox-refund',
            {'status':'pending','reason':'unknown','conversation_id':'c1','detail':'checkout confirming'})
        job=await runner.start(StartRequest.model_validate(PAYLOAD))
        for _ in range(4):job=await runner.step(job['id'])
        assert job['phase']=='reconciling' and escrow.submissions==0
        assert node.calls==['/purchase/'] and len(module.calls)==1
    finally:store.close()


@pytest.mark.parametrize('case,env,flags,files,message', [
    ('mason-payout', {}, {'allow_real_card': True}, ['.env.prod', '.agentcard_tokens.prod.json'], 'AGENTCARD_ENV=prod'),
    ('mason-payout', {'AGENTCARD_ENV': 'prod'}, {}, ['.env.prod', '.agentcard_tokens.prod.json'], 'allow-real-card'),
    ('mason-sandbox-refund', {'AGENTCARD_ENV': 'prod'}, {}, ['.env', '.agentcard_tokens.json'], 'must not run'),
    ('mason-sandbox-refund', {}, {'exclusive_handoff': False}, ['.env', '.agentcard_tokens.json'], 'exclusive'),
    ('mason-sandbox-refund', {}, {}, ['.env'], 'agentcard_tokens'),
])
def test_mason_cases_are_bound_to_the_card_environment(tmp_path, case, env, flags, files, message):
    from cardano_card.acceptance import check_mason_case
    (tmp_path/'purchase.py').write_text('')
    for name in files:(tmp_path/name).write_text('{}')
    args=Namespace(case=case, agentcard_dir=str(tmp_path), **{'allow_real_card':False,'exclusive_handoff':True, **flags})
    with pytest.raises(ValueError, match=message):
        check_mason_case(args, env)


def test_mason_sandbox_case_ready(tmp_path):
    from cardano_card.acceptance import check_mason_case
    for name in ('purchase.py','.env','.agentcard_tokens.json'):(tmp_path/name).write_text('{}')
    check_mason_case(Namespace(case='mason-sandbox-refund',agentcard_dir=str(tmp_path),allow_real_card=False,exclusive_handoff=True),{})

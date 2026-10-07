from unittest.mock import AsyncMock

import httpx
import pytest

from cardano_card.api import create_app, configured_engine
from cardano_card.engine import Conflict
from cardano_card.models import ProvideInput, StartRequest
from cardano_card.providers import FakeEscrow
from cardano_card.staged_engine import StagedEngine
from cardano_card.staged_purchase import FakeStagedModule, StagedModule
from cardano_card.store import Store
from test_lifecycle import PAYLOAD


@pytest.fixture
def engine(tmp_path):
    store = Store(str(tmp_path / 'staged.db'))
    module = FakeStagedModule(store)
    engine = StagedEngine(store, FakeEscrow(store), module, escrow_lovelace=10_000_000, payout_address='SIM-payout')
    yield engine
    store.close()


async def quoted(engine):
    job = await engine.start(StartRequest.model_validate(PAYLOAD))
    await engine.tick()
    return engine.get(job['id'])


async def approved(engine, job):
    await engine.provide(ProvideInput(job_id=job['id'], input_schema_hash=job['input_schema_hash'], input_data={'approved': True}))
    await engine.tick()
    return engine.get(job['id'])


async def funded(engine, job):
    job = await approved(engine, job)
    await engine.simulate(job['id'], 'fund')
    await engine.tick()
    return engine.get(job['id'])


async def test_quote_approval_funding_order_and_separate_payout(engine):
    job = await quoted(engine)
    assert job['phase'] == 'awaiting_quote_approval' and job['payment'] is None
    assert engine.store.get('staged_fake', job['id'])['confirmations'] == 0
    with pytest.raises(Conflict):
        await engine.simulate(job['id'], 'fund')
    job = await approved(engine, job)
    assert job['phase'] == 'awaiting_payment'
    assert job['payment']['RequestedFunds'] == [{'unit': '', 'amount': '10000000'}]
    assert job['payment']['payoutAddress'] == 'SIM-payout'
    await engine.tick()
    assert engine.store.get('staged_fake', job['id'])['confirmations'] == 0
    await engine.simulate(job['id'], 'fund')
    await engine.tick()
    assert engine.get(job['id'])['phase'] == 'submitting_result'
    await engine.tick()
    assert engine.get(job['id'])['phase'] == 'result_submitted'
    assert engine.evidence(engine.get(job['id']))['settlement']['verified_on_chain'] is False
    await engine.simulate(job['id'], 'withdraw')
    await engine.tick()
    assert engine.get(job['id'])['phase'] == 'paid'
    assert engine.store.get('staged_fake', job['id'])['confirmations'] == 1


async def test_rejection_and_stale_approval_never_create_payment(engine):
    job = await quoted(engine)
    with pytest.raises(Conflict):
        await engine.provide(ProvideInput(job_id=job['id'], input_schema_hash='f'*64, input_data={'approved': True}))
    await engine.provide(ProvideInput(job_id=job['id'], input_schema_hash=job['input_schema_hash'], input_data={'approved': False}))
    await engine.tick()
    assert engine.get(job['id'])['phase'] == 'quote_rejected'
    assert engine.store.get('escrow', job['id']) is None


@pytest.mark.parametrize('field,value', [('authorization_ceiling_cents', True), ('currency', 'EUR'),
    ('authorization_ceiling_cents', 1), ('job_id', 'wrong'), ('payment_source', 'issued')])
async def test_invalid_quote_never_reaches_funding(engine, field, value):
    original = engine.module.prepare_purchase
    async def prepare(**kw):
        raw = await original(**kw)
        raw[field] = value
        return raw
    engine.module.prepare_purchase = prepare
    job = await quoted(engine)
    assert job['phase'] == 'quote_reconciling' and job['payment'] is None


async def test_ceiling_not_subtotal_controls_budget(engine):
    engine.module.scenario = 'over_budget'
    job = await quoted(engine)
    assert job['phase'] == 'quote_rejected'
    assert engine.store.get('staged_fake', job['id'])['confirmations'] == 0


async def test_preparation_timeout_is_inspection_only(engine):
    original = engine.module.prepare_purchase
    calls = []
    async def prepare(**kw):
        calls.append(kw)
        await original(**kw)
        raise TimeoutError()
    engine.module.prepare_purchase = prepare
    job = await quoted(engine)
    assert job['phase'] == 'quote_reconciling'
    await engine.tick()
    assert engine.get(job['id'])['phase'] == 'awaiting_quote_approval'
    assert len(calls) == 1


async def test_expired_quote_after_funding_refunds_without_checkout(engine):
    job = await approved(engine, await quoted(engine))
    engine.purchaser.clock = lambda: job['quote']['expires_at'] + 1
    await engine.simulate(job['id'], 'fund')
    await engine.tick()
    assert engine.get(job['id'])['phase'] == 'refund_due'
    assert engine.store.get('staged_fake', job['id'])['confirmations'] == 0
    await engine.simulate(job['id'], 'request_refund')
    await engine.tick()
    await engine.tick()
    assert engine.get(job['id'])['phase'] == 'refunded'


@pytest.mark.parametrize('scenario', ['unknown', 'partial'])
async def test_unknown_or_partial_cannot_refund_or_repeat(engine, scenario):
    engine.module.scenario = scenario
    job = await funded(engine, await quoted(engine))
    assert job['phase'] in {'processing', 'reconciling'}
    for _ in range(3):
        await engine.tick()
    assert engine.store.get('staged_fake', job['id'])['confirmations'] == 1
    await engine.simulate(job['id'], 'request_refund')
    await engine.tick()
    # Keeps inspecting (never refunds or reconfirms) while the purchase is unresolved.
    assert engine.get(job['id'])['phase'] in {'processing', 'reconciling'}
    assert engine.store.get('staged_fake', job['id'])['confirmations'] == 1


async def test_confirm_timeout_restart_inspects_only(engine):
    original = engine.module.confirm_purchase
    async def confirm(**kw):
        await original(**kw)
        raise TimeoutError()
    engine.module.confirm_purchase = confirm
    job = await funded(engine, await quoted(engine))
    assert job['phase'] == 'reconciling'
    module = FakeStagedModule(engine.store)
    module.confirm_purchase = AsyncMock(side_effect=AssertionError('must not confirm again'))
    recovered = StagedEngine(engine.store, engine.escrow, module, escrow_lovelace=10_000_000, payout_address='SIM-payout')
    await recovered.tick()
    assert recovered.get(job['id'])['phase'] == 'submitting_result'
    module.confirm_purchase.assert_not_called()
    assert engine.store.get('staged_fake', job['id'])['confirmations'] == 1


@pytest.mark.parametrize('field,value', [('merchant_confirmed', False), ('total_cents', 99999),
    ('cart_hash', 'different'), ('status', 'partial'), ('no_purchase', True)])
async def test_unproven_or_wrong_order_does_not_submit_result(engine, field, value):
    original = engine.module.confirm_purchase
    async def confirm(**kw):
        raw = await original(**kw)
        raw[field] = value
        return raw
    engine.module.confirm_purchase = confirm
    job = await funded(engine, await quoted(engine))
    assert job['phase'] == 'reconciling' and job['result'] is None
    assert engine.store.get('escrow', job['id'])['state'] == 'FundsLocked'


async def test_explicit_no_purchase_and_no_charge_can_refund(engine):
    engine.module.scenario = 'declined'
    job = await funded(engine, await quoted(engine))
    assert job['phase'] == 'refund_due'
    await engine.simulate(job['id'], 'request_refund')
    await engine.tick()
    await engine.tick()
    assert engine.get(job['id'])['phase'] == 'refunded'


async def test_changed_payment_terms_stop_checkout(engine):
    job = await approved(engine, await quoted(engine))
    job['payment']['payoutAddress'] = 'SIM-other'
    engine.store.put('jobs', job['id'], job)
    await engine.simulate(job['id'], 'fund')
    await engine.tick()
    assert engine.get(job['id'])['phase'] == 'manual_review'
    assert engine.store.get('staged_fake', job['id'])['confirmations'] == 0


async def test_uncertain_payment_creation_never_repeats(engine):
    engine.escrow.create = AsyncMock(side_effect=TimeoutError())
    job = await approved(engine, await quoted(engine))
    assert job['phase'] == 'payment_creation_unknown'
    await engine.tick()
    same = await engine.start(StartRequest.model_validate(PAYLOAD))
    assert same['id'] == job['id']
    engine.escrow.create.assert_awaited_once()


async def test_http_quote_terms_and_funding_remain_authenticated(engine):
    app = create_app(engine, 'a'*30, background=False, frontend=False)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.post('/start_job',json=PAYLOAD)).status_code == 401
        client.headers['Authorization'] = 'Bearer ' + 'a'*30
        response = await client.post('/start_job',json=PAYLOAD)
        assert response.status_code == 202
        job_id = response.json()['id']
        await engine.tick()
        status = (await client.get('/status',params={'job_id':job_id})).json()
        assert status['status'] == 'awaiting_input'
        assert status['quote']['authorization_ceiling_cents'] == 900
        assert status['quote']['items'][0]['name'] == 'Simulated item'
        assert status['payment'] is None
        response = await client.post('/provide_input', json={'job_id':job_id,
            'input_schema_hash':status['input_schema_hash'], 'input_data':{'approved':True}})
        assert response.status_code == 200
        await engine.tick()
        status = (await client.get('/status',params={'job_id':job_id})).json()
        assert status['payment']['payoutAddress'] == 'SIM-payout'
        assert (await client.get('/availability')).json()['purchase_backend'] == 'staged_fake'


def test_external_backend_requires_explicit_flags(monkeypatch, tmp_path):
    monkeypatch.setenv('CARDANO_CARD_MODE','local')
    monkeypatch.setenv('PURCHASE_BACKEND','staged_module')
    monkeypatch.setenv('CARDANO_CARD_DB',str(tmp_path/'no.db'))
    with pytest.raises(ValueError, match='explicit execution'):
        configured_engine()
    assert not (tmp_path/'no.db').exists()


def test_staged_backend_does_not_load_legacy_module(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr('cardano_card.staged_purchase.importlib.import_module',lambda _: SimpleNamespace(purchase=lambda:None))
    with pytest.raises(ValueError, match='VERSION'):
        StagedModule('legacy')


async def test_rejected_payment_terms_never_leak_as_funding_instructions(engine):
    job = await quoted(engine)
    original = engine.escrow.create
    # Exercise real-node response comparison, not the fake terms injection.
    engine.escrow.simulated = False
    async def wrong_terms(job):
        payment = await original(job)
        payment.update(RequestedFunds=[{'unit':'','amount':'999'}],payoutAddress='SIM-payout')
        return payment
    engine.escrow.create = wrong_terms
    job = await approved(engine, job)
    assert job['phase'] == 'manual_review'
    app = create_app(engine, 'a'*30, background=False)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+'a'*30}) as client:
        response = await client.post('/start_job',json=PAYLOAD)
        assert response.status_code == 202
        assert 'blockchainIdentifier' not in response.json()
        assert (await client.get('/status',params={'job_id':job['id']})).json()['payment'] is None


async def test_real_wire_native_ada_price_matches_quote(engine):
    job = await quoted(engine)
    original = engine.escrow.create
    engine.escrow.simulated = False
    async def node_terms(job):
        payment = await original(job)
        payment.update(RequestedFunds=[{'unit':'','amount':'10000000'}],payoutAddress='SIM-payout')
        return payment
    engine.escrow.create = node_terms
    job = await approved(engine, job)
    assert job['phase'] == 'awaiting_payment'


async def test_changed_saved_cart_cannot_use_previous_approval(engine):
    job = await approved(engine, await quoted(engine))
    job['quote']['cart_hash'] = 'mutated-cart'
    engine.store.put('jobs',job['id'],job)
    await engine.simulate(job['id'],'fund')
    await engine.tick()
    assert engine.store.get('staged_fake',job['id'])['confirmations'] == 0
    assert engine.get(job['id'])['phase'] == 'reconciling'


async def test_later_no_purchase_claim_cannot_erase_partial_order_evidence(engine):
    engine.module.scenario = 'partial'
    job = await funded(engine, await quoted(engine))
    record = engine.store.get('staged_fake',job['id'])
    record['outcome'].update(status='failed_no_purchase',no_purchase=True,charge_status='none')
    engine.store.put('staged_fake',job['id'],record)
    await engine.tick()
    assert engine.get(job['id'])['phase'] == 'reconciling'
    assert engine.evidence(engine.get(job['id']))['checkout_evidence']['possible_purchase'] is True
    await engine.simulate(job['id'],'request_refund')
    await engine.tick()
    assert engine.get(job['id'])['phase'] == 'reconciling'
    assert engine.evidence(engine.get(job['id']))['checkout_evidence']['possible_purchase'] is True


def test_existing_staged_database_cannot_switch_recipient_or_purchase_mode(engine):
    with pytest.raises(ValueError,match='another provider'):
        StagedEngine(engine.store,engine.escrow,engine.module,escrow_lovelace=10_000_000,payout_address='SIM-other')
    other=FakeStagedModule(engine.store)
    other.simulated=False
    with pytest.raises(ValueError,match='another provider'):
        StagedEngine(engine.store,engine.escrow,other,escrow_lovelace=10_000_000,payout_address='SIM-payout')

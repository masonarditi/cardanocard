import copy
import json
import stat

import httpx
import pytest
from dotenv import dotenv_values

from cardano_card import preprod_setup as module
from cardano_card.preprod_setup import PreprodSetup, private_env, schema_fingerprint
from cardano_card.store import Store


@pytest.fixture
def setup(tmp_path, monkeypatch):
    schema = {"openapi": "3.0.0", "paths": {"/registry": {}}}
    monkeypatch.setattr(module, "SCHEMA_SHA256", schema_fingerprint(schema))
    state = {"schema": schema, "assets": [], "posts": [], "balances": 30_000_000,
             "permission": "Admin", "network_limit": ["Preprod"], "timeout": False, "key_timeout": False}
    source = {"network": "Preprod", "paymentType": "Web3CardanoV1", "smartContractAddress": "addr_test1contract", "feeRatePermille": 50, "FeeReceiverNetworkWallet": {"walletAddress": "addr_test1fee"}}
    for role, field in (("seller", "SellingWallets"), ("buyer", "PurchasingWallets")):
        source[field] = [{"id": role, "walletVkey": role + "vkey", "walletAddress": "addr_test1" + role,
                          "collectionAddress": "addr_test1" + role}]
    state["sources"] = [source]

    def node_handler(request):
        route = request.url.path
        if route == "/api-docs":
            return httpx.Response(200, json=state["schema"])
        if request.method == "POST":
            body = json.loads(request.content)
            state["posts"].append((route, body))
            if route == "/api/v1/registry/":
                if state["timeout"]:
                    raise httpx.ReadTimeout("sensitive internal data", request=request)
                asset = make_asset(body)
                state["assets"].append(asset)
                return ok(asset)
            if route == "/api/v1/api-key/":
                if state["key_timeout"]:
                    raise httpx.ReadTimeout("secret", request=request)
                return ok({"id": "key" + str(len(state["posts"])), "token": "private-key-" + str(len(state["posts"])),
                           "permission": "ReadAndPay", "networkLimit": ["Preprod"], "usageLimited": True, "status": "Active"})
        if route == "/api/v1/api-key-status/":
            return ok({"permission": "ReadAndPay" if request.headers["token"].startswith("private-") else state["permission"],
                       "networkLimit": state["network_limit"], "usageLimited": True, "status": "Active", "token": "do-not-output"})
        if route == "/api/v1/payment-source/":
            return ok({"PaymentSources": state["sources"]})
        if route == "/api/v1/registry/":
            assert request.url.params["network"] == "Preprod"
            assets = state["assets"]
            cursor = request.url.params.get("cursorId")
            start = next((i for i, a in enumerate(assets) if a["id"] == cursor), 0)
            return ok({"Assets": assets[start:start + 10]})
        raise AssertionError(str(request.url))

    def chain_handler(request):
        if state["balances"] == 0:
            return httpx.Response(404, json={"error": "Not Found"})
        return httpx.Response(200, json={"amount": [{"unit": "lovelace", "quantity": str(state["balances"])}]})

    store = Store(str(tmp_path / "setup.db"))
    node = httpx.AsyncClient(base_url="http://127.0.0.1:3001/api/v1/", headers={"token": "admin"}, transport=httpx.MockTransport(node_handler))
    chain = httpx.AsyncClient(base_url=module.BLOCKFROST_URL, transport=httpx.MockTransport(chain_handler))
    result = PreprodSetup(store, node, chain)
    yield result, state, tmp_path
    store.close()


def ok(data):
    return httpx.Response(200, json={"status": "success", "data": data})


def make_asset(payload, identifier=None):
    return {**copy.deepcopy(payload), "id": "registration", "state": "RegistrationConfirmed" if identifier else "RegistrationRequested",
            "SmartContractWallet": {"walletVkey": payload["sellingWalletVkey"], "walletAddress": "addr_test1seller"},
            "agentIdentifier": identifier, "CurrentTransaction": {"txHash": "a" * 64, "status": "Confirmed"}}


async def test_inspect_is_read_only_checks_schema_network_and_public_balances(setup):
    service, state, _ = setup
    result = await service.inspect()
    assert result["schema_verified"] is True and result["settlement_verified"] is False
    assert result["wallets"]["buyer"]["balance_lovelace"] == 30_000_000
    assert result["funded"] is True
    assert result["settlement_policy"] == {"fee_permille": 50, "fee_address": "addr_test1fee", "smart_contract_address": "addr_test1contract"}
    assert "do-not-output" not in json.dumps(result)
    assert not state["posts"]


async def test_schema_drift_stops_before_operations(setup):
    service, state, _ = setup
    state["schema"] = {"different": "version"}
    with pytest.raises(ValueError, match="OpenAPI differs"):
        await service.register(execute=True)
    assert not state["posts"]


@pytest.mark.parametrize("change", ["mainnet", "wallet", "type", "permission", "two_buyers"])
async def test_invalid_runtime_fails_closed(setup, change):
    service, state, _ = setup
    source = state["sources"][0]
    if change == "mainnet":
        source["network"] = "Mainnet"
    elif change == "wallet":
        source["SellingWallets"][0]["walletAddress"] = "addr1mainnet"
    elif change == "type":
        source["paymentType"] = "Web3CardanoV2"
    elif change == "permission":
        state["permission"] = "Read"
    else:
        source["PurchasingWallets"].append(dict(source["PurchasingWallets"][0], walletVkey="another"))
    with pytest.raises(ValueError):
        await service.register(execute=True)
    assert not state["posts"]


async def test_no_write_without_execute_or_funding(setup):
    service, state, _ = setup
    assert (await service.register())["operation"] == "not_submitted"
    state["balances"] = 0
    report = await service.register(execute=True)
    assert report["operation"] == "blocked_funding"
    assert report["wallets"]["buyer"]["balance_lovelace"] == 0
    assert not state["posts"]


async def test_registration_reuses_identical_request_and_readonly_reconciles(setup):
    service, state, _ = setup
    assert (await service.register(execute=True))["operation"] == "accepted_by_node"
    state["assets"][0].update(state="RegistrationConfirmed", agentIdentifier="registered-agent")
    report = await service.register()
    assert report["operation"] == "observed"
    assert report["registration"]["agent_identifier"] == "registered-agent"
    assert len(state["posts"]) == 1
    assert state["posts"][0][1]["network"] == "Preprod"
    with pytest.raises(ValueError, match="parameters changed"):
        await service.register(fee="20000000", execute=True)


async def test_timeout_never_retried_across_restart(setup):
    service, state, _ = setup
    state["timeout"] = True
    assert (await service.register(execute=True))["operation"] == "unknown"
    replacement = PreprodSetup(service.store, service.node, service.chain)
    assert (await replacement.register(execute=True))["operation"] == "unknown"
    assert len(state["posts"]) == 1


async def test_unknown_registration_can_be_reconciled_by_exact_metadata(setup):
    service, state, _ = setup
    state["timeout"] = True
    report = await service.register(execute=True)
    state["assets"].append(make_asset(service.registration_payload(report, "http://127.0.0.1:8081"), "agent"))
    assert (await service.register(execute=True))["registration"]["agent_identifier"] == "agent"
    assert len(state["posts"]) == 1


async def test_duplicate_matching_registration_stops(setup):
    service, state, _ = setup
    await service.register(execute=True)
    state["assets"].append(dict(state["assets"][0], id="duplicate"))
    with pytest.raises(ValueError, match="Multiple matching"):
        await service.register(execute=True)


async def test_registry_inclusive_cursor_pages_are_deduplicated(setup):
    service, state, _ = setup
    report = await service.inspect()
    asset = make_asset(service.registration_payload(report, "http://127.0.0.1:8081"))
    state["assets"] = [dict(asset, id=str(i)) for i in range(22)]
    assert len(await service.registry(report["contract_address"])) == 22


async def test_configure_waits_for_confirmed_registry_and_execute(setup):
    service, state, path = setup
    target = path / ".env.preprod"
    assert (await service.configure(target, execute=True))["configuration"] == "blocked_registration"
    assert not target.exists() and not state["posts"]
    await service.register(execute=True)
    state["assets"][0].update(state="RegistrationConfirmed", agentIdentifier="registered-agent")
    assert (await service.configure(target))["configuration"] == "not_written"
    assert not target.exists()


async def test_configure_stores_two_private_capped_keys_and_preserves_existing_values(setup):
    service, state, path = setup
    target = path / ".env.preprod"
    target.write_text("CARDANO_CARD_TOKEN='local-only'\n")
    await service.register(execute=True)
    state["assets"][0].update(state="RegistrationConfirmed", agentIdentifier="registered-agent")
    report = await service.configure(target, execute=True)
    assert report["configuration"] == "saved"
    values = dotenv_values(target)
    assert values["PAYMENT_API_KEY"] != values["BUYER_PAYMENT_API_KEY"]
    assert values["CARDANO_CARD_TOKEN"] == "local-only"
    assert values["MASUMI_CONTRACT_ADDRESS"] == "addr_test1contract"
    assert values["MASUMI_SCHEMA_VERIFIED"] == "true"
    assert values["PAYOUT_ADDRESS"] == "addr_test1seller"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert "private-key" not in json.dumps(report)
    assert all(post[1]["networkLimit"] == ["Preprod"] and post[1]["permission"] == "ReadAndPay" for post in state["posts"][1:])
    assert (await service.configure(target, execute=True))["configuration"] == "saved"
    assert len(state["posts"]) == 3
    # Neither secret is persisted in the setup database.
    assert "private-key" not in str(service.store.db.execute("SELECT data FROM records").fetchall())


async def test_unknown_key_creation_never_repeats(setup):
    service, state, path = setup
    await service.register(execute=True)
    state["assets"][0].update(state="RegistrationConfirmed", agentIdentifier="registered-agent")
    state["key_timeout"] = True
    target = path / ".env.preprod"
    for _ in range(2):
        assert (await service.configure(target, execute=True))["configuration"] == "unknown_key_request"
    assert len(state["posts"]) == 2
    assert not target.exists()


def test_private_env_rejects_symlink_and_newline(tmp_path):
    target = tmp_path / "private"
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        private_env(link, {"TOKEN": "secret"})
    with pytest.raises(ValueError, match="unsupported"):
        private_env(target, {"TOKEN": "injected\nvalue"})
    assert not target.exists()


@pytest.mark.parametrize("url", ["https://node.example/api/v1/", "http://localhost:3001/wrong/"])
def test_nonlocal_or_wrong_api_node_rejected(url):
    with pytest.raises(ValueError):
        PreprodSetup(None, httpx.AsyncClient(base_url=url))


async def test_builtin_admin_empty_network_list_is_allowed_only_for_admin(setup):
    service, state, _ = setup
    state["network_limit"] = []
    assert (await service.inspect())["schema_verified"] is True
    state["permission"] = "ReadAndPay"
    with pytest.raises(ValueError, match="active Preprod"):
        await service.inspect()


@pytest.mark.parametrize("value", [-1, 1001, True, "50"])
async def test_invalid_fee_policy_cannot_become_settlement_evidence(setup, value):
    service, state, _ = setup
    state["sources"][0]["feeRatePermille"] = value
    with pytest.raises(ValueError, match="settlement fee policy"):
        await service.inspect()


@pytest.mark.parametrize('collection', [None, 'addr_test1externalcollection'])
async def test_configure_payout_uses_collection_destination_or_seller_fallback(setup, collection):
    service,state,path=setup
    state['sources'][0]['SellingWallets'][0]['collectionAddress']=collection
    await service.register(execute=True)
    state['assets'][0].update(state='RegistrationConfirmed',agentIdentifier='registered-agent')
    target=path/'.env.preprod'
    assert (await service.configure(target,execute=True))['configuration']=='saved'
    values=dotenv_values(target)
    assert values['SELLER_ADDRESS']=='addr_test1seller'
    assert values['PAYOUT_ADDRESS']==(collection or 'addr_test1seller')


async def test_configure_rejects_existing_mismatched_payout_before_creating_keys(setup):
    service,state,path=setup
    await service.register(execute=True)
    state['assets'][0].update(state='RegistrationConfirmed',agentIdentifier='registered-agent')
    target=path/'.env.preprod'
    target.write_text('PAYOUT_ADDRESS=addr_test1different\n')
    with pytest.raises(ValueError,match='payout address'):
        await service.configure(target,execute=True)
    assert len(state['posts'])==1
    assert dotenv_values(target)['PAYOUT_ADDRESS']=='addr_test1different'


@pytest.mark.parametrize('collection', ['addr1mainnet', 'addr_test1with whitespace'])
async def test_invalid_seller_collection_destination_blocks_setup(setup, collection):
    service,state,_=setup
    state['sources'][0]['SellingWallets'][0]['collectionAddress']=collection
    with pytest.raises(ValueError,match='collection destination'):
        await service.inspect()
    assert not state['posts']

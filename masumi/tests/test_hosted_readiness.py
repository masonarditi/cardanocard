from copy import deepcopy

import httpx
import pytest

from cardano_card.hosted_readiness import assess, inspect


def fixture():
    rail = {"chain": "Cardano", "network": "Preprod", "paymentSourceType": "Web3CardanoV2",
            "address": "contract", "pricing": {"pricingType": "Dynamic"}}
    agent = {"id": "agent", "name": "demo", "userId": "user", "networkIdentifier": "Preprod",
             "apiUrl": "https://example.com", "payoutAddress": "addr_test1approved",
             "registrationState": "RegistrationConfirmed", "agentIdentifier": "asset",
             "pricing": {"pricingType": "Dynamic"}, "supportedPaymentSources": [deepcopy(rail)]}
    entry = {"id": "registry", "name": "demo", "apiBaseUrl": "https://example.com",
             "state": "RegistrationConfirmed", "agentIdentifier": "asset", "supportedPaymentSources": [rail]}
    source = {"network": "Preprod", "paymentSourceType": "Web3CardanoV2", "smartContractAddress": "contract"}
    return agent, [entry], [source]


def report(a, e, s):
    return assess(a, e, s, "user", "https://example.com", "addr_test1approved", {"accepting_jobs": False})


def test_dashboard_free_does_not_override_dynamic_registry():
    a, e, s = fixture()
    a["supportedPaymentSources"][0]["pricing"] = {"pricingType": "Free"}
    r = report(a, e, s)
    assert r["registration_ready"] is True
    assert r["registry_pricing"] == "Dynamic" and r["warnings"]
    assert r["execution_ready"] is False


@pytest.mark.parametrize("field,value", [("userId", "other"), ("networkIdentifier", "Mainnet"),
    ("apiUrl", "https://wrong.example"), ("payoutAddress", "addr_test1wrong"),
    ("agentIdentifier", "wrong"), ("registrationState", "RegistrationRequested")])
def test_identity_and_confirmation_must_match(field, value):
    a, e, s = fixture(); a[field] = value
    assert report(a, e, s)["registration_ready"] is False


def test_ambiguous_registry_and_wrong_rail_are_blocked():
    a, e, s = fixture()
    assert report(a, e + deepcopy(e), s)["registration_ready"] is False
    s[0]["paymentSourceType"] = "Web3CardanoV1"
    assert report(a, e, s)["registration_ready"] is False


def test_pending_is_not_diagnosed_as_funding_failure():
    a, e, s = fixture()
    a["registrationState"] = e[0]["state"] = "RegistrationRequested"
    a["agentIdentifier"] = e[0]["agentIdentifier"] = None
    r = report(a, e, s)
    assert not r["registration_ready"]
    assert all("fund" not in reason.lower() for reason in r["blockers"])


def test_read_only_inspection_stops_on_wrong_account():
    seen = []
    def handler(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"success": True, "data": {"userId": "other"}})
    with httpx.Client(base_url="https://app.masumi.network", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="different account"):
            inspect(client, "agent", "user", "https://example.com", "addr_test1approved", {})
    assert seen == [("GET", "/api/api-key-status")]

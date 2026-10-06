"""Read-only checks for SaaS registration; never enables jobs or retries writes."""

import argparse
import json
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values

HOST = "https://app.masumi.network"


def assess(agent, entries, sources, user_id, expected_url, payout_address, availability):
    blockers, warnings = [], []
    if agent.get("userId") != user_id:
        blockers.append("Hosted agent belongs to a different account")
    if agent.get("networkIdentifier") != "Preprod":
        blockers.append("Hosted agent is not on Preprod")
    if agent.get("apiUrl") != expected_url:
        blockers.append("Hosted API URL differs from the expected deployment")
    if agent.get("payoutAddress") != payout_address or not payout_address.startswith("addr_test1"):
        blockers.append("Preprod payout address differs from the approved destination")
    matches = [e for e in entries if e.get("apiBaseUrl") == expected_url and e.get("name") == agent.get("name")]
    entry = matches[0] if len(matches) == 1 else {}
    if len(matches) != 1:
        blockers.append("Registry record is missing or ambiguous")
    confirmed = (agent.get("registrationState") == "RegistrationConfirmed"
                 and entry.get("state") == "RegistrationConfirmed"
                 and bool(entry.get("agentIdentifier"))
                 and agent.get("agentIdentifier") == entry.get("agentIdentifier"))
    if not confirmed:
        blockers.append("On-chain registration is not confirmed consistently in both APIs")
    advertised = entry.get("supportedPaymentSources") or []
    rails = [r for r in advertised if r.get("chain") == "Cardano" and r.get("network") == "Preprod"
             and r.get("paymentSourceType") == "Web3CardanoV2"]
    rail = rails[0] if len(rails) == 1 else {}
    configured = [s for s in sources if s.get("network") == "Preprod"
                  and s.get("paymentSourceType") == "Web3CardanoV2"
                  and s.get("smartContractAddress") == rail.get("address")]
    if len(rails) != 1 or len(configured) != 1:
        blockers.append("Registry V2 payment source does not uniquely match configured Preprod escrow")
    canonical_pricing = rail.get("pricing", {}).get("pricingType")
    if canonical_pricing != "Dynamic" or agent.get("pricing", {}).get("pricingType") != "Dynamic":
        blockers.append("Requested Dynamic pricing is not present in both agent and registry")
    dashboard_rails = [r for r in agent.get("supportedPaymentSources", []) if r.get("address") == rail.get("address")]
    if len(dashboard_rails) != 1 or dashboard_rails[0].get("pricing") != rail.get("pricing"):
        warnings.append("Dashboard payment-source pricing differs from canonical registry pricing; do not use it to quote payments")
    return {
        "agent_id": agent.get("id"), "network": agent.get("networkIdentifier"),
        "account_matches": agent.get("userId") == user_id,
        "registration_state": agent.get("registrationState"), "registry_state": entry.get("state"),
        "registry_id": entry.get("id"), "agent_identifier": entry.get("agentIdentifier"),
        "registry_last_checked": entry.get("lastCheckedAt"),
        "transaction_present": bool(entry.get("CurrentTransaction")),
        "registry_pricing": canonical_pricing,
        "registration_ready": not blockers,
        # A valid registration does not prove execution, authentication, storage or settlement.
        "execution_ready": False,
        "execution_blockers": ["Hosted V2 execution adapter and payout/refund acceptance are not implemented/verified"],
        "public_accepting_jobs": availability.get("accepting_jobs"),
        "blockers": blockers, "warnings": warnings,
    }


def inspect(client, agent_id, user_id, expected_url, payout_address, availability):
    def data(path, params=None):
        r = client.get(path, params=params)
        r.raise_for_status()
        body = r.json()
        if body.get("success") is not True and body.get("status") != "success":
            raise ValueError("Unexpected hosted API response")
        return body["data"]
    identity = data("/api/api-key-status")
    if identity.get("userId") != user_id:
        raise ValueError("API key belongs to a different account")
    agent = data("/api/agents/" + agent_id)
    entries = data("/pay/api/v1/registry", {"network": "Preprod", "filterPaymentSourceType": "Web3CardanoV2",
                   "searchQuery": agent["name"], "limit": 100})["Assets"]
    sources = data("/pay/api/v1/payment-source", {"take": 100})["PaymentSources"]
    if len(entries) >= 100 or len(sources) >= 100:
        raise ValueError("Diagnostic result may be truncated; reconcile pagination before proceeding")
    return assess(agent, entries, sources, user_id, expected_url, payout_address, availability)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--env-file", default=".env.hosted")
    p.add_argument("--agent-id", required=True)
    p.add_argument("--user-id", required=True)
    p.add_argument("--url", required=True)
    p.add_argument("--payout-address", required=True)
    args = p.parse_args()
    url = urlsplit(args.url)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        p.error("Use an HTTPS deployment URL without credentials, query or fragment")
    if not all(c.isalnum() or c in "-_" for c in args.agent_id):
        p.error("Invalid agent ID")
    key = dotenv_values(args.env_file).get("PAYMENT_API_KEY")
    if not key:
        p.error("PAYMENT_API_KEY is missing from the private environment file")
    try:
        # Never send the SaaS key to the deployment URL or follow credential-bearing redirects.
        with httpx.Client(timeout=30, follow_redirects=False) as public:
            response = public.get(args.url.rstrip("/") + "/availability")
            response.raise_for_status()
            availability = response.json()
        with httpx.Client(base_url=HOST, headers={"x-api-key": key}, timeout=30, follow_redirects=False) as client:
            report = inspect(client, args.agent_id, args.user_id, args.url, args.payout_address, availability)
        print(json.dumps(report, indent=2))
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        # Do not echo raw server bodies, request objects or credentials.
        print(json.dumps({"registration_ready": False, "execution_ready": False,
                          "error": "Hosted readiness could not be verified; check connectivity, credentials and response schemas"}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()

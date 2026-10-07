"""Resumable local Preprod acceptance. Every payment requires --execute.

Payout uses a simulated purchase; sandbox-refund uses guarded AgentCard sandbox.
mason-sandbox-refund and mason-payout run Mason's agentcard/purchase.py (the PURCHASE_BACKEND=mason path):
sandbox must end in sandbox_mode -> refund; mason-payout spends the real card and needs --allow-real-card.
Node completion and independent settlement verification are separate outcomes.
"""
import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

from .agentcard_bridge import AgentCardPurchaser, ReplayTransport
from .chain_evidence import chain_client, chain_credential_ok, chain_provider
from .providers import ModulePurchaser
from .engine import Engine
from .event_output import event_line, safe
from .masumi_adapter import MasumiEscrow
from .models import StartRequest, digest
from .preprod_buyer import PreprodBuyer, loopback_url
from .sandbox_transport import SandboxTransport, private_json, readiness
from .store import Store

CASES = {"payout", "refund", "sandbox-refund", "mason-sandbox-refund", "mason-payout"}
REAL_PURCHASE = {"sandbox-refund", "mason-sandbox-refund", "mason-payout"}
PAYOUT_CASES = {"payout", "mason-payout"}
STOP_PHASES = {"paid", "refunded", "manual_review", "awaiting_input", "payment_creation_unknown"}
REQUIRED = ("PAYMENT_SERVICE_URL", "PAYMENT_API_KEY", "BUYER_PAYMENT_SERVICE_URL",
            "BUYER_PAYMENT_API_KEY", "AGENT_IDENTIFIER", "SELLER_VKEY", "BUYER_VKEY",
            "MASUMI_FEE_LOVELACE", "MASUMI_CONTRACT_ADDRESS", "BUYER_ADDRESS", "SELLER_ADDRESS", "PAYOUT_ADDRESS")


class FundingNeedsReconciliation(Exception):
    """No matching buyer record exists after an unconfirmed funding attempt."""


def assert_sandbox_reconciled(path):
    """Check the migrated state before creating another checkout on the shared card."""
    path = Path(path).resolve()
    if not path.exists():
        raise ValueError("Restore or explicitly reconcile the prior sandbox database before this run")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        for row in db.execute("SELECT data FROM records WHERE namespace='jobs'"):
            job = json.loads(row[0])
            if not job.get("simulated_purchase", True) and job.get("phase") not in {"paid", "refunded", "expired"}:
                raise ValueError("Prior sandbox checkout needs reconciliation; no new checkout is allowed")


class NodeRoutes:
    def __init__(self, client):
        self.client = client

    async def post(self, route, **kwargs):
        return await self.client.post(route.lstrip("/"), **kwargs)

    async def observe_buyer(self, job, buyer_vkey):
        payment = job["payment"]
        response = await self.post("purchase/resolve-blockchain-identifier", json={
            "network": "Preprod", "blockchainIdentifier": payment["blockchainIdentifier"], "includeHistory": "true"})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        envelope = response.json()
        if envelope.get("status") != "success":
            raise ValueError("Buyer observation unavailable")
        data = envelope["data"]
        if not data.get("SmartContractWallet"):
            return None  # Node 0.22 assigns the buyer wallet only when it locks funds; not observable yet.
        source = data.get("PaymentSource") or {}
        if (data.get("blockchainIdentifier") != payment["blockchainIdentifier"] or
                data.get("inputHash") != payment["inputHash"] or
                source.get("network") != "Preprod" or source.get("paymentType") != "Web3CardanoV1" or
                source.get("smartContractAddress") != payment["smartContractAddress"] or
                (data.get("SellerWallet") or {}).get("walletVkey") != payment["sellerVKey"] or
                (data.get("SmartContractWallet") or {}).get("walletVkey") != buyer_vkey or
                MasumiEscrow.funds(data.get("PaidFunds")) != payment["RequestedFunds"] or
                any(str(data.get(k)) != str(v) for k, v in payment["rawTimes"].items())):
            raise ValueError("Buyer observation identity or payment terms do not match")
        records = (data.get("TransactionHistory") or []) + [data.get("CurrentTransaction") or {}]
        hashes = sorted({r["txHash"].lower() for r in records if isinstance(r.get("txHash"), str)
                         and re.fullmatch(r"[0-9a-fA-F]{64}", r["txHash"])})
        return {"state": data.get("onChainState"), "tx_hashes": hashes,
                "node_action": (data.get("NextAction") or {}).get("requestedAction")}


class AcceptanceRunner:
    def __init__(self, engine, buyer, *, case, identity, observe_buyer):
        if case not in CASES or engine.escrow.simulated:
            raise ValueError("Acceptance requires a named case and real Preprod escrow")
        if engine.purchaser.simulated != (case not in REAL_PURCHASE):
            raise ValueError("Purchase mode does not match the acceptance case")
        self.engine, self.buyer, self.store = engine, buyer, engine.store
        self.case, self.identity, self.observe_buyer = case, identity, observe_buyer
        binding = {"case": case, "identity": identity}
        saved = self.store.get("acceptance_config", "binding")
        if saved and saved != binding:
            raise ValueError("Use the original case and provider configuration with this database")
        if not saved and self.store.jobs():
            raise ValueError("Acceptance must use a dedicated database, not an existing demo database")
        self.store.put("acceptance_config", "binding", binding)

    async def start(self, request):
        caller = request.identifier_from_purchaser
        if any(job["caller_id"] != caller for job in self.store.jobs()):
            raise ValueError("This acceptance database belongs to another request; reconcile that job first")
        fingerprint = digest({"case": self.case, "request": request.model_dump(mode="json"), "identity": self.identity})
        saved = self.store.get("acceptance_requests", caller)
        if saved and saved["fingerprint"] != fingerprint:
            raise ValueError("Acceptance request changed; reuse the original input and ID")
        if not saved:
            self.store.put("acceptance_requests", caller, {"fingerprint": fingerprint})
        job = await self.engine.start(request)
        if not job["payment"]:
            return job
        terms = {"id": job["id"], "simulated_escrow": False, **job["payment"]}
        # Never initiate a first funding attempt for an already progressing job.
        prior = self.buyer.store.get("buyer_writes", "fund:" + job["id"])
        if not prior and job["phase"] != "awaiting_payment":
            raise ValueError("Missing original buyer funding record; manual reconciliation required")
        await self.buyer.fund(request.model_dump(mode="json"), terms)
        return job

    async def step(self, job_id):
        job = self.engine.get(job_id)
        if not job.get("payment") or job["phase"] in STOP_PHASES:
            return job
        # Confirm buyer identity before the coordinator may invoke purchasing.
        observed = await self.observe_buyer(job)
        if observed is None:
            attempt = self.buyer.store.get("buyer_writes", "fund:" + job_id)
            if attempt and attempt["state"] == "unknown":
                raise FundingNeedsReconciliation()
            return job
        self.store.put("acceptance_buyer_observations", job_id, observed)
        transactions = {r["tx_hash"]: r for r in job.get("chain_transactions", [])}
        for tx_hash in observed["tx_hashes"]:
            transactions.setdefault(tx_hash, {"tx_hash": tx_hash, "network": "Preprod",
                "reported_by": "masumi-buyer-node", "verified_on_chain": False})
        job["chain_transactions"] = list(transactions.values())
        self.store.put("jobs", job_id, job)
        await self.engine.advance(job)
        job = self.engine.get(job_id)
        if job["phase"] == "refund_due":
            await self.buyer.refund(job_id, self.engine.public(job))
        return self.engine.get(job_id)

    def mason_reason(self, job):
        """Mason's saved failure reason (normalize drops it). Read-only; nothing is confirmed or sent."""
        outcome = job.get("outcome") or {}
        module = getattr(self.engine.purchaser, "module", None)
        if outcome.get("status") != "failed" or module is None:
            return None
        try:
            raw = module.inspect_purchase(job["id"])
        except Exception:
            return None
        return raw.get("reason") if isinstance(raw, dict) and raw.get("status") == "failed" else None

    def evidence(self, job, chain=None):
        result = self.engine.evidence(job)
        record = self.store.get("agentcard_purchases", job["id"]) or {}
        expected = "paid" if self.case in PAYOUT_CASES else "refunded"
        outcome = job.get("outcome") or {}
        scenario_matched = {
            "sandbox-refund": record.get("provider_reason") == "sandbox_mode",
            "mason-sandbox-refund": self.mason_reason(job) == "sandbox_mode",
            "mason-payout": outcome.get("status") == "success" and not str(outcome.get("order_id", "")).startswith("SIM-"),
        }.get(self.case, True)
        node_complete = job["phase"] == expected and scenario_matched
        proof = chain or {"settlement_verified": False, "limitations": ["Independent verification not yet run"]}
        expected_kind = "payout" if self.case in PAYOUT_CASES else "refund"
        verified = (proof.get("settlement_verified") is True and proof.get("settlement_kind") == expected_kind
                    and proof.get("funding_verified") is True
                    and (self.case not in PAYOUT_CASES or proof.get("result_verified") is True))
        result.update(case=self.case, node_complete=node_complete,
            acceptance_passed=node_complete and verified,
            independent_chain_evidence=proof,
            funding_attempt=self.buyer.store.get("buyer_writes", "fund:" + job["id"]),
            refund_attempt=self.buyer.store.get("buyer_writes", "refund:" + job["id"]),
            agentcard={"provider_reason": record.get("provider_reason"), "confirm_attempts": record.get("confirm_attempts", 0),
                       "budget": record.get("budget_evidence"), "currency_evidence": (record.get("cart") or {}).get("currency_evidence")})
        return result


def check_mason_case(args, environ):
    """Mason's module picks sandbox or production from AGENTCARD_ENV at import time; bind it to the case."""
    prod = environ.get("AGENTCARD_ENV") == "prod"
    if args.case == "mason-payout" and not (prod and args.allow_real_card):
        raise ValueError("mason-payout spends the real card: set AGENTCARD_ENV=prod and pass --allow-real-card")
    if args.case == "mason-sandbox-refund" and prod:
        raise ValueError("mason-sandbox-refund must not run with AGENTCARD_ENV=prod")
    if not args.exclusive_handoff:
        raise ValueError("Refresh tokens are single-use: confirm the exclusive AgentCard handoff (--exclusive-handoff)")
    directory = Path(args.agentcard_dir)
    suffix = ".prod" if prod else ""
    missing = [name for name in (".env" + suffix, ".agentcard_tokens" + suffix + ".json") if not (directory / name).exists()]
    if missing or not (directory / "purchase.py").exists():
        raise ValueError("Mason module, credentials or linked user tokens are missing: " + ", ".join(missing or ["purchase.py"]))


def configuration(args):
    # Explicit files prevent inherited production variables overriding this runner.
    settings = dict(dotenv_values(args.env))
    infra = dict(dotenv_values(args.node_env))
    missing = [name for name in REQUIRED if not settings.get(name)]
    provider, credential = chain_provider(infra)
    if not chain_credential_ok(provider, credential):
        missing.append("NOWNODES_API_KEY" if provider == "nownodes" else "BLOCKFROST_API_KEY_PREPROD")
    return settings, infra, missing


async def preflight(settings, infra, store):
    from .preprod_setup import PreprodSetup
    seller_url = loopback_url(settings["PAYMENT_SERVICE_URL"])
    if loopback_url(settings["BUYER_PAYMENT_SERVICE_URL"]) != seller_url:
        raise ValueError("This acceptance runner requires the same local Preprod node for buyer and seller")
    if settings.get("NETWORK") != "Preprod" or not 0 < int(settings["MASUMI_FEE_LOVELACE"]) <= 10_000_000:
        raise ValueError("Acceptance is limited to Preprod and at most 10 test ADA per job")
    async with httpx.AsyncClient(base_url=seller_url + "/", headers={"token": infra.get("ADMIN_KEY", "")}, timeout=30) as node, \
            chain_client(infra) as chain:
        if settings["PAYMENT_API_KEY"] == settings["BUYER_PAYMENT_API_KEY"]:
            raise ValueError("Buyer and seller must use separate capped Preprod keys")
        buyer_credits = None
        for key in ("PAYMENT_API_KEY", "BUYER_PAYMENT_API_KEY"):
            response = await node.get("api-key-status/", headers={"token": settings[key]})
            response.raise_for_status()
            envelope = response.json()
            auth = envelope.get("data") or {}
            if (envelope.get("status") != "success" or auth.get("permission") != "ReadAndPay" or
                    auth.get("networkLimit") != ["Preprod"] or auth.get("usageLimited") is not True or auth.get("status") != "Active"):
                raise ValueError("Runtime keys must be active, capped and restricted to Preprod")
            if key == "BUYER_PAYMENT_API_KEY":
                buyer_credits = auth.get("RemainingUsageCredits")
        snapshot = await PreprodSetup(store, node, chain, identity=seller_url).inspect(
            seller_vkey=settings["SELLER_VKEY"], buyer_vkey=settings["BUYER_VKEY"])
        snapshot["buyer_usage_credits"] = buyer_credits
    # The final schema for the setup snapshot is validated here, never guessed.
    return snapshot


async def run(args):
    settings, infra, missing = configuration(args)
    print("+----------------------------------------------------------+\n"
          "|  CARDANO CARD / PREPROD ACCEPTANCE                        |\n"
          "+----------------------------------------------------------+")
    print("PREPROD ACCEPTANCE | " + args.case + " | " + ("EXECUTE" if args.execute else "PREFLIGHT"))
    print("ESCROW | " + ("Real test ADA" if args.execute else "No provider calls") +
          " | PURCHASE | " + {"sandbox-refund": "AgentCard sandbox", "mason-sandbox-refund": "Mason module / AgentCard sandbox",
                               "mason-payout": "Mason module / REAL CARD"}.get(args.case, "Simulated"))
    if missing:
        print("BLOCKED | configure: " + ", ".join(missing))
        return 2
    if not args.execute:
        print("Configuration present. Run preprod_setup inspect for live readiness; no provider calls made.")
        return 0
    if not args.request or not args.request_id:
        raise ValueError("Supply a request JSON file and a stable 26-hex request ID; reuse both to resume")
    if args.case == "sandbox-refund":
        if not args.exclusive_handoff:
            raise ValueError("Sandbox requires the current exclusive AgentCard handoff")
        assert_sandbox_reconciled(args.prior_sandbox_db)
        if not all(readiness(Path(args.agentcard_dir))[k] for k in ("sandbox_credentials", "linked_user_tokens")):
            raise ValueError("Matching sandbox credentials and current user tokens are missing")
    if args.case.startswith("mason-"):
        check_mason_case(args, os.environ)
    request = StartRequest(identifier_from_purchaser=args.request_id, input_data=json.loads(Path(args.request).read_text()))
    store = Store(args.database or "data/acceptance-" + args.case + ".db")
    transport = None
    try:
        snapshot = await preflight(settings, infra, store)
        existing = next((j for j in store.jobs() if j["caller_id"] == request.identifier_from_purchaser), None)
        funded_attempt = existing and store.get("buyer_writes", "fund:" + existing["id"])
        validate_preflight(snapshot, settings, require_funding=not bool(funded_attempt))
        escrow = MasumiEscrow(settings["PAYMENT_SERVICE_URL"], settings["PAYMENT_API_KEY"], settings["AGENT_IDENTIFIER"], settings["SELLER_VKEY"],
                              payout_address=settings["PAYOUT_ADDRESS"])
        if args.case.startswith("mason-"):
            sys.path.insert(0, str(Path(args.agentcard_dir).resolve()))
            purchaser = ModulePurchaser("purchase")
        else:
            if args.case == "sandbox-refund":
                transport = SandboxTransport(Path(args.agentcard_dir), exclusive_until=time.time() + min(args.timeout, 1800))
                await transport.verify_sandbox()
            else:
                transport = ReplayTransport(store, "success" if args.case == "payout" else "declined")
            purchaser = AgentCardPurchaser(store, transport)
        engine = Engine(store, escrow, purchaser)
        store.event_sink = lambda event: print(event_line(event, ascii_only=args.ascii), flush=True)
        identity = digest({name: settings[name] for name in REQUIRED})
        async with httpx.AsyncClient(base_url=loopback_url(settings["BUYER_PAYMENT_SERVICE_URL"]) + "/",
                                     headers={"token": settings["BUYER_PAYMENT_API_KEY"]}, timeout=30) as client:
            node = NodeRoutes(client)
            buyer = PreprodBuyer(store, node, settings["AGENT_IDENTIFIER"], settings["SELLER_VKEY"], escrow.input_hash,
                                identity, expected_funds=[{"unit": "", "amount": settings["MASUMI_FEE_LOVELACE"]}])
            runner = AcceptanceRunner(engine, buyer, case=args.case, identity=identity,
                observe_buyer=lambda job: node.observe_buyer(job, settings["BUYER_VKEY"]))
            job = await runner.start(request)
            deadline, last_heartbeat = time.monotonic() + args.timeout, 0
            while job["phase"] not in STOP_PHASES and time.monotonic() < deadline:
                try:
                    job = await runner.step(job["id"])
                except FundingNeedsReconciliation:
                    attempt = store.get("buyer_writes", "fund:" + job["id"])
                    print("BLOCKED | funding unconfirmed; buyer record absent | HTTP " +
                          str(attempt.get("http_status", "unavailable")) +
                          " | reconcile this job before another payment", flush=True)
                    break
                except ValueError:
                    raise
                except Exception:
                    print("WAIT | provider unavailable; saved operations will not be repeated")
                if isinstance(transport, SandboxTransport) and transport.halted:
                    break
                if time.monotonic() - last_heartbeat >= 30:
                    print("WAIT | " + safe(job["phase"]) + " | Cardano deadlines and confirmations are real", flush=True)
                    last_heartbeat = time.monotonic()
                if job["phase"] not in STOP_PHASES:
                    await asyncio.sleep(args.poll_seconds)
            job = engine.get(job["id"])
            from .chain_evidence import BlockfrostEvidence  # late import: tests substitute the verifier
            provider, credential = chain_provider(infra)
            verifier = BlockfrostEvidence(credential, provider=provider)
            try:
                proof = await verifier.verify(job, buyer_vkey=settings["BUYER_VKEY"], seller_vkey=settings["SELLER_VKEY"],
                    buyer_address=settings["BUYER_ADDRESS"], seller_address=settings["SELLER_ADDRESS"],
                    payout_address=settings["PAYOUT_ADDRESS"], settlement_policy=snapshot["settlement_policy"])
            except Exception:
                proof = {"settlement_verified": False, "limitations": ["Independent evidence unavailable; retry inspection of this same job"]}
            finally:
                if hasattr(verifier, "close"):
                    await verifier.close()
            evidence = runner.evidence(job, proof)
            target = Path(args.evidence_dir) / (job["id"] + ".json")
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            private_json(target, evidence)
            print(("PASS" if evidence["acceptance_passed"] else "INCOMPLETE") + " | node_complete=" + str(evidence["node_complete"]) +
                  " | independently_verified=" + str(evidence["acceptance_passed"]))
            print("EVIDENCE | " + safe(target))
            return 0 if evidence["acceptance_passed"] else 2
    finally:
        if isinstance(transport, SandboxTransport):
            await transport.close()
        store.close()


def validate_preflight(snapshot, settings, *, require_funding=True):
    """Require current schema, wallet funding and matching confirmed registration."""
    if require_funding:
        credits = snapshot.get("buyer_usage_credits")
        if (not isinstance(credits, list) or
                sum(int(item["amount"]) for item in credits if item.get("unit") in {"", "lovelace"})
                < int(settings["MASUMI_FEE_LOVELACE"])):
            raise ValueError("Buyer API key has insufficient usage credits; no new job was created")
    if snapshot.get("schema_verified") is not True or snapshot.get("contract_address") != settings["MASUMI_CONTRACT_ADDRESS"]:
        raise ValueError("Live node schema or contract does not match configured Preprod")
    # Setup and runner use the same explicit fixed-fee registry entry.
    matches = [r for r in snapshot.get("registry", []) if r.get("agent_identifier") == settings["AGENT_IDENTIFIER"]]
    if len(matches) != 1 or matches[0].get("state") != "RegistrationConfirmed":
        raise ValueError("Agent registration is not confirmed")
    registration = matches[0]
    if (registration.get("seller_vkey") != settings["SELLER_VKEY"] or
            MasumiEscrow.funds((registration.get("fee") or {}).get("Pricing")) != [{"unit": "", "amount": settings["MASUMI_FEE_LOVELACE"]}]):
        raise ValueError("Registered seller or fixed fee does not match the acceptance configuration")
    seller = snapshot["wallets"]["seller"]
    observed_payout = seller.get("collectionAddress") or seller["walletAddress"]
    if settings.get("PAYOUT_ADDRESS", settings["SELLER_ADDRESS"]) != observed_payout:
        raise ValueError("Configured payout address differs from the observed seller collection destination")
    for role, key in (("buyer", "BUYER_VKEY"), ("seller", "SELLER_VKEY")):
        wallet = snapshot["wallets"][role]
        if wallet["walletVkey"] != settings[key] or wallet["walletAddress"] != settings[role.upper() + "_ADDRESS"]:
            raise ValueError("Wallet identity changed")
        balance = wallet["balance_lovelace"]
        minimum = int(settings["MASUMI_FEE_LOVELACE"]) + 5_000_000 if role == "buyer" else 5_000_000
        if require_funding and (balance is None or balance < minimum):
            raise ValueError("Fund both Preprod wallets before acceptance")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=sorted(CASES), required=True)
    parser.add_argument("--env", default=".env.preprod")
    parser.add_argument("--node-env", default="infra/masumi/.env")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--request")
    parser.add_argument("--request-id")
    parser.add_argument("--database")
    parser.add_argument("--agentcard-dir", default="../agentcard")
    parser.add_argument("--prior-sandbox-db", default="data/agentcard-sandbox.db")
    parser.add_argument("--exclusive-handoff", action="store_true")
    parser.add_argument("--allow-real-card", action="store_true", help="Required for mason-payout (real merchant order)")
    parser.add_argument("--evidence-dir", default="work/preprod-evidence")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--poll-seconds", type=float, default=5)
    parser.add_argument("--ascii", action="store_true")
    args = parser.parse_args()
    if not 0 < args.timeout <= 7200 or not 1 <= args.poll_seconds <= 60:
        parser.error("timeout must be 1–7200 seconds; poll interval 1–60 seconds")
    try:
        raise SystemExit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        raise SystemExit("Stopped. Resume with the same case, database, input and request ID.") from None
    except Exception:
        raise SystemExit("Acceptance blocked or interrupted. Inspect saved state and readiness; do not repeat an uncertain write with a new ID. Provider details suppressed.") from None


if __name__ == "__main__":
    main()

"""Resumable local Preprod setup. Inspection is the default; --execute permits writes.

Schema compatibility is a wire-format gate, never a claim of live settlement.
Credentials are written only to .env.preprod (0600), not the node encryption file.
Unknown registration/key requests are reconciled or stopped, never retried.
"""
import argparse
import asyncio
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import httpx
from dotenv import dotenv_values

from .models import digest
from .preprod_buyer import loopback_url
from .store import Store

# Canonical OpenAPI from Masumi payment-service 0.22.0, commit 26c7297821fd.
SCHEMA_SHA256 = "113b4e5f6e9240b391b7d61ae73326c4d93ea7d96111ee2c5673cf489f82ebe2"
MIN_WALLET_LOVELACE = 20_000_000
BLOCKFROST_URL = "https://cardano-preprod.blockfrost.io/api/v0/"


def schema_fingerprint(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def private_env(path, updates):
    """Preserve unrelated settings; refuse symlinks and atomically replace at 0600."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Configuration path must not be a symlink")
    values = dict(dotenv_values(path, interpolate=False)) if path.exists() else {}
    values.update(updates)
    if any(not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) for key in values):
        raise ValueError("Invalid configuration key")
    # Single-quoted dotenv values avoid interpolation; generated values cannot contain controls.
    if any(value is None or any(c in str(value) for c in "\r\n\x00'") for value in values.values()):
        raise ValueError("Configuration contains an unsupported value")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".preprod-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            os.fchmod(out.fileno(), 0o600)
            out.write("# Private Preprod runtime. Schema verification does not prove settlement.\n")
            out.writelines(f"{key}='{value}'\n" for key, value in values.items())
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class PreprodSetup:
    def __init__(self, store, node, chain=None, identity=None, *, name="Cardano Card Preprod", author="Cardano Card team"):
        self.store, self.node, self.chain = store, node, chain
        self.name, self.author = name, author
        self.identity = identity or str(node.base_url)
        loopback_url(str(node.base_url))
        if str(node.base_url).rstrip("/").split("?")[0].endswith("/api/v1") is False:
            raise ValueError("Expected the local /api/v1 node")
        if chain is not None and str(chain.base_url) != BLOCKFROST_URL:
            raise ValueError("Only the Preprod Blockfrost endpoint is allowed")
        self.lock = asyncio.Lock()

    async def _get(self, route, **kwargs):
        response = await self.node.get(route, **kwargs)
        response.raise_for_status()
        body = response.json()
        if body.get("status") != "success" or not isinstance(body.get("data"), dict):
            raise ValueError("Node read was not successful")
        return body["data"]

    async def registry(self, contract):
        assets, seen, cursor = [], set(), None
        for _ in range(100):
            params = {"network": "Preprod", "filterSmartContractAddress": contract}
            if cursor:
                params["cursorId"] = cursor
            page = (await self._get("registry/", params=params))["Assets"]
            if not isinstance(page, list):
                raise ValueError("Invalid registry page")
            for item in page:
                if item["id"] not in seen:
                    assets.append(item)
                    seen.add(item["id"])
            if len(page) < 10:
                return assets
            following = page[-1]["id"]
            if following == cursor:
                raise ValueError("Registry pagination did not advance")
            cursor = following
        raise ValueError("Registry pagination limit exceeded")

    async def inspect(self, seller_vkey=None, buyer_vkey=None):
        schema = await self.node.get(self.node.base_url.copy_with(path="/api-docs"))
        schema.raise_for_status()
        fingerprint = schema_fingerprint(schema.json())
        if fingerprint != SCHEMA_SHA256:
            raise ValueError("Node OpenAPI differs from the reviewed 0.22.0 schema")
        auth = await self._get("api-key-status/")
        if (auth.get("permission") not in {"Admin", "ReadAndPay"} or auth.get("status") != "Active"
                or (auth.get("permission") != "Admin" and "Preprod" not in auth.get("networkLimit", []))):
            raise ValueError("An active Preprod node credential is required")
        sources = (await self._get("payment-source/", params={"take": 100}))["PaymentSources"]
        if len(sources) >= 100:
            raise ValueError("Payment-source discovery reached its bound; cannot prove unique buyer selection")
        preprod_sources = [s for s in sources if s.get("network") == "Preprod" and s.get("paymentType") == "Web3CardanoV1"]
        # V1 purchase POST has no buyer-wallet selector. Never pretend a key selects it.
        if sum(len(s.get("PurchasingWallets", [])) for s in preprod_sources) != 1:
            raise ValueError("V1 requires exactly one Preprod purchasing wallet; node selection would be ambiguous")
        matches = []
        for source in sources:
            if source.get("network") != "Preprod" or source.get("paymentType") != "Web3CardanoV1":
                continue
            sellers = [w for w in source.get("SellingWallets", []) if not seller_vkey or w.get("walletVkey") == seller_vkey]
            buyers = [w for w in source.get("PurchasingWallets", []) if not buyer_vkey or w.get("walletVkey") == buyer_vkey]
            if len(sellers) == len(buyers) == 1:
                matches.append((source, sellers[0], buyers[0]))
        if len(matches) != 1:
            raise ValueError("Select exactly one Preprod source and one buyer/seller wallet")
        source, seller, buyer = matches[0]
        if not source["smartContractAddress"].startswith("addr_test1"):
            raise ValueError("Expected a testnet payment contract")
        fee_permille = source.get("feeRatePermille")
        fee_address = (source.get("FeeReceiverNetworkWallet") or {}).get("walletAddress")
        if type(fee_permille) is not int or not 0 <= fee_permille <= 1000 or not isinstance(fee_address, str) or not fee_address.startswith("addr_test1"):
            raise ValueError("Invalid Preprod settlement fee policy")
        settlement_policy = {"fee_permille": fee_permille, "fee_address": fee_address, "smart_contract_address": source["smartContractAddress"]}
        wallets = {}
        for role, wallet in (("seller", seller), ("buyer", buyer)):
            if not wallet["walletAddress"].startswith("addr_test1"):
                raise ValueError("Expected a testnet wallet")
            public = {key: wallet[key] for key in ("id", "walletVkey", "walletAddress", "collectionAddress")}
            recipient = public["collectionAddress"] or public["walletAddress"]
            if role == "seller" and (not isinstance(recipient, str) or not recipient.startswith("addr_test1") or any(c.isspace() for c in recipient)):
                raise ValueError("Expected a Preprod seller collection destination")
            public["balance_lovelace"] = None
            if self.chain:
                response = await self.chain.get("addresses/" + wallet["walletAddress"])
                if response.status_code == 404:
                    public["balance_lovelace"] = 0
                else:
                    response.raise_for_status()
                    public["balance_lovelace"] = sum(int(x["quantity"]) for x in response.json()["amount"] if x["unit"] == "lovelace")
            wallets[role] = public
        assets = await self.registry(source["smartContractAddress"])
        # Never expose raw node responses; they can contain API credentials/error strings.
        registry = [self._public_registration(a) for a in assets]
        return {"network": "Preprod", "schema_verified": True, "schema_sha256": fingerprint,
                "contract_address": source["smartContractAddress"], "wallets": wallets, "settlement_policy": settlement_policy,
                "funded": all(w["balance_lovelace"] is not None and w["balance_lovelace"] >= MIN_WALLET_LOVELACE for w in wallets.values()),
                "minimum_wallet_lovelace": MIN_WALLET_LOVELACE, "registry": registry,
                "settlement_verified": False}

    @staticmethod
    def _public_registration(asset):
        transaction = asset.get("CurrentTransaction") or {}
        tx = transaction.get("txHash")
        return {"id": asset["id"], "state": asset["state"], "agent_identifier": asset.get("agentIdentifier"),
                "name": asset["name"], "api_base_url": asset["apiBaseUrl"],
                "seller_vkey": asset["SmartContractWallet"]["walletVkey"],
                "fee": asset["AgentPricing"],
                "transaction_hash": tx if isinstance(tx, str) and re.fullmatch(r"[0-9a-fA-F]{64}", tx) else None}

    @staticmethod
    def registration_payload(report, api_url, fee="10000000", name="Cardano Card Preprod", author="Cardano Card team"):
        api_url = loopback_url(api_url)
        if not str(fee).isdigit() or not 0 < int(fee) <= 100_000_000:
            raise ValueError("Preprod service fee must be positive and at most 100 test ADA")
        return {"network": "Preprod", "sellingWalletVkey": report["wallets"]["seller"]["walletVkey"],
                "ExampleOutputs": [], "Tags": ["shopping", "agentcard"], "name": name,
                "apiBaseUrl": api_url, "description": "Sandbox purchasing agent; merchant spending is separate from the service fee.",
                "Capability": {"name": "cardano-card", "version": "0.1.0"},
                "AgentPricing": {"pricingType": "Fixed", "Pricing": [{"unit": "", "amount": str(int(fee))}]},
                "Author": {"name": author}}

    @staticmethod
    def _matches(asset, payload):
        return (asset.get("SmartContractWallet", {}).get("walletVkey") == payload["sellingWalletVkey"]
                and all(asset.get(key) == payload[key] for key in
                        ("name", "apiBaseUrl", "description", "Capability", "AgentPricing", "Tags", "ExampleOutputs"))
                and asset.get("Author", {}).get("name") == payload["Author"]["name"])

    async def register(self, api_url="http://127.0.0.1:8081", fee="10000000", *, execute=False, seller_vkey=None, buyer_vkey=None):
        async with self.lock:
            report = await self.inspect(seller_vkey, buyer_vkey)
            payload = self.registration_payload(report, api_url, fee, self.name, self.author)
            fingerprint = digest({"node": self.identity, "contract": report["contract_address"], "payload": payload})
            assets = await self.registry(report["contract_address"])
            matches = [a for a in assets if self._matches(a, payload)]
            if len(matches) > 1:
                raise ValueError("Multiple matching registrations require reconciliation")
            key = digest({"node": self.identity, "seller": payload["sellingWalletVkey"]})
            previous = self.store.get("setup_registration", key) if self.store else None
            if previous and previous["fingerprint"] != fingerprint:
                raise ValueError("Registration parameters changed; reconcile the saved request")
            if matches:
                record = {"fingerprint": fingerprint, "state": "observed", "registration": self._public_registration(matches[0])}
                if self.store and execute:
                    self.store.put("setup_registration", key, record)
                return {**report, "registration": record["registration"], "operation": "observed"}
            if previous:
                return {**report, "operation": previous["state"], "next": "Reconcile the saved registration; do not submit again"}
            if not execute:
                return {**report, "operation": "not_submitted", "next": "Fund both wallets, then register with --execute"}
            if not report["funded"]:
                return {**report, "operation": "blocked_funding", "next": "Fund both public Preprod addresses; no registration submitted"}
            if not self.store:
                raise ValueError("A durable setup store is required for writes")
            record = {"fingerprint": fingerprint, "state": "unknown"}
            self.store.put("setup_registration", key, record)
            try:
                response = await self.node.post("registry/", json=payload)
                response.raise_for_status()
                body = response.json()
                if body.get("status") == "success":
                    registration = self._public_registration(body["data"])
                    if not self._matches(body["data"], payload):
                        raise ValueError("Registration receipt differs from submitted terms")
                    record.update(state="accepted_by_node", registration=registration)
                    self.store.put("setup_registration", key, record)
            except Exception:
                pass
            return {**report, "operation": record["state"], "registration": record.get("registration"),
                    "next": "Inspect until RegistrationConfirmed; acceptance is not chain confirmation"}

    async def configure(self, env_path, *, execute=False, api_url="http://127.0.0.1:8081", fee="10000000", seller_vkey=None, buyer_vkey=None):
        """Issue separate capped Preprod keys, only after registration is confirmed."""
        report = await self.register(api_url, fee, execute=False, seller_vkey=seller_vkey, buyer_vkey=buyer_vkey)
        registration = report.get("registration") or {}
        if registration.get("state") != "RegistrationConfirmed" or not registration.get("agent_identifier"):
            return {**report, "configuration": "blocked_registration"}
        if not execute:
            return {**report, "configuration": "not_written"}
        if not self.store:
            raise ValueError("A durable setup store is required for writes")
        async with self.lock:
            existing = dict(dotenv_values(env_path, interpolate=False)) if Path(env_path).exists() else {}
            binding = digest({"node": self.identity, "contract": report["contract_address"], "seller": registration["seller_vkey"], "agent": registration["agent_identifier"]})
            seller = report["wallets"]["seller"]
            payout_address = seller["collectionAddress"] or seller["walletAddress"]
            if existing.get("PAYOUT_ADDRESS") and existing["PAYOUT_ADDRESS"] != payout_address:
                raise ValueError("Configured payout address differs from the observed seller collection destination")
            # Reserve each role independently. A crash after node acceptance cannot create a duplicate.
            for role, variable in (("seller", "PAYMENT_API_KEY"), ("buyer", "BUYER_PAYMENT_API_KEY")):
                key = binding + ":" + role
                previous = self.store.get("setup_keys", key)
                if existing.get(variable):
                    auth = await self._get("api-key-status/", headers={"token": existing[variable]})
                    if (auth.get("permission") != "ReadAndPay" or auth.get("networkLimit") != ["Preprod"]
                            or auth.get("usageLimited") is not True or auth.get("status") != "Active"):
                        raise ValueError("Existing runtime key is not an active capped Preprod payment key")
                    continue
                if previous:
                    return {**report, "configuration": "unknown_key_request", "next": "Recover the existing key through node administration; do not generate another"}
                self.store.put("setup_keys", key, {"state": "unknown"})
                try:
                    response = await self.node.post("api-key/", json={"usageLimited": "true", "UsageCredits": [{"unit": "", "amount": "100000000"}], "networkLimit": ["Preprod"], "permission": "ReadAndPay"})
                    response.raise_for_status()
                    body = response.json()
                    data = body.get("data") or {}
                    if (body.get("status") != "success" or data.get("permission") != "ReadAndPay" or data.get("networkLimit") != ["Preprod"] or data.get("usageLimited") is not True or data.get("status") != "Active" or not data.get("token")):
                        raise ValueError("Invalid restricted-key response")
                    private_env(env_path, {variable: data["token"]})
                    existing[variable] = data["token"]
                    self.store.put("setup_keys", key, {"state": "saved", "id": data["id"]})
                except Exception:
                    return {**report, "configuration": "unknown_key_request", "next": "Recover the existing key through node administration; do not generate another"}
            if existing["PAYMENT_API_KEY"] == existing["BUYER_PAYMENT_API_KEY"]:
                raise ValueError("Buyer and seller require separate payment keys")
            private_env(env_path, {"NETWORK": "Preprod", "MASUMI_SCHEMA_VERIFIED": "true", "MASUMI_SCHEMA_SHA256": SCHEMA_SHA256,
                                  "MASUMI_V1_COMPATIBLE": "true", "AGENT_IDENTIFIER": registration["agent_identifier"],
                                  "SELLER_VKEY": report["wallets"]["seller"]["walletVkey"], "BUYER_VKEY": report["wallets"]["buyer"]["walletVkey"],
                                  "SELLER_ADDRESS": report["wallets"]["seller"]["walletAddress"], "BUYER_ADDRESS": report["wallets"]["buyer"]["walletAddress"],
                                  "PAYOUT_ADDRESS": payout_address,
                                  "MASUMI_CONTRACT_ADDRESS": report["contract_address"], "MASUMI_FEE_LOVELACE": str(int(fee)),
                                  "MASUMI_FEE_PERMILLE": str(report["settlement_policy"]["fee_permille"]), "MASUMI_FEE_ADDRESS": report["settlement_policy"]["fee_address"],
                                  "PAYMENT_SERVICE_URL": str(self.node.base_url), "BUYER_PAYMENT_SERVICE_URL": str(self.node.base_url)})
            return {**report, "configuration": "saved", "next": "Run explicit Preprod acceptance; schema verification alone does not prove settlement"}


async def run(args):
    values = dotenv_values(args.node_env)
    key = values.get("ADMIN_KEY")
    if not key:
        raise ValueError("Local node admin configuration is missing")
    node_url = loopback_url(args.node_url) + "/"
    blockfrost = values.get("BLOCKFROST_API_KEY_PREPROD")
    if blockfrost and not blockfrost.startswith("preprod"):
        raise ValueError("A Preprod Blockfrost key is required")
    store = Store(args.database) if args.execute else None
    try:
        async with httpx.AsyncClient(base_url=node_url, headers={"token": key}, timeout=30) as node, \
                   httpx.AsyncClient(base_url=BLOCKFROST_URL, headers={"project_id": blockfrost or ""}, timeout=30) as chain:
            setup = PreprodSetup(store, node, chain if blockfrost else None, name=args.agent_name, author=args.author)
            options = {"seller_vkey": args.seller_vkey, "buyer_vkey": args.buyer_vkey}
            if args.action == "inspect":
                report = await setup.inspect(**options)
            elif args.action == "register":
                report = await setup.register(args.api_url, args.fee, execute=args.execute, **options)
            else:
                report = await setup.configure(args.output_env, execute=args.execute, api_url=args.api_url, fee=args.fee, **options)
            print(json.dumps(report, indent=2))
    finally:
        if store:
            store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=("inspect", "register", "configure"), default="inspect")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--node-env", default="infra/masumi/.env")
    parser.add_argument("--output-env", default=".env.preprod")
    parser.add_argument("--node-url", default="http://127.0.0.1:3001/api/v1/")
    parser.add_argument("--database", default="data/preprod-setup.db")
    parser.add_argument("--api-url", default="http://127.0.0.1:8081")
    parser.add_argument("--fee", default="10000000")
    parser.add_argument("--seller-vkey")
    parser.add_argument("--buyer-vkey")
    parser.add_argument("--agent-name", default="Cardano Card Preprod", help="Registered name; label whose node this is")
    parser.add_argument("--author", default="Cardano Card team")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except Exception:
        raise SystemExit("Preprod setup incomplete. Inspect local configuration and saved checkpoints; provider errors and secrets are withheld. Never repeat an uncertain write with a new database.") from None


if __name__ == "__main__":
    main()

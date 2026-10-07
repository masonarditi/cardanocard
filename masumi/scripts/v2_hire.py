"""Hire the hosted CardanoCard agent (Web3CardanoV2, Railway `/v3`) from our own V2 buyer node.

Stands in for Sokosumi's wallet: the seller side stays on Railway, only the escrow payment is signed here by the
0.29 node in infra/masumi-v2buyer (port 3002), which holds our funded Preprod purchasing wallet.

  .venv/bin/python scripts/v2_hire.py setup                 # one-time: V2 payment source + purchasing wallet on the node
  .venv/bin/python scripts/v2_hire.py hire --request work/v2-hire-request.json
  .venv/bin/python scripts/v2_hire.py status [--job ID]     # follow an existing hire

State for each hire is written to work/v2-hire/<job>.json so a re-run never pays twice.
"""
import argparse
import json
import os
import secrets
import sys
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
NODE = os.getenv("V2_BUYER_NODE_URL", "http://127.0.0.1:3002/api/v1")
AGENT = os.getenv("V2_AGENT_URL", "https://cardanocard-preprod-production.up.railway.app/v3")
STATE_DIR = ROOT / "work" / "v2-hire"

# Canonical Masumi Preprod V2 contract (payment-service 0.29 DEFAULTS); the SaaS CardanoCard agent lives there.
V2_CONTRACT = "addr_test1wzs4e6wc95hkwezlccjw9mdvq0r0rsgx6zk34avptga3ftgn37w4g"
V2_POLICY = "67ab0c92c4ac1610895a1c965ee50aba41a8f1513b15240723b3bd0b"
ADMIN_WALLETS = [
    "addr_test1qr7pdg0u7vy6a5p7cx9my9m0t63f4n48pwmez30t4laguawge7xugp6m5qgr6nnp6wazurtagjva8l9fc3a5a4scx0rq2ymhl3",
    "addr_test1qplhs9snd92fmr3tzw87uujvn7nqd4ss0fn8yz7mf3y2mf3a3806uqngr7hvksqvtkmetcjcluu6xeguagwyaxevdhmsuycl5a",
    "addr_test1qzy7a702snswullyjg06j04jsulldc6yw0m4r4w49jm44f30pgqg0ez34lrdj7dy7ndp2lgv8e35e6jzazun8gekdlsq99mm6w",
]
# The listed CardanoCard agent: V2 policy + its asset name suffix, selling wallet, fixed 20 tUSDM fee.
AGENT_SUFFIX = "36cbc1000000"
SELLER_VKEY = "37d35cc914cb8282183c3fdd2eb7dcc497e614ceeddf5f85f0b04432"
USDM = "16a55b2a349361ff88c03788f93e1e966e5d689605d044fef722ddde0014df10745553444d"
EXPECTED_FUNDS = [{"unit": USDM, "amount": "20000000"}]
TIMES = ("payByTime", "submitResultTime", "unlockTime", "externalDisputeUnlockTime")
TERMINAL = {"completed", "paid", "refunded", "expired", "failed", "manual_review"}


def node():
    key = dotenv_values(ROOT / "infra/masumi/.env")["ADMIN_KEY"]
    return httpx.Client(base_url=NODE, headers={"token": key}, timeout=120)


def ok(r):
    if r.status_code >= 400:
        sys.exit(f"{r.request.method} {r.request.url.path} -> {r.status_code}: {r.text[:600]}")
    body = r.json()
    return body.get("data", body)


def v2_source(c):
    data = ok(c.get("payment-source-extended/", params={"take": 25}))
    for s in data.get("ExtendedPaymentSources") or data.get("PaymentSources") or []:
        if s.get("network") == "Preprod" and s.get("paymentSourceType") == "Web3CardanoV2":
            return s
    return None


def cmd_setup(args):
    with node() as c:
        existing = v2_source(c)
        if existing:
            print("V2 source already present:", existing["id"], existing.get("smartContractAddress"), existing.get("contractSyncStatus"))
            return
        infra = dotenv_values(ROOT / "infra/masumi/.env")
        buyer = json.loads((ROOT / "work/.buyer-wallet-export.json").read_text())
        selling = Path(args.selling_mnemonic).read_text().strip()
        body = {
            "network": "Preprod", "paymentSourceType": "Web3CardanoV2",
            "PaymentSourceConfig": {"rpcProvider": "Blockfrost", "rpcProviderApiKey": infra["BLOCKFROST_API_KEY_PREPROD"]},
            "AdminWallets": [{"walletAddress": a} for a in ADMIN_WALLETS], "requiredAdminSignatures": 2,
            "PurchasingWallets": [{"walletMnemonic": buyer["mnemonic"], "collectionAddress": None, "note": "Cardano Card buyer (funded Preprod wallet)"}],
            "SellingWallets": [{"walletMnemonic": selling, "collectionAddress": None, "note": "unused (buyer-only node)"}],
        }
        created = ok(c.post("payment-source-extended/", json=body))
        print("created source", created.get("id"), "| contract", created.get("smartContractAddress"), "| policy", created.get("policyId"))
        if created.get("smartContractAddress") != V2_CONTRACT or created.get("policyId") != V2_POLICY:
            sys.exit("Derived contract/policy differ from the canonical V2 contract — do not use this source")
        wallets = [w.get("walletVkey") for w in created.get("PurchasingWallets") or []]
        if buyer["walletVkey"] not in wallets:
            sys.exit(f"Purchasing wallet mismatch: {wallets}")
        print("purchasing wallet vkey", buyer["walletVkey"][:12] + "… in sync:", created.get("contractSyncStatus", "?"))


def check_terms(resp, request):
    from masumi.helper_functions import create_masumi_input_hash
    ident = resp.get("agentIdentifier") or ""
    if not (ident.startswith(V2_POLICY) and ident.endswith(AGENT_SUFFIX)):
        sys.exit(f"Unexpected agentIdentifier {ident}")
    if resp.get("sellerVKey") != SELLER_VKEY or resp.get("smartContractAddress") != V2_CONTRACT:
        sys.exit(f"Unexpected seller/contract: {resp.get('sellerVKey')} {resp.get('smartContractAddress')}")
    if resp.get("paymentSourceType") != "Web3CardanoV2" or resp.get("simulated_escrow") is not False:
        sys.exit("Not a real V2 escrow")
    funds = sorted((f["unit"], str(f["amount"])) for f in resp.get("RequestedFunds") or [])
    if funds != sorted((f["unit"], f["amount"]) for f in EXPECTED_FUNDS):
        sys.exit(f"Fee differs from 20 tUSDM: {resp.get('RequestedFunds')}")
    expected = create_masumi_input_hash(request["input_data"], request["identifier_from_purchaser"])
    if resp.get("inputHash") != expected:
        sys.exit("inputHash does not match our exact inputs")
    raw = resp.get("rawTimes") or {}
    if set(raw) != set(TIMES) or any(not str(raw[k]).isdigit() or len(str(raw[k])) < 12 for k in TIMES):
        sys.exit(f"Need millisecond deadlines: {raw}")
    if not time.time() * 1000 + 60_000 < int(raw["payByTime"]) < int(raw["submitResultTime"]) <= int(raw["unlockTime"]) <= int(raw["externalDisputeUnlockTime"]):
        sys.exit("Deadlines expired or out of order")
    return expected


def purchase_payload(resp, request, input_hash):
    return {
        "identifierFromPurchaser": request["identifier_from_purchaser"], "network": "Preprod",
        "paymentSourceType": "Web3CardanoV2", "smartContractAddress": V2_CONTRACT,
        "supportedPaymentSourceIndex": int(resp.get("supportedPaymentSourceIndex", 0)),
        "blockchainIdentifier": resp["blockchainIdentifier"], "agentIdentifier": resp["agentIdentifier"],
        "sellerVkey": SELLER_VKEY, "inputHash": input_hash, "Amounts": EXPECTED_FUNDS,
        "sellerReturnAddress": resp.get("payoutAddress"),
        **{k: str(resp["rawTimes"][k]) for k in TIMES},
    }


def save(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f"{state['job_id']}.json").write_text(json.dumps(state, indent=2))


def cmd_hire(args):
    request = json.loads(Path(args.request).read_text())
    request.setdefault("identifier_from_purchaser", secrets.token_hex(13))
    open_jobs = [p for p in STATE_DIR.glob("*.json") if not json.loads(p.read_text()).get("done")] if STATE_DIR.exists() else []
    if open_jobs and not args.new:
        sys.exit(f"Unfinished hire(s) exist: {[p.stem for p in open_jobs]} — follow with `status`, or pass --new")
    with node() as c:
        src = v2_source(c)
        if not src or src.get("smartContractAddress") != V2_CONTRACT:
            sys.exit("Run `setup` first (no canonical V2 source on the buyer node)")
        r = httpx.post(f"{AGENT}/start_job", json=request, timeout=120)
        if r.status_code != 200:
            sys.exit(f"start_job -> {r.status_code}: {r.text[:600]}")
        resp = r.json()
        state = {"job_id": resp["id"], "request": request, "start": resp, "done": False}
        save(state)
        input_hash = check_terms(resp, request)
        payload = purchase_payload(resp, request, input_hash)
        print("job", resp["id"], "| agent", resp["agentIdentifier"][-18:], "| fee", resp["RequestedFunds"], "| payBy", resp["payByTime"])
        state["purchase_payload"] = payload
        save(state)
        p = ok(c.post("purchase/", json=payload))
        state["purchase"] = {"id": p.get("id"), "onChainState": p.get("onChainState"), "created": p.get("createdAt")}
        save(state)
        print("node purchase", p.get("id"), "| state", p.get("onChainState"), "| next action", (p.get("NextAction") or {}).get("requestedAction"))
    follow(state["job_id"], args.poll)


def node_purchase(c, identifier):
    data = ok(c.get("purchase/", params={"network": "Preprod", "limit": 25, "includeHistory": "true", "filterPaymentSourceType": "Web3CardanoV2"}))
    for p in data.get("Purchases") or data.get("PurchaseRequests") or []:
        if p.get("blockchainIdentifier") == identifier:
            return p
    return None


def follow(job_id, poll):
    path = STATE_DIR / f"{job_id}.json"
    state = json.loads(path.read_text())
    identifier = state["start"]["blockchainIdentifier"]
    last = None
    with node() as c:
        while True:
            s = httpx.get(f"{AGENT}/status", params={"job_id": job_id}, timeout=60).json()
            p = node_purchase(c, identifier) or {}
            txs = sorted({t.get("txHash") for t in (p.get("TransactionHistory") or []) if t.get("txHash")} | ({p["CurrentTransaction"]["txHash"]} if (p.get("CurrentTransaction") or {}).get("txHash") else set()))
            line = f"{time.strftime('%H:%M:%S')} agent {s.get('phase')}/{s.get('escrow_state')} | node {p.get('onChainState')} next={(p.get('NextAction') or {}).get('requestedAction')} err={(p.get('NextAction') or {}).get('errorNote')} | tx {[t[:12] for t in txs]}"
            if line[9:] != (last or "")[9:]:
                print(line)
                last = line
            state.update({"status": s, "node": {"onChainState": p.get("onChainState"), "NextAction": p.get("NextAction"), "txs": txs}})
            save(state)
            phase = s.get("phase")
            if phase == "refund_due" and (s.get("purchase") or {}).get("status") == "failed" and not state.get("refund_requested"):
                print("purchase failed with no charge -> requesting refund")
                ok(c.post("purchase/request-refund", json={"network": "Preprod", "blockchainIdentifier": identifier}))
                state["refund_requested"] = True
                save(state)
            if phase in TERMINAL and (p.get("onChainState") in (None, "Withdrawn", "RefundWithdrawn", "Expired") or phase in ("expired", "failed", "manual_review")):
                state["done"] = True
                save(state)
                print("final:", json.dumps({k: s.get(k) for k in ("id", "phase", "escrow_state", "purchase", "result")}, indent=1)[:1500])
                print("txs:", txs)
                return
            time.sleep(poll)


def cmd_status(args):
    job = args.job or max(STATE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime).stem
    follow(job, args.poll)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup"); s.add_argument("--selling-mnemonic", required=True, help="file with a throwaway 24-word mnemonic")
    h = sub.add_parser("hire"); h.add_argument("--request", required=True); h.add_argument("--new", action="store_true"); h.add_argument("--poll", type=int, default=30)
    st = sub.add_parser("status"); st.add_argument("--job"); st.add_argument("--poll", type=int, default=30)
    args = p.parse_args()
    {"setup": cmd_setup, "hire": cmd_hire, "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    main()

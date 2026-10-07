"""Point the Railway runtime at one of our registered agents / escrow rails, then redeploy.

  .venv/bin/python scripts/railway_rail.py --rail v2   # CardanoCard (tUSDM) on Masumi's hosted V2 service — the Sokosumi listing
  .venv/bin/python scripts/railway_rail.py --rail v1   # CardanoCard (tUSDM) on our own Railway V1 node — fallback / own-buyer tests

Values come from .env.hosted (V2 agent + SaaS key, V1 agent id), .env.preprod (seller key) and infra/masumi/.env
(node admin key); nothing is printed. Each rail keeps its own job database on the volume.
"""
import argparse
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
STAGING = Path("/private/tmp/cardanocard-hosted-runtime-20261007")
V2_AGENT_SAAS_ID = "f59af5fc-66e7-47ff-8c7d-569aba1251f1"
V2_SELLER = "37d35cc914cb8282183c3fdd2eb7dcc497e614ceeddf5f85f0b04432"
NODE_PUBLIC = "https://cardanocard-node-production.up.railway.app/api/v1"
PAYOUT = "addr_test1qpgq4gf9fmzg3gkdnp9jdtujtscr2qdms07tmtp8aqz5ykc7uvlelkeuqsgctx9n4en6xmkf4qhqkumzcc3qwt4845hqsxwadj"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rail", choices=("v1", "v2"), required=True)
    p.add_argument("--no-deploy", action="store_true")
    args = p.parse_args()
    hosted, preprod, infra = dotenv_values(ROOT / ".env.hosted"), dotenv_values(ROOT / ".env.preprod"), dotenv_values(ROOT / "infra/masumi/.env")
    if args.rail == "v2":
        import httpx
        with httpx.Client(base_url="https://app.masumi.network", headers={"x-api-key": hosted["PAYMENT_API_KEY"]}, timeout=60) as c:
            agent = c.get(f"/api/agents/{V2_AGENT_SAAS_ID}").json()["data"]["agentIdentifier"]
        vars_ = {"CARDANO_CARD_MODE": "hosted", "PAYMENT_SERVICE_URL": "https://app.masumi.network/pay/api/v1/",
                 "PAYMENT_API_KEY": hosted["PAYMENT_API_KEY"], "AGENT_IDENTIFIER": agent, "SELLER_VKEY": V2_SELLER,
                 "MASUMI_PAYMENT_SOURCE_TYPE": "Web3CardanoV2", "MASUMI_AUTH_HEADER": "x-api-key", "MASUMI_LOVELACE_PER_USD": "fixed",
                 "MASUMI_FEE_LOVELACE": "20000000", "MASUMI_PAYMENT_SOURCE_INDEX": "0", "CARDANO_CARD_DB": "/data/jobs-v2.db", "PAYOUT_ADDRESS": PAYOUT}
    else:
        vars_ = {"CARDANO_CARD_MODE": "preprod", "MASUMI_V1_COMPATIBLE": "true", "MASUMI_ALLOW_REMOTE_NODE": "true",
                 "PAYMENT_SERVICE_URL": NODE_PUBLIC, "PAYMENT_API_KEY": infra["ADMIN_KEY"], "AGENT_IDENTIFIER": hosted["V1_AGENT_IDENTIFIER"],
                 "SELLER_VKEY": preprod["SELLER_VKEY"], "CARDANO_CARD_DB": "/data/jobs.db", "PAYOUT_ADDRESS": PAYOUT}
    cmd = ["railway", "variables", "--skip-deploys", "--service", "cardanocard-preprod"]
    for k, v in vars_.items():
        cmd += ["--set", f"{k}={v}"]
    if subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT).returncode != 0:
        sys.exit("railway variables failed")
    print(f"rail {args.rail}: variables set (agent …{vars_['AGENT_IDENTIFIER'][-12:]}, seller {vars_['SELLER_VKEY'][:8]}…, db {vars_['CARDANO_CARD_DB']})")
    if not args.no_deploy:
        up = subprocess.run(["railway", "up", "--ci", "--service", "cardanocard-preprod", "--workspace", "eztramble's Projects"], capture_output=True, text=True, cwd=STAGING)
        print([l for l in (up.stdout + up.stderr).splitlines() if "Deploy complete" in l or "Deploy failed" in l] or f"railway up exit {up.returncode}")
    print("verify: POST /v3/start_job (v2) or /v4/start_job (v1) and check agentIdentifier / RequestedFunds")


if __name__ == "__main__":
    main()

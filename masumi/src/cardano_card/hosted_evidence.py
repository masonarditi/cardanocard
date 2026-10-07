"""Independently verify a hosted job's escrow settlement: pull the job from the Railway runtime (operator token),
then check its transactions through the configured Preprod chain provider (NOWNodes or Blockfrost).

  .venv/bin/python -m cardano_card.hosted_evidence --job JOB_ID [--url URL] [--out work/hosted-evidence/JOB.json]

Reads CARDANO_CARD_TOKEN and the hosted seller settings from .env.hosted, the chain-provider key from
infra/masumi/.env. Read-only: it never funds, confirms or submits anything.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values

from .chain_evidence import BlockfrostEvidence, chain_provider
from .sandbox_transport import private_json

DEFAULT_URL = "https://cardanocard-preprod-production.up.railway.app"


async def run(args):
    hosted = dotenv_values(args.env)
    infra = dotenv_values(args.node_env)
    token = hosted.get("CARDANO_CARD_TOKEN")
    if not token:
        raise SystemExit("CARDANO_CARD_TOKEN missing from " + args.env)
    async with httpx.AsyncClient(base_url=args.url.rstrip("/"), headers={"Authorization": "Bearer " + token}, timeout=30,
                                 follow_redirects=False) as client:
        response = await client.get(f"/operator/jobs/{args.job}")
        response.raise_for_status()
        job = response.json()
    payment = job.get("payment") or {}
    print(f"job {job['id']} | phase {job['phase']} | escrow {job.get('escrow_state')} | rail {payment.get('rail')} | "
          f"funds {payment.get('RequestedFunds')} | txs {len(job.get('chain_transactions', []))}")
    provider, credential = chain_provider(infra)
    verifier = BlockfrostEvidence(credential, provider=provider)
    try:
        proof = await verifier.verify(job, buyer_vkey=args.buyer_vkey or (payment.get("buyerVKey") or "0" * 56),
                                      seller_vkey=payment.get("sellerVKey"), payout_address=payment.get("payoutAddress"),
                                      settlement_policy=None)
    finally:
        if hasattr(verifier, "close"):
            await verifier.close()
    print(f"provider {proof['provider']} | funding_verified {proof['funding_verified']} | result_verified {proof['result_verified']} | "
          f"payout_observed {proof['payout_observed']} | refund_observed {proof['refund_observed']} | "
          f"settlement_verified {proof['settlement_verified']} ({proof.get('settlement_kind')})")
    for item in proof["transactions"]:
        print(f"  {item['tx_hash']}  {item['status']}  {item.get('settlement_kind') or ''}")
    if not proof["settlement_verified"]:
        print("note: V2 datum/redeemer layout is not strictly decoded yet; inclusion and value observations only.")
    out = Path(args.out or f"work/hosted-evidence/{job['id']}.json")
    out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_json(out, {"job": {k: v for k, v in job.items() if k != "input"}, "independent_chain_evidence": proof})
    print("EVIDENCE |", out)
    return 0 if proof["funding_verified"] else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--job", required=True)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--env", default=".env.hosted")
    parser.add_argument("--node-env", default="infra/masumi/.env")
    parser.add_argument("--buyer-vkey", help="Buyer payment key hash (56 hex) if known; otherwise funding is checked by datum identity only")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(run(args)))
    except httpx.HTTPStatusError as exc:
        raise SystemExit(f"Runtime returned HTTP {exc.response.status_code} for that job") from None


if __name__ == "__main__":
    main()

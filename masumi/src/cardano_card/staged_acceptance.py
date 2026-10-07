"""Resumable Preprod acceptance for the staged flow (added by Mason; reuses acceptance.py's checks).

quote -> buyer approves -> real escrow -> checkout -> payout or refund, in one process with a dedicated database.
Cases: payout/refund use the simulated staged module; sandbox runs agentcard/purchase_v2 against Agentcard sandbox
(expects a sandbox_mode refund); live runs purchase_v2 in production on the real card (needs --allow-real-card).
"""
import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

from .acceptance import NodeRoutes, configuration, preflight, validate_preflight
from .event_output import event_line, safe
from .masumi_adapter import MasumiEscrow
from .models import ProvideInput, StartRequest, digest
from .preprod_buyer import PreprodBuyer, loopback_url
from .sandbox_transport import private_json
from .staged_engine import StagedEngine
from .staged_purchase import FakeStagedModule, StagedModule
from .store import Store

CASES = {"payout": ("success", "paid"), "refund": ("declined", "refunded"),
         "sandbox": ("sandbox", "refunded"), "live": ("prod", "paid")}
STOP = {"paid", "refunded", "manual_review", "quote_rejected", "quote_expired", "payment_creation_unknown", "expired",
        "awaiting_input"}  # a vault approval needs a human; nothing here answers it
AGENTCARD = Path(__file__).resolve().parents[3] / "agentcard"


def check_agentcard_files(directory, prod):
    """Refresh tokens are single-use: the matching credentials and linked user tokens must be present here."""
    suffix = ".prod" if prod else ""
    missing = [n for n in (".env" + suffix, ".agentcard_tokens" + suffix + ".json") if not (Path(directory) / n).exists()]
    if missing:
        raise ValueError("Agentcard credentials or linked user tokens are missing: " + ", ".join(missing))


def purchase_module(case, store, *, exclusive_handoff=False, agentcard_dir=None):
    mode = CASES[case][0]
    if mode in ("success", "declined"):
        return FakeStagedModule(store, mode)
    if not exclusive_handoff:
        raise ValueError("Confirm the exclusive Agentcard handoff (--exclusive-handoff); only one machine may hold the tokens")
    check_agentcard_files(agentcard_dir or AGENTCARD, mode == "prod")
    os.environ["AGENTCARD_ENV"] = mode
    sys.path.insert(0, str(AGENTCARD))
    module = StagedModule("purchase_v2")
    if module.module.whoami()["env"] != mode:
        raise ValueError("Agentcard environment does not match the case")
    return module


class StagedRunner:
    def __init__(self, engine, buyer, observe_buyer, request):
        self.engine, self.buyer, self.observe_buyer = engine, buyer, observe_buyer
        self.request = request.model_dump(mode="json")

    async def step(self, job_id):
        job = self.engine.get(job_id)
        if job["phase"] == "awaiting_quote_approval":
            q = job["quote"]
            print(f"QUOTE | {safe(q['merchant'])} | {safe(', '.join(i['name'][:60] for i in q['items']))} | subtotal "
                  f"${q['subtotal_cents'] / 100:.2f} | ceiling ${q['authorization_ceiling_cents'] / 100:.2f} | approved",
                  flush=True)
            await self.engine.provide(ProvideInput(job_id=job_id, input_schema_hash=job["input_schema_hash"],
                                                   input_data={"approved": True}))
        if job.get("payment"):
            if job["phase"] == "awaiting_payment":
                await self.buyer.fund(self.request, {"id": job_id, "simulated_escrow": False, **job["payment"]})
            # Confirm the buyer's own payment record before the coordinator may check out.
            observed = await self.observe_buyer(job)
            if observed is None:
                return job
            transactions = {r["tx_hash"]: r for r in job.get("chain_transactions", [])}
            for tx_hash in observed["tx_hashes"]:
                transactions.setdefault(tx_hash, {"tx_hash": tx_hash, "network": "Preprod",
                                                  "reported_by": "masumi-buyer-node", "verified_on_chain": False})
            job["chain_transactions"] = list(transactions.values())
            self.engine.store.put("jobs", job_id, job)
        await self.engine.tick()
        job = self.engine.get(job_id)
        if job["phase"] == "refund_due":
            await self.buyer.refund(job_id, self.engine.public(job))
        return self.engine.get(job_id)


async def run(args):
    settings, infra, missing = configuration(args)
    print(f"STAGED PREPROD ACCEPTANCE | {args.case} | {'EXECUTE' if args.execute else 'PREFLIGHT'}")
    if missing:
        print("BLOCKED | configure: " + ", ".join(missing))
        return 2
    if not args.execute:
        print("Configuration present; add --execute with --request and --request-id to run.")
        return 0
    if args.case == "live" and not args.allow_real_card:
        raise ValueError("The live case spends on the real card; pass --allow-real-card")
    request = StartRequest(identifier_from_purchaser=args.request_id, input_data=json.loads(Path(args.request).read_text()))
    store = Store(args.database or f"data/staged-{args.case}.db")
    try:
        snapshot = await preflight(settings, infra, store)
        existing = next((j for j in store.jobs() if j["caller_id"] == request.identifier_from_purchaser), None)
        if any(j["caller_id"] != request.identifier_from_purchaser for j in store.jobs()):
            raise ValueError("This database belongs to another request")
        validate_preflight(snapshot, settings, require_funding=not (existing and existing.get("payment")))
        escrow = MasumiEscrow(settings["PAYMENT_SERVICE_URL"], settings["PAYMENT_API_KEY"], settings["AGENT_IDENTIFIER"],
                              settings["SELLER_VKEY"], payout_address=settings["PAYOUT_ADDRESS"])
        engine = StagedEngine(store, escrow, purchase_module(args.case, store, exclusive_handoff=args.exclusive_handoff),
                              escrow_lovelace=int(settings["MASUMI_FEE_LOVELACE"]), payout_address=settings["PAYOUT_ADDRESS"])
        store.event_sink = lambda event: print(event_line(event, ascii_only=args.ascii), flush=True)
        identity = digest({"case": args.case, "buyer": settings["BUYER_PAYMENT_SERVICE_URL"], "vkey": settings["BUYER_VKEY"]})
        async with httpx.AsyncClient(base_url=loopback_url(settings["BUYER_PAYMENT_SERVICE_URL"]) + "/",
                                     headers={"token": settings["BUYER_PAYMENT_API_KEY"]}, timeout=30) as client:
            node = NodeRoutes(client)
            buyer = PreprodBuyer(store, node, settings["AGENT_IDENTIFIER"], settings["SELLER_VKEY"], escrow.input_hash,
                                 identity, expected_funds=[{"unit": "", "amount": settings["MASUMI_FEE_LOVELACE"]}])
            runner = StagedRunner(engine, buyer, lambda job: node.observe_buyer(job, settings["BUYER_VKEY"]), request)
            job = await engine.start(request)
            print("JOB | " + job["id"], flush=True)
            deadline, heartbeat = time.monotonic() + args.timeout, 0
            while job["phase"] not in STOP and time.monotonic() < deadline:
                try:
                    job = await runner.step(job["id"])
                except ValueError:
                    raise
                except Exception:
                    print("WAIT | provider unavailable; saved operations will not be repeated", flush=True)
                if time.monotonic() - heartbeat >= 60:
                    print(f"WAIT | {safe(job['phase'])} | escrow {safe(job['escrow_state'])}", flush=True)
                    heartbeat = time.monotonic()
                if job["phase"] not in STOP:
                    await asyncio.sleep(args.poll_seconds)
            job = engine.get(job["id"])
            if job["phase"] == "awaiting_input":
                print("STOPPED | vault approval required; approve on the phone, then rerun the same command to inspect",
                      flush=True)
            from .chain_evidence import BlockfrostEvidence
            verifier = BlockfrostEvidence(infra["BLOCKFROST_API_KEY_PREPROD"])
            try:
                proof = await verifier.verify(job, buyer_vkey=settings["BUYER_VKEY"], seller_vkey=settings["SELLER_VKEY"],
                    buyer_address=settings["BUYER_ADDRESS"], seller_address=settings["SELLER_ADDRESS"],
                    payout_address=settings["PAYOUT_ADDRESS"], settlement_policy=snapshot["settlement_policy"])
            except Exception:
                proof = {"settlement_verified": False, "limitations": ["Independent evidence unavailable; rerun to inspect"]}
            finally:
                if hasattr(verifier, "close"):
                    await verifier.close()
            expected = CASES[args.case][1]
            kind = "payout" if expected == "paid" else "refund"
            verified = (proof.get("settlement_verified") is True and proof.get("settlement_kind") == kind
                        and proof.get("funding_verified") is True and (kind != "payout" or proof.get("result_verified") is True))
            evidence = {**engine.evidence(job), "case": args.case, "node_complete": job["phase"] == expected,
                        "acceptance_passed": job["phase"] == expected and verified, "independent_chain_evidence": proof,
                        "funding_attempt": store.get("buyer_writes", "fund:" + job["id"]),
                        "refund_attempt": store.get("buyer_writes", "refund:" + job["id"])}
            target = Path(args.evidence_dir) / (job["id"] + ".json")
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            private_json(target, evidence)
            print(f"{'PASS' if evidence['acceptance_passed'] else 'INCOMPLETE'} | phase={job['phase']} | "
                  f"independently_verified={verified} | EVIDENCE {safe(target)}", flush=True)
            return 0 if evidence["acceptance_passed"] else 2
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=sorted(CASES), required=True)
    parser.add_argument("--env", default=".env.preprod")
    parser.add_argument("--node-env", default="infra/masumi/.env")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-real-card", action="store_true")
    parser.add_argument("--exclusive-handoff", action="store_true", help="Required for sandbox/live: this machine alone holds the Agentcard tokens")
    parser.add_argument("--request")
    parser.add_argument("--request-id")
    parser.add_argument("--database")
    parser.add_argument("--evidence-dir", default="work/preprod-evidence")
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("--poll-seconds", type=float, default=10)
    parser.add_argument("--ascii", action="store_true")
    args = parser.parse_args()
    if not 0 < args.timeout <= 7200 or not 1 <= args.poll_seconds <= 60:
        parser.error("timeout must be 1-7200 seconds; poll interval 1-60 seconds")
    try:
        raise SystemExit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        raise SystemExit("Stopped. Resume with the same case, database, input and request ID.") from None


if __name__ == "__main__":
    main()

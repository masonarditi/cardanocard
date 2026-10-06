"""Run our agent against AgentCard sandbox with explicitly simulated escrow."""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from .agentcard_bridge import AgentCardPurchaser
from .engine import Engine
from .event_output import event_line, feed_banner, safe
from .models import StartRequest
from .providers import FakeEscrow
from .sandbox_transport import SandboxTransport, private_json, readiness
from .store import Store


async def run(args):
    directory = Path(args.agentcard_dir)
    ready = readiness(directory)
    print(feed_banner())
    print("ESCROW: SIMULATED | PURCHASE: AGENTCARD SANDBOX API")
    for key, value in ready.items():
        print(key + ": " + str(value))
    if not args.execute:
        print("PREFLIGHT ONLY | no authentication, refresh, checkout or payment performed")
        return 0 if ready["sandbox_credentials"] and ready["linked_user_tokens"] and not ready["refresh_needs_reconciliation"] else 2
    if not args.exclusive_handoff:
        raise ValueError("Confirm Mason has stopped using the token, then pass --exclusive-handoff")
    if not args.request or not args.request_id:
        raise ValueError("Supply --request JSON_FILE and a stable 26-hex --request-id; reuse that ID to resume")
    request = StartRequest(identifier_from_purchaser=args.request_id, input_data=json.loads(Path(args.request).read_text()))
    store = Store(args.db)
    transport = None
    try:
        if any(not job["simulated_escrow"] or job["simulated_purchase"] for job in store.jobs()):
            raise ValueError("Use a separate sandbox database; saved provider modes must match")
        transport = SandboxTransport(directory, exclusive_until=time.time() + args.timeout)
        await transport.verify_sandbox()
        print("AGENTCARD | verified test_mode=true; production checkout blocked")
        store.event_sink = lambda event: print(event_line(event), flush=True)
        engine = Engine(store, FakeEscrow(store), AgentCardPurchaser(store, transport))
        job = await engine.start(request)
        if job["phase"] == "awaiting_payment":
            await engine.simulate(job["id"], "fund")
        deadline = time.monotonic() + args.timeout
        last_message = None
        while time.monotonic() < deadline:
            await engine.advance(engine.get(job["id"]))
            job = engine.get(job["id"])
            record = store.get("agentcard_purchases", job["id"]) or {}
            outcome = job["outcome"] or {}
            message = outcome.get("message")
            if message and message != last_message:
                print("AGENTCARD | " + safe(message))
                last_message = message
            if job["phase"] == "refund_due":
                await engine.simulate(job["id"], "request_refund")
            if job["phase"] in {"refunded", "manual_review", "expired", "awaiting_input"} or transport.halted:
                break
            if record.get("stage") == "review" or (job["phase"] == "reconciling" and record.get("stage") != "confirm_requested"):
                break
            await asyncio.sleep(1)
        evidence = engine.evidence(job)
        record = store.get("agentcard_purchases", job["id"]) or {}
        evidence["agentcard"] = {"test_mode_verified": True, "stage": record.get("stage"),
            "provider_reason": record.get("provider_reason"), "confirm_attempts": record.get("confirm_attempts", 0),
            "outcome_reason": (job["outcome"] or {}).get("reason"), "budget": record.get("budget_evidence"),
            "currency_evidence": (record.get("cart") or {}).get("currency_evidence")}
        target = Path(args.evidence_dir) / (job["id"] + ".json")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_json(target, evidence)
        passed = job["phase"] == "refunded" and record.get("provider_reason") == "sandbox_mode"
        if job["phase"] == "refunded" and (job["outcome"] or {}).get("reason") == "over_budget":
            print("PASS BUDGET GUARD | cart rejected before confirmation; simulated service-fee refund completed")
            print("NOT EXERCISED | sandbox_mode confirmation branch; no checkout confirmation sent")
        else:
            print(("PASS" if passed else "INCOMPLETE") + " | AgentCard sandbox -> simulated service-fee refund")
        print("EVIDENCE | " + safe(target) + " | no Cardano transaction claimed")
        return 0 if passed else 2
    finally:
        if transport:
            await transport.close()
        store.close()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agentcard-dir", default="../agentcard")
    parser.add_argument("--db", default="data/agentcard-sandbox.db")
    parser.add_argument("--evidence-dir", default="work/sandbox-evidence")
    parser.add_argument("--request")
    parser.add_argument("--request-id")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--exclusive-handoff", action="store_true")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    if not 0 < args.timeout <= 1800:
        parser.error("timeout must be between 0 and 1800 seconds")
    try:
        raise SystemExit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        raise SystemExit("Stopped. Reuse the same request ID to inspect; never create another checkout for an uncertain attempt.") from None
    except ValueError as exc:
        # Only our own operational errors are shown; JSON/validation exceptions
        # can contain private request bodies and remain suppressed below.
        if type(exc) is ValueError:
            raise SystemExit(safe(exc)) from None
        raise SystemExit("Invalid sandbox configuration or request; no private values printed") from None
    except Exception:
        raise SystemExit("Sandbox run blocked or interrupted. Check preflight and saved job; no provider secrets printed. Do not retry with a new request ID.") from None


if __name__ == "__main__":
    main()

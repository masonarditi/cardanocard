"""Local-only buyer demonstration. Never funds a real wallet."""
import argparse
import json
import os
import secrets
import time
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--request-id", default=None, help="Reuse this value to demonstrate request deduplication")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--reject", action="store_true", help="Reject the simulated approval")
    args = parser.parse_args()
    if urlparse(args.url).hostname not in {"127.0.0.1", "localhost"}:
        parser.error("This buyer demo only connects to loopback")
    token = os.getenv("CARDANO_CARD_TOKEN")
    if not token:
        parser.error("Set CARDANO_CARD_TOKEN in .env")
    with httpx.Client(base_url=args.url, headers={"Authorization": "Bearer " + token}, timeout=10) as client:
        info = client.get("/availability")
        info.raise_for_status()
        if not info.json()["simulated_escrow"] or not info.json()["simulated_purchase"]:
            parser.error("This demo requires both fake providers")
        caller = args.request_id or secrets.token_hex(13)
        payload = {"identifier_from_purchaser": caller, "input_data": {
            "ask": "Buy the simulated sample item", "max_total_usd": "10.00",
            "street": "123 Example Street", "city": "Example City", "state": "CA",
            "zip": "00000", "phone": "+12025550123", "name": "Demo Buyer"}}
        started = client.post("/start_job", json=payload)
        started.raise_for_status()
        job_id = started.json()["id"]
        print(f"SIMULATION ONLY — request_id={caller} job_id={job_id}")
        acted = set()
        last_phase = None
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            response = client.get("/status", params={"job_id": job_id})
            response.raise_for_status()
            job = response.json()
            if job["phase"] != last_phase:
                print(f"{job['phase']} | escrow={job['escrow_state']}")
                last_phase = job["phase"]
            action = {"awaiting_payment": "fund", "refund_due": "request_refund", "result_submitted": "withdraw"}.get(job["phase"])
            if action and action not in acted:
                response = client.post(f"/local/jobs/{job_id}/{action}")
                response.raise_for_status()
                acted.add(action)
            if job["status"] == "awaiting_input":
                field = job["input_schema"]["input_data"][0]["id"]
                value = {"approved": not args.reject} if field == "approved" else {"answer": "Use the sample option"}
                response = client.post("/provide_input", json={"job_id": job_id,
                    "input_schema_hash": job["input_schema_hash"], "input_data": value})
                response.raise_for_status()
            if job["phase"] in {"paid", "refunded", "manual_review", "expired", "payment_creation_unknown"}:
                print(json.dumps({k: job[k] for k in ("phase", "escrow_state", "purchase", "simulated_escrow", "simulated_purchase")}, indent=2))
                return
            time.sleep(0.3)
        raise SystemExit("Still pending; saved job can be inspected/resumed. No second purchase was started.")


if __name__ == "__main__":
    main()

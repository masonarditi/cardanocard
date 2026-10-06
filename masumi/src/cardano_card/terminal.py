"""Terminal visibility: read-only status/watch and explicitly simulated demos."""
import argparse
import json
import os
import re
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

from .preprod_buyer import loopback_url
from .event_output import color_enabled, event_line, feed_banner, safe

TERMINAL = {"paid", "refunded", "manual_review", "expired", "payment_creation_unknown", "quote_rejected", "quote_expired"}


def request(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code == 401:
        raise ValueError("Local caller token rejected; check CARDANO_CARD_TOKEN")
    if response.status_code == 404:
        raise ValueError("Job or route not found; check the job ID and restart the updated API")
    if response.status_code >= 400:
        raise ValueError(f"API request rejected (HTTP {response.status_code}); no automatic write retry")
    return response.json()


def modes(info):
    return ("SIMULATED" if info["simulated_escrow"] else "PREPROD", "SIMULATED" if info["simulated_purchase"] else "EXTERNAL")


def _amount(value, scale):
    """Format integer minor units without floating-point rounding."""
    text = str(value)
    if not re.fullmatch(r"[0-9]+", text) or len(text) > 30:
        return "unknown"
    value = int(text)
    return f"{value // scale}.{value % scale:0{len(str(scale)) - 1}d}"


def funding_lines(job):
    """Only display approved public quote/funding fields, never purchase payloads."""
    lines = []
    quote = job.get("quote") or {}
    if quote:
        ceiling = _amount(quote.get("authorization_ceiling_cents"), 100)
        lines.append(f"QUOTE {safe(quote.get('quote_id', 'unknown'))} | ceiling={ceiling} {safe(quote.get('currency', 'unknown'))} | expires_at={safe(quote.get('expires_at', 'unknown'))}")
        for item in quote.get("items", []):
            lines.append(f"  ITEM {safe(item.get('quantity', '?'))} x {safe(item.get('name', 'unknown'))}")
        if "subtotal_cents" in quote and "estimated_total_cents" in quote:
            lines.append(f"  subtotal={_amount(quote['subtotal_cents'], 100)} USD | estimated_total={_amount(quote['estimated_total_cents'], 100)} USD")
        if job.get("input_schema_hash"):
            lines.append(f"QUOTE APPROVAL HASH {safe(job['input_schema_hash'])}")
        for fund in quote.get("requested_funds", []):
            if fund.get("unit") in {"lovelace", ""}:
                lines.append(f"SERVICE FEE {_amount(fund.get('amount'), 1_000_000)} test ADA | merchant budget is separate")
        if quote.get("payout_address"):
            lines.append(f"PAYOUT RECIPIENT {safe(quote['payout_address'])}")
    settlement = job.get("settlement") or {}
    if settlement:
        status = settlement.get("status", "unverified")
        verified = settlement.get("verified_on_chain") is True
        label = ("SIMULATED" if status == "simulated" else
                 "INDEPENDENTLY VERIFIED" if verified else
                 "NODE-REPORTED; NOT INDEPENDENTLY VERIFIED" if status == "node_reported" else
                 safe(status).upper().replace("_", " "))
        lines.append(f"SETTLEMENT {label}")
        if settlement.get("payout_address") and settlement.get("payout_address") != quote.get("payout_address"):
            lines.append(f"PAYOUT RECIPIENT {safe(settlement['payout_address'])}")
        if settlement.get("unlock_time") is not None:
            lines.append(f"UNLOCK at={safe(settlement['unlock_time'])} | seconds_remaining={safe(settlement.get('seconds_until_unlock', 'unknown'))} | release still requires confirmation")
    return lines


def approve_quote(client, job_id, quote_hash, emit=print):
    """Approve exactly the displayed revision once; a lost response is never retried."""
    if not isinstance(quote_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", quote_hash):
        raise ValueError("Pass the exact 64-character input_schema_hash from current job status")
    job = request(client, "GET", "/status", params={"job_id": job_id})
    if job.get("id") != job_id:
        raise ValueError("Returned job identity does not match")
    if job.get("phase") != "awaiting_quote_approval" or not job.get("quote"):
        raise ValueError("Job is not awaiting quote approval")
    if job.get("input_schema_hash") != quote_hash:
        raise ValueError("Quote changed; inspect the current quote and use its exact input_schema_hash")
    for line in funding_lines(job):
        emit(line)
    result = request(client, "POST", "/provide_input", json={"job_id": job_id,
        "input_schema_hash": quote_hash, "input_data": {"approved": True}})
    emit("QUOTE APPROVAL ACCEPTED | follow this same job with watch; no automatic retry")
    return result


def print_status(client, emit=print):
    info = request(client, "GET", "/availability")
    escrow, purchase = modes(info)
    emit(f"CARDANO CARD | escrow={escrow} | purchase={purchase} | backend={safe(info.get('purchase_backend', 'unknown'))}")
    jobs = request(client, "GET", "/jobs")["jobs"]
    if not jobs:
        emit("No jobs yet. Run: python -m cardano_card.terminal demo --scenario success")
    for job in jobs:
        emit(f"{safe(job['id'])}  {safe(job['phase']):22}  escrow={safe(job['escrow_state'])}")
    return jobs


def live(client, *, after=None, timeout=None, interval=0.5, emit=print,
         sleep=time.sleep, clock=time.monotonic, color=None, ascii_only=False):
    """Follow every job using a durable sequence; this command never writes."""
    cursor, disconnected = after, False
    deadline = clock() + timeout if timeout is not None else float("inf")
    emit(feed_banner())
    emit("Showing the latest 20 steps, then following new events." if after is None else f"Resuming after event #{after}.")
    while clock() < deadline:
        params = {"limit": 20 if cursor is None else 100}
        if cursor is not None:
            params["after"] = cursor
        try:
            response = client.get("/events", params=params)
            if response.status_code == 409:
                raise ValueError("Event cursor no longer matches this database; restart live without --after")
            if response.status_code == 401:
                raise ValueError("Local caller token rejected; check CARDANO_CARD_TOKEN")
            if response.status_code == 404:
                raise ValueError("Live feed unavailable; restart the updated API")
            response.raise_for_status()
            batch = response.json()
        except (httpx.TransportError, httpx.HTTPStatusError):
            if not disconnected:
                emit("DISCONNECTED | retrying read-only feed; saved jobs continue on the server")
            disconnected = True
            sleep(interval)
            continue
        if disconnected:
            emit("RECONNECTED | resuming saved events")
            disconnected = False
        for event in batch["events"]:
            if cursor is None or event["sequence"] > cursor:
                emit(event_line(event, color=color, ascii_only=ascii_only))
                cursor = event["sequence"]
        cursor = batch["cursor"]
        if cursor >= batch["latest"]:
            sleep(interval)
    emit(f"Feed stopped | resume with: live --after {cursor or 0}")
    return cursor


def watch(client, job_id, *, timeout=120, interval=1, simulate=False, reject=False,
          emit=print, sleep=time.sleep, clock=time.monotonic, evidence_path=None):
    seen, acted, answered = set(), set(), set()
    deadline = clock() + timeout
    last, last_details = None, None
    emit(f"JOB {safe(job_id)} | {'SIMULATION DRIVER' if simulate else 'READ-ONLY WATCH'}")
    while clock() < deadline:
        job = request(client, "GET", "/status", params={"job_id": job_id})
        if job["id"] != job_id:
            raise ValueError("Returned job identity does not match")
        if simulate and not (job["simulated_escrow"] and job["simulated_purchase"]):
            raise ValueError("Simulation driver refuses non-simulated providers")
        escrow, purchase = modes(job)
        state = (job["phase"], job["escrow_state"])
        if state != last:
            emit(f"STATE {safe(job['phase'])} | escrow={safe(job['escrow_state'])} | modes={escrow}/{purchase}")
            last = state
        details = funding_lines(job)
        # Show countdown progress at most every 30 seconds instead of flooding the feed.
        settlement = job.get("settlement") or {}
        remaining = settlement.get("seconds_until_unlock")
        detail_key = (json.dumps(job.get("quote"), sort_keys=True), job.get("input_schema_hash"),
                      settlement.get("status"), settlement.get("verified_on_chain"),
                      settlement.get("payout_address"), settlement.get("unlock_time"),
                      int(remaining) // 30 if isinstance(remaining, (int, float)) else None)
        if detail_key != last_details:
            for line in details:
                emit(line)
            last_details = detail_key
        for index, event in enumerate(job.get("events", [])):
            key = (index, event["at"], event["phase"], event["message"])
            if key not in seen:
                stamp = datetime.fromtimestamp(event["at"], ZoneInfo("Asia/Singapore")).strftime("%H:%M:%S SGT")
                emit(f"  {stamp}  {safe(event['phase']):22} {safe(event['message'])}")
                seen.add(key)
        if simulate:
            action = {"awaiting_payment": "fund", "refund_due": "request_refund", "result_submitted": "withdraw"}.get(job["phase"])
            if action and action not in acted:
                # Record locally before I/O. This invocation never repeats an uncertain write.
                acted.add(action)
                request(client, "POST", f"/local/jobs/{job_id}/{action}")
                emit(f"ACTION simulated {action}")
            revision = job.get("input_schema_hash")
            if (job["status"] == "awaiting_input" or job["phase"] == "awaiting_quote_approval") and revision not in answered:
                field = "approved" if job["phase"] == "awaiting_quote_approval" else job["input_schema"]["input_data"][0]["id"]
                value = {"approved": not reject} if field == "approved" else {"answer": "Use the sample option"}
                answered.add(revision)
                request(client, "POST", "/provide_input", json={"job_id": job_id,
                    "input_schema_hash": revision, "input_data": value})
                emit("ACTION simulated quote approval" if job["phase"] == "awaiting_quote_approval" else "ACTION simulated input response")
        if job["phase"] in TERMINAL:
            evidence = request(client, "GET", "/evidence", params={"job_id": job_id})
            emit(f"RESULT {safe(job['phase'])} | order={safe(evidence.get('order_id') or 'none')} | total_usd={safe(evidence.get('total_usd') or 'none')}")
            transactions = evidence.get('chain_transactions', [])
            verified = sum(item.get('verified_on_chain') is True for item in transactions)
            emit(f"PROOF node_reported_transactions={len(transactions)} | independently_verified={verified} | escrow={escrow} | purchase={purchase}")
            if evidence_path:
                path = Path(evidence_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                # No accidental overwrite of a previous evidence bundle.
                with path.open("x") as output:
                    path.chmod(0o600)
                    json.dump(evidence, output, indent=2)
                emit("Evidence saved: " + safe(path))
            return job["phase"]
        sleep(interval)
    emit("PENDING — saved job remains available. Watch the same ID; do not start another purchase.")
    return "pending"


def demo(client, args, emit=print):
    info = request(client, "GET", "/availability")
    if not (info["simulated_escrow"] and info["simulated_purchase"]):
        raise ValueError("Demo requires simulated escrow and purchases; use watch for Preprod visibility")
    caller = args.request_id or secrets.token_hex(13)
    payload = {"identifier_from_purchaser": caller, "input_data": {
        "ask": "Buy the simulated sample item", "max_total_usd": "10.00",
        "street": "123 Example Street", "city": "Example City", "state": "CA",
        "zip": "00000", "phone": "+12025550123", "name": "Demo Buyer"}}
    emit(f"SIMULATION ONLY | scenario={safe(args.scenario)} | request_id={safe(caller)}")
    started = request(client, "POST", "/local/start_job", json={"request": payload, "scenario": args.scenario})
    return watch(client, started["id"], timeout=args.timeout, simulate=True, reject=args.reject,
                 emit=emit, evidence_path=args.evidence)


def main():
    load_dotenv()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("status", help="Read service mode and recent jobs; no side effects")
    following = subs.add_parser("live", help="Continuously stream saved steps across all jobs; read-only")
    following.add_argument("--after", type=int, help="Resume after this event number; 0 replays all history")
    following.add_argument("--timeout", type=float, help="Stop after this many seconds; default stays open")
    following.add_argument("--ascii", action="store_true", help="Use ASCII markers instead of emoji")
    following.add_argument("--color", choices=("auto", "always", "never"), default="auto", help="Color stages; auto respects NO_COLOR and terminal support")
    watching = subs.add_parser("watch", help="Read a job's progress; never fund or purchase")
    watching.add_argument("job_id")
    watching.add_argument("--timeout", type=float, default=300)
    watching.add_argument("--evidence")
    approving = subs.add_parser("approve-quote", help="Explicitly approve the exact current quote revision once")
    approving.add_argument("job_id")
    approving.add_argument("--quote-hash", required=True, help="Exact input_schema_hash displayed by watch")
    playing = subs.add_parser("demo", help="Run an explicitly simulated lifecycle")
    playing.add_argument("--scenario", default="success", choices=("success", "declined", "over_budget", "approval", "needs_input", "unknown", "timeout_recovered", "partial"))
    playing.add_argument("--request-id")
    playing.add_argument("--reject", action="store_true")
    playing.add_argument("--timeout", type=float, default=60)
    playing.add_argument("--evidence")
    args = parser.parse_args()
    try:
        url = loopback_url(args.url)
        token = os.environ["CARDANO_CARD_TOKEN"]
        timeout = getattr(args, "timeout", None)
        if not token or (timeout is not None and timeout <= 0) or (getattr(args, "after", None) is not None and args.after < 0):
            raise ValueError("Configure a caller token and positive timeout")
        with httpx.Client(base_url=url, headers={"Authorization": "Bearer " + token}, timeout=30) as client:
            if args.command == "live":
                live(client, after=args.after, timeout=args.timeout,
                     color=color_enabled(args.color), ascii_only=args.ascii)
                return
            if args.command == "status":
                print_status(client)
                return
            if args.command == "approve-quote":
                approve_quote(client, args.job_id, args.quote_hash)
                return
            result = demo(client, args) if args.command == "demo" else watch(client, args.job_id, timeout=args.timeout, evidence_path=args.evidence)
            if result not in {"paid", "refunded"}:
                raise SystemExit(2)
    except KeyboardInterrupt:
        raise SystemExit("Stopped watching. The saved job and server continue unchanged.") from None
    except (httpx.HTTPError, KeyError):
        raise SystemExit("Local API unavailable or configuration missing. Start the API and check .env; no provider details printed.") from None
    except ValueError as exc:
        raise SystemExit(safe(exc)) from None


if __name__ == "__main__":
    main()

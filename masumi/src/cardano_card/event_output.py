"""Shared, compact rendering for persisted lifecycle events."""
from datetime import datetime
import os
import sys
from zoneinfo import ZoneInfo


# Labels convey the state even when color or emoji is unavailable.
STAGES = {
    "creating_payment": ("📝", "+", "JOB CREATED", "36"),
    "awaiting_payment": ("⏳", "...", "WAITING FOR PAYMENT", "33"),
    "purchasing": ("🛒", ">>", "CHECKOUT STARTED", "36"),
    "processing": ("⚙️", "...", "PURCHASE PROCESSING", "36"),
    "reconciling": ("🔎", "?", "CHECKING PURCHASE OUTCOME", "33"),
    "awaiting_input": ("🙋", "?", "YOUR RESPONSE NEEDED", "33"),
    "resume_ready": ("▶️", ">>", "RESPONSE SAVED", "36"),
    "submitting_result": ("📤", ">>", "SUBMITTING ORDER RESULT", "36"),
    "result_submitted": ("⏳", "...", "RESULT SAVED / WAITING FOR PAYOUT", "33"),
    "paid": ("✅", "OK", "SERVICE FEE PAID", "32"),
    "refund_due": ("↩️", "<", "REFUND REQUEST NEEDED", "33"),
    "refund_authorizing": ("↩️", "<", "AUTHORIZING REFUND", "33"),
    "refunded": ("✅", "OK", "SERVICE FEE REFUNDED", "32"),
    "manual_review": ("🛑", "!", "OPERATOR REVIEW NEEDED", "31"),
    "payment_creation_unknown": ("⚠️", "!", "PAYMENT OUTCOME UNKNOWN", "31"),
    "expired": ("⌛", "X", "PAYMENT WINDOW EXPIRED", "33"),
}


def safe(value):
    return "".join(c if c.isprintable() else " " for c in str(value))[:500]


def color_enabled(mode="auto"):
    return mode == "always" or (mode == "auto" and "NO_COLOR" not in os.environ
                                and os.getenv("TERM") != "dumb" and sys.stdout.isatty())


def feed_banner():
    border = "+" + "-" * 58 + "+"
    rows = ["CARDANO CARD  /  LIVE FEED", "All jobs  |  Read-only viewer  |  Ctrl-C to stop"]
    return "\n".join([border, *(f"|  {row:<54}  |" for row in rows), border])


def event_line(event, *, color=None, ascii_only=False):
    if color is None:
        color = color_enabled()
    def paint(text, code):
        return f"\033[{code}m{text}\033[0m" if color else text
    def clean(value):
        value = safe(value)
        return value.encode("ascii", "replace").decode() if ascii_only else value
    stamp = datetime.fromtimestamp(event["at"], ZoneInfo("Asia/Singapore")).strftime("%H:%M:%S SGT")
    if event["simulated_escrow"] and event["simulated_purchase"]:
        mode = "SIMULATED"
    else:
        escrow = "SIMULATED" if event["simulated_escrow"] else "PREPROD"
        purchase = "SIMULATED" if event["simulated_purchase"] else "EXTERNAL"
        mode = f"escrow={escrow} | purchase={purchase}"
    icon, symbol, label, code = STAGES.get(event["phase"], ("•", ">", clean(event["phase"]).upper(), "36"))
    marker = f"[{symbol}]" if ascii_only else icon
    return (f"\n{stamp}  {paint(marker + ' ' + label, '1;' + code)}  {paint('[' + mode + ']', '35')}\n"
            f"  | {clean(event['message'])}\n"
            + paint(f"  `- job {clean(event['job_id'])} | #{clean(event['sequence'])} | {clean(event['phase'])}", "90"))

# Optional browser demonstration

The terminal is now the primary interface; see TERMINAL_RUNBOOK.md. This browser walkthrough is retained as an optional aid.

From the `masumi/` directory:

```sh
CARDANO_CARD_FRONTEND=true PURCHASE_BACKEND=replay CARDANO_CARD_DB=data/replay-demo.db .venv/bin/cardano-card
```

Then open the authenticated demo in your browser without printing its token:

```sh
.venv/bin/python scripts/open_demo.py
```

The launcher passes the local caller token in a URL fragment (not an HTTP request path); the page removes it after loading and retains it only in memory. Alternatively, open `http://127.0.0.1:8080` and enter `CARDANO_CARD_TOKEN` from your private `masumi/.env`. Do not use an AgentCard or Masumi API key in this form. Refreshing requires connecting again.

Choose an outcome and start. Click **Simulate funding**. Success waits for a simulated merchant confirmation, then offers **Simulate payout**. Decline/budget failure offers **Simulate refund request**. Approval/clarification offers a response. Unknown/partial outcomes remain unresolved rather than refunding. **Recover lost response** simulates a timeout after a purchase and recovers the same order without another confirm.

Use the job ID to resume after a browser or server restart, retaining the same database. Download evidence to capture the event timeline and order reference. The export excludes the delivery address and credentials; synthetic IDs remain labelled `SIM-`. Empty chain transaction arrays mean no blockchain proof exists.

## Suggested three-minute walkthrough

- 0:00–0:25: problem and the two money flows: service-fee escrow vs merchant card charge.
- 0:25–1:10: request → simulated funding → merchant evidence → simulated payout.
- 1:10–1:45: decline → buyer refund request → simulated refund.
- 1:45–2:15: lost-confirmation recovery and one saved order; no duplicate purchase.
- 2:15–2:40: evidence export, clear simulation/testnet/real labels.
- 2:40–3:00: remaining external acceptance. Replace simulated claims only after real evidence exists.

This script supports local rehearsal. It is not proof of a working Cardano-network submission yet.

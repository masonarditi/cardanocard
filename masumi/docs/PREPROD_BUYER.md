# Gated Preprod buyer

This client is implemented and tested with mocked HTTP responses. It has not sent a live payment. It implements the pinned Masumi SDK's `Web3CardanoV1` purchase payload; it is not an x402/V2 client.

First complete `LOCAL_MASUMI_SETUP.md`, inspect the node's actual OpenAPI, verify registered seller/agent/fee information, and record compatible contract, auth, timestamp and hash behavior. Set `MASUMI_V1_COMPATIBLE=true` only after that review. The client intentionally requires a local node and a local agent with real Preprod escrow but simulated merchant purchases.

Private `masumi/.env` configuration:

```dotenv
# Only after compatibility review:
MASUMI_V1_COMPATIBLE=true
BUYER_PAYMENT_SERVICE_URL=http://127.0.0.1:3001/api/v1
BUYER_PAYMENT_API_KEY=REPLACE_PRIVATELY
AGENT_IDENTIFIER=REPLACE_WITH_REGISTERED_AGENT
SELLER_VKEY=REPLACE_WITH_SELLER_VERIFICATION_KEY
# Expected fixed test service fee, in lovelace (10 test ADA):
MASUMI_FEE_LOVELACE=10000000
```

Install the optional pinned Masumi dependency before using the client (`uv pip install -e '.[masumi]'`). Do not paste keys into chat or commit them.

Create a private request JSON file matching `/start_job` (26-character lowercase hex `identifier_from_purchaser`, plus `input_data` containing ask, max_total_usd and the flat address fields). Keep the same request ID and exact payload when recovering a lost start response.

```sh
.venv/bin/python -m cardano_card.preprod_buyer start work/buyer-request.json
.venv/bin/python -m cardano_card.preprod_buyer fund JOB_ID
.venv/bin/python -m cardano_card.preprod_buyer status JOB_ID
# Only after the agent reports a definitive no-purchase failure:
.venv/bin/python -m cardano_card.preprod_buyer refund JOB_ID
```

The buyer stores state in `data/preprod-buyer.db`. Preserve it across runs. It verifies the configured agent/seller, exact input hash, fixed fee budget, deadlines and escrow identity before funding. The seller adapter requests pay-by +5m, result +20m, unlock +36m, dispute +52m; node auto-withdraw adds a 10-minute clock margin, so payout is not immediate. Raw node timestamps are preserved. `accepted_by_node` is not proof of funds locked or refunded. Observe the seller and buyer node records and actual Cardano transactions before claiming settlement.

A funding/refund timeout leaves a durable `unknown` record. Repeating the command returns that record and does not send another payment. Resolve it through the node/explorer; do not delete the database or change the request ID to retry. Operator recovery and buyer-node transaction resolution remain future work.

To snapshot the OpenAPI JSON URL exposed by the running node's docs:

```sh
.venv/bin/python scripts/inspect_masumi_openapi.py http://127.0.0.1:3001/ACTUAL_SPEC_PATH
```

Use the actual URL shown by the node; the script does not guess it or automatically enable compatibility.

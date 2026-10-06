# Masumi 0.22.0 compatibility review

Source: official `masumi-network/masumi-payment-service`, tag `0.22.0`, commit `26c7297821fdc9f803d5cca91bc8d36daf178a1a`. Inspected the generated OpenAPI and actual route/service implementations. This review does not substitute for live Preprod acceptance.

- Raw node auth uses the `token` header; local base URL ends in `/api/v1/`.
- JSON OpenAPI is exposed at `/api-docs`; `/docs` is the human documentation page.
- Contract enum is `Web3CardanoV1`. No x402/V2 compatibility is implied.
- Seller payment creation accepts ISO datetime strings; response and buyer purchase payload retain Unix milliseconds as strings.
- Required timing constraints in the actual route: result >= now+15m; pay-by <= result-5m; unlock >= result+15m; dispute >= unlock+15m. Our demo requests +5/+20/+36/+52 minutes.
- Automatic payout starts only when unlock <= now-10m. The minimum chosen demo payout start is therefore approximately creation+46m, before extra processing/confirmation delay. The SDK's original +24h result/+6h unlock defaults were unsuitable for this demo.
- Fixed-price amounts come from the registered agent. We preserve the node's `RequestedFunds` and require the buyer's explicit `MASUMI_FEE_LOVELACE` budget to match before funding, then send `Amounts` explicitly.
- Resolve uses `/payment/resolve-blockchain-identifier`, includes `includeHistory: "true"`, and supplies `CurrentTransaction` / `TransactionHistory` with `txHash`. Node-reported hashes are marked unverified until checked independently.
- The adapter checks escrow ID, input hash, Preprod network, contract generation/address, seller key and requested amounts before trusting funding. A submitted result must match the exact pinned SDK output hash before settlement is accepted.
- Wallet discovery uses `/payment-source/` → `PaymentSources` → `PurchasingWallets`/`SellingWallets`. `/wallet/` requires walletType+id and can return secrets; our status tool does not request it or `includeSecret`.
- Registry creation accepts a string API URL and does not probe the endpoint in this release. A loopback registration can be used for a private local test, but does not create public agent discoverability. Do not call it a publicly reachable agent.

The pinned Python SDK hash helpers come from commit `5a54f5e0755a1b7de52ae1823d767fe442b1ac13` (1.2.0). We use its canonical input hash and escaped output-string hash. Interoperability with other agent clients remains a separate conformance check.

Before enabling Preprod: compare the running node's OpenAPI with this source, verify auth/Preprod payment source, fund test wallets, register a local-only test identity, and record the acceptance results. Health alone never sets the compatibility flag.

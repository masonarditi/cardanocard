# Masumi support note — prepared, not sent

Our hosted Preprod agent registration under **ezzycoin** is stuck at RegistrationRequested. Please inspect the managed Preprod registration worker and configured minting/funding wallet.

- SaaS agent ID: `544fcfcd-29e5-4453-9a04-ac6554956d56`
- Payment-node registry record: `cmuwdqxzm00sk1ytq7t700l44`
- Submitted: 2026-10-06 07:51:29 UTC / 15:51:29 Singapore time
- Name: Cardano Card Preprod
- URL: https://cardanocard-preprod-production.up.railway.app
- Payment source: Web3CardanoV2, Preprod
- Contract: `addr_test1wzs4e6wc95hkwezlccjw9mdvq0r0rsgx6zk34avptga3ftgn37w4g`
- Registry state: RegistrationRequested; `error`, `lastCheckedAt`, `CurrentTransaction`, and `agentIdentifier` are all null.
- Minting wallet: `addr_test1qr47lqluxhtvg0m2dxeayenddw8ly7rze6zwupsr60t6qw90aa5hylqrkcztcd8379zshfnh7juagxyhp364wfvpy6qq7fxngl`
- Recipient wallet: `addr_test1qr27p5asjsfrs8k0z28dspl46v4nu3lkw9catjs8y0adqlhlr5lraddz89trf03a29qyweama7ayheyrfsy4r6vf75tsl0hffe`
- Registry `sendFundingLovelace`: 10000000.
- Blockfrost Preprod returned 404/no address history for both wallets when checked.

POST `/api/agents/{id}/complete-registration` returns 202/pending with “Wallet not yet funded.” We understand that this message is generic for pending outcomes. Please confirm whether the managed funding wallet needs funding, whether the V2 registration worker is running, and whether it can process this existing request without recreating the agent.

There is also a pricing discrepancy: GET `/api/agents/{id}` reports Dynamic top-level pricing but Free in `supportedPaymentSources`; GET `/pay/api/v1/registry?network=Preprod&filterPaymentSourceType=Web3CardanoV2&searchQuery=Cardano%20Card%20Preprod` correctly reports Dynamic in the canonical registry source. Please check SaaS metadata synchronization.

The Railway endpoint intentionally reports unavailable and rejects jobs while integration is incomplete. We have not submitted a customer payment, sent funds to the managed wallets, or created a duplicate hosted registration.

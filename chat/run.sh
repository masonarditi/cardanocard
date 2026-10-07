#!/bin/sh
# Cardano Card (seller, sandbox Agentcard on Mason's Preprod node) + buyer agent + HTTPS tunnel + chat bot.
set -e
cd "$(dirname "$0")"
set -a; . ../masumi/.env.preprod; . ./.env; set +a
mkdir -p data
trap 'kill 0' EXIT

(cd ../masumi && CARDANO_CARD_MODE=preprod PURCHASE_BACKEND=staged_module MASON_STAGED_MODULE=purchase_v2 \
  ALLOW_EXTERNAL_VAULT_CHECKOUT=true VAULT_OPERATOR_EXCLUSIVE=true AGENTCARD_ENV=sandbox CARDANO_CARD_PUBLIC_JOBS=true \
  CARDANO_CARD_TOKEN=$(openssl rand -hex 16) CARDANO_CARD_DB=../chat/data/seller.db CARDANO_CARD_PORT=8787 \
  PYTHONPATH=src .venv/bin/cardano-card > ../chat/data/seller.log 2>&1) &
PYTHONPATH=../masumi/src ../masumi/.venv/bin/uvicorn buyer_agent:app --port 8788 --log-level warning > data/buyer.log 2>&1 &

if [ -n "$SPECTRUM_PROJECT_ID" ]; then
  cloudflared tunnel --url http://localhost:8789 > data/tunnel.log 2>&1 &
  until PUBLIC_URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' data/tunnel.log | head -1) && [ -n "$PUBLIC_URL" ]; do sleep 1; done
  export PUBLIC_URL
fi
sleep 3
bun bot.ts

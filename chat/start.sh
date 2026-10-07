#!/bin/sh
# Hosted entrypoint: buyer agent (:8788, loopback) + chat bot (:$PORT, public). Either dying stops the container so
# Railway restarts both. Required env: V3_AGENT_URL, V2_BUYER_NODE_URL, V2_BUYER_NODE_KEY, PUBLIC_URL,
# DELIVERY_ADDRESS, OWNER_PHONES, SPECTRUM_PROJECT_ID/SECRET (empty = terminal mode), CHANNELS.
set -e
cd "$(dirname "$0")"
mkdir -p "${CHAT_DATA:-/data}"
python -m uvicorn buyer_agent:app --host 127.0.0.1 --port 8788 --log-level warning &
BUYER=$!
trap 'kill $BUYER 2>/dev/null' EXIT
for i in $(seq 1 30); do
  curl -sf http://127.0.0.1:8788/docs >/dev/null 2>&1 && break
  kill -0 $BUYER 2>/dev/null || { echo "buyer agent exited"; exit 1; }
  sleep 1
done
exec bun bot.ts

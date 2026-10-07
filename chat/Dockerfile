# Cardano Card chat front door (iMessage / WhatsApp / Telegram via Photon Spectrum) for Railway: Mason's bot.ts (Bun)
# plus buyer_agent.py (Python), which hires the deployed CardanoCard (/v3) and pays its escrow through the V2 buyer
# node. State (passkeys, buyer.db) lives on the /data volume. No card or wallet secrets: the node holds the wallet.
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip git ca-certificates \
 && curl -fsSL https://bun.sh/install | bash && ln -s /root/.bun/bin/bun /usr/local/bin/bun \
 && rm -rf /var/lib/apt/lists/*
# masumi package (engine models, store, v2 buyer helpers) + the masumi SDK for the input hash
COPY masumi/pyproject.toml masumi/README.md ./masumi/
COPY masumi/src ./masumi/src
COPY masumi/scripts/v2_hire.py ./masumi/scripts/v2_hire.py
RUN pip install --no-cache-dir './masumi[masumi]' && apt-get purge -y git && apt-get autoremove -y
# the chat stack and the assets the status cards render
COPY chat/package.json chat/bun.lock chat/tsconfig.json ./chat/
RUN cd chat && bun install --frozen-lockfile
COPY chat/bot.ts chat/cards.ts chat/page.ts chat/buyer_agent.py chat/start.sh ./chat/
COPY chat/assets ./chat/assets
COPY demo/video/public ./demo/video/public
RUN chmod +x chat/start.sh && mkdir -p /data
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8789 CHAT_DATA=/data CHAT_ASSETS=/app/demo/video/public \
    BUYER_URL=http://127.0.0.1:8788 PYTHONPATH=/app/masumi/src
EXPOSE 8789
CMD ["sh", "chat/start.sh"]

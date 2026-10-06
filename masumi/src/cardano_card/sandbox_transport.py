"""Sandbox-only AgentCard connection with explicit handoff and durable refresh guard."""
import fcntl
import json
import os
import tempfile
import time
import copy
import hashlib
from pathlib import Path
from urllib.parse import quote

import httpx
from dotenv import dotenv_values


def private_json(path, data):
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".agentcard-private-")
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(data, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def readiness(directory):
    directory = Path(directory)
    values = dotenv_values(directory / ".env")
    return {"sandbox_credentials": all(values.get(key) for key in ("AGENTCARD_CLIENT_ID", "AGENTCARD_CLIENT_SECRET")),
            "linked_user_tokens": (directory / ".agentcard_tokens.json").is_file(),
            "refresh_needs_reconciliation": (directory / ".agentcard_refresh_pending").exists()}


class SandboxTransport:
    simulated = False

    def __init__(self, directory, *, exclusive_until, client=None, clock=time.time, emit=print):
        self.directory, self.clock, self.emit = Path(directory), clock, emit
        self.exclusive_until, self.halted = exclusive_until, False
        self.require_handoff()
        ready = readiness(self.directory)
        if not ready["sandbox_credentials"] or not ready["linked_user_tokens"]:
            raise ValueError("Matching agentcard/.env and .agentcard_tokens.json are required")
        if ready["refresh_needs_reconciliation"]:
            raise ValueError("Earlier token refresh is unresolved; stop and arrange a fresh handoff with Mason")
        self.values = dotenv_values(self.directory / ".env")
        self.token_path = self.directory / ".agentcard_tokens.json"
        self.marker = self.directory / ".agentcard_refresh_pending"
        self._lock = open(self.directory / ".agentcard_tokens.lockfile", "a")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lock.close()
            raise ValueError("Another local sandbox runner owns the token file") from None
        self.client = client or httpx.AsyncClient(base_url="https://api.agentcard.sh", timeout=150)
        self.owned = client is None
        self.org_token = None
        self.org_expires = 0

    def require_handoff(self):
        if self.halted or self.clock() >= self.exclusive_until:
            raise ValueError("Sandbox access stopped; Mason's current exclusive token handoff is required")

    async def close(self):
        try:
            if self.owned:
                await self.client.aclose()
        finally:
            self._lock.close()

    async def verify_sandbox(self):
        try:
            return await self._verify_sandbox()
        except Exception:
            self.halted = True
            raise ValueError("Sandbox identity verification failed; access stopped") from None

    async def _verify_sandbox(self):
        self.require_handoff()
        if self.clock() >= self.org_expires - 60:
            response = await self.client.post("/api/v2/oauth/token", data={"grant_type": "client_credentials",
                "client_id": self.values["AGENTCARD_CLIENT_ID"], "client_secret": self.values["AGENTCARD_CLIENT_SECRET"]})
            response.raise_for_status()
            data = response.json()
            self.org_token = data["access_token"]
            self.org_expires = self.clock() + float(data["expires_in"])
        response = await self.client.get("/api/v2", headers={"Authorization": "Bearer " + self.org_token})
        response.raise_for_status()
        if response.json().get("test_mode") is not True:
            self.halted = True
            raise ValueError("AgentCard did not confirm test_mode=true; user access is blocked")
        return True

    async def user_token(self):
        self.require_handoff()
        try:
            tokens = json.loads(self.token_path.read_text())
            if not all(tokens.get(key) for key in ("user_id", "access_token", "refresh_token", "expires_at")):
                raise ValueError("Incomplete token file")
        except Exception:
            self.halted = True
            raise ValueError("User token file is incomplete; access stopped") from None
        if self.clock() >= float(tokens["expires_at"]) - 60:
            # Rotation cannot be retried after a lost response, including on restart.
            private_json(self.marker, {"started_at": self.clock()})
            self.emit("AGENTCARD | refreshing shared sandbox token once")
            try:
                response = await self.client.post("/api/v2/connect/refresh",
                    headers={"Authorization": "Bearer " + self.org_token},
                    json={"refresh_token": tokens["refresh_token"]})
                response.raise_for_status()
                data = response.json()
                if not data.get("access_token") or not data.get("refresh_token"):
                    raise ValueError("Invalid token refresh")
                tokens.update(access_token=data["access_token"], refresh_token=data["refresh_token"],
                              expires_at=self.clock() + float(data["expires_in"]))
                private_json(self.token_path, tokens)
                self.marker.unlink()
            except Exception:
                self.halted = True
                self.emit("STOP | token refresh unresolved; do not retry. Ask Mason to re-link and hand off fresh files.")
                raise ValueError("Token refresh needs reconciliation with Mason") from None
        return tokens["access_token"]

    async def headers(self):
        await self.verify_sandbox()
        token = await self.user_token()
        self.require_handoff()
        return {"Authorization": "Bearer " + token}

    def check_auth(self, response):
        if response.status_code in {401, 403}:
            self.halted = True
            self.emit("STOP | AgentCard user access rejected; no refresh retry. Ask Mason to re-link.")
            raise ValueError("AgentCard token rejected")

    async def buy(self, body, request_id):
        headers = await self.headers()
        self.emit("AGENTCARD | " + ("confirming saved cart once" if "confirm" in body else "requesting cart"))
        response = await self.client.post("/buy", headers=headers, json=body)
        self.check_auth(response)
        receipt = self.directory / (".agentcard-response-" + hashlib.sha256(request_id.encode()).hexdigest() + ".json")
        # Save the provider response before optional metadata reads. A metadata
        # outage must never discard the only conversation ID after a write.
        private_json(receipt, {"status_code": response.status_code, "body": response.text,
                              "received_at": self.clock(), "confirm": "confirm" in body})
        try:
            data = response.json()
        except ValueError:
            self.emit("AGENTCARD | non-JSON response saved privately; do not repeat this write")
            return response.status_code, {}
        if response.is_success and isinstance(data, dict) and data.get("cart") and "confirm" not in body:
            cart = data["cart"]
            if isinstance(cart, dict) and cart.get("currency") is None:
                data = await self.enrich_currency(data)
        return response.status_code, data

    async def enrich_currency(self, data):
        try:
            metadata = await self.client.get("/buy/merchants", headers=await self.headers())
            self.check_auth(metadata)
            metadata.raise_for_status()
            return self.with_currency_evidence(data, metadata.json(), self.clock())
        except Exception:
            self.emit("AGENTCARD | currency metadata unavailable; original response retained, confirmation blocked")
            return data

    @staticmethod
    def with_currency_evidence(data, catalogue, observed_at):
        """Use authenticated merchant market metadata; never infer USD from absence."""
        data = copy.deepcopy(data)
        merchants = catalogue.get("merchants", [])
        carts = [data.get("cart"), *(data.get("carts") or [])]
        for cart in carts:
            if not isinstance(cart, dict) or cart.get("currency") is not None:
                continue
            matches = [m for m in merchants if m.get("slug") == cart.get("merchant") and m.get("slug")]
            if len(matches) != 1:
                continue
            market = matches[0].get("market") or {}
            currency = market.get("currency")
            if currency != "USD" or "US" not in market.get("countries", []):
                continue
            if cart.get("merchant_currency") not in {None, "USD", "usd"}:
                continue
            cart["currency"] = currency
            cart["currency_evidence"] = {"source": "AgentCard GET /buy/merchants", "merchant": cart["merchant"],
                                         "currency": currency, "observed_at": observed_at}
        return data

    async def conversation(self, conversation_id, request_id):
        response = await self.client.get("/buy/conversations/" + quote(conversation_id, safe=""), headers=await self.headers())
        self.check_auth(response)
        response.raise_for_status()
        return await self.enrich_currency(response.json())

    async def track(self, order_id, request_id):
        # Sandbox should never produce a fulfilled order. Never fabricate evidence.
        self.halted = True
        self.emit("STOP | unexpected order in sandbox; operator review required")
        return {}

import fcntl
import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

HERE = Path(__file__).parent
PROD = os.environ.get("AGENTCARD_ENV") == "prod"
load_dotenv(HERE / (".env.prod" if PROD else ".env"), override=True)
BASE = "https://api.agentcard.sh"
TOKENS = HERE / (".agentcard_tokens.prod.json" if PROD else ".agentcard_tokens.json")
_org = {"token": None, "exp": 0}


class ApiError(Exception):
    def __init__(self, status, body):
        super().__init__(f"{status}: {body}")
        self.status, self.body = status, body


def _json(r):
    try:
        return r.json()
    except ValueError:
        return {"error": r.text}


def _check(r):
    if r.status_code >= 400:
        raise ApiError(r.status_code, _json(r))
    return _json(r)


def org_token():
    if time.time() < _org["exp"] - 60:
        return _org["token"]
    d = _check(requests.post(f"{BASE}/api/v2/oauth/token", timeout=30, data={
        "grant_type": "client_credentials",
        "client_id": os.environ["AGENTCARD_CLIENT_ID"],
        "client_secret": os.environ["AGENTCARD_CLIENT_SECRET"],
    }))
    _org.update(token=d["access_token"], exp=time.time() + d["expires_in"])
    return _org["token"]


def org(method, path, **kw):
    return _check(requests.request(method, BASE + path, timeout=30,
                                   headers={"Authorization": f"Bearer {org_token()}"}, **kw))


def save_tokens(user_id, d):
    # Each refresh returns a new single-use refresh token: write it atomically, owner-only.
    tmp = TOKENS.with_suffix(f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"user_id": user_id, "access_token": d["access_token"], "refresh_token": d["refresh_token"],
                   "expires_at": time.time() + d["expires_in"]}, f, indent=2)
    os.replace(tmp, TOKENS)


# Shared with masumi's SandboxTransport: one lockfile and one "refresh in flight" marker per directory.
# Sandbox keeps the names SandboxTransport uses; prod gets its own so a stale sandbox marker can't block it.
LOCKFILE = HERE / (".agentcard_tokens.prod.lockfile" if PROD else ".agentcard_tokens.lockfile")
REFRESH_MARKER = HERE / (".agentcard_refresh_pending.prod" if PROD else ".agentcard_refresh_pending")


def user_token():
    t = json.loads(TOKENS.read_text())
    if time.time() <= t["expires_at"] - 60:
        return t["access_token"]
    with open(LOCKFILE, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another process holds the Agentcard token lock; not refreshing")
        t = json.loads(TOKENS.read_text())  # another process may have refreshed already
        if time.time() > t["expires_at"] - 60:
            if REFRESH_MARKER.exists():
                raise RuntimeError("an earlier token refresh did not finish; reconcile before refreshing again")
            org_token()  # fetch the org token first: a failure here never spends the refresh token
            REFRESH_MARKER.write_text(json.dumps({"started_at": time.time()}))
            save_tokens(t["user_id"], org("POST", "/api/v2/connect/refresh", json={"refresh_token": t["refresh_token"]}))
            REFRESH_MARKER.unlink()
            t = json.loads(TOKENS.read_text())
    return t["access_token"]


def buy(body, timeout=150):
    r = requests.post(f"{BASE}/buy", json=body, timeout=timeout,
                      headers={"Authorization": f"Bearer {user_token()}"})
    return r.status_code, _json(r)


def conversation(conversation_id):
    return _check(requests.get(f"{BASE}/buy/conversations/{conversation_id}", timeout=30,
                               headers={"Authorization": f"Bearer {user_token()}"}))

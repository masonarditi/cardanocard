import argparse
import sys
import time

from agentcard import TOKENS, ApiError, org, save_tokens

p = argparse.ArgumentParser()
p.add_argument("--partner", action="store_true", help="request app auto-approval (production only)")
p.add_argument("--connect", metavar="EMAIL_OR_PHONE", help="existing account: connect by one-time code, no vault link")
args = p.parse_args()


def connect_by_code(contact):
    a = org("POST", "/api/v2/connect/start", json={"email" if "@" in contact else "phone": contact})
    code = input(f"Code sent by {a['channel']} (sandbox: 111111): ").strip()
    return org("POST", "/api/v2/connect/verify", json={"connect_id": a["id"], "code": code})


def connect_by_vault_link():
    s = org("POST", "/api/v2/vault_sessions", json={"approval_mode": "partner"} if args.partner else {})
    print(f"Session {s['id']} (test_mode={s.get('test_mode')}). Open this link and add a card:\n")
    print(s["url"])
    print()
    while True:
        time.sleep(s.get("poll_interval") or 3)
        st = org("GET", f"/api/v2/vault_sessions/{s['id']}")
        if st["status"] == "expired":
            sys.exit("Session expired. Run again.")
        perm = st.get("payment_permission") or {}
        if st["status"] == "linked" and (not args.partner or perm.get("ready")):
            break
        print(f"  {st['status']}{' / permission ' + str(perm.get('status')) if perm else ''}...")
    try:
        return org("POST", f"/api/v2/vault_sessions/{s['id']}/exchange")
    except ApiError as e:
        if "account_verification_required" not in str(e.body):
            sys.exit(f"Exchange failed: {e}")
        return connect_by_code(input("Existing account: enter its email or phone (E.164) for a one-time code: ").strip())


c = connect_by_code(args.connect) if args.connect else connect_by_vault_link()
save_tokens(c["user"]["id"], c)
print(f"Connected user {c['user']['id']}. Tokens saved to {TOKENS.name}")

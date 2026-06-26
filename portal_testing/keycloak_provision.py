"""Provision a dedicated MFA test user in the Keycloak ``factory`` realm.

Run this once (in-cluster, where the Keycloak admin API is reachable). It creates
(or resets) a test user with a password AND an enrolled TOTP credential whose
secret we control -- so the Playwright harness can mint real OTP codes.

The Keycloak TOTP trick: Keycloak stores the OTP secret as a raw string ``R`` and
the authenticator's base32 secret is ``base32(R)``. So we set the credential's
``secretData.value = R`` and hand the harness ``base32(R)`` for pyotp.

Env:
  KC_URL              Keycloak base (default http://keycloak:8080 in-cluster)
  KC_ADMIN_PASSWORD   master-realm admin password (NEVER passed on argv)
  TEST_USER           username to create (default harness-tester)
  TEST_PASSWORD       password to set (default generated)

Prints the env block (TEST_USER / TEST_PASSWORD / TEST_TOTP_SECRET) for the run.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import sys
import urllib.request

KC = os.environ.get("KC_URL", "http://keycloak:8080").rstrip("/")
REALM = os.environ.get("KEYCLOAK_REALM", "factory")
ADMIN = os.environ.get("KC_ADMIN_USER", "admin")
ADMIN_PW = os.environ.get("KC_ADMIN_PASSWORD", "")
USER = os.environ.get("TEST_USER", "harness-tester")
PW = os.environ.get("TEST_PASSWORD") or ("Hx-" + secrets.token_urlsafe(12))


def _req(
    method: str, path: str, token: str | None = None, body: dict | None = None
) -> tuple[int, bytes]:
    url = f"{KC}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _form(path: str, fields: dict) -> tuple[int, bytes]:
    import urllib.parse

    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(f"{KC}{path}", data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def admin_token() -> str:
    st, body = _form(
        "/realms/master/protocol/openid-connect/token",
        {
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": ADMIN,
            "password": ADMIN_PW,
        },
    )
    if st != 200:
        raise SystemExit(f"admin token failed ({st}): {body[:200]!r}")
    return json.loads(body)["access_token"]


def main() -> int:
    if not ADMIN_PW:
        raise SystemExit("KC_ADMIN_PASSWORD not set")
    tok = admin_token()

    # Find or create the user.
    st, body = _req(
        "GET", f"/admin/realms/{REALM}/users?username={USER}&exact=true", tok
    )
    users = json.loads(body) if st == 200 else []
    if users:
        uid = users[0]["id"]
    else:
        st, _ = _req(
            "POST",
            f"/admin/realms/{REALM}/users",
            tok,
            {
                "username": USER,
                "enabled": True,
                "emailVerified": True,
                "email": f"{USER}@example.test",
                "firstName": "Harness",
                "lastName": "Tester",
            },
        )
        if st not in (201, 204):
            raise SystemExit(f"create user failed ({st})")
        st, body = _req(
            "GET", f"/admin/realms/{REALM}/users?username={USER}&exact=true", tok
        )
        uid = json.loads(body)[0]["id"]

    # Set a permanent password.
    st, _ = _req(
        "PUT",
        f"/admin/realms/{REALM}/users/{uid}/reset-password",
        tok,
        {"type": "password", "value": PW, "temporary": False},
    )
    if st not in (204, 200):
        raise SystemExit(f"set password failed ({st})")

    # Enroll a TOTP credential with a secret we control. Keycloak 26 has no
    # POST .../credentials create endpoint, but it *imports* credentials given
    # in the user representation (the realm export/import round-trip path).
    raw = secrets.token_hex(20)  # 40-char raw secret R
    totp_b32 = base64.b32encode(raw.encode()).decode().rstrip("=")
    cred = {
        "type": "otp",
        "userLabel": "harness-totp",
        "secretData": json.dumps({"value": raw}),
        "credentialData": json.dumps(
            {
                "subType": "totp",
                "digits": 6,
                "counter": 0,
                "period": 30,
                "algorithm": "HmacSHA1",
            }
        ),
    }
    # Clear any existing OTP creds first (idempotent re-runs).
    st, body = _req("GET", f"/admin/realms/{REALM}/users/{uid}/credentials", tok)
    for c in json.loads(body) if st == 200 else []:
        if c.get("type") == "otp":
            _req(
                "DELETE",
                f"/admin/realms/{REALM}/users/{uid}/credentials/{c['id']}",
                tok,
            )
    # Import the OTP credential via the user representation.
    st, body = _req(
        "PUT", f"/admin/realms/{REALM}/users/{uid}", tok, {"credentials": [cred]}
    )
    if st not in (204, 200):
        raise SystemExit(f"import otp credential failed ({st}): {body[:200]!r}")
    # Verify it stuck.
    st, body = _req("GET", f"/admin/realms/{REALM}/users/{uid}/credentials", tok)
    if not any(c.get("type") == "otp" for c in (json.loads(body) if st == 200 else [])):
        raise SystemExit("otp credential did not persist after PUT")

    # Remove any CONFIGURE_TOTP required action so login goes straight to the OTP prompt.
    _req("PUT", f"/admin/realms/{REALM}/users/{uid}", tok, {"requiredActions": []})

    print("# MFA test user provisioned in realm", REALM)
    print(f"export TEST_USER={USER}")
    print(f"export TEST_PASSWORD='{PW}'")
    print(f"export TEST_TOTP_SECRET={totp_b32}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Mint the read-only reporter JWT for SUPABASE_READ_KEY.

    SUPABASE_JWT_SECRET=... python3 infra/mint_reporter_jwt.py
    python3 infra/mint_reporter_jwt.py            # prompts, hidden

The reporter role (migrations.sql, 2026-08-30) can SELECT the research tables
and nothing else. PostgREST switches to it when a request's JWT carries
{"role": "reporter"}, and Supabase's gateway accepts the same JWT as the
apikey header, so one token serves both. The signing secret is the project's
legacy JWT secret (Supabase dashboard: Project Settings, API, JWT Settings).
A project that has moved to asymmetric signing keys still accepts HS256
tokens signed with the legacy secret unless that secret has been revoked; if
it has, use the dashboard's own key minting instead.

The secret is read from the environment or a hidden prompt and never from a
file, and the token is printed once. Stdlib only: no PyJWT dependency for a
script that runs about once a decade.
"""
import base64
import getpass
import hashlib
import hmac
import json
import os
import sys
import time

ROLE = "reporter"
YEARS = 10


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def mint(secret: str, role: str = ROLE, years: int = YEARS, now=None) -> str:
    now = int(time.time()) if now is None else now
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"role": role, "iss": "supabase", "iat": now,
               "exp": now + years * 365 * 24 * 3600}
    signing_input = (b64url(json.dumps(header, separators=(",", ":")).encode())
                     + "." + b64url(json.dumps(payload, separators=(",", ":")).encode()))
    sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return signing_input + "." + b64url(sig)


def main() -> int:
    secret = os.environ.get("SUPABASE_JWT_SECRET") or getpass.getpass(
        "Project JWT secret (hidden): ")
    if len(secret) < 32:
        print("That does not look like a Supabase JWT secret (too short).",
              file=sys.stderr)
        return 1
    token = mint(secret)
    print(token)
    print(f"\nrole={ROLE}, valid {YEARS} years. Put it in "
          f"[connections.supabase_connection] as SUPABASE_READ_KEY, in\n"
          f".streamlit/secrets.toml AND in the Railway STREAMLIT_SECRETS_TOML "
          f"variable.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Calling Cloudflare Workers AI, shared by every generator in brand/.

This is the plumbing and NOTHING about what any picture looks like: credentials,
a working SSL context, the FLUX.2 multipart encoding, and one call. What a guide
hero looks like lives in `build_ai_hero.py`; what a book opener looks like lives
in `build_book_opener.py`; neither imports the other.

IT EXISTS BECAUSE THE BOOK WAS IMPORTING THE GUIDES. `build_book_opener.py` took
`credentials` and `generate` from `build_ai_hero.py`, and `generate` had grown a
`size=` parameter that only the book wanted. So a change to the site's hero
generator could break the book and vice versa, and the 3.5:1 web banner's
constants sat in the same file as a 2:1 printed opener's. Extracting the shared
half is what separates them without copying eighty lines into a second place,
which is the drift trap CLAUDE.md records against chart twins over and over.

`size` is REQUIRED-ish rather than defaulted: it was `size=BAND` when this code
lived beside BAND, which quietly made a web banner the default shape for every
caller. Pass what you want. `None` sends no dimensions at all, which is the only
thing schnell accepts.
"""
import base64
import io
import json
import os
import ssl
import uuid
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent

# THE 4B/9B DISTINCTION IS A LICENCE, NOT A SIZE. Black Forest Labs released
# FLUX.2 [klein] 4B under Apache-2.0 and FLUX.2 [klein] 9B under the FLUX
# Non-Commercial License. The 9B is the tempting one -- it answered in 3s
# against the 4B's 15s in testing -- and it is the one this project may not use.
# Picking by parameter count gets that exactly backwards.
#   https://bfl.ai/licensing
#
#   klein4b  DEFAULT. Apache-2.0, so no reasoning required about weights or
#            outputs. Takes width/height, so it returns the band directly and
#            nothing is cropped. Newer generation than schnell.
#   schnell  Apache-2.0 as well, kept as a fallback. No dimension parameters at
#            all, so it returns ~1024x1024 and the band is cropped out of it.
#   klein9b  Present so the name resolves, and rejected in build(): the weights
#            are non-commercial and this site is public.
#
# FLUX.2 [dev] is deliberately ABSENT. It returned HTTP 500 in about a second on
# this account regardless of parameters, so it is not merely a licence question
# here -- it does not work. Its weights are non-commercial too, though its
# licence does carve outputs out ("You may use Output for any purpose").
MODELS = {
    "klein4b": "@cf/black-forest-labs/flux-2-klein-4b",
    "schnell": "@cf/black-forest-labs/flux-1-schnell",
    "klein9b": "@cf/black-forest-labs/flux-2-klein-9b",
}
NON_COMMERCIAL = {"klein9b"}
MULTIPART_MODELS = {"klein4b", "klein9b"}     # the FLUX.2 family; schnell is JSON


def credentials():
    """Account id and API token, from the environment or secrets.toml.

    wrangler.toml deliberately omits account_id because this repo is public;
    the same reasoning applies to both of these. `.streamlit/secrets.toml` is
    already gitignored and already holds the admin key, so it is the existing
    home for a local secret rather than a new one.
    """
    acct = os.environ.get("CF_ACCOUNT_ID") or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    token = os.environ.get("CF_API_TOKEN") or os.environ.get("CLOUDFLARE_API_TOKEN")
    if not (acct and token):
        secrets = ROOT / ".streamlit" / "secrets.toml"
        if secrets.exists():
            try:                                   # tomllib is stdlib on 3.11+
                import tomllib
                data = tomllib.loads(secrets.read_text())
            except Exception:
                data = {}
            # CASE-INSENSITIVE. TOML keys are case-sensitive and the first
            # version looked only for lowercase, so a file holding
            # CF_ACCOUNT_ID reported "no credentials" while sitting right next
            # to them. Uppercase is also the convention this very file already
            # uses for COLLEGE_SCORECARD_API_KEY, so lowercase was the wrong
            # thing to have asked for. Accept either and stop caring.
            lower = {k.lower(): v for k, v in data.items()}
            acct = acct or lower.get("cf_account_id")
            token = token or lower.get("cf_api_token")
    if not (acct and token):
        raise SystemExit(
            "  NO CREDENTIALS. Set CF_ACCOUNT_ID and CF_API_TOKEN in the\n"
            "  environment, or add them to .streamlit/secrets.toml (either\n"
            "  case works).\n\n"
            "  The token needs Workers AI: Read and NOTHING else. It is a\n"
            "  different credential from the wrangler deploy OAuth, and it must\n"
            "  never be committed -- this repo is public.\n"
            "  Create it: Cloudflare dashboard > My Profile > API Tokens >\n"
            "  Create Token > Custom token > Workers AI: Read.")
    return acct, token


def _ssl_context():
    """An SSL context that actually has root certificates.

    The macOS python.org framework build ships without them wired into the
    default context, so urllib raises CERTIFICATE_VERIFY_FAILED against a
    perfectly healthy host while curl, which uses the system store, succeeds.
    That asymmetry sends you looking for a network fault: on this machine
    CLAUDE.md documents a TLS-intercepting proxy that produces the same error,
    so the first suspicion is the proxy rather than the interpreter. It was not
    the proxy -- curl returned real Cloudflare JSON with no fire.glass marker.
    certifi is already installed as a transitive dependency.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _multipart(fields: dict):
    """A real multipart/form-data body.

    The FLUX.2 models declare their input schema as a single required
    `multipart` object, which is Cloudflare's way of saying "send an actual
    multipart request", not "send JSON with a multipart key". A plain JSON body
    is rejected with `required properties at '/' are 'multipart'`, which reads
    like a missing field rather than a wrong encoding.
    """
    b = uuid.uuid4().hex
    parts = [f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n"
             for k, v in fields.items()]
    parts.append(f"--{b}--\r\n")
    return "".join(parts).encode(), f"multipart/form-data; boundary={b}"


def generate(prompt: str, seed: int, steps: int, acct: str, token: str,
             model: str = "klein4b", size=None) -> Image.Image:
    """One call to Workers AI. Returns a PIL image.

    The FLUX.2 family is asked for the size directly and needs multipart;
    schnell takes JSON, has no dimension parameters, and is cropped by the
    caller.

    `size` defaults to the guide band, so every caller here is unchanged. It
    exists for `build_book_opener.py`, which wants a 2:1 page opener rather
    than a 3.5:1 web banner and must not go anywhere near `crop_to_band` or
    `compose_hero`. Dimensions must be multiples of 16, as BAND's own comment
    records.
    """
    if model in NON_COMMERCIAL:
        raise SystemExit(
            f"  {model} weights are under the FLUX Non-Commercial License and\n"
            f"  this project is published and sold. Use klein4b, which is\n"
            f"  Apache-2.0 and the same FLUX.2 generation.\n"
            f"  https://bfl.ai/licensing")
    if size and any(n % 16 for n in size):
        raise SystemExit(f"  {size} is not a multiple of 16 on both axes")
    fields = {"prompt": prompt, "seed": seed, "steps": steps}
    if model in MULTIPART_MODELS:
        if not size:
            raise SystemExit(f"  {model} needs a size")
        fields["width"], fields["height"] = size
        data, ctype = _multipart(fields)
    else:
        data, ctype = json.dumps(fields).encode(), "application/json"
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{MODELS[model]}",
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": ctype},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120, context=_ssl_context()) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as err:
        # Cloudflare puts the real reason in the body; the status alone sends
        # you looking in the wrong place (403 is usually a token scope, not a
        # bad account id).
        raise SystemExit(f"  Workers AI HTTP {err.code}: {err.read().decode()[:400]}")
    except urllib.error.URLError as err:
        raise SystemExit(f"  Workers AI unreachable: {err.reason}")

    if not payload.get("success", True) and payload.get("errors"):
        raise SystemExit(f"  Workers AI error: {payload['errors']}")
    b64 = (payload.get("result") or {}).get("image")
    if not b64:
        raise SystemExit(f"  no image in response: {json.dumps(payload)[:400]}")
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")

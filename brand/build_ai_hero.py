#!/usr/bin/env python3
"""Guide hero backgrounds, generated with Cloudflare Workers AI (FLUX.1 schnell).

    python3 brand/build_ai_hero.py --list
    python3 brand/build_ai_hero.py --slug for-parents-run-the-numbers
    python3 brand/build_ai_hero.py --slug <slug> --dry-run     # prompt only, no call

WHY FLUX.1 [schnell] AND NOT FLUX.2. schnell is Apache-2.0: commercial use of
the model and its outputs is unrestricted. FLUX.2 [dev] is the better model and
is the only one of the two that takes width/height, which a 3.5:1 banner
actually wants -- but it ships under the FLUX Non-Commercial License, Black
Forest Labs directs commercial use to their own API or a paid licence, and
whether consuming it through Cloudflare's hosting clears that is ambiguous.
worthmydegree.com is public. Apache-2.0 removes the question and the price is
one crop, because schnell takes no dimensions and returns ~1024x1024.

    https://developers.cloudflare.com/ai/models/@cf/black-forest-labs/flux-1-schnell/
    https://bfl.ai/legal/non-commercial-license-terms

WHAT THIS MUST NEVER GENERATE. This project's entire claim is real federal data
rather than guesses, and the parent guide carries two dozen figures each checked
against the code. A generated image is DECORATION AND NEVER EVIDENCE: no charts,
no numbers, no dashboards, no screenshots of an interface that does not exist.
A plausible-looking fake graph on this site would undermine the one thing the
whole thing refuses to do. Prompts stay on people, desks, light and texture.

WHY BUILD TIME AND NOT REQUEST TIME. The output is a committed PNG served like
every other asset. Generating per request would cost money per view, make a page
render depend on Workers AI being up, and return a different picture every time.

REPRODUCIBILITY. Every entry in PROMPTS pins a seed and a step count, so an
image can be regenerated exactly. That matters here: `build_guide_graphics.py`
is byte-identical across runs, which is what lets a change in output size mean
something rather than being compression noise. Do not edit a prompt in place
without changing the seed too, or the recorded pair stops describing the file.

COST. 1024x1024 at 4 steps is 4 tiles plus 4 steps, about $0.00065 an image.
"""
import argparse
import base64
import io
import json
import os
import re
import ssl
import sys
import uuid
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from build_guide_graphics import (                           # noqa: E402
    STATIC, compose_hero, compose_og_card)

ROOT = Path(__file__).resolve().parent.parent
# 464 not 460: the FLUX.2 models want dimensions on a multiple of 16, and this
# is the nearest to the 3.5:1 band the template expects.
BAND = (1600, 464)

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
MODEL = MODELS["klein4b"]

# (prompt, seed, steps). Seed and steps are part of the record, not defaults.
PROMPTS = {
    "for-parents-run-the-numbers": (
        "A parent and a teenager sitting together at a kitchen table in warm "
        "late afternoon light, papers and a laptop between them, seen from "
        "behind and slightly to one side so no faces are visible. Shallow "
        "depth of field, documentary photography, muted natural colour, "
        "unposed. No text, no charts, no screens showing content.",
        20260812, 4),
    "parent-plus-senior-year": (
        "An empty kitchen table at dusk with a single envelope and a mug, soft "
        "window light, quiet and still, documentary photography, muted natural "
        "colour. No text, no charts, no visible screens.",
        20260811, 4),
    "community-college-first": (
        "A community college walkway on an ordinary weekday morning, a student "
        "with a backpack walking toward a low plain building, seen from behind "
        "so no face is visible. Unglamorous and everyday, documentary "
        "photography, muted natural colour, overcast light, unposed. No text, "
        "no signage, no charts, no screens showing content.",
        20260814, 4),
    "switching-repayment-plans-2026": (
        "A grown adult woman in her early thirties, full adult height and "
        "build, sitting alone at a kitchen table late in the evening after "
        "her child has gone to bed, photographed from directly behind her so "
        "that the back of her head and shoulders fill the foreground and no "
        "part of her face is visible. A high chair pushed against the wall "
        "and a few toys left on the floor behind her, a stack of opened mail "
        "and a laptop on the table. Shallow depth of field, documentary "
        "photography, muted natural colour, unposed. No children in the "
        "frame, no text, no charts, no screens showing content.",
        20260815, 4),
    # Shot from directly above, unlike the four behind-the-shoulder heroes, so
    # the guides index does not read as five photographs of the same room.
    # NOTE: klein4b puts illegible squiggle "text" on paper regardless of the
    # "No text" instruction. Checked at 4x on this one: no readable words, no
    # digits, no charts. That clears the rule at the top of this file, which
    # bans fake EVIDENCE rather than the texture of a printed page. Re-check
    # at magnification if this prompt is ever reseeded.
    "consolidating-student-loans-2026": (
        "Overhead flat lay of a dining table in flat morning light, six or "
        "seven opened letters and statements spread across the wood and being "
        "gathered into one neat stack by a pair of adult hands, a pen and a "
        "cold cup of coffee to one side, photographed from directly above so "
        "no face is in the frame. Documentary photography, muted natural "
        "colour, unposed. No text, no charts, no screens showing content.",
        20260815, 4),
    "repayment-plans-2026-what-changed": (
        "Two nearly identical printed forms lying side by side on a plain desk "
        "in flat window light, one slightly out of alignment with the other, a "
        "pen resting across the corner of the left one, an adult hand just "
        "entering the frame from the right. Photographed from above at a slight "
        "angle so no face is in the frame. Documentary photography, muted "
        "natural colour, unposed. No text, no charts, no screens showing "
        "content.",
        20260819, 4),
    "for-counselors-the-money-conversation": (
        "A small high school counselor's office in late afternoon light, two "
        "chairs turned toward each other across a corner of a desk, a student's "
        "backpack on the floor, seen from behind and to one side so no faces "
        "are visible. Shallow depth of field, documentary photography, muted "
        "natural colour, unposed. No text, no charts, no screens showing "
        "content.",
        20260813, 4),
}


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
             model: str = "klein4b") -> Image.Image:
    """One call to Workers AI. Returns a PIL image.

    The FLUX.2 family is asked for the band directly and needs multipart;
    schnell takes JSON, has no dimension parameters, and is cropped by the
    caller.
    """
    fields = {"prompt": prompt, "seed": seed, "steps": steps}
    if model in MULTIPART_MODELS:
        fields["width"], fields["height"] = BAND
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


def crop_to_band(im: Image.Image, size=BAND) -> np.ndarray:
    """Centre-crop the square output to the banner, then resize.

    schnell has no width/height, so this is the only way to a 3.5:1 band. The
    crop takes the middle horizontally and sits ABOVE centre vertically: the
    subject in a photo tends to sit high, and the bottom of the band is cut away
    by the curve anyway.
    """
    target = size[0] / size[1]
    w, h = im.size
    keep_h = int(w / target)
    if keep_h > h:                       # unusually wide source; crop width
        keep_w = int(h * target)
        box = ((w - keep_w) // 2, 0, (w - keep_w) // 2 + keep_w, h)
    else:
        top = max(0, int(h * 0.30) - keep_h // 2)
        top = min(top, h - keep_h)
        box = (0, top, w, top + keep_h)
    return np.asarray(im.crop(box).resize(size, Image.LANCZOS))


def build(slug: str, dry_run: bool = False, model: str = "klein4b"):
    if slug not in PROMPTS:
        raise SystemExit(f"  no prompt for {slug!r}. Known: {', '.join(PROMPTS)}")
    if model in NON_COMMERCIAL:
        raise SystemExit(
            f"  {model} weights are under the FLUX Non-Commercial License and\n"
            f"  worthmydegree.com is a public site. Use klein4b, which is\n"
            f"  Apache-2.0 and the same FLUX.2 generation.\n"
            f"  https://bfl.ai/licensing")
    prompt, seed, steps = PROMPTS[slug]
    # The model rides in the filename. Two files generated from one prompt by
    # different models are different pictures, and a shared name would let the
    # second silently replace the first with no way to tell which is published.
    name = f"guide-hero-{slug}.png" if model == "schnell" \
        else f"guide-hero-{slug}-{model}.png"
    print(f"  slug   {slug}\n  model  {model} ({MODELS[model]})"
          f"\n  seed   {seed}   steps {steps}\n  prompt {prompt[:70]}...")
    if dry_run:
        print("  --dry-run: no call made")
        return
    acct, token = credentials()
    raw = generate(prompt, seed, steps, acct, token, model)
    print(f"  model returned {raw.size[0]}x{raw.size[1]}")
    # flux2 returns the band already; schnell needs the crop.
    band = np.asarray(raw) if raw.size == BAND else crop_to_band(raw)
    src = compose_hero(name=name, background=band)
    dst = STATIC / src.name
    dst.write_bytes(src.read_bytes())
    print(f"  wrote {src.relative_to(ROOT)} ({src.stat().st_size:,} bytes) "
          f"-> {dst.relative_to(ROOT)}")
    write_og_card(slug, src)
    print(f"  add to the post's front matter:  hero: {name}")


def write_og_card(slug: str, hero_png: Path):
    """The link-preview card for `slug`, cropped from the hero just written.

    Emitted by the SAME command as the hero, so a guide cannot end up with a
    photograph on the page and the generic house card in every share of it --
    which is what shipped first, and which is invisible from the page itself.
    The name is what infra/build_site.py's og_image_for() derives, so nothing
    has to be declared in front matter.
    """
    card = compose_og_card(f"guide-og-{slug}.png", hero_png)
    dst = STATIC / card.name
    dst.write_bytes(card.read_bytes())
    print(f"  wrote {card.relative_to(ROOT)} ({card.stat().st_size:,} bytes) "
          f"-> {dst.relative_to(ROOT)}")
    return card


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--slug", help="which post's hero to build")
    ap.add_argument("--list", action="store_true", help="show known slugs")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the prompt and seed without calling the API")
    ap.add_argument("--model", choices=sorted(MODELS), default="klein4b",
                    help="klein4b (default; Apache-2.0, native aspect ratio) "
                         "or schnell (Apache-2.0, cropped from square). "
                         "klein9b is non-commercial and is refused.")
    ap.add_argument("--cards-only", action="store_true",
                    help="rebuild social cards from the heroes already in "
                         "static/, making no API call")
    args = ap.parse_args()
    if args.cards_only:
        # For heroes generated before the card existed, and for retuning the
        # card's own layout. Deriving from the committed PNG means this needs
        # no credentials and cannot return a different photograph.
        #
        # DRIVEN BY FRONT MATTER, not by a glob over static/. Globbing looked
        # equivalent and was not: a slug can have more than one hero on disk
        # (an older flat-navy one beside the generated photograph), both reduce
        # to the same slug, and the loop wrote the card twice with the LAST one
        # winning -- so the published card came from whichever file sorted
        # last. `hero:` names exactly the image the page renders, which is the
        # only one a preview should agree with.
        found = 0
        for md in sorted((ROOT / "content" / "posts").glob("*.md")):
            fm = md.read_text().split("---\n", 2)[1]
            hero = dict(
                (k.strip(), v.strip())
                for k, _, v in (ln.partition(":") for ln in fm.splitlines())
                if k.strip()).get("hero")
            if not hero:
                print(f"  skip {md.stem} (no hero in front matter)")
                continue
            src = STATIC / hero
            if not src.exists():
                raise SystemExit(f"  {md.name} names hero {hero!r}, "
                                 f"absent from static/")
            write_og_card(md.stem, src)
            found += 1
        print(f"  {found} card(s) rebuilt, no API call made")
        sys.exit(0)
    if args.list or not args.slug:
        print("known slugs:")
        for s, (p, seed, steps) in PROMPTS.items():
            print(f"  {s}\n      seed {seed}, {steps} steps: {p[:64]}...")
        sys.exit(0)
    build(args.slug, args.dry_run, args.model)

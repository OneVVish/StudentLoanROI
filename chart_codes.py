"""One place for the infographics' ?src= vocabulary: a two-letter CHANNEL, a
hyphen, and a short CHART CODE.

    pi-fmm    the picture itself (burned into the footer; a picture travels)
    sh-fmm    the gallery's Share button
    re-fmm    a link we post on Reddit, in a comment or a source note

Why this shape. Until 2026-08-22 every picture carried src=img and nothing
said which picture; then each carried its own slug, which said the picture and
not the channel, and the gallery's Share link carried nothing at all. A tag
that names both, in under ten characters, fits a URL a person will read.

The CODE is the initials of the manifest slug's words, digits dropped:
federal-money-map -> fmm, what-the-aid-formula-expects-ca -> wtafec. A manifest
may set `code:` to override. Unique across content/charts/, asserted by
all_codes(); a collision fails the build rather than filing two pictures under
one tag. Both this file and infra/build_site.py read it, and the chart scripts
import it, so the rule is written once.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHARTS = ROOT / "content" / "charts"

CHANNELS = {
    "pi": "burned into the picture itself; the picture travels across channels",
    "sh": "the gallery's own Share button",
    "re": "a link we post on Reddit",
    "ig": "a link we post on Instagram",
    "li": "a link we post on LinkedIn",
    "em": "a link we send by email",
}
CODE_RE = re.compile(r"^[a-z]{2,6}$")
TAG_RE = re.compile(r"^[a-z]{2}-[a-z]{2,6}$")


def initials(slug: str) -> str:
    return "".join(w[0] for w in slug.split("-") if w and not w.isdigit())


def _manifests() -> dict:
    """slug -> (code override or None, image stem), from content/charts/."""
    out = {}
    for path in sorted(CHARTS.glob("*.md")):
        text = path.read_text()
        code = re.search(r"^code:\s*(\S+)", text, re.M)
        image = re.search(r"^image:\s*(\S+)", text, re.M)
        out[path.stem] = (code.group(1) if code else None,
                          re.sub(r"\.png$", "", image.group(1)) if image else None)
    return out


def chart_code(name: str) -> str:
    """The code for a chart named by its manifest slug OR its image stem.

    Scripts pass the manifest slug; the community-college script passes the
    output stem it is about to write, which is the manifest's image stem. A
    name no manifest knows (an unpublished picture) gets the initials of the
    name itself, so a script can be run before its manifest exists.
    """
    for slug, (override, stem) in _manifests().items():
        if name in (slug, stem):
            code = override or initials(slug)
            break
    else:
        code = initials(name)
    if not CODE_RE.match(code):
        raise ValueError(f"{name!r} yields code {code!r}, not 2 to 6 lowercase letters")
    return code


def all_codes() -> dict:
    """slug -> code for every manifest, raising on a collision."""
    codes, seen = {}, {}
    for slug in _manifests():
        code = chart_code(slug)
        if code in seen:
            raise ValueError(f"chart code {code!r} is shared by {seen[code]!r} and "
                             f"{slug!r}; set `code:` in one manifest")
        seen[code] = slug
        codes[slug] = code
    return codes


def tag(channel: str, name: str) -> str:
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel {channel!r}; add it to CHANNELS")
    return f"{channel}-{chart_code(name)}"


if __name__ == "__main__":
    for slug, code in all_codes().items():
        print(f"{code:8} {slug}")

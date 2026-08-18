#!/usr/bin/env python3
"""Guard: a reel's spoken script must agree with the model it describes.

Run before rendering or posting any reel:

    marketing/.venv-reel/bin/python check_reel.py

WHAT IT EXISTS FOR. A reel says its findings OUT LOUD, in a synthesised voice,
over a chart that was drawn separately. Every other surface in this repo can be
re-read by whoever spots the error; a video is watched once, at speed, by
someone who cannot check it. So the failure this guards against is specific:
the model moves, the beats file keeps the old number, and the reel states it
confidently in a voice while the bars behind it show something else. Nothing
about that looks wrong on screen. `marketing/reel.py` refuses to render on it,
and this checks it without rendering anything.

It also enforces the prose rules that apply to a burned-in caption exactly as
they apply to a rendered string in app.py: no dash punctuation, American
English. A caption is visitor-facing text that happens to be typeset by a
renderer.

NEGATIVE CONTROLS RUN ON EVERY INVOCATION. Five deliberate breakages of a
synthetic beats file, each of which must fail the check that covers it: a claim
that disagrees with the model, an em dash in a caption, a focus name no adapter
offers, a `{fact}` nothing resolves, and a beat with no caption. A guard that
passes for the wrong reason is worse than none, and this one is checkable only
against fixtures because the real beats file is expected to pass.
"""
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / "marketing"))

import reel  # noqa: E402

# Rendered strings only: what a viewer sees or hears. `key`, `data`, `template`
# and the rest are identifiers, and are held to no prose rule.
VISIBLE_META = ("headline", "source", "end", "cta", "url", "footer")
VISIBLE_BEAT = ("caption", "panel", "spoken")

DASHES = ("—", "–")
BRITISH = ("modelled", "programme", "licence", "cancelled", "labelled", "colour",
           "amortised", "annualised", "capitalised", "standardised", "recognised",
           "neighbour", "towards", "maths", "modelling")

WPM = 150          # a slow, deliberate read; the engine measures the real thing
MAX_SECONDS = 90   # Instagram's ceiling for a reel


def visible_strings(meta: dict):
    """(where, text) for everything a viewer reads or hears."""
    for field in VISIBLE_META:
        value = meta.get(field)
        for text in (value if isinstance(value, list) else [value]):
            if text:
                yield f"front matter {field}", str(text)
    for beat in meta["beats"]:
        for field in VISIBLE_BEAT:
            if beat.get(field):
                yield f"beat {beat['key']} {field}", beat[field]
        for value, label in beat["panel_rows"]:
            yield f"beat {beat['key']} panel_row", f"{value} {label}"


def check_one(path: Path, data_cache: dict) -> list[str]:
    problems = []
    meta = reel.parse_reel(path)
    meta.setdefault("slug", path.stem)

    if meta["slug"] != path.stem:
        problems.append(f"slug {meta['slug']!r} is not the filename {path.stem!r}")

    template = meta.get("template", "ranked_bars")
    if template not in ("ranked_bars", "stat_cards"):
        problems.append(f"unknown template {template!r}")
    if template == "ranked_bars" and not meta.get("data"):
        problems.append("ranked_bars needs a `data:` adapter; without one there "
                        "are no rows and the beats would draw an empty chart")

    chart = meta.get("chart")
    if chart and not (REPO / chart).exists():
        problems.append(f"chart manifest {chart} does not exist")

    for i, beat in enumerate(meta["beats"]):
        if not beat["spoken"]:
            problems.append(f"beat {beat['key']}: nothing spoken")
        # The first beat's caption would print the headline's own last line
        # twice on one frame, so the engine suppresses it. Every other beat
        # carries one: a caption is the only text a viewer with sound off gets.
        if i and not beat.get("caption"):
            problems.append(f"beat {beat['key']}: no caption")

    for where, text in visible_strings(meta):
        for dash in DASHES:
            if dash in text:
                problems.append(f"{where}: dash punctuation in {text!r}")
        for word in BRITISH:
            if re.search(rf"\b{word}\b", text, re.I):
                problems.append(f"{where}: British spelling {word!r}")

    # The adapter is expensive (it execs app.py's section 1-2 prefix), so one
    # per data source however many reels name it.
    name = meta.get("data")
    if name:
        if name not in data_cache:
            try:
                data_cache[name] = reel.load_data(meta)
            except SystemExit as e:
                problems.append(str(e))
                return problems
        data = data_cache[name]

        problems += reel.check_claims(meta, data)

        for beat in meta["beats"]:
            if beat.get("focus") and beat["focus"] not in data["focus"]:
                problems.append(
                    f"beat {beat['key']}: focus {beat['focus']!r} is not one the "
                    f"adapter offers ({', '.join(sorted(data['focus']))})")
        for where, text in visible_strings(meta):
            for fact in re.findall(r"\{(\w+)\}", text):
                if fact not in data["facts"]:
                    problems.append(f"{where}: no fact {fact!r} to fill {{{fact}}}")

        if not any(beat["claims"] for beat in meta["beats"]):
            problems.append("no `claim:` anywhere: nothing ties the spoken "
                            "figures to the model, which is what this guards")

    words = sum(len(b["spoken"].split()) for b in meta["beats"])
    seconds = words / WPM * 60 + 0.32 * len(meta["beats"])
    if seconds > MAX_SECONDS:
        problems.append(f"about {seconds:.0f}s of script, over Instagram's "
                        f"{MAX_SECONDS}s ceiling")
    return problems


FIXTURE = """---
slug: fixture
template: ranked_bars
data: top_earning_careers
headline: ["A", "B"]
---

## title
claim: physicians=14
Fourteen are physicians.

## middle focus=bachelors
caption: two of twenty
panel: {med_debt} of debt
Only two need a bachelor's alone.

## end
caption: the end
That is the end of it.
"""

MUTATIONS = (
    ("a claim that disagrees with the model",
     lambda s: s.replace("claim: physicians=14", "claim: physicians=11")),
    ("an em dash in a caption",
     lambda s: s.replace("caption: two of twenty", "caption: two — of twenty")),
    ("a focus no adapter offers",
     lambda s: s.replace("focus=bachelors", "focus=dentists")),
    ("a fact nothing resolves",
     lambda s: s.replace("{med_debt}", "{med_dept}")),
    ("a beat with no caption",
     lambda s: s.replace("caption: two of twenty\n", "")),
)


def self_test(data_cache: dict) -> list[str]:
    """Every mutation must be caught. A guard nothing can fail is decoration."""
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "fixture.md"
        base.write_text(FIXTURE)
        if check_one(base, data_cache):
            failures.append("the clean fixture does not pass: "
                            f"{check_one(base, data_cache)}")
        for label, mutate in MUTATIONS:
            broken = Path(tmp) / "fixture.md"
            broken.write_text(mutate(FIXTURE))
            if not check_one(broken, data_cache):
                failures.append(f"NOT CAUGHT: {label}")
    return failures


def main() -> int:
    reels = sorted((REPO / "marketing" / "reels").glob("*.md"))
    if not reels:
        print("no beats files in marketing/reels/")
        return 1

    data_cache: dict = {}
    bad = 0
    for path in reels:
        problems = check_one(path, data_cache)
        if problems:
            bad += 1
            print(f"FAIL  {path.name}")
            for p in problems:
                print(f"        {p}")
        else:
            meta = reel.parse_reel(path)
            words = sum(len(b["spoken"].split()) for b in meta["beats"])
            print(f"ok    {path.name}  {len(meta['beats'])} beats, {words} words")

    failures = self_test(data_cache)
    for f in failures:
        print(f"SELF-TEST {f}")
    print(f"\n{len(reels) - bad}/{len(reels)} reels ok, "
          f"{len(MUTATIONS)} negative controls "
          f"{'all caught' if not failures else 'FAILED'}")
    return 1 if (bad or failures) else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Guard: every categorical colour pair the app renders must be readable.

    python3 check_chart_palette.py     (exit 1 on a violation)

WHY THIS EXISTS. The balance and payment charts stacked two bands --
"Federal (capped, income-driven)" against "PLUS & private (uncapped)" -- in
#4C78A8 and #B279A2. Those two separate by ΔE 5.0 under protanopia (OKLab
x100), below even the 6-8 band that is legal with secondary encoding. A
red-green-colourblind reader could not see the distinction that chart exists to
make. They were also below the chroma floor, so they read as grey to everyone,
and at ΔE 14.9 normal-vision they were under the 15 hard floor too.

Nothing caught it for months, and nothing could: it is not a crash, not a wrong
number, and not visible to anyone with typical colour vision looking at a chart
they already know how to read. It took running a validator by hand.

So the validator runs here instead. This file is deliberately thin -- it shells
out to the dataviz skill's validate_palette.js rather than reimplementing OKLab
and CVD simulation in Python, because a second implementation of a colour model
is a second thing to be wrong.

WHAT IT CANNOT DO. It checks colour, not layout: a passing palette can still be
rendered as an unreadable chart. And it needs node plus the skill's script; when
either is absent it SKIPS with a message rather than failing, because a missing
tool is not a broken palette -- see the exit code note in main().

Run after touching any chart colour.
"""
import json
import os
import shutil
import subprocess
import sys

APP = "app.py"

# Streamlit's two surfaces. The light one is the app's default; #0E1117 is
# Streamlit's dark theme background, which the charts inherit -- so a palette
# that only passes on white is only half checked.
LIGHT_SURFACE = "#ffffff"
DARK_SURFACE = "#0E1117"

# Where the skill's validator lives. An env var first so this keeps working if
# the skill is installed somewhere else; the bundled path is the fallback.
VALIDATOR_ENV = "DATAVIZ_VALIDATOR"
BUNDLED = ("/private/tmp/claude-501/bundled-skills/2.1.226/"
           "21cb79353991a36c8719e5ad43fda7cd/dataviz/scripts/validate_palette.js")


def find_validator():
    for candidate in (os.environ.get(VALIDATOR_ENV), BUNDLED):
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def app_namespace():
    """app.py's sections 1-2, for the colour constants. Same exec-prefix trick
    the other guards use -- see CLAUDE.md on why the section banners matter."""
    src = open(APP).read()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    prefix = src[:src.rindex("# " + "=" * 60, 0, cut)]
    ns = {"__name__": "palettecheck"}
    exec(compile(prefix, APP, "exec"), ns)
    return ns


def pairs_to_check(ns):
    """Every set of colours that appears TOGETHER in one chart.

    Adjacency is what the CVD check measures, so the sets have to be per-chart:
    two colours that never share a plot are not a pair, and lumping them
    together invents failures. Read from the app's own constants rather than
    retyped, so a colour change here cannot be a colour change nowhere.
    """
    light = [
        ("balance + payment stack (federal vs private)",
         [ns["STACK_COLORS"][0], ns["STACK_COLORS"][1]]),
        ("balance chart (principal vs unpaid interest)",
         [ns["SERIES_BLUE"], ns["SERIES_RED"]]),
        ("wage ridgeline (local vs national)",
         [ns["PANEL_WAGE_LOCAL_COLOR"], ns["PANEL_WAGE_NATIONAL_COLOR"]]),
    ]
    dark = [
        ("balance + payment stack (federal vs private)",
         [ns["SERIES_BLUE_DARK"], ns["SERIES_ORANGE_DARK"]]),
        ("balance chart (principal vs unpaid interest)",
         [ns["SERIES_BLUE_DARK"], ns["SERIES_RED_DARK"]]),
        ("wage ridgeline (local vs national)",
         [ns["SERIES_ORANGE_DARK"], ns["SERIES_BLUE_DARK"]]),
    ]
    return light, dark


# --- the one check the validator structurally cannot do -------------------
#
# validate_palette.js checks a CATEGORICAL palette, and the salary-flow bar's
# three tax segments are deliberately not one: they are three steps of a single
# grey, chosen to recede so that the two segments carrying the decision -- the
# loan payment and what is left -- hold the only real colour on the chart. Fed
# to the validator they fail the chroma floor by design, which is why this is
# written out longhand rather than delegated.
#
# What still has to hold, and did not at first: two of the three tax segments
# are usually too thin to hold a label, so the LEGEND SWATCH is the only place
# their identity lives. The first ramp stepped by 1.25 contrast and the three
# swatches were indistinguishable at 8pt -- three separate lines of legend
# pointing at what looked like one colour.
GREY_STEP_MIN = 1.30      # adjacent swatches, so the legend separates them
GREY_INK_MIN = 4.50       # the in-segment label, where a segment is wide enough
GREY_EDGE_MIN = 2.00      # the FIRST grey only: it is the bar's left end, and
                          # the only tax segment that borders the page itself


def _relative_luminance(hex_colour):
    hex_colour = hex_colour.lstrip("#")
    channels = []
    for i in (0, 2, 4):
        c = int(hex_colour[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928
                        else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a, b):
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def check_tax_greys(ns):
    """The salary-flow bar's grey ramp. Returns a list of problem strings."""
    greys = list(ns["SALARY_FLOW_TAX_GREYS"])
    ink = ns["SALARY_FLOW_TAX_INK"]
    problems = []
    for first, second in zip(greys, greys[1:]):
        ratio = contrast_ratio(first, second)
        if ratio < GREY_STEP_MIN:
            problems.append(
                f"  [salary-flow greys] {first} and {second} step by only "
                f"{ratio:.2f} (need {GREY_STEP_MIN})\n"
                f"      Their legend swatches are the only identity a tax "
                f"segment too thin to label has.")
    for grey in greys:
        ratio = contrast_ratio(ink, grey)
        if ratio < GREY_INK_MIN:
            problems.append(
                f"  [salary-flow greys] label ink {ink} on {grey} is "
                f"{ratio:.2f}:1 (need {GREY_INK_MIN})")
    edge = contrast_ratio(greys[0], LIGHT_SURFACE)
    if edge < GREY_EDGE_MIN:
        problems.append(
            f"  [salary-flow greys] the first grey {greys[0]} is {edge:.2f}:1 "
            f"against the page (need {GREY_EDGE_MIN})\n"
            f"      It is the bar's left end, so at that contrast the segment "
            f"fades into the background rather than starting somewhere.")
    return problems


def validate(validator, colours, mode, surface):
    """Run the skill's validator and return (ok, failing_check_lines)."""
    result = subprocess.run(
        ["node", validator, ",".join(colours), "--mode", mode,
         "--surface", surface],
        capture_output=True, text=True, timeout=60)
    out = result.stdout + result.stderr
    failures = [line.strip() for line in out.splitlines() if "[FAIL]" in line]
    return ("ALL CHECKS PASS" in out and not failures), failures, out


def main() -> int:
    validator = find_validator()
    if validator is None or shutil.which("node") is None:
        # A SKIP, not a failure. The palette is not broken because a tool is
        # missing, and a guard that fails on someone else's machine for that
        # reason gets ignored -- which is how a real failure then slips past.
        missing = "node" if validator else f"the validator (set ${VALIDATOR_ENV})"
        print(f"chart palette: SKIPPED -- {missing} not available. "
              f"The colours were not checked.")
        return 0

    ns = app_namespace()
    light, dark = pairs_to_check(ns)
    grey_problems = check_tax_greys(ns)
    problems, checked = list(grey_problems), 0
    for mode, surface, chart_pairs in (("light", LIGHT_SURFACE, light),
                                        ("dark", DARK_SURFACE, dark)):
        for label, colours in chart_pairs:
            checked += 1
            ok, failures, out = validate(validator, colours, mode, surface)
            if not ok:
                detail = "\n".join(f"      {line}" for line in failures) or \
                    f"      {out.strip().splitlines()[-1] if out.strip() else 'no output'}"
                problems.append(
                    f"  [{mode}] {label}\n"
                    f"      {', '.join(colours)} on {surface}\n{detail}")

    if problems:
        print(f"chart palette: {len(problems)} problem(s) "
              f"({checked} chart pairs checked, plus the grey ramp)\n")
        print("\n\n".join(problems))
        # Only when a PAIR failed. Printed unconditionally it explained a CVD
        # failure under a grey-ramp one, which is a different problem with a
        # different fix.
        if len(problems) > len(grey_problems):
            print("\n  A pair below the CVD floor is not a style opinion: the "
                  "two bands\n  it colours cannot be told apart by a "
                  "colourblind reader, which for a\n  stacked chart removes "
                  "the only thing the stack is for.")
        return 1
    print("salary-flow grey ramp OK -- the three tax swatches separate in the "
          "legend,\n  carry their label ink, and the bar's left end is visible "
          "against the page.")
    print(f"chart palette OK -- {checked} chart pairs pass every check "
          f"(lightness band, chroma floor, CVD separation, normal-vision "
          f"floor, contrast) on both the light and #0E1117 surfaces.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

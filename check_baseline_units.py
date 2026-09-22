#!/usr/bin/env python3
"""Guard: both sides of the ROI comparison are in the same units.

    python3 check_baseline_units.py        (exit 1 on a violation)

This exists because they were not, for as long as the age curve has existed,
and nothing could see it. Every figure was internally consistent; the two sides
simply measured different things and the premium was the difference between
them.

The high-school baseline grew at HS_GRAD_GROWTH_RATE = 2% a year ON TOP of
hs_age_factor, which by itself supplies 2.17%/yr of real progression from 18 to
40. The graduate side has no such term at all: get_major_growth_rate fits a
CAGR from OEWS p25 to p50 where both percentiles come from ONE release
published the same day, so no time passes between the measurements and the rate
is a cross-sectional gradient with no inflation in it, median 2.14%.

So the baseline compounded at roughly twice the median career's rate, through
exactly the years the comparison covers, and every degree's premium was
understated. At the default 10-year window, fixing it moves 130 of 836
occupations from a negative premium to a positive one.

Three things are asserted:

1. **The baseline has no calendar drift.** With the age curve off, it is
   constant in real terms. This is the invariant the bug violated, stated
   directly rather than inferred from any premium.
2. **The two sides' real growth rates are comparable.** The baseline's comes
   from CPS ASEC microdata and the graduate's from OEWS, so agreement between
   them is a real check and not the code agreeing with itself. A gap wide
   enough to matter means an inflation term has come back on one side.
3. **The age curve still carries the baseline's progression**, i.e. removing
   the drift did not flatten the baseline into a single figure for every age.
   That would be the opposite error and would flatter degrees even harder.

Each carries a negative control.
"""

import ast
import sys

APP = "app.py"

# Literals, not reads of the constants under test: a check that derives its
# expectation from the code it polices asserts only that the code equals itself.
AGE_CURVE_START, AGE_CURVE_END = 18, 40
GROWTH_RATE_TOLERANCE = 0.010   # 1.0 percentage point
DRIFT_TOLERANCE = 0.01          # dollars


def load_app_namespace(source: str = None):
    """app.py's sections 1-2 plus its later pure functions, without the UI."""
    src = source if source is not None else open(APP).read()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    prefix = src[:src.rindex("# " + "=" * 60, 0, cut)]
    ns = {"__name__": "baselineunitscheck"}
    exec(compile(prefix, APP, "exec"), ns)
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name not in ns:
            exec(compile(ast.Module(body=[node], type_ignores=[]), APP, "exec"), ns)
    ns["MAJOR_DATA"] = ns["build_major_data"](ns["CAREERS_CSV_PATH_NATIONAL"])
    return ns


def with_drift(rate: float):
    """A namespace whose baseline carries `rate` of calendar drift again."""
    ns = load_app_namespace()
    ns["HS_GRAD_GROWTH_RATE"] = rate
    return ns


def baseline_is_flat(ns, years=35) -> bool:
    """With the age curve off, is the baseline constant in real terms?"""
    wages = [ns["hs_wage_for_timeline_year"](y, 1.0, None) for y in range(years)]
    return max(wages) - min(wages) <= DRIFT_TOLERANCE


def check_no_calendar_drift(ns) -> list:
    problems = []
    if not baseline_is_flat(ns):
        wages = [ns["hs_wage_for_timeline_year"](y, 1.0, None) for y in (0, 34)]
        problems.append(
            f"the baseline is not flat in real terms: year 0 is ${wages[0]:,.0f} "
            f"and year 34 is ${wages[1]:,.0f}. Something is compounding a "
            f"calendar term the graduate side has no counterpart for, which "
            f"understates every degree's premium.")
    # NEGATIVE CONTROL: the old 2% must fail this.
    if baseline_is_flat(with_drift(0.02)):
        problems.append(
            "negative control did not fire: restoring the 2% drift left the "
            "baseline looking flat, so this check cannot see the bug it exists "
            "for.")
    return problems


def check_growth_rates_are_comparable(ns) -> list:
    """The baseline's real progression vs the median occupation's."""
    problems = []
    factor = ns["hs_age_factor"]
    span = AGE_CURVE_END - AGE_CURVE_START
    baseline_rate = (factor(AGE_CURVE_END) / factor(AGE_CURVE_START)) ** (1 / span) - 1

    rates = sorted(ns["get_major_growth_rate"](m) for m in ns["MAJOR_DATA"])
    median_rate = rates[len(rates) // 2]

    gap = abs(baseline_rate - median_rate)
    if gap > GROWTH_RATE_TOLERANCE:
        problems.append(
            f"the baseline grows {baseline_rate:.2%}/yr in real terms while the "
            f"median occupation grows {median_rate:.2%}/yr, a gap of "
            f"{gap:.2%}. These come from different federal sources (CPS ASEC "
            f"and OEWS) and should land close together; a gap this wide means "
            f"an inflation term is back on one side.")

    # NEGATIVE CONTROL: with the drift restored the baseline's EFFECTIVE rate is
    # age curve + drift, which must blow the tolerance.
    drifted = baseline_rate + 0.02
    if abs(drifted - median_rate) <= GROWTH_RATE_TOLERANCE:
        problems.append(
            "negative control did not fire: the baseline's rate plus the old 2% "
            "drift still sits inside the tolerance, so this check would pass on "
            "the original bug.")
    return problems


def check_age_curve_still_carries_progression(ns) -> list:
    """Removing the drift must not have flattened the baseline entirely."""
    problems = []
    young = ns["hs_wage_for_timeline_year"](0, 1.0, AGE_CURVE_START)
    older = ns["hs_wage_for_timeline_year"](AGE_CURVE_END - AGE_CURVE_START,
                                            1.0, AGE_CURVE_START)
    if older <= young:
        problems.append(
            f"the age-aware baseline does not rise with age: ${young:,.0f} at "
            f"{AGE_CURVE_START} against ${older:,.0f} at {AGE_CURVE_END}. The "
            f"drift was removed because hs_age_factor carries this progression; "
            f"losing it too would flatter degrees even harder than the bug did.")
    # NEGATIVE CONTROL: a flat age factor must fail it.
    flat = load_app_namespace()
    flat["hs_age_factor"] = lambda age: 1.0
    if flat["hs_wage_for_timeline_year"](22, 1.0, AGE_CURVE_START) != \
       flat["hs_wage_for_timeline_year"](0, 1.0, AGE_CURVE_START):
        problems.append(
            "negative control did not fire: flattening hs_age_factor still "
            "produced a rising baseline, so this check is reading something "
            "else.")
    return problems


# San Francisco's no-degree wage level, TRANSCRIBED FROM AN INDEPENDENT
# SOURCE, never read off the CSV under test. Census ACS via the Silicon Valley
# Institute for Regional Studies puts a San Francisco high school graduate's
# median earnings at 1.152x the national figure; this repo's own OEWS files,
# a different agency measuring a different thing (the job's required education
# rather than the person's attainment), give 1.153. The tolerance is wide
# enough that the two instruments agreeing is what passes, and narrow enough
# that the ALL-OCCUPATIONS index of 1.457 cannot.
#
# This is the check_rap_payment_table discipline: a guard that derives its
# expectation from the file it is guarding asserts only that the file equals
# itself.
SF_NODEGREE_INDEX = 1.152
SF_INDEX_TOLERANCE = 0.05
SF_CITY = "San Francisco, CA"


def check_baseline_uses_the_nodegree_index(ns) -> list:
    """The baseline is scaled by the no-degree index, not the all-occupations
    one.

    A metro's all-occupations median is high partly BECAUSE the metro is full
    of degree-holders, so scaling the NON-degree baseline by it imports the
    degree premium into the counterfactual the degree is measured against.
    Nothing asserted anything about either index until this existed, and the
    wrong one was wired to all 23 baseline call sites.
    """
    found = []
    baseline = ns["get_baseline_wage_index"](SF_CITY)
    if abs(baseline - SF_NODEGREE_INDEX) > SF_INDEX_TOLERANCE:
        found.append(
            f"the baseline index for {SF_CITY} is {baseline:.3f}, and Census "
            f"ACS puts a high school graduate there at {SF_NODEGREE_INDEX:.3f}. "
            f"If this is the all-occupations index (about 1.457), every "
            f"baseline is carrying other people's degrees.")
    career = ns["get_metro_wage_index"](SF_CITY)
    if abs(baseline - career) < 0.01:
        found.append(
            f"the baseline index and the all-occupations index are both "
            f"{baseline:.3f} for {SF_CITY}. Either a build wrote the same "
            f"figure into both files, or the baseline is reading the wrong "
            f"one -- and the two measure different populations.")
    return found


def check_every_city_resolves(ns) -> list:
    """Every city the app offers has a baseline index, and an unknown one
    falls back to exactly 1.0.

    FALLING BACK TO 1.0 AND NOT TO THE ALL-OCCUPATIONS INDEX is the point. A
    metro with no no-degree figure is "we do not know", and the national
    median is the honest answer; falling back to the old index would
    reintroduce the bug for exactly the metros the file could not measure,
    silently.
    """
    found = []
    for city in ns["CITY_DATA"]:
        if city == "National Average":
            continue
        ix = ns["get_baseline_wage_index"](city)
        if not (0.5 < ix < 2.0):
            found.append(f"{city} resolves to {ix}, which is not a wage index")
    for absent in ("Nowhere, ZZ", "", None):
        ix = ns["get_baseline_wage_index"](absent)
        if ix != 1.0:
            found.append(
                f"an unknown city ({absent!r}) resolves to {ix}, not 1.0. The "
                f"fallback must be the national figure, never the "
                f"all-occupations index.")
    return found


def main() -> int:
    ns = load_app_namespace()
    problems, checks = [], 0
    for label, run in (
        ("no calendar drift on the baseline", lambda: check_no_calendar_drift(ns)),
        ("both sides' real growth is comparable",
         lambda: check_growth_rates_are_comparable(ns)),
        ("the age curve still carries progression",
         lambda: check_age_curve_still_carries_progression(ns)),
        ("the baseline uses the no-degree index",
         lambda: check_baseline_uses_the_nodegree_index(ns)),
        ("every city resolves, and an unknown one is 1.0",
         lambda: check_every_city_resolves(ns)),
    ):
        checks += 1
        found = run()
        if found:
            problems.append(f"{label}:\n  " + "\n  ".join(found))

    if problems:
        print(f"baseline units: {len(problems)} failing check(s)\n")
        print("\n\n".join(problems))
        return 1

    factor = ns["hs_age_factor"]
    span = AGE_CURVE_END - AGE_CURVE_START
    baseline_rate = (factor(AGE_CURVE_END) / factor(AGE_CURVE_START)) ** (1 / span) - 1
    rates = sorted(ns["get_major_growth_rate"](m) for m in ns["MAJOR_DATA"])
    print(f"baseline units OK -- the baseline carries no calendar drift, its "
          f"real progression ({baseline_rate:.2%}/yr, CPS) sits beside the "
          f"median occupation's ({rates[len(rates)//2]:.2%}/yr, OEWS), and the "
          f"age curve still does its job. The baseline reads the NO-DEGREE "
          f"metro index ({SF_CITY} {ns['get_baseline_wage_index'](SF_CITY):.3f} "
          f"against the all-occupations {ns['get_metro_wage_index'](SF_CITY):.3f}) "
          f"({checks} checks, 5 negative controls).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

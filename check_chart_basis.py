#!/usr/bin/env python3
"""Guard: a published chart compares two things measured the same way.

    python3 check_chart_basis.py        (exit 1 on a violation)

WHY THIS EXISTS. Twice in one week a chart put two quantities side by side and
called the difference meaningful, when the two were not the same quantity. The
calculator was right both times; the picture drawn from it was not. That layer --
between app.py's model and a published PNG -- had no guard at all, and both
failures were found by readers.

  2026-08-21  Two years of community-college TUITION against four years of a
              COST OF ATTENDANCE, which counts housing and food. One path was
              charged rent and the other was not. It inflated the published
              ratio from about 12 times to 71.

  2026-08-22  Ten WORKING years on each side of a comparison between a two-year
              credential and a four-year one, so ages 20-30 were compared against
              ages 22-32 and finishing sooner counted for nothing. Raised by a
              reader on r/dataisbeautiful. Aligned to a common twelve years after
              high school the count goes from 8 of 44 to 22 of 44, so the chart
              erred in its own favour -- which is the right direction and the
              wrong thing to leave unstated.

WHAT MAKES THIS CHECKABLE. app.py already answers "what does year 0 mean" in one
place, `pre_earnings_years`, written after the same class of bug inside the
model. So the offset between two education levels is DERIVABLE from the model
rather than restated here, and this file can compare what the model implies
against what the chart says. That is the whole design: two independent things
compared, not one thing asserted against itself.

THE THREE CHECKS, and what each would have caught:

  1. An offset must be DISCLOSED. If the drawn population starts earning at a
     different age than the reference population, the deck has to say so. Catches
     the 2026-08-22 failure.

  2. The disclosure must be TRUE. The deck claims both sides get the same number
     of working years; this asserts the code still does that. Catches someone
     changing the arithmetic and leaving the sentence, which is the more likely
     next failure now that the sentence exists.

  3. A tuition claim must not be built from cost-of-attendance figures. Catches
     the 2026-08-21 failure.

Check 2 is the one worth understanding. Check 1 alone rewards adding a sentence,
and a guard that can be satisfied by typing prose is a guard that will be
satisfied by typing prose. Check 2 is what stops the sentence drifting away from
the code it describes.
"""
import ast
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHART = ROOT / "marketing" / "community_college_careers_chart.py"

# Cost-of-attendance quantities. A chart whose deck says "tuition and required
# fees" must not be built from any of these -- they include housing and food.
# Matched by NAME against the chart's source, because the failure is reading the
# wrong field, and a field is a name before it is a number.
COA_NAMES = (
    "COMMUNITY_COLLEGE_COA_DEFAULT",
    "in_state_coa",
    "out_of_state_coa",
    "find_school_coa",
    "load_coa_dataset",
)

# The claim the deck makes, in words the guard owns. HARDCODED rather than
# imported from the chart: a check that reads its expectation out of the thing it
# is checking asserts only that the thing equals itself, which this repo has now
# recorded three times. Editing the deck sentence means editing this tuple.
DISCLOSURE_TERMS = ("working years", "finishing sooner")


def load_chart():
    """The chart module, without running main() or importing matplotlib."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("cc_chart", CHART)
    module = importlib.util.module_from_spec(spec)
    sys.argv = ["check_chart_basis"]
    spec.loader.exec_module(module)
    return module


def start_offset(ns, education, title=None):
    """Years between age 18 and year 0 of this education level's earnings.

    Straight from app.py's own resolvers. This is the number the chart has to
    agree with, and deriving it here rather than restating it is what keeps the
    check honest -- teach the model a new programme length and this moves with it.
    """
    years = ns["program_years_for_education"](education, title)
    return ns["pre_earnings_years"](title or "", years)


def check_disclosure(module, ns) -> list:
    """An offset between the drawn and reference populations must be stated.

    UNKNOWN IS NOT ZERO, and the first version of this check got that wrong in a
    way that passed. `program_years_for_education` falls back to UNDERGRAD_YEARS
    for any level it has no defensible length for, so a certificate resolved to 4
    years -- identical to a bachelor's -- and the certificate chart was reported
    as "aligned" and waved through. It is the tier that needs the disclosure MOST:
    a certificate is SHORTER than an associate's, so its head start is larger.

    app.py already names these levels in MISMODELLED_EDUCATION_LEVELS, so the rule
    is the model's rather than one invented here: a level the model admits it
    models wrongly can never be treated as aligned.
    """
    problems = []
    reference = start_offset(ns, module.REFERENCE_EDUCATION)
    mismodelled = set(ns["MISMODELLED_EDUCATION_LEVELS"])
    for key, tier in sorted(module.TIERS.items()):
        drawn = start_offset(ns, tier["education"])
        unknown = tier["education"] in mismodelled
        deck = module.deck_text(
            tier, "CA", "California",
            two_years=2576, four_years=30408, multiple=11.8,
            systems=[("a CSU campus", 30408, 11.8)],
        ).lower()
        if drawn == reference and not unknown:
            continue
        missing = [t for t in DISCLOSURE_TERMS if t not in deck]
        if missing:
            why = (f"app.py lists {tier['education']!r} in "
                   f"MISMODELLED_EDUCATION_LEVELS, so its start time is UNKNOWN "
                   f"rather than aligned -- and a certificate is shorter than an "
                   f"associate's, so the head start is larger, not absent"
                   if unknown else
                   f"they start earning at age {18 + drawn} against a reference "
                   f"population starting at {18 + reference}, a "
                   f"{abs(reference - drawn)}-year head start the arithmetic does "
                   f"not count")
            problems.append(
                f"  The {key} chart compares populations that do not start at the "
                f"same moment: {why}. The deck has to say so and does not: missing "
                f"{missing}. Erring conservatively by accident and erring "
                f"conservatively on purpose look identical on the page, and are "
                f"not the same thing.")
    return problems


def check_claim_is_true(module, ns) -> list:
    """The deck says both sides get the same working years. They must."""
    problems = []
    if not module.EQUAL_WORKING_YEARS:
        return problems
    source = CHART.read_text()
    tree = ast.parse(source)
    # Every call that sums a career's pay must use the same horizon. The chart
    # sums `range(YEARS)` for the drawn rows and for the reference median; a
    # second literal anywhere is the arithmetic drifting away from the sentence.
    horizons = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "range" and len(node.args) == 1):
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Name):
            horizons.add(arg.id)
        elif isinstance(arg, ast.Constant):
            horizons.add(repr(arg.value))
    pay_horizons = {h for h in horizons if h == "YEARS" or h.isdigit()}
    if pay_horizons - {"YEARS"}:
        problems.append(
            f"  The deck claims both sides get the same number of working years, "
            f"and the chart now sums over {sorted(pay_horizons)} rather than YEARS "
            f"alone. Either the arithmetic changed and the sentence is now false, "
            f"or set EQUAL_WORKING_YEARS = False and reword the deck. A disclosure "
            f"that has stopped being true is worse than none, because it reads as "
            f"having been checked.")
    return problems


def check_tuition_not_coa(module) -> list:
    """A tuition claim must not be built from a cost of attendance."""
    problems = []
    source = CHART.read_text()
    tree = ast.parse(source)
    # Comments are stripped by the parser, so the docstrings that DISCUSS the
    # 2026-08-21 error cannot be read as the error. That flaw is on record twice
    # here already -- check_deploy_parity matched a commented-out exclusion, and
    # check_share_coverage read prose as compliance.
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    used |= {s.value for n in ast.walk(tree) if isinstance(n, ast.Subscript)
             for s in ast.walk(n.slice) if isinstance(s, ast.Constant)
             and isinstance(s.value, str)}
    hits = sorted(set(COA_NAMES) & used)
    if hits:
        problems.append(
            f"  This chart's deck says \"tuition and required fees on both "
            f"sides\", and it reads {hits}. Those include housing and food. "
            f"Comparing one against a tuition figure is what inflated the "
            f"published community-college ratio from about 12 times to 71.")
    return problems


def main() -> int:
    module = load_chart()
    ns = module.load_app()

    problems = (check_disclosure(module, ns)
                + check_claim_is_true(module, ns)
                + check_tuition_not_coa(module))

    if problems:
        print(f"chart basis: {len(problems)} problem(s)\n")
        for p in problems:
            print(p + "\n")
        print("  A chart is a claim about two quantities. This checks they are\n"
              "  the same quantity, measured from the same moment, and that what\n"
              "  the chart says about that is still true of what it does.")
        return 1

    reference = start_offset(ns, module.REFERENCE_EDUCATION)
    lines = []
    for key, tier in sorted(module.TIERS.items()):
        drawn = start_offset(ns, tier["education"])
        gap = reference - drawn
        unknown = tier["education"] in set(ns["MISMODELLED_EDUCATION_LEVELS"])
        lines.append(f"{key} " + ("start UNKNOWN, disclosed" if unknown
                                  else f"starts at {18 + drawn}, {gap}y before the "
                                       f"reference, disclosed" if gap
                                  else f"starts at {18 + drawn}, aligned"))
    print("chart basis OK: " + "; ".join(lines))
    print("  offsets derived from app.py's pre_earnings_years, disclosure "
          "asserted against the rendered deck")
    return 0


if __name__ == "__main__":
    sys.exit(main())

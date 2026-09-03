#!/usr/bin/env python3
"""Guard: the refinance comparison says the right thing, not merely something.

    python3 check_refinance_comparison.py     (exit 1 on any violation)

check_repayment_surfaces proves these functions RUN. This proves what they say,
and the division is the one this repo already draws between
check_repayment_invariants (the plan does not lose money) and
check_rap_payment_table (it charges the right amount).

IT EXISTS BECAUSE THE FIRST ATTEMPT AT GUARDING THIS PASSED ON BROKEN CODE.
The surfaces guard tested `not lines` for the PSLF case, so when the refusal
was deleted the function emitted the ordinary sentences, `lines` was non-empty,
and nothing fired. A check for silence cannot catch wrongness.

THE THREE THINGS THAT MUST HOLD, none of which is visible from the screen:

  - THE BAND IS TWO-SIDED where the curve really does cross twice. The federal
    cost curve is not monotonic: cheap at the bottom for forgiveness, cheap at
    the top for speed, dear in the middle. A bisection over the whole range
    returns None every time, which is how the error in the published guide was
    found. A one-sided answer here is not a simplification, it is false for
    every borrower above the upper crossing.
  - PSLF IS REFUSED, and refused in words that say why. Refinancing forfeits a
    tax-free discharge at 120 payments. A crossover figure would dignify a
    forfeiture as a close call.
  - WHAT IS GIVEN UP CARRIES NO AMOUNT. Pricing it needs the probability of a
    bad year, which nobody knows about one household. Same contract
    css_profile_divergences holds, and the same reason.
"""
import ast
import re
import sys

# The refinancing guide's own borrower, so the feature and the prose describe
# one person. Held as literals: a model change that moved these should fail
# here rather than quietly redefine what the guide is about.
FED = [{"balance": 50_000.0, "rate": 8.5}]
OFFER, YEARS = 6.0, 10
BAND_LOW, BAND_HIGH = 30_300.0, 94_800.0
TOL = 1_500.0

MONEY = re.compile(r"\$[\d,]+(?:\.\d+)?|\d+(?:\.\d+)?\s*(?:%|percent)")


def load():
    src = open("app.py").read()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    prefix = src[:src.rindex("# " + "=" * 60, 0, cut)]
    ns = {"__name__": "refinancecheck"}
    exec(compile(prefix, "app.py", "exec"), ns)
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name not in ns:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), ns)
    return ns


def check_band_is_two_sided(ns):
    """Both crossings found, and near where they actually are."""
    out = []
    band = ns["refinance_cost_band"](FED[0]["balance"], FED[0]["rate"], OFFER, YEARS)
    if not band:
        out.append("  no band at all on the guide's own borrower, where the "
                   "curve demonstrably crosses twice")
        return out
    low, high = band
    if high is None:
        out.append(f"  the band is one-sided ({low:,.0f} upward). The federal "
                   f"path becomes cheaper again near ${BAND_HIGH:,.0f}, and a "
                   f"one-sided answer is false for everyone above it. A "
                   f"bisection over the whole range does exactly this")
        return out
    for got, want, name in ((low, BAND_LOW, "lower"), (high, BAND_HIGH, "upper")):
        if abs(got - want) > TOL:
            out.append(f"  the {name} crossing is ${got:,.0f}, hand-computed "
                       f"${want:,.0f}")
    if low >= high:
        out.append(f"  the band is inverted: {low:,.0f} to {high:,.0f}")
    return out


def check_pslf_refused(ns):
    """Refused, and refused in words. NOT merely 'produces some prose'.

    The weak version of this check passed on code with the refusal deleted,
    because the ordinary sentences are also non-empty.
    """
    out = []
    a = ns["refinance_comparison"](FED, 53_000.0, OFFER, YEARS, pslf=True)
    if not a or a.get("refused") != "pslf":
        out.append("  PSLF does not refuse; it returns a comparison. "
                   "Refinancing forfeits a tax-free discharge at 120 payments, "
                   "which is not a trade with a crossover")
        return out
    text = " ".join(ns["refinance_sentences"](a))
    if "Public Service" not in text and "PSLF" not in text:
        out.append("  the refusal does not name PSLF as the reason")
    if MONEY.search(text.replace("120 payments", "")):
        out.append(f"  the refusal quotes a figure: {text!r}. A crossover "
                   f"number on a forfeiture dignifies it as a close call")
    # And the ordinary path must still answer, or 'refuses' means 'broken'.
    ok = ns["refinance_comparison"](FED, 53_000.0, OFFER, YEARS, pslf=False)
    if not ok or ok.get("refused"):
        out.append("  a non-PSLF borrower is refused too, so the refusal is "
                   "not discriminating")
    return out


def check_no_amount_in_privileges(ns):
    """Direction only. The css_profile_divergences contract."""
    out = []
    for forgivable in (True, False):
        items = ns["refinance_privileges_lost"](forgivable)
        if not items:
            out.append(f"  the privileges list is empty at forgivable={forgivable}")
            continue
        for head, why in items:
            if MONEY.search(head) or MONEY.search(why):
                out.append(f"  {head!r} carries a figure. Pricing what a "
                           f"refinance gives up needs the probability of a bad "
                           f"year, which nobody knows about one household")
    own = {h for h, _ in ns["refinance_privileges_lost"](True)}
    plus = {h for h, _ in ns["refinance_privileges_lost"](False)}
    if not plus < own:
        out.append("  Parent PLUS is not given a strictly smaller list; those "
                   "borrowers cannot reach RAP or IBR, so naming income-driven "
                   "payments and forgiveness among their losses names a loss "
                   "they have not got")
    return out


def check_offer_is_an_input(ns):
    """No modelled rate, ever. There is no dataset for one."""
    out = []
    if ns["refinance_comparison"](FED, 53_000.0, 0.0, YEARS) is not None:
        out.append("  a zero offer still produces a comparison; 0 means 'not "
                   "considering' and a substituted typical rate would be a "
                   "fabricated figure beside traceable ones")
    if ns["refinance_comparison"]([], 53_000.0, OFFER, YEARS) is not None:
        out.append("  no federal balance still produces a comparison")
    if ns["refinance_comparison"](FED, 0.0, OFFER, YEARS) is not None:
        out.append("  a zero income still produces a comparison")
    return out


def main() -> int:
    ns = load()
    checks = (
        ("the band is two-sided", check_band_is_two_sided, (ns,)),
        ("PSLF is refused, in words", check_pslf_refused, (ns,)),
        ("what is given up carries no amount", check_no_amount_in_privileges, (ns,)),
        ("the offered rate is an input", check_offer_is_an_input, (ns,)),
    )
    problems = []
    for name, fn, args in checks:
        found = fn(*args)
        if found:
            problems.append(f"{name}:\n" + "\n".join(found))
    if problems:
        print(f"refinance comparison: {len(problems)} failing check(s)\n")
        print("\n\n".join(problems))
        return 1
    print(f"refinance comparison OK -- {len(checks)} checks: the band is "
          f"two-sided, PSLF is refused in words, the privileges carry no "
          f"amount, and the offered rate is never modelled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

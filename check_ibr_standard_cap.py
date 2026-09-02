#!/usr/bin/env python3
"""Guard: an IBR payment is the LESSER of the percentage or the 10-year
Standard payment, and RAP has no such ceiling.

    python3 check_ibr_standard_cap.py     (exit 1 on any violation)

34 CFR 685.209(f)(2), for the 10% variant:

    "...the lesser of the amount the borrower would pay under the standard
     repayment plan under 685.208(b) based on a 10-year repayment period and
     the loan amount owed when the borrower entered repayment under the IBR
     plan, or 10 percent of the borrower's discretionary income."

(f)(3) says the same for the 15% variant that pre-July-2014 borrowers get.
PAYE shares (f)(2) -- it is one sentence covering "new borrowers under the IBR
plan and for all borrowers on the PAYE plan" -- which is why PAYE is not a
separate line anywhere here.

WHY THIS EXISTS. calculate_idr_repayment charged the percentage with no
ceiling from the day it was written until 2026-09-02. On a $30,000 balance at a
$150,000 income it billed $1,050/month against a real ceiling of $340.64, and
nothing failed: check_repayment_invariants proves the simulator does not lose
money, and a wrong-but-consistent payment balances its own books perfectly.
That is the same pairing check_rap_payment_table already documents -- one guard
proves the plan does not lose money, another proves it charges the right amount
to begin with -- and IBR had only the first half.

The defect had ALREADY been found and fixed once, in
brand/build_guide_plan_chart.py (#164/#165). That fix reached one guide figure
and never reached the simulator every visitor actually drives, so the repo
carried a corrected chart over an uncorrected model for months.

DIRECTION MATTERS. The bug OVERSTATED IBR, so it understated how much more RAP
asks of a high earner -- which is the finding the whole 2026-versus-legacy
comparison exists to carry.

Run after touching calculate_idr_repayment, calculate_standard_repayment,
simulate_rap_schedule, or any of the OLD_IBR_*/IDR_* constants.
"""
import ast
import sys

# The published rule, as a term in months. A LITERAL, never STANDARD_TERM_YEARS
# read back off app.py: a check that derives its expectation from the code
# under test asserts only that the code equals itself. Same discipline as
# check_rap_payment_table's transcribed chart and check_plan_switching's
# poverty-guideline anchors.
STANDARD_TERM_MONTHS = 120

# Hand-computed 10-year Standard payments, from the ordinary amortisation
# formula at the rates below. Anchors for the anchor: if `standard_payment`
# here ever disagrees with these, the guard's own arithmetic has drifted.
KNOWN_STANDARD = {
    (30_000.0, 6.5): 340.64,
    (65_000.0, 7.5): 771.56,
    (250_000.0, 8.0): 3033.19,
}

TOLERANCE = 0.01


def load():
    src = open("app.py").read()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    prefix = src[:src.rindex("# " + "=" * 60, 0, cut)]
    ns = {"__name__": "ibrcapcheck"}
    exec(compile(prefix, "app.py", "exec"), ns)
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name not in ns:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), ns)
    return ns


def standard_payment(principal, annual_rate_pct, months=STANDARD_TERM_MONTHS):
    """The 10-year Standard payment, written out longhand.

    Deliberately NOT a call to calculate_standard_repayment. That function is
    what calculate_idr_repayment now uses to build its ceiling, so reusing it
    would compare the app against itself and pass on a broken formula.
    """
    r = annual_rate_pct / 100.0 / 12.0
    if r == 0:
        return principal / months
    return principal * r / (1 - (1 + r) ** -months)


def uncapped_payment(income, allowance, rate):
    """What the percentage alone asks in month one, i.e. the pre-fix payment."""
    return max((income / 12.0) - (allowance / 12.0), 0.0) * rate


def month1(result):
    sched = result["schedule"]
    return float(sched["payment"].iloc[0])


# Balance, rate, income. The first three are the regime where the CAP binds
# (small balance, high income -- the ordinary bachelor's borrower who does
# well, which is exactly where the bug lived). The last three are where the
# PERCENTAGE binds, and they must come back untouched by the fix.
CAPPED = [
    (30_000.0, 6.5, 150_000.0),
    (30_000.0, 6.5, 100_000.0),
    (40_000.0, 6.5, 120_000.0),
]
UNCAPPED = [
    (250_000.0, 8.0, 130_000.0),
    (65_000.0, 7.5, 75_000.0),
    (30_000.0, 6.5, 40_000.0),
]


def check_known_anchors():
    """The guard's own formula against hand-computed figures."""
    out = []
    for (principal, rate), want in KNOWN_STANDARD.items():
        got = standard_payment(principal, rate)
        if abs(got - want) > TOLERANCE:
            out.append(f"  the GUARD's own standard_payment is wrong: "
                       f"${principal:,.0f} at {rate}% gives {got:,.2f}, "
                       f"hand-computed {want:,.2f}")
    return out


def check_cap_binds(ns):
    """Where the cap binds, the payment IS the cap -- and the uncapped figure
    must be visibly different, or the fixture proves nothing."""
    idr = ns["calculate_idr_repayment"]
    allowance = ns["IDR_LIVING_ADJUSTMENT"]
    out = []
    for balance, rate, income in CAPPED:
        cap = standard_payment(balance, rate)
        got = month1(idr(balance, rate, "x", annual_income=income))
        if abs(got - cap) > TOLERANCE:
            out.append(
                f"  ${balance:,.0f} at {rate}%, income ${income:,.0f}\n"
                f"    month-1 payment {got:,.2f} != the 10-year Standard "
                f"ceiling {cap:,.2f}\n"
                f"    34 CFR 685.209(f)(2): the payment is THE LESSER of the "
                f"percentage or the Standard payment")

        # NEGATIVE CONTROL, and it asserts its own anchor: the pre-fix figure
        # has to be far enough away that this fixture could actually catch a
        # revert. A fixture where the two coincide passes either way.
        was = uncapped_payment(income, allowance, ns["IDR_PAYMENT_RATE"])
        if abs(was - cap) <= 1.0:
            out.append(
                f"  ${balance:,.0f} at {rate}%, income ${income:,.0f}\n"
                f"    NEGATIVE CONTROL FAILED: the uncapped payment {was:,.2f} "
                f"is indistinguishable from the ceiling {cap:,.2f}, so this "
                f"fixture cannot catch the cap being removed. Resize it.")
    return out


def check_cap_does_not_bind(ns):
    """Where the percentage is the lesser figure, the fix must change nothing.

    This is the half that stops the cap being applied too eagerly -- a ceiling
    written as an unconditional assignment rather than a min() would pass every
    check above and silently overcharge every low earner.
    """
    idr = ns["calculate_idr_repayment"]
    allowance = ns["IDR_LIVING_ADJUSTMENT"]
    out = []
    for balance, rate, income in UNCAPPED:
        want = uncapped_payment(income, allowance, ns["IDR_PAYMENT_RATE"])
        cap = standard_payment(balance, rate)
        if want > cap:
            out.append(f"  fixture ${balance:,.0f}/{rate}%/${income:,.0f} is "
                       f"capped after all ({want:,.2f} > {cap:,.2f}); it belongs "
                       f"in CAPPED, not UNCAPPED")
            continue
        got = month1(idr(balance, rate, "x", annual_income=income))
        if abs(got - want) > TOLERANCE:
            out.append(
                f"  ${balance:,.0f} at {rate}%, income ${income:,.0f}\n"
                f"    month-1 payment {got:,.2f} != the percentage {want:,.2f}\n"
                f"    the ceiling must be a MINIMUM, not a replacement: a "
                f"borrower below it pays the percentage")
    return out


def check_old_ibr_capped(ns):
    """(f)(3) caps the 15% variant at the same 10-year Standard payment."""
    idr = ns["calculate_idr_repayment"]
    out = []
    balance, rate, income = 30_000.0, 6.5, 120_000.0
    cap = standard_payment(balance, rate)
    got = month1(idr(balance, rate, "x", annual_income=income,
                     payment_rate=ns["OLD_IBR_PAYMENT_RATE"],
                     max_term_years=ns["OLD_IBR_MAX_TERM_YEARS"]))
    if abs(got - cap) > TOLERANCE:
        out.append(f"  old IBR (15%/25y) month-1 {got:,.2f} != ceiling {cap:,.2f}\n"
                   f"    34 CFR 685.209(f)(3) caps the 15% variant too")
    was = uncapped_payment(income, ns["IDR_LIVING_ADJUSTMENT"],
                           ns["OLD_IBR_PAYMENT_RATE"])
    if abs(was - cap) <= 1.0:
        out.append(f"  NEGATIVE CONTROL FAILED: uncapped old-IBR {was:,.2f} is "
                   f"indistinguishable from the ceiling {cap:,.2f}")
    return out


def check_cap_is_the_entry_balance(ns):
    """The ceiling is figured on the balance at ENTRY and stays constant.

    Read off the RUNNING balance it would fall every month, so a capped
    borrower's bill would shrink as they repaid -- which no plan does and which
    would break the flat line the payment chart draws.
    """
    idr = ns["calculate_idr_repayment"]
    balance, rate, income = 30_000.0, 6.5, 150_000.0
    cap = standard_payment(balance, rate)
    sched = idr(balance, rate, "x", annual_income=income)["schedule"]
    # Every month before the final part-payment must sit exactly at the cap:
    # income only grows, so once capped the borrower stays capped.
    body = sched["payment"].iloc[:-1]
    strays = [(i, float(v)) for i, v in enumerate(body, 1)
              if abs(float(v) - cap) > TOLERANCE]
    if strays:
        m, v = strays[0]
        return [f"  the ceiling is not constant: month {m} pays {v:,.2f} "
                f"against an entry-balance ceiling of {cap:,.2f} "
                f"({len(strays)} months differ)\n"
                f"    it must be figured on the balance owed when the borrower "
                f"ENTERED the plan, not on the running balance"]
    return []


def check_rap_is_not_capped(ns):
    """RAP has NO ceiling, and that is most of why the two plans diverge.

    The temptation once the cap exists is to "fix" RAP the same way. RAP is a
    percentage of gross AGI with no shelter and no cap; capping it would delete
    the finding that RAP asks more of a high earner than IBR ever could.
    """
    rap = ns["simulate_rap_schedule"]
    balance, rate, income = 30_000.0, 6.5, 150_000.0
    cap = standard_payment(balance, rate)
    got = month1(rap(balance, rate, "x", 0, annual_income=income))
    if got <= cap + TOLERANCE:
        return [f"  RAP month-1 {got:,.2f} sits at or below the 10-year "
                f"Standard payment {cap:,.2f}\n"
                f"    RAP has no such ceiling -- 34 CFR 685.209(f) is an IBR "
                f"and PAYE provision. If a cap has been copied into "
                f"simulate_rap_schedule, remove it."]
    return []


def check_extras_are_not_capped(ns):
    """The ceiling bounds the STATUTORY payment, not a voluntary extra.

    Capping the sum would silently swallow the strategy module's whole pivot
    arm: a borrower redirecting a freed private payment at a capped federal
    balance would hand over the extra and see nothing happen.
    """
    idr = ns["calculate_idr_repayment"]
    balance, rate, income = 30_000.0, 6.5, 150_000.0
    cap = standard_payment(balance, rate)
    extra = 400.0
    got = month1(idr(balance, rate, "x", annual_income=income,
                     extra_payments=((1, extra),)))
    if abs(got - (cap + extra)) > TOLERANCE:
        return [f"  with a ${extra:,.0f} extra the month-1 payment is "
                f"{got:,.2f}, expected {cap + extra:,.2f}\n"
                f"    the ceiling applies to the statutory payment only; an "
                f"extra payment is voluntary and the regulation does not "
                f"bound it"]
    return []


def main() -> int:
    ns = load()
    checks = (
        ("the guard's own arithmetic", check_known_anchors, ()),
        ("the cap binds where it should", check_cap_binds, (ns,)),
        ("and not where it should not", check_cap_does_not_bind, (ns,)),
        ("old IBR (15%) is capped too", check_old_ibr_capped, (ns,)),
        ("the ceiling is the entry balance", check_cap_is_the_entry_balance, (ns,)),
        ("RAP is NOT capped", check_rap_is_not_capped, (ns,)),
        ("extra payments are NOT capped", check_extras_are_not_capped, (ns,)),
    )
    problems = []
    for name, fn, args in checks:
        found = fn(*args)
        if found:
            problems.append(f"{name}:\n" + "\n".join(found))
    if problems:
        print(f"IBR standard cap: {len(problems)} failing check(s)\n")
        print("\n\n".join(problems))
        return 1
    print(f"IBR standard cap OK -- {len(checks)} checks: the payment is the "
          f"lesser of the percentage or the 10-year Standard payment, on both "
          f"variants, figured on the entry balance; RAP and voluntary extras "
          f"are unbounded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Guard: the IDR plan-switching rules, which are asymmetric on purpose.

    python3 check_plan_switching.py     (exit 1 on any violation)

studentaid.gov states two rules that pull in opposite directions. Both are in
the "Repayment Assistance Plan" accordion, below the payment-amount chart:

    https://studentaid.gov/announcements-events/big-updates/definitions#rap


    "If you change from one IDR plan to another, your repayment period might
     also change. For example, if you're enrolled in the PAYE Plan, which has a
     20-year repayment period, and you subsequently enroll in RAP, which has a
     30-year repayment period, then your payments under the PAYE Plan will
     count toward discharge under RAP, but your repayment period would increase
     from 20 to 30 years."

    "...payments made under RAP won't count toward discharge under the IBR, ICR
     or PAYE plans, with the following exception: If the monthly payment amount
     while under RAP is greater than or equal to the 10-year Standard Repayment
     Plan monthly payment amount, then the month can count toward the IBR, ICR,
     and PAYE plans."

Prior payments carry INTO RAP freely. RAP payments carry back OUT only when
they clear the 10-year Standard bar. That asymmetry is the whole finding, and
it is easy to "simplify" away by making the credit symmetric -- which would
turn a near-irreversible switch into a reversible-looking one.

The sting is that the exception is written so it almost never fires for the
borrowers RAP is aimed at: the low payment that makes RAP attractive is exactly
what fails the test. Lower income => MORE irreversible. A guard that only
checked "some months count" would pass while the app told a $35k earner their
switch was reversible.

Run after touching compare_existing_loan_plans, either simulator's term
handling, or rap_months_counting_back.
"""
import ast
import sys

RAP_TERM_MONTHS = 360
IDR_TERM_MONTHS = 240
PSLF_MONTHS = 120


def load():
    src = open("app.py").read()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    prefix = src[:src.rindex("# " + "=" * 60, 0, cut)]
    ns = {"__name__": "switchcheck"}
    exec(compile(prefix, "app.py", "exec"), ns)
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name not in ns:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), ns)
    return ns


def row(rows, needle):
    for label, result, note in rows:
        if needle in label:
            return label, result, note
    return None, None, None


def main() -> int:
    ns = load()
    compare = ns["compare_existing_loan_plans"]
    counting = ns["rap_months_counting_back"]
    problems, checked = [], 0

    BAL, RATE = 60_000.0, 6.5

    # 1. Prior payments must SHORTEN the income-driven rows, month for month,
    #    whenever the term is what binds (a low earner never amortises, so
    #    forgiveness always arrives at the cap).
    low = 25_000.0
    base = compare(BAL, RATE, low, 0, True, 0.0, False, 0)
    for prior in (12, 60, 120, 240):
        got = compare(BAL, RATE, low, 0, True, 0.0, False, prior)
        for needle, term in (("RAP", RAP_TERM_MONTHS), ("IBR-style", IDR_TERM_MONTHS)):
            _, res, _ = row(got, needle)
            checked += 1
            # Floor of 1: a schedule cannot be empty. When the prior count has
            # already reached the term the balance is dischargeable now, and the
            # simulators emit a single zero-payment closing row so the balance
            # chart still has something to draw. The app says so in words --
            # see the >= term caption in render_existing_loan_comparison.
            want = max(term - prior, 1)
            months = len(res["schedule"])
            if months != want:
                problems.append(
                    f"  {needle} with {prior} prior payments ran {months} months, "
                    f"expected {want} ({term} - {prior}, floored at one closing row)")

    # 2. The FIXED-term plans must ignore prior payments entirely. They forgive
    #    nothing, so there is no clock to have made progress against, and the
    #    balance entered is already net of what has been paid. Crediting them
    #    would double-count.
    for needle in ("Standard (10-year)", "Extended", "Tiered"):
        _, a, _ = row(base, needle)
        _, b, _ = row(compare(BAL, RATE, low, 0, True, 0.0, False, 200), needle)
        checked += 1
        if abs(a["total_interest"] - b["total_interest"]) > 0.01 or \
           len(a["schedule"]) != len(b["schedule"]):
            problems.append(
                f"  {needle} changed when prior payments were supplied; fixed-term "
                f"plans have no forgiveness clock and must be unaffected")

    # 3. PSLF's 120-payment count must take the same subtraction.
    for prior in (0, 60, 119):
        got = compare(BAL, RATE, low, 0, True, 0.0, True, prior)
        _, res, _ = row(got, "RAP")
        checked += 1
        want = max(PSLF_MONTHS - prior, 1)
        if len(res["schedule"]) != want:
            problems.append(
                f"  PSLF RAP with {prior} prior payments ran "
                f"{len(res['schedule'])} months, expected {want}")

    # 4. THE ASYMMETRY. A low earner's RAP months must NOT count back, and a
    #    high earner's must. Getting this backwards, or making it uniform, is
    #    the failure this file exists for.
    std = ns["calculate_standard_repayment"](BAL, RATE, ns["STANDARD_TERM_YEARS"])
    std_monthly = std["monthly_payment"]
    for income, expect_any in ((20_000.0, False), (35_000.0, False), (120_000.0, True)):
        rap = ns["simulate_rap_schedule"](BAL, RATE, None, 0, annual_income=income)
        back = counting(rap, std_monthly)
        checked += 1
        if bool(back["counting"]) != expect_any:
            problems.append(
                f"  income ${income:,.0f}: {back['counting']} of {back['total']} RAP "
                f"months count back, expected {'some' if expect_any else 'NONE'} "
                f"(RAP payment vs ${std_monthly:,.2f} 10-year Standard)")

    # 5. Monotonic: a higher income can never make FEWER months count back.
    #    The test is a threshold on the payment, and the payment rises with
    #    income, so an inversion here means the comparison is upside down.
    prev = -1
    for income in (10_000.0, 30_000.0, 50_000.0, 80_000.0, 150_000.0, 300_000.0):
        rap = ns["simulate_rap_schedule"](BAL, RATE, None, 0, annual_income=income)
        share = counting(rap, std_monthly)["share"]
        checked += 1
        if share < prev - 1e-9:
            problems.append(
                f"  income ${income:,.0f} counts back {share:.0%} of months, LESS "
                f"than the next-lower income's {prev:.0%} -- the threshold is "
                f"inverted")
        prev = share

    # 6. The counts the on-screen warning is built from. Tested here rather
    #    than by string-matching the row note: the finding was moved OUT of the
    #    dataframe cell because Streamlit clips that column, so asserting on
    #    cell prose would pin it back to the place it renders invisibly.
    rows = compare(BAL, RATE, 25_000.0, 0, True, 0.0, False, 0)
    _, rap_row, _ = row(rows, "RAP")
    _, std_row, _ = row(rows, "Standard (10-year)")
    back = counting(rap_row, std_row["monthly_payment"])
    checked += 1
    if back["counting"] != 0 or back["total"] == 0:
        problems.append(
            f"  a $25,000 earner has {back['counting']} of {back['total']} RAP "
            f"months counting back; expected 0 of a non-empty schedule, which is "
            f"what triggers the one-way-door warning")

    if problems:
        print(f"plan switching: {len(problems)} violation(s) across {checked} checks\n")
        print("\n".join(problems))
        return 1
    print(f"plan switching OK -- {checked} checks against the published rules.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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


def monthly(result):
    """What the borrower hands over in month one.

    Mirrors the renderer: Standard-family rows carry a flat monthly_payment,
    while IDR and RAP carry a per-month `payment` column because theirs moves
    with income. After combine_repayment_results an income-driven row still has
    no monthly_payment -- the private tranche reaches it through the merged
    schedule's reconstructed payment column instead, which is exactly what has
    to be read here or the check tests a number the visitor never sees.
    """
    if "monthly_payment" in result:
        return float(result["monthly_payment"])
    sched = result["schedule"]
    return float(sched["payment"].iloc[0]) if "payment" in sched.columns else 0.0


def row(rows, needle):
    for label, result, note in rows:
        if needle in label:
            return label, result, note
    return None, None, None


def check_household(ns):
    """The poverty guideline, the 150% shelter, and the filing-status asymmetry.

    Anchors are LITERALS from the published 2026 HHS guidelines, never derived
    from the constants under test -- the flaw this repo already records against
    an early check_chart_axes and the residency guard. HHS states both of these
    outright: a family of four is $33,000 in the contiguous states and $41,250
    in Alaska.
    """
    problems = []
    pg = ns["poverty_guideline"]
    for size, region, expected in ((1, "contiguous", 15_960),
                                    (4, "contiguous", 33_000),
                                    (4, "alaska", 41_250),
                                    (1, "hawaii", 18_360)):
        got = pg(size, region)
        if abs(got - expected) > 0.5:
            problems.append(
                f"  poverty_guideline({size}, {region!r}) is ${got:,.0f}, "
                f"published is ${expected:,.0f}\n"
                "    this is a federal table, so a mismatch is a wrong payment "
                "for every household of that size")

    allowance = ns["idr_income_allowance"]
    # 150% of the guideline, and the fallback must be EXACTLY the old flat
    # figure: a visitor who never answers the household question has to see
    # what they saw before this feature existed.
    if abs(allowance(4) - 49_500) > 0.5:
        problems.append(f"  a household of four shelters ${allowance(4):,.0f}, "
                        f"not 150% of $33,000\n    IBR shelters 150%")
    if allowance(None) != float(ns["IDR_LIVING_ADJUSTMENT"]):
        problems.append(
            f"  with no household size the allowance is ${allowance(None):,.0f}, "
            f"not the flat ${ns['IDR_LIVING_ADJUSTMENT']:,}\n"
            "    unanswered must mean unchanged, or this feature silently "
            "moves every existing scenario")

    countable = ns["idr_countable_income"]
    joint = countable(65_000, 60_000, ns["FILING_JOINT"])
    separate = countable(65_000, 60_000, ns["FILING_SEPARATE"])
    if joint != 125_000:
        problems.append(f"  filing jointly counts ${joint:,.0f}, not both incomes")
    if separate != 65_000:
        problems.append(
            f"  filing separately counts ${separate:,.0f}, not the borrower's "
            "alone\n    that exclusion is the whole reason the control exists")

    # THE ASYMMETRY, which is the finding this feature exists to surface: a
    # spouse raises family size even when their income is excluded, so filing
    # separately after marrying shelters MORE than being single did.
    if not allowance(2) > allowance(1):
        problems.append(
            "  a two-person household does not shelter more than a one-person "
            "one\n    filing separately would then be strictly worse than "
            "staying single, which is the opposite of the rule")
    return problems


def main() -> int:
    ns = load()
    compare = ns["compare_existing_loan_plans"]
    counting = ns["rap_months_counting_back"]
    problems, checked = [], 0

    # Household and filing status, checked against the published HHS table.
    household = check_household(ns)
    problems.extend(household)
    checked += 9

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

    # 7. THE PRIVATE TRANCHE MUST NOT BE FORGIVEN, AND MUST NOT MOVE THE
    #    COUNT-BACK THRESHOLD. Both are the same class of bug: private money
    #    riding inside a federal pool. CLAUDE.md records the first costing
    #    $464,461 of imaginary forgiveness on a $193,033 loan.
    PRIV, PRIV_RATE = 40_000.0, 11.0
    base_rows = compare(BAL, RATE, low, 0, True, 0.0, False, 0)
    priv_rows = compare(BAL, RATE, low, 0, True, 0.0, False, 0, PRIV, PRIV_RATE)

    _, base_rap, _ = row(base_rows, "RAP")
    _, priv_rap, _ = row(priv_rows, "RAP")
    checked += 1
    if abs(priv_rap["forgiven_amount"] - base_rap["forgiven_amount"]) > 1.0:
        problems.append(
            f"  adding a ${PRIV:,.0f} PRIVATE balance changed RAP's forgiven "
            f"amount from ${base_rap['forgiven_amount']:,.0f} to "
            f"${priv_rap['forgiven_amount']:,.0f}. Private debt is outside the "
            f"federal system and can never be forgiven.")

    # The count-back verdict is a FEDERAL test, and this case is sized so that
    # getting it wrong VISIBLY flips the answer. A $40k private loan is not
    # enough -- at this income the combined payment still falls short of the
    # threshold, so a combined-basis implementation would pass by luck. Pick a
    # private balance whose payment ALONE clears the federal Standard payment,
    # then assert that it did, so this can never silently stop discriminating.
    BIG_PRIV = 150_000.0
    big_rows = compare(BAL, RATE, low, 0, True, 0.0, False, 0, BIG_PRIV, PRIV_RATE)
    _, big_rap, _ = row(big_rows, "RAP")
    big_priv_only = ns["calculate_standard_repayment"](BIG_PRIV, PRIV_RATE, 10)
    threshold = base_rap.get("countback", {}).get("threshold", 0.0)
    checked += 1
    if big_priv_only["monthly_payment"] <= threshold:
        problems.append(
            f"  the count-back fixture no longer discriminates: the private "
            f"payment ${big_priv_only['monthly_payment']:,.0f} does not exceed "
            f"the ${threshold:,.0f} threshold, so a combined-basis bug would "
            f"pass unnoticed. Raise BIG_PRIV.")
    checked += 1
    if big_rap.get("countback", {}).get("counting") != \
       base_rap.get("countback", {}).get("counting"):
        problems.append(
            f"  a ${BIG_PRIV:,.0f} private balance changed the RAP count-back "
            f"verdict from {base_rap.get('countback')} to "
            f"{big_rap.get('countback')}. The test compares the FEDERAL RAP "
            f"payment against the FEDERAL 10-year Standard payment; private "
            f"money belongs on neither side.")

    # Multi-loan federal: splitting one balance into two notes must not move
    # the count-back threshold -- it is the SUM of the per-loan 10-year
    # Standard payments, and at one shared rate that sum equals the single
    # loan's payment to the cent. A pooled-threshold implementation that
    # amortised the total at a wrongly-derived rate would drift here.
    split_rows = compare(0, 0, low, 0, True, 0.0, False, 0,
                         federal_loans=[{"balance": BAL * 0.6, "rate": RATE},
                                        {"balance": BAL * 0.4, "rate": RATE}])
    _, split_rap, _ = row(split_rows, "RAP")
    checked += 1
    split_threshold = split_rap.get("countback", {}).get("threshold", 0.0)
    if abs(split_threshold - threshold) > 0.51:
        problems.append(
            f"  splitting the ${BAL:,.0f} federal balance into two notes at the "
            f"same rate moved the count-back threshold from ${threshold:,.2f} "
            f"to ${split_threshold:,.2f}. The threshold is the sum of per-loan "
            f"10-year Standard payments and must not move.")

    # It must still show up in what the borrower PAYS, or it has been dropped
    # rather than separated.
    checked += 1
    if priv_rap["total_interest"] <= base_rap["total_interest"]:
        problems.append(
            f"  adding a ${PRIV:,.0f} private balance at {PRIV_RATE}% did not "
            f"increase total interest ({base_rap['total_interest']:,.0f} -> "
            f"{priv_rap['total_interest']:,.0f}); the tranche is being ignored, "
            f"not amortised alongside.")

    # And it is identical in every row, since no plan touches it.
    deltas = []
    for label in ("Standard (10-year)", "Extended", "Tiered", "RAP", "IBR-style"):
        _, a, _ = row(base_rows, label)
        _, b, _ = row(priv_rows, label)
        if a and b:
            deltas.append(round(monthly(b) - monthly(a), 2))
    checked += 1
    if len(set(deltas)) > 1:
        problems.append(
            f"  the private tranche added a DIFFERENT monthly amount to each "
            f"plan {deltas}. No federal plan alters a private loan, so the "
            f"increment must be identical in every row.")

    if problems:
        print(f"plan switching: {len(problems)} violation(s) across {checked} checks\n")
        print("\n".join(problems))
        return 1
    print(f"plan switching OK -- {checked} checks against the published rules.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

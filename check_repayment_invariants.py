#!/usr/bin/env python3
"""Guard: every repayment simulator must balance its own books.

    python3 check_repayment_invariants.py      (exit 1 on a violation)

This exists because of a bug nothing else could catch. `simulate_rap_schedule`
reported `total_interest: 0.0` for every scenario, on the reasoning that RAP
waives unpaid interest. It waives only the interest a payment does not cover --
so a borrower earning enough to cover it has nothing waived and pays all of it.
On a $190,000 loan that was $165,109 reported as $0.

Nothing flagged it. py_compile passed, the share-coverage guard passed,
analyze_model.py passed, all four PDFs rendered, and the page looked right --
because the figure was internally consistent with itself. It was simply false
about the world. A user reading the screen found it.

What that bug violated is an accounting identity, and identities are checkable:

    payments + forgiven + government_match  ==  principal + interest_paid

Read as money-in/money-out. The borrower's payments cover interest and some
principal; whatever principal they never covered was either forgiven or paid
down by the government's RAP match. Interest the payment never covered is
waived and appears on neither side -- it is a cost that simply never lands.

Reporting $0 interest breaks the right-hand side while payments on the left
stay real, so the identity fails by exactly the missing amount. It would have
failed loudly on the very first RAP scenario.

Run this after touching any simulator, the tranche split, or
combine_repayment_results.
"""
import ast
import sys

TOLERANCE = 1.0   # dollars; amortisation rounds per month


def load_app_namespace():
    """app.py's sections 1-2 plus its later pure functions, without the UI.

    Same exec-prefix trick analyze_model.py uses -- see CLAUDE.md on why the
    section banners are load-bearing.
    """
    src = open("app.py").read()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    prefix = src[:src.rindex("# " + "=" * 60, 0, cut)]
    ns = {"__name__": "invariantcheck"}
    exec(compile(prefix, "app.py", "exec"), ns)
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name not in ns:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), ns)
    return ns


def payments_made(result: dict) -> float:
    """What the borrower actually handed over across the life of the loan.

    Standard emits a flat monthly_payment and no per-month column; IDR and RAP
    emit `payment` because theirs moves with income. Both shapes are real, so
    read whichever is present rather than assuming one.
    """
    schedule = result["schedule"]
    if "payment" in schedule.columns:
        return float(schedule["payment"].sum())
    return float(result.get("monthly_payment", 0.0)) * len(schedule)


def check(label: str, principal: float, result: dict, rate: float = 6.5) -> list:
    problems = []
    interest = float(result.get("total_interest", 0.0) or 0.0)
    forgiven = float(result.get("forgiven_amount", 0.0) or 0.0)
    waived = float(result.get("waived_interest", 0.0) or 0.0)
    match = float(result.get("government_match", 0.0) or 0.0)
    paid = payments_made(result)

    lhs = paid + forgiven + match
    rhs = principal + interest
    if abs(lhs - rhs) > TOLERANCE:
        problems.append(
            f"  {label}\n"
            f"    payments {paid:,.2f} + forgiven {forgiven:,.2f} + gov match {match:,.2f}"
            f" = {lhs:,.2f}\n"
            f"    principal {principal:,.2f} + interest {interest:,.2f} = {rhs:,.2f}\n"
            f"    off by {lhs - rhs:,.2f}"
        )

    # The specific shape of the RAP bug: a real loan at a real rate, repaid
    # over real months, reporting no interest anywhere. Kept as its own check
    # because it names the failure in the terms it will recur in.
    months = len(result["schedule"])
    if rate > 0 and principal > 0 and months > 1 and interest <= 0 and waived <= 0:
        problems.append(
            f"  {label}\n"
            f"    reports NO interest at all -- neither paid nor waived -- on a "
            f"{principal:,.0f} loan repaid over {months} months. Interest cannot "
            f"be zero on both sides unless the rate is zero."
        )
    return problems


def main() -> int:
    ns = load_app_namespace()
    ns["MAJOR_DATA"] = dict(ns["CURATED_MAJOR_DATA"])
    # Two earners, because the plans behave oppositely across them: a high
    # earner covers the interest and has nothing waived or forgiven, a low one
    # has both. A single fixture would have missed the bug this file exists for.
    ns["MAJOR_DATA"]["HighEarner"] = {"starting_salary": 163500, "median_salary": 180000,
                                      "growth_rate": 0.03}
    ns["MAJOR_DATA"]["LowEarner"] = {"starting_salary": 32000, "median_salary": 36000,
                                     "growth_rate": 0.02}

    problems = []
    checked = 0
    for major in ("HighEarner", "LowEarner"):
        for principal in (5_000.0, 50_000.0, 190_000.0, 400_000.0):
            for rate in (0.0, 6.5, 9.07):
                cases = {
                    "Standard": lambda: ns["calculate_standard_repayment"](principal, rate),
                    "Tiered Standard": lambda: ns["calculate_standard_repayment"](
                        principal, rate, ns["calculate_tiered_standard_term"](principal)),
                    "IDR": lambda: ns["calculate_idr_repayment"](principal, rate, major),
                    "RAP": lambda: ns["simulate_rap_schedule"](principal, rate, major, 0),
                }
                for plan, build in cases.items():
                    result = build()
                    checked += 1
                    problems += check(
                        f"{plan}, {major}, ${principal:,.0f} @ {rate}%", principal, result, rate)

    # The combiner has to preserve the identity too: it is what presents a
    # forgivable federal tranche beside a non-forgivable private one as a
    # single bill, and summing the wrong fields there would be invisible.
    fed = ns["simulate_rap_schedule"](60_000.0, 6.5, "LowEarner", 0)
    priv = ns["calculate_standard_repayment"](130_000.0, 8.5)
    combined = ns["combine_repayment_results"](fed, priv)
    checked += 1
    problems += check("combined RAP federal + private", 190_000.0, combined)

    # The payment-override path (the repayment tool's "what you actually pay"
    # input) closes its books with a per-month capped final payment -- a
    # SEPARATE code path from the term-derived close-out, so it gets its own
    # cases. The below-required override must be ignored (identical books to
    # the plain call), and the zero-rate case exercises the loop with no
    # interest to hide behind.
    for principal, rate, override in ((68_900.0, 6.0, 1_600.0),
                                      (68_900.0, 6.0, 10_000.0),
                                      (50_000.0, 9.07, 500.0),
                                      (5_000.0, 0.0, 300.0)):
        result = ns["calculate_standard_repayment"](
            principal, rate, 9, monthly_payment_override=override)
        checked += 1
        problems += check(
            f"Standard override ${override:,.0f}/mo on ${principal:,.0f} @ {rate}%",
            principal, result, rate)

    # The strategy simulator's extra-payment hook: statutory + a stepped-up
    # extra must still balance the books. RAP is the load-bearing case -- a
    # larger payment covers more interest, so the WAIVER shrinks, and the
    # identity is what proves the subsidy-forfeiture arithmetic conserves
    # money rather than double-counting the covered interest.
    extras = ((1, 200.0), (49, 1_600.0))
    for label, principal, rate, result in (
        ("RAP + pivot extras, low earner", 60_000.0, 6.5,
         ns["simulate_rap_schedule"](60_000.0, 6.5, "LowEarner", 0,
                                     extra_payments=extras)),
        ("IDR + pivot extras, low earner", 60_000.0, 6.5,
         ns["calculate_idr_repayment"](60_000.0, 6.5, "LowEarner",
                                       extra_payments=extras)),
        ("RAP + extras, high earner", 190_000.0, 9.07,
         ns["simulate_rap_schedule"](190_000.0, 9.07, "HighEarner", 0,
                                     extra_payments=((13, 900.0),))),
    ):
        checked += 1
        problems += check(label, principal, result, rate)

    # Conservation cannot see a MIS-SIZED extra: applying double the extra
    # still balances its own books, since a larger payment is a larger
    # payment. So check the behaviour directly -- month one's payment must be
    # the statutory RAP payment plus EXACTLY the extra.
    rap_extra = ns["simulate_rap_schedule"](60_000.0, 6.5, "LowEarner", 0,
                                            extra_payments=((1, 200.0),))
    statutory = ns["calculate_rap_payment"](32_000.0)["monthly_payment"]
    got = float(rap_extra["schedule"]["payment"].iloc[0])
    checked += 1
    if abs(got - (statutory + 200.0)) > 0.01:
        problems.append(
            f"  RAP extra_payments mis-applied: month-1 payment ${got:,.2f} is "
            f"not the statutory ${statutory:,.2f} plus exactly the $200.00 extra.")

    # The fixed-plan avalanche: several notes, extra targeted highest-rate
    # first with retired notes' payments rolling forward. Three distinct
    # failure shapes, three distinct checks:
    #  - conservation (payments == principals + interest to the cent);
    #  - equivalence: on ONE note the avalanche must reproduce the plain
    #    override path, or the two simulators disagree about the same loan;
    #  - TARGETING: with distinct rates and an extra, the highest-rate note
    #    must die first. Conservation cannot see a reversed sort key --
    #    paying the wrong note first still balances the books perfectly.
    ava = ns["simulate_fixed_avalanche"](
        [{"balance": 46_300.0, "rate": 6.05}, {"balance": 22_600.0, "rate": 3.4},
         {"balance": 8_000.0, "rate": 9.5}],
        10, extra_payments=((1, 300.0), (25, 1_200.0)))
    checked += 1
    problems += check("fixed avalanche, 3 notes + stepped extras",
                      76_900.0, ava, 6.0)
    payoffs = ava["per_loan_payoff_months"]
    checked += 1
    if not (payoffs[2] < payoffs[0] < payoffs[1]):
        problems.append(
            f"  avalanche targeting broken: payoff months {payoffs} for rates "
            f"(6.05, 3.4, 9.5) -- the highest rate must die first and the "
            f"lowest last.")
    # The budget must never shrink: a retired note's payment ROLLS FORWARD
    # into the remaining notes. Conservation cannot see this one either --
    # paying less each month still balances its own books, it just takes
    # longer -- so check the payment column directly: every month except the
    # final partial one pays that month's full budget.
    base_budget = ava["monthly_payment"] - 300.0   # month-1 budget minus its extra
    sched = ava["schedule"]
    checked += 1
    for _, row_ in sched.iloc[:-1].iterrows():
        expected = base_budget + (300.0 if row_["month"] >= 1 else 0.0) \
                   + (1_200.0 if row_["month"] >= 25 else 0.0)
        if abs(row_["payment"] - expected) > 0.01:
            problems.append(
                f"  avalanche budget shrank: month {int(row_['month'])} paid "
                f"${row_['payment']:,.2f} against a ${expected:,.2f} budget -- "
                f"a retired note's payment must roll forward, not vanish.")
            break
    single_ava = ns["simulate_fixed_avalanche"](
        [{"balance": 68_900.0, "rate": 6.0}], 9, extra_payments=((1, 773.0),))
    single_override = ns["calculate_standard_repayment"](
        68_900.0, 6.0, 9,
        monthly_payment_override=single_ava["monthly_payment"])
    checked += 1
    if (abs(single_ava["total_interest"] - single_override["total_interest"]) > 2.0
            or abs(single_ava["payoff_years"] - single_override["payoff_years"]) > 0.1):
        problems.append(
            f"  single-note avalanche disagrees with the override path: "
            f"interest {single_ava['total_interest']:,.2f} vs "
            f"{single_override['total_interest']:,.2f}, payoff "
            f"{single_ava['payoff_years']:.2f} vs "
            f"{single_override['payoff_years']:.2f} -- same loan, same money, "
            f"two answers.")

    # Multi-loan grids: two federal notes on a fixed plan chained with two
    # private loans (one paying an override) -- four schedules combined twice
    # over, which is the repayment tool's whole-bill shape. The books must
    # balance against the SUMMED principals: a chain that dropped or
    # double-counted a tranche is invisible in any single result.
    multi_rows = ns["compare_existing_loan_plans"](
        0, 0, 32_000.0,
        federal_loans=[{"balance": 40_000.0, "rate": 6.5},
                       {"balance": 20_000.0, "rate": 3.0}],
        private_loans=[{"balance": 30_000.0, "rate": 8.0, "term": 10, "actual": 0},
                       {"balance": 15_000.0, "rate": 9.5, "term": 7,
                        "actual": 400.0}])
    _std_multi = next(r for label, r, _ in multi_rows
                      if label.startswith("Standard"))
    checked += 1
    problems += check("multi-loan Standard combined (2 federal + 2 private)",
                      105_000.0, _std_multi)
    # The RAP row separately: its federal side is the POOLED simulation, so a
    # pool that silently drops a note stays internally consistent (the
    # wrong-but-consistent class again) and only the identity against the
    # SUMMED principals can see it. The Standard row cannot stand in -- it is
    # built from the per-loan chain, a different code path.
    _rap_multi = next((r for label, r, _ in multi_rows if "RAP" in label), None)
    if _rap_multi is None:
        problems.append("  multi-loan RAP row missing from compare_existing_loan_plans")
    else:
        checked += 1
        problems += check("multi-loan RAP combined (pooled federal + 2 private)",
                          105_000.0, _rap_multi)

    if problems:
        print(f"repayment invariants: {len(problems)} violation(s) across {checked} cases\n")
        print("\n\n".join(problems))
        return 1
    print(f"repayment invariants OK -- {checked} cases balance to within ${TOLERANCE:.2f}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

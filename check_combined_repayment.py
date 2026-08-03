#!/usr/bin/env python3
"""Guard: one income-driven payment covers ALL federal loans, and a combined
bill must say so.

    python3 check_combined_repayment.py      (exit 1 on a violation)

This exists because of a bug check_repayment_invariants.py structurally could
not see. A returning student's existing federal balance was simulated
SEPARATELY from the new loan under RAP/IDR and the two payments were summed --
but an income-driven payment is a function of income alone, and one payment
covers every federal loan a borrower has. The combined bill read $500/month
against a statutory $250 on a $60k+$30k pair, and the payoff came out roughly
twice as fast as reality. Each simulator balanced its own books perfectly, so
the 97-case invariant grid stayed green: the error lived in how two correct
results were put together, which no per-simulator identity can reach.

Three things are asserted, each aimed at a distinct way this can regress:

1. **The payment.** A scenario with an existing federal balance under RAP/IDR
   must charge the same month-1 payment as one pooled simulation over
   new-forgivable + existing -- and that equals the new-loan-only payment,
   because the payment never depended on the balance in the first place.
   Catches a return to per-loan summing.
2. **The pool.** The combined result must balance the money-in/money-out
   identity against the POOLED principal (new forgivable + existing), not the
   new loan alone. Catches the quieter regression where the existing balance
   silently drops out of the pool -- which check 1 cannot see, since the
   payment would not move.
3. **The step-down.** A combined fixed-payment schedule must carry a per-month
   `payment` column, and that column must fall to the surviving loan's own
   payment once the shorter loan clears. The scalar `monthly_payment` on a
   combined result is the flat sum for the whole term and must NOT be what a
   per-month reader sees. Catches _merge_balance_schedules going back to
   emitting the column only when one side already had one.

Fixed plans are checked the other way around: Standard and Tiered amortise
per-loan, so there the combined payment MUST be the sum.

Each income-driven case carries its own negative control: the guard computes
the per-loan summed payment the bug used to produce and asserts it differs
from the correct figure by more than the tolerance -- a fixture where the two
coincide would let the check pass while discriminating nothing.

Run this after touching compute_scenario_results' existing-debt handling,
combine_repayment_results, or _merge_balance_schedules.
"""
import ast
import sys

TOLERANCE = 0.51   # dollars/month; simulators round per month

RATE_NEW = 6.5
RATE_EXISTING = 5.0


def load_app_namespace():
    """app.py's sections 1-2 plus its later pure functions, without the UI.

    Same exec-prefix trick analyze_model.py uses -- see CLAUDE.md on why the
    section banners are load-bearing.
    """
    src = open("app.py").read()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    prefix = src[:src.rindex("# " + "=" * 60, 0, cut)]
    ns = {"__name__": "combinedcheck"}
    exec(compile(prefix, "app.py", "exec"), ns)
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name not in ns:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), ns)
    return ns


def month1_payment(result: dict) -> float:
    """The first month's payment, from the per-month column when the schedule
    carries one and the flat scalar when it doesn't -- the same two shapes
    every other consumer reads."""
    schedule = result["schedule"]
    if "payment" in schedule.columns:
        row = schedule[schedule["month"] == 1]
        if not row.empty:
            return float(row.iloc[0]["payment"])
    return float(result.get("monthly_payment", 0.0))


def payments_made(result: dict) -> float:
    schedule = result["schedule"]
    if "payment" in schedule.columns:
        return float(schedule["payment"].sum())
    return float(result.get("monthly_payment", 0.0)) * len(schedule)


def check_income_driven(ns, strategy: str, major: str, new_loan: float,
                         existing: float) -> list:
    """Checks 1 and 2 for one plan/earner/balance combination."""
    problems = []
    label = f"{strategy}, {major}, new ${new_loan:,.0f} + existing ${existing:,.0f}"

    scen = ns["compute_scenario_results"](
        major, new_loan, RATE_NEW, strategy,
        existing_debt=existing, existing_debt_rate=RATE_EXISTING)
    combined = scen["combined_repayment"]
    new_only = scen["repayment_result"]

    # The reference: one simulation over the pooled balance at the weighted
    # rate, exactly what the statute describes.
    pool = new_loan + existing
    pool_rate = (new_loan * RATE_NEW + existing * RATE_EXISTING) / pool
    if strategy == ns["RAP_STRATEGY_LABEL"]:
        pooled = ns["simulate_rap_schedule"](pool, pool_rate, major, 0)
    else:
        pooled = ns["calculate_idr_repayment"](pool, pool_rate, major)

    got = month1_payment(combined)
    want = month1_payment(pooled)
    if abs(got - want) > TOLERANCE:
        problems.append(
            f"  {label}\n"
            f"    combined month-1 payment {got:,.2f} != pooled statutory {want:,.2f}\n"
            f"    an income-driven payment is set by income alone and covers "
            f"every federal loan; two balances must not mean two payments")

    # Negative control: the summed figure the bug produced must be
    # DISTINGUISHABLE from the statutory one, or this fixture proves nothing.
    if strategy == ns["RAP_STRATEGY_LABEL"]:
        existing_alone = ns["simulate_rap_schedule"](existing, RATE_EXISTING, major, 0)
    else:
        existing_alone = ns["calculate_idr_repayment"](existing, RATE_EXISTING, major)
    wrong = month1_payment(new_only) + month1_payment(existing_alone)
    if abs(wrong - want) <= TOLERANCE:
        problems.append(
            f"  {label}\n"
            f"    NEGATIVE CONTROL FAILED: the per-loan summed payment "
            f"{wrong:,.2f} is indistinguishable from the statutory {want:,.2f}, "
            f"so this fixture cannot catch a return to summing. Resize it.")

    # Check 2: money in == money out against the POOLED principal. A combined
    # result that quietly dropped the existing balance from the pool keeps the
    # same payment (income-driven), so only the accounting can catch it.
    paid = payments_made(combined)
    forgiven = float(combined.get("forgiven_amount", 0.0) or 0.0)
    match = float(combined.get("government_match", 0.0) or 0.0)
    interest = float(combined.get("total_interest", 0.0) or 0.0)
    lhs = paid + forgiven + match
    rhs = pool + interest
    if abs(lhs - rhs) > 1.0:
        problems.append(
            f"  {label}\n"
            f"    combined books do not balance against the pooled principal:\n"
            f"    payments {paid:,.2f} + forgiven {forgiven:,.2f} + match {match:,.2f}"
            f" = {lhs:,.2f}\n"
            f"    pool {pool:,.2f} + interest {interest:,.2f} = {rhs:,.2f}\n"
            f"    off by {lhs - rhs:,.2f} -- is the existing balance in the pool?")

    if combined["payoff_years"] + 1e-9 < new_only["payoff_years"]:
        problems.append(
            f"  {label}\n"
            f"    combined payoff {combined['payoff_years']:.2f}y is EARLIER than "
            f"the new loan alone ({new_only['payoff_years']:.2f}y) -- a bigger "
            f"pool on the same payment cannot clear sooner")
    return problems


def check_fixed_sum(ns, strategy: str, major: str, new_loan: float,
                     existing: float) -> list:
    """Fixed plans amortise per-loan, so there the payments MUST add."""
    problems = []
    label = f"{strategy}, {major}, new ${new_loan:,.0f} + existing ${existing:,.0f}"
    scen = ns["compute_scenario_results"](
        major, new_loan, RATE_NEW, strategy,
        existing_debt=existing, existing_debt_rate=RATE_EXISTING)
    combined = scen["combined_repayment"]
    expected = (scen["repayment_result"]["monthly_payment"]
                + scen["existing_debt_result"]["monthly_payment"])
    got = float(combined.get("monthly_payment", 0.0))
    if abs(got - expected) > TOLERANCE:
        problems.append(
            f"  {label}\n"
            f"    combined flat payment {got:,.2f} != summed per-loan {expected:,.2f}"
            f" -- fixed plans repay each loan on its own schedule")
    return problems


def check_step_down(ns) -> list:
    """Check 3: the merged per-month column steps down; the scalar does not.

    Fixture: a small new Tiered loan (10-year term) beside a large existing
    Tiered balance (25-year term). Deliberately different terms -- identical
    terms would never step and the check would pass while testing nothing,
    so the fixture asserts its own terms differ first.
    """
    problems = []
    scen = ns["compute_scenario_results"](
        "HighEarner", 20_000, RATE_NEW, ns["TIERED_STANDARD_STRATEGY_LABEL"],
        existing_debt=120_000, existing_debt_rate=RATE_NEW)
    combined = scen["combined_repayment"]
    t_new = scen["repayment_result"]["payoff_years"]
    t_existing = scen["existing_debt_result"]["payoff_years"]
    if abs(t_new - t_existing) < 1.0:
        problems.append(
            "  step-down fixture: the two loans' terms nearly coincide "
            f"({t_new:.1f}y vs {t_existing:.1f}y), so no step exists to test. "
            "Resize the fixture.")
        return problems

    schedule = combined["schedule"]
    if "payment" not in schedule.columns:
        problems.append(
            "  combined fixed-payment schedule carries no per-month `payment` "
            "column -- _merge_balance_schedules must emit it unconditionally, "
            "or every per-month reader falls back to the summed flat scalar.")
        return problems

    early = month1_payment(combined)
    late_month = int(combined["payoff_years"] * 12) - 6
    late_row = schedule[schedule["month"] == late_month]
    late = float(late_row.iloc[0]["payment"]) if not late_row.empty else None
    surviving = scen["existing_debt_result"]["monthly_payment"]
    if late is None:
        problems.append(f"  no schedule row at month {late_month} to read the late payment from")
    elif abs(late - surviving) > TOLERANCE:
        problems.append(
            f"  after the {t_new:.0f}-year loan clears, the combined column reads "
            f"{late:,.2f}/mo where only the surviving loan's {surviving:,.2f}/mo is owed"
            " -- the step-down is missing")
    # The scalar is the flat sum by design; the column must differ from it late
    # in the term, or a reader could not tell the two apart.
    flat = float(combined.get("monthly_payment", 0.0))
    if late is not None and abs(flat - late) <= TOLERANCE:
        problems.append(
            "  NEGATIVE CONTROL FAILED: the late-term column payment equals the "
            "flat combined scalar, so this fixture cannot tell column from "
            "scalar. Resize it.")
    if early <= surviving:
        problems.append(
            f"  month-1 combined payment {early:,.2f} is not above the surviving "
            f"loan's own {surviving:,.2f} -- both loans should be paying early on")
    return problems


def main() -> int:
    ns = load_app_namespace()
    ns["MAJOR_DATA"] = dict(ns["CURATED_MAJOR_DATA"])
    # Two earners for the same reason check_repayment_invariants uses two: a
    # low earner's RAP payment sits below the interest (forgiveness path), a
    # high earner's clears the balance (payoff path). The pooling rule must
    # hold on both.
    ns["MAJOR_DATA"]["HighEarner"] = {"starting_salary": 163500, "median_salary": 180000,
                                      "growth_rate": 0.03}
    ns["MAJOR_DATA"]["LowEarner"] = {"starting_salary": 32000, "median_salary": 36000,
                                     "growth_rate": 0.02}

    problems = []
    checked = 0
    income_driven = (ns["RAP_STRATEGY_LABEL"], "Income-Driven Repayment (IDR)")
    fixed = ("Standard 10-Year", ns["TIERED_STANDARD_STRATEGY_LABEL"])
    for major in ("HighEarner", "LowEarner"):
        for new_loan, existing in ((60_000.0, 30_000.0), (20_000.0, 120_000.0)):
            for strategy in income_driven:
                checked += 1
                problems += check_income_driven(ns, strategy, major, new_loan, existing)
            for strategy in fixed:
                checked += 1
                problems += check_fixed_sum(ns, strategy, major, new_loan, existing)

    checked += 1
    problems += check_step_down(ns)

    # No existing debt: combined must BE the new-loan result, so the display
    # path needs no conditional and first-time students cannot be affected by
    # any of the pooling arithmetic above.
    scen = ns["compute_scenario_results"]("HighEarner", 30_000, RATE_NEW,
                                          ns["RAP_STRATEGY_LABEL"])
    checked += 1
    if abs(month1_payment(scen["combined_repayment"])
           - month1_payment(scen["repayment_result"])) > 1e-9:
        problems.append("  with no existing debt, combined != new-loan-only result")

    if problems:
        print(f"combined repayment: {len(problems)} violation(s) across {checked} cases\n")
        print("\n\n".join(problems))
        return 1
    print(f"combined repayment OK -- {checked} cases: one income-driven payment, "
          f"pooled books balance, fixed plans sum, merged schedules step down.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Guard: calculate_rap_payment must reproduce studentaid.gov's published chart.

    python3 check_rap_payment_table.py     (exit 1 on any mismatch)

RAP's payment schedule is published as a table, not a formula, so it can be
checked exactly rather than approximately. Source:

    https://studentaid.gov/announcements-events/big-updates/definitions
    -> "Repayment Assistance Plan" -> "Monthly Payment Amount Based on Your
       AGI Range" (page last updated 2026-07-06)

Two real bugs motivated this, both found from a single user report of "$0
monthly payment below $10,000 AGI":

  1. THE $10 FLOOR WAS APPLIED AS $0. The chart's footnote reads: "You can
     subtract $50 from the monthly payment amount for each dependent you claim
     on your federal income tax return, but your monthly payment amount can
     never be less than $10." That floor is the bottom of the whole schedule,
     not a special case for the lowest band -- it is also why the $0-$10,000
     row reads a flat $10.00, since 1% of $10,000 is $8.33/month. The code
     floored the dependent deduction at $0, so any borrower with dependents
     and a low income was shown $0.

  2. EXACT MULTIPLES OF $10,000 LANDED ONE BAND TOO HIGH. The brackets are
     $X,001-$Y,000, so $50,000 is the TOP of 4%, not the bottom of 5%.
     `agi // 10000` gave 5, charging $208.33 against a published bracket
     maximum of $166.67 -- 25% high, on the roundest number a visitor types.
     Every boundary in the chart is such a multiple, so this was wrong at all
     ten of them and right everywhere in between, which is why nothing caught
     it.

Both are boundary bugs, and both are invisible to the accounting identity in
check_repayment_invariants.py: a wrong-but-consistent payment balances its own
books perfectly. That checker proves the simulator does not lose money; this
one proves it charges the right amount to begin with.
"""
import ast
import sys

# The published chart, verbatim. (low, high, min_monthly, max_monthly) with
# `high = None` for the open-ended top row. Dollars, no dependents.
PUBLISHED = [
    (0,       10_000,   10.00,  10.00),
    (10_001,  20_000,   10.00,  16.67),
    (20_001,  30_000,   33.34,  50.00),
    (30_001,  40_000,   75.00, 100.00),
    (40_001,  50_000,  133.34, 166.67),
    (50_001,  60_000,  208.34, 250.00),
    (60_001,  70_000,  300.01, 350.00),
    (70_001,  80_000,  408.34, 466.67),
    (80_001,  90_000,  533.34, 600.00),
    (90_001, 100_000,  675.01, 750.00),
    (100_001,   None,  833.34,   None),   # "More than $100,000: At least $833.33"
]

CENT = 0.011   # the chart is rounded to the cent; allow one cent of slack


def load_calculate_rap_payment():
    """app.py's function, without running its UI. Same exec-prefix trick
    analyze_model.py and check_repayment_invariants.py use."""
    src = open("app.py").read()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    prefix = src[:src.rindex("# " + "=" * 60, 0, cut)]
    ns = {"__name__": "raptablecheck"}
    exec(compile(prefix, "app.py", "exec"), ns)
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name not in ns:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), ns)
    return ns["calculate_rap_payment"]


def main() -> int:
    pay = load_calculate_rap_payment()
    problems, checked = [], 0

    # Both EDGES of every bracket. The edges are the whole point: an off-by-one
    # in the band is correct everywhere except exactly here.
    for low, high, want_low, want_high in PUBLISHED:
        for agi, want in ((low, want_low), (high, want_high)):
            if agi is None or want is None:
                continue
            got = pay(float(agi), 0)["monthly_payment"]
            checked += 1
            if abs(got - want) > CENT:
                problems.append(
                    f"  AGI ${agi:,} -> ${got:,.2f}/month, chart says ${want:,.2f}"
                    f"  (bracket ${low:,}-{'∞' if high is None else f'${high:,}'})")

    # Every payment must sit inside its bracket's published range, not merely
    # hit the endpoints.
    for low, high, want_low, want_high in PUBLISHED:
        if high is None:
            continue
        for agi in range(low, high + 1, max(1, (high - low) // 20)):
            got = pay(float(agi), 0)["monthly_payment"]
            checked += 1
            if not (want_low - CENT <= got <= want_high + CENT):
                problems.append(
                    f"  AGI ${agi:,} -> ${got:,.2f}/month, outside the chart's "
                    f"${want_low:,.2f}-${want_high:,.2f} range for its bracket")

    # The $10 floor, which is what the user reported. Every one of these
    # returned $0.00 before the fix.
    for agi in (0, 1, 5_000, 9_999, 10_000, 12_000, 20_000, 30_000):
        for dependents in (1, 2, 3, 10):
            got = pay(float(agi), dependents)["monthly_payment"]
            checked += 1
            if got < RAP_FLOOR - CENT:
                problems.append(
                    f"  AGI ${agi:,} with {dependents} dependent(s) -> "
                    f"${got:,.2f}/month, below the $10.00 floor the chart's "
                    f"footnote sets")

    # The deduction must still WORK where there is room above the floor --
    # a floor bug is easy to "fix" by ignoring dependents entirely.
    base = pay(60_000.0, 0)["monthly_payment"]
    for dependents in (1, 2, 3):
        got = pay(60_000.0, dependents)["monthly_payment"]
        checked += 1
        if abs(got - (base - 50 * dependents)) > CENT:
            problems.append(
                f"  AGI $60,000 with {dependents} dependent(s) -> ${got:,.2f}, "
                f"expected ${base - 50 * dependents:,.2f} "
                f"($50/dependent off ${base:,.2f})")

    if problems:
        print(f"RAP payment table: {len(problems)} mismatch(es) across {checked} checks\n")
        print("\n".join(problems))
        return 1
    print(f"RAP payment table OK -- {checked} checks against the published chart.")
    return 0


RAP_FLOOR = 10.00

if __name__ == "__main__":
    sys.exit(main())

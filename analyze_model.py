"""
Standalone break-even analysis over the calculator's own ROI model.

This is NOT part of the deployed Streamlit app, and it needs no survey data
at all -- it's a simulation study over the model app.py already implements,
for the companion research paper. Where analyze_survey.py asks "what did
visitors do?", this asks "what does the model itself say?", across every
occupation in the BLS dataset rather than the one a visitor happened to pick.

The question: at what debt level does a given major stop beating a debt-free
high school graduate over the app's 10-year window? Every major has such a
crossover point, and for some it's below zero -- the degree never wins on
this model's terms no matter how it's financed.

Usage:
    python3 analyze_model.py                 # national BLS wages
    python3 analyze_model.py --state CA      # California BLS wages
    python3 analyze_model.py -o breakeven.csv

Outputs a per-major CSV plus a printed summary. Needs no Supabase
credentials and makes no network calls -- unlike analyze_survey.py, this
runs entirely off the committed CSVs.
"""

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

APP_PATH = Path(__file__).parent / "app.py"
DEFAULT_OUTPUT = Path(__file__).parent / "analysis_output" / "breakeven_by_major.csv"

# Bisection bounds for the break-even search, in dollars of undergrad loan.
# The upper bound is deliberately far past any realistic undergrad debt: a
# major whose break-even sits above it is reported as ">$1M" rather than
# clipped to the bound, so an implausible number never silently reads as a
# real one.
SEARCH_MAX_LOAN = 1_000_000.0
SEARCH_TOLERANCE = 50.0  # dollars; well below the precision the model claims

STRATEGIES = ["Standard 10-Year", "Income-Driven Repayment"]


def load_model_layer(state: str = None) -> dict:
    """app.py's constants + helper functions (its sections 1 and 2), without
    executing any of its Streamlit UI.

    app.py is a single-file Streamlit script: importing it would run the
    whole page top to bottom (st.set_page_config, every widget, the Supabase
    pageview insert) and then exit. But its sections 1-2 are pure -- data
    constants and math functions, no UI calls -- so this execs exactly that
    prefix, stopping at the "# 3. PAGE CONFIG & SESSION STATE" banner.

    Why not just reimplement the formulas here? Because that's the same trap
    the PDF chart builders already fell into (see CLAUDE.md's "Two parallel
    chart implementations"): a second copy of the math drifts from the first,
    and then the paper's numbers quietly stop matching the app's. This way
    there is exactly one implementation of calculate_roi, and a change to it
    shows up here automatically.

    MAJOR_DATA is assembled the same way app.py's section 4 does it
    (load_bls_careers + CURATED_MAJOR_DATA), because there it depends on a
    sidebar widget -- the Career Salary Data source -- that doesn't exist
    outside a real UI session.
    """
    src = APP_PATH.read_text()
    marker = re.search(r"^# =+\n# 3\. PAGE CONFIG & SESSION STATE", src, re.M)
    if not marker:
        sys.exit("Could not find app.py's section 3 banner -- has the file been reorganized? "
                 "This script execs everything above it as the model layer.")

    ns = {"__name__": "app_model_layer", "__file__": str(APP_PATH)}
    exec(compile(src[:marker.start()], str(APP_PATH), "exec"), ns)

    csv_path = ns["CAREERS_CSV_PATH_CA"] if state == "CA" else ns["CAREERS_CSV_PATH_NATIONAL"]
    ns["MAJOR_DATA"] = {**ns["load_bls_careers"](csv_path), **ns["CURATED_MAJOR_DATA"]}
    return ns


def earnings_premium_at(ns: dict, major: str, loan: float, rate: float, strategy: str) -> float:
    """The major's COL-adjusted 10-year advantage over a high school grad at
    this debt level. Positive = the degree is ahead; the break-even is where
    this crosses zero."""
    result = ns["compute_scenario_results"](major, loan, rate, strategy)
    return result["roi_result"]["earnings_premium"]


def find_breakeven_loan(ns: dict, major: str, rate: float, strategy: str) -> dict:
    """The undergrad loan amount at which `major` stops beating a debt-free
    high school graduate, found by bisection.

    Bisection is valid here because earnings_premium is monotonically
    decreasing in loan size -- more debt means strictly more repaid inside
    the 10-year window, and nothing else in the model depends on the loan.
    It is not a closed-form solve because the repayment engines aren't
    invertible: IDR/RAP payments are income-driven with forgiveness, so
    "payments made in 10 years" is a simulation, not a formula.

    Returns a dict rather than a float because the interesting cases aren't
    numbers: a major can lose at zero debt (never_breaks_even), or still be
    ahead at an absurd debt level (breakeven_above_search_max).
    """
    premium_at_zero = earnings_premium_at(ns, major, 0.0, rate, strategy)
    if premium_at_zero <= 0:
        # Loses to a high school grad even fully funded. For most majors this
        # means low wages; for Medicine/Law it can also mean the professional
        # -school debt get_effective_principal adds on top of the slider, plus
        # the years of delayed earnings those tracks model.
        return {"status": "never_breaks_even", "breakeven_loan": None,
                "premium_at_zero_debt": premium_at_zero}

    premium_at_max = earnings_premium_at(ns, major, SEARCH_MAX_LOAN, rate, strategy)
    if premium_at_max > 0:
        return {"status": "breakeven_above_search_max", "breakeven_loan": None,
                "premium_at_zero_debt": premium_at_zero}

    lo, hi = 0.0, SEARCH_MAX_LOAN
    while hi - lo > SEARCH_TOLERANCE:
        mid = (lo + hi) / 2
        if earnings_premium_at(ns, major, mid, rate, strategy) > 0:
            lo = mid
        else:
            hi = mid
    return {"status": "ok", "breakeven_loan": round((lo + hi) / 2, 2),
            "premium_at_zero_debt": premium_at_zero}


def build_breakeven_table(ns: dict, rate: float) -> pd.DataFrame:
    rows = []
    majors = sorted(ns["MAJOR_DATA"].keys())
    for i, major in enumerate(majors, 1):
        if i % 100 == 0:
            print(f"  ...{i}/{len(majors)} majors", file=sys.stderr)
        row = {
            "major": major,
            "starting_salary": ns["MAJOR_DATA"][major]["starting_salary"],
            "additional_training_debt": ns["MAJOR_DATA"][major].get("additional_training_debt", 0),
        }
        for strategy in STRATEGIES:
            result = find_breakeven_loan(ns, major, rate, strategy)
            key = "standard" if strategy == STRATEGIES[0] else "idr"
            row[f"breakeven_loan_{key}"] = result["breakeven_loan"]
            row[f"status_{key}"] = result["status"]
            if key == "standard":
                row["premium_at_zero_debt"] = round(result["premium_at_zero_debt"], 2)
        rows.append(row)
    return pd.DataFrame(rows)


def print_summary(df: pd.DataFrame, ns: dict, rate: float):
    line = "=" * 78
    print(f"\n{line}\nBREAK-EVEN DEBT BY MAJOR\n"
          f"At what undergrad loan does each major stop beating a debt-free HS grad?\n{line}")
    print(f"Model: {ns['ROI_WINDOW_YEARS']}-year window, HS baseline "
          f"${ns['HS_GRAD_SALARY']:,}/yr growing {ns['HS_GRAD_GROWTH_RATE']*100:.0f}%/yr, "
          f"loan rate {rate}%.")
    print(f"Occupations analyzed: {len(df)}")

    never = df[df["status_standard"] == "never_breaks_even"]
    print(f"\nNever break even, even at $0 debt: {len(never)} "
          f"({len(never)/len(df)*100:.1f}% of occupations)")
    print("  -- these lose to a high school graduate on this model's terms no matter\n"
          "     how they're financed, so no amount of scholarship changes the verdict.")
    if not never.empty:
        worst = never.nsmallest(10, "premium_at_zero_debt")[["major", "starting_salary", "premium_at_zero_debt"]]
        print("\n  Worst 10 (10-yr premium at zero debt):")
        print(worst.to_string(index=False))

    solved = df[df["status_standard"] == "ok"]
    if not solved.empty:
        print(f"\n{line}\nBREAK-EVEN DEBT, STANDARD 10-YEAR (n={len(solved)})\n{line}")
        b = solved["breakeven_loan_standard"]
        print(f"median ${b.median():,.0f} | 25th ${b.quantile(.25):,.0f} | "
              f"75th ${b.quantile(.75):,.0f} | min ${b.min():,.0f} | max ${b.max():,.0f}")
        print("\n  Lowest 10 break-even debt (least debt-tolerant majors that still win):")
        print(solved.nsmallest(10, "breakeven_loan_standard")[
            ["major", "starting_salary", "breakeven_loan_standard"]].to_string(index=False))

    print(f"\n{line}\nSTANDARD vs INCOME-DRIVEN REPAYMENT\n"
          f"Does repayment plan choice change the verdict, and by how much?\n{line}")
    both = df[(df["status_standard"] == "ok") & (df["status_idr"] == "ok")].copy()
    if both.empty:
        print("  (no majors where both strategies have a finite break-even)")
    else:
        both["idr_advantage"] = both["breakeven_loan_idr"] - both["breakeven_loan_standard"]
        print(f"Majors with a finite break-even under both plans: {len(both)}")
        print(f"IDR raises break-even debt by a median of ${both['idr_advantage'].median():,.0f} "
              f"(min ${both['idr_advantage'].min():,.0f}, max ${both['idr_advantage'].max():,.0f})")
        print("\n  IDR's break-even is higher because forgiveness caps what's actually repaid\n"
              "  inside the 10-year ROI window -- the balance may survive past it, but the\n"
              "  payments this window counts don't. Read it as 'debt tolerated within 10\n"
              "  years', not 'debt made harmless'.")

    idr_unbounded = df[(df["status_standard"] == "ok") & (df["status_idr"] == "breakeven_above_search_max")]
    if not idr_unbounded.empty:
        print(f"\n  {len(idr_unbounded)} majors break even under Standard but stay ahead past "
              f"${SEARCH_MAX_LOAN:,.0f} under IDR --")
        print("  i.e. within this window IDR's payment cap makes debt size nearly irrelevant.")

    print(f"\n{line}\nWHY CITY ISN'T A VARIABLE HERE\n{line}")
    print("adjust_for_cost_of_living divides both sides of the comparison by the same\n"
          "index, so it scales the earnings premium without moving the point where that\n"
          "premium hits zero. Cost of living changes how much a major wins by, never\n"
          "whether it wins -- so break-even debt is identical in every city, and sweeping\n"
          "all 23 would produce 23 identical tables. That's a property of the model worth\n"
          "stating in the paper, not a gap in this analysis.")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state", choices=["CA"], default=None,
                        help="Use the California BLS wage dataset instead of national.")
    parser.add_argument("--rate", type=float, default=6.5,
                        help="Loan interest rate %% to model (default: 6.5).")
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"CSV output path (default: {DEFAULT_OUTPUT}).")
    args = parser.parse_args()

    print(f"Loading model layer from {APP_PATH.name} "
          f"({'California' if args.state == 'CA' else 'national'} wages)...", file=sys.stderr)
    ns = load_model_layer(args.state)
    print(f"  {len(ns['MAJOR_DATA'])} occupations loaded.", file=sys.stderr)

    df = build_breakeven_table(ns, args.rate)
    print_summary(df, ns, args.rate)

    args.output.parent.mkdir(exist_ok=True)
    df.to_csv(args.output, index=False)
    print(f"\nPer-major table written to {args.output}")


if __name__ == "__main__":
    main()

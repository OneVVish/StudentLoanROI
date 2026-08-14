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
STRATEGIES = ["Standard 10-Year", "Income-Driven Repayment"]

# BLS's "typical education needed for entry" (see add_education_field.py),
# grouped by whether the app's core assumption -- 4 years of undergrad,
# financed by the loan slider, then straight into this occupation's wage --
# is actually true for that occupation.
ADVANCED_EDUCATION_LEVELS = {"Master's degree", "Doctoral or professional degree"}

EDU_BACHELORS = "bachelors"            # the assumption holds; break-even is meaningful
EDU_SUB_BACHELORS = "sub_bachelors"    # charged a degree the job doesn't require
EDU_ADVANCED_UNMODELED = "advanced_unmodeled"  # extra school ignored -> ROI overstated
EDU_ADVANCED_MODELED = "advanced_modeled"      # the 3 hand-curated tracks


def classify_education(info: dict, sub_bachelors_levels: set) -> str:
    """Which of the four modelling regimes an occupation falls into.

    The app charges every occupation the same thing: 4 years of undergrad on
    the loan slider, then immediate entry at that occupation's wage. That is
    true for a bachelor's-level occupation, and false in opposite directions
    for the other two groups -- so pooling them produces a break-even
    distribution that answers no question at all.
    """
    level = info.get("typical_education") or ""
    models_training = bool(info.get("unpaid_training_years")
                           or info.get("stipend_training_years")
                           or info.get("additional_training_debt"))
    if models_training:
        return EDU_ADVANCED_MODELED
    if level in ADVANCED_EDUCATION_LEVELS:
        return EDU_ADVANCED_UNMODELED
    if level in sub_bachelors_levels:
        return EDU_SUB_BACHELORS
    return EDU_BACHELORS


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

    MAJOR_DATA is assembled by calling app.py's own build_major_data, since
    section 4 builds it from a sidebar widget -- the Career Salary Data
    source -- that doesn't exist outside a real UI session. Calling the
    shared function rather than re-implementing the merge keeps the wage +
    curated + training-overlay precedence identical to the app's.
    """
    src = APP_PATH.read_text()
    marker = re.search(r"^# =+\n# 3\. PAGE CONFIG & SESSION STATE", src, re.M)
    if not marker:
        sys.exit("Could not find app.py's section 3 banner -- has the file been reorganized? "
                 "This script execs everything above it as the model layer.")

    ns = {"__name__": "app_model_layer", "__file__": str(APP_PATH)}
    exec(compile(src[:marker.start()], str(APP_PATH), "exec"), ns)

    csv_path = ns["CAREERS_CSV_PATH_CA"] if state == "CA" else ns["CAREERS_CSV_PATH_NATIONAL"]
    ns["MAJOR_DATA"] = ns["build_major_data"](csv_path)
    return ns


def find_breakeven_loan(ns: dict, major: str, rate: float, strategy: str) -> dict:
    """The undergrad loan at which `major` stops beating a debt-free high
    school graduate.

    Delegates to app.py's own find_breakeven_loan rather than reimplementing
    the bisection here. This script used to own that logic, which meant the
    paper's break-even and the app's break-even were two functions free to
    disagree -- the same trap CLAUDE.md flags for the chart twins. The app
    now shows a break-even to every visitor, so they must be the same number.

    Normalises the status names to the ones this script's table already uses.
    """
    # Pass the SAME baseline the app uses, or this table stops being the app's
    # number -- which is the one thing the docstring above says it must be.
    #
    # Two model changes broke the old bare call. The high-school baseline now
    # follows an age-earnings curve rather than a flat all-ages median, and
    # program length is per-occupation (0 years for a job needing no degree, 2
    # for an associate's) rather than a flat four. baseline_start_age carries
    # both: its offset is derived from program length. Omitting it understated
    # every break-even here by 15-26% against what a visitor actually sees --
    # Nuclear Power Reactor Operators read $431,289 against the app's $542,587.
    #
    # enrollment_years stays 0: this table is the default no-foregone-earnings
    # view, matching the app's default.
    program_years = ns["program_years_for_education"](
        ns["MAJOR_DATA"].get(major, {}).get("typical_education"), major)
    # The title is required: for the 74 occupations with a training overlay
    # the earnings curve starts at the bachelor's and the baseline must too.
    baseline_start_age = ns["baseline_start_age_for"](program_years, 0, major)
    result = ns["find_breakeven_loan"](major, rate, strategy,
                                        baseline_start_age=baseline_start_age)
    status = {"never": "never_breaks_even",
              "beyond_search_max": "breakeven_above_search_max"}.get(result["status"], "ok")
    premium_at_zero = ns["compute_scenario_results"](
        major, 0.0, rate, strategy,
        baseline_start_age=baseline_start_age)["roi_result"]["earnings_premium"]
    return {"status": status, "breakeven_loan": result["breakeven_loan"],
            "premium_at_zero_debt": premium_at_zero,
            "program_years": program_years,
            # The app deliberately shows no break-even for these: a career you
            # can enter with a diploma has no debt ceiling, and a level whose
            # length we don't believe would give a number built on a wrong
            # premise. The figure is still computed here because it's a valid
            # research quantity -- but the paper must not report it as
            # something a visitor saw.
            "shown_in_app": not (
                ns["MAJOR_DATA"].get(major, {}).get("typical_education", "")
                in ns["MISMODELLED_EDUCATION_LEVELS"] or program_years == 0)}


def build_breakeven_table(ns: dict, rate: float) -> pd.DataFrame:
    rows = []
    majors = sorted(ns["MAJOR_DATA"].keys())
    for i, major in enumerate(majors, 1):
        if i % 100 == 0:
            print(f"  ...{i}/{len(majors)} majors", file=sys.stderr)
        info = ns["MAJOR_DATA"][major]
        # An "extended-training track" delays earnings, carries professional
        # -school debt, or both (Medicine: 4 unpaid years + 3 residency years
        # + $205k; Athletic Training: 2 unpaid years, no debt). They fail the
        # 10-year comparison for a categorically different reason than a
        # low-wage occupation does, and must not be pooled with them.
        row = {
            "major": major,
            "starting_salary": info["starting_salary"],
            "additional_training_debt": info.get("additional_training_debt", 0),
            "unpaid_training_years": info.get("unpaid_training_years", 0),
            "stipend_training_years": info.get("stipend_training_years", 0),
        }
        row["extended_training_track"] = bool(
            row["additional_training_debt"] or row["unpaid_training_years"]
            or row["stipend_training_years"]
        )
        row["typical_education"] = info.get("typical_education") or "(unknown)"
        row["education_group"] = classify_education(info, ns["SUB_BACHELORS_EDUCATION_LEVELS"])
        for strategy in STRATEGIES:
            result = find_breakeven_loan(ns, major, rate, strategy)
            key = "standard" if strategy == STRATEGIES[0] else "idr"
            row[f"breakeven_loan_{key}"] = result["breakeven_loan"]
            row[f"status_{key}"] = result["status"]
            if key == "standard":
                row["premium_at_zero_debt"] = round(result["premium_at_zero_debt"], 2)
                row["program_years"] = result["program_years"]
                row["shown_in_app"] = result["shown_in_app"]
        rows.append(row)
    return pd.DataFrame(rows)


def ns_window_note() -> str:
    return "WHAT THE 10-YEAR WINDOW DECIDES"


def print_window_sensitivity(ns: dict, rate: float):
    """How much of the verdict is the model's 10-year horizon rather than the
    occupation?

    ROI_WINDOW_YEARS is the single most consequential constant in the app: a
    track that spends its early years in training (Medicine's 4 unpaid years
    plus 3 at a $65k residency stipend) has barely started earning by year
    10, while its debt is repaid inside it. The window doesn't just measure
    the outcome, it largely determines it -- so the paper needs to say so
    before reporting anything above as a property of the occupation.

    This re-runs the same ROI math at other horizons. It's the honest
    sensitivity check on the app's own headline number.
    """
    print(f"\n{'=' * 78}\n{ns_window_note()}\n"
          f"How much of the verdict is the horizon, not the occupation?\n{'=' * 78}")
    majors = ["Medicine", "Law", "Athletic Training", "Registered Nurses", "Software Developers"]
    horizons = [10, 15, 20, 30]
    print(f"10-yr premium vs a debt-free HS grad, at $0 undergrad loan, by horizon:\n")
    header = f"{'major':22s}" + "".join(f"{h:>14}yr" for h in horizons)
    print(header)
    for m in majors:
        if m not in ns["MAJOR_DATA"]:
            continue
        cells = ""
        for h in horizons:
            principal = ns["get_effective_principal"](m, 0.0)
            repay = ns["calculate_standard_repayment"](principal, rate)
            # Same baseline as the break-even table above and as the app --
            # this panel is the sensitivity check ON the app's headline number,
            # so running it against a different baseline would compare the
            # horizon effect to the wrong thing.
            _py = ns["program_years_for_education"](
                ns["MAJOR_DATA"].get(m, {}).get("typical_education"), m)
            roi = ns["calculate_roi"](m, repay["total_paid_in_roi_window"], principal or 1,
                                       years=h,
                                       baseline_start_age=ns["baseline_start_age_for"](_py, 0, m))
            cells += f"{roi['earnings_premium']:>16,.0f}"
        print(f"{m[:22]:22s}{cells}")
    print("\n  Medicine is negative at 10 years and strongly positive later: at year 10 it\n"
          "  has had 3 years of attending salary against 7 of training, while its\n"
          "  $205k of med-school debt was repaid inside the window regardless. That is\n"
          "  a fact about the horizon, not about medicine. The app reports the 10-year\n"
          "  number to prospective students as though it were the verdict.")


def print_education_partition(df: pd.DataFrame):
    """Which occupations the app's core assumption is even true for.

    Everything else this script prints is conditional on this section. The
    app charges every occupation 4 years of undergrad on the loan slider and
    then drops the graduate straight into that occupation's wage. That holds
    for a bachelor's-level occupation. For the other two groups it's false in
    opposite directions, and a break-even number computed across all of them
    pools three incompatible questions.
    """
    line = "=" * 78
    print(f"\n{line}\nIS THE MODEL'S CORE ASSUMPTION TRUE FOR THIS OCCUPATION?\n"
          f"4 years of undergrad, financed, then straight into this wage.\n{line}")
    counts = df.education_group.value_counts()
    total = len(df)
    labels = {
        EDU_BACHELORS: "Bachelor's -- assumption HOLDS. Break-even is meaningful here.",
        EDU_SUB_BACHELORS: "Sub-bachelor's -- charged a 4-year degree the job never required.",
        EDU_ADVANCED_UNMODELED: "Needs a Master's/Doctorate -- extra school NOT modelled.",
        EDU_ADVANCED_MODELED: "Extended training, hand-modelled (Medicine/Law/Athletic Training).",
    }
    for group in [EDU_BACHELORS, EDU_SUB_BACHELORS, EDU_ADVANCED_UNMODELED, EDU_ADVANCED_MODELED]:
        n = int(counts.get(group, 0))
        print(f"  {n:4d} ({n/total*100:4.1f}%)  {labels[group]}")
    valid = int(counts.get(EDU_BACHELORS, 0)) + int(counts.get(EDU_ADVANCED_MODELED, 0))
    print(f"\n  Only {valid} of {total} ({valid/total*100:.1f}%) are modelled on the right "
          f"educational\n  assumption. Every aggregate below is restricted to those unless "
          f"it says\n  otherwise.")

    sub = df[df.education_group == EDU_SUB_BACHELORS]
    if not sub.empty:
        print(f"\n  SUB-BACHELOR'S ({len(sub)}): the comparison is malformed, not unfavourable.\n"
              "  Nobody borrows $190k to become a Shampooer, so 'does this degree pay off'\n"
              "  has no answer. These dominate the never-break-even group and inflate it:")
        print("   " + ", ".join(sub.typical_education.value_counts().index[:5]))

    adv = df[df.education_group == EDU_ADVANCED_UNMODELED]
    if not adv.empty:
        print(f"\n  ADVANCED, UNMODELLED ({len(adv)}): ROI is systematically OVERSTATED.\n"
              "  The model pays these occupations their full wage from year 1 with only\n"
              "  undergrad debt -- no extra years of school, no graduate-school debt.\n"
              "  Worst offenders (highest wage granted with no extra schooling):")
        print(adv.nlargest(6, "starting_salary")[
            ["major", "starting_salary", "typical_education"]].to_string(index=False))
        print("\n  This contradicts the app's own hand-modelled Medicine, which DOES serve\n"
              "  4 unpaid years + 3 of residency + $205k. Both live in the same dropdown:\n"
              "  at a $190k loan over 10 years, Medicine reports -$405k while Pediatric\n"
              "  Surgeons reports +$3.48M. Same life path, a $3.88M disagreement, and\n"
              "  nothing on screen tells a student which one to believe.")


def print_summary(df: pd.DataFrame, ns: dict, rate: float):
    line = "=" * 78
    print(f"\n{line}\nBREAK-EVEN DEBT BY MAJOR\n"
          f"At what undergrad loan does each major stop beating a debt-free HS grad?\n{line}")
    print(f"Model: {ns['ROI_WINDOW_YEARS']}-year window, HS baseline "
          f"${ns['HS_GRAD_SALARY']:,}/yr growing {ns['HS_GRAD_GROWTH_RATE']*100:.0f}%/yr, "
          f"loan rate {rate}%.")

    # Restricted to occupations the 4-year-degree assumption is actually true
    # for -- see print_education_partition. Reporting these aggregates over
    # all 836 was pooling three incompatible questions and producing a
    # headline ("half never break even") that was mostly an artifact of
    # charging cashiers for a degree they never needed.
    full_n = len(df)
    df = df[df.education_group.isin([EDU_BACHELORS, EDU_ADVANCED_MODELED])]
    print(f"Occupations analyzed: {len(df)} of {full_n} "
          f"(bachelor's-level + the hand-modelled training tracks; the rest are\n"
          f"  excluded as not-comparable -- see the education partition above)")

    never = df[df["status_standard"] == "never_breaks_even"]
    print(f"\nNever break even, even at $0 undergrad loan: {len(never)} "
          f"({len(never)/len(df)*100:.1f}% of occupations)")

    # Two different phenomena get pooled here, and conflating them would
    # misread the model badly. "$0 undergrad loan" is not "$0 debt" for a
    # track carrying additional_training_debt (Medicine's $205k of med
    # school, Law's $130k) -- get_effective_principal adds that on top of the
    # slider, so it's still being repaid inside the window no matter what the
    # loan is set to. A scholarship can't remove it in this model.
    wage_limited = never[~never["extended_training_track"]]
    extended = never[never["extended_training_track"]]

    print(f"\n  {len(wage_limited)} are wage-limited: the occupation earns less over 10 years than\n"
          "  a high school graduate does, so financing is irrelevant to the verdict and\n"
          "  no scholarship changes it.")
    if not wage_limited.empty:
        print("\n  Worst 10 (10-yr premium at zero undergrad loan):")
        print(wage_limited.nsmallest(10, "premium_at_zero_debt")[
            ["major", "starting_salary", "premium_at_zero_debt"]].to_string(index=False))

    if not extended.empty:
        print(f"\n  {len(extended)} are extended-training tracks, losing for a categorically\n"
              "  different reason: years spent training instead of earning, plus (where it\n"
              "  applies) professional-school debt that a $0 undergrad loan does NOT zero\n"
              "  out -- get_effective_principal adds it on top of the slider regardless.")
        print(extended[["major", "starting_salary", "unpaid_training_years",
                        "stipend_training_years", "additional_training_debt",
                        "premium_at_zero_debt"]].to_string(index=False))
        print("\n  Do not read these as 'the degree is not worth it'. Read the window:\n"
              f"  see the {ns_window_note()} section below.")

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
              f"${ns['BREAKEVEN_SEARCH_MAX_LOAN']:,.0f} under IDR --")
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
    print_education_partition(df)
    print_summary(df, ns, args.rate)
    print_window_sensitivity(ns, args.rate)

    args.output.parent.mkdir(exist_ok=True)
    df.to_csv(args.output, index=False)
    print(f"\nPer-major table written to {args.output}")


if __name__ == "__main__":
    main()

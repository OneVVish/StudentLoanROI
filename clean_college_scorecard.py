"""
Clean and Calculate Cost of Attendance (COA) from College Scorecard Data
--------------------------------------------------------------------------
Loads the U.S. Department of Education's College Scorecard institution-level
CSV (download at collegescorecard.ed.gov/data) and derives two clean fields
for every school: in_state_coa and out_of_state_coa.

Project rules this script implements:
  1. COSTT4_A (academic-year programs) and COSTT4_P (continuous-enrollment
     programs, e.g. many cosmetology/vocational programs) are alternative
     base COA figures -- a school reports one or the other, not both.
  2. COSTT4_A/COSTT4_P are treated as being built on IN-STATE tuition. For
     public schools, Out-of-State COA is derived by swapping the tuition
     component: Out-of-State COA = Base COA - TUITIONFEE_IN + TUITIONFEE_OUT.
  3. For private schools (nonprofit or for-profit), there's no in-state/
     out-of-state distinction, so In-State COA = Out-of-State COA = Base COA.
  4. CONTROL (1 = Public, 2 = Private Non-Profit, 3 = Private For-Profit) is
     kept alongside a human-readable label.

Usage:
    python clean_college_scorecard.py path/to/Most-Recent-Cohorts-Institution.csv
    python clean_college_scorecard.py raw.csv -o college_coa_clean.csv
"""

import argparse

import pandas as pd

# Only pull the columns this script actually needs -- the real Scorecard
# file has 3,000+ columns, so limiting to these keeps load time and memory
# usage reasonable.
COLUMNS_TO_LOAD = [
    "INSTNM",          # Institution name
    "STABBR",          # 2-letter state abbreviation (used to pick a state-level
                       # community-college cost default in the app)
    "CONTROL",         # 1 = Public, 2 = Private Non-Profit, 3 = Private For-Profit
    "COSTT4_A",        # Avg. cost of attendance, academic-year programs
    "COSTT4_P",        # Avg. cost of attendance, continuous-enrollment programs
    "TUITIONFEE_IN",   # In-state tuition & fees
    "TUITIONFEE_OUT",  # Out-of-state tuition & fees
    "NPCURL",          # URL of the school's own Net Price Calculator (a text
                       # field -- deliberately NOT in numeric_columns below, or
                       # to_numeric would coerce every URL to NaN). Often blank
                       # or missing a scheme; the app normalizes it at display.
]

# College Scorecard's raw CSV export encodes missing numeric values as the
# literal strings "NULL" or "PrivacySuppressed" (the latter marks cohorts too
# small to report without risking identifying individual students, per
# FERPA) instead of leaving the cell blank. Pandas won't treat these as
# missing unless told to -- otherwise the whole column silently loads as
# text (object dtype) and every calculation below would error or no-op.
NA_VALUES = ["NULL", "PrivacySuppressed"]

CONTROL_LABELS = {
    1: "Public",
    2: "Private Non-Profit",
    3: "Private For-Profit",
}


def load_scorecard_data(csv_path: str) -> pd.DataFrame:
    """Read the raw Scorecard CSV and coerce the columns we need to numeric."""
    df = pd.read_csv(
        csv_path,
        usecols=COLUMNS_TO_LOAD,
        na_values=NA_VALUES,
        low_memory=False,  # avoids dtype-guessing warnings on a very wide file
    )
    numeric_columns = ["CONTROL", "COSTT4_A", "COSTT4_P", "TUITIONFEE_IN", "TUITIONFEE_OUT"]
    for column in numeric_columns:
        # errors="coerce" is a safety net: if any stray non-numeric text
        # slipped past NA_VALUES, it becomes NaN instead of crashing the script.
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def calculate_coa(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the project's COA rules and add in_state_coa/out_of_state_coa."""
    df = df.copy()

    # ---- Step 1: pick one base COA per row ----
    # COSTT4_A takes priority; COSTT4_P fills in only where COSTT4_A is
    # missing (continuous-enrollment programs, which mostly won't have an
    # academic-year figure at all).
    df["base_coa"] = df["COSTT4_A"].fillna(df["COSTT4_P"])

    is_public = df["CONTROL"] == 1

    # ---- Step 2: In-State COA ----
    # Rule 2 treats the base COA as already built on in-state tuition, for
    # every school (public or private), so in-state COA is just the base COA.
    df["in_state_coa"] = df["base_coa"]

    # ---- Step 3: Out-of-State COA ----
    # Start every row equal to in-state (this is the correct final answer
    # for private schools per rule 3, and for any school we can't otherwise
    # adjust); then override the public-school rows we CAN adjust below.
    df["out_of_state_coa"] = df["base_coa"]

    # Only public schools with both tuition figures present can have the
    # swap applied: Out-of-State COA = Base COA - TUITIONFEE_IN + TUITIONFEE_OUT.
    # This assumes every other COA component (room & board, books, fees,
    # etc.) is the same regardless of residency -- only the tuition line
    # item changes. That's this project's stated simplification, not a
    # methodology published by College Scorecard itself.
    has_both_tuition_figures = df["TUITIONFEE_IN"].notna() & df["TUITIONFEE_OUT"].notna()
    adjustable = is_public & has_both_tuition_figures
    df.loc[adjustable, "out_of_state_coa"] = (
        df.loc[adjustable, "base_coa"]
        - df.loc[adjustable, "TUITIONFEE_IN"]
        + df.loc[adjustable, "TUITIONFEE_OUT"]
    )
    # Edge case: a public school can have a known base_coa but a missing
    # TUITIONFEE_IN or TUITIONFEE_OUT (common for small/new programs). The
    # adjustment above already leaves those rows at out_of_state_coa ==
    # in_state_coa (the pre-set fallback from the line above) rather than
    # NaN -- i.e. we degrade to "best available estimate" instead of
    # discarding a COA figure we actually have.

    # ---- Step 4: human-readable control type ----
    df["control_type"] = df["CONTROL"].map(CONTROL_LABELS).fillna("Unknown")

    return df


def build_clean_dataframe(csv_path: str) -> pd.DataFrame:
    """Load, clean, and return the final consolidated COA dataframe."""
    raw = load_scorecard_data(csv_path)
    calculated = calculate_coa(raw)

    # Final cleaning pass: drop schools with no usable cost data at all --
    # if both COSTT4_A and COSTT4_P were missing, nothing above could
    # produce a real number, and keeping all-NaN rows isn't "clean."
    before = len(calculated)
    calculated = calculated.dropna(subset=["in_state_coa", "out_of_state_coa"], how="all")
    dropped = before - len(calculated)
    if dropped:
        print(f"Dropped {dropped} row(s) with no reported COA data (COSTT4_A and COSTT4_P both missing).")

    # Identity/label columns first, then the calculated fields, then the
    # raw source columns -- keeping the raw figures alongside the derived
    # ones lets you show your work (how in_state_coa/out_of_state_coa were
    # derived) rather than presenting a black-box number.
    final_columns = [
        "INSTNM", "STABBR", "CONTROL", "control_type",
        "in_state_coa", "out_of_state_coa",
        "COSTT4_A", "COSTT4_P", "TUITIONFEE_IN", "TUITIONFEE_OUT",
        "NPCURL",
    ]
    return calculated[final_columns].reset_index(drop=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Clean College Scorecard data and calculate in-state/out-of-state cost of attendance."
    )
    parser.add_argument("input_csv", help="Path to the raw College Scorecard institution CSV")
    parser.add_argument("-o", "--output", default="college_coa_clean.csv", help="Output CSV path")
    args = parser.parse_args()

    clean_df = build_clean_dataframe(args.input_csv)
    clean_df.to_csv(args.output, index=False)
    print(f"Wrote {len(clean_df)} rows to {args.output}")

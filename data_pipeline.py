"""
Clean and Format BLS OEWS Occupational Wage Data
--------------------------------------------------------------------------
Loads the U.S. Bureau of Labor Statistics' Occupational Employment and Wage
Statistics (OEWS) national XLSX release (download at bls.gov/oes/tables.htm)
and produces a clean per-occupation wage dataset for the app's career
dropdown: cleaned_careers.csv.

Project rules this script implements:
  1. Column names are normalized to lowercase on load -- BLS's real XLSX
     files use uppercase headers (OCC_CODE, A_MEDIAN, ...), so matching
     case-insensitively keeps this script working across release years
     without silently breaking on a header-casing change.
  2. Only o_group == "detailed" rows are kept -- "major"/"minor"/"broad"/
     "total" rows are cross-occupation summary aggregates, not real,
     individually selectable careers.
  3. Wage columns use two BLS-specific text markers instead of numbers:
       "#"  -- wage is top-coded: BLS suppresses the exact figure above a
               threshold and reports "#" instead. Current published
               threshold: $239,200/year ($115/hour) -- converted to that
               number, since it's a real floor, not a guess.
       "*"  -- estimate suppressed/not published (confidentiality or
               reliability) -- there's no real number behind this marker,
               so rows that are still unusable after cleaning are dropped.
  4. Every occupation gets a starting_salary (a_pct10) and median_salary
     (a_median) pair, so the app's existing CAGR growth formula
     (get_major_growth_rate in app.py) works identically for BLS-sourced
     careers as it does for the 11 hand-curated majors. annual_growth_rate
     is still included per-row as a documented, human-readable summary of
     that same rate (and as the fallback value on the rare row where
     a_pct10 itself is unusable).

Usage:
    python data_pipeline.py raw_bls_data.xlsx
    python data_pipeline.py raw_bls_data.xlsx -o cleaned_careers.csv
"""

import argparse

import pandas as pd

# BLS's current top-code threshold for suppressed high wages (the "#"
# marker). Source: BLS OEWS technical documentation, $115/hour x 2,080
# hours/year = $239,200/year. This changes over time -- if a newer BLS
# release uses a different threshold, update this one constant.
TOP_CODE_ANNUAL_WAGE = 239200

# Number of years assumed between "starting" (10th percentile) and
# "median" pay for CAGR purposes -- matches get_major_growth_rate's
# existing 10-year assumption in app.py exactly, so every BLS-sourced
# career's implied growth rate is computed the same way as the 11
# hand-curated majors.
GROWTH_WINDOW_YEARS = 10

# Fallback annual growth rate used only when a_pct10 is unusable for a
# given occupation (suppressed/missing) and a real CAGR can't be computed.
DEFAULT_GROWTH_RATE = 0.03

REQUIRED_COLUMNS = ["occ_code", "occ_title", "o_group", "a_median", "a_pct10"]


def load_bls_data(xlsx_path: str) -> pd.DataFrame:
    """Read the raw BLS OEWS national XLSX and normalize column names to
    lowercase -- BLS ships these as uppercase (OCC_CODE, A_MEDIAN, ...),
    and matching case-insensitively keeps this script working even if a
    future release changes header casing."""
    df = pd.read_excel(xlsx_path)
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Raw BLS file is missing expected column(s): {missing}. "
            f"Found columns: {list(df.columns)}"
        )
    return df


def clean_wage_column(series: pd.Series) -> pd.Series:
    """Convert a raw OEWS wage column (mixed numbers and BLS text markers)
    into a strictly numeric column. "#" becomes the real top-code wage;
    "*"/"-"/blank become NaN, since there's no real number behind them --
    callers decide whether to drop or fall back on those rows."""
    cleaned = series.astype(str).str.strip()
    numeric = pd.to_numeric(cleaned.str.replace(",", "", regex=False), errors="coerce")
    is_top_coded = cleaned == "#"
    numeric.loc[is_top_coded] = TOP_CODE_ANNUAL_WAGE
    return numeric


def build_clean_dataframe(xlsx_path: str) -> pd.DataFrame:
    """Load, filter, and clean the raw BLS release into the final dataset."""
    raw = load_bls_data(xlsx_path)

    detailed = raw[raw["o_group"].astype(str).str.strip().str.lower() == "detailed"].copy()

    detailed["a_median"] = clean_wage_column(detailed["a_median"])
    detailed["a_pct10"] = clean_wage_column(detailed["a_pct10"])

    # No usable median wage at all -- this occupation can't be modeled,
    # drop it (mirrors clean_college_scorecard.py dropping schools with no
    # usable COA figure).
    before = len(detailed)
    detailed = detailed.dropna(subset=["a_median"])
    dropped_no_median = before - len(detailed)

    # a_pct10 suppressed/missing but a_median is fine: back-fill a
    # starting_salary that reproduces exactly DEFAULT_GROWTH_RATE through
    # get_major_growth_rate's CAGR formula, instead of leaving a NaN that
    # would break every downstream calculation for this career.
    missing_pct10 = detailed["a_pct10"].isna()
    fallback_count = int(missing_pct10.sum())
    detailed.loc[missing_pct10, "a_pct10"] = (
        detailed.loc[missing_pct10, "a_median"] / (1 + DEFAULT_GROWTH_RATE) ** GROWTH_WINDOW_YEARS
    )

    detailed["annual_growth_rate"] = (
        (detailed["a_median"] / detailed["a_pct10"]) ** (1 / GROWTH_WINDOW_YEARS) - 1
    )

    final_columns = ["occ_code", "occ_title", "o_group", "a_pct10", "a_median", "annual_growth_rate"]
    result = detailed[final_columns].reset_index(drop=True)

    if dropped_no_median:
        print(f"Dropped {dropped_no_median} occupation(s) with no usable median wage (suppressed/missing).")
    if fallback_count:
        print(
            f"{fallback_count} occupation(s) had a suppressed/missing 10th-percentile wage -- "
            f"assigned the default {DEFAULT_GROWTH_RATE:.0%} annual growth rate instead."
        )
    return result


def print_summary(df: pd.DataFrame) -> None:
    print()
    print(f"Total occupations processed: {len(df)}")
    print(f"Average median salary: ${df['a_median'].mean():,.0f}")
    print()
    print("Top 5 highest-paying careers:")
    top5 = df.nlargest(5, "a_median")
    for _, row in top5.iterrows():
        print(f"  {row['occ_title']}: ${row['a_median']:,.0f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Clean a BLS OEWS national XLSX release into a per-occupation wage dataset."
    )
    parser.add_argument("input_xlsx", nargs="?", default="raw_bls_data.xlsx",
                         help="Path to the raw BLS OEWS national XLSX file (default: raw_bls_data.xlsx)")
    parser.add_argument("-o", "--output", default="cleaned_careers.csv", help="Output CSV path")
    args = parser.parse_args()

    try:
        clean_df = build_clean_dataframe(args.input_xlsx)
    except FileNotFoundError:
        raise SystemExit(f"Error: could not find '{args.input_xlsx}'. Download it from bls.gov/oes/tables.htm.")
    except ValueError as exc:
        raise SystemExit(f"Error: {exc}")

    clean_df.to_csv(args.output, index=False)
    print(f"Wrote {len(clean_df)} rows to {args.output}")
    print_summary(clean_df)

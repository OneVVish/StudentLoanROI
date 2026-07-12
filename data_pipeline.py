"""
Clean and Format BLS OEWS Occupational Wage Data
--------------------------------------------------------------------------
Loads a U.S. Bureau of Labor Statistics' Occupational Employment and Wage
Statistics (OEWS) XLSX release (download at bls.gov/oes/tables.htm) and
produces a clean per-occupation wage dataset for the app's career dropdown.

Two geographic scopes are supported from two different BLS releases:
  - National ("oesm##nat.zip" -> a file like national_M2025_dl.xlsx): every
    occupation, one row per occupation, no --state flag needed.
  - State ("oesm##st.zip" -> a file like state_M2025_dl.xlsx): every
    occupation *for every state combined in one file* -- pass --state (a
    two-letter abbreviation, e.g. CA) to filter down to just that state.
    Do NOT pass the National file with --state; it has no state column.

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
  4. Every occupation gets a starting_salary (a_pct25) and median_salary
     (a_median) pair, so the app's existing CAGR growth formula
     (get_major_growth_rate in app.py) works identically for BLS-sourced
     careers as it does for the 11 hand-curated majors. 25th percentile
     rather than 10th: OEWS's 10th percentile mixes in part-time/reporting
     -quirk outliers (most visible for occupations like physicians, where
     it understates real entry-level pay dramatically) and produced
     unrealistically low starting-salary figures. annual_growth_rate is
     still included per-row as a documented, human-readable summary of
     that same rate (and as the fallback value on the rare row where
     a_pct25 itself is unusable).
  5. When --state is given, rows are additionally filtered to that state
     before the o_group/wage cleaning above. The state column's exact name
     isn't hardcoded to one guess -- BLS has used different names for it
     across releases -- so a short list of known candidates is checked in
     order, and column values are matched as either a 2-letter abbreviation
     or a full state name, whichever the file actually contains.

Usage:
    python data_pipeline.py raw_bls_data.xlsx
    python data_pipeline.py raw_bls_data.xlsx -o cleaned_careers.csv
    python data_pipeline.py state_M2025_dl.xlsx --state CA
    python data_pipeline.py state_M2025_dl.xlsx --state CA -o cleaned_careers_ca.csv
"""

import argparse

import pandas as pd

# BLS's current top-code threshold for suppressed high wages (the "#"
# marker). Source: BLS OEWS technical documentation, $115/hour x 2,080
# hours/year = $239,200/year. This changes over time -- if a newer BLS
# release uses a different threshold, update this one constant.
TOP_CODE_ANNUAL_WAGE = 239200

# Number of years assumed between "starting" (25th percentile) and
# "median" pay for CAGR purposes -- matches get_major_growth_rate's
# existing 10-year assumption in app.py exactly, so every BLS-sourced
# career's implied growth rate is computed the same way as the 11
# hand-curated majors.
GROWTH_WINDOW_YEARS = 10

# Fallback annual growth rate used only when a_pct25 is unusable for a
# given occupation (suppressed/missing) and a real CAGR can't be computed.
DEFAULT_GROWTH_RATE = 0.03

REQUIRED_COLUMNS = ["occ_code", "occ_title", "o_group", "a_median", "a_pct25"]

# Candidate column names BLS has used to hold the state for each row in the
# State release, checked in order -- whichever one is actually present in
# the loaded file is used, so this script isn't locked to one exact release.
STATE_COLUMN_CANDIDATES = ["prim_state", "st", "state", "area_title"]

STATE_ABBR_TO_NAME = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}


def find_state_column(df: pd.DataFrame) -> str:
    """Return whichever known state-column name is present in this file.
    Raises ValueError (with the real column list) if none match, instead
    of silently filtering on a column that doesn't exist."""
    for candidate in STATE_COLUMN_CANDIDATES:
        if candidate in df.columns:
            return candidate
    raise ValueError(
        f"--state was given, but no recognized state column "
        f"({STATE_COLUMN_CANDIDATES}) was found. Found columns: {list(df.columns)}. "
        f"Make sure you're passing the BLS *State* release, not the National one."
    )


def filter_to_state(df: pd.DataFrame, state_abbr: str) -> pd.DataFrame:
    """Filter to rows matching state_abbr (e.g. "CA"), auto-detecting
    whether this file's state column holds 2-letter abbreviations or full
    state names."""
    state_abbr = state_abbr.strip().upper()
    if state_abbr not in STATE_ABBR_TO_NAME:
        raise ValueError(f"'{state_abbr}' isn't a recognized two-letter state abbreviation.")

    column = find_state_column(df)
    values = df[column].astype(str).str.strip()
    looks_like_abbreviation = values.str.len().median() <= 3
    if looks_like_abbreviation:
        matches = values.str.upper() == state_abbr
    else:
        matches = values.str.lower() == STATE_ABBR_TO_NAME[state_abbr].lower()
    return df[matches]


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


def build_clean_dataframe(xlsx_path: str, state: str = None) -> pd.DataFrame:
    """Load, filter, and clean the raw BLS release into the final dataset.
    state, if given (e.g. "CA"), restricts to that state -- only valid
    against the BLS *State* release, which has every state in one file."""
    raw = load_bls_data(xlsx_path)

    if state:
        raw = filter_to_state(raw, state)

    detailed = raw[raw["o_group"].astype(str).str.strip().str.lower() == "detailed"].copy()

    detailed["a_median"] = clean_wage_column(detailed["a_median"])
    detailed["a_pct25"] = clean_wage_column(detailed["a_pct25"])

    # No usable median wage at all -- this occupation can't be modeled,
    # drop it (mirrors clean_college_scorecard.py dropping schools with no
    # usable COA figure).
    before = len(detailed)
    detailed = detailed.dropna(subset=["a_median"])
    dropped_no_median = before - len(detailed)

    # a_pct25 suppressed/missing but a_median is fine: back-fill a
    # starting_salary that reproduces exactly DEFAULT_GROWTH_RATE through
    # get_major_growth_rate's CAGR formula, instead of leaving a NaN that
    # would break every downstream calculation for this career.
    missing_pct25 = detailed["a_pct25"].isna()
    fallback_count = int(missing_pct25.sum())
    detailed.loc[missing_pct25, "a_pct25"] = (
        detailed.loc[missing_pct25, "a_median"] / (1 + DEFAULT_GROWTH_RATE) ** GROWTH_WINDOW_YEARS
    )

    detailed["annual_growth_rate"] = (
        (detailed["a_median"] / detailed["a_pct25"]) ** (1 / GROWTH_WINDOW_YEARS) - 1
    )

    final_columns = ["occ_code", "occ_title", "o_group", "a_pct25", "a_median", "annual_growth_rate"]
    result = detailed[final_columns].reset_index(drop=True)

    if dropped_no_median:
        print(f"Dropped {dropped_no_median} occupation(s) with no usable median wage (suppressed/missing).")
    if fallback_count:
        print(
            f"{fallback_count} occupation(s) had a suppressed/missing 25th-percentile wage -- "
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
                         help="Path to the raw BLS OEWS XLSX file (default: raw_bls_data.xlsx)")
    parser.add_argument("--state", default=None,
                         help="Two-letter state abbreviation (e.g. CA) to filter to. "
                              "Requires the BLS OEWS *State* release, not the National file.")
    parser.add_argument("-o", "--output", default=None,
                         help="Output CSV path (default: cleaned_careers.csv, or "
                              "cleaned_careers_<state>.csv when --state is given)")
    args = parser.parse_args()
    output_path = args.output or (
        f"cleaned_careers_{args.state.lower()}.csv" if args.state else "cleaned_careers.csv"
    )

    try:
        clean_df = build_clean_dataframe(args.input_xlsx, state=args.state)
    except FileNotFoundError:
        raise SystemExit(f"Error: could not find '{args.input_xlsx}'. Download it from bls.gov/oes/tables.htm.")
    except ValueError as exc:
        raise SystemExit(f"Error: {exc}")

    clean_df.to_csv(output_path, index=False)
    print(f"Wrote {len(clean_df)} rows to {output_path}")
    print_summary(clean_df)

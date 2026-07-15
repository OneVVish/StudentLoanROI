"""
Clean the NY Fed's College Labor Market Data into a per-major dataset
--------------------------------------------------------------------------
Loads the Federal Reserve Bank of New York's "The Labor Market for Recent
College Graduates" workbook and produces the app's per-MAJOR wage dataset —
the counterpart to data_pipeline.py, which produces the per-OCCUPATION one
from BLS OEWS.

Why a second dataset at all: a 17-year-old chooses a *major* ("Psychology"),
not an *occupation* ("Clinical and Counseling Psychologists"). The BLS
dataset can only answer "what if you become this?"; this one answers "what
if you study this?", which is the question actually being asked at the
moment the loan is signed.

    Download: https://www.newyorkfed.org/research/college-labor-market
              -> "College-labor-data.xlsx"
    Usage:    python3 nyfed_pipeline.py College-labor-data.xlsx \
                  -o data/nyfed_majors_clean.csv

The workbook is refreshed each February; re-run this against the new file.

--------------------------------------------------------------------------
THE WAGE MAPPING, WHICH IS THE WHOLE JOB

The NY Fed publishes two wage figures per major, each a median over an AGE
BAND, not a point in time:

    Median Wage Early Career  -> ages 22-27  (midpoint 24.5)
    Median Wage Mid-Career    -> ages 35-45  (midpoint 40)

The app models salary as `starting_salary * (1 + growth) ** year_index`,
where year_index counts years since finishing a bachelor's (age ~22). So the
two published figures land at roughly year 2.5 and year 18 — 15.5 years
apart, NOT the 10 years the app's BLS-derived data assumes.

Two consequences this script handles, and getting either wrong silently
inflates every projection:

 1. growth = (mid / early) ** (1 / 15.5) - 1, not ** (1 / 10). Using 10
    overstates annual growth by ~55% for every major — compounding, over a
    30-year horizon, to a $226k vs $161k year-30 salary for Computer
    Science.

 2. starting_salary is back-extrapolated to year 0 (age 22): the early
    figure is the median across 22-27-year-olds, so it describes ~year 2.5,
    not graduation day. starting = early / (1 + growth) ** 2.5. Feeding the
    early median in as the year-0 salary would overstate the first years.

Note the two spans are DIFFERENT and conflating them is a silent error:
growth is derived from the published pair (15.5 years apart), but the app
derives it from starting_salary (year 0) to median_salary (year 18), so the
emitted wage_growth_span_years is 18. Emitting 15.5 reconstructs Computer
Science's year-18 salary as $127,450 against a published $120,000. The
identity is median/starting = (1+g)^15.5 * (1+g)^2.5 = (1+g)^18.

CAVEAT worth carrying into any writeup: the app's default 10-year window
ends at age 32, which falls in the GAP between the two published bands. Year
10 is therefore interpolated between them, not observed. Longer horizons
(20y -> age 42) sit inside the mid-career band and are better supported.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

# The published age bands, and the graduation age the app counts years from.
GRADUATION_AGE = 22
EARLY_BAND = (22, 27)
MID_BAND = (35, 45)


def _band_midpoint_year(band: tuple) -> float:
    """Years post-graduation at the midpoint of an age band."""
    return (band[0] + band[1]) / 2 - GRADUATION_AGE


EARLY_YEAR = _band_midpoint_year(EARLY_BAND)   # 2.5 — early-career median describes ~here
MID_YEAR = _band_midpoint_year(MID_BAND)       # 18.0 — mid-career median describes ~here

# Growth is derived from the two PUBLISHED figures, 15.5 years apart.
PUBLISHED_SPAN_YEARS = MID_YEAR - EARLY_YEAR   # 15.5

# ...but the span the APP needs is different, and conflating the two is an
# easy and silent error. The app derives growth from starting_salary (which
# this script back-extrapolates to year 0) to median_salary (year 18) — so
# it must divide by 18, not 15.5. Emitting 15.5 reconstructs a year-18
# salary of $127,450 for Computer Science against a published $120,000.
# The identity: median/starting = (1+g)^15.5 * (1+g)^2.5 = (1+g)^18.
WAGE_GROWTH_SPAN_YEARS = MID_YEAR             # 18.0

# The sheet holds a preamble (title/source/updated block) before the header.
SHEET_NAME = "outcomes by major"
HEADER_ROW = 10
RAW_COLUMNS = ["major", "unemployment_rate", "underemployment_rate",
               "median_wage_early_career", "median_wage_mid_career",
               "share_with_graduate_degree"]
# The sheet's final row is an all-majors aggregate, not a major.
AGGREGATE_ROW_LABEL = "Overall"


def load_nyfed(xlsx_path: str) -> pd.DataFrame:
    try:
        raw = pd.read_excel(xlsx_path, sheet_name=SHEET_NAME, header=HEADER_ROW)
    except FileNotFoundError:
        sys.exit(f"No such file: {xlsx_path}")
    except ValueError as exc:
        sys.exit(f"Couldn't read sheet {SHEET_NAME!r} from {xlsx_path}: {exc}\n"
                 "Has the NY Fed changed the workbook's layout? Check the sheet names "
                 "and the header row offset at the top of this script.")
    if raw.shape[1] != len(RAW_COLUMNS):
        sys.exit(f"Expected {len(RAW_COLUMNS)} columns in {SHEET_NAME!r}, found {raw.shape[1]}. "
                 "The workbook's layout has changed; re-check RAW_COLUMNS before trusting output.")
    raw.columns = RAW_COLUMNS
    return raw.dropna(subset=["major"])


def clean(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw[raw["major"] != AGGREGATE_ROW_LABEL].copy()
    for col in RAW_COLUMNS[1:]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["median_wage_early_career", "median_wage_mid_career"])

    # See the module docstring: growth over the real 15.5-year interval
    # between band midpoints, then back-extrapolate to year 0.
    df["wage_growth_rate"] = (
        (df["median_wage_mid_career"] / df["median_wage_early_career"]) ** (1 / PUBLISHED_SPAN_YEARS) - 1
    )
    df["starting_salary"] = df["median_wage_early_career"] / (1 + df["wage_growth_rate"]) ** EARLY_YEAR
    df["starting_salary"] = df["starting_salary"].round(0)
    # median_salary is what the app's get_major_growth_rate divides by; emit
    # the mid-career figure so that derivation reproduces wage_growth_rate
    # exactly, given wage_growth_span_years.
    df["median_salary"] = df["median_wage_mid_career"].round(0)
    df["wage_growth_span_years"] = WAGE_GROWTH_SPAN_YEARS

    out = df[[
        "major", "starting_salary", "median_salary", "wage_growth_span_years",
        "unemployment_rate", "underemployment_rate", "share_with_graduate_degree",
        "median_wage_early_career", "median_wage_mid_career",
    ]].sort_values("major").reset_index(drop=True)
    return out


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("xlsx", help="NY Fed College-labor-data.xlsx")
    parser.add_argument("-o", "--output", default="data/nyfed_majors_clean.csv",
                        help="output CSV path (default: data/nyfed_majors_clean.csv)")
    args = parser.parse_args()

    raw = load_nyfed(args.xlsx)
    out = clean(raw)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.output, index=False)

    print(f"{len(out)} majors -> {args.output}")
    print(f"growth derived from published figures {PUBLISHED_SPAN_YEARS}y apart "
          f"(year {EARLY_YEAR} -> year {MID_YEAR}); emitted span for the app is "
          f"{WAGE_GROWTH_SPAN_YEARS}y (year 0 -> year {MID_YEAR})")
    print(f"median underemployment across majors: {out.underemployment_rate.median():.1f}%")
    print("\nsanity — highest and lowest starting salary:")
    print(out.nlargest(3, "starting_salary")[["major", "starting_salary", "median_salary"]].to_string(index=False))
    print(out.nsmallest(3, "starting_salary")[["major", "starting_salary", "median_salary"]].to_string(index=False))


if __name__ == "__main__":
    main()

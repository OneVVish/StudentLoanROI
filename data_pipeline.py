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
    python data_pipeline.py MSA_M2025_dl.xlsx --metros --national national_M2025_dl.xlsx

Every geographic file must come from the same release year as the others --
app.py shows metro and national wages side by side, so a metro file one
release behind reads as a pay cut rather than as stale data. --metros
enforces this where it can (see release_vintage).
"""

import argparse
import re
from pathlib import Path

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

# BLS's own national all-occupations median annual wage, the denominator of
# the metro wage index (see build_metro_wage_index). Read straight off the
# National release's o_group == "total" row -- "All Occupations", 155,495,730
# workers, May 2025. Update it in the same pass as any release bump, and from
# the SAME release as the metro file: mixing vintages here would tilt every
# city's index by whatever wage growth happened in between.
#
# Prefer `--metros --national national_M####_dl.xlsx`, which reads this figure
# out of that file instead and refuses to run if the two releases disagree.
# The constant is the fallback for when the National file isn't on hand, and
# it is only correct while NATIONAL_MEDIAN_VINTAGE matches the metro release.
NATIONAL_ALL_OCCUPATIONS_MEDIAN = 50980
NATIONAL_MEDIAN_VINTAGE = "M2025"

# The app's CITY_DATA keys mapped to their BLS OEWS metropolitan area
# titles, for --metros. Hardcoded here rather than derived, because BLS's
# area titles are long, change wording between releases ("Nashville-
# Davidson--Murfreesboro--Franklin, TN"), and a fuzzy match that silently
# picks the wrong metro would be worse than a loud failure -- --metros exits
# if any of these no longer resolves.
#
# Keep in sync with CITY_DATA in app.py section 1. Every key here must exist
# there; "National Average" is deliberately absent, since that IS the
# national file this script already produces.
METRO_AREA_BY_CITY = {
    "New York, NY": "New York-Newark-Jersey City, NY-NJ",
    "San Francisco, CA": "San Francisco-Oakland-Fremont, CA",
    "Chicago, IL": "Chicago-Naperville-Elgin, IL-IN",
    "Austin, TX": "Austin-Round Rock-San Marcos, TX",
    "Atlanta, GA": "Atlanta-Sandy Springs-Roswell, GA",
    "Columbus, OH": "Columbus, OH",
    "Denver, CO": "Denver-Aurora-Centennial, CO",
    "Los Angeles, CA": "Los Angeles-Long Beach-Anaheim, CA",
    "San Diego, CA": "San Diego-Chula Vista-Carlsbad, CA",
    "Dallas, TX": "Dallas-Fort Worth-Arlington, TX",
    "Houston, TX": "Houston-Pasadena-The Woodlands, TX",
    "San Antonio, TX": "San Antonio-New Braunfels, TX",
    "Cleveland, OH": "Cleveland, OH",
    "Miami, FL": "Miami-Fort Lauderdale-West Palm Beach, FL",
    "Seattle, WA": "Seattle-Tacoma-Bellevue, WA",
    "Nashville, TN": "Nashville-Davidson--Murfreesboro--Franklin, TN",
    "Philadelphia, PA": "Philadelphia-Camden-Wilmington, PA-NJ-DE-MD",
    "Boston, MA": "Boston-Cambridge-Newton, MA-NH",
    "Phoenix, AZ": "Phoenix-Mesa-Chandler, AZ",
    "Detroit, MI": "Detroit-Warren-Dearborn, MI",
    "Charlotte, NC": "Charlotte-Concord-Gastonia, NC-SC",
    "Minneapolis, MN": "Minneapolis-St. Paul-Bloomington, MN-WI",
}

# Fallback annual growth rate used only when a_pct25 is unusable for a
# given occupation (suppressed/missing) and a real CAGR can't be computed.
DEFAULT_GROWTH_RATE = 0.03

REQUIRED_COLUMNS = ["occ_code", "occ_title", "o_group", "a_median", "a_pct25"]

# The rest of OEWS's published wage distribution, carried through so the app
# can show what an occupation's pay actually spreads across rather than just a
# starting/median pair. These are NOT in REQUIRED_COLUMNS: the model runs
# entirely off a_pct25 and a_median, so a release missing them should still
# produce a usable dataset -- the app treats absent percentiles as "no
# distribution chart for this career" and carries on.
#
# OEWS publishes only these five points, never microdata, so a real frequency
# histogram is not derivable from it; what IS derivable is the share of workers
# between consecutive percentiles. See build_wage_distribution in app.py.
DISTRIBUTION_COLUMNS = ["a_pct10", "a_pct75", "a_pct90"]

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


def release_vintage(xlsx_path: str) -> str:
    """The "M2025"-style release token out of a BLS filename
    (national_M2025_dl.xlsx, MSA_M2024_dl.xlsx), or None if the file has been
    renamed past recognition.

    The filename is the only place the vintage lives -- OEWS's own columns
    (AREA, OCC_CODE, A_MEDIAN, ...) carry no year field, so there is nothing
    inside the workbook to check against. That makes a mismatch invisible at
    read time and permanent once it's in a committed CSV, which is exactly
    what happened here: the metro files were built from M2024 while
    cleaned_careers.csv moved on to M2025, leaving 18% of New York
    occupations reading *below* the national wage purely from a year of
    wage growth."""
    match = re.search(r"[_\b]M(\d{4})", Path(xlsx_path).stem, flags=re.IGNORECASE)
    return f"M{match.group(1)}" if match else None


def national_all_occupations_median(xlsx_path: str) -> float:
    """The all-occupations median annual wage off a National release's
    o_group == "total" row -- the metro wage index's denominator, taken from
    a real file rather than the hardcoded constant."""
    raw = load_bls_data(xlsx_path)
    totals = raw[raw["o_group"].astype(str).str.strip().str.lower() == "total"]
    if totals.empty:
        raise ValueError(
            f"'{xlsx_path}' has no o_group == 'total' row, so it can't supply the "
            f"national all-occupations median. Pass the BLS *National* release."
        )
    median = clean_wage_column(totals["a_median"]).iloc[0]
    if pd.isna(median):
        raise ValueError(f"'{xlsx_path}' has no usable all-occupations median wage.")
    return float(median)


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

    return _clean_detailed(raw)


def _clean_detailed(raw: pd.DataFrame, quiet: bool = False) -> pd.DataFrame:
    """The o_group/wage cleaning every release shares, applied after any
    geographic filtering. Extracted so --metros runs each city through
    exactly the same rules as the national and state files rather than a
    second copy of them (quiet suppresses the per-slice notes, which would
    otherwise print 22 times)."""
    detailed = raw[raw["o_group"].astype(str).str.strip().str.lower() == "detailed"].copy()

    detailed["a_median"] = clean_wage_column(detailed["a_median"])
    detailed["a_pct25"] = clean_wage_column(detailed["a_pct25"])

    # The rest of the distribution, where this release publishes it. Left as
    # NaN when suppressed rather than back-filled the way a_pct25 is above:
    # a_pct25 feeds the growth model and must have a number, but these are
    # display-only, and inventing a percentile would draw a distribution that
    # BLS never measured.
    present_distribution = [c for c in DISTRIBUTION_COLUMNS if c in detailed.columns]
    for column in present_distribution:
        detailed[column] = clean_wage_column(detailed[column])

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

    # a_pct10 ... a_pct90 in ascending order, so the CSV reads as the
    # distribution it is. Columns the release didn't publish are simply absent.
    final_columns = (
        ["occ_code", "occ_title", "o_group"]
        + [c for c in ["a_pct10", "a_pct25", "a_median", "a_pct75", "a_pct90"]
           if c == "a_pct25" or c == "a_median" or c in present_distribution]
        + ["annual_growth_rate"]
    )
    result = detailed[final_columns].reset_index(drop=True)

    if dropped_no_median and not quiet:
        print(f"Dropped {dropped_no_median} occupation(s) with no usable median wage (suppressed/missing).")
    if fallback_count and not quiet:
        print(
            f"{fallback_count} occupation(s) had a suppressed/missing 25th-percentile wage -- "
            f"assigned the default {DEFAULT_GROWTH_RATE:.0%} annual growth rate instead."
        )
    return result


def build_metro_dataframe(xlsx_path: str) -> pd.DataFrame:
    """Every app city's own metro wages, from the BLS OEWS *Metropolitan*
    release (oesm##ma.zip -> MSA_M####_dl.xlsx), as ONE long-format table
    with a `city` column.

    Long format rather than 22 separate CSVs: the app filters by city at
    load, 22 files would drift out of sync with each other, and the whole
    set is only ~1MB.

    Why this exists at all: without it, a San Francisco student is modelled
    with a *national* wage against San Francisco's cost-of-living index --
    earning the country's average while paying SF's prices. The obvious
    shortcut, scaling the national wage by the cost index, is worse than
    useless: it cancels algebraically against the app's existing COL
    division, and it's empirically false anyway. Comparing this app's own
    national and California files, the CA/national wage ratio runs from 1.01
    to 1.37 across occupations (nurses 1.52, cashiers 1.25) against a
    statewide price index of ~1.12. Geographic wage premiums are
    occupation-specific; one index cannot represent them. So: measure, don't
    estimate.

    Suppression is heavier here than nationally -- BLS withholds small
    occupation-by-metro cells -- so each city carries roughly 73-91% of the
    national occupation list. Occupations a city doesn't publish are simply
    absent from its rows; app-side, they fall back to the national wage and
    say so rather than silently pretending the figure is local.
    """
    raw = load_bls_data(xlsx_path)
    if "area_title" not in raw.columns:
        raise ValueError(
            "No 'area_title' column -- this doesn't look like the BLS Metropolitan release. "
            "Download oesm##ma.zip from bls.gov/oes/tables.htm and pass its MSA_M####_dl.xlsx."
        )

    available = set(raw["area_title"].dropna().astype(str))
    missing = {city: area for city, area in METRO_AREA_BY_CITY.items() if area not in available}
    if missing:
        # Loud rather than silent: a renamed metro would otherwise quietly
        # drop that city to national wages for every visitor.
        raise ValueError(
            "These metro area titles aren't in the file -- BLS likely renamed them in this "
            "release. Update METRO_AREA_BY_CITY:\n  "
            + "\n  ".join(f"{city}: {area!r}" for city, area in missing.items())
        )

    frames = []
    for city, area in METRO_AREA_BY_CITY.items():
        metro_raw = raw[raw["area_title"].astype(str) == area]
        cleaned = _clean_detailed(metro_raw, quiet=True)
        cleaned.insert(0, "city", city)
        frames.append(cleaned)
        print(f"  {city:20s} {len(cleaned):>4d} occupations")

    return pd.concat(frames, ignore_index=True)


def build_metro_wage_index(xlsx_path: str, national_xlsx: str = None) -> pd.DataFrame:
    """Each app city's overall wage level relative to the nation, from BLS's
    own all-occupations median (the o_group == "total" row per area).

    Two jobs, both of which the per-occupation metro file can't do:

     1. The high-school-graduate baseline every comparison runs against is a
        single national figure. Give the DEGREE a San Francisco wage while
        the baseline stays national and San Francisco's premium lands on one
        side of the scale only -- the degree looks better purely for being
        in an expensive city. A high school graduate in SF earns more too.
        This index scales that baseline.

     2. Major mode's NY Fed wages are national and have no per-city
        equivalent, so an index is the only way to localise them at all.
        It happens to suit them: a major's salary describes people spread
        across many occupations, which is exactly the population an
        all-occupations index describes. (It would be a poor stand-in for a
        single occupation -- which is why Career mode uses real per-metro
        occupation wages instead.)

    Not the cost-of-living index, which measures prices, not pay: San
    Francisco's wage index is 1.49 against a 1.18 price index, and the
    occupation-level premiums cluster near 1.49. Using prices to stand in for
    pay understates high-wage metros by a third.

    The denominator is BLS's published national all-occupations median from
    the SAME release, so the index is internally consistent rather than
    mixing vintages. Pass national_xlsx (the National release paired with this
    metro file) to read it from that file and have the vintage checked;
    without it the NATIONAL_ALL_OCCUPATIONS_MEDIAN constant is used, which is
    only right while its vintage still matches.
    """
    raw = load_bls_data(xlsx_path)
    if "area_title" not in raw.columns:
        raise ValueError("No 'area_title' column -- this isn't the BLS Metropolitan release.")

    metro_vintage = release_vintage(xlsx_path)
    if national_xlsx:
        national_vintage = release_vintage(national_xlsx)
        if metro_vintage and national_vintage and metro_vintage != national_vintage:
            raise ValueError(
                f"Release mismatch: the metro file is {metro_vintage} but "
                f"'{national_xlsx}' is {national_vintage}. The index would divide one "
                f"year's metro wages by another's national wage, tilting every city by "
                f"whatever wage growth happened in between. Download the matching pair "
                f"from bls.gov/oes/tables.htm."
            )
        national_median = national_all_occupations_median(national_xlsx)
    else:
        national_median = NATIONAL_ALL_OCCUPATIONS_MEDIAN
        if metro_vintage and metro_vintage != NATIONAL_MEDIAN_VINTAGE:
            raise ValueError(
                f"The metro file is {metro_vintage} but NATIONAL_ALL_OCCUPATIONS_MEDIAN is "
                f"{NATIONAL_MEDIAN_VINTAGE}. Either pass --national {metro_vintage}'s National "
                f"release, or update the constant and NATIONAL_MEDIAN_VINTAGE from its "
                f"o_group == 'total' row."
            )
        print(
            f"Note: no --national file given, so the index uses the hardcoded "
            f"${NATIONAL_ALL_OCCUPATIONS_MEDIAN:,} ({NATIONAL_MEDIAN_VINTAGE}) denominator."
        )

    totals = raw[raw["o_group"].astype(str).str.strip().str.lower() == "total"].copy()
    totals["a_median"] = clean_wage_column(totals["a_median"])

    rows = []
    for city, area in METRO_AREA_BY_CITY.items():
        match = totals[totals["area_title"].astype(str) == area]
        if match.empty or pd.isna(match["a_median"].iloc[0]):
            raise ValueError(f"No all-occupations median for {city} ({area!r}).")
        median = float(match["a_median"].iloc[0])
        rows.append({"city": city, "all_occupations_median": median,
                     "wage_index": median / national_median})
    return pd.DataFrame(rows).sort_values("wage_index", ascending=False).reset_index(drop=True)


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
    parser.add_argument("--metros", action="store_true",
                         help="Produce one long-format dataset covering every city in the app's "
                              "CITY_DATA, with a `city` column. Requires the BLS OEWS "
                              "*Metropolitan* release (oesm##ma.zip -> MSA_M####_dl.xlsx).")
    parser.add_argument("--national", default=None,
                         help="With --metros: the National release from the SAME year, whose "
                              "all-occupations median becomes the wage index's denominator. "
                              "Recommended -- without it the hardcoded "
                              "NATIONAL_ALL_OCCUPATIONS_MEDIAN is used instead.")
    parser.add_argument("-o", "--output", default=None,
                         help="Output CSV path (default: cleaned_careers.csv, or "
                              "cleaned_careers_<state>.csv when --state is given)")
    args = parser.parse_args()
    if args.metros and args.state:
        raise SystemExit("Error: --metros and --state are different releases; pass one or the other.")
    if args.national and not args.metros:
        raise SystemExit("Error: --national only applies to --metros (it supplies the wage index's denominator).")
    output_path = args.output or (
        "data/metro_careers_clean.csv" if args.metros else
        f"cleaned_careers_{args.state.lower()}.csv" if args.state else "cleaned_careers.csv"
    )

    try:
        if args.metros:
            print(f"Extracting {len(METRO_AREA_BY_CITY)} metro areas:")
            clean_df = build_metro_dataframe(args.input_xlsx)
            index_df = build_metro_wage_index(args.input_xlsx, national_xlsx=args.national)
        else:
            clean_df = build_clean_dataframe(args.input_xlsx, state=args.state)
    except FileNotFoundError:
        raise SystemExit(f"Error: could not find '{args.input_xlsx}'. Download it from bls.gov/oes/tables.htm.")
    except ValueError as exc:
        raise SystemExit(f"Error: {exc}")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    clean_df.to_csv(output_path, index=False)
    print(f"Wrote {len(clean_df)} rows to {output_path}")
    if args.metros:
        print(f"{clean_df.city.nunique()} cities, "
              f"{clean_df.occ_title.nunique()} distinct occupations, "
              f"median {clean_df.groupby('city').size().median():.0f} per city.")
        index_path = str(Path(output_path).with_name("metro_wage_index.csv"))
        index_df.to_csv(index_path, index=False)
        print(f"\nWrote {len(index_df)} rows to {index_path} "
              f"(all-occupations wage level vs the national ${NATIONAL_ALL_OCCUPATIONS_MEDIAN:,} median):")
        print(index_df.head(4).to_string(index=False))
        print(index_df.tail(2).to_string(index=False))
        vintage = release_vintage(args.input_xlsx) or "this"
        print(f"\nRegenerate cleaned_careers.csv and cleaned_careers_ca.csv from {vintage}'s "
              f"National/State releases too -- app.py compares them against these metro wages.")
    else:
        print_summary(clean_df)

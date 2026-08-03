"""
Add BLS "Typical Education Needed for Entry" to a Cleaned Careers CSV
--------------------------------------------------------------------------
Joins BLS Employment Projections' real per-occupation education-requirement
data -- Table 1.2 "Occupational projections... and worker characteristics",
part of the comprehensive occupation.xlsx release (download at
bls.gov/emp/data/occupational-data.htm, or directly at
bls.gov/emp/ind-occ-matrix/occupation.xlsx) -- into an existing
cleaned_careers.csv/cleaned_careers_ca.csv (produced by data_pipeline.py),
adding one new column: typical_education.

occupation.xlsx ships as a multi-sheet workbook (Table 1.1, 1.2, 1.3, ...),
and Table 1.2 itself has a merged title row before the real header row, and
mixes "Summary" rows (major/minor/broad occupation groups, e.g. code
"11-0000") in with the "Line item" rows that are real detailed occupations
(e.g. "11-1011") -- only "Line item" rows carry a real
typical-education-needed-for-entry value and match cleaned_careers.csv's
occ_code granularity, so summary rows are filtered out here the same way
data_pipeline.py already filters raw OEWS releases to o_group == "detailed".

This is a separate, small script rather than a data_pipeline.py flag
because the original raw BLS wage XLSX files used to build the existing
cleaned CSVs aren't kept locally (only their cleaned output is) -- the wage
numbers aren't changing, so there's no need to re-download and re-clean
them just to add one new column. This script instead joins straight onto
the already-cleaned CSV, which already carries occ_code at the same
6-digit SOC granularity the education file is keyed by.

Typical education needed for entry doesn't vary by state, so the same
national education file is joined against both the national and CA
cleaned CSVs.

Usage:
    python add_education_field.py cleaned_careers.csv occupation.xlsx -o cleaned_careers.csv
    python add_education_field.py cleaned_careers_ca.csv occupation.xlsx -o cleaned_careers_ca.csv
"""

import argparse

import pandas as pd

# Exact-match candidates for the SOC code column, checked first.
OCC_CODE_COLUMN_CANDIDATES = ["occ_code", "soc_code", "code"]

# BLS's real header for this is "20XX National Employment Matrix code" --
# the year changes every release, so this is matched as a substring of the
# normalized (lowercased, spaces->underscores) column name instead of an
# exact guess.
OCC_CODE_COLUMN_SUBSTRINGS = ["national_employment_matrix_code"]

# Exact-match candidates for the education-requirement column. BLS's real
# header for this ("Typical education needed for entry") normalizes to the
# first candidate below.
EDUCATION_COLUMN_CANDIDATES = [
    "typical_education_needed_for_entry", "typical_entry_level_education",
    "entry_level_education", "typical_education", "education",
]

OCCUPATION_TYPE_COLUMN_CANDIDATES = ["occupation_type"]

# Only "Line item" rows are real detailed occupations with their own
# typical-education value -- "Summary" rows are major/minor/broad
# aggregates (e.g. "Management occupations", code "11-0000") that don't
# correspond to any single occ_code in cleaned_careers.csv.
DETAILED_OCCUPATION_TYPE_VALUE = "line item"

# BLS marks "not applicable" with an em dash in these columns instead of
# leaving the cell blank -- treated the same as a true missing value.
MISSING_VALUE_MARKERS = {"—", "-", "nan", "none", ""}

# How many leading rows to scan for the real header row before giving up --
# BLS's exported tables put one merged title row before the header today,
# but this allows a little slack for a future release adding more.
MAX_HEADER_SCAN_ROWS = 10


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [
        str(c).strip().lower().replace(" ", "_").replace(",", "").replace("–", "_").replace("-", "_")
        for c in df.columns
    ]
    return df


def find_column(df: pd.DataFrame, candidates: list, substrings: list, what: str) -> str:
    """Return whichever candidate column name is present in this file,
    falling back to a substring match (for columns like "typical education
    needed for entry" whose header embeds a release year). Raises
    ValueError (with the real column list) instead of silently matching on
    a column that doesn't exist."""
    for candidate in candidates:
        if candidate in df.columns:
            return candidate
    for col in df.columns:
        if any(s in col for s in substrings):
            return col
    raise ValueError(
        f"Could not find a {what} column among {candidates} "
        f"(or containing {substrings}). Found columns: {list(df.columns)}"
    )


def find_header_row(raw: pd.DataFrame, marker: str = "typical education needed for entry") -> int:
    """Locate the real header row by content rather than a hardcoded row
    index, since BLS's exported tables put a merged title row above it."""
    for i in range(min(MAX_HEADER_SCAN_ROWS, len(raw))):
        row_values = [str(v).strip().lower() for v in raw.iloc[i].values]
        if any(marker in v for v in row_values):
            return i
    raise ValueError(f"Could not find a header row containing '{marker}' in the first {MAX_HEADER_SCAN_ROWS} rows.")


def load_education_data(xlsx_path: str, sheet_name: str = None) -> pd.DataFrame:
    """Read the BLS education XLSX, auto-detecting the sheet (preferring
    "Table 1.2" if present, matching occupation.xlsx's real multi-sheet
    layout) and the real header row (skipping the merged title row above
    it), then filter down to real detailed occupations."""
    xls = pd.ExcelFile(xlsx_path)
    if sheet_name:
        sheet_candidates = [sheet_name]
    elif "Table 1.2" in xls.sheet_names:
        sheet_candidates = ["Table 1.2"]
    else:
        sheet_candidates = xls.sheet_names

    last_error = None
    for sheet in sheet_candidates:
        try:
            raw = pd.read_excel(xlsx_path, sheet_name=sheet, header=None)
            header_row = find_header_row(raw)
            df = normalize_columns(pd.read_excel(xlsx_path, sheet_name=sheet, header=header_row))
            occ_code_col = find_column(df, OCC_CODE_COLUMN_CANDIDATES, OCC_CODE_COLUMN_SUBSTRINGS, "SOC/occupation code")
            education_col = find_column(df, EDUCATION_COLUMN_CANDIDATES, [], "typical education")
        except ValueError as exc:
            last_error = exc
            continue

        rename_map = {occ_code_col: "occ_code", education_col: "typical_education"}
        occ_type_col = next((c for c in OCCUPATION_TYPE_COLUMN_CANDIDATES if c in df.columns), None)
        if occ_type_col:
            rename_map[occ_type_col] = "occupation_type"
        result = df[list(rename_map.keys())].rename(columns=rename_map)

        if "occupation_type" in result.columns:
            result = result[
                result["occupation_type"].astype(str).str.strip().str.lower() == DETAILED_OCCUPATION_TYPE_VALUE
            ].drop(columns=["occupation_type"])

        result["typical_education"] = result["typical_education"].astype(str).str.strip()
        result.loc[result["typical_education"].str.lower().isin(MISSING_VALUE_MARKERS), "typical_education"] = ""
        result["occ_code"] = result["occ_code"].astype(str).str.strip()
        return result.reset_index(drop=True)

    raise ValueError(f"Could not find the required columns in any sheet of '{xlsx_path}'. Last error: {last_error}")


def add_education_column(cleaned_csv_path: str, education_xlsx_path: str, sheet_name: str = None) -> pd.DataFrame:
    """Left-join typical_education onto an existing cleaned careers CSV by
    occ_code. Rows with no match get "" (empty), not dropped and not
    guessed -- every original row is preserved exactly."""
    careers_df = pd.read_csv(cleaned_csv_path)
    if "occ_code" not in careers_df.columns:
        raise ValueError(
            f"'{cleaned_csv_path}' has no occ_code column -- pass a CSV "
            f"produced by data_pipeline.py, not a raw BLS release."
        )
    careers_df["occ_code"] = careers_df["occ_code"].astype(str).str.strip()
    education_df = load_education_data(education_xlsx_path, sheet_name=sheet_name)

    # REFRESH, not only first-time add: the committed CSVs already carry these
    # columns from the previous run, and merging them in again produced
    # typical_education_x/_y and a KeyError -- the documented in-place refresh
    # (`-o cleaned_careers.csv`) had been broken since the first run's output
    # was committed. The stale columns are dropped and rebuilt from the
    # education file being passed in, which is the refresh semantics the
    # docstring promises.
    careers_df = careers_df.drop(columns=[
        c for c in ("typical_education", "occupation_type")
        if c in careers_df.columns])

    before = len(careers_df)
    merged = careers_df.merge(education_df, on="occ_code", how="left")
    merged["typical_education"] = merged["typical_education"].fillna("")
    assert len(merged) == before, "Left-join must preserve every original row."
    return merged


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Add BLS's typical-education-needed-for-entry field to a cleaned careers CSV."
    )
    parser.add_argument("cleaned_csv", help="Path to an existing cleaned_careers*.csv (from data_pipeline.py)")
    parser.add_argument("education_xlsx", help="Path to BLS's occupation.xlsx (bls.gov/emp/ind-occ-matrix/occupation.xlsx)")
    parser.add_argument("--sheet", default=None,
                         help="Sheet name to read (default: auto-detect, preferring \"Table 1.2\")")
    parser.add_argument("-o", "--output", default=None,
                         help="Output CSV path (default: overwrite cleaned_csv in place)")
    args = parser.parse_args()
    output_path = args.output or args.cleaned_csv

    try:
        result_df = add_education_column(args.cleaned_csv, args.education_xlsx, sheet_name=args.sheet)
    except FileNotFoundError as exc:
        raise SystemExit(f"Error: could not find file: {exc}")
    except ValueError as exc:
        raise SystemExit(f"Error: {exc}")

    matched = int((result_df["typical_education"] != "").sum())
    # A zero-match join is not a result, it is a broken key: a release that
    # reformats occ_code would print "0 matched" and exit 0, after which
    # app.py silently charges UNDERGRAD_YEARS for every occupation (program
    # length falls back when typical_education is empty). Refuse to write.
    if matched == 0:
        raise SystemExit(
            f"Error: 0 of {len(result_df)} occupation codes matched the education "
            f"file -- the join key is broken (occ_code format change?). Refusing "
            f"to overwrite {output_path} with an all-empty typical_education column."
        )
    result_df.to_csv(output_path, index=False)
    print(f"Wrote {len(result_df)} rows to {output_path} ({matched} matched a typical_education value, "
          f"{len(result_df) - matched} unmatched/kept as \"\").")

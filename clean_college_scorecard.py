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

WHICH RELEASE THE COMMITTED DATASET IS. data/college_coa_clean.csv holds
College Scorecard DATA YEAR 2024. Nothing recorded that until 2026-08-14 and
the file name ("Most-Recent-Cohorts") deliberately carries no year, so it had
to be recovered from the data: every committed in_state_coa was compared
against the API's year-prefixed cost fields for the same UNITID. Seven schools
across two samples matched 2024.cost.attendance.academic_year to the dollar
and matched no other year from 2018 to 2023; the API's `latest` alias returns
the same figure. (A wider sweep hits api.data.gov's rate limit at HTTP 429,
which is why the sample is small rather than exhaustive.)

That data year comes from the RELEASE OF JUNE 10, 2026, which is the current
one as of 2026-08-14 (collegescorecard.ed.gov/data, "last updated June 10,
2026"). Two independent traces agree: the API's `latest` alias returns the same
cost figures as data year 2024, and the field-of-study download this repo pins
elsewhere is Most-Recent-Cohorts-Field-of-Study_06102026.zip, the same release.
So the committed dataset is CURRENT, not stale, and the 2024 data year is
simply how far the cost fields reach in it.

Do not confuse the two numbers. The release is June 2026; the cost data inside
it describes 2024. The footer cites the data year, because that is what the
figures describe.

Re-run that check after any regeneration and update the year here, because the
site footer cites it and "Most Recent" silently means something different every
release.

Usage:
    python clean_college_scorecard.py path/to/Most-Recent-Cohorts-Institution.csv
    python clean_college_scorecard.py raw.csv -o college_coa_clean.csv
"""

import argparse
import re
import sys
from pathlib import Path

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
    # ---- Added for the app's budget-first school search ----
    "UNITID",          # IPEDS id. The stable key: 67 INSTNM values are shared by
                       # 153 different institutions (e.g. two "Southwestern
                       # College", $22k apart), so the name alone cannot identify
                       # a school and the app pins search results by this instead.
    "CITY",            # Disambiguates those same-named schools for a reader, and
                       # is the only place city appears -- the API has it, the
                       # committed dataset previously did not.
    "CURROPER",        # 1 = currently operating. Carried but NOT filtered on here
                       # (see build_clean_dataframe): filtering would change the
                       # row set every existing app behaviour is built on. The
                       # search excludes closed schools at query time instead.
    "DISTANCEONLY",    # 1 = institution teaches exclusively at a distance. Kept at
                       # institution level; per-PROGRAM distance-only is encoded in
                       # the CIP flags as value 2 and deliberately not preserved.
    "ADM_RATE",        # Undergraduate admission rate. Sparse BY CONSTRUCTION --
                       # open-admission schools report none -- so it is displayed
                       # where present and never ranked or filtered on. ~36%
                       # overall, but 87% among bachelor's-predominant schools.
    # ---- Parent PLUS, for disclosure only ----
    # The student's own median debt is still fetched live (see app.py's note by
    # COA_DATASET_PATH); this is the PARENT's borrowing for the same school and
    # has no live equivalent the app already calls, so it rides in the local
    # dataset where it costs no API request.
    #
    # It exists because the student figure alone understates what a family
    # takes on, and by wildly varying amounts: Berkeley completers borrow a
    # median $13,000 while Berkeley parents who took PLUS borrowed $29,590,
    # and at NYU it is $20,500 against $88,733. Student debt across schools
    # spans $5,858-$40,621 -- Parent PLUS spans $3,182-$164,776, because it
    # has no aggregate cap.
    #
    # THE COMP VARIANT, deliberately: it covers COMPLETERS, matching the
    # student median the app shows beside it. The plain PLUS_DEBT_INST_MD
    # mixes completers and dropouts and would not be comparable.
    #
    # NEVER add this to a loan amount. It is the parent's debt, is not
    # income-driven-eligible for the student, and app.py already models
    # Parent PLUS as a separate non-forgivable pool in split_loan_financing.
    # ---- What people actually pay, and whether they finish ----
    #
    # Both are DISPLAY-ONLY, like Parent PLUS above. The search filters and
    # sorts on the STICKER cost and must keep doing so: net price below is an
    # average for AIDED students only, so using it as the affordability test
    # would quietly promise a discount not everyone gets. Sticker is the honest
    # ceiling; these two sit beside it and say what usually happens.
    #
    # NET PRICE is the biggest single correction this dataset can make to the
    # app's headline number. Sticker runs a median $7,063/yr above it, and at
    # 1,388 of 5,035 schools the net price is less than HALF the sticker. The
    # app has always told visitors to go and run a net price calculator; the
    # figure was in this file's source all along.
    #
    # Two columns because Scorecard splits them by sector and never populates
    # both: NPT4_PUB for public institutions, NPT4_PRIV for private. The app
    # coalesces them.
    "NPT4_PUB",        # Avg annual net price, public, Title IV aid recipients
    "NPT4_PRIV",       # Avg annual net price, private, same basis
    #
    # COMPLETION is the assumption the ROI model never states. It charges a
    # full programme and pays out a graduate's salary; a school where 28% of
    # students finish is a different financial proposition from one where 90%
    # do, and until now the two rendered identically. 578 schools in this file
    # graduate under 30%.
    #
    # C150 is "completed within 150% of normal time" -- six years for a
    # four-year degree, three for a two-year one -- for FIRST-TIME, FULL-TIME
    # students only. A student who transfers in, or out and finishes elsewhere,
    # counts against the school here. That undercounts real completion and the
    # caption has to say so. Two columns again, by institution length, never
    # both populated.
    "C150_4",          # Completion rate, 4-year institutions
    "C150_L4",         # Completion rate, less-than-4-year institutions
    "PLUS_DEBT_INST_COMP_MD",   # Median Parent PLUS debt, completers
    "PLUS_DEBT_INST_COMP_N",    # How many families that median describes --
                                # required, because the median is conditional
                                # on having borrowed PLUS at all. Showing the
                                # dollar figure without the count invites
                                # reading it as "what parents here pay".
]

# Credential levels in the CIP flag columns, ordered least to most advanced.
# Each pairs with a 2-digit family code as CIP<family><LEVEL>, e.g. CIP11BACHL
# = "does this school award a bachelor's in Computer & Information Sciences".
CIP_CREDENTIAL_LEVELS = ["CERT1", "CERT2", "ASSOC", "CERT4", "BACHL"]

# Matches only the family flags (CIP11BACHL), not CIPCODE1/CIPTITLE1/CIPTFBS1 --
# those are a different, 63%-null series and mixing them in would produce
# inconsistent coverage.
CIP_FLAG_PATTERN = re.compile(r"^CIP(\d{2})(" + "|".join(CIP_CREDENTIAL_LEVELS) + r")$")

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


def discover_cip_columns(csv_path: str) -> dict:
    """{level: [(family_code, column_name), ...]} read from the file's own header.

    Discovered rather than hard-coded: the list is 190 columns wide and the set
    of CIP families can change between Scorecard releases. Hand-listing them
    would rot silently -- a family dropped from a future release would just stop
    appearing in search results with nothing saying so.

    Exits loudly if a release has no CIP flags at all. Writing empty program
    lists instead would produce a dataset that loads fine and returns zero
    matches for every search, which reads as "no school teaches this" rather
    than "the pipeline was run against the wrong file".
    """
    header = pd.read_csv(csv_path, nrows=0).columns
    by_level = {level: [] for level in CIP_CREDENTIAL_LEVELS}
    for column in header:
        match = CIP_FLAG_PATTERN.match(column)
        if match:
            by_level[match.group(2)].append((match.group(1), column))
    missing = [level for level, cols in by_level.items() if not cols]
    if missing:
        sys.exit(
            f"ERROR: no CIP program columns found for credential level(s) {missing} "
            f"in {csv_path}.\nExpected columns like CIP11BACHL. This release either "
            "predates the field-of-study flags or is the wrong file -- the "
            "institution-level 'Most-Recent-Cohorts-Institution.csv' is the one "
            "that carries them."
        )
    return {level: sorted(cols) for level, cols in by_level.items()}


def load_scorecard_data(csv_path: str) -> pd.DataFrame:
    """Read the raw Scorecard CSV and coerce the columns we need to numeric."""
    cip_columns = discover_cip_columns(csv_path)
    flat_cip = [col for cols in cip_columns.values() for _, col in cols]
    df = pd.read_csv(
        csv_path,
        usecols=COLUMNS_TO_LOAD + flat_cip,
        na_values=NA_VALUES,
        low_memory=False,  # avoids dtype-guessing warnings on a very wide file
    )
    numeric_columns = ["CONTROL", "COSTT4_A", "COSTT4_P", "TUITIONFEE_IN",
                       "TUITIONFEE_OUT", "UNITID", "CURROPER", "DISTANCEONLY",
                       "ADM_RATE", "PLUS_DEBT_INST_COMP_MD", "PLUS_DEBT_INST_COMP_N",
                       "NPT4_PUB", "NPT4_PRIV", "C150_4", "C150_L4"]
    for column in numeric_columns:
        # errors="coerce" is a safety net: if any stray non-numeric text
        # slipped past NA_VALUES, it becomes NaN instead of crashing the script.
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df.attrs["cip_columns"] = cip_columns
    return df


def add_program_lists(df: pd.DataFrame, cip_columns: dict) -> pd.DataFrame:
    """Collapse 190 CIP flag columns into 5 pipe-delimited family lists.

    One column per credential level, e.g. programs_bachl = "11|14|26|45|51".
    The alternative -- writing all 190 flags -- would make a committed CSV
    unreviewable in a diff for no gain, since the app only ever asks "does this
    school teach family X at level Y".

    Flag values are 0 (not offered), 1 (offered), 2 (offered by distance
    education only). Both 1 and 2 count as offered: the school does teach it,
    and per-program delivery mode is a level of detail this search does not
    claim. Institution-wide DISTANCEONLY is carried separately for the rarer
    case where the whole school is online.
    """
    df = df.copy()
    for level, families in cip_columns.items():
        codes = [code for code, _ in families]
        flags = df[[col for _, col in families]].fillna(0).to_numpy() > 0
        df[f"programs_{level.lower()}"] = [
            "|".join(code for code, offered in zip(codes, row) if offered)
            for row in flags
        ]
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

    # ---- Step 5: coalesce the two sector/length pairs ----
    #
    # Scorecard splits both by institution type and never populates both sides,
    # so a consumer that reads only one silently loses half the dataset:
    # NPT4_PUB is blank at every private school, C150_4 at every two-year one.
    # Coalesced HERE rather than in the app for the same reason in_state_coa is
    # -- one rule, in the pipeline, so every reader gets the same answer and
    # the derivation is visible beside the raw columns it came from.
    df["net_price"] = df["NPT4_PUB"].fillna(df["NPT4_PRIV"])
    df["completion_rate"] = df["C150_4"].fillna(df["C150_L4"])

    return df


def build_clean_dataframe(csv_path: str) -> pd.DataFrame:
    """Load, clean, and return the final consolidated COA dataframe."""
    raw = load_scorecard_data(csv_path)
    calculated = calculate_coa(raw)
    calculated = add_program_lists(calculated, raw.attrs["cip_columns"])

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
    # The first eleven are the original schema and their ORDER AND CONTENT MUST
    # NOT CHANGE -- the app has read this file since before the search existed,
    # and a regeneration that shifts them would silently alter every existing
    # scenario. The search columns are appended after, never interleaved.
    final_columns = [
        "INSTNM", "STABBR", "CONTROL", "control_type",
        "in_state_coa", "out_of_state_coa",
        "COSTT4_A", "COSTT4_P", "TUITIONFEE_IN", "TUITIONFEE_OUT",
        "NPCURL",
        "UNITID", "CITY", "CURROPER", "DISTANCEONLY", "ADM_RATE",
    ] + [f"programs_{level.lower()}" for level in CIP_CREDENTIAL_LEVELS] + [
        # Appended last, after the program flags, for the same reason the
        # search columns were appended rather than interleaved.
        "PLUS_DEBT_INST_COMP_MD", "PLUS_DEBT_INST_COMP_N",
        # Appended after those, same rule again. The raw halves ride along
        # beside the coalesced value so the derivation can be checked without
        # re-reading a 100MB source file.
        "net_price", "NPT4_PUB", "NPT4_PRIV",
        "completion_rate", "C150_4", "C150_L4",
    ]
    return calculated[final_columns].reset_index(drop=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Clean College Scorecard data and calculate in-state/out-of-state cost of attendance."
    )
    parser.add_argument("input_csv", help="Path to the raw College Scorecard institution CSV")
    # data/, matching where app.py reads it (and where every other pipeline
    # writes). The old default was the repo root, so a run without -o
    # "succeeded" while the app stayed on the stale committed file.
    parser.add_argument("-o", "--output", default="data/college_coa_clean.csv",
                        help="Output CSV path (default: data/college_coa_clean.csv, "
                             "which is the file app.py reads)")
    args = parser.parse_args()

    clean_df = build_clean_dataframe(args.input_csv)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    clean_df.to_csv(args.output, index=False)
    print(f"Wrote {len(clean_df)} rows to {args.output}")

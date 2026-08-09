#!/usr/bin/env python3
"""Per-school GRADUATE tuition and fees, from IPEDS.

    python3 build_graduate_tuition.py ic2023_ay.csv HD2023.csv \\
        -o data/graduate_tuition_clean.csv

    IC{YYYY}_AY.zip and HD{YYYY}.zip come from
    https://nces.ed.gov/ipeds/datacenter/data/ -- take both from the SAME
    year, and note that the charges file lags the directory (HD2024 was
    posted while IC2024_AY was still 404).

WHY IPEDS AND NOT COLLEGE SCORECARD. Scorecard publishes no graduate cost of
any kind. COSTT4_A, COSTT4_P and every TUITIONFEE_* field in its institution
file are undergraduate figures, and the field-of-study file carries no cost
variable at all -- which is why build_professional_debt.py had to use debt
instead, and why the school search has never offered a graduate level. IPEDS
is the only federal source for what a graduate year actually costs, and its
UNITID is the same institution key Scorecard uses, so this joins cleanly to
both data/college_coa_clean.csv and data/graduate_debt_clean.csv.

WHAT THIS IS NOT -- and the naming follows from it.

**It is not a cost of attendance.** IPEDS publishes NO graduate living costs.
Every CHG*AY* variable in this same file -- books, food and housing, other
expenses -- is defined as "Price of attendance for full-time, FIRST-TIME
UNDERGRADUATE students". There is no graduate equivalent anywhere in IPEDS.

That distinction is worth about $13,214 a year, which is the median gap
between full cost of attendance and tuition alone in data/college_coa_clean.csv.
The app's undergraduate in_state_coa INCLUDES housing. Putting a tuition-only
graduate figure beside it under a shared "per year" heading would understate
graduate cost by roughly that much and invite a comparison that is simply
wrong. Hence `graduate_tuition_clean.csv`, hence `grad_tuition_*`, and hence
nothing here is ever called a COA. (IPEDS itself says "price of attendance",
and it is published price, not net price.)

**It is an institution-wide average, not a programme price.** TUITION6 averages
every graduate programme at the school, so an MBA and an MEd collapse into one
number. Business and law schools routinely charge differential tuition well
above their institution's average. This is the single biggest fidelity limit
here, and it is why an MBA gets its own per-programme figure in
build_professional_debt.py rather than being priced from this file.

**It assumes full-time.** TUITION6 is the annual charge for a student taking
9+ graduate credits. Part-time study is normal at this level, so HRCHG6/HRCHG7
(the per-credit-hour charge, reported by 92% of these schools) ride along for
a consumer that needs to price a part-time or per-credit programme honestly.

THE REPORTING FLAG IS NOT OPTIONAL. Missingness in TUITION6 is structural, not
random: of 3,825 institutions in the 2023-24 file, 2,019 report a graduate
figure and 1,806 are flagged 'A' for "not applicable" because they have no
graduate programmes at all. Reading the value without XTUIT6 conflates "this
school has no graduate school" with "we don't know", and both would arrive as
a gap. Every row written here was flagged 'R'.
"""
import argparse
import re
import sys
from pathlib import Path

import pandas as pd

# IPEDS ships these as UTF-8 with a BOM. Read as latin-1 or cp1252 and the
# first column arrives named "﻿UNITID", so the join key silently is not
# there -- pandas raises nothing, the merge just finds no UNITID column.
# utf-8-sig strips the BOM and reads every accented institution name cleanly;
# verified against all 6,163 HD2023 rows with no replacement characters.
ENCODING = "utf-8-sig"

# Same 1/2/3 encoding College Scorecard uses, so the labels match the rest of
# the repo exactly -- clean_college_scorecard.py:113. IPEDS adds -3 for "not
# reported", which lands on Unknown like any unmapped value.
CONTROL_LABELS = {1: "Public", 2: "Private Non-Profit", 3: "Private For-Profit"}

# The graduate side of IPEDS's residency triplet. 1/2/3 are the same three
# tiers for undergraduates and are deliberately not read here.
#
# 5 (in-district) is dropped on purpose: it equals 6 (in-state) for 2,018 of
# the 2,019 reporting institutions, so carrying it would add a column that
# says nothing and a third residency case for every consumer to handle.
GRAD_CHARGE_COLUMNS = {
    "grad_tuition_in": "TUITION6",
    "grad_tuition_out": "TUITION7",
    "grad_fees_in": "FEE6",
    "grad_fees_out": "FEE7",
    "grad_hrchg_in": "HRCHG6",
    "grad_hrchg_out": "HRCHG7",
}
# The imputation flag guarding each side. 'R' = reported by the institution.
RESIDENCY_FLAGS = {"in": "XTUIT6", "out": "XTUIT7"}

HD_COLUMNS = ["UNITID", "INSTNM", "CITY", "STABBR", "CONTROL", "HLOFFER"]

# IPEDS's own code for "offers postbaccalaureate work or above" -- postbacc
# certificate (6), master's (7), post-master's certificate (8), doctorate (9).
# Used only to report coverage, never to filter: a school that reports a
# graduate tuition is a school with graduate students, whatever HLOFFER says.
POSTBACC_HLOFFER = 6

# IPEDS's own threshold for a full-time graduate student, from the TUITION6
# definition: "a student enrolled for 9 or more semester credits". Used to
# catch schools that filed a per-credit rate in the annual field -- see build().
FULL_TIME_GRAD_CREDITS = 9

OUTPUT_COLUMNS = [
    "UNITID", "INSTNM", "CITY", "STABBR", "control_type",
    "grad_tuition_in", "grad_tuition_out",
    "grad_fees_in", "grad_fees_out",
    "grad_tuition_fees_in", "grad_tuition_fees_out",
    "grad_hrchg_in", "grad_hrchg_out",
    "ipeds_year",
]


def release_year(path: str) -> int:
    """The collection year out of an IPEDS filename (ic2023_ay.csv -> 2023).

    The filename is the only place the vintage lives: IPEDS's own columns
    carry no year field, exactly like the OEWS workbooks release_vintage()
    parses in data_pipeline.py. That makes a mismatch invisible at read time
    and permanent once it is in a committed CSV, which is why this exits
    rather than defaulting -- an unstamped year is how a stale file gets
    mistaken for a price change.
    """
    match = re.search(r"IC(\d{4})_AY", Path(path).stem, flags=re.IGNORECASE)
    if not match:
        sys.exit(
            f"ERROR: cannot read a collection year from {path!r}.\n"
            "Expected a name like IC2023_AY.csv. The year is not inside the "
            "file, so it cannot be recovered if the name is lost -- rename the "
            "file back rather than removing this check."
        )
    return int(match.group(1))


def require_columns(header, needed, path: str, what: str) -> None:
    """Exit loudly when a release stops publishing something this depends on.

    The alternative is a column of NaNs that survives every downstream step
    and lands in the app as "this school has no graduate tuition" -- a wrong
    ANSWER rather than an error, and indistinguishable from a school that
    genuinely has no graduate programmes.
    """
    missing = [column for column in needed if column not in header]
    if missing:
        sys.exit(
            f"ERROR: {path} is missing {what}: {', '.join(missing)}.\n"
            "Either this is the wrong file, or IPEDS renamed the variables. "
            "Check the dictionary in IC{YYYY}_AY_Dict.zip before editing the "
            "column lists in this script -- a silent rename is exactly what "
            "this check exists to catch."
        )


def load_charges(path: str) -> pd.DataFrame:
    """The graduate charge columns plus their reporting flags, unfiltered."""
    header = pd.read_csv(path, nrows=0, encoding=ENCODING).columns
    require_columns(header, ["UNITID"], path, "the join key")
    require_columns(header, sorted(GRAD_CHARGE_COLUMNS.values()), path,
                    "graduate charge columns")
    require_columns(header, sorted(RESIDENCY_FLAGS.values()), path,
                    "graduate tuition reporting flags")
    return pd.read_csv(
        path, encoding=ENCODING, low_memory=False,
        usecols=["UNITID"] + sorted(GRAD_CHARGE_COLUMNS.values())
                + sorted(RESIDENCY_FLAGS.values()))


def load_directory(path: str) -> pd.DataFrame:
    """Institution name, place and sector.

    A separate file because this dataset must stand ALONE. 238 of the
    institutions awarding a graduate degree have no row in
    college_coa_clean.csv at all -- that file drops any school without an
    undergraduate cost of attendance, which is every graduate-only school
    (Icahn, Mayo). Joining names from there instead would lose exactly the
    schools this dataset exists to reach.
    """
    header = pd.read_csv(path, nrows=0, encoding=ENCODING).columns
    require_columns(header, HD_COLUMNS, path, "directory columns")
    return pd.read_csv(path, encoding=ENCODING, low_memory=False,
                       usecols=HD_COLUMNS)


def clean_charges(charges: pd.DataFrame) -> pd.DataFrame:
    """Numeric charges, gated on the reporting flag, with zero read as absent."""
    out = pd.DataFrame({"UNITID": charges["UNITID"]})
    for name, source in GRAD_CHARGE_COLUMNS.items():
        side = "in" if name.endswith("_in") else "out"
        value = pd.to_numeric(charges[source], errors="coerce")
        # A 0 in IPEDS means the charge does not apply, not that the programme
        # is free. Left as 0 it would sort to the top of any cheapest-first
        # list as the most affordable graduate school in the country.
        value = value.where(value > 0)
        # And a figure the institution never reported is not a figure. The
        # flag is per-residency, so out-of-state is gated independently -- a
        # school can report in-state and leave out-of-state blank.
        reported = charges[RESIDENCY_FLAGS[side]] == "R"
        out[name] = value.where(reported)
    for side in ("in", "out"):
        # The figure to price with, resolved ONCE here rather than by each
        # consumer -- the resolve_professional_debt discipline. Fees are
        # additive and frequently absent, so a missing fee must not annihilate
        # a reported tuition; the components stay separate above so the sum
        # can always be audited.
        tuition = out[f"grad_tuition_{side}"]
        fees = out[f"grad_fees_{side}"].fillna(0)
        out[f"grad_tuition_fees_{side}"] = (tuition + fees).where(tuition.notna())
    return out


def build(charges_path: str, directory_path: str) -> tuple:
    year = release_year(charges_path)
    charges = clean_charges(load_charges(charges_path))
    directory = load_directory(directory_path)

    merged = directory.merge(charges, on="UNITID", how="left")
    merged["control_type"] = merged["CONTROL"].map(CONTROL_LABELS).fillna("Unknown")
    merged["ipeds_year"] = year

    # Drop at BUILD time, the way build_professional_debt.py drops suppressed
    # debt rows, so the app can never see a NaN here and read it as free.
    # In-state is the required figure: out-of-state is genuinely absent for
    # schools that charge one price, and dropping on it would delete most
    # private universities.
    out = merged[merged["grad_tuition_in"].notna()].copy()

    # Some institutions file a PER-CREDIT rate in the annual field. The
    # signature is unmistakable once you look: 6 schools report an annual
    # figure exactly equal to their own per-credit charge, and their median
    # "year" is $995 against $13,140 for everyone else. Thomas Jefferson
    # School of Law at $1,200 a year is the clearest tell.
    #
    # The test is IPEDS's own definition rather than a dollar floor picked
    # here: TUITION6 is defined for a full-time graduate student, and IPEDS
    # defines full-time graduate as 9+ credits. So an annual figure below
    # 9 x the school's own per-credit rate contradicts the variable it is
    # stored in. That is a data error, not a cheap school.
    #
    # It matters because this dataset exists to be sorted cheapest-first:
    # left in, every one of these lands at the top of the list, which is the
    # most visible position the tool has.
    misfiled = (out["grad_hrchg_in"].notna()
                & (out["grad_tuition_in"] < out["grad_hrchg_in"] * FULL_TIME_GRAD_CREDITS))
    out = out[~misfiled].copy()
    out.attrs["misfiled_dropped"] = int(misfiled.sum())
    # 177834.0 does not join 177834.
    out["UNITID"] = out["UNITID"].astype("Int64")
    out = out.sort_values("UNITID").reset_index(drop=True)
    return out[OUTPUT_COLUMNS], merged


def summarise(out: pd.DataFrame, merged: pd.DataFrame) -> None:
    """What the run actually found. This summary is the deliverable -- it is
    the evidence for whether a graduate school search is worth building."""
    print(f"\ngraduate tuition {out['ipeds_year'].iloc[0]}: {len(out):,} schools written")
    dropped = out.attrs.get("misfiled_dropped", 0)
    if dropped:
        print(f"  dropped, annual figure below {FULL_TIME_GRAD_CREDITS} x their own "
              f"per-credit rate: {dropped}")
    # Named rather than hidden: no per-credit rate means no principled floor to
    # test against, so anything this low survives and should be looked at.
    tiny = out[out["grad_tuition_fees_in"] < 100]
    for _, row in tiny.iterrows():
        print(f"  NOTE ${row['grad_tuition_fees_in']:,.0f} at {row['INSTNM']} "
              f"-- no per-credit rate to check it against")

    offers_grad = merged["HLOFFER"] >= POSTBACC_HLOFFER
    covered = merged.loc[offers_grad, "grad_tuition_in"].notna().sum()
    print(f"  institutions offering postbacc or above : {offers_grad.sum():,}")
    print(f"  ...of those, with a tuition figure       : {covered:,} "
          f"({covered / max(offers_grad.sum(), 1):.0%})")

    in_state = out["grad_tuition_fees_in"]
    out_state = out["grad_tuition_fees_out"]
    print(f"  in-state tuition+fees   median ${in_state.median():,.0f}  "
          f"range ${in_state.min():,.0f}-${in_state.max():,.0f}")
    both = out[out_state.notna()]
    differential = both[both["grad_tuition_fees_out"] > both["grad_tuition_fees_in"]]
    print(f"  report an out-of-state figure            : {len(both):,}")
    print(f"  ...charging more than in-state           : {len(differential):,} "
          f"(median premium ${(differential['grad_tuition_fees_out'] - differential['grad_tuition_fees_in']).median():,.0f})")
    print(f"  per-credit-hour charge available         : {out['grad_hrchg_in'].notna().sum():,}")

    # The reason this file exists rather than a join onto the undergraduate one.
    try:
        undergrad = set(pd.read_csv("data/college_coa_clean.csv")["UNITID"])
        gained = set(out["UNITID"].dropna().astype(int)) - undergrad
        print(f"  schools absent from college_coa_clean    : {len(gained):,} "
              f"(graduate-only; a join onto that file would lose them)")
    except (FileNotFoundError, KeyError):
        pass

    try:
        debt = pd.read_csv("data/graduate_debt_clean.csv")
        grad_schools = set(debt[debt["credential"].isin(["master", "doctoral"])]["UNITID"])
        priced = grad_schools & set(out["UNITID"].dropna().astype(int))
        print(f"  master's/doctoral schools in the debt data: {len(grad_schools):,}")
        print(f"  ...now also carrying a tuition figure     : {len(priced):,} "
              f"({len(priced) / max(len(grad_schools), 1):.0%})")
    except (FileNotFoundError, KeyError):
        pass
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("charges", help="IC{YYYY}_AY.csv from IPEDS")
    parser.add_argument("directory", help="HD{YYYY}.csv from IPEDS, same year")
    parser.add_argument("-o", "--output", default="data/graduate_tuition_clean.csv")
    args = parser.parse_args()

    out, merged = build(args.charges, args.directory)
    if out.empty:
        sys.exit("ERROR: no school reported a graduate tuition. Wrong file?")
    out.to_csv(args.output, index=False)
    summarise(out, merged)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

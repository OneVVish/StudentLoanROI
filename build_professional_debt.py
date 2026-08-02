#!/usr/bin/env python3
"""Per-school professional-school debt, from College Scorecard field-of-study data.

    python3 build_professional_debt.py Most-Recent-Cohorts-Field-of-Study.csv \\
        -o data/professional_debt_clean.csv

app.py charges medical, dental and law school as ONE national figure each --
$205,000 / $293,900 / $130,000 -- so every doctor costs the same to train
regardless of where they train. They do not. In this release medical school
debt runs from $47,503 to $330,479 depending on the school: a seven-fold
spread, wider than any input the app's sidebar exposes.

WHY DEBT AND NOT COST OF ATTENDANCE. There is no graduate cost-of-attendance
anywhere in College Scorecard. COSTT4_A / COSTT4_P / TUITIONFEE_IN /
TUITIONFEE_OUT in the institution file are all undergraduate figures, and the
field-of-study file carries no cost variable at all. What it does carry is
cumulative debt at graduation, and the documentation is explicit that it is
scoped the way this app needs:

    "The cumulative loan debt only includes loans disbursed at the same
     academic level (i.e., graduate, undergraduate) as the evaluated
     credential level."

So a First Professional figure is graduate borrowing only, excluding the
borrower's undergraduate loans -- exactly the contract of
`additional_training_debt`, which app.py adds ON TOP of the undergraduate loan.
It measures what people actually borrowed rather than what a school charges,
which is arguably the better input: it is already net of scholarships and
family money.

WHAT IT IS NOT. The figure includes Grad PLUS, which OBBBA abolished for
graduate and professional borrowers from 2026-07-01. Every median here
therefore describes borrowing a student starting now CANNOT replicate
federally: 43% of medical schools and 78% of dental schools sit above the
$200,000 federal aggregate that replaced it, and the excess is private money.
That is a disclosure problem for app.py, not a reason to prefer a national
average that hides it. It is also pooled across award years and is a few years
old, so treat it as a reliable RELATIVE signal between schools and a rough
absolute one.

Source: collegescorecard.ed.gov/data -> "Most Recent Data by Field of Study".
The download host is ed-public-download.scorecard.network (the older
app.cloud.gov host now 404s), and the filename carries the release date.
"""
import argparse
import sys

import pandas as pd

# CREDLEV 7 is "First Professional Degree" -- verified against CREDDESC in the
# release, alongside 5 Master's and 6 Doctoral. The three programs this app
# models are all First Professional; a Master's-level path would need its own
# entry here AND its own program length in app.py, never a silent default.
FIRST_PROFESSIONAL = 7

# 4-digit CIP -> the key app.py uses. CIPDESC is carried only to verify the
# code still means what we think: these are stable, but a silent CIP
# reassignment between releases would otherwise swap medicine for something
# else without anything failing.
PROGRAMS = {
    "5112": ("medicine", "Medicine."),
    "2201": ("law", "Law."),
    "5104": ("dentistry", "Dentistry."),
}

# Median cumulative debt for Direct Subsidized/Unsubsidized plus Grad PLUS,
# counting only loans originated AT the evaluated institution (EVAL). The ANY
# variant includes debt from every institution the student attended, which
# would double-count the undergraduate school app.py already charges
# separately.
DEBT_COLUMN = "DEBT_ALL_STGP_EVAL_MDN"
PAYMENT_COLUMN = "DEBT_ALL_STGP_EVAL_MDN10YRPAY"

# "PS" is what this file actually uses for a privacy-suppressed cell -- 1,329
# of them at CREDLEV 7 -- not the "PrivacySuppressed" spelling the institution
# file uses and clean_college_scorecard.py handles. Getting this wrong does not
# raise; it silently turns every suppressed school into a dropped row or a
# parse error much later.
NA_VALUES = ["PS", "PrivacySuppressed", "NULL", ""]

# CONTROL is already a LABEL in the field-of-study file ("Public", "Private,
# nonprofit", "Private, for-profit", "Foreign") -- unlike the institution file,
# where it is the integer 1/2/3 that clean_college_scorecard.py maps. Mapping
# it again turns every row into "Unknown", which is how this was caught: the
# column was silently uniform rather than wrong-looking. Normalised only so the
# wording matches the undergraduate dataset's control_type.
CONTROL_LABELS = {
    "Public": "Public",
    "Private, nonprofit": "Private Non-Profit",
    "Private, for-profit": "Private For-Profit",
    "Foreign": "Foreign",
}

OUTPUT_COLUMNS = [
    "UNITID", "INSTNM", "CONTROL", "control_type",
    "CIPCODE", "CREDLEV", "program_key", "debt_median", "debt_10yr_payment",
]


def load_raw(path: str) -> pd.DataFrame:
    usecols = ["UNITID", "INSTNM", "CONTROL", "CIPCODE", "CIPDESC",
               "CREDLEV", DEBT_COLUMN, PAYMENT_COLUMN]
    return pd.read_csv(
        path, usecols=usecols,
        # CIPCODE must stay a string: it is zero-padded ("0101"), and reading
        # it as a number drops the leading zero and stops matching.
        dtype={"CIPCODE": str},
        na_values=NA_VALUES, low_memory=False,
    )


def build(df: pd.DataFrame) -> pd.DataFrame:
    # A row with no UNITID cannot be looked up by the app, so it cannot be
    # offered in the picker.
    df = df[df["UNITID"].notna()]
    professional = df[df["CREDLEV"] == FIRST_PROFESSIONAL]
    rows = []
    for cip, (program_key, expected_desc) in PROGRAMS.items():
        block = professional[professional["CIPCODE"] == cip].copy()
        if block.empty:
            sys.exit(
                f"ERROR: CIP {cip} ({program_key}) has no First Professional rows in "
                f"this file.\nEither the release changed its CIP taxonomy or the wrong "
                f"file was passed. Refusing to write a dataset missing a program the "
                f"app charges debt for."
            )
        # Confirm the code still means what PROGRAMS claims.
        found = sorted(block["CIPDESC"].dropna().unique())
        if expected_desc not in found:
            sys.exit(
                f"ERROR: CIP {cip} was expected to be {expected_desc!r} but this file "
                f"describes it as {found}.\nRefusing to map it to {program_key!r}."
            )
        block["program_key"] = program_key
        rows.append(block)

    out = pd.concat(rows, ignore_index=True)
    out["debt_median"] = pd.to_numeric(out[DEBT_COLUMN], errors="coerce")
    out["debt_10yr_payment"] = pd.to_numeric(out[PAYMENT_COLUMN], errors="coerce")

    # Drop suppressed/missing here rather than in the app. A school with no
    # published figure must fall back to the national constant, and the
    # cleanest way to guarantee that is for the app never to see the row --
    # otherwise a NaN reaches the loan model, where it would sail through the
    # arithmetic and only surface as a rejected Supabase insert (PGRST102).
    before = len(out)
    out = out[out["debt_median"].notna()]
    dropped = before - len(out)

    out["control_type"] = out["CONTROL"].map(CONTROL_LABELS).fillna("Unknown")
    if (out["control_type"] == "Unknown").all():
        sys.exit("ERROR: every control_type resolved to Unknown -- CONTROL's encoding "
                 "changed. Refusing to write a column that is uniformly meaningless.")
    # UNITID arrives as float64 because the column has nulls file-wide. It is a
    # join key and an app.py lookup value, so 177834.0 would never match 177834.
    out["UNITID"] = out["UNITID"].astype("Int64")
    out = out.sort_values(["program_key", "INSTNM"])
    print(f"  suppressed or unpublished, dropped: {dropped}")
    return out[OUTPUT_COLUMNS]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("field_of_study_csv",
                    help="Most-Recent-Cohorts-Field-of-Study.csv from the Scorecard zip")
    ap.add_argument("-o", "--output", default="data/professional_debt_clean.csv")
    args = ap.parse_args()

    print(f"Reading {args.field_of_study_csv} ...")
    raw = load_raw(args.field_of_study_csv)
    print(f"  {len(raw):,} field-of-study rows")

    out = build(raw)

    print(f"\n  {'program':10s} {'schools':>8s} {'min':>10s} {'median':>10s} {'max':>10s}"
          f" {'over $200k cap':>15s}")
    for key, block in out.groupby("program_key"):
        d = block["debt_median"]
        over = (d > 200_000).mean() * 100
        print(f"  {key:10s} {len(block):>8d} {d.min():>10,.0f} {d.median():>10,.0f} "
              f"{d.max():>10,.0f} {over:>14.0f}%")

    out.to_csv(args.output, index=False)
    print(f"\nWrote {len(out):,} rows to {args.output}")


if __name__ == "__main__":
    main()

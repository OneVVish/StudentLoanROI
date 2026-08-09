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

THE MBA IS PRICED TWO WAYS, AND NEITHER IS A PRICE ON ITS OWN. No federal
source publishes what an MBA costs. IPEDS graduate tuition is an
institution-wide average across every graduate programme at the school, so an
MBA and an MEd collapse into one number, and IPEDS's per-programme tuition
fields cover chiropractic, dentistry, medicine, optometry, osteopathic
medicine, pharmacy, podiatry, veterinary medicine and law -- business is not
among them. The only MBA-specific federal figure is DEBT, which is what CIP
5202 at CREDLEV 5 gives here, for 837 schools.

So a consumer showing an MBA figure shows this debt median alongside the
school's average graduate tuition from data/graduate_tuition_clean.csv, and
labels them as the different things they are. Never add them and never average
them: one is cumulative borrowing at graduation, already net of scholarships
and family money, and the other is an annual sticker price for the average
master's student at that institution. Business schools also commonly charge
differential tuition above their institution's average, so the average is a
floor for an MBA, not an estimate of one.

OVERLAP -- MBA rows and family "52" rows describe the same students. The
2-digit rollup below medians every business master's together, including the
MBAs broken out above. Both are emitted because they answer different
questions. A consumer picks one and never sums them.

Source: collegescorecard.ed.gov/data -> "Most Recent Data by Field of Study".
The download host is ed-public-download.scorecard.network (the older
app.cloud.gov host now 404s), and the filename carries the release date.

USE THE DATED FILENAME. The undated
`Most-Recent-Cohorts-Field-of-Study.zip` at that host serves an OLDER release
than the dated `..._06102026.zip` -- verified: it yields 22,012 rows with
medical debt spanning $28,083-$272,823 and Harvard at $83,975, where the dated
file yields 20,868 rows, $47,503-$330,479 and Harvard at $99,160, reproducing
the committed CSV exactly. Rebuilding from the undated file silently rolls
every graduate and professional debt figure in the app back several years, and
nothing about the output looks wrong.
"""
import argparse
import sys

import pandas as pd

# CREDLEV 7 is "First Professional Degree" -- verified against CREDDESC in the
# release, alongside 3 Bachelor's, 5 Master's and 6 Doctoral. The three
# programs this app models are all First Professional; a Master's-level path
# would need its own entry here AND its own program length in app.py, never a
# silent default.
FIRST_PROFESSIONAL = 7
MASTERS = 5
DOCTORAL = 6
BACHELORS = 3

# Bachelor's, master's and doctoral rows are emitted alongside the three
# professional programs, but keyed differently and for a different purpose.
#
# The professional block is three exact 4-digit CIP codes mapped to three
# occupations, because app.py knows those occupations by name. Degree study
# spans the whole taxonomy instead -- 220 CIP fields publish a master's median
# -- so there is no occupation to key on, and app.py has no occupation-to-CIP
# crosswalk (it explicitly declines to build one: the SOC-CIP crosswalk's own
# documentation calls itself conceptual rather than empirical).
#
# What app.py DOES have is MAJOR_TO_CIP_FAMILY, a hand-checked map from NY Fed
# major to 2-DIGIT CIP family, used by the school search. So these rows are
# aggregated to the 2-digit family, which is the granularity that map can
# reach. 28 of its 29 families have master's data.
#
# Consequence worth stating: this is Major-mode only. Career mode has no
# bridge and falls back to asking the visitor.
#
# BACHELOR'S is the newest of the three and the only one app.py does not read
# yet: CREDENTIAL_DATA_KEY maps Master's and Doctoral only, so the graduate
# school picker cannot reach a "bachelor" row. That is deliberate -- these
# rows exist for a per-major "what graduates in this field typically borrow"
# figure, NOT to offer a bachelor's student a graduate-school dropdown. Adding
# CREDENTIAL_BACHELORS to that map would do exactly that; don't.
FAMILY_CREDENTIALS = {BACHELORS: "bachelor", MASTERS: "master", DOCTORAL: "doctoral"}

# 4-digit CIP -> (the key app.py uses, expected CIPDESC, credential level).
# CIPDESC is carried only to verify the code still means what we think: these
# are stable, but a silent CIP reassignment between releases would otherwise
# swap medicine for something else without anything failing.
#
# The credential level is per-programme rather than a single constant because
# the MBA is a MASTER'S, not a First Professional degree. Keying it at
# CREDLEV 7 would find nothing; forcing it to credential "professional" would
# be worse -- it would put an MBA in professional_schools_for() beside
# medicine and law, and inherit the "first professional" framing that the
# federal aggregate limits and the app's own disclosures are built around.
#
# WHY THE MBA IS HERE RATHER THAN IN THE 2-DIGIT ROLLUP BELOW. An MBA lands in
# CIP family 52, Business -- pooled with accounting, finance, marketing and
# every other business master's into one median-of-medians. That is the right
# granularity for "what does a business master's borrow" and the wrong one for
# pricing an MBA, which is the single most-asked graduate question this app
# gets. The exact 4-digit code gives 837 schools publishing an MBA-specific
# median, so the precision is available and only the aggregation was hiding it.
#
# These rows are ADDITIVE: the 2-digit "52" master rows are untouched and
# still include the same students. The two overlap by construction. A consumer
# picks one or the other and must never sum them -- see OVERLAP note in the
# module docstring.
PROGRAMS = {
    "5112": ("medicine", "Medicine.", FIRST_PROFESSIONAL),
    "2201": ("law", "Law.", FIRST_PROFESSIONAL),
    "5104": ("dentistry", "Dentistry.", FIRST_PROFESSIONAL),
    "5202": ("mba", "Business Administration, Management and Operations.", MASTERS),
}

# What each exact-CIP programme is called in the `credential` column. The three
# First Professional programmes share one label because app.py's picker filters
# on it; the MBA gets the master's label because that is what it is.
PROGRAM_CREDENTIAL = {FIRST_PROFESSIONAL: "professional", MASTERS: "master"}

# Median cumulative debt for Direct Subsidized/Unsubsidized plus Grad PLUS,
# counting only loans originated AT the evaluated institution (EVAL). The ANY
# variant includes debt from every institution the student attended, which
# would double-count the undergraduate school app.py already charges
# separately.
DEBT_COLUMN = "DEBT_ALL_STGP_EVAL_MDN"
PAYMENT_COLUMN = "DEBT_ALL_STGP_EVAL_MDN10YRPAY"

# PARENT PLUS, carried alongside. STGP above is the STUDENT's own federal
# borrowing (Stafford + Grad PLUS); PP is what the PARENT borrowed for the
# same program. They are different debts owed by different people and must
# never be summed into one "total": Parent PLUS is the parent's obligation,
# is not IDR-eligible for the student, and app.py's split_loan_financing
# already models it as a separate non-forgivable pool.
#
# Kept because the two behave completely differently by field. Student
# borrowing is capped ($31,000 aggregate, dependent) and lands within
# $6,774 across 31 fields; Parent PLUS has no aggregate cap and spans
# $25,459. The flat chart and the wide one come from the same release --
# the difference is the cap, not the field.
#
# The median is conditional on having borrowed PLUS at all, and only about
# a third as many cells report it, so it describes PLUS families rather
# than all families. Say so wherever it is shown.
PARENT_PLUS_COLUMN = "DEBT_ALL_PP_EVAL_MDN"

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
    "CIPCODE", "CREDLEV", "credential", "program_key",
    "debt_median", "debt_10yr_payment", "parent_plus_median",
]


def load_raw(path: str) -> pd.DataFrame:
    usecols = ["UNITID", "INSTNM", "CONTROL", "CIPCODE", "CIPDESC",
               "CREDLEV", DEBT_COLUMN, PAYMENT_COLUMN, PARENT_PLUS_COLUMN]
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
    rows = []
    for cip, (program_key, expected_desc, credlev) in PROGRAMS.items():
        # Filtered per-programme, not once outside the loop: the MBA sits at
        # CREDLEV 5 while the other three sit at 7, and a single pre-filter
        # would silently return nothing for it.
        block = df[(df["CREDLEV"] == credlev) & (df["CIPCODE"] == cip)].copy()
        if block.empty:
            sys.exit(
                f"ERROR: CIP {cip} ({program_key}) has no CREDLEV {credlev} rows in "
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
        block["credential"] = PROGRAM_CREDENTIAL[credlev]
        rows.append(block)

    # Degree rows (bachelor's, master's, doctoral): every CIP field,
    # aggregated to the 2-digit family so app.py's MAJOR_TO_CIP_FAMILY can
    # reach them. One row per (school, family, credential), taking the MEDIAN
    # of the 4-digit medians within the family -- a median of medians rather
    # than a true pooled median, which the app must not present as more
    # precise than it is.
    for credlev, credential in FAMILY_CREDENTIALS.items():
        block = df[df["CREDLEV"] == credlev].copy()
        if block.empty:
            sys.exit(f"ERROR: no CREDLEV {credlev} ({credential}) rows in this file.")
        block["debt_median"] = pd.to_numeric(block[DEBT_COLUMN], errors="coerce")
        block["debt_10yr_payment"] = pd.to_numeric(block[PAYMENT_COLUMN], errors="coerce")
        block["parent_plus_median"] = pd.to_numeric(block[PARENT_PLUS_COLUMN], errors="coerce")
        block = block[block["debt_median"].notna()]
        block["CIPCODE"] = block["CIPCODE"].str[:2]
        grouped = (block.groupby(["UNITID", "INSTNM", "CONTROL", "CIPCODE"], as_index=False)
                        .agg(debt_median=("debt_median", "median"),
                             debt_10yr_payment=("debt_10yr_payment", "median"),
                             parent_plus_median=("parent_plus_median", "median")))
        grouped["CREDLEV"] = credlev
        grouped["credential"] = credential
        grouped["program_key"] = grouped["CIPCODE"]
        rows.append(grouped)

    out = pd.concat(rows, ignore_index=True)
    # The professional blocks still carry the raw string columns; the graduate
    # blocks already resolved theirs above. Only fill where absent.
    if DEBT_COLUMN in out.columns:
        out["debt_median"] = out["debt_median"].fillna(
            pd.to_numeric(out[DEBT_COLUMN], errors="coerce"))
        out["debt_10yr_payment"] = out["debt_10yr_payment"].fillna(
            pd.to_numeric(out[PAYMENT_COLUMN], errors="coerce"))
        out["parent_plus_median"] = out["parent_plus_median"].fillna(
            pd.to_numeric(out[PARENT_PLUS_COLUMN], errors="coerce"))

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

    # The federal aggregate a figure is measured against differs by credential
    # -- $31,000 for a dependent undergraduate, $100,000 for graduate study,
    # $200,000 for professional -- so the "over the cap" column has to know
    # which it is looking at, or it reports a master's median as comfortably
    # inside a ceiling that does not apply to it.
    #
    # The bachelor's row uses the DEPENDENT aggregate ($31,000); an
    # independent undergraduate may borrow $57,500. Over-cap here therefore
    # means "more than a dependent student could have borrowed federally",
    # which is a signal about family money and private loans, not an error.
    caps = {"bachelor": 31_000, "master": 100_000,
            "doctoral": 100_000, "professional": 200_000}
    print(f"\n  {'credential':13s} {'rows':>6s} {'schools':>8s} {'min':>10s} "
          f"{'median':>10s} {'max':>10s} {'over its cap':>13s}")
    for credential, block in out.groupby("credential"):
        d = block["debt_median"]
        cap = caps[credential]
        over = (d > cap).mean() * 100
        print(f"  {credential:13s} {len(block):>6d} {block['UNITID'].nunique():>8d} "
              f"{d.min():>10,.0f} {d.median():>10,.0f} {d.max():>10,.0f} "
              f"{over:>11.0f}% (${cap:,})")

    out.to_csv(args.output, index=False)
    print(f"\nWrote {len(out):,} rows to {args.output}")


if __name__ == "__main__":
    main()

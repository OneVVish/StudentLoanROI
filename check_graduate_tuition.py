#!/usr/bin/env python3
"""Guard: the graduate tuition dataset says what it claims, and the MBA rows
stay separable from the family rollup they overlap.

    python3 check_graduate_tuition.py      (exit 1 on a violation)

This dataset exists to be sorted CHEAPEST-FIRST, which makes its failure mode
specific: anything that lands at an implausibly low price does not look like an
error, it looks like the answer. A $2 graduate school sorts above every real
one, and a school that filed a per-credit rate in the annual field sorts there
too. Neither raises anything. Both are top of the list.

So the checks below are mostly about the LOW end and about values that cannot
mean what the column says. Two of them come from bugs found while building:

  * IPEDS encodes "this charge does not apply" as 0, not as blank. Left alone,
    2 institutions arrived priced at zero -- free graduate school, top of every
    list.
  * 30 institutions filed a PER-CREDIT rate in the annual field. The signature
    is an annual figure below 9 x their own per-credit charge, which cannot be
    a full-time year because IPEDS defines full-time graduate as 9+ credits.
    Their median "year" was $995 against $13,140 for everyone else, and it
    included Thomas Jefferson School of Law at $1,200.

Both are fixed in build_graduate_tuition.py. These assertions are what stops
them coming back, and what stops a future IPEDS release reintroducing them
under a new spelling.

The MBA half guards a different risk. MBA rows and CIP family "52" rows in
graduate_debt_clean.csv describe the SAME STUDENTS at 779 shared schools -- the
family rollup medians every business master's, including the MBAs broken out
separately. Both are emitted because they answer different questions, so the
overlap is by design; what must not happen is a consumer summing them. The
check pins the shape that keeps them distinguishable.

WHAT THIS DELIBERATELY DOES NOT CHECK: that the figures are RIGHT. A federal
aggregate can be internally consistent and still disagree with what a school
publishes on its own website. That is a spot-check against primary sources at
build time, not an assertion -- see the verification notes in the PR.

NEGATIVE CONTROL. Each property was confirmed to fail against a deliberately
corrupted copy of the CSV or a patched builder -- see the table in the PR body.

Run after touching build_graduate_tuition.py, the PROGRAMS table in
build_professional_debt.py, or either committed CSV.
"""
import sys

import pandas as pd

TUITION_PATH = "data/graduate_tuition_clean.csv"
DEBT_PATH = "data/graduate_debt_clean.csv"
UNDERGRAD_PATH = "data/college_coa_clean.csv"

MONEY_COLUMNS = [
    "grad_tuition_in", "grad_tuition_out", "grad_fees_in", "grad_fees_out",
    "grad_tuition_fees_in", "grad_tuition_fees_out",
    "grad_hrchg_in", "grad_hrchg_out",
]

# IPEDS's own definition of a full-time graduate student, mirrored from
# build_graduate_tuition.FULL_TIME_GRAD_CREDITS.
FULL_TIME_GRAD_CREDITS = 9

# Coverage floor against the schools the app already knows award a graduate
# degree. Set well below the 96% observed so ordinary release drift does not
# trip it, but high enough that a release which halves coverage fails loudly
# rather than quietly shrinking any search built on this.
COVERAGE_FLOOR = 0.80


def check_identity(tuition) -> list:
    problems = []
    if tuition["UNITID"].isna().any():
        problems.append("  a row has no UNITID -- it cannot be joined to anything")
    if tuition["UNITID"].duplicated().any():
        dupes = int(tuition["UNITID"].duplicated().sum())
        problems.append(
            f"  UNITID is not unique ({dupes} duplicate row(s))\n"
            "    every consumer joins on it; two rows per school means the join "
            "silently multiplies")
    return problems


def check_no_free_schools(tuition) -> list:
    """Zero is IPEDS for 'not applicable', and it sorts to the top."""
    problems = []
    for column in MONEY_COLUMNS:
        bad = tuition[tuition[column].notna() & (tuition[column] <= 0)]
        if not bad.empty:
            problems.append(
                f"  {column} has {len(bad)} row(s) at or below zero "
                f"(e.g. {bad.iloc[0]['INSTNM']})\n"
                "    IPEDS writes 0 for 'this charge does not apply', which is "
                "not the same as free -- and free sorts first")
    return problems


def check_residency_direction(tuition) -> list:
    """Out-of-state is never cheaper than in-state."""
    problems = []
    both = tuition[tuition["grad_tuition_fees_in"].notna()
                   & tuition["grad_tuition_fees_out"].notna()]
    wrong = both[both["grad_tuition_fees_out"] < both["grad_tuition_fees_in"]]
    if not wrong.empty:
        problems.append(
            f"  {len(wrong)} school(s) charge out-of-state LESS than in-state "
            f"(e.g. {wrong.iloc[0]['INSTNM']})\n"
            "    the residency columns are the likeliest explanation, and a "
            "swap would price every public school backwards")
    return problems


def check_full_time_plausibility(tuition) -> list:
    """No annual figure that is really a per-credit rate."""
    problems = []
    both = tuition[tuition["grad_hrchg_in"].notna() & tuition["grad_tuition_in"].notna()]
    misfiled = both[both["grad_tuition_in"]
                    < both["grad_hrchg_in"] * FULL_TIME_GRAD_CREDITS]
    if not misfiled.empty:
        worst = misfiled.nsmallest(1, "grad_tuition_in").iloc[0]
        problems.append(
            f"  {len(misfiled)} school(s) report an annual tuition below "
            f"{FULL_TIME_GRAD_CREDITS} x their own per-credit rate\n"
            f"    e.g. {worst['INSTNM']}: ${worst['grad_tuition_in']:,.0f}/year "
            f"against ${worst['grad_hrchg_in']:,.0f}/credit\n"
            "    that cannot be a full-time year, and it sorts to the top of a "
            "cheapest-first list")
    return problems


def check_sum_is_resolved(tuition) -> list:
    """grad_tuition_fees_* is resolved once, in the builder, and is coherent."""
    problems = []
    for side in ("in", "out"):
        tui = tuition[f"grad_tuition_{side}"]
        fee = tuition[f"grad_fees_{side}"].fillna(0)
        total = tuition[f"grad_tuition_fees_{side}"]
        # Present exactly when tuition is present -- a missing FEE must not
        # annihilate a reported tuition.
        mismatch = tui.notna() != total.notna()
        if mismatch.any():
            problems.append(
                f"  grad_tuition_fees_{side} is present {int(mismatch.sum())} "
                f"time(s) where grad_tuition_{side} is not, or vice versa\n"
                "    a missing fee must not delete a reported tuition")
        drift = (total - (tui + fee)).abs()
        if (drift[total.notna()] > 0.01).any():
            problems.append(
                f"  grad_tuition_fees_{side} does not equal tuition + fees\n"
                "    it is the figure consumers price with; deriving it twice is "
                "how two callers come to disagree")
    return problems


def check_vintage(tuition) -> list:
    problems = []
    years = tuition["ipeds_year"].dropna().unique()
    if len(years) != 1:
        problems.append(
            f"  ipeds_year is not uniform: {sorted(years.tolist())}\n"
            "    two vintages in one file reads as a price change rather than "
            "as stale data -- the OEWS lesson")
    elif not (2000 <= int(years[0]) <= 2100):
        problems.append(f"  ipeds_year {years[0]} is not a plausible year")
    return problems


def check_coverage(tuition) -> list:
    """The dataset must reach the schools the app already knows about, and must
    reach the graduate-only ones the undergraduate file cannot."""
    problems = []
    try:
        debt = pd.read_csv(DEBT_PATH)
    except FileNotFoundError:
        return ["  data/graduate_debt_clean.csv missing; coverage unverifiable"]

    grad_schools = set(debt[debt["credential"].isin(["master", "doctoral"])]["UNITID"])
    priced = grad_schools & set(tuition["UNITID"].dropna().astype(int))
    share = len(priced) / max(len(grad_schools), 1)
    if share < COVERAGE_FLOOR:
        problems.append(
            f"  only {share:.0%} of the {len(grad_schools):,} master's/doctoral "
            f"schools have a tuition figure (floor {COVERAGE_FLOOR:.0%})\n"
            "    a release that halves coverage would otherwise just shrink any "
            "search built on this, silently")

    try:
        undergrad = set(pd.read_csv(UNDERGRAD_PATH)["UNITID"])
    except (FileNotFoundError, KeyError):
        return problems
    graduate_only = set(tuition["UNITID"].dropna().astype(int)) - undergrad
    if not graduate_only:
        problems.append(
            "  every school here also appears in college_coa_clean.csv\n"
            "    that file drops institutions with no undergraduate COA, so a "
            "graduate dataset that adds none of them has lost exactly the "
            "graduate-only schools (Icahn, Mayo) it exists to reach")
    return problems


def check_mba_rows() -> list:
    """MBA rows are MBA-shaped, and stay separable from the family they overlap."""
    problems = []
    try:
        debt = pd.read_csv(DEBT_PATH, dtype={"program_key": str})
    except FileNotFoundError:
        return ["  data/graduate_debt_clean.csv missing; MBA rows unverifiable"]

    mba = debt[debt["program_key"] == "mba"]
    if mba.empty:
        return ["  no program_key == 'mba' rows; the MBA breakout is missing"]

    credentials = sorted(mba["credential"].dropna().unique())
    if credentials != ["master"]:
        problems.append(
            f"  MBA rows carry credential {credentials}, expected ['master']\n"
            "    an MBA is not a First Professional degree; labelling it one "
            "puts it in professional_schools_for() beside medicine and law")
    levels = sorted(mba["CREDLEV"].dropna().unique().tolist())
    if levels != [5]:
        problems.append(f"  MBA rows carry CREDLEV {levels}, expected [5] (master's)")
    if mba["UNITID"].duplicated().any():
        problems.append("  a school has more than one MBA row")

    # The overlap is by design and must stay visible: family "52" still
    # medians every business master's, MBAs included.
    family = debt[(debt["program_key"] == "52") & (debt["credential"] == "master")]
    if family.empty:
        problems.append(
            "  the CIP '52' business rollup has disappeared\n"
            "    the MBA breakout is ADDITIVE; it must not replace the family row")
    shared = set(mba["UNITID"]) & set(family["UNITID"])
    if not shared:
        problems.append(
            "  no school appears in both the MBA rows and the '52' rollup\n"
            "    they describe the same students, so no overlap means one of "
            "them is not being built from the same population")
    return problems


def main() -> int:
    try:
        tuition = pd.read_csv(TUITION_PATH)
    except FileNotFoundError:
        print(f"graduate tuition: {TUITION_PATH} not found -- run "
              "build_graduate_tuition.py")
        return 1
    if tuition.empty:
        print("graduate tuition: the dataset is empty")
        return 1

    problems, checks = [], []
    for name, found in [
        ("identity", check_identity(tuition)),
        ("no free schools", check_no_free_schools(tuition)),
        ("residency direction", check_residency_direction(tuition)),
        ("full-time plausibility", check_full_time_plausibility(tuition)),
        ("resolved sum", check_sum_is_resolved(tuition)),
        ("vintage", check_vintage(tuition)),
        ("coverage", check_coverage(tuition)),
        ("mba rows", check_mba_rows()),
    ]:
        checks.append(name)
        problems += [f"[{name}]\n{p}" for p in found]

    if problems:
        print(f"graduate tuition: {len(problems)} violation(s)\n")
        print("\n\n".join(problems))
        return 1

    year = int(tuition["ipeds_year"].iloc[0])
    mba_count = 0
    try:
        debt = pd.read_csv(DEBT_PATH, dtype={"program_key": str})
        mba_count = int((debt["program_key"] == "mba").sum())
    except FileNotFoundError:
        pass
    print(f"graduate tuition OK -- {len(checks)} properties over "
          f"{len(tuition):,} schools (IPEDS {year}): none free, none priced "
          f"per-credit-as-annual, out-of-state never cheaper, sum resolved "
          f"once, one vintage, graduate-only schools reached, "
          f"{mba_count:,} MBA rows separable from the '52' rollup.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

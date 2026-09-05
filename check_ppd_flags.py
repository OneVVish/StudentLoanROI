#!/usr/bin/env python3
"""Guard for data/ppd_program_flags.csv, the PPD:2026 earnings-test lookup.

A dataset guard: it reads the committed file and never execs app.py (the
check_graduate_tuition pattern).

Every row here is a claim about whether a real school's programme may lose
federal loan eligibility, and every way of getting it wrong is silent. The
checks are weighted accordingly: a wrongly propagated flag is a false statement
about somebody's loans, while a withheld one merely shows nothing.
"""
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent
FLAGS = REPO / "data" / "ppd_program_flags.csv"
CROSSWALK = REPO / "data" / "ppd_opeid_crosswalk.csv"

# PPD:2026, released 2025-12-30, superseded by the first real measurement on
# 2027-07-01. Held here as well as in the builder because a vintage that lives
# in one place goes stale unnoticed, which is the poverty-guidelines problem.
PPD_RELEASE = "2025-12-30"
PPD_SUPERSEDED_BY = "2027-07-01"

# Transcribed from the 2026-09-04 build and from ED's published figures, NOT
# read back off the builder. A check deriving its expectation from the code
# under test asserts only that the code equals itself.
MASTER_FAIL_SHARE = (0.04, 0.08)      # 5.7% measured; ED's analyses report ~5%
OBBB_FAIL_SHARE = (0.015, 0.045)      # 2.6% measured
MIN_ROWS = 20_000
CREDLEV_RANGE = (1, 8)


def load():
    if not FLAGS.exists():
        raise SystemExit(f"{FLAGS} missing; run build_ppd_flags.py")
    return pd.read_csv(FLAGS, dtype={"OPEID6": "str"})


def check_shape(df):
    problems = []
    if len(df) < MIN_ROWS:
        problems.append(f"  only {len(df)} rows, expected at least {MIN_ROWS}")
    dup = df.duplicated(subset=["UNITID", "CIPCODE", "CREDLEV"]).sum()
    if dup:
        problems.append(
            f"  {dup} duplicate (UNITID, CIPCODE, CREDLEV) keys. The app reads "
            f"this as a per-programme lookup and a duplicate would return two "
            f"verdicts for one programme.")
    bad = df[~df.CREDLEV.between(*CREDLEV_RANGE)]
    if len(bad):
        problems.append(f"  {len(bad)} rows have a CREDLEV outside "
                        f"{CREDLEV_RANGE}; the credlev map probably slipped")
    return problems


def check_failure_rates(df):
    """The published rates are the outside check on the whole pipeline."""
    problems = []
    for col, (lo, hi) in (("master_fail", MASTER_FAIL_SHARE),
                          ("obbb_fail", OBBB_FAIL_SHARE)):
        got = df[col].notna()
        if not got.any():
            problems.append(f"  {col} is empty; the join lost its verdicts")
            continue
        share = (df.loc[got, col] == 1).mean()
        if not lo <= share <= hi:
            problems.append(
                f"  {col} fails {share:.1%} of programmes with a verdict, "
                f"outside the measured {lo:.0%} to {hi:.0%}. ED's own analyses "
                f"put the master rate near 5%.")
    return problems


def check_the_two_flags_are_not_confused(df):
    """obbb_fail and master_fail differ by more than a factor of two."""
    a = int((df.obbb_fail == 1).sum())
    b = int((df.master_fail == 1).sum())
    if a == b:
        return ["  obbb_fail and master_fail report identical failure counts. "
                "They are different tests: measured 1,519 against 3,829. If a "
                "build collapses them, a consumer quoting one under the other's "
                "headline is wrong by more than a factor of two."]
    return []


def check_cosmetology_dominates(df):
    """ED's published finding: 93% of failing undergraduate certificates are
    cosmetology. CIP 1204 is Cosmetology and Related Personal Grooming
    Services. This is an OUTSIDE anchor: if the CIP join slipped, the failures
    would not land there."""
    cert = df[(df.CREDLEV == 1) & (df.master_fail == 1)]
    if cert.empty:
        return ["  no failing undergraduate certificates at all; the credlev "
                "map or the flag join has broken"]
    share = (cert.CIPCODE == 1204).mean()
    if share < 0.25:
        return [f"  cosmetology (CIP 1204) is only {share:.0%} of failing "
                f"undergraduate certificates. ED reports it as the dominant "
                f"field, so a low share means the CIP join slipped."]
    return []


def check_mixed_opeids_are_absent(df):
    """The crosswalk withholds OPE IDs whose campuses are different schools.
    Nothing from those may appear here."""
    if not CROSSWALK.exists():
        return ["  crosswalk missing; cannot verify withheld OPE IDs"]
    cross = pd.read_csv(CROSSWALK, dtype={"OPEID6": "str"})
    withheld = set(cross.loc[cross.propagate == "mixed", "OPEID6"])
    leaked = set(df.OPEID6) & withheld
    if leaked:
        return [f"  {len(leaked)} withheld OPE IDs appear in the flags, e.g. "
                f"{sorted(leaked)[:3]}. Those are certifications spanning "
                f"different institutions, which is how Bard College, Simon's "
                f"Rock and Longy School of Music share one."]
    return []


def check_propagation_is_recorded(df):
    """A fanned row is a fact about a certification, not a campus, and the
    caption has to be able to say so."""
    if "campuses" not in df.columns or "propagation" not in df.columns:
        return ["  campuses/propagation columns missing; a consumer could not "
                "tell a single-campus fact from one inherited across a system"]
    fanned = df[df.campuses > 1]
    if fanned.empty:
        return ["  no row was fanned beyond ED's representative campus. PPD "
                "carries exactly one UNITID per OPE ID, so without fanning "
                "every sibling campus is missing (Penn State's 22, Ivy Tech's "
                "20). Measured: 23,956 rows."]
    if fanned.propagation.isna().any():
        return ["  some fanned rows record no propagation basis"]
    return []


def check_earnings_and_benchmark_are_both_present(df):
    """Both sides of the comparison must come from PPD or neither does. A row
    with a benchmark and no earnings invites someone to supply the other side
    from this repo's own Scorecard figures, which are different cohorts with no
    common deflation."""
    verdict = df[df.master_fail.notna()]
    half = verdict[verdict.benchmark.notna() & verdict.earnings.isna()]
    if len(half) > 0.5 * len(verdict):
        return [f"  {len(half):,} of {len(verdict):,} rows carry a benchmark "
                f"with no earnings. Both sides must come from PPD; pairing its "
                f"benchmark with this repo's earn_median compares different "
                f"cohorts with no common deflation."]
    return []


CHECKS = (
    ("shape and key uniqueness", check_shape),
    ("failure rates match ED's published range", check_failure_rates),
    ("the two flags are not collapsed", check_the_two_flags_are_not_confused),
    ("cosmetology dominates failing certificates", check_cosmetology_dominates),
    ("withheld OPE IDs never appear", check_mixed_opeids_are_absent),
    ("propagation is recorded on every fanned row", check_propagation_is_recorded),
    ("earnings and benchmark travel together", check_earnings_and_benchmark_are_both_present),
)


def main():
    df = load()
    failures = []
    for label, fn in CHECKS:
        problems = fn(df)
        print(f"{'FAIL' if problems else 'ok  '}  {label}")
        failures.extend(problems)
    if failures:
        print("\n" + "\n".join(failures))
        sys.exit(1)
    print(f"\nPPD flags OK -- {len(df):,} rows, {df.UNITID.nunique():,} schools, "
          f"{int((df.master_fail == 1).sum()):,} flagged. "
          f"Release {PPD_RELEASE}, superseded {PPD_SUPERSEDED_BY}.")


if __name__ == "__main__":
    main()

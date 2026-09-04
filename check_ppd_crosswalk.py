#!/usr/bin/env python3
"""Guard for data/ppd_opeid_crosswalk.csv, the UNITID to OPEID6 map.

This is a DATASET guard: it reads the committed file and the builder's own
rules, and never execs app.py (the check_graduate_tuition pattern).

It exists because a PPD flag propagated through this crosswalk is a claim about
a real school's federal loan eligibility, and every way of getting it wrong is
silent. A block wrongly marked cohesive puts a beauty school's failing programme
on an unrelated college; a block wrongly marked mixed merely withholds. The two
errors are not symmetric and the checks are weighted accordingly.

Run it after regenerating the crosswalk, and after any change to
NAME_SIMILARITY_MIN.
"""
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent
CROSSWALK = REPO / "data" / "ppd_opeid_crosswalk.csv"

# Transcribed from the 2026-09-04 measurement, NOT read back off the builder.
# A check that derives its expectation from the code under test asserts only
# that the code equals itself, which this repo has recorded more than once.
EXPECTED_MIN_ROWS = 6_000
EXPECTED_SHARED_SHARE = (0.25, 0.32)     # 28.2% measured
MAX_MIXED_OPEIDS = 130                   # 95 measured; a jump means a rule moved

# The four blocks IPEDS says span different systems. These are definitive and
# must never be marked propagatable, whatever their names look like.
SYSTEMS_DIFFER_EXAMPLES = ("Finger Lakes", "Eagle Gate College", "Provo College")

# IPEDS reports exactly four OPE IDs spanning different systems (2026-09-04).
# The bound exists because treating F1SYSCOD -2 as a system code rather than as
# "not applicable" takes this to 15, and NOTHING ELSE IN THIS GUARD NOTICES:
# the extra blocks are merely withheld, which looks like caution. That control
# came back inconclusive on its first run, which this repo rates worse than
# having no control at all.
MAX_SYSTEMS_DIFFER = 8


def load():
    if not CROSSWALK.exists():
        raise SystemExit(f"{CROSSWALK} missing; run build_ppd_crosswalk.py")
    return pd.read_csv(CROSSWALK, dtype={"OPEID6": "str"})


def check_shape(df):
    problems = []
    if len(df) < EXPECTED_MIN_ROWS:
        problems.append(f"  only {len(df)} rows, expected at least {EXPECTED_MIN_ROWS}")
    if df.UNITID.duplicated().any():
        dup = df[df.UNITID.duplicated()].UNITID.tolist()[:5]
        problems.append(f"  UNITID is not unique, e.g. {dup}. The crosswalk is "
                        f"read as a per-school lookup and a duplicate would fan "
                        f"a flag out silently.")
    lo, hi = EXPECTED_SHARED_SHARE
    share = (df.campuses > 1).mean()
    if not lo <= share <= hi:
        problems.append(f"  {share:.1%} of schools sit under a shared OPE ID, "
                        f"outside the measured {lo:.0%} to {hi:.0%}")
    return problems


def check_single_campus_is_never_mixed(df):
    """A one-campus OPE ID cannot fan out, so withholding there is a bug."""
    bad = df[(df.campuses == 1) & (df.propagate != "single")]
    if len(bad):
        return [f"  {len(bad)} single-campus rows are not marked 'single'; a "
                f"lone campus has nobody to inherit a flag from and must never "
                f"be withheld."]
    return []


def check_systems_differ_are_withheld(df):
    """IPEDS saying two systems share an OPE ID is definitive. Withhold."""
    problems = []
    sd = df[df.basis == "systems differ"]
    if sd.empty:
        return ["  no block is classified 'systems differ'. IPEDS reports four; "
                "if that rule stopped firing the mixed set is now decided by the "
                "name heuristic alone."]
    if not (df.basis == "one system, names differ").any():
        problems.append(
            "  no block is withheld for 'one system, names differ'. That rule is "
            "what stops an IPEDS system, which is an administrative parent and "
            "not one institution, from propagating a flag across genuinely "
            "different schools. Bard is the case it exists for.")
    n = sd.OPEID6.nunique()
    if n > MAX_SYSTEMS_DIFFER:
        problems.append(
            f"  {n} OPE IDs are classified 'systems differ', above the bound of "
            f"{MAX_SYSTEMS_DIFFER}. IPEDS reports four. The usual cause is "
            f"F1SYSCOD -2 being read as a system code instead of as 'not "
            f"applicable', which takes this to 15.")
    if (sd.propagate != "mixed").any():
        problems.append("  a 'systems differ' block is marked propagatable. That "
                        "verdict is definitive and must always withhold.")
    for name in SYSTEMS_DIFFER_EXAMPLES:
        hit = df[df.INSTNM.str.startswith(name, na=False)]
        if hit.empty:
            problems.append(f"  {name!r} is absent from the crosswalk entirely")
        elif (hit.campuses > 1).any() and (hit.propagate == "cohesive").any():
            problems.append(f"  {name!r} is marked cohesive; IPEDS puts its "
                            f"campuses in different systems.")
    return problems


def check_bard_is_withheld(df):
    """The institution family that has now broken two different joins."""
    bard = df[df.INSTNM.str.contains("Bard College", na=False)]
    if bard.empty:
        return ["  no Bard College row found; the crosswalk may have lost rows"]
    shared = bard[bard.campuses > 1]
    if shared.empty:
        return ["  Bard College is no longer under a shared OPE ID. Verify "
                "against the source before relaxing anything: it was 15 "
                "campuses spanning Simon's Rock and Longy School of Music."]
    if (shared.propagate == "cohesive").any():
        return ["  Bard College's shared OPE ID is marked propagatable. It "
                "spans Simon's Rock and Longy School of Music, which are "
                "different institutions, and this is the same family that broke "
                "the CSS Profile name join."]
    return []


def check_mixed_is_bounded(df):
    """The heuristic half must stay small enough to hand-review."""
    mixed = df[(df.campuses > 1) & (df.propagate == "mixed")]
    n = mixed.OPEID6.nunique()
    if n > MAX_MIXED_OPEIDS:
        return [f"  {n} OPE IDs are withheld, above the ceiling of "
                f"{MAX_MIXED_OPEIDS}. Either the inputs changed or "
                f"NAME_SIMILARITY_MIN moved; the withheld set is supposed to be "
                f"small enough to review by hand."]
    return []


def check_basis_is_recorded(df):
    """Every verdict must say what decided it, so a reader can tell the
    definitive cases from the heuristic ones."""
    allowed = {"single campus", "one system", "one system, names differ",
               "systems differ", "name similarity"}
    seen = set(df.basis.dropna().unique())
    if not seen <= allowed:
        return [f"  unknown basis values {sorted(seen - allowed)}"]
    if df.basis.isna().any():
        return ["  some rows record no basis for their verdict"]
    return []


CHECKS = (
    ("shape", check_shape),
    ("a single-campus OPE ID is never withheld", check_single_campus_is_never_mixed),
    ("IPEDS 'systems differ' always withholds", check_systems_differ_are_withheld),
    ("Bard's shared OPE ID is withheld", check_bard_is_withheld),
    ("the withheld set stays hand-reviewable", check_mixed_is_bounded),
    ("every verdict records its basis", check_basis_is_recorded),
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
    multi = df[df.campuses > 1]
    print(f"\ncrosswalk OK -- {len(df):,} schools, {len(multi):,} under a shared "
          f"OPE ID, {multi[multi.propagate == 'mixed'].OPEID6.nunique()} OPE IDs "
          f"withheld")


if __name__ == "__main__":
    main()

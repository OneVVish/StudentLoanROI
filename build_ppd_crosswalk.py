#!/usr/bin/env python3
"""UNITID to OPEID6, with a verdict on whether a PPD flag may be propagated.

WHY THIS EXISTS. The Department of Education's Program Performance Data
(PPD:2026) is keyed on (opeid6, credlev, cip4). Every committed dataset in this
repo is keyed on UNITID, and nothing here retains an OPE ID at all. So joining
PPD to the school search needs a crosswalk, and the crosswalk is not one-to-one:
6,252 UNITIDs map to 4,956 OPE IDs, 465 OPE IDs cover more than one campus, and
1,761 UNITIDs (28.2%) sit under a shared one. The largest covers 31 campuses.

WHY A NAIVE JOIN IS DANGEROUS. Fan a PPD row out across every UNITID under its
OPE ID and a failing programme at one campus attaches itself to its siblings.
Nothing errors; the flag simply appears on schools that did not earn it, which
is a false claim about somebody's federal loan access. It is the shape of the
CSS Profile name-join hazard, and OPEID6 002671 is Bard College, Simon's Rock
and Longy School of Music -- the same institution family that broke that join,
arriving through a different identifier.

WHY IT IS STILL MOSTLY SAFE. Title IV eligibility attaches to the OPE ID rather
than the campus, that being what a Program Participation Agreement certifies, so
a programme losing Direct Loan eligibility loses it across the certification.
For a genuine multi-campus system the propagation is CORRECT and only the
WORDING has to change: it is a fact about the certification, not about the
campus, because the programme may be taught at only one of them.

SO THIS SCRIPT CLASSIFIES, and the classification is deliberately not one rule:

  systems differ      -> MIXED. Different IPEDS systems under one OPE ID.
                         Withhold. Definitive, and there are only 4.
  one system          -> COHESIVE. Render, labelled with the campus count.
                         Definitive, from IPEDS F1SYSCOD.
  no system reported  -> fall back to a NAME heuristic, because IPEDS records no
                         system for either campus. 209 blocks, of which 34 come
                         out mixed. That 34 is a hand-reviewable list and should
                         be reviewed, the way build_css_profile_schools.py's
                         ALIASES reviews its eight.

THE HEURISTIC IS THE WEAK PART AND IS CONFINED ON PURPOSE. Worst pairwise
name similarity against NAME_SIMILARITY_MIN. Checked against F1SYSCOD where both
exist: it disagrees with the system identifier 58 times out of 256, in both
directions, so it is not fit to be the primary rule and is used only where there
is no other signal.

TWO TRAPS, both hit while writing this:

  1. IPEDS SHIPS UTF-8 WITH A BOM. Read HD2023.csv as latin-1 and the first
     column arrives named "﻿UNITID", so UNITID is simply absent and pandas
     raises a KeyError about a column that is visibly there. Same trap
     build_cc_costs.py and build_graduate_tuition.py already record.
  2. F1SYSCOD -2 MEANS "NOT APPLICABLE", NOT "A DIFFERENT SYSTEM". Treating it
     as a value made every block where some campuses report a system and others
     do not come out as "systems differ", which wrongly condemned Ohio
     University's regional campuses among others. It took the count of genuinely
     mixed systems from 15 down to 4.

Writes data/ppd_opeid_crosswalk.csv. It does NOT read PPD itself: PPD is a
separate download from ed.gov and this file is what it will be joined THROUGH.

    python3 build_ppd_crosswalk.py
    python3 build_ppd_crosswalk.py --review    # the blocks needing human review
"""
import argparse
import difflib
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent
SCORECARD = REPO / "Most-Recent-Cohorts-Institution.csv"
IPEDS_HD = REPO / "HD2023.csv"
OUT = REPO / "data" / "ppd_opeid_crosswalk.csv"

# Worst pairwise name similarity for a block with no IPEDS system to count as
# one institution. Only consulted when F1SYSCOD is silent for every member.
NAME_SIMILARITY_MIN = 0.60

# Refuse to write a table that has lost its shape, the MIN_STATES discipline in
# build_cc_costs.py. These are floors, not expectations.
MIN_ROWS = 5_000
MIN_SHARED_OPEIDS = 300


def worst_pairwise_similarity(names) -> float:
    lowered = [str(n).lower() for n in names]
    return min(difflib.SequenceMatcher(None, lowered[0], other).ratio()
               for other in lowered[1:])


def build() -> pd.DataFrame:
    if not SCORECARD.exists() or not IPEDS_HD.exists():
        raise SystemExit(
            f"need {SCORECARD.name} (College Scorecard institution file, for the\n"
            f"UNITID/OPEID6 pair) and {IPEDS_HD.name} (IPEDS directory, for\n"
            f"F1SYSCOD). Both are raw downloads and neither is committed.")

    inst = pd.read_csv(SCORECARD, usecols=["UNITID", "OPEID6", "INSTNM"],
                       dtype={"OPEID6": "str"}, low_memory=False)
    inst = inst.dropna(subset=["OPEID6"]).drop_duplicates("UNITID")
    # utf-8-sig, NOT latin-1. See trap 1 in the docstring.
    hd = pd.read_csv(IPEDS_HD, encoding="utf-8-sig", low_memory=False)
    if "UNITID" not in hd.columns:
        raise SystemExit("HD2023.csv has no UNITID column; it was probably read "
                         "with the wrong encoding (see trap 1)")
    df = inst.merge(hd[["UNITID", "F1SYSCOD", "F1SYSNAM"]], on="UNITID", how="left")

    counts = df.groupby("OPEID6").UNITID.nunique()
    out = []
    for opeid6, block in df.groupby("OPEID6"):
        campuses = len(block)
        # -2 is "not applicable". Treating it as a system code is trap 2.
        systems = {int(v) for v in block.F1SYSCOD.dropna() if v > 0}
        sim = worst_pairwise_similarity(block.INSTNM) if campuses > 1 else 1.0
        similar = sim >= NAME_SIMILARITY_MIN
        if campuses == 1:
            verdict, basis = "single", "single campus"
        elif len(systems) > 1:
            verdict, basis = "mixed", "systems differ"
        elif len(systems) == 1:
            # BOTH SIGNALS, NOT EITHER. "One system" alone marked Bard's OPE ID
            # propagatable: IPEDS files Longy School of Music and Simon's Rock
            # under the same system as Bard College, and an IPEDS system is an
            # administrative parent rather than one institution. The guard
            # caught it on its first run.
            #
            # Requiring both withholds 57 further blocks -- Purdue, Strayer,
            # Triton, Nebraska -- which are single institutions with descriptive
            # branch names, so this over-withholds. That is the CHEAP error: a
            # withheld flag shows nothing, while a wrongly propagated one is a
            # false claim about a school's federal loan access.
            #
            # The threshold is deliberately NOT tuned to separate Bard (0.231)
            # from Purdue (0.54). Fitting a cutoff to the handful of blocks
            # someone happened to read is how a check comes to encode its
            # author's examples instead of a rule.
            verdict = "cohesive" if similar else "mixed"
            basis = "one system" if similar else "one system, names differ"
        else:
            verdict = "cohesive" if similar else "mixed"
            basis = "name similarity"
        # IPEDS pads its string fields and writes "-2" for not applicable, so
        # an unstripped filter lets "-2" through as if it were a system name.
        names = sorted({str(n).strip() for n in block.F1SYSNAM.dropna()})
        system = next((n for n in names if n and n != "-2"), "")
        for unitid, instnm in zip(block.UNITID, block.INSTNM):
            out.append({"UNITID": unitid, "OPEID6": opeid6, "INSTNM": instnm,
                        "campuses": campuses, "propagate": verdict,
                        "basis": basis, "name_similarity": round(sim, 3),
                        "system": system})
    frame = pd.DataFrame(out).sort_values(["OPEID6", "UNITID"]).reset_index(drop=True)

    shared = int((counts > 1).sum())
    if len(frame) < MIN_ROWS or shared < MIN_SHARED_OPEIDS:
        raise SystemExit(
            f"refusing to write: {len(frame)} rows and {shared} shared OPE IDs, "
            f"below the floors of {MIN_ROWS} and {MIN_SHARED_OPEIDS}. The inputs "
            f"probably changed shape.")
    return frame


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--review", action="store_true",
                    help="print the blocks whose verdict rests on the name "
                         "heuristic alone, for human review")
    args = ap.parse_args()

    frame = build()
    multi = frame[frame.campuses > 1]
    print(f"{len(frame):,} UNITIDs across {frame.OPEID6.nunique():,} OPE IDs")
    print(f"  under a shared OPE ID: {len(multi):,} "
          f"({len(multi) / len(frame):.1%})")
    for verdict in ("cohesive", "mixed"):
        sub = multi[multi.propagate == verdict]
        print(f"  {verdict:>8}: {sub.OPEID6.nunique():>3} OPE IDs, "
              f"{len(sub):>5} campuses")
        for basis, n in sub.groupby("basis").OPEID6.nunique().items():
            print(f"             {basis:<16} {n}")

    if args.review:
        rev = multi[multi.basis == "name similarity"]
        rev = rev[rev.propagate == "mixed"]
        print(f"\nBlocks resting on the name heuristic and judged MIXED "
              f"({rev.OPEID6.nunique()} to review):")
        for opeid6, blk in rev.groupby("OPEID6"):
            print(f"  {opeid6} sim={blk.name_similarity.iloc[0]:.2f}")
            for n in blk.INSTNM.head(4):
                print(f"      {n[:70]}")
        return

    OUT.parent.mkdir(exist_ok=True)
    frame.to_csv(OUT, index=False)
    print(f"\nwrote {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()

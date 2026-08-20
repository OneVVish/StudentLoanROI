#!/usr/bin/env python3
"""Per-state community-college tuition, IN-DISTRICT and OUT-OF-STATE, from IPEDS.

    python3 build_cc_costs.py ic2023_ay.csv HD2023.csv

Writes data/cc_costs_clean.csv: one row per state, with the median annual
tuition-and-required-fees at that state's public two-year institutions, at both
the in-district rate a resident pays and the out-of-state rate a non-resident
pays.

WHY THIS EXISTS. The app priced the community-college leg from a hand-typed
fifty-state dict of in-district figures and had no concept of a non-resident at
all, while the four-year side has been residency-aware from the start. So a
visitor could select a community-college state they do not live in and be
charged the resident price. That understates by a factor of two at the median
and by nearly eight in California, which is both the cheapest state for a
resident and the most likely to be picked by someone who is not one.

BOTH COLUMNS COME OUT OF ONE PARSE OF ONE RELEASE, which is the whole point of
building it rather than typing a second dict. The repo already records what
happens when two halves of a comparison drift apart in vintage: a metro wage
file one release behind the national one does not read as stale data, it reads
as a pay cut. An in-district figure from one source beside an out-of-state
figure from another would invite exactly that.

WHAT A "COMMUNITY COLLEGE" IS HERE, and the states it misses. IPEDS SECTOR 4 is
"public, two-year", which is the standard definition and the one this uses.
Four states have no institution in it: Alaska, Delaware, Florida and Nevada.
That is not a data gap, it is a reclassification. Their community colleges now
award bachelor's degrees and are therefore filed as four-year: Broward College
and Eastern Florida State College in Florida, College of Southern Nevada,
Delaware Technical Community College. This app already knows that pattern from
the other direction -- `ccb_school` exists because a community college awarding
a bachelor's cannot be identified by degree level alone, and CLAUDE.md names
Broward and Miami Dade specifically.

Rather than guess a rule that would sweep those four back in and sweep real
universities in with them, the build leaves them uncovered and NAMES them.
app.py falls back to the national median for an uncovered state, and the
caption says so. A silently shorter table is how somebody concludes Florida has
no community colleges.

THREE TRAPS, all of them silent:

1. **IPEDS ships UTF-8 with a BOM.** Read as latin-1 or cp1252 and the first
   column arrives named "﻿UNITID", so the join key is simply not there and
   pandas raises nothing -- the merge just finds no UNITID. utf-8-sig strips it.
   build_graduate_tuition.py records the same trap.
2. **Tuition and required fees are separate fields.** TUITION1 alone
   understates: California's in-district tuition is $1,196 and its fees take it
   to $1,288. Every consumer of this file is comparing against a price a family
   actually pays, so the fee belongs in it.
3. **A zero is "does not apply", not "free".** IPEDS writes 0 where a charge is
   not applicable, and a state median built over those reads as a bargain. They
   are dropped, and so are the rows whose reporting flag says the figure was
   not reported at all.

WHAT THIS IS NOT. It is tuition and fees, not a cost of attendance: no housing,
no food, no books. The app models the community-college years as lived at home
and pays them out of pocket rather than borrowing them, which is why the
narrower figure is the right one. Never compare it against `in_state_coa` from
the Scorecard file, which includes housing.
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent
OUT_DEFAULT = REPO / "data" / "cc_costs_clean.csv"

# IPEDS ships UTF-8 with a BOM; see trap 1 in the module docstring.
ENCODING = "utf-8-sig"

# IPEDS SECTOR 4 = "Public, 2-year". CONTROL 1 = public, kept as a belt-and-
# braces filter so a future SECTOR renumbering cannot quietly admit a private.
PUBLIC_TWO_YEAR_SECTOR = "4"
PUBLIC_CONTROL = "1"

# Undergraduate charges, academic year. 1 = in-district, 3 = out-of-state.
# In-state (2) is deliberately unused: for a community college the district
# rate is what a local resident pays, and it is the rate the app's 2+2 path
# assumes. Carrying a third column would invite picking the wrong one.
CHARGE_FIELDS = {
    "in_district": ("TUITION1", "FEE1", "XTUIT1"),
    "out_of_state": ("TUITION3", "FEE3", "XTUIT3"),
}

# IPEDS reporting flags are LETTERS, not the 1/2/3 digits the CONTROL and
# SECTOR fields use: "R" is reported and "A" is not applicable. Guessing digits
# here matched nothing, and the failure was silent in the worst way -- every row
# was filtered out, the groupby produced an empty frame, and a perfectly valid
# CSV with a header and no data was written before anything complained. Hence
# MIN_STATES below.
REPORTED_FLAGS = {"R"}

# A community college charging under this is not cheap, it is miscoded. The
# floor exists because a zero is "does not apply" and IPEDS uses it freely.
MIN_PLAUSIBLE = 100

# Refuse to write a table that is obviously wrong. A filter that matches nothing
# still produces a valid CSV, and the app would then fall back to the national
# figure for every state while looking like it had per-state data. 40 is well
# below the 46 states IPEDS actually covers and well above any plausible
# accident.
MIN_STATES = 40


def load(ic_path: str, hd_path: str) -> pd.DataFrame:
    ic = pd.read_csv(ic_path, dtype=str, encoding=ENCODING, low_memory=False)
    hd = pd.read_csv(hd_path, dtype=str, encoding=ENCODING, low_memory=False)
    for frame, name in ((ic, ic_path), (hd, hd_path)):
        if "UNITID" not in frame.columns:
            sys.exit(f"{name}: no UNITID column. This is the BOM trap: read it "
                     f"with encoding='utf-8-sig'.")
    keep_ic = ["UNITID"] + [c for spec in CHARGE_FIELDS.values() for c in spec]
    missing = [c for c in keep_ic if c not in ic.columns]
    if missing:
        sys.exit(f"{ic_path}: missing {missing}. Is this an IC*_AY release?")
    return hd[["UNITID", "INSTNM", "STABBR", "SECTOR", "CONTROL"]].merge(
        ic[keep_ic], on="UNITID", how="inner")


def charge(frame: pd.DataFrame, tuition: str, fee: str, flag: str) -> pd.Series:
    """Tuition plus required fees, for the rows that actually reported it."""
    t = pd.to_numeric(frame[tuition], errors="coerce")
    f = pd.to_numeric(frame[fee], errors="coerce").fillna(0)
    total = t + f
    reported = frame[flag].isin(REPORTED_FLAGS)
    # A zero is "does not apply". Dropping it AFTER adding the fee, so a school
    # with zero tuition and a real fee still counts.
    return total.where(reported & (total >= MIN_PLAUSIBLE))


def build(ic_path: str, hd_path: str, out_path: Path) -> int:
    merged = load(ic_path, hd_path)
    cc = merged[(merged["SECTOR"] == PUBLIC_TWO_YEAR_SECTOR)
                & (merged["CONTROL"] == PUBLIC_CONTROL)].copy()
    if cc.empty:
        sys.exit("no public two-year institutions matched; check SECTOR coding")

    for label, (tuition, fee, flag) in CHARGE_FIELDS.items():
        cc[label] = charge(cc, tuition, fee, flag)

    grouped = cc.groupby("STABBR").agg(
        in_district=("in_district", "median"),
        out_of_state=("out_of_state", "median"),
        schools=("UNITID", "count"),
    ).round(0)
    grouped = grouped.dropna(subset=["in_district", "out_of_state"])
    grouped = grouped[grouped.index.str.len() == 2].sort_index()

    if len(grouped) < MIN_STATES:
        sys.exit(f"only {len(grouped)} states survived the filters, expected at "
                 f"least {MIN_STATES}. Refusing to write: an empty or short "
                 f"table is indistinguishable from working code downstream, "
                 f"because every state simply falls back to the national "
                 f"figure. Check the reporting flags first -- they are letters "
                 f"(R/A), not digits.")

    grouped.insert(2, "multiple",
                   (grouped["out_of_state"] / grouped["in_district"]).round(2))
    grouped.to_csv(out_path, index_label="state")

    nat_in = grouped["in_district"].median()
    nat_out = grouped["out_of_state"].median()
    print(f"wrote {out_path}  ({len(grouped)} states)")
    print(f"  national median in-district  ${nat_in:>8,.0f}")
    print(f"  national median out-of-state ${nat_out:>8,.0f}"
          f"   ({nat_out / nat_in:.1f}x)")
    widest = grouped["multiple"].idxmax()
    print(f"  widest gap: {widest} at {grouped.loc[widest, 'multiple']:.1f}x "
          f"(${grouped.loc[widest, 'in_district']:,.0f} -> "
          f"${grouped.loc[widest, 'out_of_state']:,.0f})")
    return len(grouped)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ic_csv", help="ic{YYYY}_ay.csv from IPEDS")
    ap.add_argument("hd_csv", help="HD{YYYY}.csv from the SAME year")
    ap.add_argument("-o", "--out", default=str(OUT_DEFAULT))
    args = ap.parse_args()
    build(args.ic_csv, args.hd_csv, Path(args.out))


if __name__ == "__main__":
    main()

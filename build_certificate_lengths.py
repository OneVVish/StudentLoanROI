#!/usr/bin/env python3
"""How long a sub-baccalaureate certificate actually takes, from IPEDS.

Writes data/certificate_lengths_clean.csv: one row per IPEDS award-level band
below the associate's degree, with the national count of awards conferred in
that band and its share. app.py reads it to price the "Postsecondary nondegree
award" level, which BLS assigns to 51 occupations and 9.7 million jobs and for
which BLS itself publishes no length.

WHY THIS EXISTS. Until 2026-09-16 the app charged that level UNDERGRAD_YEARS,
because MISMODELLED_EDUCATION_LEVELS had no defensible length to offer. Four
years is not merely imprecise: measured here, 97.8% of these awards take under
two years and NONE takes four. The app was charging a length no certificate in
the country has, twice over -- once as tuition and once as foregone wages --
and it flipped the sign of the ten-year premium on the largest occupations at
the level (Heavy and Tractor-Trailer Truck Drivers read -$36,062 at four years
and +$103,495 at one).

THE BANDS ARE IPEDS'S OWN, WHICH IS THE WHOLE POINT. The app does not invent a
length and does not average these into one; it offers the reader the federal
categories and charges whole years. A band is a fact about how the award is
classified, so a reader can match their own program's catalog page to one.

    python3 build_certificate_lengths.py C2023_A.zip
    python3 build_certificate_lengths.py C2023_a_RV.csv

The file is a manual download from nces.ed.gov/ipeds/datacenter/data/ (about
9 MB). It answers plain certifi from this network; see CLAUDE.md on when it
does not.

FOUR TRAPS, all silent, and the first two would each produce a valid CSV of
the wrong thing:

  1. AWLEVEL CARRIES AGGREGATE ROWS. Codes 12 ("Degrees total"), 13
     ("Certificates below the baccalaureate total"), 14 and 15 are SUMS of
     other codes in the same file. Summing the column without filtering
     double counts every award. Only 20, 21, 2 and 4 are read, by name.
  2. AWLEVEL 1 IS ALSO AN AGGREGATE, and it is the one that looks like data.
     "Certificates of less than 1 year" is 20 plus 21, which IPEDS split out
     in a later collection. It is absent from the revised 2023 file and must
     stay unread if a future release reinstates it.
  3. MAJORNUM 2 IS THE SECOND MAJOR of a double major, counted again under
     its own CIP. First majors only.
  4. CIPCODE 99 is the institution-wide total row, not a field of study.

The file ships UTF-8 with a BOM, the trap build_cc_costs.py and
build_graduate_tuition.py both already record.
"""
import sys
import zipfile
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent
OUT = REPO / "data" / "certificate_lengths_clean.csv"

# IPEDS award levels below the associate's degree, with the whole number of
# years the cost model charges for each. The model's enrollment loop is
# range(years), so a band shorter than a year is charged one -- which rounds
# UP for 56% of all awards conferred, and is the direction this project errs
# in. The labels are IPEDS's own wording, lightly shortened for a dropdown.
BANDS = {
    20: ("Under 12 weeks", 1),
    21: ("12 weeks to under 1 year", 1),
    2:  ("1 to under 2 years", 2),
    4:  ("2 to under 4 years", 3),
}
# Aggregates that must never be read as data. Named so a future release
# reinstating one fails loudly here rather than silently doubling the totals.
AGGREGATE_LEVELS = {1, 12, 13, 14, 15}

MIN_AWARDS = 1_000_000     # refuse a table that has lost its shape


def load(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            # The revised file wins where both are present: IPEDS reissues
            # _RV after the collection closes and it is the corrected one.
            names = z.namelist()
            name = next((n for n in names if n.lower().endswith("_rv.csv")),
                        None) or names[0]
            with z.open(name) as fh:
                return pd.read_csv(fh, encoding="utf-8-sig", low_memory=False)
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False)


def build(path: Path) -> pd.DataFrame:
    raw = load(path)
    for col in ("AWLEVEL", "MAJORNUM", "CIPCODE", "CTOTALT"):
        if col not in raw.columns:
            raise SystemExit(f"{path.name} has no {col} column; IPEDS's schema moved")

    present = set(raw.AWLEVEL.dropna().astype(int).unique())
    leaked = present & AGGREGATE_LEVELS
    if leaked:
        print(f"  note: aggregate award levels {sorted(leaked)} are present and "
              f"are deliberately NOT read", file=sys.stderr)
    missing = set(BANDS) - present
    if missing:
        raise SystemExit(
            f"award level(s) {sorted(missing)} absent from {path.name}. A band "
            f"that is missing rather than empty means the collection changed "
            f"its categories, and charging the remaining bands would describe a "
            f"different population.")

    first = raw[(raw.MAJORNUM == 1)
                & (raw.CIPCODE.astype(str).str.strip() != "99")]
    rows = []
    for level, (label, years) in BANDS.items():
        awards = int(pd.to_numeric(
            first.loc[first.AWLEVEL == level, "CTOTALT"], errors="coerce").sum())
        rows.append({"awlevel": level, "label": label,
                     "years_charged": years, "awards": awards})
    frame = pd.DataFrame(rows)
    total = frame.awards.sum()
    if total < MIN_AWARDS:
        raise SystemExit(
            f"only {total:,} awards across the four bands, under the {MIN_AWARDS:,} "
            f"floor. A filter matched almost nothing, which downstream is "
            f"indistinguishable from a shorter year.")
    frame["share"] = (frame.awards / total).round(5)
    frame["data_year"] = data_year(path)
    return frame


def data_year(path: Path) -> str:
    """The collection year, read off IPEDS's own filename.

    These files carry no year column, exactly like the OEWS workbooks, so the
    vintage lives only in the name and a mismatch cannot be detected from the
    data. Kept as a string because it is a label rather than a number.
    """
    import re
    m = re.search(r"C(\d{4})_a", path.name, re.I)
    if not m:
        raise SystemExit(
            f"cannot read a collection year from {path.name!r}. IPEDS names "
            f"these C<YYYY>_A; renaming one loses the only vintage it has.")
    return m.group(1)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__.strip().splitlines()[0]
                         + "\n\n  python3 build_certificate_lengths.py C2023_A.zip")
    frame = build(Path(sys.argv[1]))
    OUT.parent.mkdir(exist_ok=True)
    frame.to_csv(OUT, index=False)
    total = frame.awards.sum()
    print(f"wrote {OUT} ({len(frame)} bands, {total:,} awards, "
          f"IPEDS {frame.data_year.iloc[0]})")
    cum = 0.0
    for _, r in frame.iterrows():
        cum += r.share
        print(f"  {r.label:<26} {r.awards:>10,}  {r.share:>6.1%}  "
              f"cumulative {cum:>6.1%}  charged {r.years_charged}y")


if __name__ == "__main__":
    main()

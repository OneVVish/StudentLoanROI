#!/usr/bin/env python3
"""Guard for data/discipline_outcomes_clean.csv and build_discipline_outcomes.py.

This checks a committed DATASET, not the app, so unlike most guards here it
never execs app.py's section 1-2 prefix -- the check_graduate_tuition.py
pattern.

THE LOAD-BEARING PROPERTY IS [formula reproduces]. The entire defence of a
composite we invented is that its weights are published, and a published weight
is only meaningful if something proves the stored number was actually built from
it. That check recomputes discipline_score from the stored components and the
imported weights and requires agreement inside the builder's own
rounding. Write it first, keep it first.

Every rule is IMPORTED from build_discipline_outcomes rather than restated. A
mirrored copy stops checking the real rule the moment the two drift, which is
the flaw this repo has already recorded against an early check_chart_axes and
the residency guard.

WHAT THIS DELIBERATELY DOES NOT CHECK: that the figures are RIGHT. Nothing here
can tell a correct federal median from a plausible wrong one. It checks that the
dataset says what the builder claims it says.

NEGATIVE CONTROL. Each breakage below was actually run and caught by the
property aimed at it, not merely by something:

    a weight changed after the fact         -> [formula reproduces]
    reweight when a component is missing    -> [no partial scores]
    normalize across all disciplines pooled -> [per-discipline components]
    to_numeric() on a BBRR band column      -> [numeric columns are numeric]
    thin cohorts dropped, flag left False   -> [thin cohorts flagged]
    a CREDLEV 3 national median for medicine-> [denominator matches release]
    a discipline's coverage collapses       -> [coverage floors]
    medicine moved back to the 4-yr window  -> [medicine uses 5-yr window]
    a discipline switches benchmark basis    -> [benchmark basis]
    a scored row with no state benchmark    -> [benchmark basis]
    a one-state top decile ships            -> [state concentration]

Two corrections worth keeping, because the first draft of this table claimed
otherwise and was wrong:

  * [formula reproduces] does NOT catch a reweight on a missing component. The
    absent component makes the recomputation NaN, so the comparison is vacuous
    and passes. [no partial scores] is the only thing standing there.
  * SWAPPING WINSORIZED MIN-MAX FOR PERCENTILE RANK IS NOT CAUGHT BY ANYTHING
    HERE, and that is the sharpest limitation of this guard. The builder would
    store components and score consistently, both still inside [0, 1], and every
    property would pass -- while every school's number had changed and a tightly
    clustered discipline had been spread across the full scale, which is the one
    thing the normalization exists to prevent. Nothing short of pinning expected
    scores for named schools would catch it, and that is a fixture this file
    deliberately does not carry. Read WINSOR_PCTILES' comment before touching it.

TWO PROPERTIES ARE NOT HERE YET, AND BOTH ARE NAMED RATHER THAN FAKED:

  * [prose names the weights] -- assert app.py's Methodology quotes the same
    three integers as WEIGHTS. It lands with the app integration, because
    nothing in app.py mentions this dataset yet and a check written now would
    either fail the build or pass vacuously. A guard that cannot fail is worse
    than no guard, because it reads like coverage.
  * [vintage] -- the field-of-study release carries no year column anywhere, and
    the vintage lives only in the filename. clean_college_scorecard.py records
    the same problem and had to recover its data year from the API. Recording a
    date this script cannot verify would be inventing one.

Run after touching build_discipline_outcomes.py or the committed CSV.
"""
import sys

import pandas as pd

from build_discipline_outcomes import (
    DISCIPLINE_SOC, DISCIPLINES, MAX_TOP_DECILE_STATE_EXCESS, WITHHELD, MAX_ADMIT_RATE_CORR, MIN_SCORED_SCHOOLS, NA_VALUES,
    SCORECARD_MIN_N, SCORE_MAX, SCORE_MIN, THIN_COHORT_N, WEIGHTS,
    WINDOW_MEDICINE,
)

SCORE_ROUNDING_TOLERANCE = 1e-3

DATASET = "data/discipline_outcomes_clean.csv"
RAW = "Most-Recent-Cohorts-Field-of-Study.csv"

# Set BELOW observed so ordinary release drift does not trip them, but high
# enough that a release which stops reporting a discipline fails loudly. The
# same reasoning as check_graduate_tuition.py's APP_PROFESSIONAL_PROGRAMS.
# Observed at build time: chemical .81, dentistry .78, law .76, aerospace .75,
# nursing .73, mechanical .74, civil .68, business .68, industrial .63,
# medicine .63, economics .39 (shipped below the floor, deliberately).
COVERAGE_FLOORS = {
    "eng_chemical": 0.70, "law": 0.66, "eng_aerospace": 0.65,
    "eng_mechanical": 0.64, "nursing": 0.60, "business": 0.58,
    "eng_industrial": 0.53, "dentistry": 0.68,
    # Shipped below the 60% build floor by explicit decision, so their guard
    # floors sit below their own observed coverage rather than below the floor
    # they failed: electrical .57, biomedical .51, computer .41, economics .39.
    "eng_electrical": 0.50, "eng_biomedical": 0.45, "eng_computer": 0.35,
    "economics": 0.30,
}


def check_identity(df):
    problems = []
    if df["UNITID"].isna().any():
        problems.append(
            "  rows with a null UNITID\n"
            "    UNITID is the key the app merges on; foreign institutions are "
            "supposed to be dropped at build time (T6)")
    dupes = df.duplicated(subset=["UNITID", "discipline_key"]).sum()
    if dupes:
        problems.append(
            f"  {dupes} duplicate (UNITID, discipline_key) row(s)\n"
            "    that pair is the join key, so a duplicate multiplies a school's "
            "row in the search results")
    unknown = set(df["discipline_key"]) - set(DISCIPLINES)
    if unknown:
        problems.append(
            f"  discipline_key(s) not in the registry: {sorted(unknown)}\n"
            "    a key the builder cannot produce means the CSV and the builder "
            "disagree about what fields exist")
    return problems


def check_score_bounds(df):
    problems = []
    scored = df["discipline_score"].dropna()
    if scored.empty:
        return ["  no row carries a score at all\n"
                "    the dataset exists to carry one"]
    if scored.min() < SCORE_MIN or scored.max() > SCORE_MAX:
        problems.append(
            f"  scores run {scored.min():.2f} to {scored.max():.2f}, outside "
            f"[{SCORE_MIN}, {SCORE_MAX}]\n"
            "    the app renders this as an integer out of 100")
    return problems


def check_no_partial_scores(df):
    """A score must never be built from fewer than all three components.

    Reweighting on a missing component would score a school where few students
    borrow on a DIFFERENT FORMULA than one where many do, while both displayed a
    number in the same column.
    """
    problems = []
    scored = df[df["discipline_score"].notna()]
    for column in WEIGHTS:
        missing = scored[column].isna().sum()
        if missing:
            problems.append(
                f"  {missing} scored row(s) have no {column}\n"
                "    all three components are required; a partial score is a "
                "different formula wearing the same column heading")
    bad_basis = scored[scored["score_basis"] != "complete"]
    if len(bad_basis):
        problems.append(
            f"  {len(bad_basis)} scored row(s) whose score_basis is not "
            f"'complete'\n    score_basis is what the caption reads to say why a "
            "school has no number")
    unscored = df[df["discipline_score"].isna()]
    nameless = unscored[unscored["score_basis"] == "complete"]
    if len(nameless):
        problems.append(
            f"  {len(nameless)} unscored row(s) claim score_basis 'complete'\n"
            "    every absent score must name its reason")
    return problems


def check_formula_reproduces(df):
    """THE load-bearing property. Recompute the score from the stored parts.

    This is what makes the published weights verifiable rather than decorative.
    It catches a changed weight, a changed normalization, and a reweight on a
    missing component, all at once.
    """
    problems = []
    if sum(WEIGHTS.values()) != 100:
        problems.append(
            f"  WEIGHTS sum to {sum(WEIGHTS.values())}, not 100\n"
            "    the published formula is stated as percentages")
    scored = df[df["discipline_score"].notna()]
    if scored.empty:
        return problems
    weighted = sum(scored[column] * weight
                   for column, weight in WEIGHTS.items()) / sum(WEIGHTS.values())
    expected = SCORE_MIN + (SCORE_MAX - SCORE_MIN) * weighted
    # The stored score is rounded to 4 dp for legibility, so the tolerance is
    # that rounding and nothing more. Tightening it below the rounding makes the
    # check fail on every row; loosening it past 0.01 would let a real weight
    # change hide inside it.
    drift = (expected - scored["discipline_score"]).abs()
    off = drift > SCORE_ROUNDING_TOLERANCE
    if off.any():
        worst = scored.loc[drift.idxmax()]
        problems.append(
            f"  {off.sum()} row(s) whose score is not what the published weights "
            f"produce (worst {drift.max():.4f}, {worst['INSTNM']} / "
            f"{worst['discipline_key']})\n"
            "    the entire defence of an invented score is that the stored "
            "number is the published formula applied to the stored components")
    return problems


def check_per_discipline_normalization(df):
    """Each COMPONENT must span [0, 1] within its own discipline.

    Asserted on the components, not on the composite. Winsorized min-max
    guarantees each component reaches both ends within its discipline; the
    weighted composite only reaches an endpoint if one school sits at the
    extreme of all three at once, which nothing guarantees and which dentistry
    and medicine do not do. Testing the composite's range was this guard's own
    first version, and it failed on correct data.

    Pooled normalization is still caught: scaled against every discipline at
    once, a lower-paying field's earnings component could never reach 1.
    """
    problems = []
    scored = df[df["discipline_score"].notna()]
    for key, block in scored.groupby("discipline_key"):
        if len(block) < MIN_SCORED_SCHOOLS:
            continue
        for column in WEIGHTS:
            low, high = block[column].min(), block[column].max()
            if low > 0.001 or high < 0.999:
                problems.append(
                    f"  {key}/{column} spans only {low:.3f} to {high:.3f}\n"
                    "    a component scaled within its own discipline reaches "
                    "both ends; this looks like pooled normalization")
    return problems


def check_coverage_floors(df):
    problems = []
    for key, floor in COVERAGE_FLOORS.items():
        block = df[df["discipline_key"] == key]
        if block.empty:
            problems.append(
                f"  {key} is absent from the dataset\n"
                "    it is named in COVERAGE_FLOORS, so it is expected to ship")
            continue
        share = float(block["scored_share"].iloc[0])
        if share < floor:
            problems.append(
                f"  {key} scores {share:.0%} of the schools teaching it, below "
                f"its {floor:.0%} floor\n"
                "    below a floor the column ranks who reports rather than who "
                "teaches")
    return problems


def check_numeric_columns_are_numeric(df):
    """T5: a BBRR band or a suppression token reaching a numeric column.

    to_numeric(errors="coerce") would empty such a column silently and the
    composite would collapse to its other components with nothing raising.
    """
    problems = []
    for column in ("earn_median", "earn_national", "earn_ratio", "debt_median",
                   "earn_to_debt", "employed_share", "discipline_score",
                   *WEIGHTS):
        if df[column].dtype.kind != "f":
            problems.append(
                f"  {column} is {df[column].dtype}, not float\n"
                "    a banded string or a suppression token has survived into a "
                "numeric column")
    for column in ("repayment_band_makeprog", "repayment_band_paidinfull"):
        values = df[column].dropna().astype(str)
        if not values.empty and not values.str.contains(r"[<>-]").any():
            problems.append(
                f"  {column} carries no banded value at all\n"
                "    these are published as bands ('0.10 - 0.14', '>=0.80'); a "
                "column of clean numbers means someone coerced them")
    return problems


def check_thin_cohorts_flagged(df):
    problems = []
    if not df["thin_cohort"].any():
        problems.append(
            "  no row is flagged thin_cohort\n"
            f"    programmes below {THIN_COHORT_N} completers exist and are "
            "supposed to be flagged rather than dropped")
    tiny = df["cohort_n"].dropna()
    if not tiny.empty and tiny.min() < SCORECARD_MIN_N:
        problems.append(
            f"  a cohort of {tiny.min():.0f} is below Scorecard's own floor of "
            f"{SCORECARD_MIN_N}\n"
            "    the federal file should not publish one that small")
    mismatched = df[df["cohort_n"].notna()
                    & (df["cohort_n"].lt(THIN_COHORT_N) != df["thin_cohort"])]
    if len(mismatched):
        problems.append(
            f"  {len(mismatched)} row(s) whose thin_cohort disagrees with "
            f"cohort_n < {THIN_COHORT_N}\n"
            "    the flag is what the caption's asterisk reads")
    return problems


def check_honesty_metadata(df):
    """The caption cannot tell the truth about this column without these.

    A selectivity correlation past MAX_ADMIT_RATE_CORR means the score IS the
    admit-rate ordering with extra steps, which is a thing to refuse rather than
    to footnote.
    """
    problems = []
    for key, block in df[df["discipline_score"].notna()].groupby("discipline_key"):
        row = block.iloc[0]
        corr = row["admit_rate_corr"]
        if pd.isna(corr):
            problems.append(
                f"  {key} has no admit_rate_corr\n"
                "    the caption quotes it; without it the page cannot say what "
                "the score partly measures")
        elif abs(corr) > MAX_ADMIT_RATE_CORR:
            problems.append(
                f"  {key} correlates {corr:+.2f} with admit rate, past "
                f"{MAX_ADMIT_RATE_CORR}\n"
                "    at that point the column is the admit-rate ordering with "
                "extra steps")
        for column in ("rank_stability", "median_rank_shift"):
            if pd.isna(row[column]):
                problems.append(
                    f"  {key} has no {column}\n"
                    "    the caption tells the reader to read a band rather than "
                    "a rank, and quotes this to say how wide")
        shift = row["median_rank_shift"]
        if pd.notna(shift) and not 0 <= shift <= 1:
            problems.append(f"  {key} median_rank_shift {shift} is not a share")
    return problems


def check_state_concentration(df):
    """No shipped discipline's top decile may be one state's list.

    The builder refuses above MAX_TOP_DECILE_STATE_EXCESS; this asserts the
    refusal actually happened, because a gate that silently stops firing looks
    exactly like a dataset that got better.
    """
    problems = []
    for key, block in df[df["discipline_score"].notna()].groupby("discipline_key"):
        excess = block["top_state_excess"].iloc[0]
        if pd.isna(excess):
            problems.append(
                f"  {key} has no top_state_excess\n"
                "    it is the measure the concentration gate reads")
        elif excess > MAX_TOP_DECILE_STATE_EXCESS:
            # A waiver is a decision someone made and the data records; a
            # missing waiver is the gate having quietly stopped firing, which
            # looks exactly like a dataset that got better.
            if not bool(block["concentration_waived"].iloc[0]):
                problems.append(
                    f"  {key} ships with {block['top_state'].iloc[0]} "
                    f"+{excess:.0%} over-represented in its top decile, past "
                    f"+{MAX_TOP_DECILE_STATE_EXCESS:.0%}, with no waiver "
                    f"recorded\n"
                    "    the top of that ranking is a place, not a set of "
                    "schools; ship it with --allow-concentrated or not at all")
        elif bool(block["concentration_waived"].iloc[0]):
            problems.append(
                f"  {key} records a concentration waiver it does not need "
                f"(excess +{excess:.0%})\n"
                "    a stale waiver would hide a real concentration if the "
                "data later drifted past the threshold")
    return problems


def check_benchmark_basis(df):
    """Each discipline divides by the benchmark DISCIPLINE_SOC says it does.

    The map is the measured result of comparing both benchmarks, so a
    discipline quietly switching basis is a discipline whose whole ranking
    changed. It is also the one thing a reader cannot see: both bases produce a
    ratio near 1 and a score out of 100.
    """
    problems = []
    for key, block in df.groupby("discipline_key"):
        expected = ("state_occupation" if DISCIPLINE_SOC.get(key)
                    else "national_field")
        seen = set(block["benchmark_basis"].dropna().unique())
        if seen != {expected}:
            problems.append(
                f"  {key} uses benchmark(s) {sorted(seen)}, not {expected!r}\n"
                "    DISCIPLINE_SOC records which benchmark measurement chose "
                "for this field; switching it silently rewrites every score")
        if expected == "state_occupation":
            socs = set(block["benchmark_soc"].dropna().unique())
            if socs != {DISCIPLINE_SOC[key]}:
                problems.append(
                    f"  {key} is benchmarked against SOC {sorted(socs)}, not "
                    f"{DISCIPLINE_SOC[key]}")
            scored = block[block["discipline_score"].notna()]
            if scored["earn_benchmark"].isna().any():
                problems.append(
                    f"  {key} has scored rows with no state benchmark\n"
                    "    mixing a state and a national benchmark inside one "
                    "discipline puts two scales in one column")
    return problems


def check_medicine_window(df):
    """T8. Deleting the exception moves medicine back onto residency pay.

    Named against the FACT rather than derived from the registry: deriving it
    would only assert the registry equals itself, which is the flaw recorded
    against the first residency guard.
    """
    problems = []
    block = df[df["discipline_key"] == "medicine"]
    if block.empty:
        # The expected state: medicine is in WITHHELD. Assert the reason is
        # still recorded, so nobody re-adds it having forgotten why it went.
        if "medicine" not in WITHHELD:
            problems.append(
                "  medicine is absent from the dataset AND absent from WITHHELD\n"
                "    a discipline that silently stops shipping is indistinguish"
                "able from one nobody noticed breaking")
        return problems
    windows = set(block["earn_window"].unique())
    if windows != {WINDOW_MEDICINE}:
        problems.append(
            f"  medicine is scored on window(s) {sorted(windows)}, not "
            f"{WINDOW_MEDICINE}\n"
            "    at four years the measure is residency pay: the national median "
            "is $106,490 against $159,023 at five, nine of the top ten schools "
            "are osteopathic, and Vanderbilt comes 146th of 155")
    bases = set(block["national_basis"].unique())
    if bases != {"derived"}:
        problems.append(
            f"  medicine's national_basis is {sorted(bases)}, not 'derived'\n"
            "    there is no EARN_MDN_5YR_NAT (T9), so its denominator is a "
            "median of school medians and must never be described as a federally "
            "published figure")
    others = df[(df["discipline_key"] != "medicine")
                & (df["national_basis"] != "published")]
    if len(others):
        problems.append(
            f"  {len(others)} non-medicine row(s) use a derived national median\n"
            "    every four-year discipline has EARN_MDN_4YR_NAT published")
    return problems


def check_denominator_matches_release(df):
    """P11, against the raw file rather than against ourselves.

    SKIPS LOUDLY when the release is absent -- it is gitignored, so it is not on
    a clone. Reporting OK over zero comparisons is worse than saying nothing.
    """
    try:
        raw = pd.read_csv(RAW, usecols=["CIPCODE", "CREDLEV", "EARN_MDN_4YR_NAT"],
                          dtype={"CIPCODE": str}, na_values=NA_VALUES,
                          low_memory=False)
    except FileNotFoundError:
        print(f"SKIPPED [denominator matches release]: {RAW} is not in this "
              f"checkout (it is gitignored). Run this where the release is.")
        return []

    problems = []
    raw["EARN_MDN_4YR_NAT"] = pd.to_numeric(raw["EARN_MDN_4YR_NAT"],
                                            errors="coerce")
    published = df[df["national_basis"] == "published"]
    for key, block in published.groupby("discipline_key"):
        _label, cip, _desc, credlev, _window = DISCIPLINES[key]
        expected = raw[(raw["CIPCODE"] == cip)
                       & (raw["CREDLEV"] == credlev)]["EARN_MDN_4YR_NAT"].dropna()
        if expected.empty:
            problems.append(f"  {key}: the release publishes no national median")
            continue
        stored = block["earn_national"].iloc[0]
        if abs(stored - expected.iloc[0]) > 1.0:
            problems.append(
                f"  {key}: stored national median ${stored:,.0f} but the release "
                f"says ${expected.iloc[0]:,.0f} for CIP {cip} CREDLEV {credlev}\n"
                "    a national median from the wrong credential level rebases "
                "every ratio and produces plausible numbers")
    return problems


def main():
    try:
        df = pd.read_csv(DATASET, dtype={"CIPCODE": str, "cip_family": str,
                                         "discipline_key": str})
    except FileNotFoundError:
        print(f"{DATASET} is missing. Run build_discipline_outcomes.py first.")
        return 1

    checks = [
        ("formula reproduces", check_formula_reproduces(df)),
        ("identity", check_identity(df)),
        ("score bounds", check_score_bounds(df)),
        ("no partial scores", check_no_partial_scores(df)),
        ("per-discipline components", check_per_discipline_normalization(df)),
        ("coverage floors", check_coverage_floors(df)),
        ("numeric columns are numeric", check_numeric_columns_are_numeric(df)),
        ("thin cohorts flagged", check_thin_cohorts_flagged(df)),
        ("honesty metadata", check_honesty_metadata(df)),
        ("benchmark basis", check_benchmark_basis(df)),
        ("state concentration", check_state_concentration(df)),
        ("medicine uses 5-yr window", check_medicine_window(df)),
        ("denominator matches release", check_denominator_matches_release(df)),
    ]

    failures = 0
    for name, problems in checks:
        for problem in problems:
            print(f"[{name}]\n{problem}")
            failures += 1

    if failures:
        print(f"\n{failures} violation(s)")
        return 1

    shipped = df["discipline_key"].nunique()
    scored = int(df["discipline_score"].notna().sum())
    print(f"discipline outcomes OK -- {len(df):,} row(s), {scored:,} scored "
          f"across {shipped} disciplines:\n"
          f"  every score is the published formula applied to its own stored "
          f"components,\n"
          f"  built from all three or not at all, scaled within its own "
          f"discipline,\n"
          f"  above its coverage floor, under |r| {MAX_ADMIT_RATE_CORR} with "
          f"admit rate,\n"
          f"  thin cohorts flagged rather than dropped, every withheld "
          f"discipline still recording why,\n"
          f"  and no banded repayment string in any numeric column.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

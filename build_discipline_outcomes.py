#!/usr/bin/env python3
"""Per-discipline school outcome scores, from the College Scorecard
FIELD-OF-STUDY release.

Writes data/discipline_outcomes_clean.csv: one row per (school, discipline),
carrying what that school's graduates in that exact field earned, what they
borrowed, how many were found working, and a 1-100 composite built from the
three. Thirteen disciplines -- seven engineering branches, economics, business,
nursing, medicine, dentistry and law.

WHY WE BUILD A SCORE RATHER THAN REPUBLISHING ONE. Every ranking a reader knows
by name is proprietary. US News' terms forbid reproducing, republishing or
otherwise exploiting their materials without written permission, and their only
licensing path is schools buying badges. QS, THE, Forbes and Niche are the same.
So this is our own composite, and the whole of its defence is that the formula
and the weights are published, the components are federal, and
check_discipline_outcomes.py recomputes the stored score from the stored parts.

WHAT THE SCORE CAN CLAIM. That graduates of this field at this school, four
years out, earned more or less than the national median for the same field and
credential; that they did so against more or less federal borrowing; and that
more or fewer of them were found working and not enrolled.

WHAT IT CANNOT CLAIM, and every consumer must say so. Not that the school caused
any of it. Dale & Krueger (2002, NBER 7322; 2011 update) find the selectivity
premium shrinks toward zero once the student's own ability is controlled for,
and this score's measured correlation with admit rate reaches -0.73 in
economics. Not geographic neutrality. And not a stable ordering: between the two
windows this same file publishes, a school's percentile position moves by a
median of 5 to 14 points.

It is DISPLAY ONLY. Nothing here may reach calculate_roi. app.py's commitment is
that no salary it MODELS ever comes from the school, and that stands: this is a
column, not an input.

--------------------------------------------------------------------------------
TRAPS
--------------------------------------------------------------------------------

T1  USE THE DATED FILENAME. Inherited verbatim from build_professional_debt.py:
    the undated Most-Recent-Cohorts-Field-of-Study.zip at
    ed-public-download.scorecard.network serves an OLDER release, and nothing
    about the output looks wrong. This is the one trap here that cannot be
    fixed in code.

T2  SUPPRESSION IS "PS" in this file, not "PrivacySuppressed". NA_VALUES is
    imported from build_professional_debt rather than re-declared, so the two
    parsers of one file cannot disagree about what a missing cell looks like.

T3  CIPCODE MUST STAY A STRING. It is zero-padded, so "0180" read as a number
    becomes 180 and matches nothing.

T4  CONTROL IS ALREADY A LABEL HERE, unlike the institution file. Mapping it
    again turns every row "Unknown" -- uniform rather than wrong-looking.

T5  BBRR*_FED_COMP_* RATES ARE BANDED STRINGS, NOT NUMBERS. 99 distinct values
    in this release, 57 of them containing "<", ">" or "-": "<=0.01",
    "0.03 - 0.04", "0.10 - 0.14", ">=0.80". pd.to_numeric(errors="coerce")
    empties the column SILENTLY and any composite built on it quietly collapses
    to its other components with nothing raising. The block is carried here as
    the raw string and is deliberately NOT scored. Some bands are 0.20 wide, so
    a midpoint would inject +/-0.10 of invented precision.

T6  FOREIGN INSTITUTIONS HAVE NULL UNITID (McGill, UCL, Edinburgh -- 24 in law
    alone) and are the only source of apparent duplicate grain. Dropped first.

T7  TWO UNITIDs CAN SHARE ONE INSTNM. Kansas City University appears twice in
    medicine. Join on UNITID; find_school_coa already records 86 shared names in
    the undergraduate dataset.

T8  MEDICINE AT FOUR YEARS MEASURES RESIDENCY, NOT THE SCHOOL, which is why it
    alone is scored on the five-year window. At four years the national median
    is $106,490 against $159,023 at five, because most graduates are still
    residents. Ranked on the four-year figure the top ten medical schools are
    Pikeville, Pacific Northwest, WVSOM, Lincoln Memorial, Nevada-Reno,
    A T Still, Des Moines, Ross, Western U and Ohio -- nine of ten osteopathic
    or primary-care-heavy -- and VANDERBILT COMES 146th OF 155. That is a
    correct measurement of the wrong thing. See T9 for what the exception costs.

T9  THERE IS NO EARN_MDN_5YR_NAT. The federal national median is published for
    the four-year window only, so medicine's denominator is DERIVED here as the
    median of the school medians in this release. national_basis records which
    of the two any row used, and a consumer must not describe a derived figure
    as a federally published one.

T10 EARN_GT_THRESHOLD_4YR IS UNUSABLE. The "earned more than a high school
    graduate" measure -- the one that would line up with app.py's own
    HS_GRAD_SALARY baseline -- is present for 8 of 321 mechanical engineering
    schools and 3 of 148 medical schools. Do not plan around it.

T11 EARN_MDN_4YR_NAT IS PER CIP *AND* CREDLEV. Using a CREDLEV 3 national median
    against a CREDLEV 7 school rebases everything and produces plausible
    numbers. Verified single-valued within each block, and the builder refuses
    if that ever stops being true.

T12 DEBT_ALL_STGP_EVAL_MDN IS CONDITIONAL ON HAVING BORROWED. A school where few
    students borrow publishes no median, so it is UNSCORED rather than penalised.
    score_basis says which.

T14 GEOGRAPHY IS FIXED FOR TWO DISCIPLINES AND STILL PRESENT IN THE REST.
    Dividing by the school's own state median for the occupation removes the
    local wage level, and it was applied only where measurement showed it helps
    -- see DISCIPLINE_SOC for the full comparison. Nursing went from state
    explaining 76.6% of the ratio's between-school variance to 41.5%, and the
    artifact that started this is gone: the best Alabama nursing programme used
    to score 46.7 against a California MEDIAN of 84.0, and Alabama's median is
    now 58.6 against California's 36.2. Law went to 10.6% and California is now
    slightly UNDER-represented in its top fifty.

    IT IS NOT FIXED ANYWHERE ELSE, and that must be captioned rather than
    implied away. Engineering still runs 38% to 58%, with California 32% of
    mechanical's top fifty against a 9% base and 36% of civil's; dentistry is
    81.5%. The state benchmark made every one of them WORSE, because their
    graduates are nationally mobile and no federal source publishes where a
    given school's graduates went. There is no fix here with this data: read the
    top of those lists as partly a map of where graduates work.

T13 BUSINESS IS 5202, NOT THE "52" FAMILY. The family medians accounting,
    marketing, finance and hospitality into one number, which is the right
    granularity for "what does a business degree borrow" and the wrong one for
    a per-school outcome. build_professional_debt.py makes the same argument for
    breaking the MBA out of that rollup.

--------------------------------------------------------------------------------

Usage:
    python3 build_discipline_outcomes.py Most-Recent-Cohorts-Field-of-Study.csv \\
        --coa data/college_coa_clean.csv

    # A discipline below the coverage floor must be allowed BY NAME. There is
    # deliberately no global --force: shipping a thin discipline is a per
    # discipline decision, and this is where it gets written down.
    python3 build_discipline_outcomes.py ... --allow-below-floor economics

Run check_discipline_outcomes.py afterwards.
"""
import argparse
import sys
from argparse import RawDescriptionHelpFormatter

import numpy as np
import pandas as pd

# One parser of this file already exists. Importing its rules rather than
# restating them is the same discipline the guards follow: two copies of "what
# does a suppressed cell look like" is how the two come to disagree.
from build_professional_debt import CONTROL_LABELS, NA_VALUES

BACHELORS = 3
FIRST_PROFESSIONAL = 7

CREDENTIALS = {BACHELORS: "bachelor", FIRST_PROFESSIONAL: "professional"}

# The earnings window, in years after completion. Four everywhere except
# medicine -- see T8. Changing a window changes what the column measures, so it
# is per-discipline data rather than a flag anyone can pass.
WINDOW_DEFAULT = 4
WINDOW_MEDICINE = 5

# key -> (label, CIPCODE, expected CIPDESC, CREDLEV, earnings window)
#
# CIPDESC is carried only to verify the code still MEANS what we think. These
# are stable, but a silent CIP reassignment between releases would otherwise
# swap one field for another with nothing failing -- the check that turned
# build_professional_debt.py's veterinary-is-0180 trap into a build failure.
DISCIPLINES = {
    "eng_aerospace": ("Aerospace Engineering", "1402",
                      "Aerospace, Aeronautical, and Astronautical/Space Engineering.",
                      BACHELORS, WINDOW_DEFAULT),
    "eng_biomedical": ("Biomedical Engineering", "1405",
                       "Biomedical/Medical Engineering.",
                       BACHELORS, WINDOW_DEFAULT),
    "eng_chemical": ("Chemical Engineering", "1407",
                     "Chemical Engineering.", BACHELORS, WINDOW_DEFAULT),
    "eng_civil": ("Civil Engineering", "1408",
                  "Civil Engineering.", BACHELORS, WINDOW_DEFAULT),
    "eng_computer": ("Computer Engineering", "1409",
                     "Computer Engineering.", BACHELORS, WINDOW_DEFAULT),
    "eng_electrical": ("Electrical Engineering", "1410",
                       "Electrical, Electronics, and Communications Engineering.",
                       BACHELORS, WINDOW_DEFAULT),
    "eng_industrial": ("Industrial Engineering", "1435",
                       "Industrial Engineering.", BACHELORS, WINDOW_DEFAULT),
    "eng_mechanical": ("Mechanical Engineering", "1419",
                       "Mechanical Engineering.", BACHELORS, WINDOW_DEFAULT),
    # In the registry ON PURPOSE despite failing the floor at about 21%
    # coverage. Listed and refused by name on every build is a fact the script
    # states; omitted, it is a fact someone has to notice.
    "eng_general": ("General Engineering", "1401",
                    "Engineering, General.", BACHELORS, WINDOW_DEFAULT),
    "economics": ("Economics", "4506", "Economics.", BACHELORS, WINDOW_DEFAULT),
    "business": ("Business Administration", "5202",
                 "Business Administration, Management and Operations.",
                 BACHELORS, WINDOW_DEFAULT),
    "nursing": ("Nursing", "5138",
                "Registered Nursing, Nursing Administration, Nursing Research "
                "and Clinical Nursing.", BACHELORS, WINDOW_DEFAULT),
    "medicine": ("Medicine", "5112", "Medicine.",
                 FIRST_PROFESSIONAL, WINDOW_MEDICINE),
    "dentistry": ("Dentistry", "5104", "Dentistry.",
                  FIRST_PROFESSIONAL, WINDOW_DEFAULT),
    "law": ("Law", "2201", "Law.", FIRST_PROFESSIONAL, WINDOW_DEFAULT),
}

# The published formula. These three integers are the entire methodological
# claim, so they are asserted against the app's Methodology prose by the guard:
# a weight changed here and not there is the specific failure this feature
# exists not to have.
#
# AND THE CORRELATIONS MUST BE PUBLISHED BESIDE THEM. A reader shown 55/30/15
# assumes three independent measures. They are not: earn_ratio and earn_to_debt
# share a numerator and run r = +0.41 to +0.76, while employed_share barely
# varies. Weights without that context are a number misrepresenting itself.
WEIGHTS = {
    # The only component whose benchmark is published by the same source, on
    # the same row, for the same CIP and credential -- which is what makes
    # cross-discipline contamination structurally impossible rather than
    # carefully avoided.
    "component_earn_ratio": 55,
    # The only component describing the TRADE rather than the intake. Weighted
    # below its apparent importance precisely because it shares a numerator
    # with the first; a 40/40 split would look like two constructs and behave
    # like one and a third.
    "component_earn_to_debt": 30,
    # The only component uncorrelated with selectivity (r +0.01 to +0.12
    # against -0.30 to -0.73 for the earnings ratio). Its spread is narrow on
    # purpose, so it can only move the bottom tail -- which is the only place an
    # employment rate should move anything.
    "component_employed": 15,
}

# Winsorized min-max, NOT percentile rank. Mechanical engineering's middle half
# of schools sits within 0.080 of ratio of each other against a 3.1% single
# school standard error; percentile rank would spread that across the whole
# 1-100 range and manufacture discrimination the data does not contain. A
# tightly clustered discipline should produce tightly clustered scores.
WINSOR_PCTILES = (5, 95)
SCORE_MIN, SCORE_MAX = 1, 100

# Refusals. Set as a floor on COVERAGE rather than on count alone, because a
# discipline that publishes for a third of its schools is not a ranking of that
# discipline -- it is a ranking of who reports.
MIN_SCORED_SHARE = 0.60
MIN_SCORED_SCHOOLS = 40

# Flagged, never dropped. Scorecard's own floor is n>=16. Dropping below 30
# would remove 36 of 321 mechanical engineering schools and 111 of 369
# economics ones -- a systematic loss of small programmes, not a random one.
# Same treatment PLUS_DEBT_THIN_N already gives Parent PLUS in app.py.
THIN_COHORT_N = 30
SCORECARD_MIN_N = 16

# Above this the column is the admit-rate ordering with extra steps.
MAX_ADMIT_RATE_CORR = 0.85

# THE GEOGRAPHY FIX (T14). Divide a school's graduate earnings by its own
# STATE's median for the occupation the degree leads to, instead of by the
# national median for the field. That removes the local wage LEVEL and leaves
# the residual: how a school's graduates do against the market they work in.
#
# Measured need, on the national-ratio version: state explained 76.6% of
# between-school earnings variance in nursing (62.3% excluding Puerto Rico,
# 52.2% excluding Puerto Rico and California), 50.7% in industrial engineering
# and 50.3% in civil. Within any one state, schools sat within 1.07x to 1.37x of
# each other -- so between-state spread was three to ten times the within-state
# spread, and the top of every list was mostly a map of where graduates work.
#
# None WHERE NO SINGLE OCCUPATION IS DEFENSIBLE, following MAJOR_TO_CIP_FAMILY's
# convention in app.py. Business (5202) graduates scatter across hundreds of
# occupations and "General and Operations Managers" would be a guess; economics
# graduates overwhelmingly do not become Economists (19-3011 is a small,
# PhD-heavy occupation whose median would badly misdescribe a BA). Those two
# keep the national-field benchmark, and benchmark_basis records it per row.
#
# This map is ten entries about degrees that lead to ONE named, mostly licensed
# occupation. It is far narrower than the general SOC-CIP crosswalk this repo
# declines to build, whose own documentation calls itself conceptual rather than
# empirical -- and build_professional_debt.py's PROGRAMS already hand-maps three
# CIPs to three occupations on the same reasoning.
# MEASURED, NOT ASSUMED. Both benchmarks were built and compared on the same
# metric -- the share of between-school variance in the earnings ratio that
# state explains, where lower is better because it means less of the ranking is
# a map of where graduates work:
#
#     discipline        R2 national   R2 state    better
#     nursing                 63.2%      38.2%    STATE
#     law                     21.4%      10.0%    STATE
#     eng_mechanical          29.7%      56.9%    national
#     eng_industrial          41.5%      53.8%    national
#     eng_civil               48.3%      52.7%    national
#     eng_chemical            46.9%      79.3%    national
#     eng_aerospace           57.7%      76.6%    national
#     dentistry               79.4%      88.9%    national
#
# So the state benchmark is applied to NURSING AND LAW ONLY, and the reason it
# fails elsewhere is not noise. Nursing and law are licensed state by state and
# their graduates overwhelmingly practise where they qualified, so the local
# wage is the market they actually face. ENGINEERS ARE NATIONALLY MOBILE: a
# Purdue mechanical engineering graduate may work anywhere, so dividing by
# INDIANA's mechanical engineer wage benchmarks them against a market they may
# never enter, and adds variance rather than removing it. Dentistry fails for a
# second reason on top -- 54 schools across some thirty states is a handful
# each, and OEWS dentist medians exclude much practice income.
#
# None WHERE NO SINGLE OCCUPATION IS DEFENSIBLE, following MAJOR_TO_CIP_FAMILY's
# convention in app.py: business (5202) graduates scatter across hundreds of
# occupations, and economics graduates overwhelmingly do not become Economists
# (19-3011 is a small, PhD-heavy occupation whose median would misdescribe a BA).
#
# Adding an entry here is a claim that a degree leads to one named occupation
# AND that its graduates stay put. Re-run the comparison above before making it.
DISCIPLINE_SOC = {
    "nursing": "29-1141",
    "law": "23-1011",
}

# Built, measured, and NOT WRITTEN. A discipline whose score is a correct
# measurement of the wrong thing gets withheld rather than footnoted -- the
# standard marketing/rejected-charts/README.md already applies to content, for
# the same reason: a caveat under a number does not stop the number being read.
#
# Pass --allow-withheld to write one anyway; the build says loudly that it did.
WITHHELD = {
    "medicine":
        "Federal earnings measure residency pay, and the five-year window does "
        "not fix it. Surgical residencies run five to seven years, so the "
        "ranking still sorts by specialty mix: at five years the University of "
        "Pikeville places FIRST and Dartmouth 123rd of 125. Publishing that as "
        "a medical school score would be indefensible, and no caption repairs "
        "it. Reconsider only with an earnings window past residency, which this "
        "release does not carry.",
}

OUTPUT_COLUMNS = [
    "UNITID", "INSTNM", "CONTROL", "control_type",
    "discipline_key", "discipline_label", "CIPCODE", "CREDLEV",
    "cip_family", "credential", "earn_window", "national_basis",
    "earn_median", "earn_national", "earn_benchmark", "benchmark_basis",
    "benchmark_state", "benchmark_soc", "earn_ratio",
    "debt_median", "earn_to_debt",
    "cohort_n", "cohort_nwne", "employed_share", "thin_cohort",
    "repayment_band_makeprog", "repayment_band_paidinfull",
    "component_earn_ratio", "component_earn_to_debt", "component_employed",
    "discipline_score", "score_basis",
    "universe_n", "scored_n", "scored_share",
    "admit_rate_corr", "rank_stability", "median_rank_shift",
    "state_ratio_p10", "state_ratio_p90",
]


def load_raw(path):
    """The field-of-study release, whitelisted to what this build reads.

    Both earnings windows are read even though each discipline scores on one:
    the unused one is what makes rank_stability computable, and a stability
    figure the caption can quote is worth more than the column it costs. It is
    NEVER scored on -- say so, or the next reader adds it to the formula.
    """
    usecols = [
        "UNITID", "INSTNM", "CONTROL", "CIPCODE", "CIPDESC", "CREDLEV",
        "EARN_MDN_4YR", "EARN_MDN_5YR", "EARN_MDN_4YR_NAT",
        "EARN_COUNT_WNE_4YR", "EARN_COUNT_NWNE_4YR",
        "EARN_COUNT_WNE_5YR", "EARN_COUNT_NWNE_5YR",
        "DEBT_ALL_STGP_EVAL_MDN",
        "BBRR2_FED_COMP_MAKEPROG", "BBRR2_FED_COMP_PAIDINFULL",
    ]
    df = pd.read_csv(path, usecols=usecols, dtype={"CIPCODE": str},
                     na_values=NA_VALUES, low_memory=False)
    # T6: foreign institutions, and the only reason the grain looks non-unique.
    before = len(df)
    df = df[df["UNITID"].notna()].copy()
    dropped = before - len(df)
    df["UNITID"] = df["UNITID"].astype("int64")
    return df, dropped


def load_state_benchmarks(state_careers_path, coa_path, professional_path):
    """(state, occ_code) -> median wage, and UNITID -> state.

    TWO SOURCES FOR THE STATE, because neither covers everything.
    college_coa_clean.csv drops any institution with no undergraduate cost of
    attendance, which is every graduate-only school -- that alone left 27% of
    dental schools and 15% of law schools without a state, and therefore
    unscorable. professional_tuition_clean.csv carries exactly those. Together
    they reach 98% of dentistry and 99% of law.
    """
    wages = pd.read_csv(state_careers_path, dtype={"occ_code": str},
                        usecols=["state", "occ_code", "a_median"])
    wages = wages.dropna(subset=["a_median"])
    benchmarks = {(row.state, row.occ_code): row.a_median
                  for row in wages.itertuples()}

    frames = []
    for path in (coa_path, professional_path):
        try:
            frames.append(pd.read_csv(path, usecols=["UNITID", "STABBR"]))
        except (FileNotFoundError, ValueError):
            continue
    if not frames:
        return benchmarks, {}
    located = pd.concat(frames).dropna(subset=["UNITID", "STABBR"])
    located = located.drop_duplicates(subset=["UNITID"])
    return benchmarks, dict(zip(located["UNITID"].astype("int64"),
                                located["STABBR"]))


def winsorized_unit_scale(series):
    """Winsorize at WINSOR_PCTILES, then min-max onto [0, 1].

    A discipline whose schools genuinely cluster comes out clustered. That is
    the property this normalization exists for; see WINSOR_PCTILES.
    """
    values = series.astype(float)
    lo, hi = np.nanpercentile(values, WINSOR_PCTILES)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        # Every school identical on this component. Mid-scale is the only
        # honest answer; spreading them would be inventing a difference.
        return pd.Series(0.5, index=series.index)
    return ((values.clip(lo, hi) - lo) / (hi - lo)).clip(0.0, 1.0)


def score_block(block):
    """Attach the three components and the composite, in place, per discipline.

    ALL THREE COMPONENTS ARE REQUIRED and there is NO REWEIGHTING when one is
    missing. A school with no debt median is disproportionately one where few
    students borrow, so reweighting toward the earnings ratio would score a
    cheap school on a different formula than an expensive one while both
    displayed a number in the same column.
    """
    block = block.copy()
    block["score_basis"] = "complete"
    block.loc[block["earn_median"].isna(), "score_basis"] = "suppressed"
    block.loc[block["score_basis"].eq("complete")
              & block["debt_median"].isna(), "score_basis"] = "no_debt"
    block.loc[block["score_basis"].eq("complete")
              & block["employed_share"].isna(), "score_basis"] = "no_employment"
    # MIXING BENCHMARKS INSIDE ONE DISCIPLINE WOULD PUT TWO SCALES IN ONE
    # COLUMN. A school whose state publishes no median for the occupation is
    # unscored rather than silently normalized against the national figure --
    # the same rule as never reweighting a missing component. It costs Puerto
    # Rico and the Virgin Islands, which OEWS does not cover at state level, and
    # those are exactly the jurisdictions whose national ratio was the artifact.
    block.loc[block["score_basis"].eq("complete")
              & block["earn_benchmark"].isna(),
              "score_basis"] = "no_state_benchmark"

    scorable = block["score_basis"].eq("complete")
    for column in WEIGHTS:
        block[column] = np.nan
    block["discipline_score"] = np.nan
    if not scorable.any():
        return block

    rows = block.loc[scorable]
    block.loc[scorable, "component_earn_ratio"] = winsorized_unit_scale(
        rows["earn_ratio"])
    block.loc[scorable, "component_earn_to_debt"] = winsorized_unit_scale(
        rows["earn_to_debt"])
    block.loc[scorable, "component_employed"] = winsorized_unit_scale(
        rows["employed_share"])

    weighted = sum(block.loc[scorable, column] * weight
                   for column, weight in WEIGHTS.items()) / sum(WEIGHTS.values())
    # Ties after rounding are EXPECTED and there is deliberately no tiebreak:
    # a tiebreak manufactures an ordering the data does not have. The unrounded
    # score is stored; the app displays the integer.
    block.loc[scorable, "discipline_score"] = (
        SCORE_MIN + (SCORE_MAX - SCORE_MIN) * weighted).round(4)
    return block


def honesty_metadata(block, coa):
    """The three numbers that let a caption tell the truth about the column.

    Repeated on every row of the discipline, the ipeds_year idiom. Without a
    --coa file the selectivity and geography figures are NaN and the builder
    says so, rather than emitting a zero that reads as "no relationship".
    """
    meta = {"admit_rate_corr": np.nan, "state_ratio_p10": np.nan,
            "state_ratio_p90": np.nan}
    scored = block[block["discipline_score"].notna()]

    # Stability between the two windows the SAME file publishes. Not a
    # robustness check we invented: it is the same schools, the same measure,
    # one year apart, and it moves a lot.
    pair = block[["earn_median", "earn_median_other"]].dropna()
    if len(pair) >= 10:
        meta["rank_stability"] = round(
            pair["earn_median"].corr(pair["earn_median_other"],
                                     method="spearman"), 4)
        shift = (pair["earn_median"].rank(pct=True)
                 - pair["earn_median_other"].rank(pct=True)).abs()
        meta["median_rank_shift"] = round(float(shift.median()), 4)
    else:
        meta["rank_stability"] = np.nan
        meta["median_rank_shift"] = np.nan

    if coa is None or scored.empty:
        return meta

    joined = scored.merge(coa[["UNITID", "ADM_RATE", "STABBR"]],
                          on="UNITID", how="left")
    admits = joined[["earn_ratio", "ADM_RATE"]].dropna()
    if len(admits) >= 10:
        meta["admit_rate_corr"] = round(
            admits["earn_ratio"].corr(admits["ADM_RATE"]), 4)

    by_state = joined.dropna(subset=["STABBR"]).groupby("STABBR")["earn_ratio"]
    state_medians = by_state.median()
    if len(state_medians) >= 5:
        meta["state_ratio_p10"] = round(float(state_medians.quantile(0.10)), 4)
        meta["state_ratio_p90"] = round(float(state_medians.quantile(0.90)), 4)
    return meta


def build(df, coa, benchmarks, school_state, allow_below_floor,
          allow_withheld):
    frames, refused, report, withheld_now = [], [], [], []

    for key, (label, cip, expected_desc, credlev, window) in DISCIPLINES.items():
        block = df[(df["CIPCODE"] == cip) & (df["CREDLEV"] == credlev)].copy()
        if block.empty:
            sys.exit(
                f"ERROR: CIP {cip} ({key}) has no CREDLEV {credlev} rows in this "
                f"file.\nEither the release changed its taxonomy or this is the "
                f"wrong file. Refusing to write a dataset missing a discipline "
                f"it claims to cover.")

        # T-registry: the code must still MEAN what the registry says.
        seen = set(block["CIPDESC"].dropna().unique())
        if expected_desc not in seen:
            sys.exit(
                f"ERROR: CIP {cip} ({key}) is described as {sorted(seen)!r} in "
                f"this release, not {expected_desc!r}.\nA CIP reassignment would "
                f"otherwise swap one field for another silently.")

        other_window = 5 if window == 4 else 4
        block["earn_median"] = pd.to_numeric(
            block[f"EARN_MDN_{window}YR"], errors="coerce")
        block["earn_median_other"] = pd.to_numeric(
            block[f"EARN_MDN_{other_window}YR"], errors="coerce")
        block["debt_median"] = pd.to_numeric(
            block["DEBT_ALL_STGP_EVAL_MDN"], errors="coerce")
        block["cohort_n"] = pd.to_numeric(
            block[f"EARN_COUNT_WNE_{window}YR"], errors="coerce")
        block["cohort_nwne"] = pd.to_numeric(
            block[f"EARN_COUNT_NWNE_{window}YR"], errors="coerce")

        # T9: the federal national median exists for the 4-year window only.
        if window == WINDOW_DEFAULT:
            national = pd.to_numeric(block["EARN_MDN_4YR_NAT"],
                                     errors="coerce").dropna().unique()
            # T11: the entire normalization rests on this being one number for
            # this exact (CIPCODE, CREDLEV).
            if len(national) != 1:
                sys.exit(
                    f"ERROR: {key} has {len(national)} distinct "
                    f"EARN_MDN_4YR_NAT values ({sorted(national)[:5]}).\n"
                    f"The earnings ratio's denominator must be the one national "
                    f"median for this field and credential level.")
            block["earn_national"] = national[0]
            block["national_basis"] = "published"
        else:
            derived = block["earn_median"].median()
            if not np.isfinite(derived):
                sys.exit(f"ERROR: {key} has no {window}-year earnings to derive "
                         f"a national median from.")
            block["earn_national"] = round(float(derived), 2)
            block["national_basis"] = "derived"

        # The benchmark the ratio actually divides by. State-first where the
        # degree leads to one named occupation; national field median otherwise.
        soc = DISCIPLINE_SOC.get(key)
        block["benchmark_soc"] = soc
        block["benchmark_state"] = block["UNITID"].map(school_state)
        if soc is None:
            block["earn_benchmark"] = block["earn_national"]
            block["benchmark_basis"] = "national_field"
        else:
            block["earn_benchmark"] = [
                benchmarks.get((state, soc)) if pd.notna(state) else None
                for state in block["benchmark_state"]]
            block["earn_benchmark"] = pd.to_numeric(block["earn_benchmark"],
                                                    errors="coerce")
            block["benchmark_basis"] = "state_occupation"
        block["earn_ratio"] = block["earn_median"] / block["earn_benchmark"]
        block["earn_to_debt"] = block["earn_median"] / block["debt_median"]
        found = block["cohort_n"] + block["cohort_nwne"]
        block["employed_share"] = (block["cohort_n"] / found).where(found > 0)
        block["thin_cohort"] = block["cohort_n"].lt(THIN_COHORT_N).fillna(False)

        block["discipline_key"] = key
        block["discipline_label"] = label
        block["cip_family"] = cip[:2]
        block["credential"] = CREDENTIALS[credlev]
        block["earn_window"] = window
        block["control_type"] = (block["CONTROL"].map(CONTROL_LABELS)
                                 .fillna("Unknown"))
        block["repayment_band_makeprog"] = block["BBRR2_FED_COMP_MAKEPROG"]
        block["repayment_band_paidinfull"] = block["BBRR2_FED_COMP_PAIDINFULL"]

        block = block.drop_duplicates(subset=["UNITID"])
        block = score_block(block)

        universe = block["UNITID"].nunique()
        scored = int(block["discipline_score"].notna().sum())
        share = scored / universe if universe else 0.0
        block["universe_n"] = universe
        block["scored_n"] = scored
        block["scored_share"] = round(share, 4)
        for name, value in honesty_metadata(block, coa).items():
            block[name] = value

        report.append((key, label, universe, scored, share,
                       block["admit_rate_corr"].iloc[0],
                       block["median_rank_shift"].iloc[0]))

        if key in WITHHELD and key not in allow_withheld:
            withheld_now.append(key)
            continue
        if (share < MIN_SCORED_SHARE or scored < MIN_SCORED_SCHOOLS) \
                and key not in allow_below_floor:
            refused.append((key, universe, scored, share))
            continue
        frames.append(block)

    if withheld_now:
        print("\nWITHHELD, measured and deliberately not written:")
        for key in withheld_now:
            print(f"  {key}: {WITHHELD[key]}")

    if refused:
        print("\nREFUSED, below the coverage floor "
              f"({MIN_SCORED_SHARE:.0%} of schools teaching it, "
              f"min {MIN_SCORED_SCHOOLS}):")
        for key, universe, scored, share in refused:
            print(f"  {key:<16} {scored:>5} of {universe:>5} schools "
                  f"({share:.0%})  -- pass --allow-below-floor {key} to ship it")

    if not frames:
        sys.exit("\nERROR: every discipline was refused. Nothing to write.")

    out = pd.concat(frames, ignore_index=True)

    if out.duplicated(subset=["UNITID", "discipline_key"]).any():
        sys.exit("ERROR: duplicate (UNITID, discipline_key) rows. That pair is "
                 "the join key the app merges on.")
    # T5: a banded string reaching a numeric column would be silently coerced
    # to NaN downstream. Assert the dtypes at the only point it is still
    # visible.
    for column in ("earn_median", "debt_median", "earn_ratio", "earn_to_debt",
                   "employed_share", "discipline_score"):
        if out[column].dtype.kind != "f":
            sys.exit(f"ERROR: {column} is {out[column].dtype}, not float. A "
                     f"suppression token or a BBRR band has survived into a "
                     f"numeric column.")
    if (out["control_type"] == "Unknown").all():
        sys.exit("ERROR: every control_type is Unknown. CONTROL is already a "
                 "label in this file (T4); refusing to write a column that is "
                 "uniformly meaningless.")

    out["UNITID"] = out["UNITID"].astype("Int64")
    return out.reindex(columns=OUTPUT_COLUMNS), report


def summarise(out, report, dropped_foreign):
    print(f"\nDropped {dropped_foreign:,} row(s) with no UNITID (T6: foreign "
          f"institutions).")
    print(f"\n{'discipline':<18}{'scored':>8}{'univ':>7}{'share':>7}"
          f"{'r(admit)':>10}{'rankshift':>11}")
    for key, _label, universe, scored, share, corr, shift in report:
        shipped = key in set(out["discipline_key"])
        mark = " " if shipped else "x"
        corr_s = "n/a" if pd.isna(corr) else f"{corr:+.2f}"
        shift_s = "n/a" if pd.isna(shift) else f"{shift:.1%}"
        print(f"{mark} {key:<16}{scored:>8}{universe:>7}{share:>7.0%}"
              f"{corr_s:>10}{shift_s:>11}")

    thin = int(out["thin_cohort"].sum())
    unscored = out["score_basis"].value_counts().to_dict()
    print(f"\n{len(out):,} rows, {int(out['discipline_score'].notna().sum()):,} "
          f"scored, {thin:,} flagged thin (n < {THIN_COHORT_N}).")
    print(f"score_basis: {unscored}")

    hot = [(key, corr) for key, _l, _u, _s, _sh, corr, _rs in report
           if pd.notna(corr) and abs(corr) > MAX_ADMIT_RATE_CORR]
    if hot:
        print(f"\nWARNING: {hot} exceed |r| {MAX_ADMIT_RATE_CORR} with admit "
              f"rate. At that point the column is the admit-rate ordering with "
              f"extra steps.")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=RawDescriptionHelpFormatter)
    parser.add_argument("field_of_study_csv",
                        help="Most-Recent-Cohorts-Field-of-Study.csv (T1: the "
                             "DATED release)")
    parser.add_argument("--coa", default="data/college_coa_clean.csv",
                        help="college_coa_clean.csv, for the selectivity and "
                             "geography metadata. Without it those columns are "
                             "NaN rather than zero.")
    parser.add_argument("--state-careers", default="data/state_careers_clean.csv",
                        help="state OEWS medians, for the geography benchmark")
    parser.add_argument("--professional-tuition",
                        default="data/professional_tuition_clean.csv",
                        help="second source of a school's state; covers the "
                             "graduate-only schools the COA file drops")
    parser.add_argument("--allow-withheld", default="",
                        help="comma-separated discipline keys to write despite "
                             "being in WITHHELD. Read that entry's reason first.")
    parser.add_argument("--allow-below-floor", default="",
                        help="comma-separated discipline keys to ship despite "
                             "failing the coverage floor. No global --force: "
                             "this is where a thin discipline gets written down.")
    # Defaults to the path the app will actually read. build_professional_debt.py
    # shipped with a default pointing at a file nothing loaded, and the
    # documented command wrote a dead file and appeared to succeed.
    parser.add_argument("-o", "--output",
                        default="data/discipline_outcomes_clean.csv")
    args = parser.parse_args()

    allow = {k.strip() for k in args.allow_below_floor.split(",") if k.strip()}
    unknown = allow - set(DISCIPLINES)
    if unknown:
        sys.exit(f"ERROR: --allow-below-floor names unknown discipline(s): "
                 f"{sorted(unknown)}")

    df, dropped_foreign = load_raw(args.field_of_study_csv)

    try:
        coa = pd.read_csv(args.coa, usecols=["UNITID", "ADM_RATE", "STABBR"])
    except (FileNotFoundError, ValueError) as exc:
        print(f"NOTE: {args.coa} unreadable ({exc}); selectivity and geography "
              f"metadata will be blank.")
        coa = None

    allow_withheld = {k.strip() for k in args.allow_withheld.split(",") if k.strip()}
    benchmarks, school_state = load_state_benchmarks(
        args.state_careers, args.coa, args.professional_tuition)
    out, report = build(df, coa, benchmarks, school_state, allow, allow_withheld)
    summarise(out, report, dropped_foreign)
    out.to_csv(args.output, index=False)
    print(f"\nWrote {len(out):,} rows to {args.output}")


if __name__ == "__main__":
    main()

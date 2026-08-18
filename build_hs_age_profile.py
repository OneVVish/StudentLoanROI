"""
Build an Age-Earnings Profile for High School Graduates from CPS Microdata
--------------------------------------------------------------------------
Loads the Census Bureau's CPS Annual Social and Economic Supplement (ASEC)
public-use person file and produces median/mean annual wages by age band for
full-time, year-round high school graduates with no college -- the same
population behind app.py's HS_GRAD_SALARY, but broken out by age.

Why this script exists
----------------------
HS_GRAD_SALARY is a single national figure: BLS's published median usual
weekly earnings for workers **age 25 and over** with a high school diploma
and no college. BLS only crosses earnings with educational attainment at 25+
(attainment isn't settled before then), so there is no published under-25
figure to anchor to -- confirmed by querying every series in the CPS
`education = "High school graduates, no college"` dimension: all of them are
"25 years and over".

That matters because the person the app models is roughly 18 to 32 across the
whole comparison window, and a 25-and-over median is a blend across ages 25 to
65+ that sits well above what a young worker earns. The app's baseline is
consequently too generous in the early years -- when the graduate is still
enrolled, earning nothing, and accruing interest -- and, because it then grows
at a flat 2%/year while real pay in one's twenties climbs much faster, too
stingy later. The errors run in opposite directions and partly cancel; the net
is sensitive to the assumed rate of wage inflation, so this script deliberately
reports the profile rather than a single "corrected" number.

The tempting shortcut -- deflating HS_GRAD_SALARY backwards at that same
2%/year to manufacture an age-18 wage -- is invalid, and is the specific error
this dataset exists to make unnecessary. 2%/year is a *time-series* assumption
about how a statistic drifts over calendar time. An age-earnings slope is a
*life-cycle* quantity describing how one person's pay climbs as they get older.
They are different things, and substituting one for the other lands roughly 33%
high.

Why CPS ASEC and not IPUMS
--------------------------
IPUMS-CPS is the usual tool for this, but it requires registration and an API
key, and its terms restrict redistributing microdata -- a permission question
for a public repo that commits its generated datasets. The Census Bureau's own
public-use file is the same underlying survey, is public domain, and needs no
key: it's a plain download. ACS PUMS (what the NY Fed underemployment data this
app cites is built from) has ~30x the sample and would give a smoother
single-year-of-age curve, but it's a different survey from the one BLS uses for
the headline figure. Staying inside the CPS family means adding an age
dimension without also switching lineages.

Download the raw file (~150MB zipped) from:
    https://www2.census.gov/programs-surveys/cps/datasets/2025/march/asecpub25csv.zip
and pass the pppub25.csv inside it. Bump the year for a newer release; note the
ASEC collected in March of year N reports income for year N-1.

What it does NOT do
-------------------
Nothing in app.py reads the output yet. Changing the HS-graduate baseline moves
every ROI figure, every break-even, and the numbers in the research paper, so
wiring this in is a deliberate modelling decision rather than a data refresh.
This script just makes the evidence available.

Usage:
    python build_hs_age_profile.py pppub25.csv
    python build_hs_age_profile.py pppub25.csv -o data/hs_age_profile.csv
    python build_hs_age_profile.py pppub25.csv --min-sample 100
"""

import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd

# CPS ASEC person-file columns this script needs. The full file is 800+
# columns and ~280MB, so loading only these keeps it to a few seconds.
COLUMNS_TO_LOAD = [
    "A_AGE",       # Age (top-coded at 85)
    "A_HGA",       # Educational attainment, highest grade completed
    "A_USLHRS",    # Usual hours worked per week at main job
    "A_CLSWKR",    # Class of worker (private / government / self-employed)
    "WKSWORK",     # Weeks worked last year
    "WSAL_VAL",    # Wage and salary income last year, in dollars
    "MARSUPWT",    # ASEC supplement person weight
]

# A_HGA == 39 is "High school graduate - high school diploma or equivalent".
# This is the exact cut BLS labels "High school graduates, no college": codes
# above it (40 = some college, 41 = associate, 43 = bachelor's, ...) are
# excluded, as are codes below it (38 and under = no diploma).
HS_GRAD_ATTAINMENT_CODE = 39

# Bachelor's degree and above: 43 bachelor's, 44 master's, 45 professional,
# 46 doctorate. This is the population the app's GRADUATE side describes, so
# the graduate profile pools all four rather than taking 43 alone -- a
# doctoral occupation's curve is not a bachelor's curve, and the app models
# both from one shape.
#
# The profile built from this is used for its SHAPE ONLY, never its level.
# app.py takes every salary level from OEWS, per occupation; what CPS supplies
# is the one thing OEWS structurally cannot, which is an age dimension. See
# GRAD_AGE_PROFILE_PATH in app.py.
GRAD_ATTAINMENT_MIN_CODE = 43

# The two populations this script can profile. Adding one means adding a
# predicate here and nothing else.
POPULATIONS = {
    "hs": {
        "label": "high school graduates, no college",
        "predicate": lambda df: df["A_HGA"] == HS_GRAD_ATTAINMENT_CODE,
        "default_output": "data/hs_age_profile.csv",
    },
    "grad": {
        "label": "bachelor's degree and above",
        "predicate": lambda df: df["A_HGA"] >= GRAD_ATTAINMENT_MIN_CODE,
        "default_output": "data/grad_age_profile.csv",
    },
}

# A sanity band on the graduate/high-school median ratio, checked whenever the
# graduate profile is built. BLS's published weekly-earnings series put
# bachelor's-and-above around 1.7 to 1.9 times high-school-only, and computing
# BOTH from the same microdata makes this a real check on the attainment filter
# rather than a restatement of it -- if A_HGA's codes are ever renumbered, this
# is what notices. It is deliberately wide: the point is to catch a filter that
# has stopped meaning what it says, not to pin a number.
GRAD_HS_RATIO_BAND = (1.4, 2.2)

# Class-of-worker codes for wage and salary workers: private, federal, state
# and local government. Deliberately excludes the self-employed (incorporated
# and not), matching BLS's "wage and salary workers" universe -- self-employment
# income is volatile and isn't what a high-school-graduate wage baseline means.
WAGE_SALARY_CLASS_CODES = {1, 2, 3, 4}

# MARSUPWT is stored with two implied decimal places. Without this divisor the
# weights sum to ~33.8 billion instead of the ~338 million US population, which
# wouldn't change a median (it's scale-invariant) but would make every
# diagnostic printed below nonsense, and would silently corrupt any weighted
# mean or population count added later.
WEIGHT_IMPLIED_DECIMALS = 100.0

# "Full-time, year-round", matching how an annual salary figure is normally
# defined. The app models an annual wage, so year-round is the right cut here
# even though BLS's headline weekly-earnings series doesn't impose it.
FULL_TIME_MIN_HOURS = 35
YEAR_ROUND_MIN_WEEKS = 50

# Age bands rather than single years of age. CPS ASEC yields only ~120-250
# full-time year-round high school graduates per single year of age, which is
# too thin for a stable median; banding trades resolution for a figure that
# doesn't jump around. Bands are narrow early (where the wage curve is steep
# and the app is most sensitive) and widen later.
AGE_BANDS = [
    (18, 20), (21, 23), (24, 26), (27, 29), (30, 32),
    (33, 35), (36, 40), (41, 50), (51, 64),
]

# Minimum unweighted observations for a band to be published. A median over a
# handful of records is noise wearing a number's clothes; better to omit the
# band and say so than to ship it.
DEFAULT_MIN_SAMPLE = 50

# The computed 25+ median is checked against app.py's HS_GRAD_SALARY before
# anything is written. This is the script's only real correctness test: if a
# future ASEC release renumbers A_HGA, or the weight's implied decimals change,
# the filters would still "work" and produce a plausible-looking table of
# entirely wrong numbers. Reproducing the independently published figure is
# what rules that out. The tolerance is wide because the two aren't supposed to
# match exactly -- different survey (ASEC annual wage vs CPS weekly earnings
# x 52), different reference period -- but a miscoded attainment filter misses
# by far more than this.
VALIDATION_TOLERANCE = 0.12


def weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    """Median of `values` weighted by `weights` -- survey microdata is only
    nationally representative once the sampling weights are applied, so an
    unweighted median would describe the sample rather than the country."""
    order = np.argsort(values)
    sorted_values = np.asarray(values)[order]
    cumulative = np.cumsum(np.asarray(weights)[order])
    if cumulative[-1] <= 0:
        return float("nan")
    return float(sorted_values[np.searchsorted(cumulative, cumulative[-1] / 2.0)])


def read_app_baseline(app_path: str = "app.py") -> int:
    """app.py's current HS_GRAD_SALARY, read out of the source rather than
    duplicated here.

    Parsed with a regex instead of importing: `import app` would execute the
    whole Streamlit page, and exec-ing its section 1-2 prefix (the trick
    analyze_model.py uses) is far more machinery than one integer justifies.
    Returns None if it can't be found -- validation is then skipped with a
    warning rather than failing the run, since the profile itself doesn't
    depend on it."""
    try:
        source = Path(app_path).read_text()
    except OSError:
        return None
    match = re.search(r"^HS_GRAD_SALARY\s*=\s*(\d+)", source, flags=re.MULTILINE)
    return int(match.group(1)) if match else None


def load_cps_asec(csv_path: str) -> pd.DataFrame:
    """Read the ASEC person file and apply the population filters."""
    try:
        raw = pd.read_csv(csv_path, usecols=COLUMNS_TO_LOAD, low_memory=False)
    except ValueError as exc:
        raise ValueError(
            f"'{csv_path}' is missing expected CPS ASEC column(s): {exc}. "
            f"Make sure this is the *person* file (pppub##.csv) from "
            f"asecpub##csv.zip, not the household (hhpub) or family (ffpub) file."
        ) from exc

    raw["MARSUPWT"] = raw["MARSUPWT"] / WEIGHT_IMPLIED_DECIMALS

    employed = raw[
        (raw["A_USLHRS"] >= FULL_TIME_MIN_HOURS)
        & (raw["WKSWORK"] >= YEAR_ROUND_MIN_WEEKS)
        & (raw["WSAL_VAL"] > 0)
        & (raw["A_CLSWKR"].isin(WAGE_SALARY_CLASS_CODES))
    ]
    return employed


def validate_grad_ratio(employed: pd.DataFrame) -> float:
    """Reference median for the graduate profile, checked against the HS one.

    There is no app.py constant for graduate pay to validate against the way
    HS_GRAD_SALARY anchors the other profile, so the check is a RELATIVE one:
    both medians come from this same file, and their ratio has to land in the
    band BLS's published series imply. That makes it a real test of the
    attainment filter -- if A_HGA's codes are renumbered, or `>= 43` stops
    meaning "bachelor's and above", the ratio moves and this raises. Comparing
    the graduate median against itself would test nothing.
    """
    adults = employed[employed["A_AGE"] >= 25]
    grads = adults[POPULATIONS["grad"]["predicate"](adults)]
    hs = adults[POPULATIONS["hs"]["predicate"](adults)]
    if grads.empty or hs.empty:
        raise ValueError("No age-25+ graduates or high school graduates matched "
                         "-- check the attainment filters.")
    grad_median = weighted_median(grads["WSAL_VAL"].values, grads["MARSUPWT"].values)
    hs_median = weighted_median(hs["WSAL_VAL"].values, hs["MARSUPWT"].values)
    ratio = grad_median / hs_median
    low, high = GRAD_HS_RATIO_BAND
    print("Validation -- age 25+ median annual wage, both from this file")
    print(f"  bachelor's and above : ${grad_median:>9,.0f}  (n={len(grads):,} unweighted)")
    print(f"  high school only     : ${hs_median:>9,.0f}  (n={len(hs):,} unweighted)")
    print(f"  ratio                : {ratio:>9.2f}  (expected {low} to {high})")
    if not low <= ratio <= high:
        raise ValueError(
            f"The graduate/high-school median ratio is {ratio:.2f}, outside the "
            f"{low}-{high} band BLS's published series imply. Most likely A_HGA's "
            f"codes changed meaning in this release. Do not publish this profile "
            f"until it reconciles.")
    return grad_median


def validate_against_published(hs_grads: pd.DataFrame, baseline: int) -> float:
    """Reproduce the published 25-and-over median before trusting anything
    this file says about ages BLS doesn't publish. Raises on a wide miss."""
    adults = hs_grads[hs_grads["A_AGE"] >= 25]
    if adults.empty:
        raise ValueError("No age-25+ high school graduates matched -- check the filters.")
    computed = weighted_median(adults["WSAL_VAL"].values, adults["MARSUPWT"].values)

    if baseline is None:
        print("Warning: couldn't read HS_GRAD_SALARY from app.py -- skipping validation.")
        return computed

    drift = abs(computed / baseline - 1)
    print(f"Validation -- age 25+ median annual wage")
    print(f"  from this microdata : ${computed:>9,.0f}  (n={len(adults):,} unweighted)")
    print(f"  app.py HS_GRAD_SALARY: ${baseline:>9,.0f}")
    print(f"  difference           : {computed / baseline - 1:>+9.1%}")
    if drift > VALIDATION_TOLERANCE:
        raise ValueError(
            f"The 25+ median computed here is {drift:.1%} away from app.py's "
            f"HS_GRAD_SALARY (tolerance {VALIDATION_TOLERANCE:.0%}). These describe the "
            f"same population and should be close. Something is wrong with the filters "
            f"-- most likely A_HGA == {HS_GRAD_ATTAINMENT_CODE} no longer means "
            f"'high school graduate' in this release, or MARSUPWT's implied decimals "
            f"changed. Do not publish this profile until it reconciles."
        )
    print("  -> reconciles.\n")
    return computed


def build_age_profile(hs_grads: pd.DataFrame, reference_median: float,
                      min_sample: int) -> pd.DataFrame:
    """Median and mean annual wage per age band, plus each band's ratio to the
    published 25+ figure. The ratio is the portable part: it's a shape, so it
    survives a vintage change far better than the dollar levels do."""
    rows, skipped = [], []
    for low, high in AGE_BANDS:
        band = hs_grads[(hs_grads["A_AGE"] >= low) & (hs_grads["A_AGE"] <= high)]
        if len(band) < min_sample:
            skipped.append(f"{low}-{high} (n={len(band)})")
            continue
        weights = band["MARSUPWT"].values
        wages = band["WSAL_VAL"].values
        median = weighted_median(wages, weights)
        rows.append({
            "age_low": low,
            "age_high": high,
            "median_wage": round(median),
            # Mean alongside median deliberately: CPS income responses heap on
            # round numbers ($45,000, $50,000), which flattens adjacent medians
            # into looking identical. The mean is skewed by high earners but
            # doesn't heap, so a reader comparing the two can see where the
            # median is an artifact of rounding rather than a real plateau.
            "mean_wage": round(float(np.average(wages, weights=weights))),
            "ratio_to_25plus": round(median / reference_median, 4),
            "n_unweighted": len(band),
        })
    if skipped:
        print(f"Omitted {len(skipped)} band(s) below the {min_sample}-observation "
              f"minimum: {', '.join(skipped)}")
    if not rows:
        raise ValueError("No age band met the minimum sample size -- nothing to write.")
    return pd.DataFrame(rows)


def print_summary(profile: pd.DataFrame, reference_median: float, baseline: int) -> None:
    print()
    print(f"Age-earnings profile ({len(profile)} bands, "
          f"n={profile.n_unweighted.sum():,} unweighted):")
    print(f"  {'age':>7}  {'median':>9}  {'mean':>9}  {'vs 25+':>7}  {'n':>6}")
    for row in profile.itertuples():
        print(f"  {row.age_low:>3}-{row.age_high:<3}  ${row.median_wage:>8,}  "
              f"${row.mean_wage:>8,}  {row.ratio_to_25plus:>7.3f}  {row.n_unweighted:>6,}")

    if baseline is None:
        return
    # .iloc on an all-numeric frame returns a float64 Series, which would print
    # ages as "18.0-year-old" -- pull the fields back to int explicitly.
    youngest = profile.iloc[0]
    young_low, young_high = int(youngest.age_low), int(youngest.age_high)
    young_wage = int(youngest.median_wage)
    print()
    print(f"app.py starts its baseline at ${baseline:,} for an {young_low}-year-old. "
          f"This microdata puts\nan {young_low}-to-{young_high}-year-old at "
          f"${young_wage:,} ({baseline / young_wage - 1:+.0%}).")
    print("That gap narrows and eventually reverses as the flat 2%/yr assumption "
          "falls behind\nreal early-career wage growth -- see the age slope below.")

    mid_career = profile[profile.age_high <= 35].iloc[-1]
    mid_low, mid_wage = int(mid_career.age_low), int(mid_career.median_wage)
    years = mid_low - young_low
    if years > 0:
        slope = (mid_wage / young_wage) ** (1 / years) - 1
        print(f"\nImplied real age slope, {young_low} -> {mid_low}: "
              f"{slope:.2%}/yr (constant dollars).")
        print("app.py assumes 2.00%/yr nominal, which is negative in real terms.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Build an age-earnings profile for high school graduates "
                    "from CPS ASEC public-use microdata."
    )
    parser.add_argument("input_csv",
                        help="Path to the CPS ASEC person file (pppub##.csv), from "
                             "asecpub##csv.zip at www2.census.gov/programs-surveys/cps/datasets/")
    parser.add_argument("--population", choices=sorted(POPULATIONS), default="hs",
                        help="Which population to profile: 'hs' (high school only, "
                             "the ROI baseline) or 'grad' (bachelor's and above, the "
                             "shape the graduate earnings curve decelerates along). "
                             "Default: hs, so the original invocation is unchanged.")
    parser.add_argument("-o", "--output", default=None,
                        help="Output CSV path (default: per --population, "
                             "data/hs_age_profile.csv or data/grad_age_profile.csv)")
    parser.add_argument("--min-sample", type=int, default=DEFAULT_MIN_SAMPLE,
                        help=f"Minimum unweighted observations per age band "
                             f"(default: {DEFAULT_MIN_SAMPLE})")
    parser.add_argument("--app", default="app.py",
                        help="Path to app.py, read for HS_GRAD_SALARY to validate against.")
    args = parser.parse_args()

    try:
        employed = load_cps_asec(args.input_csv)
        population = POPULATIONS[args.population]
        print(f"Population: {population['label']}\n")
        if args.population == "grad":
            reference = validate_grad_ratio(employed)
        else:
            reference = validate_against_published(
                employed[population["predicate"](employed)],
                read_app_baseline(args.app))
        profile = build_age_profile(
            employed[population["predicate"](employed)], reference, args.min_sample)
    except FileNotFoundError:
        raise SystemExit(
            f"Error: could not find '{args.input_csv}'. Download asecpub##csv.zip from "
            f"www2.census.gov/programs-surveys/cps/datasets/ and pass the pppub##.csv inside it."
        )
    except ValueError as exc:
        raise SystemExit(f"Error: {exc}")

    output = args.output or POPULATIONS[args.population]["default_output"]
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    profile.to_csv(output, index=False)
    print(f"\nWrote {len(profile)} rows to {output}")
    print_summary(profile, reference, baseline)

"""
Student Loan Payoff & Major ROI Calculator
--------------------------------------------
A Streamlit application that lets a prospective student model the financial
outcome of a chosen major and loan strategy, and that quietly collects
anonymous usage/survey data to support a companion research paper on how
this kind of tool influences student decision-making.

Architecture:
  1. Constants & Data      -> hardcoded BLS-style salary baselines.
  2. Helper Functions       -> financial math, file I/O, chart builders, API calls.
  3. Session State Setup    -> flags that make Streamlit's rerun-on-every-widget
                                model behave like a normal single-page app.
  4. Sidebar (Inputs)       -> user profile + admin toggle.
  5. Main Page              -> admin dashboard (optional), calculator results,
                                and the impact survey.
"""

from datetime import datetime

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
import streamlit.components.v1 as components
from st_supabase_connection import SupabaseConnection, execute_query

# ============================================================
# 1. CONFIGURATION & CONSTANTS
# ============================================================

COLLEGE_SCORECARD_URL = "https://api.data.gov/ed/collegescorecard/v1/schools.json"

# Starting salary + mid-career (median) salary per major, sourced from the
# U.S. Bureau of Labor Statistics Occupational Employment and Wage Statistics
# (OEWS), May 2023 national estimates (bls.gov/oes/2023/may/). Each major is
# mapped to its closest BLS-tracked occupation (SOC code below).
# "starting_salary" is that occupation's 25th-percentile annual wage (a proxy
# for an entry-level new grad); "median_salary" is the occupation's median
# annual wage (a proxy for a mid-career worker, ~10 years in). 25th percentile
# rather than 10th: OEWS's 10th percentile mixes in part-time/reporting-quirk
# outliers (most visible for occupations like physicians, where it understates
# real entry-level pay dramatically -- 10th pct $68,890 vs. this 25th pct
# $152,810 for Family Medicine Physicians below) and produced unrealistically
# low starting-salary figures. The annual growth rate implied between these
# two real BLS figures is derived on demand by get_major_growth_rate() -- see
# 2a -- rather than stored separately, so the loan/ROI simulation's year-10
# salary always matches this median exactly.
CURATED_MAJOR_DATA = {
    # Software Developers, SOC 15-1252: 25th pct $101,200 / median $132,270
    "Computer Science": {"starting_salary": 101200, "median_salary": 132270},
    # Registered Nurses, SOC 29-1141: 25th pct $75,990 / median $86,070
    "Nursing": {"starting_salary": 75990, "median_salary": 86070},
    # Business Operations Specialists, All Other, SOC 13-1199: 25th pct $59,010 / median $79,590
    "Business": {"starting_salary": 59010, "median_salary": 79590},
    # Financial and Investment Analysts, SOC 13-2051: 25th pct $76,880 / median $99,010
    "Finance": {"starting_salary": 76880, "median_salary": 99010},
    # Market Research Analysts and Marketing Specialists, SOC 13-1161: 25th pct $52,840 / median $74,680
    "Humanities": {"starting_salary": 52840, "median_salary": 74680},
    # Fine Artists, Including Painters, Sculptors, and Illustrators, SOC 27-1013: 25th pct $38,160 / median $59,300
    "Arts": {"starting_salary": 38160, "median_salary": 59300},
    # Coaches and Scouts, SOC 27-2022: 25th pct $32,440 / median $45,910
    "Sports Management": {"starting_salary": 32440, "median_salary": 45910},
    # Exercise Physiologists, SOC 29-1128: 25th pct $45,870 / median $54,860
    "Exercise Science": {"starting_salary": 45870, "median_salary": 54860},
    # Athletic Trainers, SOC 29-9091: 25th pct $49,750 / median $57,930. BLS
    # now lists a master's as the typical entry-level education, so this
    # major has a 2-year unpaid training delay (the accredited master's
    # program) before the salary above applies -- see get_annual_salary_for_year.
    "Athletic Training": {
        "starting_salary": 49750, "median_salary": 57930,
        "unpaid_training_years": 2,
    },
    # Family Medicine Physicians, SOC 29-1215: 25th pct $152,810 / median
    # $224,640. 4 unpaid years (med school) + 3 stipend years (residency;
    # 3-year length matches Family Medicine's real ACGME program length, so
    # this pathway is internally consistent). Stipend is AAMC's 2024
    # preliminary median first-post-MD-year resident stipend ($65,100),
    # used as a flat representative figure across residency (real PGY2/PGY3
    # pay is a few thousand higher). additional_training_debt is AAMC's 2024
    # median medical school debt ($205,000, aamc.org/data-reports/students-
    # residents) -- added to the user's loan slider as the true principal.
    "Medicine": {
        "starting_salary": 152810, "median_salary": 224640,
        "unpaid_training_years": 4, "stipend_training_years": 3,
        "stipend_salary": 65000, "additional_training_debt": 205000,
    },
    # Lawyers, SOC 23-1011: 25th pct $98,030 / median $145,760. 3 unpaid
    # years (law school, no paid-training equivalent). additional_training_
    # debt is the ABA Young Lawyers Division 2024 Student Loan Survey's
    # average law-school-only debt ($130,000, americanbar.org).
    "Law": {
        "starting_salary": 98030, "median_salary": 145760,
        "unpaid_training_years": 3, "additional_training_debt": 130000,
    },
}

# BLS OEWS-sourced careers from data_pipeline.py's output, in the same
# {major_name: {starting_salary, median_salary}} shape as the curated dict
# above, so every existing calculation (get_major_growth_rate,
# get_annual_salary_for_year, etc.) works on them identically -- no
# special-casing needed anywhere else in the app. Two geographic scopes are
# available (see the "Career Salary Data" sidebar selector in section 4,
# which picks one of these paths and builds the final MAJOR_DATA from it).
CAREERS_CSV_PATH_NATIONAL = "cleaned_careers.csv"
CAREERS_CSV_PATH_CA = "cleaned_careers_ca.csv"


@st.cache_data
def load_bls_careers(csv_path: str) -> dict:
    try:
        careers_df = pd.read_csv(csv_path)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return {}
    return {
        row.occ_title: {"starting_salary": row.a_pct25, "median_salary": row.a_median}
        for row in careers_df.itertuples()
    }

# Baseline comparison group: a high school graduate (no college) who takes on
# no loans. Annual figure is real BLS Current Population Survey data: median
# usual weekly earnings for full-time workers age 25+ with a high school
# diploma and no college, Q3 2024, was $946/week (bls.gov/opub/ted/2024/
# median-weekly-earnings-946-for-workers-with-high-school-diploma...htm),
# annualized as $946 * 52. BLS does not publish a matching by-experience wage
# growth trajectory for this group, so growth_rate remains a modest assumption
# reflecting ordinary cost-of-living/seniority raises rather than freezing pay
# for a decade.
HS_GRAD_SALARY = 49192
HS_GRAD_GROWTH_RATE = 0.02

# Income-Driven Repayment (IDR) assumptions, modeled after undergraduate
# REPAYE/SAVE-style plans: 10% of discretionary income, where discretionary
# income is pay above a flat living allowance, with unpaid balances forgiven
# after a fixed number of years if never fully repaid.
IDR_LIVING_ADJUSTMENT = 22000
IDR_PAYMENT_RATE = 0.10
IDR_MAX_TERM_YEARS = 20

STANDARD_TERM_YEARS = 10
ROI_WINDOW_YEARS = 10

# Assumed bachelor's degree length, for converting the per-year Cost of
# Attendance / Personal Contribution sidebar inputs into 4-year totals (the
# figures every downstream calculation -- effective_principal, ROI,
# take-home snapshot -- actually operates on). Distinct from
# STANDARD_TERM_YEARS (loan repayment term) and IDR_MAX_TERM_YEARS
# (forgiveness horizon) -- this is how long you're *enrolled*, not how long
# you're *repaying*.
UNDERGRAD_YEARS = 4

# Cost of Attendance inflation estimate: CAGR between these two fixed
# College Scorecard data years (school-specific, via the API's year-prefixed
# fields, e.g. "2018.cost.attendance.academic_year"). Fixed years rather
# than "latest" keep the estimate stable across app runs instead of
# silently drifting whenever College Scorecard releases newer data.
COA_INFLATION_START_YEAR = 2018
COA_INFLATION_END_YEAR = 2022

# Fallback annual COA inflation rate by institution control type, used when
# a school-specific estimate isn't available (no API key, missing year
# data, or no school entered). Source: College Board, Trends in College
# Pricing 2024 (nominal year-over-year increase, 2023-24 -> 2024-25) for
# Public/Private Non-Profit. Private For-Profit has no equivalent recent
# nominal figure readily published -- NCES Fast Facts shows for-profit
# *real* (inflation-adjusted) tuition has been flat-to-declining over the
# last decade, so its nominal growth is assumed to track general price
# inflation rather than the tuition-specific premium seen in the other two
# sectors. This is a modeling judgment call, not a directly sourced figure.
CATEGORY_COA_INFLATION_RATES = {
    "Public": 0.027,
    "Private Non-Profit": 0.039,
    "Private For-Profit": 0.025,
}
DEFAULT_COA_INFLATION_RATE = 0.027  # Public rate, used when control type is unknown

# Federal income tax, 2024, single filer. Source: IRS Rev. Proc. 2023-34.
# Brackets are (upper bound of bracket, marginal rate on income up to that
# bound). Scope: single filer only, no dependents, no itemized deductions or
# credits -- standard deduction is the only reduction applied.
STANDARD_DEDUCTION_2024_SINGLE = 14600
FEDERAL_TAX_BRACKETS_2024_SINGLE = [
    (11600, 0.10),
    (47150, 0.12),
    (100525, 0.22),
    (191950, 0.24),
    (243725, 0.32),
    (609350, 0.35),
    (float("inf"), 0.37),
]

# FICA, 2024. Source: SSA (ssa.gov/oact/cola/cbb.html) + IRS Topic 751.
# Additional Medicare Tax (extra 0.9% over $200k) is intentionally excluded --
# no major's starting-to-median salary trajectory reaches that threshold.
SOCIAL_SECURITY_WAGE_BASE_2024 = 168600
SOCIAL_SECURITY_RATE = 0.062
MEDICARE_RATE = 0.0145

# State income tax, 2024. Source: Tax Foundation state income tax tables
# (taxfoundation.org/data/all/state/state-income-tax-rates-2024/). NY, CA,
# and OH are modeled as real marginal brackets (a flat top-marginal-rate
# approximation would badly overstate tax at these salary levels -- e.g. NY's
# 10.9% top rate only applies above $25M of income). IL, GA, CO, and TX are
# genuinely flat/zero-rate states, represented as a single-bracket list so
# every state shares the same bracket-summation logic (see _apply_marginal_
# brackets in 2h). Local/city taxes beyond the NYC line-item in CITY_DATA,
# and any state-specific credits or exemptions, are not modeled.
STATE_TAX_BRACKETS = {
    "NY": {
        "brackets": [
            (8500, 0.04), (11700, 0.045), (13900, 0.0525), (80650, 0.055),
            (215400, 0.06), (1077550, 0.0685), (5000000, 0.0965),
            (25000000, 0.103), (float("inf"), 0.109),
        ],
        "standard_deduction": 8000,
    },
    "CA": {
        "brackets": [
            (10756, 0.01), (25499, 0.02), (40245, 0.04), (55866, 0.06),
            (70606, 0.08), (360659, 0.093), (432787, 0.103),
            (721314, 0.113), (1000000, 0.123), (float("inf"), 0.133),
        ],
        "standard_deduction": 5540,
    },
    "OH": {
        "brackets": [(26050, 0.0), (100000, 0.0275), (float("inf"), 0.035)],
        "standard_deduction": 0,
    },
    "IL": {"brackets": [(float("inf"), 0.0495)], "standard_deduction": 0},
    "GA": {"brackets": [(float("inf"), 0.0539)], "standard_deduction": 0},
    "CO": {"brackets": [(float("inf"), 0.0425)], "standard_deduction": 0},
    "TX": {"brackets": [(float("inf"), 0.0)], "standard_deduction": 0},
    "MN": {
        # Real 2024 brackets (Minnesota Dept. of Revenue).
        "brackets": [
            (31690, 0.0535), (104090, 0.068), (193240, 0.0785), (float("inf"), 0.0985),
        ],
        "standard_deduction": 14950,
    },
    "PA": {"brackets": [(float("inf"), 0.0307)], "standard_deduction": 0},
    "AZ": {"brackets": [(float("inf"), 0.025)], "standard_deduction": 0},
    "MI": {"brackets": [(float("inf"), 0.0425)], "standard_deduction": 0},
    "NC": {"brackets": [(float("inf"), 0.045)], "standard_deduction": 0},
    # MA is a flat 5% up to $1M/year, plus a 4% "millionaire's tax" surtax
    # above that -- omitted here since no major's trajectory in this app
    # gets anywhere close to $1M, the same scope limitation already noted
    # for the federal Additional Medicare Tax above.
    "MA": {"brackets": [(float("inf"), 0.05)], "standard_deduction": 0},
    "FL": {"brackets": [(float("inf"), 0.0)], "standard_deduction": 0},
    "WA": {"brackets": [(float("inf"), 0.0)], "standard_deduction": 0},
    "TN": {"brackets": [(float("inf"), 0.0)], "standard_deduction": 0},
}

# Cost of living by metro area, from BEA Regional Price Parities (RPP), 2023
# release (national average = 100), sourced via Tax Foundation's "Real Value
# of $100 by Metro" compilation of the same BEA data:
# col_index = 10000 / real_value_of_$100. "state_key" of None (National
# Average) means state tax is not modeled -- shown as "N/A" in the UI, not
# "$0", since those are different claims. NYC's local_tax_rate is a flat
# approximation of its real 3.078%-3.876% resident bracket range.
CITY_DATA = {
    "National Average": {"state_key": None, "col_index": 100.0, "local_tax_rate": 0.0},
    "New York, NY": {"state_key": "NY", "col_index": 112.5, "local_tax_rate": 0.035},
    "San Francisco, CA": {"state_key": "CA", "col_index": 118.2, "local_tax_rate": 0.0},
    "Chicago, IL": {"state_key": "IL", "col_index": 102.6, "local_tax_rate": 0.0},
    "Austin, TX": {"state_key": "TX", "col_index": 97.6, "local_tax_rate": 0.0},
    "Atlanta, GA": {"state_key": "GA", "col_index": 100.9, "local_tax_rate": 0.0},
    "Columbus, OH": {"state_key": "OH", "col_index": 94.5, "local_tax_rate": 0.0},
    "Denver, CO": {"state_key": "CO", "col_index": 105.5, "local_tax_rate": 0.0},
    "Los Angeles, CA": {"state_key": "CA", "col_index": 115.47, "local_tax_rate": 0.0},
    "San Diego, CA": {"state_key": "CA", "col_index": 111.49, "local_tax_rate": 0.0},
    "Dallas, TX": {"state_key": "TX", "col_index": 103.30, "local_tax_rate": 0.0},
    "Houston, TX": {"state_key": "TX", "col_index": 100.22, "local_tax_rate": 0.0},
    "San Antonio, TX": {"state_key": "TX", "col_index": 93.73, "local_tax_rate": 0.0},
    "Cleveland, OH": {"state_key": "OH", "col_index": 93.05, "local_tax_rate": 0.0},
    "Miami, FL": {"state_key": "FL", "col_index": 111.82, "local_tax_rate": 0.0},
    "Seattle, WA": {"state_key": "WA", "col_index": 113.00, "local_tax_rate": 0.0},
    "Nashville, TN": {"state_key": "TN", "col_index": 97.43, "local_tax_rate": 0.0},
    "Philadelphia, PA": {"state_key": "PA", "col_index": 103.55, "local_tax_rate": 0.0},
    "Boston, MA": {"state_key": "MA", "col_index": 111.57, "local_tax_rate": 0.0},
    "Phoenix, AZ": {"state_key": "AZ", "col_index": 105.52, "local_tax_rate": 0.0},
    "Detroit, MI": {"state_key": "MI", "col_index": 98.01, "local_tax_rate": 0.0},
    "Charlotte, NC": {"state_key": "NC", "col_index": 96.97, "local_tax_rate": 0.0},
    "Minneapolis, MN": {"state_key": "MN", "col_index": 104.50, "local_tax_rate": 0.0},
}

# Maps a UI-facing career-stage label to a 0-based year_index, fed straight
# into get_annual_salary_for_year. This only ever drives the take-home/COL
# snapshot (5d) -- never the loan amortization or ROI simulation, which
# always starts from year 0 and grows independently of this selection. For
# majors with a training delay (Medicine, Law, Athletic Training), "Starting
# (Year 1)" correctly shows $0 or a training stipend, not a professional
# salary -- that's intentional, see get_annual_salary_for_year.
CAREER_STAGE_OPTIONS = {
    "Starting (Year 1)": 0,
    "Mid-Career (Year 10)": 9,
}


# ============================================================
# 2. HELPER FUNCTIONS
# ============================================================

# ---- 2a. Formatting -----------------------------------------------------

def fmt_money(value):
    return f"${value:,.0f}"


def fmt_pct(value):
    return f"{value:.1f}%"


def get_major_growth_rate(major_name: str) -> float:
    """CAGR from a major's starting_salary to its median_salary over 10 years
    of actually practicing (excludes any training delay -- see
    get_annual_salary_for_year)."""
    data = MAJOR_DATA[major_name]
    return (data["median_salary"] / data["starting_salary"]) ** (1 / 10) - 1


def get_annual_salary_for_year(major_name: str, year_index: int) -> float:
    """Gross salary in a given year post-bachelor's (0-based: 0 = first year
    out). Majors with no training delay (unpaid_training_years/
    stipend_training_years both default to 0) collapse to exactly the
    original starting_salary * (1+growth_rate)**year_index formula. Majors
    like Medicine/Law/Athletic Training spend their first N years earning
    $0 (school) or a flat stipend (e.g. medical residency) before the real
    salary curve begins -- every other calculation (loan simulation, ROI,
    take-home snapshot) must call this instead of reading MAJOR_DATA fields
    directly, so they can never disagree about what a major earns in a
    given year."""
    data = MAJOR_DATA[major_name]
    unpaid_years = data.get("unpaid_training_years", 0)
    stipend_years = data.get("stipend_training_years", 0)
    years_to_practice = unpaid_years + stipend_years
    if year_index < unpaid_years:
        return 0.0
    if year_index < years_to_practice:
        return data.get("stipend_salary", 0)
    practicing_year = year_index - years_to_practice
    return data["starting_salary"] * (1 + get_major_growth_rate(major_name)) ** practicing_year


def get_effective_principal(major_name: str, loan_amount: float) -> float:
    """The true total debt behind a major's salary, including any
    professional-school debt beyond the undergrad loan slider (e.g.
    Medicine's median medical school debt). Used as the actual loan
    principal AND the ROI% denominator -- see calculate_roi."""
    return loan_amount + MAJOR_DATA[major_name].get("additional_training_debt", 0)


# ---- 2b. Usage / Survey Logging (Supabase) -------------------------------
# Backed by a hosted Postgres table (via st-supabase-connection) instead of
# local CSVs, since Streamlit Community Cloud's filesystem is ephemeral --
# local files would be silently wiped on every sleep/restart, defeating the
# whole point of logging this data for the companion research paper.

@st.cache_resource
def get_supabase_connection():
    return st.connection("supabase_connection", type=SupabaseConnection)


def log_usage_event(action: str):
    """Insert a single usage event into the usage_logs table."""
    conn = get_supabase_connection()
    execute_query(
        conn.table("usage_logs").insert(
            [{"timestamp": datetime.now().isoformat(), "action": action}],
            count="None",
        ),
        ttl=0,
    )


def save_survey_response(perception_change: str, feedback_text: str, context: dict) -> bool:
    """Insert one anonymous survey submission into the survey_responses
    table. `context` is a flat {column_name: value} dict carrying every
    simulation-context field: school name, Scenario A's inputs/outputs, and
    -- when Compare Mode is on at submission time -- Scenario B's
    inputs/outputs plus the ROI delta between them. A dict (rather than a
    long, ever-growing list of named params) lets the row shape keep
    growing without this function's signature growing with it.

    The caller reads every context value straight from the sidebar/Compare
    Mode widget variables, which Streamlit re-evaluates to their current
    value on every rerun -- including the rerun triggered by clicking
    "Submit Feedback" -- so this always captures the exact slider/dropdown
    state at click-time, never a stale value from an earlier run.

    Returns True on success, False on any failure (network, bad
    credentials, schema mismatch) so the caller can tell the user their
    submission didn't save instead of silently losing it.
    """
    try:
        conn = get_supabase_connection()
        row = {
            "timestamp": datetime.now().isoformat(),
            "perception_change": perception_change,
            "feedback_text": feedback_text,
            **context,
        }
        execute_query(
            conn.table("survey_responses").insert([row], count="None"),
            ttl=0,
        )
        return True
    except Exception:
        return False


def load_table_safe(table_name: str, columns: list) -> pd.DataFrame:
    """Read all rows from a Supabase table, tolerating any connection/query
    failure (e.g. secrets not configured yet) by returning an empty frame."""
    try:
        conn = get_supabase_connection()
        result = execute_query(conn.table(table_name).select("*"), ttl=0)
        return pd.DataFrame(result.data) if result.data else pd.DataFrame(columns=columns)
    except Exception:
        return pd.DataFrame(columns=columns)


# ---- 2c. School Data: Local COA Dataset + College Scorecard API ---------
# Hybrid design: in_state_coa/out_of_state_coa come from a pre-cleaned local
# dataset (see clean_college_scorecard.py) instead of a live API call, since
# that script already derives them correctly (including the public-school
# in-state/out-of-state tuition swap) and doesn't cost an API request per
# lookup. median_debt has no equivalent in that dataset, so it's still
# fetched live -- this works for any school, not just ones in the local
# dataset's current (small sample) coverage.

COA_DATASET_PATH = "data/college_coa_clean.csv"


@st.cache_data(show_spinner=False)
def load_coa_dataset() -> pd.DataFrame:
    """Load the pre-cleaned local COA dataset, tolerating a missing file
    (e.g. before it's been generated) by returning an empty frame."""
    try:
        return pd.read_csv(COA_DATASET_PATH)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return pd.DataFrame(columns=["INSTNM", "control_type", "in_state_coa", "out_of_state_coa"])


def find_school_coa(school_name: str, coa_df: pd.DataFrame):
    """Case-insensitive lookup by institution name: exact match first, then
    falls back to a substring match so e.g. "Michigan" still finds
    "University of Michigan-Ann Arbor". Returns None if nothing matches --
    expected while the local dataset only covers a small sample of schools."""
    if not school_name or coa_df.empty:
        return None
    names_lower = coa_df["INSTNM"].str.lower()
    query_lower = school_name.strip().lower()
    exact = coa_df[names_lower == query_lower]
    if not exact.empty:
        return exact.iloc[0]
    partial = coa_df[names_lower.str.contains(query_lower, regex=False)]
    return partial.iloc[0] if not partial.empty else None


def get_suggested_coa_per_year(school_name: str, in_state: bool):
    """Cost of Attendance (in-state or out-of-state, per `in_state`) for a
    school in the local COA dataset, for auto-filling a scenario's per-year
    cost -- or None if the school has no match (expected while the dataset
    only covers a small sample; see find_school_coa)."""
    match = find_school_coa(school_name, load_coa_dataset())
    if match is None:
        return None
    return float(match["in_state_coa"] if in_state else match["out_of_state_coa"])


def _autofill_coa(school_key: str, in_state_key: str, coa_key: str):
    """on_change callback for a school text_input or its In-State checkbox:
    suggests a per-year Cost of Attendance into the paired number_input's
    session_state key when the school matches the local COA dataset. A
    no-match (or the field being cleared) is a no-op -- it never resets a
    manually-entered COA estimate just because the lookup came up empty.
    Must write to st.session_state directly (not return a value) since
    callbacks run before the script reruns, and a number_input's value=
    argument only sets its first-render default, not later reruns, once it
    has a key."""
    suggested = get_suggested_coa_per_year(
        st.session_state.get(school_key, ""),
        st.session_state.get(in_state_key, False),
    )
    if suggested is not None:
        st.session_state[coa_key] = suggested


def get_coa_confirmation_caption(school_name: str, match, in_state: bool):
    """One-line confirmation of what the local COA dataset matched (or
    didn't), meant to render immediately under the school name field so a
    student sees Cost of Attendance feedback right away rather than several
    sections down the page. Takes an already-looked-up `match` (from
    find_school_coa) rather than re-querying, so the caller can reuse the
    same match for control_type / inflation-rate purposes too. Returns None
    (render nothing) if the school field is empty."""
    if not school_name:
        return None
    if match is None:
        return "No Cost of Attendance match in the local dataset yet -- enter your own estimate below."
    label = "In-state" if in_state else "Out-of-state"
    coa_value = match["in_state_coa"] if in_state else match["out_of_state_coa"]
    return (
        f"✅ {match['INSTNM']} ({match['control_type']}) — {label} COA: {fmt_money(coa_value)}/year"
    ).replace("$", r"\$")


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_median_debt(school_name: str, api_key: str):
    """
    Look up median completer debt for a school via the College Scorecard
    API. Returns None (rather than raising) on any failure so a missing
    key, bad network, or unmatched school name never breaks the calculator.
    """
    if not school_name or not api_key:
        return None
    params = {
        "school.name": school_name,
        "fields": "school.name,latest.aid.median_debt.completers.overall",
        "api_key": api_key,
    }
    try:
        response = requests.get(COLLEGE_SCORECARD_URL, params=params, timeout=6)
        response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            return None
        top = results[0]
        return {
            "name": top.get("school.name"),
            "median_debt": top.get("latest.aid.median_debt.completers.overall"),
        }
    except (requests.exceptions.RequestException, ValueError, KeyError):
        return None


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_school_coa_history(school_name: str, api_key: str):
    """
    Look up a school's Cost of Attendance for the two fixed reference years
    (COA_INFLATION_START_YEAR/END_YEAR), for estimating a school-specific
    COA inflation rate. Fixed years (not "latest") keep the estimate stable
    across app runs rather than silently drifting whenever College
    Scorecard releases newer data. Returns None on any failure.
    """
    if not school_name or not api_key:
        return None
    params = {
        "school.name": school_name,
        "fields": (
            "school.name,"
            f"{COA_INFLATION_START_YEAR}.cost.attendance.academic_year,"
            f"{COA_INFLATION_END_YEAR}.cost.attendance.academic_year"
        ),
        "api_key": api_key,
    }
    try:
        response = requests.get(COLLEGE_SCORECARD_URL, params=params, timeout=6)
        response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            return None
        top = results[0]
        return {
            "coa_start": top.get(f"{COA_INFLATION_START_YEAR}.cost.attendance.academic_year"),
            "coa_end": top.get(f"{COA_INFLATION_END_YEAR}.cost.attendance.academic_year"),
        }
    except (requests.exceptions.RequestException, ValueError, KeyError):
        return None


def estimate_coa_inflation_rate(school_name: str, api_key: str, control_type) -> float:
    """
    School-specific annual COA inflation rate: the CAGR between
    COA_INFLATION_START_YEAR and COA_INFLATION_END_YEAR from a live College
    Scorecard lookup. Falls back to a category rate (by control_type --
    "Public"/"Private Non-Profit"/"Private For-Profit", from the local COA
    dataset match; may be None) when school-specific history is
    unavailable, and to DEFAULT_COA_INFLATION_RATE when control_type itself
    is unknown. Always returns a usable number, never None, since the loan
    calculation needs *some* rate every run.
    """
    history = fetch_school_coa_history(school_name, api_key)
    if history and history.get("coa_start") and history.get("coa_end"):
        years = COA_INFLATION_END_YEAR - COA_INFLATION_START_YEAR
        return (history["coa_end"] / history["coa_start"]) ** (1 / years) - 1
    return CATEGORY_COA_INFLATION_RATES.get(control_type, DEFAULT_COA_INFLATION_RATE)


# ---- 2d. Financial Math: Standard Amortization ---------------------------

def calculate_standard_repayment(principal: float, annual_rate_pct: float,
                                  term_years: int = STANDARD_TERM_YEARS) -> dict:
    """
    Fixed-payment amortization: the classic loan formula where a constant
    monthly payment is split between interest (on the remaining balance)
    and principal, fully retiring the loan in exactly `term_years`.
    """
    monthly_rate = annual_rate_pct / 100 / 12
    n_months = term_years * 12

    if monthly_rate == 0:
        monthly_payment = principal / n_months
    else:
        monthly_payment = principal * monthly_rate / (1 - (1 + monthly_rate) ** -n_months)

    balance = principal
    total_interest = 0.0
    schedule_rows = []

    for month in range(1, n_months + 1):
        interest = balance * monthly_rate
        principal_paid = monthly_payment - interest
        balance = max(balance - principal_paid, 0.0)
        total_interest += interest
        schedule_rows.append({"month": month, "year": month / 12, "balance": balance})
        if balance <= 0:
            break

    schedule_df = pd.DataFrame(schedule_rows)
    total_paid = monthly_payment * len(schedule_df)

    return {
        "monthly_payment": monthly_payment,
        "total_interest": total_interest,
        "payoff_years": schedule_df["month"].iloc[-1] / 12,
        "schedule": schedule_df,
        "total_paid_in_roi_window": min(total_paid, monthly_payment * min(len(schedule_df), ROI_WINDOW_YEARS * 12)),
        "forgiven_amount": 0.0,
    }


# ---- 2e. Financial Math: Income-Driven Repayment --------------------------

def calculate_idr_repayment(principal: float, annual_rate_pct: float,
                             major_name: str,
                             living_adjustment: float = IDR_LIVING_ADJUSTMENT,
                             payment_rate: float = IDR_PAYMENT_RATE,
                             max_term_years: int = IDR_MAX_TERM_YEARS) -> dict:
    """
    Models a payment as 10% of discretionary income (salary above a flat
    living allowance). Salary each year comes from get_annual_salary_for_year,
    so majors with a training delay (Medicine, Law, Athletic Training) pay
    $0 while their salary there is $0. Because the payment is income-based
    rather than balance-based, it can fall below the interest accruing that
    month (negative amortization); any balance still outstanding after
    `max_term_years` is forgiven.
    """
    monthly_rate = annual_rate_pct / 100 / 12
    balance = principal
    total_interest = 0.0
    total_paid_in_roi_window = 0.0
    forgiven_amount = 0.0
    schedule_rows = []
    max_months = max_term_years * 12

    for month in range(1, max_months + 1):
        year_index = (month - 1) // 12
        current_salary = get_annual_salary_for_year(major_name, year_index)
        discretionary_monthly = max((current_salary / 12) - (living_adjustment / 12), 0.0)
        payment = discretionary_monthly * payment_rate

        interest = balance * monthly_rate
        balance = max(balance + interest - payment, 0.0)

        total_interest += interest
        if month <= ROI_WINDOW_YEARS * 12:
            total_paid_in_roi_window += payment

        schedule_rows.append({"month": month, "year": month / 12, "balance": balance, "payment": payment})
        if balance <= 0:
            break
    else:
        # Loop exhausted max_term_years without reaching a zero balance:
        # remaining principal is forgiven under the IDR plan.
        forgiven_amount = balance
        balance = 0.0
        schedule_rows.append({"month": max_months, "year": max_months / 12, "balance": 0.0, "payment": 0.0})

    schedule_df = pd.DataFrame(schedule_rows)

    return {
        "total_interest": total_interest,
        "payoff_years": schedule_df["month"].iloc[-1] / 12,
        "schedule": schedule_df,
        "total_paid_in_roi_window": total_paid_in_roi_window,
        "forgiven_amount": forgiven_amount,
    }


# ---- 2f. 10-Year ROI ------------------------------------------------------

def calculate_roi(major_name: str, total_loan_payments_in_window: float,
                   total_investment: float, col_index: float = 100.0,
                   years: int = ROI_WINDOW_YEARS) -> dict:
    """
    ROI = (major's cumulative earnings over `years`, minus loan payments made
    in that window) compared against a debt-free high school graduate's
    cumulative earnings over the same window. `total_investment` is the ROI%
    denominator -- not just the loan principal: it's effective_principal
    (loan slider + any additional training debt, see get_effective_principal)
    plus any personal_contribution the caller adds on top (money put toward
    the degree that wasn't borrowed, e.g. savings/scholarships/family
    contribution). This is deliberately a different figure from the
    principal actually fed into the loan repayment simulation -- you don't
    pay interest on money you never borrowed, but it's still part of what
    you "invested" for ROI purposes.

    Both net positions are adjusted for the selected city's cost of living
    (col_index) -- assuming the HS grad lives in the same city as the major
    track, since that's the only city input this app has -- so Earnings
    Premium/ROI% reflect real purchasing power in that city, not nominal
    national-average dollars. col_index=100.0 (national average) is a
    no-op, preserving the original nominal comparison when no city is
    selected (or "National Average" is). total_investment is never
    COL-adjusted -- you repay a fixed nominal dollar amount regardless of
    where you live, consistent with the loan repayment simulation itself
    not being COL-adjusted either. Only earnings/purchasing power get
    adjusted, never debt.
    """
    major_cumulative_earnings = sum(
        get_annual_salary_for_year(major_name, y) for y in range(years)
    )
    hs_cumulative_earnings = sum(
        HS_GRAD_SALARY * (1 + HS_GRAD_GROWTH_RATE) ** y for y in range(years)
    )

    major_net_position_nominal = major_cumulative_earnings - total_loan_payments_in_window
    hs_net_position_nominal = hs_cumulative_earnings

    major_net_position = adjust_for_cost_of_living(major_net_position_nominal, col_index)
    hs_net_position = adjust_for_cost_of_living(hs_net_position_nominal, col_index)
    earnings_premium = major_net_position - hs_net_position
    roi_pct = (earnings_premium / total_investment * 100) if total_investment > 0 else None

    return {
        "major_cumulative_earnings": major_cumulative_earnings,
        "hs_cumulative_earnings": hs_cumulative_earnings,
        "major_net_position": major_net_position,
        "hs_net_position": hs_net_position,
        "earnings_premium": earnings_premium,
        "roi_pct": roi_pct,
        "major_net_position_nominal": major_net_position_nominal,
        "hs_net_position_nominal": hs_net_position_nominal,
        "earnings_premium_nominal": major_net_position_nominal - hs_net_position_nominal,
    }


def compute_loan_schedule_by_year(coa_per_year: float, personal_contribution_per_year: float,
                                   grants_per_year: float, inflation_rate: float,
                                   years: int = UNDERGRAD_YEARS) -> list:
    """Per-year loan breakdown across `years` of enrollment, growing Cost of
    Attendance year-over-year by inflation_rate while Personal Contribution
    and Grants & Scholarships both stay flat nominal amounts -- Year 1 uses
    coa_per_year as entered/auto-filled; each subsequent year compounds by
    (1 + inflation_rate). The loan gap widens each year since neither
    funding source scales with rising costs, matching how this plays out
    for most families/awards in practice. Returns one dict per year
    (1-indexed): {"year", "coa", "loan_amount"}. compute_total_loan_amount
    below just sums this -- kept separate so the results page can show the
    year-by-year build-up, not only the final total."""
    schedule = []
    for year_index in range(years):
        coa_this_year = coa_per_year * (1 + inflation_rate) ** year_index
        loan_amount = max(coa_this_year - personal_contribution_per_year - grants_per_year, 0)
        schedule.append({"year": year_index + 1, "coa": coa_this_year, "loan_amount": loan_amount})
    return schedule


def compute_total_loan_amount(coa_per_year: float, personal_contribution_per_year: float,
                               grants_per_year: float, inflation_rate: float,
                               years: int = UNDERGRAD_YEARS) -> float:
    """Total loan across `years` of enrollment -- see
    compute_loan_schedule_by_year for the year-by-year math this sums.
    Grants & Scholarships reduces the loan the same way Personal
    Contribution does, but -- unlike Personal Contribution -- is never
    added to total_investment (the ROI% denominator) in
    compute_scenario_results, since it's free third-party money, not
    something the student/family gave up."""
    schedule = compute_loan_schedule_by_year(coa_per_year, personal_contribution_per_year,
                                              grants_per_year, inflation_rate, years)
    return sum(row["loan_amount"] for row in schedule)


def compute_scenario_results(major_name: str, loan_amount: float,
                              interest_rate: float, repayment_strategy: str,
                              personal_contribution: float = 0.0,
                              col_index: float = 100.0) -> dict:
    """Run the full loan-payoff + ROI pipeline for one scenario. Shared by
    the single-scenario view and Compare Mode (and the survey's context
    capture) so every caller runs the exact same calculation code -- no
    duplicated orchestration to drift out of sync.

    personal_contribution (savings/scholarships/family money that wasn't
    borrowed) only affects the ROI% denominator (total_investment below) --
    it's never added to the loan principal fed into the repayment
    simulation, since you don't pay interest on money you never borrowed.
    Defaults to 0.0 so every existing call site is unaffected until it
    explicitly opts in.

    col_index (the selected city's cost-of-living index, default 100.0 =
    national average = no-op) is passed straight through to calculate_roi,
    which adjusts both sides of the ROI comparison -- see that function's
    docstring.
    """
    effective_principal = get_effective_principal(major_name, loan_amount)
    total_investment = effective_principal + personal_contribution
    if repayment_strategy == "Standard 10-Year":
        repayment_result = calculate_standard_repayment(effective_principal, interest_rate)
        strategy_label = "Standard 10-Year"
    else:
        repayment_result = calculate_idr_repayment(effective_principal, interest_rate, major_name)
        strategy_label = "Income-Driven Repayment"
    roi_result = calculate_roi(major_name, repayment_result["total_paid_in_roi_window"],
                                total_investment, col_index=col_index)
    return {
        "major": major_name,
        "strategy_label": strategy_label,
        "effective_principal": effective_principal,
        "personal_contribution": personal_contribution,
        "total_investment": total_investment,
        "repayment_result": repayment_result,
        "roi_result": roi_result,
    }


# ---- 2h. Taxes & Take-Home Pay --------------------------------------------

def _apply_marginal_brackets(taxable_income: float, brackets: list) -> float:
    """Shared by federal and state tax: brackets = [(upper_bound, rate), ...]."""
    tax, lower = 0.0, 0.0
    for upper, rate in brackets:
        if taxable_income <= lower:
            break
        tax += (min(taxable_income, upper) - lower) * rate
        lower = upper
    return tax


def calculate_federal_tax(gross_annual_income: float) -> float:
    taxable = max(0.0, gross_annual_income - STANDARD_DEDUCTION_2024_SINGLE)
    return _apply_marginal_brackets(taxable, FEDERAL_TAX_BRACKETS_2024_SINGLE)


def calculate_fica_tax(gross_annual_income: float) -> float:
    social_security = min(gross_annual_income, SOCIAL_SECURITY_WAGE_BASE_2024) * SOCIAL_SECURITY_RATE
    medicare = gross_annual_income * MEDICARE_RATE
    return social_security + medicare


def calculate_state_tax(gross_annual_income: float, state_key, local_tax_rate: float = 0.0) -> float:
    """state_key of None ("National Average") means no state is modeled -> $0,
    surfaced by the caller as "N/A" rather than a literal zero-tax claim."""
    if state_key is None:
        return 0.0
    state = STATE_TAX_BRACKETS[state_key]
    taxable = max(0.0, gross_annual_income - state["standard_deduction"])
    return _apply_marginal_brackets(taxable, state["brackets"]) + gross_annual_income * local_tax_rate


def calculate_take_home_pay(gross_annual_income: float, state_key, local_tax_rate: float = 0.0) -> dict:
    federal = calculate_federal_tax(gross_annual_income)
    state = calculate_state_tax(gross_annual_income, state_key, local_tax_rate)
    fica = calculate_fica_tax(gross_annual_income)
    net = gross_annual_income - federal - state - fica
    return {
        "gross": gross_annual_income,
        "federal_tax": federal,
        "state_tax": state,
        "fica_tax": fica,
        "net_take_home": net,
        "state_modeled": state_key is not None,
        "effective_tax_rate": (federal + state + fica) / gross_annual_income if gross_annual_income else 0.0,
    }


def get_monthly_payment_for_stage(repayment_result: dict, strategy: str, target_month: int) -> float:
    """The loan payment at a given career-stage snapshot. If the loan is
    already paid off or forgiven by target_month, the payment is $0 for
    either strategy -- Standard's constant monthly_payment is only valid
    while the loan is still active."""
    if target_month >= repayment_result["payoff_years"] * 12:
        return 0.0
    if strategy == "Standard 10-Year":
        return repayment_result["monthly_payment"]
    schedule = repayment_result["schedule"]
    row = schedule[schedule["month"] == target_month]
    return row.iloc[0]["payment"] if not row.empty else 0.0


# ---- 2i. Cost-of-Living Adjustment ----------------------------------------

def adjust_for_cost_of_living(amount: float, col_index: float) -> float:
    """Normalize a nominal dollar amount to national-average purchasing power."""
    return amount / (col_index / 100.0)


# ---- 2j. Chart Builders ----------------------------------------------------

def build_balance_chart(schedule_df: pd.DataFrame, strategy_label: str):
    fig = px.line(
        schedule_df, x="year", y="balance",
        title=f"Loan Balance Over Time — {strategy_label}",
        labels={"year": "Years", "balance": "Remaining Balance ($)"},
    )
    fig.update_traces(line=dict(width=3))
    fig.update_layout(yaxis_tickprefix="$", hovermode="x unified")
    return fig


def build_roi_bar_chart(hs_net_position: float, major_net_position: float, major_name: str):
    comparison_df = pd.DataFrame({
        "Group": ["High School Graduate", major_name],
        "10-Year Net Position ($)": [hs_net_position, major_net_position],
    })
    fig = px.bar(
        comparison_df, x="Group", y="10-Year Net Position ($)", color="Group",
        title=f"10-Year Net Financial Position (COL-Adjusted): {major_name} vs. High School Baseline",
        text_auto=".2s",
    )
    fig.update_layout(yaxis_tickprefix="$", showlegend=False, title_x=0.5)
    return fig


def build_comparison_balance_chart(schedule_a: pd.DataFrame, label_a: str,
                                    schedule_b: pd.DataFrame, label_b: str):
    """Overlay both scenarios' loan balance curves on one chart for direct
    side-by-side comparison, instead of two separate charts."""
    combined = pd.concat([
        schedule_a.assign(Scenario=label_a),
        schedule_b.assign(Scenario=label_b),
    ])
    fig = px.line(
        combined, x="year", y="balance", color="Scenario",
        title="Loan Balance Over Time: Scenario A vs. Scenario B",
        labels={"year": "Years", "balance": "Remaining Balance ($)"},
    )
    fig.update_layout(yaxis_tickprefix="$", hovermode="x unified")
    return fig


def build_scenario_comparison_roi_chart(hs_net_position: float,
                                         net_a: float, label_a: str,
                                         net_b: float, label_b: str):
    """3-bar version of build_roi_bar_chart: HS-grad baseline plus both
    scenarios, for comparing net financial position directly."""
    comparison_df = pd.DataFrame({
        "Group": ["High School Graduate", label_a, label_b],
        "10-Year Net Position ($)": [hs_net_position, net_a, net_b],
    })
    fig = px.bar(
        comparison_df, x="Group", y="10-Year Net Position ($)", color="Group",
        title="10-Year Net Financial Position (COL-Adjusted): Scenario Comparison",
        text_auto=".2s",
    )
    fig.update_layout(yaxis_tickprefix="$", showlegend=False)
    return fig


def build_takehome_pie_chart(take_home: dict):
    """Pie chart of how gross salary splits between take-home pay and each
    tax category -- "slices of a whole" is a more intuitive framing for a
    high-school audience than a waterfall's running subtraction."""
    fig = px.pie(
        names=["Take-Home Pay", "Federal Tax", "State + Local Tax", "FICA (Social Security/Medicare)"],
        values=[take_home["net_take_home"], take_home["federal_tax"],
                 take_home["state_tax"], take_home["fica_tax"]],
        title="Where Your Salary Actually Goes",
    )
    fig.update_traces(textinfo="percent+label")
    return fig


def build_survey_pie_chart(survey_df: pd.DataFrame):
    counts = survey_df["perception_change"].value_counts().reset_index()
    counts.columns = ["Response", "Count"]
    fig = px.pie(counts, names="Response", values="Count", title="Did This Tool Change Student Perceptions?")
    return fig


def build_perception_by_major_chart(survey_df: pd.DataFrame):
    """Grouped bar chart: perception_change counts broken down by
    scenario_a_major, for spotting whether some majors are more "elastic"
    (more likely to report a changed perception) than others. Rows saved
    before this field existed have a null scenario_a_major and are excluded
    here (they still count in the overall pie chart/metrics above)."""
    plottable = survey_df.dropna(subset=["scenario_a_major", "perception_change"])
    cross_tab = (
        plottable.groupby(["scenario_a_major", "perception_change"])
        .size()
        .reset_index(name="Count")
    )
    fig = px.bar(
        cross_tab, x="scenario_a_major", y="Count", color="perception_change",
        title="Impact of the Tool by Selected Major",
        labels={"scenario_a_major": "Selected Major", "perception_change": "Response", "Count": "Responses"},
        barmode="group",
    )
    return fig


# ============================================================
# 3. PAGE CONFIG & SESSION STATE
# ============================================================

st.set_page_config(page_title="Student Loan Payoff & Major ROI Calculator", page_icon="🎓", layout="wide")

# Log exactly one "pageview" per browser session. This check runs before any
# widgets are drawn, so later reruns triggered by moving a slider or opening
# an expander see "pageview_logged" already set and skip logging again.
if "pageview_logged" not in st.session_state:
    log_usage_event("pageview")
    st.session_state.pageview_logged = True

if "has_calculated" not in st.session_state:
    st.session_state.has_calculated = False

if "has_compared" not in st.session_state:
    st.session_state.has_compared = False

if "survey_submitted" not in st.session_state:
    st.session_state.survey_submitted = False

# Read here (before the checkbox widget below it is even drawn) so the
# button's label/click-handling -- which sits ABOVE the "Compare Two
# Scenarios" checkbox on the page -- always reflects the checkbox's
# current value. Streamlit persists key="compare_mode" in session_state
# across reruns, so toggling the checkbox (further down) updates this
# same slot and the button picks it up correctly on the next rerun.
if "compare_mode" not in st.session_state:
    st.session_state.compare_mode = False

# Admin Analytics View starts hidden -- Ctrl+Shift+A reveals the checkbox
# that controls it (see the hidden trigger button + injected JS near the
# bottom of the sidebar). Stays revealed for the rest of the session once
# triggered, matching the has_calculated/has_compared one-way-flag pattern.
if "admin_revealed" not in st.session_state:
    st.session_state.admin_revealed = False


# ============================================================
# 4. SIDEBAR — USER INPUTS
# ============================================================

st.sidebar.header("🎓 Your Profile")

# Global styling for every number_input in the sidebar (Scenario A and B
# alike): hide the +/- stepper buttons, and show a $ or % unit prefix on
# the left based on which one appears in the widget's own label -- every
# number_input in this app has exactly one or the other (e.g. "Cost of
# Attendance (per year, $)", "Average Loan Interest Rate (%)"), and
# Streamlit mirrors that label text onto the input's aria-label, so a
# plain CSS attribute selector is enough without touching each widget.
st.markdown(
    """
    <style>
    div[data-testid="stNumberInputContainer"] div:has(> button[data-testid="stNumberInputStepUp"]) {
        display: none;
    }
    div[data-baseweb="input"] {
        position: relative;
    }
    div[data-baseweb="input"]:has(input[aria-label*="$"])::before,
    div[data-baseweb="input"]:has(input[aria-label*="%"])::before {
        position: absolute;
        left: 10px;
        top: 50%;
        transform: translateY(-50%);
        color: #808495;
        z-index: 2;
        pointer-events: none;
    }
    div[data-baseweb="input"]:has(input[aria-label*="$"])::before {
        content: "$";
    }
    div[data-baseweb="input"]:has(input[aria-label*="%"])::before {
        content: "%";
    }
    div[data-baseweb="input"]:has(input[aria-label*="$"]) input,
    div[data-baseweb="input"]:has(input[aria-label*="%"]) input {
        padding-left: 22px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Pulls real tuition & median debt for the school below via api.data.gov.
# No sidebar control for this -- the public rate-limited DEMO_KEY is enough
# for this app's usage volume, so there's nothing for a user to configure.
scorecard_api_key = "DEMO_KEY"

st.sidebar.subheader("💼 Career")

# Which BLS OEWS geographic release backs the career dropdown below --
# National (every state combined into one nationwide figure per occupation)
# or California (that state's own wages, which run higher for many careers,
# e.g. tech and healthcare). Affects every curated-major lookup too, since
# MAJOR_DATA is rebuilt from this choice on every rerun -- picking a source
# here is a data-source preference for the whole session, not per-scenario.
career_data_source = st.sidebar.radio(
    "Career Salary Data", ["National", "California"],
    help="National: nationwide BLS OEWS wage estimates (cleaned_careers.csv). "
         "California: that state's own BLS OEWS wage estimates "
         "(cleaned_careers_ca.csv), generated via `data_pipeline.py ... --state CA`.",
)
careers_csv_path = CAREERS_CSV_PATH_CA if career_data_source == "California" else CAREERS_CSV_PATH_NATIONAL
MAJOR_DATA = {**load_bls_careers(careers_csv_path), **CURATED_MAJOR_DATA}

major = st.sidebar.selectbox(
    "Target Major", sorted(MAJOR_DATA.keys()),
    help="Pick the career you're evaluating -- this determines the salary "
         "numbers used everywhere else in the app. There are hundreds of "
         "options, so instead of scrolling, click the box and type part of "
         "your major or career to jump straight to it.",
)

city = st.sidebar.selectbox(
    "City / Metro Area", list(CITY_DATA.keys()),
    help="Where you plan to live and work after graduating. Adjusts your "
         "take-home pay and the 10-year comparison for how expensive that "
         "area is to live in.",
)
# Computed here (not just where it's first used, further down) so it's
# available for every compute_scenario_results() call in section 5 --
# including Compare Mode's, which run before the Real-World Take-Home
# section that used to be the only place this was computed.
city_info = CITY_DATA[city]

# School next: entering it immediately shows Cost of Attendance below, and
# (if it matches the local dataset) auto-fills the per-year COA field --
# everything in the Financing section below builds on that number.
school_name_a = st.sidebar.text_input(
    "Target Undergraduate School", placeholder="e.g. University of Michigan",
    key="school_name_a", on_change=lambda: _autofill_coa("school_name_a", "in_state_a", "coa_per_year_a"),
    help="Type a school name to auto-fill Cost of Attendance below from "
         "real government data, if we have it on file. If your school "
         "isn't found, just enter Cost of Attendance yourself.",
)
in_state_a = st.sidebar.checkbox(
    "In-State Student?", key="in_state_a",
    on_change=lambda: _autofill_coa("school_name_a", "in_state_a", "coa_per_year_a"),
    help="Check this if you'd pay in-state tuition at the school above. "
         "Changes the auto-filled Cost of Attendance and how fast tuition "
         "is estimated to grow each year.",
)
coa_match_a = find_school_coa(school_name_a, load_coa_dataset()) if school_name_a else None
coa_caption_a = get_coa_confirmation_caption(school_name_a, coa_match_a, in_state_a)
if coa_caption_a:
    st.sidebar.caption(coa_caption_a)

# Which point in this major's career the Real-World Take-Home section
# (5d) snapshots -- has no functional dependency on School/In-State above
# or Financing/City below, so its position here is purely about profile
# layout, not calculation order.
career_stage_label = st.sidebar.radio(
    "Career Stage Snapshot", list(CAREER_STAGE_OPTIONS.keys()),
    help="Preview your income right after graduating (Year 1) or 10 years "
         "into this career, in the Real-World Take-Home section below.",
)
career_stage_key = CAREER_STAGE_OPTIONS[career_stage_label]

st.sidebar.subheader("💰 Financing")
coa_per_year_a = st.sidebar.number_input(
    "Cost of Attendance (per year, $)", min_value=0, max_value=100000, value=7500, step=500,
    key="coa_per_year_a",
    help="The full sticker price for one year at this school -- tuition, "
         "fees, room & board, books, everything -- before subtracting "
         "scholarships or what you pay yourself. Auto-fills if we found "
         "your school above.",
)
personal_contribution_per_year_a = st.sidebar.number_input(
    "Personal Contribution (per year, $)", min_value=0, max_value=100000, value=0, step=500,
    key="personal_contribution_per_year_a",
    help="Also called the Student Aid Index (SAI) -- the amount your family "
         "is expected to contribute. Savings or family money toward this "
         "year's cost that you did NOT borrow. The loan amount below is "
         "Cost of Attendance minus this and Grants & Scholarships -- "
         "counted in the ROI% denominator, but not added to the loan "
         "you're actually repaying (no interest accrues on it).",
)
grants_per_year_a = st.sidebar.number_input(
    "Grants & Scholarships (per year, $)", min_value=0, max_value=100000, value=0, step=500,
    key="grants_per_year_a",
    help="Grant or scholarship aid that reduces what you need to borrow. "
         "Unlike Personal Contribution, this is NOT counted as part of your "
         "own investment for ROI purposes -- it was never your money.",
)
# Loan amount is derived, not entered: Cost of Attendance minus whatever
# isn't borrowed, per year, growing COA by an estimated inflation rate each
# year while Personal Contribution and Grants & Scholarships both stay flat
# -- then summed to the total every downstream calculation
# (effective_principal, ROI, take-home) operates on.
control_type_a = coa_match_a["control_type"] if coa_match_a is not None else None
inflation_rate_a = estimate_coa_inflation_rate(school_name_a, scorecard_api_key, control_type_a)
loan_amount = compute_total_loan_amount(coa_per_year_a, personal_contribution_per_year_a,
                                         grants_per_year_a, inflation_rate_a)
personal_contribution = personal_contribution_per_year_a * UNDERGRAD_YEARS
st.sidebar.caption((
    f"Year 1: {fmt_money(coa_per_year_a)} COA − {fmt_money(personal_contribution_per_year_a)} personal "
    f"− {fmt_money(grants_per_year_a)} grants → est. {fmt_pct(inflation_rate_a * 100)} COA inflation/yr "
    f"→ over {UNDERGRAD_YEARS} years: **{fmt_money(loan_amount)}** loan, **{fmt_money(personal_contribution)}** personal"
).replace("$", r"\$"))
interest_rate = st.sidebar.number_input(
    "Average Loan Interest Rate (%)", min_value=0.0, max_value=20.0, value=5.5, step=0.1,
    help="The interest rate on your student loan. 5.50% is a reasonable "
         "placeholder for recent federal undergraduate loan rates -- check "
         "your school's financial aid offer for your real rate.",
)
repayment_strategy = st.sidebar.selectbox(
    "Repayment Strategy",
    ["Standard 10-Year", "Income-Driven Repayment (IDR)"],
    help="Standard 10-Year: a fixed payment every month for 10 years. "
         "Income-Driven Repayment (IDR): your payment is based on your "
         "income instead, and whatever's left is forgiven after 20 years.",
)

st.sidebar.divider()

# Admin Analytics View is hidden by default -- a real (but invisible)
# Streamlit button is the only way to actually flip admin_revealed, since
# that's what makes the click go through Streamlit's normal widget/rerun
# machinery instead of trying to fake session state from raw JS. The
# injected script below just finds this button by its exact text and
# calls .click() on it when Ctrl+Shift+A is pressed; the CSS block hides
# its wrapping container so it's never visible or in the way.
with st.sidebar.container(key="admin_reveal_trigger_wrap"):
    admin_reveal_clicked = st.button("Reveal Admin Panel", key="admin_reveal_trigger")
if admin_reveal_clicked:
    st.session_state.admin_revealed = True

st.markdown(
    "<style>div.st-key-admin_reveal_trigger_wrap { display: none !important; }</style>",
    unsafe_allow_html=True,
)
components.html(
    """
    <script>
    (function() {
        function findRevealButton() {
            const doc = window.parent.document;
            const buttons = doc.querySelectorAll("button");
            for (const b of buttons) {
                if (b.textContent.trim() === "Reveal Admin Panel") return b;
            }
            return null;
        }
        window.parent.document.addEventListener("keydown", function (e) {
            if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === "a") {
                e.preventDefault();
                const btn = findRevealButton();
                if (btn) btn.click();
            }
        });
    })();
    </script>
    """,
    height=0,
)

admin_enabled = st.sidebar.checkbox("🔐 Admin Analytics View") if st.session_state.admin_revealed else False

# Read from session_state (not a bare compare_mode variable, which doesn't
# exist yet at this point in the script -- see the "Compare Two Scenarios"
# checkbox below, now positioned after this button per request) so the
# label/click-handling still reflect the checkbox's current value.
button_label = "⚖️ Compare Scenarios" if st.session_state.compare_mode else "🚀 Calculate My Payoff Plan & ROI"
action_clicked = st.sidebar.button(button_label, use_container_width=True)
if action_clicked:
    if st.session_state.compare_mode:
        log_usage_event("comparison")
        st.session_state.has_compared = True
    else:
        log_usage_event("calculation")
        st.session_state.has_calculated = True

# Compare Mode adds a second scenario (Scenario B) rather than hiding the
# widgets above -- those always represent Scenario A, in both modes. This
# means toggling Compare Mode on/off never loses a tuned value (there's
# only ever one copy of Scenario A's inputs) and the survey section below
# never needs to guess which scenario's context to save. Positioned below
# the Calculate/Compare button (unlike every other input, which sits above
# it) per request -- key="compare_mode" keeps this checkbox's value in
# sync with the session_state read the button above needs before this
# checkbox is even drawn.
compare_mode = st.sidebar.checkbox(
    "🔀 Compare Two Scenarios", key="compare_mode",
    help="Turn this on to compare two different majors, schools, or loan "
         "setups side by side instead of looking at just one.",
)

if compare_mode:
    with st.sidebar.expander("⚖️ Scenario B (for comparison)", expanded=True):
        major_b = st.selectbox(
            "Target Major", sorted(MAJOR_DATA.keys()), key="major_b",
            help="Pick the career you're evaluating -- this determines the "
                 "salary numbers used everywhere else in the app. There are "
                 "hundreds of options, so instead of scrolling, click the "
                 "box and type part of your major or career to jump "
                 "straight to it.",
        )

        school_name_b = st.text_input(
            "Target Undergraduate School", placeholder="e.g. Ohio State University",
            key="school_name_b", on_change=lambda: _autofill_coa("school_name_b", "in_state_b", "coa_per_year_b"),
            help="Type a school name to auto-fill Cost of Attendance below "
                 "from real government data, if we have it on file. If "
                 "your school isn't found, just enter Cost of Attendance "
                 "yourself.",
        )
        in_state_b = st.checkbox(
            "In-State Student?", key="in_state_b",
            on_change=lambda: _autofill_coa("school_name_b", "in_state_b", "coa_per_year_b"),
            help="Check this if you'd pay in-state tuition at the school "
                 "above. Changes the auto-filled Cost of Attendance and how "
                 "fast tuition is estimated to grow each year.",
        )
        coa_match_b = find_school_coa(school_name_b, load_coa_dataset()) if school_name_b else None
        coa_caption_b = get_coa_confirmation_caption(school_name_b, coa_match_b, in_state_b)
        if coa_caption_b:
            st.caption(coa_caption_b)

        st.subheader("💰 Financing")
        coa_per_year_b = st.number_input(
            "Cost of Attendance (per year, $)", min_value=0, max_value=100000, value=7500, step=500,
            key="coa_per_year_b",
            help="The full sticker price for one year at this school -- "
                 "tuition, fees, room & board, books, everything -- before "
                 "subtracting scholarships or what you pay yourself. "
                 "Auto-fills if we found your school above.",
        )
        personal_contribution_per_year_b = st.number_input(
            "Personal Contribution (per year, $)", min_value=0, max_value=100000, value=0, step=500,
            key="personal_contribution_per_year_b",
            help="Also called the Student Aid Index (SAI) -- the amount your "
                 "family is expected to contribute. Savings or family money "
                 "toward this year's cost that wasn't borrowed. The loan "
                 "amount below is Cost of Attendance minus this and Grants "
                 "& Scholarships.",
        )
        grants_per_year_b = st.number_input(
            "Grants & Scholarships (per year, $)", min_value=0, max_value=100000, value=0, step=500,
            key="grants_per_year_b",
            help="Grant or scholarship aid that reduces what you need to "
                 "borrow. Not counted as part of your own investment for ROI "
                 "purposes -- it was never your money.",
        )
        control_type_b = coa_match_b["control_type"] if coa_match_b is not None else None
        inflation_rate_b = estimate_coa_inflation_rate(school_name_b, scorecard_api_key, control_type_b)
        loan_amount_b = compute_total_loan_amount(coa_per_year_b, personal_contribution_per_year_b,
                                                   grants_per_year_b, inflation_rate_b)
        personal_contribution_b = personal_contribution_per_year_b * UNDERGRAD_YEARS
        st.caption((
            f"Year 1: {fmt_money(coa_per_year_b)} COA − {fmt_money(personal_contribution_per_year_b)} personal "
            f"− {fmt_money(grants_per_year_b)} grants → est. {fmt_pct(inflation_rate_b * 100)} COA inflation/yr "
            f"→ over {UNDERGRAD_YEARS} years: **{fmt_money(loan_amount_b)}** loan, **{fmt_money(personal_contribution_b)}** personal"
        ).replace("$", r"\$"))
        interest_rate_b = st.number_input(
            "Average Loan Interest Rate (%)", min_value=0.0, max_value=20.0, value=5.5, step=0.1,
            key="interest_rate_b",
            help="The interest rate on your student loan. 5.50% is a "
                 "reasonable placeholder for recent federal undergraduate "
                 "loan rates -- check your school's financial aid offer "
                 "for your real rate.",
        )
        repayment_strategy_b = st.selectbox(
            "Repayment Strategy",
            ["Standard 10-Year", "Income-Driven Repayment (IDR)"],
            key="repayment_strategy_b",
            help="Standard 10-Year: a fixed payment every month for 10 "
                 "years. Income-Driven Repayment (IDR): your payment is "
                 "based on your income instead, and whatever's left is "
                 "forgiven after 20 years.",
        )


# ============================================================
# 5. MAIN PAGE
# ============================================================

st.title("🎓 Student Loan Payoff & Major ROI Calculator")
st.caption(
    "Educational estimate tool — salary and cost figures are illustrative, not financial advice."
)

# ---- 5a. Admin Analytics Dashboard (hidden behind sidebar checkbox) ------

if admin_enabled:
    st.subheader("📊 Admin Analytics Dashboard")

    usage_df = load_table_safe("usage_logs", columns=["timestamp", "action"])
    survey_df = load_table_safe("survey_responses", columns=[
        "timestamp", "perception_change", "feedback_text",
        "scenario_a_school_name", "scenario_a_major", "scenario_a_loan_amount", "scenario_a_interest_rate",
        "scenario_a_repayment_strategy", "scenario_a_starting_salary", "scenario_a_dti_ratio",
        "scenario_a_monthly_payment", "scenario_a_payoff_years", "scenario_a_total_interest",
        "scenario_a_earnings_premium", "scenario_a_roi_pct", "scenario_a_personal_contribution",
        "scenario_a_coa_inflation_rate", "scenario_a_grants_per_year",
        "scenario_b_school_name", "scenario_b_major", "scenario_b_loan_amount", "scenario_b_interest_rate",
        "scenario_b_repayment_strategy", "scenario_b_starting_salary", "scenario_b_dti_ratio",
        "scenario_b_monthly_payment", "scenario_b_payoff_years", "scenario_b_total_interest",
        "scenario_b_earnings_premium", "scenario_b_roi_pct", "scenario_b_personal_contribution", "roi_pct_delta",
        "scenario_b_coa_inflation_rate", "scenario_b_grants_per_year",
    ])

    col1, col2 = st.columns(2)
    col1.metric("Total App Interactions", len(usage_df))
    col2.metric("Total Survey Responses", len(survey_df))

    if not survey_df.empty:
        # Research metrics: skip NaN automatically (rows saved before these
        # fields existed, or saved with Compare Mode off for the B/delta
        # ones, just don't count toward the average), and check there's at
        # least one real value before displaying anything.
        research_col1, research_col2 = st.columns(2)
        if survey_df["scenario_a_loan_amount"].notna().any():
            research_col1.metric("Average Loan Amount Simulated", fmt_money(survey_df["scenario_a_loan_amount"].mean()))
        else:
            research_col1.metric("Average Loan Amount Simulated", "N/A")
        if survey_df["scenario_a_dti_ratio"].notna().any():
            research_col2.metric("Average Debt-to-Income Ratio", f"{survey_df['scenario_a_dti_ratio'].mean():.2f}")
        else:
            research_col2.metric("Average Debt-to-Income Ratio", "N/A")

        chart_col, table_col = st.columns(2)
        chart_col.plotly_chart(build_survey_pie_chart(survey_df), use_container_width=True)
        table_col.dataframe(
            survey_df[[
                "timestamp", "scenario_a_school_name",
                "scenario_a_major", "scenario_a_loan_amount", "scenario_a_personal_contribution",
                "scenario_a_interest_rate", "scenario_a_repayment_strategy", "scenario_a_roi_pct",
                "scenario_b_school_name", "scenario_b_major", "scenario_b_loan_amount",
                "scenario_b_personal_contribution", "scenario_b_roi_pct",
                "roi_pct_delta", "feedback_text",
            ]],
            use_container_width=True, height=380,
        )

        if survey_df["scenario_a_major"].notna().any():
            st.plotly_chart(build_perception_by_major_chart(survey_df), use_container_width=True)
    else:
        st.info("No survey responses recorded yet.")

    st.divider()

# ---- 5b. School Data Lookup (local COA dataset + College Scorecard API) --
# Cost of Attendance (in/out-of-state) comes from the local dataset built by
# clean_college_scorecard.py, run against the real College Scorecard
# institution file (data/college_coa_clean.csv, 5,000+ real schools).
# Median debt is still fetched live, which works for any school regardless of local
# dataset coverage. A matched school's COA also auto-fills that scenario's
# per-year Cost of Attendance field (in-state or out-of-state, per the
# In-State Student? checkbox) -- see _autofill_coa in section 2c.

def render_school_lookup(container, school_name: str, label: str):
    """Render one scenario's school lookup (COA match + median debt) into a
    layout container. Used once for the single-scenario view and twice
    (Scenario A / B) in Compare Mode, so the two can't drift apart from
    being hand-copied -- same reasoning as render_scenario_panel."""
    with container:
        if not school_name:
            return
        coa_match = find_school_coa(school_name, load_coa_dataset())
        debt_data = fetch_median_debt(school_name, scorecard_api_key)

        if coa_match is not None:
            coa_text = (
                f"**Scenario {label}: {coa_match['INSTNM']}** ({coa_match['control_type']}) — "
                f"In-state Cost of Attendance: {fmt_money(coa_match['in_state_coa'])} | "
                f"Out-of-state Cost of Attendance: {fmt_money(coa_match['out_of_state_coa'])}"
            ).replace("$", r"\$")
            st.info(coa_text)
        else:
            st.caption(
                f"Scenario {label}: no Cost of Attendance match in the local dataset yet "
                "(currently only a small sample of schools -- see data/college_coa_clean.csv)."
            )

        if debt_data and debt_data.get("median_debt"):
            # Escape "$" -- st.caption renders markdown, and a pair of literal
            # "$" gets parsed as inline LaTeX math, mangling the text between them.
            st.caption(
                f"Median completer debt for {debt_data['name']}: {fmt_money(debt_data['median_debt'])}"
                .replace("$", r"\$")
            )


if compare_mode:
    lookup_col_a, lookup_col_b = st.columns(2)
    render_school_lookup(lookup_col_a, school_name_a, "A")
    render_school_lookup(lookup_col_b, school_name_b, "B")
else:
    render_school_lookup(st.container(), school_name_a, "A")

# ---- 5c. Calculator Results ----------------------------------------------

def get_loan_principal_caption(scenario: dict) -> str:
    """Explains what actually feeds the loan repayment simulation, when it
    differs from the raw loan slider (professional-school debt on top of
    it). Returns None if there's nothing extra to explain."""
    additional_debt = MAJOR_DATA[scenario["major"]].get("additional_training_debt", 0)
    if additional_debt <= 0:
        return None
    return (
        f"Effective loan principal including {fmt_money(additional_debt)} "
        f"est. average professional-school debt: **{fmt_money(scenario['effective_principal'])}**"
    ).replace("$", r"\$")


def get_total_investment_caption(scenario: dict) -> str:
    """Explains the ROI% denominator, when it differs from the effective
    loan principal alone (personal_contribution is nonzero). Returns None
    if there's nothing extra to explain."""
    personal_contribution = scenario["personal_contribution"]
    if personal_contribution <= 0:
        return None
    return (
        f"ROI is measured against a total investment of **{fmt_money(scenario['total_investment'])}** "
        f"({fmt_money(scenario['effective_principal'])} effective loan principal + "
        f"{fmt_money(personal_contribution)} personal contribution), not the loan alone"
    ).replace("$", r"\$")


def get_investment_captions(scenario: dict) -> list:
    """Both captions above together, for callers (Compare Mode) that show
    them as one adjacent pair rather than split across sections."""
    return [c for c in (get_loan_principal_caption(scenario), get_total_investment_caption(scenario)) if c]


def render_scenario_panel(column, scenario: dict, label: str):
    """Render one scenario's metric cards (+ effective-principal caption and
    forgiveness warning) into a layout column. Used twice by Compare Mode
    (Scenario A / Scenario B) so their markup can't drift apart from being
    hand-copied -- this is the same card layout section 5c uses for the
    single-scenario view, just parameterized and column-scoped."""
    with column:
        st.markdown(f"**Scenario {label}: {scenario['major']} — {scenario['strategy_label']}**")

        for caption in get_investment_captions(scenario):
            st.caption(caption)

        repayment_result = scenario["repayment_result"]
        roi_result = scenario["roi_result"]
        st.metric(
            "Monthly Payment",
            fmt_money(repayment_result["monthly_payment"]) if "monthly_payment" in repayment_result else "Varies (IDR)",
        )
        st.metric("Payoff Timeline", f"{repayment_result['payoff_years']:.1f} yrs")
        st.metric("Total Interest Paid", fmt_money(repayment_result["total_interest"]))
        st.metric(
            "10-Year Earnings Premium (COL-Adjusted)",
            fmt_money(roi_result["earnings_premium"]),
            delta=fmt_pct(roi_result["roi_pct"]) + " ROI" if roi_result["roi_pct"] is not None else None,
        )
        if repayment_result["forgiven_amount"] > 0:
            st.warning(
                f"{fmt_money(repayment_result['forgiven_amount'])} forgiven after {IDR_MAX_TERM_YEARS} years."
            )


if compare_mode:
    st.subheader("⚖️ Scenario Comparison")
    if st.session_state.has_compared:
        scenario_a = compute_scenario_results(major, loan_amount, interest_rate, repayment_strategy,
                                               personal_contribution, city_info["col_index"])
        scenario_b = compute_scenario_results(major_b, loan_amount_b, interest_rate_b, repayment_strategy_b,
                                               personal_contribution_b, city_info["col_index"])

        col_a, col_b = st.columns(2)
        render_scenario_panel(col_a, scenario_a, "A")
        render_scenario_panel(col_b, scenario_b, "B")

        st.plotly_chart(
            build_comparison_balance_chart(
                scenario_a["repayment_result"]["schedule"], f"A: {scenario_a['major']}",
                scenario_b["repayment_result"]["schedule"], f"B: {scenario_b['major']}",
            ),
            use_container_width=True,
        )
        st.plotly_chart(
            build_scenario_comparison_roi_chart(
                scenario_a["roi_result"]["hs_net_position"],
                scenario_a["roi_result"]["major_net_position"], f"A: {scenario_a['major']}",
                scenario_b["roi_result"]["major_net_position"], f"B: {scenario_b['major']}",
            ),
            use_container_width=True,
        )
    else:
        st.info("Configure Scenario B in the sidebar, then click **Compare Scenarios**.")
elif st.session_state.has_calculated:
    scenario = compute_scenario_results(major, loan_amount, interest_rate, repayment_strategy,
                                         personal_contribution, city_info["col_index"])
    effective_principal = scenario["effective_principal"]
    repayment_result = scenario["repayment_result"]
    strategy_label = scenario["strategy_label"]
    roi_result = scenario["roi_result"]

    # ---- 5c-1. Loan Information --------------------------------------------

    st.subheader(f"💳 Loan Information — {strategy_label}")

    loan_caption = get_loan_principal_caption(scenario)
    if loan_caption:
        st.caption(loan_caption)

    loan_schedule_a = compute_loan_schedule_by_year(
        coa_per_year_a, personal_contribution_per_year_a, grants_per_year_a, inflation_rate_a
    )
    st.caption(
        "Here's how your loan builds up year by year -- Cost of Attendance "
        "grows by the estimated inflation rate each year, while Personal "
        "Contribution and Grants & Scholarships stay the same."
    )
    st.dataframe(
        pd.DataFrame([
            {"Year": row["year"], "Cost of Attendance": fmt_money(row["coa"]),
             "Loan Amount This Year": fmt_money(row["loan_amount"])}
            for row in loan_schedule_a
        ]),
        hide_index=True, use_container_width=True,
    )
    st.metric(f"Total Loan Amount (all {UNDERGRAD_YEARS} years)", fmt_money(loan_amount))

    loan_metric_cols = st.columns(3)
    loan_metric_cols[0].metric(
        "Monthly Payment",
        fmt_money(repayment_result["monthly_payment"]) if "monthly_payment" in repayment_result else "Varies (IDR)",
    )
    loan_metric_cols[1].metric("Payoff Timeline", f"{repayment_result['payoff_years']:.1f} yrs")
    loan_metric_cols[2].metric("Total Interest Paid", fmt_money(repayment_result["total_interest"]))

    if repayment_result["forgiven_amount"] > 0:
        st.warning(
            f"Under IDR, {fmt_money(repayment_result['forgiven_amount'])} of principal remains "
            f"unpaid after {IDR_MAX_TERM_YEARS} years and is forgiven."
        )

    st.plotly_chart(build_balance_chart(repayment_result["schedule"], strategy_label), use_container_width=True)

    # ---- 5d. Real-World Take-Home Snapshot --------------------------------

    st.subheader(f"🏙️ Real-World Take-Home — {career_stage_label} in {city}")

    gross = get_annual_salary_for_year(major, career_stage_key)
    take_home = calculate_take_home_pay(gross, city_info["state_key"], city_info["local_tax_rate"])
    target_month = (career_stage_key + 1) * 12
    monthly_payment = get_monthly_payment_for_stage(repayment_result, strategy_label, target_month)
    disposable_nominal = take_home["net_take_home"] / 12 - monthly_payment
    disposable_col_adjusted = adjust_for_cost_of_living(disposable_nominal, city_info["col_index"])

    if gross == 0:
        st.info(f"At this career stage, {major} has $0 gross income (still in training) — see Methodology for why.")

    take_home_cols = st.columns(4)
    take_home_cols[0].metric("Gross Salary", fmt_money(gross))
    take_home_cols[1].metric(
        "Take-Home Pay (annual, after tax)",
        fmt_money(take_home["net_take_home"]),
        delta=fmt_pct(take_home["effective_tax_rate"] * 100) + " effective tax rate" if gross > 0 else None,
    )
    take_home_cols[2].metric("Monthly Disposable Income", fmt_money(disposable_nominal))
    take_home_cols[3].metric(
        "COL-Adjusted Disposable Income", fmt_money(disposable_col_adjusted),
        help="Normalized to national-average purchasing power, so cities are comparable",
    )

    if not take_home["state_modeled"]:
        st.caption("State tax: N/A (National Average city has no specific state to model)")
    if disposable_nominal < 0:
        st.warning("At this salary, city, and loan combination, disposable income is negative.")

    if gross > 0:
        st.plotly_chart(build_takehome_pie_chart(take_home), use_container_width=True)

    # ---- 5e. 10-Year Financial Position -------------------------------------

    st.subheader("📊 10-Year Financial Position")
    st.caption((
        f"This compares two paths over your first 10 years after high school: going into "
        f"**{major}** (paying off the loan above along the way) vs. skipping college and "
        f"working right away as a high school graduate who takes on **no loan of their own**. "
        f"Both numbers are adjusted for the cost of living in **{city}** -- that's what "
        f"**\"COL-Adjusted\"** means -- so it's a fair, apples-to-apples comparison of real "
        f"spending power, not just which raw number is bigger. **Earnings Premium** is simply "
        f"the difference between the two: how much more (or less) you'd have after 10 years "
        f"by choosing {major} instead of skipping college."
    ).replace("$", r"\$"))

    investment_caption = get_total_investment_caption(scenario)
    if investment_caption:
        st.caption(investment_caption)

    position_cols = st.columns(3)
    position_cols[0].metric(
        "High School Grad — 10-Yr Net Position (No Loan)", fmt_money(roi_result["hs_net_position"]),
    )
    position_cols[1].metric(f"{major} — 10-Yr Net Position", fmt_money(roi_result["major_net_position"]))
    position_cols[2].metric(
        "Earnings Premium (COL-Adjusted)",
        fmt_money(roi_result["earnings_premium"]),
        delta=fmt_pct(roi_result["roi_pct"]) + " ROI" if roi_result["roi_pct"] is not None else None,
        help="Earnings Premium = your 10-Yr Net Position minus the High School Grad's. "
             "COL-Adjusted = adjusted for cost of living, so cities are compared fairly.",
    )

    st.plotly_chart(build_roi_bar_chart(roi_result["hs_net_position"], roi_result["major_net_position"], major), use_container_width=True)
else:
    st.info("Set your profile in the sidebar, then click **Calculate My Payoff Plan & ROI** to see results.")

st.divider()

# ---- 5e. Anonymous Impact Survey ------------------------------------------

if not st.session_state.survey_submitted:
    with st.form("survey_form", clear_on_submit=True):
        st.subheader("📋 Help Us Measure Impact")
        perception_change = st.radio(
            "Did this tool change how you view your target major or university choice?",
            ["Yes - significantly", "Yes - slightly", "No - it confirmed my choice", "No - no impact"],
        )
        feedback_text = st.text_area("How did this data influence your thinking? (optional)")
        submitted = st.form_submit_button("Submit Feedback")

        if submitted:
            # Baseline starting_salary is read straight from MAJOR_DATA (not
            # get_annual_salary_for_year), matching the requested "baseline"
            # framing -- it's the occupation's raw entry-level wage, not the
            # training-delay-adjusted figure Medicine/Law/Athletic Training
            # use elsewhere in the app.
            starting_salary = MAJOR_DATA[major]["starting_salary"]
            # DTI here is the literal loan slider divided by starting salary,
            # per the requested formula -- it intentionally does NOT use
            # get_effective_principal(), so Medicine/Law's additional
            # training debt is not included in this particular ratio.
            dti_ratio = round(loan_amount / starting_salary, 4) if starting_salary else None
            # Recomputed fresh (cheap, pure functions, no API calls) rather
            # than reused from st.session_state, so the survey reflects
            # exact click-time state even if the user never pressed
            # Calculate/Compare before submitting.
            scenario_a = compute_scenario_results(major, loan_amount, interest_rate, repayment_strategy,
                                                   personal_contribution, city_info["col_index"])

            context = {
                "scenario_a_school_name": school_name_a or None,
                "scenario_a_major": major,
                "scenario_a_loan_amount": loan_amount,
                "scenario_a_interest_rate": interest_rate,
                "scenario_a_repayment_strategy": repayment_strategy,
                "scenario_a_starting_salary": starting_salary,
                "scenario_a_dti_ratio": dti_ratio,
                "scenario_a_monthly_payment": scenario_a["repayment_result"].get("monthly_payment"),
                "scenario_a_payoff_years": scenario_a["repayment_result"]["payoff_years"],
                "scenario_a_total_interest": scenario_a["repayment_result"]["total_interest"],
                "scenario_a_earnings_premium": scenario_a["roi_result"]["earnings_premium"],
                "scenario_a_roi_pct": scenario_a["roi_result"]["roi_pct"],
                "scenario_a_personal_contribution": personal_contribution,
                "scenario_a_coa_inflation_rate": inflation_rate_a,
                "scenario_a_grants_per_year": grants_per_year_a,
            }

            # Scenario B / roi_pct_delta only exist when Compare Mode is on
            # at the moment of submission -- they stay absent (NULL in the
            # database) otherwise, since there's no Scenario B to report.
            if compare_mode:
                starting_salary_b = MAJOR_DATA[major_b]["starting_salary"]
                dti_ratio_b = round(loan_amount_b / starting_salary_b, 4) if starting_salary_b else None
                scenario_b = compute_scenario_results(major_b, loan_amount_b, interest_rate_b, repayment_strategy_b,
                                                       personal_contribution_b, city_info["col_index"])
                context.update({
                    "scenario_b_school_name": school_name_b or None,
                    "scenario_b_major": major_b,
                    "scenario_b_loan_amount": loan_amount_b,
                    "scenario_b_interest_rate": interest_rate_b,
                    "scenario_b_repayment_strategy": repayment_strategy_b,
                    "scenario_b_starting_salary": starting_salary_b,
                    "scenario_b_dti_ratio": dti_ratio_b,
                    "scenario_b_monthly_payment": scenario_b["repayment_result"].get("monthly_payment"),
                    "scenario_b_payoff_years": scenario_b["repayment_result"]["payoff_years"],
                    "scenario_b_total_interest": scenario_b["repayment_result"]["total_interest"],
                    "scenario_b_earnings_premium": scenario_b["roi_result"]["earnings_premium"],
                    "scenario_b_roi_pct": scenario_b["roi_result"]["roi_pct"],
                    "scenario_b_personal_contribution": personal_contribution_b,
                    "scenario_b_coa_inflation_rate": inflation_rate_b,
                    "scenario_b_grants_per_year": grants_per_year_b,
                })
                roi_a = scenario_a["roi_result"]["roi_pct"]
                roi_b = scenario_b["roi_result"]["roi_pct"]
                context["roi_pct_delta"] = round(abs(roi_a - roi_b), 2) if roi_a is not None and roi_b is not None else None

            saved = save_survey_response(perception_change, feedback_text, context)
            if saved:
                st.session_state.survey_submitted = True
                st.rerun()
            else:
                st.error("Something went wrong saving your response -- please try again.")
else:
    st.success("Thank you! Your feedback has been recorded anonymously.")

st.divider()

# ---- 5f. Methodology & Sources Footer -------------------------------------

with st.expander("📚 Methodology & Sources"):
    # st.markdown renders LaTeX math between paired "$" within a paragraph --
    # this text uses "$" only for literal dollar amounts, so every "$" is
    # escaped below to stop Streamlit from swallowing text between an
    # accidental pair of them into a garbled math span.
    methodology_text = """
**Major salary data.** For each major, we look up two real numbers from
the U.S. Bureau of Labor Statistics (BLS): what someone new to that career
typically earns starting out, and what someone earns after being in that
career for a while. *Starting Salary* is the 25th-percentile annual wage
for that occupation — meaning 25% of workers in that job earn less than
this, a reasonable stand-in for "what a typical new grad makes." *Growth
Rate* is how much pay would need to grow every year, for 10 years straight,
to climb from that starting number up to the occupation's median (the
middle-of-the-pack wage): `(median / entry) ** (1/10) - 1`.

Why the 25th percentile instead of the 10th? BLS's 10th percentile mixes
in a lot of part-time workers and some data quirks that can make pay look
unrealistically low for certain careers — most noticeably physicians (see
below). The 25th percentile is a more realistic floor for "typical new
grad" pay. This growth rate is our own estimate built from real BLS wage
data, not something BLS itself publishes — BLS doesn't track how any one
person's paycheck actually changes over 10 years.

| Major | BLS Occupation (SOC) | 25th Pctile | Median |
|---|---|---|---|
| Computer Science | Software Developers (15-1252) | $101,200 | $132,270 |
| Nursing | Registered Nurses (29-1141) | $75,990 | $86,070 |
| Business | Business Operations Specialists, All Other (13-1199) | $59,010 | $79,590 |
| Finance | Financial and Investment Analysts (13-2051) | $76,880 | $99,010 |
| Humanities | Market Research Analysts & Marketing Specialists (13-1161) | $52,840 | $74,680 |
| Arts | Fine Artists, incl. Painters/Sculptors/Illustrators (27-1013) | $38,160 | $59,300 |
| Sports Management | Coaches and Scouts (27-2022) | $32,440 | $45,910 |
| Exercise Science | Exercise Physiologists (29-1128) | $45,870 | $54,860 |
| Athletic Training | Athletic Trainers (29-9091) | $49,750 | $57,930 |
| Medicine | Family Medicine Physicians (29-1215) | $152,810 | $224,640 |
| Law | Lawyers (23-1011) | $98,030 | $145,760 |

Source: [bls.gov/oes/2023/may](https://www.bls.gov/oes/2023/may/) (occupation
profile pages by SOC code).

**Careers beyond the 11 majors above.** The dropdown also includes
hundreds of other real jobs, pulled straight from a BLS government data
file (cleaned by a script called `data_pipeline.py`) instead of being
hand-picked one at a time. Each gets the same Starting Salary/Growth Rate
treatment described above, computed the exact same way.

BLS marks some wages with special symbols instead of numbers, which we
have to clean up: `#` means the real number was too high to publish, so we
use BLS's own published floor for that case ($239,200/year); `*` means the
number wasn't reliable enough to publish at all, so we either drop that
career entirely (if we don't even have a usable median wage for it) or
fall back to a flat 3%/year growth estimate (if only the "starting salary"
half was unusable). If a BLS career happens to share a name with one of
the 11 hand-picked majors above, the hand-picked version always wins,
since it has extra detail (like training delays) the generic BLS data
doesn't capture.

The **"Career Salary Data" sidebar selector** lets you pick
whether these extra careers use *National* average wages or
*California*-specific wages, which can be noticeably higher for some jobs
(tech and healthcare especially). This is real state-level government
data, not just the national number scaled up.

**Majors that need school beyond a 4-year degree.** In real life,
Athletic Training, Medicine, and Law don't pay a professional salary
right after a 4-year degree — you need more school first. This calculator
models that delay honestly instead of pretending everyone starts earning
right away:

- **Athletic Training**: 2 years with no income, representing the
  master's degree BLS says is now typically required for this job.
- **Medicine**: 4 years with no income (medical school), then 3 years
  earning a flat $65,000/year (a stand-in for a medical resident's real
  pay, which actually rises a bit each year — simplified here to one
  number; source: AAMC's 2024 median first-year resident stipend). After
  that, the real Family Medicine Physician salary from the table above
  kicks in. On top of your loan, we add **$205,000** — the median amount
  medical school itself costs, according to AAMC's 2024 data
  ([source](https://www.aamc.org/data-reports/students-residents/report/physician-education-debt-and-cost-attend-medical-school)).
- **Law**: 3 years with no income (law school), then the real Lawyer
  salary from the table above kicks in. We add **$130,000** on top of your
  loan for average law school debt, from the ABA's 2024 survey
  ([source](https://www.americanbar.org/groups/young_lawyers/resources/after-the-bar/personal-financial/young-lawyers-significantly-impacted-by-high-debt-burdens/)).

During those unpaid years, this calculator shows $0 income — and any loan
you've taken out is still quietly racking up interest the whole time,
since you're not making payments yet. That's on purpose, not a bug: it's
the whole point of showing this stuff honestly. A first-year med student
really does earn $0, not a doctor's salary.

**What if you skip college? The high school graduate baseline.** We
compare every major against $49,192/year — real median pay for full-time
workers 25 and older who only finished high school (based on $946/week in
late 2024, annualized).
[Source: BLS](https://www.bls.gov/opub/ted/2024/median-weekly-earnings-946-for-workers-with-high-school-diploma-1533-for-bachelors-degree.htm).
We assume this grows a modest 2%/year (a stand-in for normal raises and
cost-of-living bumps) since BLS doesn't publish a real year-by-year
trajectory for this group the way it does for individual careers.

**How your loan payment is calculated.** *Standard 10-Year* just means a
fixed payment every month for 10 years, using the standard math lenders
use to make sure your last payment fully pays off both what you borrowed
and the interest on it (this is called "amortization"). *Income-Driven
Repayment (IDR)* works differently: your payment is 10% of your
"discretionary income" — basically your salary minus a $22,000 living
allowance — and after 20 years, whatever's still unpaid gets forgiven.
This is a simplified version of real federal IDR plans, not an exact
copy of federal rules. For Medicine, Law, and Athletic Training, both
options are calculated using your loan *plus* the extra training debt
described above — not just the loan by itself.

**How we calculate 10-Year ROI (return on investment).** We add up 10
years of a major's earnings, subtract whatever loan payments you made
during that time, and compare the result to what a debt-free high school
graduate would have earned over the same 10 years. ROI% specifically
compares that result to your *total investment* — not just your loan.
Total investment means your effective loan principal (your loan, plus any
extra training debt like medical school) *plus* whatever you type into
**Personal Contribution ($)**: savings, scholarships, or family money that
went toward school without being borrowed. Personal Contribution only
affects this ROI% comparison — it's never added to the loan itself,
since you don't pay interest on money you never borrowed. This means two
people with the exact same major and loan, but different Personal
Contributions, will see different ROI% numbers — on purpose. ROI here
means "return on everything you actually put in," not just "return on
your loan."

**Why we adjust for cost of living.** A dollar goes a lot further in
Columbus than it does in San Francisco. So both sides of the ROI
comparison — the major's outcome and the high-school-grad baseline — get
adjusted for the cost of living in whatever City/Metro Area you picked
(we assume the high school grad lives in the same city, since this app
only has one city selector). That's what **"(COL-Adjusted)"** means next
to "10-Year Earnings Premium" and the ROI charts. Picking "National
Average" is the same as not adjusting at all. One thing that does *not*
get cost-of-living adjusted: your total investment (debt + personal
contribution) — you owe a fixed dollar amount no matter where you live,
so that number stays as-is. In Compare Mode, both scenarios share one
city selector rather than each getting their own.

**Taxes.** We use real 2024 federal tax brackets for a single filer with
no dependents (IRS Rev. Proc. 2023-34), plus FICA taxes (6.2% Social
Security, up to a $168,600 wage cap, + 1.45% Medicare). We don't model
itemized deductions, tax credits, or the extra Medicare tax that kicks in
above $200K (no major's salary here reaches that high). For state tax, we
use real tax brackets for New York, California, Ohio, and Minnesota — a
single flat rate would badly overstate what most people actually pay
(New York's top rate of 10.9%, for example, only kicks in above $25
million). Illinois, Georgia, Colorado, Texas, Pennsylvania, Arizona,
Michigan, North Carolina, and Massachusetts are genuinely flat-rate
states (Massachusetts also has a 4% surtax above $1M, which we skip for
the same reason we skip the federal Additional Medicare Tax — no major's
trajectory here gets close); Florida, Washington, and Tennessee charge no
state income tax at all. New York City's local tax is approximated as a
flat 3.5%
(its real resident tax bracket actually ranges from 3.078% to 3.876%
depending on income, so 3.5% is a reasonable stand-in).
[Source: Tax Foundation, 2024 State Income Tax Rates](https://taxfoundation.org/data/all/state/state-income-tax-rates-2024/).

**How "cost of living" is measured.** Each city's cost-of-living number
comes from real government data (BEA Regional Price Parities, 2023),
by way of the Tax Foundation's "Real Value of $100 by Metro" report. In
short: if $100 buys less in a city than the national average, that city's
cost-of-living number goes up. "National Average" doesn't correspond to
any one state, which is why we show its tax rate as "N/A" instead of "$0"
— those mean different things.

**What "Career Stage Snapshot" actually changes.** Switching between
"Starting" and "Mid-Career" only changes what you see in the Real-World
Take-Home section above — it does not change your loan payoff schedule or
your 10-Year ROI numbers, which always simulate a full, real year-by-year
path starting from year 1 no matter which snapshot you're looking at.
Think of it as a window into one moment of a story that's always the same
story — not a way to start the story over from a different point.

**How "Compare Two Scenarios" works.** Comparing two scenarios runs the
exact same calculations described above, just twice — once for each
scenario, with no shortcuts or different math. Right now, comparisons
only cover loan payoff and 10-Year ROI; take-home pay and cost of living
aren't compared side by side yet, since that would need each scenario to
have its own city and career-stage picks. Scenario A is always your main
sidebar inputs; turning on Compare Mode adds Scenario B next to it rather
than replacing anything, so turning it back off never loses what you've
entered.

**Where school cost and debt numbers come from.** Cost of Attendance and
debt figures come from the U.S. Department of Education's College
Scorecard ([collegescorecard.ed.gov/data](https://collegescorecard.ed.gov/data/)).
In-state and out-of-state Cost of Attendance are pre-calculated for over
5,000 real U.S. schools (see `clean_college_scorecard.py` for exactly how)
rather than looked up live each time. Typical debt-at-graduation is looked
up live instead, so it works for any school in College Scorecard's
database. Both numbers are shown just for context — they don't feed into
any of the calculator's math directly. Each scenario has its own school
field, so Compare Mode can hold, say, "Computer Science at School A"
against "Computer Science at School B." When your school is found, Cost
of Attendance below auto-fills using in-state or out-of-state pricing,
based on whether you checked **In-State Student?**. If your school isn't
found, or you'd rather enter your own number, typing over the auto-filled
value always works — it won't get overwritten later.

**How your loan amount is actually calculated.** You don't type in a loan
amount directly — instead, each scenario asks for three things per year:
Cost of Attendance, Personal Contribution, and Grants & Scholarships.
Cost of Attendance is assumed to grow a little every year (an estimated
inflation rate), while Personal Contribution and Grants & Scholarships
stay the same dollar amount each year. Each year's loan amount is
whatever's left after subtracting Personal Contribution and Grants &
Scholarships from that year's Cost of Attendance (never less than $0),
and the total loan is those four years added together:
`Loan (Year N) = max(Cost of Attendance × (1 + inflation rate)^(N-1) − Personal Contribution − Grants & Scholarships, 0)`,
added up for all 4 years of an assumed bachelor's degree. Because tuition
keeps rising while your personal contribution and any scholarships don't,
the gap — and your loan — tends to grow a bit each year.

**Why Grants & Scholarships and Personal Contribution are treated
differently.** Personal Contribution counts toward your "total
investment" (the ROI% comparison) because it's money you or your family
actually gave up — a real cost to you. Grants & Scholarships is free
money from someone else: it shrinks how much you have to borrow, but
since it was never your money to begin with, it's left out of your "total
investment" on purpose.

**How we estimate tuition inflation for your specific school.** We
compare a school's real Cost of Attendance in 2018 versus 2022 (from
College Scorecard) and calculate the steady yearly growth rate that would
turn one into the other: `(coa_2022 / coa_2018) ** (1/4) - 1`. This kind
of calculation is called CAGR (compound annual growth rate), which just
means "the constant percentage growth per year that explains the total
change." We use fixed years (2018 and 2022)
instead of "the most recent data available" so this estimate doesn't
quietly change every time College Scorecard updates. If we can't find
your specific school's history, we fall back to a typical rate based on
the type of school: **2.7%/year** for public schools, **3.9%/year** for
private non-profit schools (both from the College Board's 2024 tuition
pricing report). **Private for-profit schools** don't have an equally
solid recent number available, so we use **2.5%/year** as an educated
estimate based on general inflation trends — this one is a judgment call,
not a number we found in a report. If we don't even know what type of
school it is, we default to the public-school rate.

**What we save when you submit the survey.** Each anonymous response
saves your exact inputs and results at that moment: school, major, loan
amount, personal contribution, interest rate, repayment strategy, your
major's starting salary, and something called `dti_ratio` — short for
"debt-to-income ratio," which just means your loan amount divided by your
starting salary, a common way to describe how big a loan is relative to
what you'll earn. We also save your monthly payment (blank for IDR, since
that payment amount changes over time), payoff timeline, total interest,
10-year earnings premium, and ROI%. If Compare Mode was on when you
submitted, we save all of that for Scenario B too, plus the difference
between the two scenarios' ROI%. None of this is tied to your name or
any personal identifying information — it's used to help a companion
research paper understand how tools like this one affect students'
thinking about college and careers.

*This tool produces educational estimates for a student research project,
not financial advice. Figures are national averages/percentiles and will not
reflect any individual's actual salary, cost of living, or loan terms.*
        """
    st.markdown(methodology_text.replace("$", r"\$"))

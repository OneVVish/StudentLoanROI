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
import plotly.graph_objects as go
import requests
import streamlit as st
from st_supabase_connection import SupabaseConnection, execute_query

# ============================================================
# 1. CONFIGURATION & CONSTANTS
# ============================================================

COLLEGE_SCORECARD_URL = "https://api.data.gov/ed/collegescorecard/v1/schools.json"

# Starting salary + mid-career (median) salary per major, sourced from the
# U.S. Bureau of Labor Statistics Occupational Employment and Wage Statistics
# (OEWS), May 2023 national estimates (bls.gov/oes/2023/may/). Each major is
# mapped to its closest BLS-tracked occupation (SOC code below).
# "starting_salary" is that occupation's 10th-percentile annual wage (a proxy
# for an entry-level new grad); "median_salary" is the occupation's median
# annual wage (a proxy for a mid-career worker, ~10 years in). The annual
# growth rate implied between these two real BLS figures is derived on demand
# by get_major_growth_rate() -- see 2a -- rather than stored separately, so
# the loan/ROI simulation's year-10 salary always matches this median exactly.
MAJOR_DATA = {
    # Software Developers, SOC 15-1252: 10th pct $77,020 / median $132,270
    "Computer Science": {"starting_salary": 77020, "median_salary": 132270},
    # Registered Nurses, SOC 29-1141: 10th pct $63,720 / median $86,070
    "Nursing": {"starting_salary": 63720, "median_salary": 86070},
    # Business Operations Specialists, All Other, SOC 13-1199: 10th pct $44,370 / median $79,590
    "Business": {"starting_salary": 44370, "median_salary": 79590},
    # Financial and Investment Analysts, SOC 13-2051: 10th pct $60,830 / median $99,010
    "Finance": {"starting_salary": 60830, "median_salary": 99010},
    # Market Research Analysts and Marketing Specialists, SOC 13-1161: 10th pct $40,040 / median $74,680
    "Humanities": {"starting_salary": 40040, "median_salary": 74680},
    # Fine Artists, Including Painters, Sculptors, and Illustrators, SOC 27-1013: 10th pct $28,390 / median $59,300
    "Arts": {"starting_salary": 28390, "median_salary": 59300},
    # Coaches and Scouts, SOC 27-2022: 10th pct $27,040 / median $45,910
    "Sports Management": {"starting_salary": 27040, "median_salary": 45910},
    # Exercise Physiologists, SOC 29-1128: 10th pct $35,460 / median $54,860
    "Exercise Science": {"starting_salary": 35460, "median_salary": 54860},
    # Athletic Trainers, SOC 29-9091: 10th pct $43,180 / median $57,930. BLS
    # now lists a master's as the typical entry-level education, so this
    # major has a 2-year unpaid training delay (the accredited master's
    # program) before the salary above applies -- see get_annual_salary_for_year.
    "Athletic Training": {
        "starting_salary": 43180, "median_salary": 57930,
        "unpaid_training_years": 2,
    },
    # Family Medicine Physicians, SOC 29-1215: 10th pct $68,890 / median
    # $224,640. 4 unpaid years (med school) + 3 stipend years (residency;
    # 3-year length matches Family Medicine's real ACGME program length, so
    # this pathway is internally consistent). Stipend is AAMC's 2024
    # preliminary median first-post-MD-year resident stipend ($65,100),
    # used as a flat representative figure across residency (real PGY2/PGY3
    # pay is a few thousand higher). additional_training_debt is AAMC's 2024
    # median medical school debt ($205,000, aamc.org/data-reports/students-
    # residents) -- added to the user's loan slider as the true principal.
    "Medicine": {
        "starting_salary": 68890, "median_salary": 224640,
        "unpaid_training_years": 4, "stipend_training_years": 3,
        "stipend_salary": 65000, "additional_training_debt": 205000,
    },
    # Lawyers, SOC 23-1011: 10th pct $69,760 / median $145,760. 3 unpaid
    # years (law school, no paid-training equivalent). additional_training_
    # debt is the ABA Young Lawyers Division 2024 Student Loan Survey's
    # average law-school-only debt ($130,000, americanbar.org).
    "Law": {
        "starting_salary": 69760, "median_salary": 145760,
        "unpaid_training_years": 3, "additional_training_debt": 130000,
    },
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


def save_survey_response(
    perception_change: str,
    feedback_text: str,
    selected_major: str,
    loan_amount: float,
    interest_rate: float,
    repayment_strategy: str,
    starting_salary: float,
    dti_ratio,
) -> bool:
    """Insert one anonymous survey submission, tagged with the simulation
    context (major/loan/rate/strategy/DTI) active at the moment of
    submission, into the survey_responses table.

    The caller reads major/loan_amount/interest_rate/repayment_strategy
    straight from the sidebar widget variables, which Streamlit re-evaluates
    to their current value on every rerun -- including the rerun triggered
    by clicking "Submit Feedback" -- so this always captures the exact
    slider/dropdown state at click-time, never a stale value from an
    earlier run.

    Returns True on success, False on any failure (network, bad
    credentials, schema mismatch) so the caller can tell the user their
    submission didn't save instead of silently losing it.
    """
    try:
        conn = get_supabase_connection()
        execute_query(
            conn.table("survey_responses").insert(
                [{
                    "timestamp": datetime.now().isoformat(),
                    "perception_change": perception_change,
                    "feedback_text": feedback_text,
                    "selected_major": selected_major,
                    "loan_amount": loan_amount,
                    "interest_rate": interest_rate,
                    "repayment_strategy": repayment_strategy,
                    "starting_salary": starting_salary,
                    "dti_ratio": dti_ratio,
                }],
                count="None",
            ),
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
                   effective_principal: float, years: int = ROI_WINDOW_YEARS) -> dict:
    """
    ROI = (major's cumulative earnings over `years`, minus loan payments made
    in that window) compared against a debt-free high school graduate's
    cumulative earnings over the same window. `effective_principal` (not the
    raw loan slider) is the ROI% denominator, since majors with additional
    training debt (e.g. Medicine's median med school debt) took on more than
    the slider value to reach this earning power -- see get_effective_principal.
    """
    major_cumulative_earnings = sum(
        get_annual_salary_for_year(major_name, y) for y in range(years)
    )
    hs_cumulative_earnings = sum(
        HS_GRAD_SALARY * (1 + HS_GRAD_GROWTH_RATE) ** y for y in range(years)
    )

    major_net_position = major_cumulative_earnings - total_loan_payments_in_window
    hs_net_position = hs_cumulative_earnings
    earnings_premium = major_net_position - hs_net_position
    roi_pct = (earnings_premium / effective_principal * 100) if effective_principal > 0 else None

    return {
        "major_cumulative_earnings": major_cumulative_earnings,
        "hs_cumulative_earnings": hs_cumulative_earnings,
        "major_net_position": major_net_position,
        "hs_net_position": hs_net_position,
        "earnings_premium": earnings_premium,
        "roi_pct": roi_pct,
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
        title="10-Year Net Financial Position: Major vs. High School Baseline",
        text_auto=".2s",
    )
    fig.update_layout(yaxis_tickprefix="$", showlegend=False)
    return fig


def build_takehome_breakdown_chart(take_home: dict):
    """A waterfall (gross salary subtracting away to take-home pay) needs
    plotly.graph_objects -- express has no waterfall trace type."""
    fig = go.Figure(go.Waterfall(
        x=["Gross", "Federal Tax", "State + Local Tax", "FICA", "Take-Home"],
        y=[take_home["gross"], -take_home["federal_tax"], -take_home["state_tax"],
           -take_home["fica_tax"], take_home["net_take_home"]],
        measure=["absolute", "relative", "relative", "relative", "total"],
    ))
    fig.update_layout(title="Where Your Salary Actually Goes", yaxis_tickprefix="$", showlegend=False)
    return fig


def build_survey_pie_chart(survey_df: pd.DataFrame):
    counts = survey_df["perception_change"].value_counts().reset_index()
    counts.columns = ["Response", "Count"]
    fig = px.pie(counts, names="Response", values="Count", title="Did This Tool Change Student Perceptions?")
    return fig


def build_perception_by_major_chart(survey_df: pd.DataFrame):
    """Grouped bar chart: perception_change counts broken down by
    selected_major, for spotting whether some majors are more "elastic"
    (more likely to report a changed perception) than others. Rows saved
    before this field existed have a null selected_major and are excluded
    here (they still count in the overall pie chart/metrics above)."""
    plottable = survey_df.dropna(subset=["selected_major", "perception_change"])
    cross_tab = (
        plottable.groupby(["selected_major", "perception_change"])
        .size()
        .reset_index(name="Count")
    )
    fig = px.bar(
        cross_tab, x="selected_major", y="Count", color="perception_change",
        title="Impact of the Tool by Selected Major",
        labels={"selected_major": "Selected Major", "perception_change": "Response", "Count": "Responses"},
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

if "survey_submitted" not in st.session_state:
    st.session_state.survey_submitted = False


# ============================================================
# 4. SIDEBAR — USER INPUTS
# ============================================================

st.sidebar.header("🎓 Your Profile")

major = st.sidebar.selectbox("Target Major", list(MAJOR_DATA.keys()))
school_name = st.sidebar.text_input("Target Undergraduate School", placeholder="e.g. University of Michigan")
loan_amount = st.sidebar.number_input("Total Student Loan Amount ($)", min_value=0, max_value=300000,
                                       value=30000, step=500)
interest_rate = st.sidebar.number_input("Average Loan Interest Rate (%)", min_value=0.0, max_value=20.0,
                                         value=5.5, step=0.1)
repayment_strategy = st.sidebar.selectbox(
    "Repayment Strategy",
    ["Standard 10-Year", "Income-Driven Repayment (IDR)"],
)
city = st.sidebar.selectbox("City / Metro Area", list(CITY_DATA.keys()))
career_stage_label = st.sidebar.radio("Career Stage Snapshot", list(CAREER_STAGE_OPTIONS.keys()))
career_stage_key = CAREER_STAGE_OPTIONS[career_stage_label]

with st.sidebar.expander("College Scorecard Lookup (optional)"):
    st.caption("Pulls real tuition & median debt for the school above via api.data.gov.")
    scorecard_api_key = st.text_input("API Key", value="DEMO_KEY", type="password")

st.sidebar.divider()
admin_enabled = st.sidebar.checkbox("🔐 Admin Analytics View")

calculate_clicked = st.sidebar.button("🚀 Calculate My Payoff Plan & ROI", use_container_width=True)
if calculate_clicked:
    log_usage_event("calculation")
    st.session_state.has_calculated = True


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
        "timestamp", "perception_change", "feedback_text", "selected_major",
        "loan_amount", "interest_rate", "repayment_strategy", "starting_salary", "dti_ratio",
    ])

    col1, col2 = st.columns(2)
    col1.metric("Total App Interactions", len(usage_df))
    col2.metric("Total Survey Responses", len(survey_df))

    if not survey_df.empty:
        # Research metrics: skip NaN automatically (rows saved before these
        # fields existed just don't count toward the average), and check
        # there's at least one real value before displaying anything.
        research_col1, research_col2 = st.columns(2)
        if survey_df["loan_amount"].notna().any():
            research_col1.metric("Average Loan Amount Simulated", fmt_money(survey_df["loan_amount"].mean()))
        else:
            research_col1.metric("Average Loan Amount Simulated", "N/A")
        if survey_df["dti_ratio"].notna().any():
            research_col2.metric("Average Debt-to-Income Ratio", f"{survey_df['dti_ratio'].mean():.2f}")
        else:
            research_col2.metric("Average Debt-to-Income Ratio", "N/A")

        chart_col, table_col = st.columns(2)
        chart_col.plotly_chart(build_survey_pie_chart(survey_df), use_container_width=True)
        table_col.dataframe(
            survey_df[[
                "timestamp", "selected_major", "loan_amount", "interest_rate",
                "repayment_strategy", "starting_salary", "dti_ratio", "feedback_text",
            ]],
            use_container_width=True, height=380,
        )

        if survey_df["selected_major"].notna().any():
            st.plotly_chart(build_perception_by_major_chart(survey_df), use_container_width=True)
    else:
        st.info("No survey responses recorded yet.")

    st.divider()

# ---- 5b. School Data Lookup (local COA dataset + College Scorecard API) --
# Cost of Attendance (in/out-of-state) comes from the local dataset built by
# clean_college_scorecard.py -- currently a small real-data sample (see
# data/college_scorecard_sample_raw.csv), so only a handful of schools will
# match until the full College Scorecard institution file is run through
# that script and swapped in at data/college_coa_clean.csv. Median debt is
# still fetched live, which works for any school regardless of local
# dataset coverage.

if school_name:
    coa_match = find_school_coa(school_name, load_coa_dataset())
    debt_data = fetch_median_debt(school_name, scorecard_api_key)

    if coa_match is not None:
        coa_text = (
            f"**{coa_match['INSTNM']}** ({coa_match['control_type']}) — "
            f"In-state Cost of Attendance: {fmt_money(coa_match['in_state_coa'])} | "
            f"Out-of-state Cost of Attendance: {fmt_money(coa_match['out_of_state_coa'])}"
        ).replace("$", r"\$")
        st.info(coa_text)
    else:
        st.caption(
            "No Cost of Attendance match in the local dataset yet "
            "(currently only a small sample of schools -- see data/college_coa_clean.csv)."
        )

    if debt_data and debt_data.get("median_debt"):
        # Escape "$" -- st.caption renders markdown, and a pair of literal
        # "$" gets parsed as inline LaTeX math, mangling the text between them.
        st.caption(
            f"Median completer debt for {debt_data['name']}: {fmt_money(debt_data['median_debt'])}"
            .replace("$", r"\$")
        )

# ---- 5c. Calculator Results ----------------------------------------------

if st.session_state.has_calculated:
    effective_principal = get_effective_principal(major, loan_amount)

    if repayment_strategy == "Standard 10-Year":
        repayment_result = calculate_standard_repayment(effective_principal, interest_rate)
        strategy_label = "Standard 10-Year"
    else:
        repayment_result = calculate_idr_repayment(effective_principal, interest_rate, major)
        strategy_label = "Income-Driven Repayment"

    roi_result = calculate_roi(
        major, repayment_result["total_paid_in_roi_window"], effective_principal,
    )

    st.subheader(f"📈 Results for {major} — {strategy_label}")

    additional_training_debt = MAJOR_DATA[major].get("additional_training_debt", 0)
    if additional_training_debt > 0:
        # Escape "$" -- st.caption renders markdown, and a *pair* of literal "$"
        # (two fmt_money() calls in one string) gets parsed as inline LaTeX math,
        # silently mangling the text between them.
        caption_text = (
            f"Effective loan principal including {fmt_money(additional_training_debt)} "
            f"est. average professional-school debt: **{fmt_money(effective_principal)}**"
        ).replace("$", r"\$")
        st.caption(caption_text)

    metric_cols = st.columns(4)
    metric_cols[0].metric(
        "Monthly Payment",
        fmt_money(repayment_result["monthly_payment"]) if "monthly_payment" in repayment_result else "Varies (IDR)",
    )
    metric_cols[1].metric("Payoff Timeline", f"{repayment_result['payoff_years']:.1f} yrs")
    metric_cols[2].metric("Total Interest Paid", fmt_money(repayment_result["total_interest"]))
    metric_cols[3].metric(
        "10-Year Earnings Premium",
        fmt_money(roi_result["earnings_premium"]),
        delta=fmt_pct(roi_result["roi_pct"]) + " ROI" if roi_result["roi_pct"] is not None else None,
    )

    if repayment_result["forgiven_amount"] > 0:
        st.warning(
            f"Under IDR, {fmt_money(repayment_result['forgiven_amount'])} of principal remains "
            f"unpaid after {IDR_MAX_TERM_YEARS} years and is forgiven."
        )

    chart_col1, chart_col2 = st.columns(2)
    chart_col1.plotly_chart(build_balance_chart(repayment_result["schedule"], strategy_label), use_container_width=True)
    chart_col2.plotly_chart(build_roi_bar_chart(roi_result["hs_net_position"], roi_result["major_net_position"], major), use_container_width=True)

    # ---- 5d. Real-World Take-Home Snapshot --------------------------------

    st.subheader(f"🏙️ Real-World Take-Home — {career_stage_label} in {city}")

    city_info = CITY_DATA[city]
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
        st.plotly_chart(build_takehome_breakdown_chart(take_home), use_container_width=True)
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

            saved = save_survey_response(
                perception_change, feedback_text,
                major, loan_amount, interest_rate, repayment_strategy,
                starting_salary, dti_ratio,
            )
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
**Major salary data** — U.S. Bureau of Labor Statistics, Occupational Employment
and Wage Statistics (OEWS), May 2023 national estimates. Each major is mapped
to its closest BLS-tracked occupation; *Starting Salary* is that occupation's
10th-percentile annual wage (a proxy for entry-level pay), and *Growth Rate*
is the compound annual rate needed to climb from the 10th percentile to the
occupation's median wage over an assumed 10-year horizon —
`(median / entry) ** (1/10) - 1`. This growth rate is a modeling assumption
applied to real BLS wage-distribution data, not a BLS-published trajectory;
BLS does not track individual workers' pay over time.

| Major | BLS Occupation (SOC) | 10th Pctile | Median |
|---|---|---|---|
| Computer Science | Software Developers (15-1252) | $77,020 | $132,270 |
| Nursing | Registered Nurses (29-1141) | $63,720 | $86,070 |
| Business | Business Operations Specialists, All Other (13-1199) | $44,370 | $79,590 |
| Finance | Financial and Investment Analysts (13-2051) | $60,830 | $99,010 |
| Humanities | Market Research Analysts & Marketing Specialists (13-1161) | $40,040 | $74,680 |
| Arts | Fine Artists, incl. Painters/Sculptors/Illustrators (27-1013) | $28,390 | $59,300 |
| Sports Management | Coaches and Scouts (27-2022) | $27,040 | $45,910 |
| Exercise Science | Exercise Physiologists (29-1128) | $35,460 | $54,860 |
| Athletic Training | Athletic Trainers (29-9091) | $43,180 | $57,930 |
| Medicine | Family Medicine Physicians (29-1215) | $68,890 | $224,640 |
| Law | Lawyers (23-1011) | $69,760 | $145,760 |

Source: [bls.gov/oes/2023/may](https://www.bls.gov/oes/2023/may/) (occupation
profile pages by SOC code).

**Majors requiring school beyond a 4-year bachelor's** — Athletic Training,
Medicine, and Law don't pay a professional salary right after a 4-year
degree in real life, so this calculator models a training delay for them
instead of pretending otherwise:

- **Athletic Training**: 2 unpaid years, representing the accredited
  master's program BLS now lists as this occupation's typical entry-level
  education.
- **Medicine**: 4 unpaid years (med school) + 3 years earning a flat
  $65,000/year stipend (AAMC's 2024 preliminary median first-post-MD-year
  resident stipend; real pay rises a few thousand per PGY year, simplified
  here to one flat figure), matching Family Medicine's real 3-year ACGME
  residency length — then the Family Medicine Physician salary above
  applies. **+$205,000** additional loan principal (AAMC's 2024 median
  medical school debt, [aamc.org](https://www.aamc.org/data-reports/students-residents/report/physician-education-debt-and-cost-attend-medical-school)),
  added on top of the loan slider.
- **Law**: 3 unpaid years (law school, no paid-training equivalent), then
  the Lawyer salary above applies. **+$130,000** additional loan principal
  (ABA Young Lawyers Division 2024 Student Loan Survey average law-school
  debt, [americanbar.org](https://www.americanbar.org/groups/young_lawyers/resources/after-the-bar/personal-financial/young-lawyers-significantly-impacted-by-high-debt-burdens/)).

During unpaid years, gross salary is $0 and any loan already taken out
still accrues interest with no payments — this is intentional, not a bug,
and it's a deliberate part of what this calculator is trying to show: at
age ~25 (year 1 out of undergrad), a Medicine major is still in medical
school with $0 income, not earning doctor money yet.

**High school graduate baseline** — $49,192/year, from median usual weekly
earnings of full-time workers age 25+ with a high school diploma and no
college ($946/week, Q3 2024), annualized. Source:
[BLS, "Median weekly earnings $946 for workers with high school diploma"](https://www.bls.gov/opub/ted/2024/median-weekly-earnings-946-for-workers-with-high-school-diploma-1533-for-bachelors-degree.htm).
Its annual growth rate (2%) is a modest cost-of-living/seniority assumption,
since BLS does not publish a matching by-experience wage trajectory for this
group.

**Repayment math** — Standard 10-Year uses the standard fixed-payment loan
amortization formula. Income-Driven Repayment models a payment of 10% of
discretionary income (salary above a $22,000 living allowance), with any
balance still outstanding after 20 years forgiven — a simplified version of
federal undergraduate REPAYE/SAVE-style IDR plans, not an exact reproduction
of federal rules. For Medicine/Law/Athletic Training, the loan principal fed
into both strategies is the loan slider **plus** the additional training
debt above, not the slider alone.

**10-Year ROI** — Cumulative 10-year earnings for the major, minus loan
payments made in that window, compared against the high school graduate's
cumulative 10-year earnings (assumed debt-free). ROI% is measured against
the *effective* principal (slider + any additional training debt), so a
Medicine major's ROI% reflects the true ~$200K+ total investment, not just
the undergrad loan amount entered.

**Taxes** — Federal tax uses real 2024 single-filer brackets and standard
deduction (IRS Rev. Proc. 2023-34); FICA is 6.2% Social Security (up to the
$168,600 2024 wage base) + 1.45% Medicare. Scope: single filer only, no
dependents, no itemized deductions/credits, no Additional Medicare Tax (no
major's trajectory reaches the $200K threshold for it). State tax uses real
marginal brackets for New York, California, and Ohio (a flat top-marginal
rate would badly overstate tax at these salary levels — e.g. NY's 10.9% top
rate only applies above $25M); Illinois, Georgia, Colorado, and Texas are
genuinely flat/zero-rate states. New York City's local tax is a flat 3.5%
approximation of its real 3.078%–3.876% resident bracket range. Source:
[Tax Foundation, 2024 State Income Tax Rates](https://taxfoundation.org/data/all/state/state-income-tax-rates-2024/).

**Cost of living** — City `col_index` values come from BEA Regional Price
Parities, 2023 release (national average = 100), via Tax Foundation's "Real
Value of $100 by Metro" compilation of the same BEA data:
`col_index = 10000 / real_value_of_100_dollars`. "National Average" has no
specific state, shown as tax "N/A" rather than "$0" — those are different
claims.

**Career Stage Snapshot** — This toggle ("Starting" vs. "Mid-Career") only
changes the Real-World Take-Home section above; it never changes the loan
payoff schedule or ROI numbers, which always simulate the full year-by-year
trajectory starting from year 1 regardless of which snapshot is selected.
Feeding a mid-career salary into the loan simulator as a fake "year 1" would
double-count growth and produce a nonsensical payoff schedule — the toggle
is a window into one point on the same real trajectory, not an alternate
starting condition.

**School Cost of Attendance & debt lookup** — U.S. Department of Education
College Scorecard ([collegescorecard.ed.gov/data](https://collegescorecard.ed.gov/data/)).
In-state/out-of-state Cost of Attendance comes from a locally pre-cleaned
dataset (see `clean_college_scorecard.py`, which derives it from
`COSTT4_A`/`COSTT4_P` and the public-school in-state/out-of-state tuition
swap) rather than a live call per lookup; that dataset currently covers only
a small real-data sample, so most schools won't have a match yet until the
full institution file is processed and swapped in. Median completer debt
has no equivalent in that dataset and is still fetched live, so it works
for any school. Both figures are shown as contextual information only and
are not used in the calculator's math.

**Survey data** — Each anonymous survey submission is tagged with the
simulation inputs active at the moment of submission (major, loan amount,
interest rate, repayment strategy) plus two derived research fields:
`starting_salary` (the major's baseline entry-level wage from `MAJOR_DATA`,
*not* the training-delay-adjusted figure Medicine/Law/Athletic Training use
elsewhere) and `dti_ratio` (loan amount ÷ starting salary -- the literal
slider value, not the effective principal that includes additional
training debt). This lets the admin view break survey responses down by
what the respondent was actually simulating, for the companion behavioral-
economics research paper.

*This tool produces educational estimates for a student research project,
not financial advice. Figures are national averages/percentiles and will not
reflect any individual's actual salary, cost of living, or loan terms.*
        """
    st.markdown(methodology_text.replace("$", r"\$"))

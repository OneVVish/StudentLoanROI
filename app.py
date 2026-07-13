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

import io
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
import streamlit.components.v1 as components
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
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
    "Computer Science": {"starting_salary": 101200, "median_salary": 132270, "soc_major_group": "15"},
    # Registered Nurses, SOC 29-1141: 25th pct $75,990 / median $86,070
    "Nursing": {"starting_salary": 75990, "median_salary": 86070, "soc_major_group": "29"},
    # Business Operations Specialists, All Other, SOC 13-1199: 25th pct $59,010 / median $79,590
    "Business": {"starting_salary": 59010, "median_salary": 79590, "soc_major_group": "13"},
    # Financial and Investment Analysts, SOC 13-2051: 25th pct $76,880 / median $99,010
    "Finance": {"starting_salary": 76880, "median_salary": 99010, "soc_major_group": "13"},
    # Market Research Analysts and Marketing Specialists, SOC 13-1161: 25th pct $52,840 / median $74,680
    "Humanities": {"starting_salary": 52840, "median_salary": 74680, "soc_major_group": "13"},
    # Fine Artists, Including Painters, Sculptors, and Illustrators, SOC 27-1013: 25th pct $38,160 / median $59,300
    "Arts": {"starting_salary": 38160, "median_salary": 59300, "soc_major_group": "27"},
    # Coaches and Scouts, SOC 27-2022: 25th pct $32,440 / median $45,910
    "Sports Management": {"starting_salary": 32440, "median_salary": 45910, "soc_major_group": "27"},
    # Exercise Physiologists, SOC 29-1128: 25th pct $45,870 / median $54,860
    "Exercise Science": {"starting_salary": 45870, "median_salary": 54860, "soc_major_group": "29"},
    # Athletic Trainers, SOC 29-9091: 25th pct $49,750 / median $57,930. BLS
    # now lists a master's as the typical entry-level education, so this
    # major has a 2-year unpaid training delay (the accredited master's
    # program) before the salary above applies -- see get_annual_salary_for_year.
    "Athletic Training": {
        "starting_salary": 49750, "median_salary": 57930,
        "unpaid_training_years": 2, "soc_major_group": "29",
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
        "soc_major_group": "29",
    },
    # Lawyers, SOC 23-1011: 25th pct $98,030 / median $145,760. 3 unpaid
    # years (law school, no paid-training equivalent). additional_training_
    # debt is the ABA Young Lawyers Division 2024 Student Loan Survey's
    # average law-school-only debt ($130,000, americanbar.org).
    "Law": {
        "starting_salary": 98030, "median_salary": 145760,
        "unpaid_training_years": 3, "additional_training_debt": 130000,
        "soc_major_group": "23",
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
        row.occ_title: {
            "starting_salary": row.a_pct25, "median_salary": row.a_median,
            # First 2 digits of the 6-digit SOC code (e.g. "15-1252" -> "15")
            # -- the SOC "major group" level, used to look up AI_EXPOSURE_BY_
            # SOC_GROUP for the optional AI Employability Risk module.
            "soc_major_group": str(row.occ_code).split("-")[0],
        }
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

# ---- College Prestige & Cost Estimator (optional "Advanced Analysis" mode) --
# Cost per tier is a straightforward sticker-price bucketing. The salary
# multiplier is the part that needs care: real research on a "prestige
# premium" is genuinely contested. Chetty et al. (Opportunity Insights,
# "Mobility Report Cards" / "Diversifying Society's Leaders?",
# opportunityinsights.org) find real, observable mid-career earnings
# differences by college selectivity tier. But Dale & Krueger (2002, NBER
# Working Paper 7322; 2011 update) found that gap shrinks toward zero once
# you control for the *student's own* ability/motivation -- i.e. the kind of
# student who gets admitted to and attends an Ivy-plus school would likely
# have earned close to the same wage regardless of where they went. These
# multipliers are set well below the raw observational gap Chetty et al.
# report, as a deliberately conservative middle ground between the two
# findings -- and are always surfaced as a modeled estimate, not a causal
# claim about any specific school, in the UI, PDF, and Methodology footer.
COLLEGE_PRESTIGE_TIERS = {
    "Tier 1: Elite Private (Ivy+ / Top 15)": {"coa_per_year": 85000, "salary_multiplier": 1.10},
    "Tier 2: Top Public / Public Ivy (In-State)": {"coa_per_year": 35000, "salary_multiplier": 1.05},
    "Tier 3: Standard Regional Public (In-State)": {"coa_per_year": 22000, "salary_multiplier": 1.00},
    "Tier 4: Out-of-State Public / Mid-Tier Private": {"coa_per_year": 55000, "salary_multiplier": 1.03},
}

# ---- AI Employability Risk Analysis (optional "Advanced Analysis" mode) -----
# A per-major "AI Exposure Score" is only as credible as its source -- so
# rather than inventing a unique 0-100 number per major, this is modeled at
# the SOC "major group" level (the first 2 digits of a 6-digit BLS SOC code,
# e.g. 15-xxxx = Computer & Mathematical), the level real published AI
# task-exposure research actually operates at: Felten, Raj & Seamans, "AI
# Occupational Exposure" (AIOE) index (nber.org/papers/w28959), and Eloundou,
# Manning, Mishkin & Rock, "GPTs are GPTs" (arXiv:2303.10130, 2023), both of
# which consistently find office/administrative-support and business/
# financial-operations tasks among the most LLM-exposed, and hands-on/
# in-person occupations (healthcare support, food service, construction,
# personal care, protective service) among the least. risk_level/score here
# are banded (Low=20, Medium=50, High=80), not a unique precision figure, to
# avoid implying false precision from a single detailed occupation title.
# "Exposure" measures task overlap with current AI tools, not certainty of
# job loss or automation -- see the Methodology footer for that distinction.
AI_EXPOSURE_BY_SOC_GROUP = {
    "11": {"label": "Management", "risk_level": "Medium", "score": 50,
           "rationale": "Judgment and people-management are hard to automate, but reporting/analysis tasks are increasingly AI-assisted."},
    "13": {"label": "Business & Financial Operations", "risk_level": "High", "score": 80,
           "rationale": "Analysis, reporting, and document-drafting tasks overlap heavily with current AI tool capabilities."},
    "15": {"label": "Computer & Mathematical", "risk_level": "Medium", "score": 55,
           "rationale": "Mixed evidence: some coding/analysis tasks are heavily AI-assisted, but system design and judgment stay human-led."},
    "17": {"label": "Architecture & Engineering", "risk_level": "Medium", "score": 45,
           "rationale": "Design/drafting tasks show moderate exposure; physical and safety judgment remain human-led."},
    "19": {"label": "Life, Physical & Social Science", "risk_level": "Medium", "score": 45,
           "rationale": "Data analysis is AI-assisted, but experimental/field work and domain judgment are not."},
    "21": {"label": "Community & Social Service", "risk_level": "Low", "score": 20,
           "rationale": "Relies on in-person trust and judgment that current AI systems can't substitute for."},
    "23": {"label": "Legal", "risk_level": "High", "score": 80,
           "rationale": "Document review and legal research are among the most-cited high-exposure task categories in the literature."},
    "25": {"label": "Educational Instruction & Library", "risk_level": "Medium", "score": 45,
           "rationale": "Content preparation is AI-assisted, but live instruction and mentorship are not."},
    "27": {"label": "Arts, Design, Entertainment, Sports & Media", "risk_level": "Medium", "score": 55,
           "rationale": "Writing/design tasks show real exposure; performance- and reputation-driven work much less so."},
    "29": {"label": "Healthcare Practitioners & Technical", "risk_level": "Low", "score": 30,
           "rationale": "Direct patient care and hands-on procedures remain largely outside current AI capability."},
    "31": {"label": "Healthcare Support", "risk_level": "Low", "score": 20,
           "rationale": "Hands-on, in-person care tasks with little task overlap with current AI systems."},
    "33": {"label": "Protective Service", "risk_level": "Low", "score": 15,
           "rationale": "Physical presence and split-second judgment dominate this work."},
    "35": {"label": "Food Preparation & Serving", "risk_level": "Low", "score": 10,
           "rationale": "Manual, in-person tasks with minimal overlap with current AI systems."},
    "37": {"label": "Building & Grounds Cleaning & Maintenance", "risk_level": "Low", "score": 10,
           "rationale": "Physical, in-person labor with minimal task overlap with current AI systems."},
    "39": {"label": "Personal Care & Service", "risk_level": "Low", "score": 15,
           "rationale": "In-person, relationship-driven work with minimal AI task overlap."},
    "41": {"label": "Sales & Related", "risk_level": "Medium", "score": 50,
           "rationale": "Research/outreach drafting is AI-assisted; relationship-building and negotiation are not."},
    "43": {"label": "Office & Administrative Support", "risk_level": "High", "score": 85,
           "rationale": "Consistently identified in the literature as the most AI-exposed occupational category."},
    "45": {"label": "Farming, Fishing & Forestry", "risk_level": "Low", "score": 10,
           "rationale": "Physical, outdoor labor with minimal overlap with current AI systems."},
    "47": {"label": "Construction & Extraction", "risk_level": "Low", "score": 10,
           "rationale": "Physical, hands-on labor with minimal overlap with current AI systems."},
    "49": {"label": "Installation, Maintenance & Repair", "risk_level": "Low", "score": 15,
           "rationale": "Physical, hands-on troubleshooting with minimal overlap with current AI systems."},
    "51": {"label": "Production", "risk_level": "Low", "score": 25,
           "rationale": "Physical assembly/manufacturing tasks with limited current AI (as opposed to separate robotics) task overlap."},
    "53": {"label": "Transportation & Material Moving", "risk_level": "Low", "score": 20,
           "rationale": "Physical operation tasks with limited current AI (as opposed to separate autonomy/robotics) task overlap."},
    "55": {"label": "Military Specific", "risk_level": "Low", "score": 20,
           "rationale": "Not covered in detail by the civilian occupational-exposure research this feature is based on."},
}

# ---- 2026 Regulatory & Macro Forecasting (optional "Advanced Analysis" mode) -
# Real, enacted federal law: the One Big Beautiful Bill Act (H.R. 1, 2025)
# replaces existing IDR plans with the Repayment Assistance Plan (RAP) and
# introduces a Tiered Standard Plan, both effective for new federal loan
# borrowers July 1, 2026 (existing borrowers transition by July 1, 2028).
# Source: U.S. Dept. of Education, "Fact Sheet: The Trump Administration Is
# Simplifying Student Loan Repayment" (ed.gov), corroborated by CRS In Focus
# IF13075. Figures below are administratively simplified, like this app's
# existing IDR model -- see the Methodology footer for the same caveat.
RAP_DEPENDENT_REDUCTION = 50  # $/month per dependent
RAP_MIN_PAYMENT = 10  # $/month floor for AGI <= $10,000
RAP_MAX_TERM_YEARS = 30  # forgiveness after 360 on-time payments
RAP_PRINCIPAL_MATCH_CAP = 50  # $/month government principal-match subsidy


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


def get_prestige_adjusted_major_name(major_name: str, tier_label: str) -> str:
    """Registers a synthetic MAJOR_DATA entry combining major_name's real
    BLS/curated salary data with tier_label's salary_multiplier (see
    COLLEGE_PRESTIGE_TIERS), under a synthetic key -- so every existing
    lookup (get_annual_salary_for_year, compute_scenario_results, the PDF/
    survey context, etc.) keeps reading MAJOR_DATA[major_name] completely
    unmodified, with no new multiplier parameter to thread through every
    function. Returns major_name unchanged when the tier applies no
    multiplier (Tier 3's 1.00x baseline)."""
    multiplier = COLLEGE_PRESTIGE_TIERS[tier_label]["salary_multiplier"]
    if multiplier == 1.0:
        return major_name
    synthetic_name = f"{major_name} ({tier_label.split(':')[0]})"
    base = MAJOR_DATA[major_name]
    MAJOR_DATA[synthetic_name] = {
        **base,
        "starting_salary": base["starting_salary"] * multiplier,
        "median_salary": base["median_salary"] * multiplier,
    }
    return synthetic_name


def get_ai_exposure_for_major(major_name: str) -> dict:
    """AI_EXPOSURE_BY_SOC_GROUP entry for major_name's SOC major group, or a
    graceful "Unclassified" placeholder if this major/career isn't mapped to
    one -- never a fabricated guess."""
    soc_group = MAJOR_DATA[major_name].get("soc_major_group")
    return AI_EXPOSURE_BY_SOC_GROUP.get(soc_group, {
        "label": "Unclassified", "risk_level": "Unknown", "score": None,
        "rationale": "This major/career isn't mapped to a BLS occupation group in this dataset.",
    })


def get_lower_risk_alternative_major(major_name: str) -> str:
    """For a Medium/High AI-exposure major, the closest-starting-salary major
    in the currently loaded MAJOR_DATA whose SOC major group is Low risk --
    or None if the dataset has no Low-risk alternative, rather than
    inventing a plausible-sounding one that isn't actually in the data."""
    current_salary = MAJOR_DATA[major_name].get("starting_salary", 0)
    candidates = [
        (name, data) for name, data in MAJOR_DATA.items()
        if name != major_name
        and AI_EXPOSURE_BY_SOC_GROUP.get(data.get("soc_major_group"), {}).get("risk_level") == "Low"
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda item: abs(item[1].get("starting_salary", 0) - current_salary))[0]


# ---- 2b. Usage / Survey Logging (Supabase) -------------------------------
# Backed by a hosted Postgres table (via st-supabase-connection) instead of
# local CSVs, since Streamlit Community Cloud's filesystem is ephemeral --
# local files would be silently wiped on every sleep/restart, defeating the
# whole point of logging this data for the companion research paper.

@st.cache_resource
def get_supabase_connection():
    return st.connection("supabase_connection", type=SupabaseConnection)


def log_usage_event(action: str):
    """Insert a single usage event into the usage_logs table. Tolerates any
    connection/query failure (matching every other save_*/log_* helper in
    this file) -- this fires on every single session via the pageview log
    at the very top of the script, before anything else renders, so a
    Supabase hiccup here must never be allowed to take down the whole
    calculator for every visitor."""
    try:
        conn = get_supabase_connection()
        execute_query(
            conn.table("usage_logs").insert(
                [{"timestamp": now_local().isoformat(), "action": action}],
                count="None",
            ),
            ttl=0,
        )
    except Exception:
        pass


def save_survey_response(respondent_role: str, hs_graduation_year: str,
                          perception_change: str, feedback_text: str, context: dict) -> bool:
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
            "timestamp": now_local().isoformat(),
            "respondent_role": respondent_role,
            "hs_graduation_year": hs_graduation_year,
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


def build_scenario_context(major, loan_amount, interest_rate, repayment_strategy,
                            personal_contribution, school_name_a, inflation_rate_a,
                            grants_per_year_a, scenario_a, compare_mode=False,
                            major_b=None, loan_amount_b=None, interest_rate_b=None,
                            repayment_strategy_b=None, personal_contribution_b=None,
                            school_name_b=None, inflation_rate_b=None,
                            grants_per_year_b=None, scenario_b=None) -> dict:
    """Flat {column_name: value} dict of Scenario A's (and, when compare_mode
    is True, Scenario B's) inputs/outputs -- the exact shape both
    survey_responses and pdf_downloads store, so the "Submit Feedback" form
    and the "Download PDF Report" button always log identically-shaped rows
    regardless of which one triggered the save. starting_salary/dti_ratio
    are derived here (not passed in) since every caller needs the same
    formula: the occupation's raw baseline wage from MAJOR_DATA, and the
    loan amount literally divided by it -- no additional_training_debt."""
    starting_salary = MAJOR_DATA[major]["starting_salary"]
    dti_ratio = round(loan_amount / starting_salary, 4) if starting_salary else None
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

    # Scenario B / roi_pct_delta only exist when Compare Mode is on at
    # save-time -- they stay absent (NULL in the database) otherwise, since
    # there's no Scenario B to report.
    if compare_mode:
        starting_salary_b = MAJOR_DATA[major_b]["starting_salary"]
        dti_ratio_b = round(loan_amount_b / starting_salary_b, 4) if starting_salary_b else None
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

    return context


def build_share_params(career_data_source, major, city, school_name_a, in_state_a, career_stage_label,
                        coa_per_year_a, personal_contribution_per_year_a, grants_per_year_a,
                        interest_rate, repayment_strategy, compare_mode, major_b=None, school_name_b=None,
                        in_state_b=None, coa_per_year_b=None, personal_contribution_per_year_b=None,
                        grants_per_year_b=None, interest_rate_b=None, repayment_strategy_b=None) -> dict:
    """Every Scenario A (and, when compare_mode, Scenario B) input as a flat
    {query_param_name: value} dict of strings -- the exact shape
    get_shared_default() reads back on a fresh visit, so a "Share Scenario"
    link recreates every selection currently on screen, not just the ones
    Part A's plain defaults happen to cover.

    Takes the *resolved* school_name_a/b (not the raw search text) so a
    school with 2+ substring matches (e.g. every "University of Michigan"
    campus) shares the exact campus that was picked, not whichever one the
    disambiguation picker happens to default to on a fresh visit -- the
    resolved name is specific enough that searching it again resolves back
    to that single school directly, no picker shown."""
    params = {
        "career_source": career_data_source,
        "major": major,
        "city": city,
        "school": school_name_a,
        "in_state": "1" if in_state_a else "0",
        "stage": career_stage_label,
        "coa": str(coa_per_year_a),
        "pc": str(personal_contribution_per_year_a),
        "grants": str(grants_per_year_a),
        "rate": str(interest_rate),
        "strategy": repayment_strategy,
        "compare": "1" if compare_mode else "0",
    }
    if compare_mode:
        params.update({
            "major_b": major_b,
            "school_b": school_name_b,
            "in_state_b": "1" if in_state_b else "0",
            "coa_b": str(coa_per_year_b),
            "pc_b": str(personal_contribution_per_year_b),
            "grants_b": str(grants_per_year_b),
            "rate_b": str(interest_rate_b),
            "strategy_b": repayment_strategy_b,
        })
    return params


# The Clipboard API (navigator.clipboard.writeText) silently fails inside
# the sandboxed iframe components.html renders into -- Streamlit doesn't
# grant that iframe a "clipboard-write" Permissions-Policy, so it always
# rejects there (confirmed via a live browser test). document.execCommand
# ("copy") on a temporary textarea, run against window.parent.document
# (the iframe has allow-same-origin, so this is reachable), is the
# pre-Permissions-Policy fallback that still works in this sandboxed
# context -- try the modern API first in case a given deployment does
# allow it, then fall back.
COPY_URL_TO_CLIPBOARD_JS = """
<script>
(function() {
    const url = window.parent.location.href;
    function legacyCopy(text) {
        const doc = window.parent.document;
        const textarea = doc.createElement("textarea");
        textarea.value = text;
        textarea.style.position = "fixed";
        textarea.style.left = "-9999px";
        doc.body.appendChild(textarea);
        textarea.focus();
        textarea.select();
        try { doc.execCommand("copy"); } catch (e) {}
        doc.body.removeChild(textarea);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).catch(() => legacyCopy(url));
    } else {
        legacyCopy(url);
    }
})();
</script>
"""


def save_pdf_download(context: dict) -> bool:
    """Insert one row into the pdf_downloads table -- same scenario-context
    shape as save_survey_response's `context` (see build_scenario_context),
    but with no respondent/demographic fields, since this isn't the feedback
    survey. Fired from st.download_button's on_click, which (like
    st.button) only runs on an actual click, not on every rerun. Returns
    True on success, False on any failure, matching save_survey_response's
    contract."""
    try:
        conn = get_supabase_connection()
        row = {"timestamp": now_local().isoformat(), **context}
        execute_query(
            conn.table("pdf_downloads").insert([row], count="None"),
            ttl=0,
        )
        return True
    except Exception:
        return False


def save_scenario_share(context: dict) -> bool:
    """Insert one row into the scenario_shares table -- same scenario-context
    shape as save_pdf_download/save_survey_response (see
    build_scenario_context). Fired when "Share Scenario" is clicked, right
    after the shareable URL is generated. Returns True on success, False on
    any failure, matching the other save_* helpers' contract."""
    try:
        conn = get_supabase_connection()
        row = {"timestamp": now_local().isoformat(), **context}
        execute_query(
            conn.table("scenario_shares").insert([row], count="None"),
            ttl=0,
        )
        return True
    except Exception:
        return False


def get_shared_default(param_name: str, fallback: str) -> str:
    """A Scenario field's value from the URL's query params, set by a
    previous "Share Scenario" click -- so opening a shared link recreates
    the exact same selections instead of landing on the plain defaults.
    Falls back to `fallback` on a normal, unshared visit. Query params are
    always strings; callers cast to int/float/bool themselves."""
    return st.query_params.get(param_name, fallback)


def get_shared_int(param_name: str, fallback: int) -> int:
    """Like get_shared_default, but safely cast to int -- a hand-edited,
    stale, or otherwise malformed shared link (e.g. ?pc=abc) falls back to
    `fallback` instead of raising an uncaught ValueError that would crash
    the page for every visitor on that URL."""
    try:
        return int(get_shared_default(param_name, str(fallback)))
    except (ValueError, TypeError):
        return fallback


def get_shared_float(param_name: str, fallback: float) -> float:
    """Like get_shared_int, but for float-valued params (e.g. ?rate=abc)."""
    try:
        return float(get_shared_default(param_name, str(fallback)))
    except (ValueError, TypeError):
        return fallback


def get_user_timezone() -> str:
    """The visitor's browser-detected IANA timezone (e.g. "America/Denver"),
    set via the hidden "Set Timezone" trigger + JS near the top of section 3.
    Falls back to UTC before that round-trip completes, or if a browser ever
    supplies something zoneinfo doesn't recognize."""
    return get_shared_default("tz", "UTC")


def now_local() -> datetime:
    """The current moment in the visitor's own local timezone, for anything
    a visitor actually sees or that gets logged as "when this happened"
    (usage/survey/PDF-download/scenario-share timestamps, the PDF footer) --
    the server's own clock (UTC on Streamlit Cloud) means nothing to a
    visitor reading a timestamp. Falls back to UTC for an invalid/unknown
    zone name rather than raising."""
    try:
        tz = ZoneInfo(get_user_timezone())
    except Exception:
        tz = timezone.utc
    return datetime.now(tz)


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
    falls back to a substring match. By the time this is called, school_name
    is already a disambiguated single name (see find_matching_schools /
    _resolve_school_name below), so the substring fallback only really
    matters for the 1-match case; it's kept as a safety net. Returns None
    if nothing matches -- expected for a school outside the local dataset."""
    if not school_name or coa_df.empty:
        return None
    names_lower = coa_df["INSTNM"].str.lower()
    query_lower = school_name.strip().lower()
    exact = coa_df[names_lower == query_lower]
    if not exact.empty:
        return exact.iloc[0]
    partial = coa_df[names_lower.str.contains(query_lower, regex=False)]
    return partial.iloc[0] if not partial.empty else None


# Common abbreviations that don't literally appear inside the real College
# Scorecard institution name they refer to -- verified against the actual
# dataset, not guessed. E.g. "UCLA"/"MIT"/"NYU" have zero substring matches
# at all; "USC" and "MIT" as plain substrings wrongly match unrelated
# schools instead (e.g. "USC" matches "Tuscarawas", "MIT" matches "Paul
# Mitchell the School"). Checked as a whole-string match on the query
# (after stripping/lowercasing) and expanded before the substring search
# in find_matching_schools below.
SCHOOL_NAME_ALIASES = {
    "ucla": "university of california-los angeles",
    "ucsd": "university of california-san diego",
    "ucsb": "university of california-santa barbara",
    "ucsc": "university of california-santa cruz",
    "virginia tech": "virginia polytechnic institute and state university",
    "georgia tech": "georgia institute of technology",
    "mit": "massachusetts institute of technology",
    "nyu": "new york university",
    "usc": "university of southern california",
    "byu": "brigham young university",
}

# Matches "UC <campus>" (e.g. "UC Berkeley", "UC San Diego") -- a single
# rule instead of one alias per campus, since every UC campus follows the
# same real-name pattern: "University of California-<Campus>".
_UC_CAMPUS_PATTERN = re.compile(r"^uc[\s-]+(\w[\w\s]*)$")


def _expand_school_query(school_name: str) -> str:
    """Expand a common abbreviation to a fragment of the school's real
    College Scorecard name (e.g. "UC Berkeley" -> "university of
    california-berkeley"), so it can be substring-matched like any other
    query. Returns the original (lowercased, stripped) query unchanged if
    it isn't a known abbreviation."""
    normalized = school_name.strip().lower()
    if normalized in SCHOOL_NAME_ALIASES:
        return SCHOOL_NAME_ALIASES[normalized]
    uc_match = _UC_CAMPUS_PATTERN.match(normalized)
    if uc_match:
        return f"university of california-{uc_match.group(1).strip()}"
    return normalized


def find_matching_schools(school_name: str, coa_df: pd.DataFrame, limit: int = 25) -> list:
    """Every institution name containing school_name (case-insensitive
    substring) -- or, when the query is a known abbreviation (see
    _expand_school_query), every name containing its expanded form
    instead. E.g. "UC Berkeley" resolves to the real "University of
    California-Berkeley" even though that abbreviation never appears in
    the official name. A known abbreviation deliberately skips the plain
    substring search rather than adding to it -- some acronyms are
    literal substrings of unrelated schools (e.g. "MIT" also matches
    every "Paul Mitchell the School" campus, "USC" matches "Tuscarawas"),
    and trusting the expansion avoids burying the real match in that
    noise. Sorted alphabetically and capped at `limit`. Used so a search
    like "University of California" surfaces all 9+ real UC campuses for
    the user to pick from, instead of find_school_coa silently guessing
    one arbitrary match. Returns [] for an empty query or an empty
    dataset."""
    if not school_name or coa_df.empty:
        return []
    query_lower = school_name.strip().lower()
    expanded_query = _expand_school_query(school_name)
    names_lower = coa_df["INSTNM"].str.lower()
    search_term = expanded_query if expanded_query != query_lower else query_lower
    matches = coa_df.loc[names_lower.str.contains(search_term, regex=False), "INSTNM"]
    return sorted(matches.unique())[:limit]


def _resolve_school_name(search_key: str, pick_key: str) -> str:
    """The effectively-selected school right now: the picker's current
    choice if the search text matched 2+ schools (a picker is showing),
    the single match if there's exactly one, or the raw search text
    otherwise (no match -- the student's free-typed entry, used as-is)."""
    search_text = st.session_state.get(search_key, "")
    matches = find_matching_schools(search_text, load_coa_dataset())
    if len(matches) >= 2:
        return st.session_state.get(pick_key, matches[0])
    if len(matches) == 1:
        return matches[0]
    return search_text


def get_suggested_coa_per_year(school_name: str, in_state: bool):
    """Cost of Attendance (in-state or out-of-state, per `in_state`) for a
    school in the local COA dataset, for auto-filling a scenario's per-year
    cost -- or None if the school has no match in the dataset."""
    match = find_school_coa(school_name, load_coa_dataset())
    if match is None:
        return None
    return float(match["in_state_coa"] if in_state else match["out_of_state_coa"])


def _autofill_coa(search_key: str, pick_key: str, in_state_key: str, coa_key: str):
    """on_change callback for the school search text_input, its disambiguation
    picker (when the search matched 2+ schools), or the In-State checkbox:
    resolves whichever school is effectively selected right now (see
    _resolve_school_name) and suggests a per-year Cost of Attendance into
    the paired number_input's session_state key when it matches the local
    COA dataset. A no-match (or the field being cleared) is a no-op -- it
    never resets a manually-entered COA estimate just because the lookup
    came up empty. Must write to st.session_state directly (not return a
    value) since callbacks run before the script reruns, and a
    number_input's value= argument only sets its first-render default, not
    later reruns, once it has a key."""
    resolved_school_name = _resolve_school_name(search_key, pick_key)
    suggested = get_suggested_coa_per_year(
        resolved_school_name, st.session_state.get(in_state_key, False),
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


# ---- 2e-2. Financial Math: 2026 Regulatory Forecasting (RAP & Tiered Standard) --
# Real, enacted federal law -- see the RAP_* constants' comment (section 1)
# for sourcing. Modeled with the same "administratively simplified, not an
# exact copy of federal rules" scope as the existing IDR model above.

def calculate_tiered_standard_term(principal: float) -> int:
    """2026 Tiered Standard Plan: fixed repayment term by loan balance."""
    if principal < 25000:
        return 10
    if principal < 50000:
        return 15
    if principal < 100000:
        return 20
    return 25


def calculate_rap_payment(agi: float, dependents: int = 0) -> dict:
    """One month's Repayment Assistance Plan (RAP) payment: a flat $10/month
    floor for AGI <= $10,000, otherwise 1% of AGI per $10,000 AGI band above
    $10,000 (so $10k-20k -> 1%, $20k-30k -> 2%, ... $90k-100k -> 9%), capped
    at 10% for AGI >= $100,000 -- then reduced by $50/month per dependent,
    floored at $0."""
    if agi <= 10000:
        base_payment = float(RAP_MIN_PAYMENT)
        applied_pct = None
    else:
        band = min(int(agi // 10000), 10)
        applied_pct = band / 100
        base_payment = agi * applied_pct / 12
    payment = max(base_payment - dependents * RAP_DEPENDENT_REDUCTION, 0.0)
    return {"monthly_payment": payment, "applied_pct": applied_pct, "base_payment": base_payment}


def simulate_rap_schedule(principal: float, annual_rate_pct: float, major_name: str,
                           dependents: int = 0, max_term_years: int = RAP_MAX_TERM_YEARS) -> dict:
    """Year-by-year RAP amortization: payment = calculate_rap_payment against
    that year's real salary (get_annual_salary_for_year), with RAP's real
    interest-waiver + up to $50/month government principal-match provisions
    applied whenever the borrower's own payment doesn't reduce principal by
    at least $50 that month -- so the balance never grows from unpaid
    interest. Any balance remaining after max_term_years (30 real years /
    360 payments) is forgiven."""
    monthly_rate = annual_rate_pct / 100 / 12
    balance = principal
    total_paid_in_roi_window = 0.0
    forgiven_amount = 0.0
    schedule_rows = []
    max_months = max_term_years * 12

    for month in range(1, max_months + 1):
        year_index = (month - 1) // 12
        agi = get_annual_salary_for_year(major_name, year_index)
        payment = calculate_rap_payment(agi, dependents)["monthly_payment"]
        interest = balance * monthly_rate
        principal_reduction = payment - interest
        if principal_reduction < RAP_PRINCIPAL_MATCH_CAP:
            principal_reduction = min(balance, RAP_PRINCIPAL_MATCH_CAP)
        balance = max(balance - principal_reduction, 0.0)
        if month <= ROI_WINDOW_YEARS * 12:
            total_paid_in_roi_window += payment
        schedule_rows.append({"month": month, "year": month / 12, "balance": balance})
        if balance <= 0:
            break
    else:
        forgiven_amount = balance
        balance = 0.0
        schedule_rows.append({"month": max_months, "year": max_months / 12, "balance": 0.0})

    schedule_df = pd.DataFrame(schedule_rows)
    return {
        "total_interest": 0.0,  # waived under RAP's real interest-subsidy provision
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
    fig.update_layout(yaxis_tickprefix="$", showlegend=False, title_x=0.5, title_xanchor="center")
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
    fig.update_layout(yaxis_tickprefix="$", showlegend=False, title_x=0.5, title_xanchor="center")
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


# ---- 2k. PDF Report Generation --------------------------------------------
# Reuses the same compute_scenario_results dicts as the on-screen views,
# laid out as tables/metrics with reportlab -- deliberately no chart images.
# An earlier version exported the same Plotly figures to PNG via kaleido,
# but kaleido's headless-Chromium rendering proved unstable in Streamlit
# Community Cloud's sandboxed container: kaleido>=1.0 needs a separate
# Chrome install that never happens there, and pinning kaleido==0.2.1 (which
# bundles its own Chromium) instead segfaulted the whole Streamlit process
# on the very first page load, taking the app down for every visitor, not
# just the PDF feature. Every number a chart would show is already in a
# table below it, so dropping the images trades a bit of visual polish for
# an actually-reliable deployment. No longer @st.cache_data'd either -- that
# existed only to avoid re-rendering kaleido chart images on every rerun,
# which no longer applies now that there are none; a table-only reportlab
# build is cheap, and skipping the cache keeps the footer's "Generated on"
# timestamp (below) accurate to the actual download instead of frozen at
# whenever this exact scenario was first cached.

# Streamlit Community Cloud doesn't expose the app's own public URL to
# server-side code, so this is hardcoded -- update it here if the app ever
# moves to a different URL/custom domain.
APP_URL = "https://studentloanroi.streamlit.app"


def _draw_pdf_header_footer(canvas, doc):
    """Page decoration for every page of every generated PDF: the app's URL
    in a header at the top, the generation date/time in a footer at the
    bottom -- passed to SimpleDocTemplate.build() as onFirstPage/
    onLaterPages, which reportlab calls once per page with the low-level
    canvas (flowables like _pdf_table can't draw outside their own frame,
    so headers/footers always go through this canvas-level hook instead)."""
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.grey)
    page_width, page_height = doc.pagesize
    canvas.drawString(doc.leftMargin, page_height - 30, APP_URL)
    canvas.drawRightString(
        page_width - doc.rightMargin, 30,
        f"Generated {now_local().strftime('%B %d, %Y at %I:%M %p %Z')}",
    )
    canvas.restoreState()


# reportlab's default font (Helvetica) has no emoji glyphs -- every emoji
# in a PDF heading renders as a solid black box, on every PDF, every time.
# Stripping them (rather than embedding an emoji-capable font, a much
# bigger lift for a cosmetic fix) keeps headings plain but legible.
_EMOJI_PATTERN = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U0000FE00-\U0000FE0F\U0000200D]+"  # variation selectors + zero-width joiner
)


def _strip_emoji(text: str) -> str:
    return _EMOJI_PATTERN.sub("", text).strip()


def _pdf_table(rows: list, header: bool = True) -> Table:
    """A simple bordered reportlab Table -- `header=True` bolds/shades row 0
    (tabular data with column headers), `header=False` bolds column 0
    instead (a plain key/value table, e.g. the profile summary)."""
    table = Table(rows, hAlign="LEFT")
    style = [
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f2f6")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ]
    else:
        style.append(("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"))
    table.setStyle(TableStyle(style))
    return table


def _pdf_profile_rows(major_name, school_name, in_state, coa_per_year,
                       personal_contribution_per_year, grants_per_year,
                       interest_rate_pct, repayment_strategy_label,
                       career_stage=None, city_name=None) -> list:
    rows = [
        ["Profession", major_name],
        ["School", school_name or "(not entered)"],
        ["In-State", "Yes" if in_state else "No"],
    ]
    if city_name is not None:
        rows.append(["City / Metro Area", city_name])
    if career_stage is not None:
        rows.append(["Career Stage Snapshot", career_stage])
    rows += [
        ["Cost of Attendance (per year)", fmt_money(coa_per_year)],
        ["Personal Contribution (per year)", fmt_money(personal_contribution_per_year)],
        ["Grants & Scholarships (per year)", fmt_money(grants_per_year)],
        ["Average Loan Interest Rate", fmt_pct(interest_rate_pct)],
        ["Repayment Strategy", repayment_strategy_label],
    ]
    return rows


def _pdf_module_sections(module_context: dict) -> list:
    """Optional PDF section(s) for whichever advanced modules were active --
    guarded per-module (see build_module_context) so a PDF generated with
    every module off is unchanged from before these modules existed."""
    if not module_context:
        return []
    styles = getSampleStyleSheet()
    elements = []
    if module_context.get("prestige_mode_active"):
        rows = [["Scenario", "Selected College Tier"], ["A", module_context.get("scenario_a_prestige_tier", "")]]
        if "scenario_b_prestige_tier" in module_context:
            rows.append(["B", module_context["scenario_b_prestige_tier"]])
        elements += [
            Spacer(1, 12), Paragraph("College Prestige & Cost Estimator", styles["Heading2"]),
            _pdf_table(rows),
        ]
    if module_context.get("ai_mode_active"):
        rows = [["Scenario", "AI Task Exposure Risk Level"], ["A", module_context.get("scenario_a_ai_risk_level", "")]]
        if "scenario_b_ai_risk_level" in module_context:
            rows.append(["B", module_context["scenario_b_ai_risk_level"]])
        elements += [
            Spacer(1, 12), Paragraph("AI Employability Risk Analysis", styles["Heading2"]),
            _pdf_table(rows),
        ]
    if module_context.get("future_forecasting_active"):
        rows = [["Scenario", "2026 Plan Selected"], ["A", module_context.get("future_plan_selected", "")]]
        if "scenario_b_future_plan_selected" in module_context:
            rows.append(["B", module_context["scenario_b_future_plan_selected"]])
        elements += [
            Spacer(1, 12), Paragraph("2026 Federal Loan Framework & Macro Forecasting", styles["Heading2"]),
            _pdf_table(rows),
        ]
    return elements


def generate_pdf_report_single(major, city, school_name_a, in_state_a, career_stage_label,
                                coa_per_year_a, personal_contribution_per_year_a, grants_per_year_a,
                                interest_rate, repayment_strategy, loan_amount, loan_schedule_a,
                                scenario, take_home, gross, disposable_nominal,
                                disposable_col_adjusted, module_context: dict = None) -> bytes:
    """PDF mirroring the on-screen single-scenario view: profile summary,
    Loan Information (+ per-year table), Real-World Take-Home, and
    10-Year Financial Position -- tables/metrics only, no chart images
    (see the section comment above for why)."""
    styles = getSampleStyleSheet()
    repayment_result = scenario["repayment_result"]
    roi_result = scenario["roi_result"]

    story = [
        Paragraph(_strip_emoji("🎓 Student Loan Payoff & Major ROI Report"), styles["Title"]),
        Paragraph(
            "Educational estimate tool — salary and cost figures are illustrative, not financial advice.",
            styles["Italic"],
        ),
        Spacer(1, 12),
        Paragraph("Your Profile", styles["Heading2"]),
        _pdf_table(
            _pdf_profile_rows(major, school_name_a, in_state_a, coa_per_year_a,
                               personal_contribution_per_year_a, grants_per_year_a,
                               interest_rate, repayment_strategy, career_stage_label, city),
            header=False,
        ),
        Spacer(1, 12),
        Paragraph(_strip_emoji(f"💳 Loan Information — {scenario['strategy_label']}"), styles["Heading2"]),
        _pdf_table([
            ["Year", "Cost of Attendance", "Loan Amount This Year"],
            *[[row["year"], fmt_money(row["coa"]), fmt_money(row["loan_amount"])] for row in loan_schedule_a],
        ]),
        Spacer(1, 6),
        Paragraph(f"Total Loan Amount (all {UNDERGRAD_YEARS} years): {fmt_money(loan_amount)}", styles["Normal"]),
        Spacer(1, 6),
        _pdf_table([
            ["Monthly Payment", "Payoff Timeline", "Total Interest Paid"],
            [
                fmt_money(repayment_result["monthly_payment"]) if "monthly_payment" in repayment_result else "Varies (IDR)",
                f"{repayment_result['payoff_years']:.1f} yrs",
                fmt_money(repayment_result["total_interest"]),
            ],
        ]),
        Spacer(1, 12),
        Paragraph(_strip_emoji(f"🏙️ Real-World Take-Home — {major}, {career_stage_label} in {city}"), styles["Heading2"]),
        _pdf_table([
            ["Gross Salary", "Take-Home Pay (annual)", "Monthly Disposable", "COL-Adjusted Disposable"],
            [fmt_money(gross), fmt_money(take_home["net_take_home"]),
             fmt_money(disposable_nominal), fmt_money(disposable_col_adjusted)],
        ]),
        Spacer(1, 12),
        Paragraph(_strip_emoji("📊 10-Year Financial Position"), styles["Heading2"]),
        _pdf_table([
            ["High School Grad — 10-Yr Net Position", f"{major} — 10-Yr Net Position", "Earnings Premium (COL-Adjusted)"],
            [fmt_money(roi_result["hs_net_position"]), fmt_money(roi_result["major_net_position"]),
             fmt_money(roi_result["earnings_premium"])],
        ]),
    ]
    story += _pdf_module_sections(module_context)

    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=letter).build(
        story, onFirstPage=_draw_pdf_header_footer, onLaterPages=_draw_pdf_header_footer,
    )
    return buffer.getvalue()


def _pdf_scenario_metrics_table(scenario: dict) -> Table:
    repayment_result = scenario["repayment_result"]
    roi_result = scenario["roi_result"]
    return _pdf_table([
        ["Monthly Payment", "Payoff Timeline", "Total Interest Paid", "10-Yr Earnings Premium (COL-Adj.)"],
        [
            fmt_money(repayment_result["monthly_payment"]) if "monthly_payment" in repayment_result else "Varies (IDR)",
            f"{repayment_result['payoff_years']:.1f} yrs",
            fmt_money(repayment_result["total_interest"]),
            fmt_money(roi_result["earnings_premium"]),
        ],
    ])


def generate_pdf_report_compare(city, major, school_name_a, in_state_a, coa_per_year_a,
                                 personal_contribution_per_year_a, grants_per_year_a, interest_rate,
                                 repayment_strategy, scenario_a, major_b, school_name_b, in_state_b,
                                 coa_per_year_b, personal_contribution_per_year_b, grants_per_year_b,
                                 interest_rate_b, repayment_strategy_b, scenario_b,
                                 module_context: dict = None) -> bytes:
    """PDF mirroring the on-screen Compare Mode view: both scenarios'
    profile summaries + metric tables (no chart images -- see the section
    comment above for why)."""
    styles = getSampleStyleSheet()
    story = [
        Paragraph(_strip_emoji("🎓 Student Loan Payoff & Major ROI Report — Scenario Comparison"), styles["Title"]),
        Paragraph(
            "Educational estimate tool — salary and cost figures are illustrative, not financial advice.",
            styles["Italic"],
        ),
        Spacer(1, 12),
        Paragraph(f"Scenario A: {scenario_a['major']} — {scenario_a['strategy_label']}", styles["Heading2"]),
        _pdf_table(
            _pdf_profile_rows(major, school_name_a, in_state_a, coa_per_year_a,
                               personal_contribution_per_year_a, grants_per_year_a,
                               interest_rate, repayment_strategy, city_name=city),
            header=False,
        ),
        Spacer(1, 6),
        _pdf_scenario_metrics_table(scenario_a),
        Spacer(1, 12),
        Paragraph(f"Scenario B: {scenario_b['major']} — {scenario_b['strategy_label']}", styles["Heading2"]),
        _pdf_table(
            _pdf_profile_rows(major_b, school_name_b, in_state_b, coa_per_year_b,
                               personal_contribution_per_year_b, grants_per_year_b,
                               interest_rate_b, repayment_strategy_b, city_name=city),
            header=False,
        ),
        Spacer(1, 6),
        _pdf_scenario_metrics_table(scenario_b),
    ]
    story += _pdf_module_sections(module_context)

    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=letter).build(
        story, onFirstPage=_draw_pdf_header_footer, onLaterPages=_draw_pdf_header_footer,
    )
    return buffer.getvalue()


# ============================================================
# 3. PAGE CONFIG & SESSION STATE
# ============================================================

st.set_page_config(page_title="Student Loan Payoff & Major ROI Calculator", page_icon="🎓", layout="wide")

# Detects the visitor's browser timezone (IANA name, e.g. "America/Los_Angeles")
# via get_user_timezone()/now_local() below, so logged timestamps and the PDF
# footer reflect the visitor's local time instead of the server's clock
# (UTC on Streamlit Cloud). Same hidden-button pattern as the admin-reveal
# trigger further down: a real (invisible) Streamlit button is the only way
# to get a rerun that picks up the newly-set query param, since changing the
# URL via JS alone doesn't notify the running Python session. The script
# re-checks on every rerun but only clicks the button when the detected
# timezone doesn't match what's already in the URL, so this settles after
# one extra rerun and never loops. The very first "pageview" log below still
# can't benefit -- there's no timezone to read until this round-trip
# completes -- so it's the one timestamp that may land in UTC regardless.
with st.container(key="tz_trigger_wrap"):
    st.button("Set Timezone", key="tz_trigger")
st.markdown(
    "<style>div.st-key-tz_trigger_wrap { display: none !important; }</style>",
    unsafe_allow_html=True,
)
components.html(
    """
    <script>
    (function() {
        function findTzButton() {
            const doc = window.parent.document;
            const buttons = doc.querySelectorAll("button");
            for (const b of buttons) {
                if (b.textContent.trim() === "Set Timezone") return b;
            }
            return null;
        }
        const detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
        const params = new URLSearchParams(window.parent.location.search);
        if (params.get("tz") !== detected) {
            params.set("tz", detected);
            const newUrl = window.parent.location.pathname + "?" + params.toString();
            window.parent.history.replaceState(null, "", newUrl);
            const btn = findTzButton();
            if (btn) btn.click();
        }
    })();
    </script>
    """,
    height=0,
)

# Log exactly one "pageview" per browser session. This check runs before any
# widgets are drawn, so later reruns triggered by moving a slider or opening
# an expander see "pageview_logged" already set and skip logging again.
if "pageview_logged" not in st.session_state:
    log_usage_event("pageview")
    st.session_state.pageview_logged = True

if "survey_submitted" not in st.session_state:
    st.session_state.survey_submitted = False

# Admin Analytics View starts hidden -- Ctrl+Shift+A reveals the checkbox
# that controls it (see the hidden trigger button + injected JS near the
# bottom of the sidebar), or visiting the app with ?admin=1 in the URL,
# for anyone whose OS/browser/extensions already claim that shortcut.
# Stays revealed for the rest of the session once triggered either way.
if "admin_revealed" not in st.session_state:
    st.session_state.admin_revealed = get_shared_default("admin", "0") == "1"


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
# No sidebar control for this -- nothing for a visitor to configure. Uses
# a personal key from secrets.toml if one's been set (COLLEGE_SCORECARD_API_KEY,
# top-level, not nested under [connections....] like the Supabase ones),
# falling back to the public "DEMO_KEY" otherwise -- DEMO_KEY's quota is
# shared across every app that uses it, not just this one, so a personal
# key (free, from https://api.data.gov/signup/) avoids that app's traffic
# silently degrading this one's COA-inflation estimates under load.
scorecard_api_key = st.secrets.get("COLLEGE_SCORECARD_API_KEY", "DEMO_KEY")

st.sidebar.subheader("💼 Career")

# Which BLS OEWS geographic release backs the career dropdown below --
# National (every state combined into one nationwide figure per occupation)
# or California (that state's own wages, which run higher for many careers,
# e.g. tech and healthcare). Affects every curated-major lookup too, since
# MAJOR_DATA is rebuilt from this choice on every rerun -- picking a source
# here is a data-source preference for the whole session, not per-scenario.
career_source_options = ["National", "California"]
shared_career_source = get_shared_default("career_source", "California")
default_career_source_index = (
    career_source_options.index(shared_career_source) if shared_career_source in career_source_options else 0
)
career_data_source = st.sidebar.radio(
    "Career Salary Data", career_source_options, index=default_career_source_index,
    help="National: nationwide BLS OEWS wage estimates (cleaned_careers.csv). "
         "California: that state's own BLS OEWS wage estimates "
         "(cleaned_careers_ca.csv), generated via `data_pipeline.py ... --state CA`.",
)
careers_csv_path = CAREERS_CSV_PATH_CA if career_data_source == "California" else CAREERS_CSV_PATH_NATIONAL
MAJOR_DATA = {**load_bls_careers(careers_csv_path), **CURATED_MAJOR_DATA}

# Defaults below assume a popular, concrete profile (Software Developer in
# San Francisco, in-state at UC Berkeley, 10 years in) instead of generic
# empty/first-alphabetical values, so there's something real on screen
# before a visitor touches anything -- see get_suggested_coa_per_year()
# usage further down for how Cost of Attendance's default is derived from
# the same school/in-state choice rather than a flat placeholder.
major_options = sorted(MAJOR_DATA.keys())
shared_major = get_shared_default("major", "Software Developers")
default_major_index = major_options.index(shared_major) if shared_major in major_options else (
    major_options.index("Software Developers") if "Software Developers" in major_options else 0
)
major = st.sidebar.selectbox(
    "Target Profession", major_options, index=default_major_index,
    help="Pick the career you're evaluating -- this determines the salary "
         "numbers used everywhere else in the app. There are hundreds of "
         "options, so instead of scrolling, click the box and type part of "
         "your major or career to jump straight to it.",
)

city_options = list(CITY_DATA.keys())
shared_city = get_shared_default("city", "San Francisco, CA")
default_city_index = city_options.index(shared_city) if shared_city in city_options else (
    city_options.index("San Francisco, CA") if "San Francisco, CA" in city_options else 0
)
city = st.sidebar.selectbox(
    "City / Metro Area", city_options, index=default_city_index,
    help="Where you plan to live and work after graduating. Adjusts your "
         "take-home pay and the 10-year comparison for how expensive that "
         "area is to live in.",
)
# Computed here (not just where it's first used, further down) so it's
# available for every compute_scenario_results() call in section 5 --
# including Compare Mode's, which run before the Real-World Take-Home
# section that used to be the only place this was computed.
city_info = CITY_DATA[city]

# Which point in this major's career the Real-World Take-Home section
# (5d) snapshots -- has no functional dependency on School/In-State or
# Financing below, so its position here is purely about profile layout
# (career-identity fields together), not calculation order.
career_stage_options = list(CAREER_STAGE_OPTIONS.keys())
shared_career_stage = get_shared_default("stage", "Mid-Career (Year 10)")
default_career_stage_index = career_stage_options.index(shared_career_stage) if shared_career_stage in career_stage_options else (
    career_stage_options.index("Mid-Career (Year 10)") if "Mid-Career (Year 10)" in career_stage_options else 0
)
career_stage_label = st.sidebar.radio(
    "Career Stage Snapshot", career_stage_options, index=default_career_stage_index,
    help="Preview your income right after graduating (Year 1) or 10 years "
         "into this career, in the Real-World Take-Home section below.",
)
career_stage_key = CAREER_STAGE_OPTIONS[career_stage_label]

# Three independent, optional modules -- each defaults off, and the app
# behaves exactly as it did before any of them existed when left off. See
# the Methodology footer for what each one models and, just as importantly,
# what it deliberately does NOT claim.
with st.sidebar.expander("🧪 Advanced Analysis Settings"):
    enable_prestige_mode = st.checkbox(
        "Enable College Prestige & Cost Estimator", value=False, key="enable_prestige_mode",
        help="Replace the manual school/Cost of Attendance fields below with "
             "a college-tier picker that also applies a modeled (not "
             "guaranteed) salary premium by tier -- see Methodology for "
             "sourcing and caveats.",
    )
    enable_ai_mode = st.checkbox(
        "Enable AI Employability Risk Analysis", value=False, key="enable_ai_mode",
        help="Show a modeled AI task-exposure estimate for your chosen "
             "major's occupation group, based on published research -- see "
             "Methodology.",
    )
    enable_future_proofing = st.checkbox(
        "Enable 2026 Regulatory & Macro Forecasting", value=False, key="enable_future_proofing",
        help="Preview the real 2026 federal repayment plans (Repayment "
             "Assistance Plan and Tiered Standard Plan) and a real "
             "cost-of-living comparison across cities -- see Methodology.",
    )
prestige_tier_a = None
prestige_tier_b = None

st.sidebar.subheader("💰 Financing")

if enable_prestige_mode:
    # College Prestige & Cost Estimator: replaces the school lookup with a
    # fixed-cost tier -- see COLLEGE_PRESTIGE_TIERS (section 1) for sourcing
    # of both the per-tier cost and the (deliberately conservative) salary
    # premium applied further below.
    prestige_tier_options = list(COLLEGE_PRESTIGE_TIERS.keys())
    shared_tier_a = get_shared_default("prestige_tier", prestige_tier_options[0])
    default_tier_a_index = (
        prestige_tier_options.index(shared_tier_a) if shared_tier_a in prestige_tier_options else 0
    )
    prestige_tier_a = st.sidebar.selectbox(
        "College Tier Selection", prestige_tier_options, index=default_tier_a_index, key="prestige_tier_a",
        help="A modeled college-tier cost + salary-premium estimate, in "
             "place of entering a specific school -- see Methodology for "
             "how the salary premium is sourced and why it's kept "
             "conservative.",
    )
    school_name_a = prestige_tier_a
    in_state_a = True
    coa_per_year_a = COLLEGE_PRESTIGE_TIERS[prestige_tier_a]["coa_per_year"]
    coa_match_a = None
    st.sidebar.caption(
        f"Annual Cost of Attendance for this tier: {fmt_money(coa_per_year_a)}".replace("$", r"\$")
    )
else:
    # School first: entering it immediately shows Cost of Attendance below, and
    # (if it matches the local dataset) auto-fills the per-year COA field --
    # everything else in this section builds on that number, which is why the
    # school/in-state choice lives here rather than up in Career. Many real
    # school names collide on a simple substring search (e.g. every
    # "University of California" campus), so a search matching 2+ schools
    # shows a picker instead of silently guessing which one was meant.
    school_search_a = st.sidebar.text_input(
        "Target Undergraduate School", placeholder="e.g. University of Michigan",
        value=get_shared_default("school", "UC Berkeley"), key="school_search_a",
        on_change=lambda: _autofill_coa("school_search_a", "school_pick_a", "in_state_a", "coa_per_year_a"),
        help="Type a school name to auto-fill Cost of Attendance below from "
             "real government data, if we have it on file. If your school "
             "isn't found, just enter Cost of Attendance yourself.",
    )
    matching_schools_a = find_matching_schools(school_search_a, load_coa_dataset())
    if len(matching_schools_a) >= 2:
        st.sidebar.selectbox(
            f"Multiple schools matched \"{school_search_a}\" -- pick yours:",
            matching_schools_a, key="school_pick_a",
            on_change=lambda: _autofill_coa("school_search_a", "school_pick_a", "in_state_a", "coa_per_year_a"),
        )
    school_name_a = _resolve_school_name("school_search_a", "school_pick_a")

    in_state_a = st.sidebar.checkbox(
        "In-State Student?", value=get_shared_default("in_state", "1") == "1", key="in_state_a",
        on_change=lambda: _autofill_coa("school_search_a", "school_pick_a", "in_state_a", "coa_per_year_a"),
        help="Check this if you'd pay in-state tuition at the school above. "
             "Changes the auto-filled Cost of Attendance and how fast tuition "
             "is estimated to grow each year.",
    )
    coa_match_a = find_school_coa(school_name_a, load_coa_dataset()) if school_name_a else None
    coa_caption_a = get_coa_confirmation_caption(school_name_a, coa_match_a, in_state_a)
    if coa_caption_a:
        st.sidebar.caption(coa_caption_a)

    shared_coa_a = get_shared_default("coa", None)
    default_coa_per_year_a = None
    if shared_coa_a is not None:
        # A shared link's explicit COA wins over auto-fill -- it may reflect a
        # manual override the original sharer typed in, not just whatever the
        # school+in-state lookup would recompute. A malformed value (e.g. a
        # hand-edited link) falls through to auto-fill below instead of
        # crashing the page.
        try:
            default_coa_per_year_a = float(shared_coa_a)
        except (ValueError, TypeError):
            pass
    if default_coa_per_year_a is None:
        default_coa_per_year_a = get_suggested_coa_per_year(school_name_a, in_state_a)
        if default_coa_per_year_a is None:
            default_coa_per_year_a = 7500
    # Seed session_state instead of passing value= directly: coa_per_year_a's
    # session_state can already be set by _autofill_coa's on_change callback
    # (fired from school_search_a/in_state_a) before this line ever runs, and
    # passing value= for a key that already has a session_state entry is
    # exactly the combination Streamlit's widget policy warns about. setdefault
    # is a no-op once anything -- the callback or a prior render -- has already
    # populated it, so this only ever supplies the very first render's default.
    st.session_state.setdefault("coa_per_year_a", int(default_coa_per_year_a))
    coa_per_year_a = st.sidebar.number_input(
        "Cost of Attendance (per year, $)", min_value=0, max_value=100000, step=500,
        key="coa_per_year_a",
        help="The full sticker price for one year at this school -- tuition, "
             "fees, room & board, books, everything -- before subtracting "
             "scholarships or what you pay yourself. Auto-fills if we found "
             "your school above.",
    )
personal_contribution_per_year_a = st.sidebar.number_input(
    "Personal Contribution (per year, $)", min_value=0, max_value=100000,
    value=get_shared_int("pc", 0), step=500,
    key="personal_contribution_per_year_a",
    help="Also called the Student Aid Index (SAI) -- the amount your family "
         "is expected to contribute. Savings or family money toward this "
         "year's cost that you did NOT borrow. The loan amount below is "
         "Cost of Attendance minus this and Grants & Scholarships -- "
         "counted in the ROI% denominator, but not added to the loan "
         "you're actually repaying (no interest accrues on it).",
)
grants_per_year_a = st.sidebar.number_input(
    "Grants & Scholarships (per year, $)", min_value=0, max_value=100000,
    value=get_shared_int("grants", 0), step=500,
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
inflation_rate_a = (
    DEFAULT_COA_INFLATION_RATE if enable_prestige_mode
    else estimate_coa_inflation_rate(school_name_a, scorecard_api_key, control_type_a)
)
loan_amount = compute_total_loan_amount(coa_per_year_a, personal_contribution_per_year_a,
                                         grants_per_year_a, inflation_rate_a)
personal_contribution = personal_contribution_per_year_a * UNDERGRAD_YEARS
st.sidebar.caption((
    f"Year 1: {fmt_money(coa_per_year_a)} COA − {fmt_money(personal_contribution_per_year_a)} personal "
    f"− {fmt_money(grants_per_year_a)} grants → est. {fmt_pct(inflation_rate_a * 100)} COA inflation/yr "
    f"→ over {UNDERGRAD_YEARS} years: **{fmt_money(loan_amount)}** loan, **{fmt_money(personal_contribution)}** personal"
).replace("$", r"\$"))
if enable_prestige_mode:
    # Apply the tier's salary premium to Scenario A's major -- see
    # get_prestige_adjusted_major_name for why this is a synthetic MAJOR_DATA
    # entry rather than a new parameter threaded through every calculation.
    major = get_prestige_adjusted_major_name(major, prestige_tier_a)
interest_rate = st.sidebar.number_input(
    "Average Loan Interest Rate (%)", min_value=0.0, max_value=20.0,
    value=get_shared_float("rate", 5.5), step=0.1,
    help="The interest rate on your student loan. 5.50% is a reasonable "
         "placeholder for recent federal undergraduate loan rates -- check "
         "your school's financial aid offer for your real rate.",
)
repayment_strategy_options = ["Standard 10-Year", "Income-Driven Repayment (IDR)"]
shared_repayment_strategy = get_shared_default("strategy", "Standard 10-Year")
default_repayment_strategy_index = (
    repayment_strategy_options.index(shared_repayment_strategy)
    if shared_repayment_strategy in repayment_strategy_options else 0
)
repayment_strategy = st.sidebar.selectbox(
    "Repayment Strategy", repayment_strategy_options, index=default_repayment_strategy_index,
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

# Compare Mode adds a second scenario (Scenario B) rather than hiding the
# widgets above -- those always represent Scenario A, in both modes. This
# means toggling Compare Mode on/off never loses a tuned value (there's
# only ever one copy of Scenario A's inputs) and the survey section below
# never needs to guess which scenario's context to save. Results below
# render live off whatever this (and every other sidebar input) is
# currently set to -- there's no Calculate/Compare button to click.
compare_mode = st.sidebar.checkbox(
    "🔀 Compare Two Scenarios", value=get_shared_default("compare", "0") == "1", key="compare_mode",
    help="Turn this on to compare two different majors, schools, or loan "
         "setups side by side instead of looking at just one.",
)

if compare_mode:
    with st.sidebar.expander("⚖️ Scenario B (for comparison)", expanded=True):
        shared_major_b = get_shared_default("major_b", "Humanities")
        default_major_b_index = major_options.index(shared_major_b) if shared_major_b in major_options else (
            major_options.index("Humanities") if "Humanities" in major_options else 0
        )
        major_b = st.selectbox(
            "Target Profession", major_options, index=default_major_b_index, key="major_b",
            help="Pick the career you're evaluating -- this determines the "
                 "salary numbers used everywhere else in the app. There are "
                 "hundreds of options, so instead of scrolling, click the "
                 "box and type part of your major or career to jump "
                 "straight to it.",
        )

        st.subheader("💰 Financing")
        if enable_prestige_mode:
            shared_tier_b = get_shared_default("prestige_tier_b", prestige_tier_options[0])
            default_tier_b_index = (
                prestige_tier_options.index(shared_tier_b) if shared_tier_b in prestige_tier_options else 0
            )
            prestige_tier_b = st.selectbox(
                "College Tier Selection", prestige_tier_options, index=default_tier_b_index, key="prestige_tier_b",
                help="A modeled college-tier cost + salary-premium estimate, "
                     "in place of entering a specific school -- see "
                     "Methodology for how the salary premium is sourced and "
                     "why it's kept conservative.",
            )
            school_name_b = prestige_tier_b
            in_state_b = True
            coa_per_year_b = COLLEGE_PRESTIGE_TIERS[prestige_tier_b]["coa_per_year"]
            coa_match_b = None
            st.caption(
                f"Annual Cost of Attendance for this tier: {fmt_money(coa_per_year_b)}".replace("$", r"\$")
            )
        else:
            school_search_b = st.text_input(
                "Target Undergraduate School", placeholder="e.g. Ohio State University",
                value=get_shared_default("school_b", "UC Berkeley"), key="school_search_b",
                on_change=lambda: _autofill_coa("school_search_b", "school_pick_b", "in_state_b", "coa_per_year_b"),
                help="Type a school name to auto-fill Cost of Attendance below "
                     "from real government data, if we have it on file. If "
                     "your school isn't found, just enter Cost of Attendance "
                     "yourself.",
            )
            matching_schools_b = find_matching_schools(school_search_b, load_coa_dataset())
            if len(matching_schools_b) >= 2:
                st.selectbox(
                    f"Multiple schools matched \"{school_search_b}\" -- pick yours:",
                    matching_schools_b, key="school_pick_b",
                    on_change=lambda: _autofill_coa("school_search_b", "school_pick_b", "in_state_b", "coa_per_year_b"),
                )
            school_name_b = _resolve_school_name("school_search_b", "school_pick_b")

            in_state_b = st.checkbox(
                "In-State Student?", value=get_shared_default("in_state_b", "1") == "1", key="in_state_b",
                on_change=lambda: _autofill_coa("school_search_b", "school_pick_b", "in_state_b", "coa_per_year_b"),
                help="Check this if you'd pay in-state tuition at the school "
                     "above. Changes the auto-filled Cost of Attendance and how "
                     "fast tuition is estimated to grow each year.",
            )
            coa_match_b = find_school_coa(school_name_b, load_coa_dataset()) if school_name_b else None
            coa_caption_b = get_coa_confirmation_caption(school_name_b, coa_match_b, in_state_b)
            if coa_caption_b:
                st.caption(coa_caption_b)

            shared_coa_b = get_shared_default("coa_b", None)
            default_coa_per_year_b = None
            if shared_coa_b is not None:
                try:
                    default_coa_per_year_b = float(shared_coa_b)
                except (ValueError, TypeError):
                    pass
            if default_coa_per_year_b is None:
                default_coa_per_year_b = get_suggested_coa_per_year(school_name_b, in_state_b)
                if default_coa_per_year_b is None:
                    default_coa_per_year_b = 7500
            st.session_state.setdefault("coa_per_year_b", int(default_coa_per_year_b))
            coa_per_year_b = st.number_input(
                "Cost of Attendance (per year, $)", min_value=0, max_value=100000, step=500,
                key="coa_per_year_b",
                help="The full sticker price for one year at this school -- "
                     "tuition, fees, room & board, books, everything -- before "
                     "subtracting scholarships or what you pay yourself. "
                     "Auto-fills if we found your school above.",
            )
        personal_contribution_per_year_b = st.number_input(
            "Personal Contribution (per year, $)", min_value=0, max_value=100000,
            value=get_shared_int("pc_b", 0), step=500,
            key="personal_contribution_per_year_b",
            help="Also called the Student Aid Index (SAI) -- the amount your "
                 "family is expected to contribute. Savings or family money "
                 "toward this year's cost that wasn't borrowed. The loan "
                 "amount below is Cost of Attendance minus this and Grants "
                 "& Scholarships.",
        )
        grants_per_year_b = st.number_input(
            "Grants & Scholarships (per year, $)", min_value=0, max_value=100000,
            value=get_shared_int("grants_b", 0), step=500,
            key="grants_per_year_b",
            help="Grant or scholarship aid that reduces what you need to "
                 "borrow. Not counted as part of your own investment for ROI "
                 "purposes -- it was never your money.",
        )
        control_type_b = coa_match_b["control_type"] if coa_match_b is not None else None
        inflation_rate_b = (
            DEFAULT_COA_INFLATION_RATE if enable_prestige_mode
            else estimate_coa_inflation_rate(school_name_b, scorecard_api_key, control_type_b)
        )
        loan_amount_b = compute_total_loan_amount(coa_per_year_b, personal_contribution_per_year_b,
                                                   grants_per_year_b, inflation_rate_b)
        personal_contribution_b = personal_contribution_per_year_b * UNDERGRAD_YEARS
        st.caption((
            f"Year 1: {fmt_money(coa_per_year_b)} COA − {fmt_money(personal_contribution_per_year_b)} personal "
            f"− {fmt_money(grants_per_year_b)} grants → est. {fmt_pct(inflation_rate_b * 100)} COA inflation/yr "
            f"→ over {UNDERGRAD_YEARS} years: **{fmt_money(loan_amount_b)}** loan, **{fmt_money(personal_contribution_b)}** personal"
        ).replace("$", r"\$"))
        if enable_prestige_mode:
            major_b = get_prestige_adjusted_major_name(major_b, prestige_tier_b)
        interest_rate_b = st.number_input(
            "Average Loan Interest Rate (%)", min_value=0.0, max_value=20.0,
            value=get_shared_float("rate_b", 5.5), step=0.1,
            key="interest_rate_b",
            help="The interest rate on your student loan. 5.50% is a "
                 "reasonable placeholder for recent federal undergraduate "
                 "loan rates -- check your school's financial aid offer "
                 "for your real rate.",
        )
        shared_repayment_strategy_b = get_shared_default("strategy_b", "Standard 10-Year")
        default_repayment_strategy_b_index = (
            repayment_strategy_options.index(shared_repayment_strategy_b)
            if shared_repayment_strategy_b in repayment_strategy_options else 0
        )
        repayment_strategy_b = st.selectbox(
            "Repayment Strategy", repayment_strategy_options, index=default_repayment_strategy_b_index,
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
st.info(
    "👈 **Update your profile in the sidebar** -- profession, school, loan terms, "
    "anything. Everything below updates instantly as you change it, no button to click."
)

# ---- 5a. Admin Analytics Dashboard (hidden behind sidebar checkbox) ------

if admin_enabled:
    st.subheader("📊 Admin Analytics Dashboard")

    usage_df = load_table_safe("usage_logs", columns=["timestamp", "action"])
    pdf_downloads_df = load_table_safe("pdf_downloads", columns=["timestamp"])
    scenario_shares_df = load_table_safe("scenario_shares", columns=["timestamp"])
    survey_df = load_table_safe("survey_responses", columns=["timestamp"])

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total App Interactions", len(usage_df))
    col2.metric("Total Survey Responses", len(survey_df))
    col3.metric("Total PDF Downloads", len(pdf_downloads_df))
    col4.metric("Total Scenario Shares", len(scenario_shares_df))

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


# Prestige Mode has no real school to look up (school_name_a/b hold a tier
# label, not a school name) -- skip the lookup entirely rather than firing a
# pointless College Scorecard API call for a nonsense query.
if not enable_prestige_mode:
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


def render_ai_risk_section(major_name: str, major_name_b: str = None) -> dict:
    """AI Employability Risk Analysis container (only rendered when
    enable_ai_mode is True). Returns the {column_name: value} fields for
    build_module_context -- see AI_EXPOSURE_BY_SOC_GROUP for sourcing."""
    st.subheader("🤖 Future Labor Market & AI Impact Analysis")
    st.caption(
        "Modeled at the occupation-group level from published AI-exposure "
        "research (Felten, Raj & Seamans; Eloundou et al. 2023), not a "
        "personalized prediction -- \"exposure\" measures task overlap with "
        "current AI tools, not certainty of job loss. See Methodology."
    )

    def _render_one(name):
        info = get_ai_exposure_for_major(name)
        st.markdown(f"**{name}** — {info['label']}")
        st.metric(
            "AI Task Exposure",
            f"{info['score']}/100" if info["score"] is not None else "N/A",
            info["risk_level"],
        )
        st.caption(info["rationale"])
        if info["risk_level"] in ("Medium", "High"):
            alt = get_lower_risk_alternative_major(name)
            st.info(
                f"Lower-exposure alternative in this dataset: **{alt}**" if alt
                else "No clear lower-exposure alternative found in the current dataset."
            )
        return info["risk_level"]

    if major_name_b:
        col_a, col_b = st.columns(2)
        with col_a:
            st.caption("Scenario A")
            risk_a = _render_one(major_name)
        with col_b:
            st.caption("Scenario B")
            risk_b = _render_one(major_name_b)
        return {"ai_mode_active": True, "scenario_a_ai_risk_level": risk_a, "scenario_b_ai_risk_level": risk_b}
    risk_a = _render_one(major_name)
    return {"ai_mode_active": True, "scenario_a_ai_risk_level": risk_a}


def render_future_proofing_section(scenario_a: dict, major_name_a: str, interest_rate_a: float,
                                    scenario_b: dict = None, major_name_b: str = None,
                                    interest_rate_b: float = None) -> dict:
    """2026 Federal Loan Framework & Macro Forecasting container (only
    rendered when enable_future_proofing is True). Returns the
    {column_name: value} fields for build_module_context. See the RAP_*
    constants and calculate_tiered_standard_term/calculate_rap_payment/
    simulate_rap_schedule (section 2e-2) for the real, cited mechanics
    behind these numbers."""
    st.subheader("⚖️ 2026 Federal Loan Framework & Macro Forecasting")
    st.caption(
        "Models the Repayment Assistance Plan (RAP) and Tiered Standard Plan "
        "created by the One Big Beautiful Bill Act (H.R. 1, 2025), effective "
        "for new federal loan borrowers July 1, 2026 -- see Methodology for "
        "sourcing and important caveats before relying on these numbers."
    )

    def _render_plan(scenario, major_name, interest_rate, key_suffix):
        effective_principal = scenario["effective_principal"]
        plan_options = ["2026 Tiered Standard Plan", "2026 Repayment Assistance Plan (RAP)"]
        future_plan = st.selectbox("2026 Repayment Plan", plan_options, key=f"future_plan_{key_suffix}")
        if future_plan == plan_options[0]:
            term_years = calculate_tiered_standard_term(effective_principal)
            result = calculate_standard_repayment(effective_principal, interest_rate, term_years)
            cols = st.columns(3)
            cols[0].metric("Fixed Term (by balance)", f"{term_years} yrs")
            cols[1].metric("Monthly Payment", fmt_money(result["monthly_payment"]))
            cols[2].metric("Total Interest Paid", fmt_money(result["total_interest"]))
        else:
            dependents = st.number_input(
                "Dependents", min_value=0, max_value=10, value=0, key=f"rap_dependents_{key_suffix}",
                help="Reduces your RAP payment by $50/month per dependent (real OBBBA provision).",
            )
            gross_year1 = get_annual_salary_for_year(major_name, 0)
            rap = calculate_rap_payment(gross_year1, dependents)
            result = simulate_rap_schedule(effective_principal, interest_rate, major_name, dependents)
            cols = st.columns(3)
            cols[0].metric("Monthly Payment (Year 1 income)", fmt_money(rap["monthly_payment"]))
            cols[1].metric("Payoff / Forgiveness Timeline", f"{result['payoff_years']:.1f} yrs")
            cols[2].metric("Forgiven After 30 Years", fmt_money(result["forgiven_amount"]))
            st.caption(
                "Under RAP, unpaid monthly interest is waived and the "
                "government matches up to $50/month toward your principal if "
                "your own payment doesn't cover that much -- so your balance "
                "never grows from unpaid interest (real OBBBA provisions)."
            )
        return future_plan

    if scenario_b is not None:
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown(f"**Scenario A: {major_name_a}**")
            plan_a = _render_plan(scenario_a, major_name_a, interest_rate_a, "a")
        with col_b:
            st.markdown(f"**Scenario B: {major_name_b}**")
            plan_b = _render_plan(scenario_b, major_name_b, interest_rate_b, "b")
        context = {
            "future_forecasting_active": True, "future_plan_selected": plan_a,
            "scenario_b_future_plan_selected": plan_b,
        }
        macro_major = major_name_a
    else:
        plan_a = _render_plan(scenario_a, major_name_a, interest_rate_a, "single")
        context = {"future_forecasting_active": True, "future_plan_selected": plan_a}
        macro_major = major_name_a

    st.divider()
    st.markdown("**Spatial Cost-of-Living Comparison**")
    st.caption(
        "Reuses this app's real per-city cost-of-living data (BEA Regional "
        "Price Parities, via CITY_DATA), not a flat percentage assumption, "
        "across a Low/Moderate/High sample."
    )
    sample_cities = ["Columbus, OH", "National Average", "San Francisco, CA"]
    gross = get_annual_salary_for_year(macro_major, 9)
    col_rows = []
    for c in sample_cities:
        info = CITY_DATA[c]
        take_home = calculate_take_home_pay(gross, info["state_key"], info["local_tax_rate"])
        disposable = adjust_for_cost_of_living(take_home["net_take_home"], info["col_index"])
        col_rows.append({
            "City": c, "CoL Index": info["col_index"],
            "COL-Adjusted Disposable Income (annual)": fmt_money(disposable),
        })
    st.dataframe(pd.DataFrame(col_rows), hide_index=True, use_container_width=True)

    st.divider()
    st.markdown("**Alternative Pathway: Trade Apprenticeship (Illustrative Benchmark)**")
    st.caption(
        "Illustrative benchmark based on typical U.S. Dept. of Labor "
        "registered apprenticeship reporting, not this app's per-major BLS "
        "data -- see Methodology."
    )
    apprentice_cols = st.columns(3)
    apprentice_cols[0].metric("Typical Total Debt", "$10,000")
    apprentice_cols[1].metric("Typical Starting Salary", "$52,000")
    apprentice_cols[2].metric("Typical Training Time", "1 year")

    return context


def build_module_context(prestige_tier_a=None, prestige_tier_b=None,
                          ai_context: dict = None, future_context: dict = None) -> dict:
    """Flat {column_name: value} dict of whichever optional advanced modules
    are active, in the same shape build_scenario_context already uses --
    merged into every save_survey_response/save_pdf_download/
    save_scenario_share call and the PDF's optional module sections."""
    context = {}
    if prestige_tier_a is not None:
        context["prestige_mode_active"] = True
        context["scenario_a_prestige_tier"] = prestige_tier_a
        if prestige_tier_b is not None:
            context["scenario_b_prestige_tier"] = prestige_tier_b
    if ai_context:
        context.update(ai_context)
    if future_context:
        context.update(future_context)
    return context


if compare_mode:
    st.subheader("⚖️ Scenario Comparison")
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

    ai_context = {}
    if enable_ai_mode:
        ai_context = render_ai_risk_section(major, major_b)

    future_context = {}
    if enable_future_proofing:
        future_context = render_future_proofing_section(scenario_a, major, interest_rate,
                                                          scenario_b, major_b, interest_rate_b)

    module_context = build_module_context(
        prestige_tier_a if enable_prestige_mode else None,
        prestige_tier_b if enable_prestige_mode else None,
        ai_context, future_context,
    )

    compare_pdf_bytes = generate_pdf_report_compare(
        city, major, school_name_a, in_state_a, coa_per_year_a, personal_contribution_per_year_a,
        grants_per_year_a, interest_rate, repayment_strategy, scenario_a,
        major_b, school_name_b, in_state_b, coa_per_year_b, personal_contribution_per_year_b,
        grants_per_year_b, interest_rate_b, repayment_strategy_b, scenario_b,
        module_context=module_context,
    )
    compare_pdf_col, compare_share_col = st.columns(2)
    compare_pdf_col.download_button(
        "📄 Download PDF Report", data=compare_pdf_bytes,
        file_name=f"{major.replace(' ', '_')}_vs_{major_b.replace(' ', '_')}_comparison_report.pdf",
        mime="application/pdf", use_container_width=True, key="download_pdf_compare",
        on_click=lambda: save_pdf_download({**build_scenario_context(
            major, loan_amount, interest_rate, repayment_strategy, personal_contribution,
            school_name_a, inflation_rate_a, grants_per_year_a, scenario_a,
            compare_mode=True, major_b=major_b, loan_amount_b=loan_amount_b,
            interest_rate_b=interest_rate_b, repayment_strategy_b=repayment_strategy_b,
            personal_contribution_b=personal_contribution_b, school_name_b=school_name_b,
            inflation_rate_b=inflation_rate_b, grants_per_year_b=grants_per_year_b,
            scenario_b=scenario_b,
        ), **module_context}),
    )
    if compare_share_col.button("🔗 Share Scenario", use_container_width=True, key="share_scenario_compare"):
        st.query_params.from_dict(build_share_params(
            career_data_source, major, city, school_name_a, in_state_a, career_stage_label,
            coa_per_year_a, personal_contribution_per_year_a, grants_per_year_a,
            interest_rate, repayment_strategy, True, major_b=major_b, school_name_b=school_name_b,
            in_state_b=in_state_b, coa_per_year_b=coa_per_year_b,
            personal_contribution_per_year_b=personal_contribution_per_year_b,
            grants_per_year_b=grants_per_year_b, interest_rate_b=interest_rate_b,
            repayment_strategy_b=repayment_strategy_b,
        ))
        save_scenario_share({**build_scenario_context(
            major, loan_amount, interest_rate, repayment_strategy, personal_contribution,
            school_name_a, inflation_rate_a, grants_per_year_a, scenario_a,
            compare_mode=True, major_b=major_b, loan_amount_b=loan_amount_b,
            interest_rate_b=interest_rate_b, repayment_strategy_b=repayment_strategy_b,
            personal_contribution_b=personal_contribution_b, school_name_b=school_name_b,
            inflation_rate_b=inflation_rate_b, grants_per_year_b=grants_per_year_b,
            scenario_b=scenario_b,
        ), **module_context})
        components.html(COPY_URL_TO_CLIPBOARD_JS, height=0)
        st.success("Shareable link copied to your clipboard! Paste it anywhere to share this exact comparison.")
else:
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

    st.subheader(f"🏙️ Real-World Take-Home — {major}, {career_stage_label} in {city}")

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
        help="How much more money you'd have after 10 years by going into this "
             "career instead of skipping college and working right away -- "
             "bigger is better. \"COL-Adjusted\" means we've factored in how "
             "expensive it is to live in your chosen city, so this is a fair "
             "comparison no matter where you live.",
    )

    st.plotly_chart(build_roi_bar_chart(roi_result["hs_net_position"], roi_result["major_net_position"], major), use_container_width=True)

    ai_context = {}
    if enable_ai_mode:
        ai_context = render_ai_risk_section(major)

    future_context = {}
    if enable_future_proofing:
        future_context = render_future_proofing_section(scenario, major, interest_rate)

    module_context = build_module_context(
        prestige_tier_a if enable_prestige_mode else None, None, ai_context, future_context,
    )

    single_pdf_bytes = generate_pdf_report_single(
        major, city, school_name_a, in_state_a, career_stage_label,
        coa_per_year_a, personal_contribution_per_year_a, grants_per_year_a,
        interest_rate, repayment_strategy, loan_amount, loan_schedule_a,
        scenario, take_home, gross, disposable_nominal, disposable_col_adjusted,
        module_context=module_context,
    )
    single_pdf_col, single_share_col = st.columns(2)
    single_pdf_col.download_button(
        "📄 Download PDF Report", data=single_pdf_bytes,
        file_name=f"{major.replace(' ', '_')}_payoff_report.pdf", mime="application/pdf",
        use_container_width=True, key="download_pdf_single",
        on_click=lambda: save_pdf_download({**build_scenario_context(
            major, loan_amount, interest_rate, repayment_strategy, personal_contribution,
            school_name_a, inflation_rate_a, grants_per_year_a, scenario,
        ), **module_context}),
    )
    if single_share_col.button("🔗 Share Scenario", use_container_width=True, key="share_scenario_single"):
        st.query_params.from_dict(build_share_params(
            career_data_source, major, city, school_name_a, in_state_a, career_stage_label,
            coa_per_year_a, personal_contribution_per_year_a, grants_per_year_a,
            interest_rate, repayment_strategy, False,
        ))
        save_scenario_share({**build_scenario_context(
            major, loan_amount, interest_rate, repayment_strategy, personal_contribution,
            school_name_a, inflation_rate_a, grants_per_year_a, scenario,
        ), **module_context})
        components.html(COPY_URL_TO_CLIPBOARD_JS, height=0)
        st.success("Shareable link copied to your clipboard! Paste it anywhere to share this exact scenario.")

st.divider()

# ---- 5e. Anonymous Impact Survey ------------------------------------------

if not st.session_state.survey_submitted:
    with st.form("survey_form", clear_on_submit=True):
        st.subheader("📋 Help Us Measure Impact")
        respondent_role = st.selectbox("I am a...", ["Parent", "Student", "Teacher", "Other"])
        hs_graduation_year = st.selectbox(
            "Expected High School Graduation Year", ["2027", "2028", "2029", "2030"],
        )
        perception_change = st.radio(
            "Did this tool change how you view your target major or university choice?",
            ["Yes - significantly", "Yes - slightly", "No - it confirmed my choice", "No - no impact"],
        )
        feedback_text = st.text_area("How did this data influence your thinking? (optional)")
        submitted = st.form_submit_button("Submit Feedback")

        if submitted:
            # Recomputed fresh (cheap, pure functions, no API calls) rather
            # than reused from st.session_state, so the survey reflects
            # exact click-time state.
            scenario_a = compute_scenario_results(major, loan_amount, interest_rate, repayment_strategy,
                                                   personal_contribution, city_info["col_index"])
            # major_b/loan_amount_b/etc. only exist as script variables when
            # compare_mode is on (they're assigned inside that sidebar
            # expander) -- referencing them outside an "if compare_mode:"
            # guard would raise NameError, so Scenario B's args are only
            # ever built when there's a Scenario B to build them from.
            compare_mode_kwargs = {}
            if compare_mode:
                scenario_b = compute_scenario_results(major_b, loan_amount_b, interest_rate_b, repayment_strategy_b,
                                                       personal_contribution_b, city_info["col_index"])
                compare_mode_kwargs = dict(
                    compare_mode=True, major_b=major_b, loan_amount_b=loan_amount_b,
                    interest_rate_b=interest_rate_b, repayment_strategy_b=repayment_strategy_b,
                    personal_contribution_b=personal_contribution_b, school_name_b=school_name_b,
                    inflation_rate_b=inflation_rate_b, grants_per_year_b=grants_per_year_b,
                    scenario_b=scenario_b,
                )
            context = {
                **build_scenario_context(
                    major, loan_amount, interest_rate, repayment_strategy, personal_contribution,
                    school_name_a, inflation_rate_a, grants_per_year_a, scenario_a,
                    **compare_mode_kwargs,
                ),
                **module_context,
            }

            saved = save_survey_response(respondent_role, hs_graduation_year, perception_change, feedback_text, context)
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
saves who's answering (Parent/Student/Teacher/Other) and an expected high
school graduation year (2027-2030), plus your exact inputs and results at
that moment: school, major, loan amount, personal contribution, interest
rate, repayment strategy, your
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

**Advanced Analysis Settings (optional, off by default).** Three extra
modules live in a sidebar expander. Each one is opt-in, and the calculator
behaves exactly as described above when all three are left off.

- **College Prestige & Cost Estimator.** Replaces the school lookup with a
  fixed cost-per-tier bucket (Elite Private, Top Public/Public Ivy, Standard
  Regional Public, Out-of-State Public/Mid-Tier Private). The *cost* side is
  just a sticker-price bucket. The *salary* side applies a modeled premium
  (Tier 1: 1.10x, Tier 2: 1.05x, Tier 4: 1.03x, Tier 3: 1.00x baseline) to
  your major's starting/median salary — and this number is genuinely
  contested in real research, so we've been deliberately conservative about
  it. Chetty et al. (Opportunity Insights, "Mobility Report Cards" /
  "Diversifying Society's Leaders?", [opportunityinsights.org](https://opportunityinsights.org/))
  find a real, observable earnings gap by college selectivity tier. But Dale
  & Krueger (NBER Working Paper 7322, 2002; 2011 update) found that gap
  shrinks toward zero once you control for the student's *own* ability and
  motivation — the kind of student admitted to and attending an Ivy-plus
  school likely would have earned close to the same wage regardless of where
  they went. These multipliers sit well below the raw observational gap
  Chetty et al. report, as a deliberate middle ground between the two
  findings. **This is a modeled estimate, not a causal claim about any
  specific school** — attending a "Tier 1" school does not guarantee this
  salary bump.
- **AI Employability Risk Analysis.** Rather than inventing a precise
  0-100 score for your specific major (which no one could actually back up),
  this models AI "task exposure" at the SOC *occupation group* level — the
  same real classification level published research on this topic actually
  uses. Sources: Felten, Raj & Seamans, "AI Occupational Exposure" ([NBER
  Working Paper 28959](https://www.nber.org/papers/w28959)), and Eloundou,
  Manning, Mishkin & Rock, "GPTs are GPTs" ([arXiv:2303.10130](https://arxiv.org/abs/2303.10130),
  2023). Both consistently find office/administrative-support and
  business/financial-operations tasks among the most exposed to current AI
  tools, and hands-on/in-person occupations (healthcare support, food
  service, construction, personal care, protective service) among the
  least. Risk Level and score are banded (Low≈20, Medium≈50, High≈80), not a
  unique number per major, to avoid implying false precision. **Important:
  "exposure" measures task overlap with current AI tools, not a prediction
  that a job will disappear** — high exposure often means parts of a job get
  AI-assisted, not that the whole job is automated. Any "lower-exposure
  alternative" suggested is picked from majors already in this app's own
  dataset by closest starting salary — never invented.
- **2026 Regulatory & Macro Forecasting.** Models two *real, enacted* federal
  loan repayment plans created by the One Big Beautiful Bill Act (H.R. 1,
  2025): the **Repayment Assistance Plan (RAP)** and the **Tiered Standard
  Plan**, both effective for new federal loan borrowers starting July 1,
  2026 (existing borrowers transition by July 1, 2028). Source: U.S. Dept.
  of Education, ["Fact Sheet: The Trump Administration Is Simplifying
  Student Loan Repayment"](https://www.ed.gov/about/news/press-release/fact-sheet-trump-administration-simplifying-student-loan-repayment),
  corroborated by Congressional Research Service In Focus IF13075. RAP
  payments are 1% of AGI per $10,000 AGI band (capped at 10% for AGI ≥
  $100,000, with a flat $10/month floor below $10,000 AGI), minus $50/month
  per dependent — with unpaid interest waived and up to a $50/month
  government principal-match, so your balance never grows from unpaid
  interest, and any remainder forgiven after 30 years. The Tiered Standard
  Plan is a fixed term of 10/15/20/25 years depending on loan balance. Like
  this app's existing IDR model, these are **administratively simplified,
  not an exact copy of federal rules** — confirm current terms at
  [studentaid.gov](https://studentaid.gov/) before relying on them for a
  real decision, since administrative details can still shift before the
  2026/2028 effective dates. The cost-of-living comparison in this section
  reuses this app's own real per-city data (BEA Regional Price Parities, see
  above) — not a separate, flat percentage assumption. The trade-apprenticeship
  benchmark card is a single illustrative reference point based on typical
  U.S. Dept. of Labor registered-apprenticeship reporting, not this app's
  per-major BLS pipeline.

*This tool produces educational estimates for a student research project,
not financial advice. Figures are national averages/percentiles and will not
reflect any individual's actual salary, cost of living, or loan terms.*
        """
    st.markdown(methodology_text.replace("$", r"\$"))

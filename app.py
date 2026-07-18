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

import hashlib
import io
import re
import uuid
from datetime import datetime, timezone
from xml.sax.saxutils import escape as xml_escape
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")  # must precede importing pyplot -- no display/browser needed
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd
import plotly.express as px
import requests
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from st_supabase_connection import SupabaseConnection, execute_query

# ============================================================
# 1. CONFIGURATION & CONSTANTS
# ============================================================

COLLEGE_SCORECARD_URL = "https://api.data.gov/ed/collegescorecard/v1/schools.json"

# Starting salary + mid-career (median) salary per major, sourced from the
# U.S. Bureau of Labor Statistics Occupational Employment and Wage Statistics
# (OEWS), May 2025 national estimates (bls.gov/oes/2025/may/). Each major is
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
    # Software Developers, SOC 15-1252: 25th pct $105,210 / median $135,980
    "Computer Science": {"starting_salary": 105210, "median_salary": 135980, "soc_major_group": "15"},
    # Registered Nurses, SOC 29-1141: 25th pct $80,330 / median $97,550
    "Nursing": {"starting_salary": 80330, "median_salary": 97550, "soc_major_group": "29"},
    # Business Operations Specialists, All Other, SOC 13-1199: 25th pct $62,640 / median $83,050
    "Business": {"starting_salary": 62640, "median_salary": 83050, "soc_major_group": "13"},
    # Financial and Investment Analysts, SOC 13-2051: 25th pct $79,290 / median $102,740
    "Finance": {"starting_salary": 79290, "median_salary": 102740, "soc_major_group": "13"},
    # Market Research Analysts and Marketing Specialists, SOC 13-1161: 25th pct $58,350 / median $78,760
    "Humanities": {"starting_salary": 58350, "median_salary": 78760, "soc_major_group": "13"},
    # Fine Artists, Including Painters, Sculptors, and Illustrators, SOC 27-1013: 25th pct $37,560 / median $55,490
    "Arts": {"starting_salary": 37560, "median_salary": 55490, "soc_major_group": "27"},
    # Coaches and Scouts, SOC 27-2022: 25th pct $35,330 / median $47,320
    "Sports Management": {"starting_salary": 35330, "median_salary": 47320, "soc_major_group": "27"},
    # Exercise Physiologists, SOC 29-1128: 25th pct $49,620 / median $59,460
    "Exercise Science": {"starting_salary": 49620, "median_salary": 59460, "soc_major_group": "29"},
    # Athletic Trainers, SOC 29-9091: 25th pct $55,130 / median $62,520. BLS
    # now lists a master's as the typical entry-level education, so this
    # major has a 2-year unpaid training delay (the accredited master's
    # program) before the salary above applies -- see get_annual_salary_for_year.
    "Athletic Training": {
        "starting_salary": 55130, "median_salary": 62520,
        "unpaid_training_years": 2, "soc_major_group": "29",
    },
    # Family Medicine Physicians, SOC 29-1215: 25th pct $162,420 / median
    # $244,180. 4 unpaid years (med school) + 3 stipend years (residency;
    # 3-year length matches Family Medicine's real ACGME program length, so
    # this pathway is internally consistent). Stipend is AAMC's 2024
    # preliminary median first-post-MD-year resident stipend ($65,100),
    # used as a flat representative figure across residency (real PGY2/PGY3
    # pay is a few thousand higher). additional_training_debt is AAMC's 2024
    # median medical school debt ($205,000, aamc.org/data-reports/students-
    # residents) -- added to the user's loan slider as the true principal.
    "Medicine": {
        "starting_salary": 162420, "median_salary": 244180,
        "unpaid_training_years": 4, "stipend_training_years": 3,
        "stipend_salary": 65000, "additional_training_debt": 205000,
        "soc_major_group": "29",
    },
    # Lawyers, SOC 23-1011: 25th pct $102,990 / median $159,670. 3 unpaid
    # years (law school, no paid-training equivalent). additional_training_
    # debt is the ABA Young Lawyers Division 2024 Student Loan Survey's
    # average law-school-only debt ($130,000, americanbar.org).
    "Law": {
        "starting_salary": 102990, "median_salary": 159670,
        "unpaid_training_years": 3, "additional_training_debt": 130000,
        "soc_major_group": "23",
    },
}

# Training structure for BLS occupations that can't be entered with a
# bachelor's, keyed by exact BLS occupation title.
#
# Why this exists: without it the app pays a Pediatric Surgeon their full
# $336,380 the year they finish undergrad, with no medical school and no
# medical-school debt -- and then, in the same dropdown, tells anyone who
# picks the curated "Medicine" above that they serve 4 unpaid years, 3 of
# residency and $205k. At a $190k loan over 10 years those two entries
# disagreed by $3.88M about the same life path, with nothing on screen to
# say which to believe. This applies Medicine's structure to the physician
# and dentist occupations BLS itself flags as needing a doctoral/
# professional degree.
#
# Only wages come from BLS; this overlay adds nothing but training fields,
# and build_major_data() merges the two. Deliberately NOT done by copying
# these occupations into CURATED_MAJOR_DATA, which would duplicate their
# salaries and let them drift from the CSV on the next BLS release.
#
# SIMPLIFICATIONS, deliberate and documented (the Methodology section
# repeats these -- keep them in sync):
#  - Residency length is modeled as a representative 3 years for every
#    physician specialty, exactly as the existing curated "Medicine" entry
#    does. Real ACGME residencies run 3-7 years (family medicine 3,
#    radiology 5, surgical specialties longer), so this UNDERSTATES the
#    training delay for the longer specialties and correspondingly
#    overstates their 10-year position. Fixing it properly needs each
#    specialty's own ACGME program requirements -- ACGME's published
#    "Levels of Training by Specialty" table defines PGY levels, not
#    program length, so there is no single citable table to read it from.
#  - Every physician gets AAMC's median medical school debt and resident
#    stipend regardless of specialty, since AAMC reports those across all
#    MD graduates rather than per specialty.
#  - Dentists are modeled with dental school's 4 years and no residency.
#    Prosthodontics and oral/maxillofacial surgery do require additional
#    residency, so those two are understated on the same basis as above.
#  - Nurse Anesthetists (SOC 29-1151) are a Master's-level occupation with
#    a different financing profile and no comparable association-published
#    debt median found, so they are deliberately NOT overlaid here and
#    remain a known gap.
ADVANCED_TRAINING_OVERLAY = {}

# Physicians: AAMC's 2024 median medical school debt ($205,000) and 2024
# preliminary median first-post-MD-year resident stipend ($65,100, used flat
# across residency), the same figures and structure the curated "Medicine"
# entry above already cites.
# aamc.org/data-reports/students-residents
_PHYSICIAN_TRAINING = {
    "unpaid_training_years": 4, "stipend_training_years": 3,
    "stipend_salary": 65000, "additional_training_debt": 205000,
}
for _title in [
    "Anesthesiologists", "Cardiologists", "Emergency Medicine Physicians",
    "Family Medicine Physicians", "Neurologists", "Obstetricians and Gynecologists",
    "Ophthalmologists, Except Pediatric", "Orthopedic Surgeons, Except Pediatric",
    "Pediatric Surgeons", "Physicians, Pathologists", "Psychiatrists", "Radiologists",
]:
    ADVANCED_TRAINING_OVERLAY[_title] = dict(_PHYSICIAN_TRAINING)

# Dentists: 4 years of dental school. Debt is the ADA/ADEA 2024 Survey of
# Dental School Seniors' average education debt among indebted graduates
# ($293,900) -- reported as a mean, unlike AAMC's median, and bimodal
# (public ~$260k vs private ~$321k), so it represents the middle of a wide
# spread rather than a typical individual.
# adea.org/home/publications/research-and-data/graduating-oral-health-students
_DENTIST_TRAINING = {
    "unpaid_training_years": 4, "additional_training_debt": 293900,
}
for _title in ["Oral and Maxillofacial Surgeons", "Prosthodontists", "Dentists, All Other Specialists"]:
    ADVANCED_TRAINING_OVERLAY[_title] = dict(_DENTIST_TRAINING)


# BLS OEWS-sourced careers from data_pipeline.py's output, in the same
# {major_name: {starting_salary, median_salary}} shape as the curated dict
# above, so every existing calculation (get_major_growth_rate,
# get_annual_salary_for_year, etc.) works on them identically -- no
# special-casing needed anywhere else in the app. Two geographic scopes are
# available (see the "Career Salary Data" sidebar selector in section 4,
# which picks one of these paths and builds the final MAJOR_DATA from it).
CAREERS_CSV_PATH_NATIONAL = "cleaned_careers.csv"
CAREERS_CSV_PATH_CA = "cleaned_careers_ca.csv"

# Per-MAJOR wages from the NY Fed, via nyfed_pipeline.py. The counterpart to
# the BLS per-OCCUPATION files above, and the basis of the sidebar's "Choose
# by: Major" mode -- see build_major_data.
MAJORS_CSV_PATH = "data/nyfed_majors_clean.csv"

# Per-METRO occupation wages, via `data_pipeline.py --metros`. Long-format:
# one row per (city, occupation). Lets Career mode answer what a Software
# Developer earns *in San Francisco* ($160,060) rather than nationally
# ($105,210) -- see build_major_data for why that matters.
METRO_CAREERS_CSV_PATH = "data/metro_careers_clean.csv"

# Each city's overall wage level vs the nation (BLS all-occupations median /
# the national $49,500), from `data_pipeline.py --metros`. Does two jobs
# neither the COL index nor the per-occupation metro file can:
#   - Scales the national high-school-graduate baseline. Without it, giving
#     the degree a San Francisco wage while the baseline stays national puts
#     SF's premium on one side of the scale only, and the degree looks better
#     purely for being in an expensive city.
#   - Localises Major mode, whose NY Fed wages are national with no per-city
#     equivalent. An all-occupations index suits a major's mixed-occupation
#     population, though it's a poor stand-in for a single occupation --
#     which is why Career mode uses real per-metro wages instead.
METRO_WAGE_INDEX_CSV_PATH = "data/metro_wage_index.csv"

# How many years separate starting_salary from median_salary in each dataset.
# BLS: the 25th-percentile-to-median reading this app has always used. NY Fed
# carries its own span per row (18), since its two figures are age-band
# medians rather than percentiles -- see get_major_growth_rate.
WAGE_GROWTH_SPAN_YEARS_BLS = 10

# The two things a visitor can pick from. Distinct datasets, deliberately not
# merged: see build_major_data.
DATASET_MODE_MAJOR = "Major"
DATASET_MODE_CAREER = "Career"

# The landing selection per mode, and Scenario B's counterpart -- the pairing
# the randomised contrast arm shows (see get_experiment_arm). Both sides are
# high-return-technical (Computer Science / Software Developers) against
# lower-return-exploratory (Journalism and its occupation), which is what the
# contrast manipulation requires. The two modes name the same field
# differently: "Journalism" is a NY Fed major, while the BLS occupation it
# maps to is "News Analysts, Reporters, and Journalists" -- Career mode has no
# bare "Journalism" row. Each string must exist in its own mode's dataset or
# the dropdown silently falls back to the alphabetically-first entry.
DEFAULT_SELECTION_A = {DATASET_MODE_MAJOR: "Computer Science",
                       DATASET_MODE_CAREER: "Software Developers"}
DEFAULT_SELECTION_B = {DATASET_MODE_MAJOR: "Journalism",
                       DATASET_MODE_CAREER: "News Analysts, Reporters, and Journalists"}

# What the dropdown is called in each mode. "Target Profession" is a lie in
# Major mode -- the visitor is picking what to study, not what to become.
SELECTION_LABEL = {DATASET_MODE_MAJOR: "Intended Major",
                   DATASET_MODE_CAREER: "Target Profession"}


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
            # BLS's own "Typical Education Needed for Entry" category for
            # this occupation (see add_education_field.py), e.g. "Bachelor's
            # degree" -- "" if the cleaned CSV predates this column or the
            # occupation had no match, so this never crashes on an
            # older/unregenerated CSV. See SUB_BACHELORS_EDUCATION_LEVELS.
            "typical_education": getattr(row, "typical_education", "") or "",
        }
        for row in careers_df.itertuples()
    }


@st.cache_data
def load_metro_wage_index(csv_path: str) -> dict:
    """{city: wage_index} from data_pipeline.py --metros. Missing file or
    unknown city means 1.0 -- i.e. national, no adjustment -- so a bad deploy
    degrades to the previous behaviour rather than to nonsense."""
    try:
        df = pd.read_csv(csv_path)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return {}
    return {row.city: float(row.wage_index) for row in df.itertuples()}


def get_metro_wage_index(city: str) -> float:
    """How far this city's overall wage level sits above or below the nation.
    1.0 for the national average, or for any city with no published index."""
    if not city or city == "National Average":
        return 1.0
    return load_metro_wage_index(METRO_WAGE_INDEX_CSV_PATH).get(city, 1.0)


@st.cache_data
def load_metro_wages(csv_path: str, city: str) -> dict:
    """One city's own BLS metro wages, as {occ_title: {starting_salary,
    median_salary}}, from data_pipeline.py --metros.

    Only the wage pair: everything else about an occupation (SOC group,
    typical education) is a property of the occupation, not of where it's
    done, so build_major_data overlays these onto the national entries
    rather than replacing them.

    Returns {} for a city with no metro file coverage, which callers must
    treat as "use national wages" -- see build_major_data.
    """
    try:
        df = pd.read_csv(csv_path)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return {}
    city_rows = df[df["city"] == city]
    return {
        row.occ_title: {"starting_salary": row.a_pct25, "median_salary": row.a_median}
        for row in city_rows.itertuples()
    }


@st.cache_data
def load_nyfed_majors(csv_path: str) -> dict:
    """Per-major wages/outcomes from nyfed_pipeline.py's output, in the same
    {name: {starting_salary, median_salary, ...}} shape load_bls_careers
    returns, so every downstream calculation works on them unchanged.

    Extra fields the BLS data has no equivalent for: wage_growth_span_years
    (18 rather than the BLS 10 -- see get_major_growth_rate), and the NY
    Fed's underemployment / unemployment / graduate-degree shares, which are
    per-major facts that simply don't exist per-occupation.

    No typical_education: every one of these is a bachelor's major by
    construction, which is why Major mode has no sub-baccalaureate problem to
    guard against (see breakeven_summary).
    """
    try:
        df = pd.read_csv(csv_path)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return {}
    return {
        row.major: {
            "starting_salary": row.starting_salary,
            "median_salary": row.median_salary,
            "wage_growth_span_years": row.wage_growth_span_years,
            "underemployment_rate": row.underemployment_rate,
            "unemployment_rate": row.unemployment_rate,
            "share_with_graduate_degree": row.share_with_graduate_degree,
        }
        for row in df.itertuples()
    }


def build_major_data(csv_path: str, mode: str = DATASET_MODE_CAREER, city: str = None) -> dict:
    """The app's full {major_name: {...}} dataset: BLS wages, overridden by
    the hand-curated entries, with training structure overlaid on the
    doctoral/professional occupations.

    Order matters. BLS first (wages for ~825 occupations), then
    CURATED_MAJOR_DATA (which fully replaces an entry, e.g. the synthetic
    "Medicine"), then ADVANCED_TRAINING_OVERLAY, which only *adds* training
    fields to whatever wages are already there -- so a BLS release changing
    a surgeon's salary flows straight through and the overlay never has to
    know about it.

    Lives here rather than being spelled out at the MAJOR_DATA assignment in
    section 4 because analyze_model.py builds the same dataset outside a
    Streamlit session; one function means the paper's numbers can't drift
    from the app's (the same reasoning as CLAUDE.md's chart-twin warning).

    mode selects which of two datasets the app is asking about, and they are
    deliberately NOT merged:

      Career (BLS): ~836 occupations. "What if I become a Software
        Developer?" Salaries are OEWS percentiles for people already doing
        the job.
      Major (NY Fed): 73 majors. "What if I study Computer Science?" Salaries
        are what people who studied it actually earn -- including the ones
        who ended up doing something else.

    Merging them is what the app did before, and it produced contradictions
    it had no way to resolve: the curated "Nursing" and the BLS "Registered
    Nurses" sat in one dropdown and disagreed about the same life path by 12x,
    with nothing on screen saying which to believe. Keeping the datasets
    apart makes that structurally impossible rather than merely unlikely.

    CURATED_MAJOR_DATA and the training overlay apply to Career mode only.
    Medicine and Law aren't undergraduate majors -- nobody majors in
    Medicine -- so their absence from Major mode is correct, not a gap. A
    prospective doctor picks Biology in Major mode (and sees that 64% of
    Biology majors go on to a graduate degree), or Family Medicine Physicians
    in Career mode (which models medical school properly).
    """
    if mode == DATASET_MODE_MAJOR:
        data = load_nyfed_majors(MAJORS_CSV_PATH)
        # The NY Fed publishes national figures only, so unlike Career mode
        # there are no real per-city wages to use -- an index is the only way
        # to localise them. It suits this data better than it would an
        # occupation: a major's salary describes people spread across many
        # jobs, which is the population an all-occupations index measures.
        # Still an estimate, and labelled as one on the page.
        index = get_metro_wage_index(city)
        if index != 1.0:
            data = {
                name: {**d,
                       "starting_salary": d["starting_salary"] * index,
                       "median_salary": d["median_salary"] * index,
                       "wage_geography": city, "wage_index": index}
                for name, d in data.items()
            }
        return data

    data = {**load_bls_careers(csv_path), **CURATED_MAJOR_DATA}

    # Real metro wages where BLS publishes them. Without this, a San
    # Francisco student was modelled on a NATIONAL wage divided by San
    # Francisco's cost index -- earning the country's average while paying
    # SF's prices, which made every expensive city a pure penalty. It isn't:
    # SF Software Developers start at $160,060 (1.52x national) against a
    # 1.18x cost index, so the premium beats the cost. The app said the
    # opposite.
    #
    # Wages only. Everything else (SOC group, typical education, the
    # training overlay below) is a property of the occupation, not of where
    # it's done.
    #
    # Occupations BLS suppresses for a metro (roughly 20% -- small
    # occupation-by-city cells) keep their national wage and are FLAGGED, so
    # the page can say which figure it's showing instead of passing a
    # national number off as local. Curated majors have no metro equivalent
    # and stay national by the same rule.
    if city:
        metro_wages = load_metro_wages(METRO_CAREERS_CSV_PATH, city)
        for occupation, wages in metro_wages.items():
            if occupation in data:
                data[occupation] = {**data[occupation], **wages, "wage_geography": city}

    for major_name, training_fields in ADVANCED_TRAINING_OVERLAY.items():
        if major_name in data:
            data[major_name] = {**data[major_name], **training_fields}
    return data

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

# Underemployment: the share of college graduates working in jobs that don't
# require a degree at all. From the Federal Reserve Bank of New York's "The
# Labor Market for Recent College Graduates" (updated February 4, 2026;
# sources: U.S. Census Bureau American Community Survey via IPUMS, and U.S.
# Department of Labor O*NET), covering 73 majors.
#
# Why this is here rather than in the model: every salary this app shows
# assumes the graduate actually works in the field they chose. Nationally
# that's true for only about 6 in 10 of them. The app was therefore making
# precisely the error it exists to correct -- section 1 of the companion
# paper argues students overestimate their own odds of landing above-median
# outcomes in their field, and then the calculator quietly assumed a 100%
# placement rate on their behalf.
#
# These are deliberately NOT applied to the salary math. Doing that would
# need a per-major rate, and this data is keyed by MAJOR (Psychology) while
# MAJOR_DATA is keyed by OCCUPATION (Clinical and Counseling Psychologists);
# only 3 of the 11 curated majors even match a NY Fed name exactly, and
# bridging the rest needs the NCES CIP-SOC crosswalk, whose own
# documentation calls it conceptual rather than empirical. So this is a
# disclosure of the model's assumption, at national scale, with the spread
# shown so the reader can see how much it varies -- not a fabricated
# per-occupation probability.
UNDEREMPLOYMENT_OVERALL_PCT = 39.35
UNDEREMPLOYMENT_MIN_PCT = 12.8
UNDEREMPLOYMENT_MIN_MAJOR = "Nursing"
UNDEREMPLOYMENT_MAX_PCT = 65.8
UNDEREMPLOYMENT_MAX_MAJOR = "Criminal Justice"
UNDEREMPLOYMENT_MAJOR_COUNT = 73
UNDEREMPLOYMENT_SOURCE_URL = "https://www.newyorkfed.org/research/college-labor-market"


def underemployment_disclosure(major_name: str = None, for_pdf: bool = False) -> str:
    """One sentence about underemployment, framed for whichever dataset is
    driving the page. Shared by the on-screen render and the PDF so the number
    and its framing can't drift.

    The two modes need genuinely different sentences, because the salaries
    mean different things:

    Career mode (BLS) shows what people already doing a job earn, so
    underemployment is an unmodelled risk sitting outside the figures — the
    honest move is to name the assumption and give the national spread,
    since this data is per-major and can't be mapped onto an occupation.

    Major mode (NY Fed) shows what everyone who studied that subject earns,
    underemployed or not. The rate is already inside the number, so quoting
    it as a warning would be wrong twice over: it's not an unmodelled risk,
    and it's this major's own published figure rather than a national one.

    for_pdf changes two things, both because the report is a static document
    read detached from the app:
      - markup is reportlab's <b>/<i>, not Streamlit's **/* -- the old code
        emitted markdown and stripped it with fragile string replaces, which
        left "*Major*" showing its asterisks in the PDF.
      - the Career-mode "Switch Choose by to Major" call-to-action is dropped:
        it tells the reader to click a sidebar toggle that doesn't exist on
        paper.
    """
    bold = (lambda t: f"<b>{t}</b>") if for_pdf else (lambda t: f"**{t}**")
    ital = (lambda t: f"<i>{t}</i>") if for_pdf else (lambda t: f"*{t}*")

    if major_name is not None:
        rate = MAJOR_DATA.get(major_name, {}).get("underemployment_rate")
        if rate is not None:
            return (
                f"These salaries already account for underemployment: "
                f"{bold(f'{rate:.0f}% of {major_name} graduates')} work in jobs that don't require "
                f"a degree, and they're included in the figures above rather than filtered out. "
                f"That's what makes this different from asking about a specific job — it's what "
                f"everyone who studied this actually earns."
            )

    base = (
        f"Every salary here assumes you work in the field you picked. Nationally, "
        f"{UNDEREMPLOYMENT_OVERALL_PCT:.0f}% of college graduates are {ital('underemployed')} — working a job "
        f"that doesn't require a degree — ranging from {UNDEREMPLOYMENT_MIN_PCT:.0f}% "
        f"({UNDEREMPLOYMENT_MIN_MAJOR}) to {UNDEREMPLOYMENT_MAX_PCT:.0f}% ({UNDEREMPLOYMENT_MAX_MAJOR}) "
        f"depending on major. This calculator assumes you're in the {100 - UNDEREMPLOYMENT_OVERALL_PCT:.0f}% "
        f"who aren't."
    )
    if not for_pdf:
        base += f" Switch {bold('Choose by')} to {ital('Major')} for figures that include them."
    return base

# Registered Apprenticeship benchmark for the "Alternative Pathway" card.
# Year-1 training wage ($52,000) and average starting salary upon
# completion ($86,000) bookend a two-phase illustrative wage curve: pay
# ramps from the training wage up to the completion salary via a constant
# growth rate across the typical training period (BLS notes apprentices
# "earn about half of what a fully qualified worker makes" early on,
# ramping up as they progress), then grows at this app's existing
# HS_GRAD_GROWTH_RATE after completion, since no BLS per-occupation
# trajectory exists past that point. Completion salary is
# apprenticeship.gov's own published "Did You Know?" statistic (footnoted
# there as sourced from Kansas Dept. of Commerce CRIS reporting -- not a
# national census figure, but DOL's own current national benchmark
# reference). Typical program length (~4 years, range 1-6) and the
# "apprentices are paid wages, not charged tuition" framing are both from
# BLS Career Outlook, "Apprenticeships: Outlook and wages in selected
# occupations" (2019).
APPRENTICESHIP_YEAR1_SALARY = 52000
APPRENTICESHIP_COMPLETION_SALARY = 86000
APPRENTICESHIP_TYPICAL_DEBT = 0
APPRENTICESHIP_TRAINING_YEARS = 4

# BLS Employment Projections' 8-category "Typical Education Needed for
# Entry" taxonomy (see add_education_field.py / bls.gov/oes/additional.htm)
# -- these five are below a bachelor's degree. Target Profession keeps
# every occupation regardless (removing real careers a student might be
# evaluating would be worse than disclosing the mismatch), but flags a
# disclosure and swaps the Alternative Pathway module's comparison data
# whenever the selected profession matches one of these, since this app's
# Cost of Attendance/loan model otherwise assumes a 4-year undergraduate
# program (UNDERGRAD_YEARS) for every major.
SUB_BACHELORS_EDUCATION_LEVELS = {
    "No formal educational credential", "High school diploma or equivalent",
    "Some college, no degree", "Postsecondary nondegree award", "Associate's degree",
}

# Income-Driven Repayment (IDR) assumptions, modeled after undergraduate
# REPAYE/SAVE-style plans: 10% of discretionary income, where discretionary
# income is pay above a flat living allowance, with unpaid balances forgiven
# after a fixed number of years if never fully repaid.
IDR_LIVING_ADJUSTMENT = 22000
IDR_PAYMENT_RATE = 0.10
IDR_MAX_TERM_YEARS = 20

STANDARD_TERM_YEARS = 10
ROI_WINDOW_YEARS = 10

# Horizons offered by the sidebar's ROI Horizon selector. ROI_WINDOW_YEARS
# stays the default (and every model function's default argument), so an
# unset horizon reproduces the original fixed-10-year behaviour exactly.
# 30 is the ceiling because it's where RAP forgiveness lands and past where
# BLS wage data can honestly support a projection.
ROI_HORIZON_OPTIONS = [10, 15, 20, 30]

# Assumed bachelor's degree length, for converting the per-year Cost of
# Attendance / Personal Contribution sidebar inputs into 4-year totals (the
# figures every downstream calculation -- effective_principal, ROI,
# take-home snapshot -- actually operates on). Distinct from
# STANDARD_TERM_YEARS (loan repayment term) and IDR_MAX_TERM_YEARS
# (forgiveness horizon) -- this is how long you're *enrolled*, not how long
# you're *repaying*.
UNDERGRAD_YEARS = 4

# Community-college-transfer path ("2+2"): a student spends the first
# COMMUNITY_COLLEGE_YEARS years at a community college, then transfers to the
# 4-year school to finish the SAME bachelor's. The degree, earnings, and the
# UNDERGRAD_YEARS enrollment timeline are unchanged -- only the first years'
# cost drops to community-college prices, cutting the loan. Costs below are the
# average annual IN-DISTRICT (in-state) tuition & fees at a public two-year
# (community) college -- NOT a full Cost of Attendance -- so the modeled saving
# reflects the tuition differential for a transfer student who lives at home
# during the community-college years, the common pattern. Source: National
# Center for Education Statistics (NCES), Digest of Education Statistics, via
# the Education Data Initiative (educationdata.org), 2025; national in-district
# average $3,890. Editable per scenario for a student who dorms or whose local
# college differs.
COMMUNITY_COLLEGE_YEARS = 2
COMMUNITY_COLLEGE_COA_DEFAULT = 3890  # national in-district avg (NCES 2025)

# Per-state average in-district community-college tuition & fees, keyed by
# 2-letter abbreviation to match STATE_TAX_BRACKETS and CITY_DATA["state_key"].
# Source: NCES Digest of Education Statistics via Education Data Initiative
# (educationdata.org, 2025). A state absent here falls back to
# COMMUNITY_COLLEGE_COA_DEFAULT.
COMMUNITY_COLLEGE_COST_BY_STATE = {
    "AL": 5440, "AK": 7140, "AZ": 2330, "AR": 3950, "CA": 1390, "CO": 3700,
    "CT": 5140, "DE": 5710, "FL": 2880, "GA": 3380, "HI": 3480, "ID": 3630,
    "IL": 4590, "IN": 5010, "IA": 6030, "KS": 3940, "KY": 4950, "LA": 4720,
    "ME": 4080, "MD": 4760, "MA": 6010, "MI": 4280, "MN": 6530, "MS": 3980,
    "MO": 4400, "MT": 4270, "NE": 3680, "NV": 3340, "NH": 7680, "NJ": 5380,
    "NM": 2080, "NY": 6210, "NC": 2730, "ND": 6060, "OH": 5000, "OK": 4770,
    "OR": 5810, "PA": 6170, "RI": 5500, "SC": 5380, "SD": 8000, "TN": 4790,
    "TX": 3160, "UT": 4600, "VT": 7470, "VA": 5640, "WA": 4990, "WV": 4970,
    "WI": 5030, "WY": 4530,
}

# 2-letter abbrev -> full name, for the Community College State dropdown. The
# app has no other US-state list; this is the canonical one.
US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}

# Helper: default annual CC cost for a state abbrev (None/unknown -> national).
def community_college_cost_for_state(state_key) -> int:
    if not state_key:
        return COMMUNITY_COLLEGE_COA_DEFAULT
    return COMMUNITY_COLLEGE_COST_BY_STATE.get(state_key, COMMUNITY_COLLEGE_COA_DEFAULT)

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

# Loan-to-income risk-tier thresholds for the "Student Loan Payment /
# Take-Home Ratio" metric (5d). Both real, commonly-cited guidelines are
# expressed as a percentage of GROSS income, not the net/take-home figure
# this app's ratio actually uses -- see get_loan_to_income_risk_tier for how
# they're converted onto a net basis using the scenario's own effective tax
# rate, rather than assuming a generic conversion factor.
# MANAGEABLE: student loan payments at or below 10% of gross monthly income
# are widely cited as leaving room for other budget priorities (e.g. SoFi,
# "What Percentage of Your Income Should Go to Student Loans?",
# sofi.com/learn/content/percentage-of-income-towards-student-loans/).
# CAUTION: 36% of gross income is the standard "back-end" total
# debt-to-income ceiling mortgage lenders use to consider a borrower
# well-qualified (Bankrate/CFPB-aligned guidance); above it is treated here
# as high-risk, since that's the point at which this loan payment ALONE
# already consumes the share of income normally budgeted for ALL debts
# combined.
LOAN_TO_INCOME_GROSS_MANAGEABLE_PCT = 10.0
LOAN_TO_INCOME_GROSS_CAUTION_PCT = 36.0

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


def render_centered_table(df: pd.DataFrame) -> None:
    """Renders df as a plain HTML table with every column -- including the
    header row -- center-aligned. st.dataframe's own column_config has an
    `alignment` option, but it only affects cell values, never the header
    text (confirmed visually: st.dataframe(..., column_config={col:
    st.column_config.Column(alignment="center")}) still left-aligns every
    header label), so it can't satisfy a "center the headers too" request.
    Plain HTML with inline CSS is the only way to control both. This loses
    st.dataframe's sort/hide-columns/download-as-CSV toolbar, an acceptable
    trade for these small, purely-for-display summary tables."""
    # Semi-transparent grey (not an opaque hex like #f0f2f6) so the header
    # tints whatever the real background is instead of hardcoding a
    # light-mode-only color -- an opaque light background combined with
    # dark mode's light inherited text color made the header text nearly
    # unreadable there.
    header_cells = "".join(
        f'<th style="padding:8px 12px; text-align:center; background:rgba(128,128,128,0.16); '
        f'border:1px solid rgba(128,128,128,0.4); font-weight:600;">{xml_escape(str(col))}</th>'
        for col in df.columns
    )
    body_rows = "".join(
        "<tr>" + "".join(
            f'<td style="padding:8px 12px; text-align:center; border:1px solid rgba(128,128,128,0.4);">'
            f'{xml_escape(str(value))}</td>'
            for value in row
        ) + "</tr>"
        for row in df.itertuples(index=False)
    )
    st.markdown(
        f'<table style="width:100%; border-collapse:collapse;">'
        f"<thead><tr>{header_cells}</tr></thead><tbody>{body_rows}</tbody></table>",
        unsafe_allow_html=True,
    )


def get_major_growth_rate(major_name: str) -> float:
    """CAGR from a major's starting_salary to its median_salary, over however
    many years actually separate those two figures (excludes any training
    delay -- see get_annual_salary_for_year).

    The span is per-entry rather than a constant because the two datasets
    measure different things. BLS OEWS gives a 25th-percentile and a median
    wage, which this app has always read as "entry level" and "~10 years in"
    -- so 10. The NY Fed's per-major data gives a median for ages 22-27 and
    one for 35-45; nyfed_pipeline.py back-extrapolates the first to year 0,
    leaving 18 years to the second. Applying 10 to that data would overstate
    every major's annual growth by ~55%, compounding over a 30-year horizon
    into a $226k rather than $161k year-30 salary for Computer Science.
    """
    data = MAJOR_DATA[major_name]
    span_years = data.get("wage_growth_span_years", WAGE_GROWTH_SPAN_YEARS_BLS)
    return (data["median_salary"] / data["starting_salary"]) ** (1 / span_years) - 1


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


def get_session_id() -> str:
    """A random id for this browser session, stamped on every row this file
    writes (usage_logs, survey_responses, pdf_downloads, scenario_shares).

    Without it those tables are four disconnected piles of rows: you can
    count PDF downloads and count survey responses, but never tell that a
    given response came from someone who had just downloaded one -- which is
    precisely the behavioral question the companion research paper asks. A
    shared id per session makes those joinable.

    Still anonymous, and deliberately so: this is a per-visit random UUID
    with nothing derived from the visitor (no IP, no fingerprint, no
    cookie). It cannot identify a person or link two separate visits by the
    same person -- a refresh starts a brand-new session with a brand-new id,
    since st.session_state doesn't survive it. It only links events *within*
    one visit, which is all the join needs.

    Lives in st.session_state rather than being regenerated per call, so
    every event in a session shares one id across Streamlit's rerun model.
    """
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    return st.session_state.session_id


def get_experiment_arm() -> str:
    """Which arm of the contrast-framing experiment this session is in:
    "contrast" (dual-scenario view on at load) or "single".

    The paper's secondary hypothesis asks whether side-by-side contrast
    framing moves perception beyond DTI disclosure alone. While the
    dual-scenario view was purely opt-in, that question was unanswerable at
    any sample size: visitors who enabled it were self-selected on
    engagement and prior uncertainty, so exposure to the manipulation was an
    outcome of the respondent's disposition rather than something assigned.
    The confound sat in the assignment mechanism, not in the noise. Randomly
    assigning the view at load is what converts that exploratory association
    into a confirmatory test.

    Derived by hashing the session id rather than drawing a random number,
    so it's stable across Streamlit's reruns without needing a separate
    persisted draw -- session_id is already a uuid4, so its hash is
    uniformly distributed and this is a fair coin, not a pattern.

    This sets the dual-scenario view's INITIAL state only. A visitor remains
    free to toggle it, which is why analysis must be intent-to-treat on this
    column rather than on whether the comparison was ultimately used --
    conditioning on the latter would reintroduce exactly the self-selection
    the randomization removes.
    """
    if "experiment_arm" not in st.session_state:
        digest = hashlib.sha256(get_session_id().encode()).hexdigest()
        st.session_state.experiment_arm = "contrast" if int(digest, 16) % 2 == 0 else "single"
    return st.session_state.experiment_arm


def log_horizon_change():
    """Fires when a visitor changes the ROI Horizon.

    The horizon is this app's most consequential control -- it's what turns
    medicine from $146k behind a high school graduate into $3.5M ahead -- and
    until now nothing recorded whether anyone touches it. scenario_events
    dedupes on the major/school signature, so a horizon change creates no
    row there; a visitor could sweep 10 to 30 years and leave no trace.

    Goes to usage_logs rather than a new table: that's an event stream keyed
    by `action` which has only ever carried "pageview", and this is exactly
    the kind of thing it exists for. No schema change.

    If nobody ever fires this, that is itself a finding about the interface,
    and one worth knowing before claiming the feature corrects anything.
    """
    log_usage_event(f"horizon_changed:{st.session_state.get('roi_horizon_select', '?')}")


def log_compare_toggle():
    """Fires when a visitor turns the dual-scenario view on or off.

    H2 is analysed intent-to-treat on the randomly assigned arm, and that
    doesn't change -- but reporting the COMPLIANCE rate is standard practice
    for a randomised trial, and a reviewer will ask for it. Assignment fixes
    only the initial view; visitors may toggle away from it.

    Without this, compliance is visible only for sessions that reach a commit
    point (survey/PDF/share), and only as a final state. Someone assigned to
    the contrast arm who switches it off and leaves is currently invisible --
    which is precisely the non-compliant case the rate is meant to capture.

    Records the arm alongside the new state, so a row is self-describing
    without a join.
    """
    state = "on" if st.session_state.get("compare_mode") else "off"
    log_usage_event(f"compare_toggled:{state}:arm={get_experiment_arm()}")


def get_traffic_source() -> str:
    """Where this visit came from, read from a ?src= tag on the URL and
    stamped on every row -- e.g. studentloanroi.streamlit.app/?src=jefferson_econ.

    Recruitment is the binding constraint on the companion research, and
    without this every visit is not merely anonymous but sourceless: if forty
    people arrive the week a counsellor forwards the link, nothing
    distinguishes them from a class visit, a newsletter, or the author's own
    testing. A tag per outreach channel makes "which recruitment actually
    worked" answerable, and lets self-testing be excluded from analysis.

    Still anonymous: this identifies a CHANNEL, chosen by whoever built the
    link, not a person. It carries nothing about the visitor, and a visitor
    who edits or drops it just becomes untagged.

    Returns None when absent, which is the normal case for organic traffic --
    NULL in the database rather than a fabricated "direct".
    """
    return get_shared_default("src", None)


def mark_major_explicitly_selected():
    """Record that the visitor chose the Target Profession themselves.

    Wired to that selectbox's on_change (section 4), which Streamlit fires
    only on a real interaction -- never on the initial render, and never on
    the reruns other widgets trigger. So this flips exactly once, the first
    time a visitor picks a major, and stays flipped for the session.
    """
    st.session_state.major_explicitly_selected = True


def get_major_explicitly_selected() -> bool:
    """Whether the major on screen is the visitor's own pick or the app's
    default, stamped on every row alongside session_id.

    The sidebar lands pre-filled with a concrete profile (Software Developers
    at UC Berkeley) so there are real numbers on screen immediately, which is
    the point of the tool. The cost is that a student whose intended
    profession genuinely is the default never touches the dropdown, and their
    session becomes indistinguishable from one where the visitor ignored the
    calculator entirely: both leave a single row reading "Software
    Developers". Without this flag the research cannot separate an answer
    from an absence, and every default-major row is uninterpretable.

    False does NOT mean the visitor disagreed with the default -- it means we
    don't know, and analysis should exclude those rows from anything that
    treats the major as a choice rather than drop them into the Software
    Developers bucket. Arriving via a share link with ?major= set also leaves
    this False: the major came from whoever built the link, which is equally
    not this visitor's pick.
    """
    return bool(st.session_state.get("major_explicitly_selected", False))


def log_usage_event(action: str):
    """Insert a single usage event into the usage_logs table. Tolerates any
    connection/query failure (matching every other save_*/log_* helper in
    this file) -- this fires on every single session via the pageview log
    at the very top of the script, before anything else renders, so a
    Supabase hiccup here must never be allowed to take down the whole
    calculator for every visitor."""
    if st.session_state.get("test_mode"):
        return  # ?test=1 developer session -- don't log interactions
    try:
        conn = get_supabase_connection()
        execute_query(
            conn.table("usage_logs").insert(
                [{"timestamp": now_local().isoformat(), "session_id": get_session_id(),
                  "traffic_source": get_traffic_source(), "action": action}],
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
    if st.session_state.get("test_mode"):
        return True  # ?test=1: show the normal success UX, but don't write
    try:
        conn = get_supabase_connection()
        row = {
            "timestamp": now_local().isoformat(),
            "session_id": get_session_id(),
            "traffic_source": get_traffic_source(),
            "experiment_arm": get_experiment_arm(),
            "major_explicitly_selected": get_major_explicitly_selected(),
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
                            grants_per_year_a, scenario_a, roi_horizon_years=ROI_WINDOW_YEARS,
                            compare_mode=False,
                            major_b=None, loan_amount_b=None, interest_rate_b=None,
                            repayment_strategy_b=None, personal_contribution_b=None,
                            school_name_b=None, inflation_rate_b=None,
                            grants_per_year_b=None, scenario_b=None,
                            start_year_a=None, start_year_b=None) -> dict:
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
        # Which dataset produced every salary-derived figure below. Without
        # it these columns pool two incompatible questions: a Major-mode row
        # is "what people who studied X earn" (underemployed included), a
        # Career-mode row is "what people doing X earn". For Computer
        # Science vs Software Developers that's a $526k difference in the
        # same column. Any analysis stratifying on ROI or salary must group
        # by this, exactly as it must for roi_horizon_years.
        "dataset_mode": dataset_mode,
        # The horizon every roi_pct/earnings_premium below was computed over.
        # Without it those columns aren't comparable across rows: a 30-year
        # ROI and a 10-year ROI are different quantities wearing the same
        # column name, and pooling them would be meaningless.
        "roi_horizon_years": roi_horizon_years,
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
        "scenario_a_start_year": start_year_a,
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
            "scenario_b_start_year": start_year_b,
        })
        roi_a = scenario_a["roi_result"]["roi_pct"]
        roi_b = scenario_b["roi_result"]["roi_pct"]
        context["roi_pct_delta"] = round(abs(roi_a - roi_b), 2) if roi_a is not None and roi_b is not None else None

    return context


def build_share_params(career_data_source, major, city, school_name_a, in_state_a, career_stage_label,
                        coa_per_year_a, personal_contribution_per_year_a, grants_per_year_a,
                        interest_rate, repayment_strategy, compare_mode, major_b=None, school_name_b=None,
                        in_state_b=None, coa_per_year_b=None, personal_contribution_per_year_b=None,
                        grants_per_year_b=None, interest_rate_b=None, repayment_strategy_b=None,
                        start_year_a=None, start_year_b=None, roi_horizon_years=None,
                        cc_mode_a="none", cc_state_a="__national__", cc_coa_per_year_a=None,
                        cc_mode_b="none", cc_state_b="__national__", cc_coa_per_year_b=None) -> dict:
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
        "mode": dataset_mode,
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
        "start_year": str(start_year_a),
        "horizon": str(roi_horizon_years),
        "cc_mode_a": cc_mode_a,
        "cc_state_a": cc_state_a,
        "cc_coa_a": str(cc_coa_per_year_a),
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
            "start_year_b": str(start_year_b),
            "cc_mode_b": cc_mode_b,
            "cc_state_b": cc_state_b,
            "cc_coa_b": str(cc_coa_per_year_b),
        })
    return params


# The Clipboard API (navigator.clipboard.writeText) silently fails inside
# the sandboxed iframe components.html renders into -- Streamlit doesn't
# grant that iframe a "clipboard-write" Permissions-Policy, so it always
# rejects there (confirmed via a live browser test). document.execCommand
# ("copy") on a temporary textarea, run against window.top.document
# (the iframe has allow-same-origin, so this is reachable), is the
# pre-Permissions-Policy fallback that still works in this sandboxed
# context -- try the modern API first in case a given deployment does
# allow it, then fall back. window.top, not window.parent: Streamlit
# Community Cloud nests this component inside an additional wrapping
# iframe, so window.parent only reaches that intermediate frame (with its
# own internal /~/+/ URL) instead of the real page -- window.top always
# reaches the actual outermost browsing context regardless of how many
# iframe layers exist in between (confirmed via a live browser test
# against the deployed app, where window.parent.location.href returned
# the wrapper iframe's own URL, not the page the visitor actually sees).
COPY_URL_TO_CLIPBOARD_JS = """
<script>
(function() {
    const url = window.top.location.href;
    function legacyCopy(text) {
        const doc = window.top.document;
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
    if st.session_state.get("test_mode"):
        return True  # ?test=1 developer session -- don't log interactions
    try:
        conn = get_supabase_connection()
        row = {"timestamp": now_local().isoformat(), "session_id": get_session_id(),
               "traffic_source": get_traffic_source(),
               "experiment_arm": get_experiment_arm(),
               "major_explicitly_selected": get_major_explicitly_selected(), **context}
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
    if st.session_state.get("test_mode"):
        return True  # ?test=1 developer session -- don't log interactions
    try:
        conn = get_supabase_connection()
        row = {"timestamp": now_local().isoformat(), "session_id": get_session_id(),
               "traffic_source": get_traffic_source(),
               "experiment_arm": get_experiment_arm(),
               "major_explicitly_selected": get_major_explicitly_selected(), **context}
        execute_query(
            conn.table("scenario_shares").insert([row], count="None"),
            ttl=0,
        )
        return True
    except Exception:
        return False


def maybe_log_scenario_event(context: dict) -> bool:
    """Log one row per distinct major/school selection this session lands on,
    into scenario_events -- the exploration path, not just the destination.

    Why this exists: every other table here records a scenario only at a
    commit point (survey submit, PDF download, share). If a visitor arrives
    set on pre-med, sees a 2.3x DTI, switches to nursing and downloads a
    report, those tables record one row saying "nursing" -- the switch, which
    is the actual behavioral finding, leaves no trace. Joined on session_id
    and ordered by event_seq, these rows reconstruct what a visitor tried and
    in what order, which is what makes a per-major switch rate computable.

    Fires on rerun, not on a click, because a major/school change *is* the
    event -- there's no button to hang it off. Streamlit reruns the whole
    script on every widget interaction, so this is called on each pass and
    dedupes against the last signature stored in session_state.

    The signature is deliberately only the major/school selections (A and B),
    not the whole scenario: those are the choices "switching" refers to, and
    keying on every field would insert a row per loan-slider tick -- adding
    network latency to a drag and drowning the switches in noise. The
    tradeoff is real: pure financing exploration (same major, different loan
    amount) is invisible here, showing up only in whatever the visitor
    eventually commits to. The rest of the scenario (loan, DTI, ROI) still
    rides along on every row, so each switch is timestamped against the
    numbers that were on screen when it happened.

    event_seq orders events within a session explicitly rather than relying
    on timestamps, which are taken from the visitor's own clock (now_local)
    and can tie or run backwards across a timezone round-trip.

    Returns True when a row was written, False when deduped or on any
    failure -- matching the other save_* helpers, a logging problem must
    never break the calculator.
    """
    if st.session_state.get("test_mode"):
        return False  # ?test=1 developer session -- don't log interactions
    signature = (
        context.get("scenario_a_major"), context.get("scenario_a_school_name"),
        context.get("scenario_b_major"), context.get("scenario_b_school_name"),
    )
    if st.session_state.get("last_scenario_signature") == signature:
        return False
    st.session_state.last_scenario_signature = signature
    seq = st.session_state.get("scenario_event_seq", 0) + 1
    st.session_state.scenario_event_seq = seq
    try:
        conn = get_supabase_connection()
        row = {
            "timestamp": now_local().isoformat(),
            "session_id": get_session_id(),
            "traffic_source": get_traffic_source(),
            "experiment_arm": get_experiment_arm(),
            "major_explicitly_selected": get_major_explicitly_selected(),
            "event_seq": seq,
            **context,
        }
        execute_query(
            conn.table("scenario_events").insert([row], count="None"),
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
                                  term_years: int = STANDARD_TERM_YEARS,
                                  roi_window_years: int = ROI_WINDOW_YEARS) -> dict:
    """
    Fixed-payment amortization: the classic loan formula where a constant
    monthly payment is split between interest (on the remaining balance)
    and principal, fully retiring the loan in exactly `term_years`.

    `term_years` and `roi_window_years` are unrelated tens and must not be
    conflated: term_years is how long the loan actually runs (the federal
    Standard plan's real 10-year term), while roi_window_years is how far
    the ROI comparison looks, which the visitor now chooses. Only
    total_paid_in_roi_window depends on the latter.
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
        "total_paid_in_roi_window": min(total_paid, monthly_payment * min(len(schedule_df), roi_window_years * 12)),
        "forgiven_amount": 0.0,
    }


# ---- 2e. Financial Math: Income-Driven Repayment --------------------------

def calculate_idr_repayment(principal: float, annual_rate_pct: float,
                             major_name: str,
                             living_adjustment: float = IDR_LIVING_ADJUSTMENT,
                             payment_rate: float = IDR_PAYMENT_RATE,
                             max_term_years: int = IDR_MAX_TERM_YEARS,
                             roi_window_years: int = ROI_WINDOW_YEARS) -> dict:
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
        if month <= roi_window_years * 12:
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
                           dependents: int = 0, max_term_years: int = RAP_MAX_TERM_YEARS,
                           roi_window_years: int = ROI_WINDOW_YEARS) -> dict:
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
        if month <= roi_window_years * 12:
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
                   years: int = ROI_WINDOW_YEARS,
                   hs_wage_index: float = 1.0,
                   personal_contribution: float = 0.0,
                   enrollment_years: int = 0,
                   working_years: int = 0) -> dict:
    """
    ROI = (major's cumulative earnings over `years`, minus loan payments made
    in that window, minus any personal_contribution) compared against a
    debt-free high school graduate's cumulative earnings over the same
    window. `total_investment` is the ROI%
    denominator -- not just the loan principal: it's effective_principal
    (loan slider + any additional training debt, see get_effective_principal)
    plus any personal_contribution the caller adds on top (money put toward
    the degree that wasn't borrowed, e.g. savings/scholarships/family
    contribution). This is deliberately a different figure from the
    principal actually fed into the loan repayment simulation -- you don't
    pay interest on money you never borrowed, but it's still part of what
    you "invested" for ROI purposes.

    personal_contribution is ALSO subtracted from the major's net position
    (the numerator), not just added to the denominator. Cash put toward the
    degree is a real outflow the debt-free HS grad never made -- exactly like
    a loan payment, only without the interest -- so it has to reduce the
    major's net position too. Leaving it in the denominator alone overstated
    the return: a degree paid for with $40k cash that earned a $40k premium
    reported +100% ROI (denominator only) when the true net gain was $0 (0%).

    enrollment_years is the in-enrollment opportunity cost: the years the
    degree-seeker spends in college earning ~nothing while the debt-free HS
    grad is already working. When it's 0 (the default) the clock starts at
    graduation for both sides -- the original behaviour. When it's >0 (the
    "count foregone earnings" Advanced-Analysis option passes UNDERGRAD_YEARS)
    the HS baseline earns for `years + enrollment_years` while the major still
    earns only `years` of post-graduation salary -- i.e. the HS grad is
    credited with the head-start wages banked during the degree-seeker's
    enrollment. This is a numerator effect only: it lowers the earnings
    premium (and pushes the break-even down), but total_investment stays
    out-of-pocket tuition/debt, so ROI% reads as "net gain per dollar of
    tuition, after netting out the wages given up to earn the degree." The
    foregone years are the biggest real cost of a degree and dwarf tuition,
    so leaving them out (enrollment_years=0) flatters every degree.

    working_years models the community-college "part-time while working
    full-time" path: for the first `working_years` of the enrollment window
    the degree-seeker is NOT foregoing earnings -- they work full-time at
    roughly a high-school-graduate wage while attending part-time -- so those
    years are added back to the major's side (same HS-wage formula, same front
    of the timeline as the baseline). Because the HS baseline earns the same
    wage those years, they cancel in the premium: the net effect is "no
    foregone penalty for the part-time years," which is exactly that path's
    advantage. working_years only bites when enrollment_years > 0 (the age-18
    timeline); with foregone earnings off, both are 0 and this is a no-op. It
    is loan-independent, so the break-even bisection stays valid.

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
    # The baseline has to live in the same city as the graduate it's being
    # compared to. HS_GRAD_SALARY is a national BLS figure, so a city that
    # pays its workers 1.49x the national rate must move the baseline too --
    # otherwise the metro wage premium lands on the degree's side of the
    # scale only, and expensive cities flatter the degree for no real reason.
    # hs_wage_index is that city's all-occupations wage level (see
    # get_metro_wage_index); 1.0 is the national average, i.e. a no-op.
    # range(years + enrollment_years): the HS grad also works the years the
    # degree-seeker spends enrolled (enrollment_years, 0 unless the foregone-
    # earnings option is on), so those head-start wages count against the
    # degree. Growth compounds from year 0, so the enrollment years correctly
    # sit at the *front* of the HS grad's raise trajectory.
    hs_cumulative_earnings = sum(
        HS_GRAD_SALARY * hs_wage_index * (1 + HS_GRAD_GROWTH_RATE) ** y
        for y in range(years + enrollment_years)
    )
    # Part-time-while-working community-college years: the major side earns a
    # HS-equivalent wage for the first `working_years` of the timeline (front,
    # growing from year 0 -- identical terms to the HS baseline's first
    # working_years, so they cancel in the premium). 0 unless the part-time CC
    # path is on AND the foregone-earnings option is on.
    major_working_earnings = sum(
        HS_GRAD_SALARY * hs_wage_index * (1 + HS_GRAD_GROWTH_RATE) ** y
        for y in range(working_years)
    )

    major_net_position_nominal = (
        major_cumulative_earnings + major_working_earnings
        - total_loan_payments_in_window - personal_contribution
    )
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


def get_apprenticeship_salary_for_year(year_index: int) -> float:
    """Two-phase illustrative wage curve for the Registered Apprenticeship
    benchmark -- ramps from the year-1 training wage to the completion
    salary via a constant growth rate across the training period (BLS:
    apprentices "earn about half of what a fully qualified worker makes"
    early on, ramping up as they progress), then grows at
    HS_GRAD_GROWTH_RATE after completion, since no BLS per-occupation
    trajectory exists past that point. See APPRENTICESHIP_* constants for
    sourcing."""
    training_periods = APPRENTICESHIP_TRAINING_YEARS - 1
    training_growth_rate = (
        (APPRENTICESHIP_COMPLETION_SALARY / APPRENTICESHIP_YEAR1_SALARY) ** (1 / training_periods) - 1
    )
    if year_index < training_periods:
        return APPRENTICESHIP_YEAR1_SALARY * (1 + training_growth_rate) ** year_index
    return APPRENTICESHIP_COMPLETION_SALARY * (1 + HS_GRAD_GROWTH_RATE) ** (year_index - training_periods)


def calculate_apprenticeship_roi(hs_net_position: float, col_index: float = 100.0,
                                  years: int = ROI_WINDOW_YEARS,
                                  enrollment_years: int = 0) -> dict:
    """Illustrative Registered Apprenticeship benchmark, computed the same
    way calculate_roi computes a major's ROI, but from the two-phase wage
    curve above instead of a MAJOR_DATA lookup. hs_net_position is passed
    in (not recomputed) so this always compares against the exact same
    HS-grad baseline already shown elsewhere on the page.

    enrollment_years mirrors calculate_roi's foregone-earnings option, but
    with the opposite sign for the trade path: an apprentice is *earning*
    (the ramping training wage) during the very years a degree-seeker is in
    college, so those years are added to the apprentice's own earning window
    rather than charged against it. When the option is on, the HS baseline
    passed in is already extended by the same enrollment_years, so all three
    paths -- degree, apprentice, HS grad -- are compared over one consistent
    span that starts at age 18 instead of at college graduation. With it off
    (0) the apprentice earns over `years`, the original behaviour."""
    apprentice_cumulative_earnings = sum(
        get_apprenticeship_salary_for_year(y) for y in range(years + enrollment_years)
    )
    apprentice_net_position_nominal = apprentice_cumulative_earnings - APPRENTICESHIP_TYPICAL_DEBT
    apprentice_net_position = adjust_for_cost_of_living(apprentice_net_position_nominal, col_index)
    earnings_premium = apprentice_net_position - hs_net_position
    roi_pct = (earnings_premium / APPRENTICESHIP_TYPICAL_DEBT * 100) if APPRENTICESHIP_TYPICAL_DEBT > 0 else None
    return {
        "apprentice_net_position": apprentice_net_position,
        "earnings_premium": earnings_premium,
        "roi_pct": roi_pct,
    }


def compute_loan_schedule_by_year(coa_per_year: float, personal_contribution_per_year: float,
                                   grants_per_year: float, inflation_rate: float,
                                   years: int = UNDERGRAD_YEARS,
                                   cc_years: int = 0, cc_coa_per_year: float = 0.0,
                                   finance_cc_years: bool = True) -> list:
    """Per-year loan breakdown across `years` of enrollment, growing Cost of
    Attendance year-over-year by inflation_rate while Personal Contribution
    and Grants & Scholarships both stay flat nominal amounts -- Year 1 uses
    coa_per_year as entered/auto-filled; each subsequent year compounds by
    (1 + inflation_rate). The loan gap widens each year since neither
    funding source scales with rising costs, matching how this plays out
    for most families/awards in practice. Returns one dict per year
    (1-indexed): {"year", "coa", "loan_amount", "phase"}. compute_total_loan_amount
    below just sums this -- kept separate so the results page can show the
    year-by-year build-up, not only the final total.

    cc_years/cc_coa_per_year model the community-college-transfer path: the
    first cc_years use cc_coa_per_year as the base cost (community-college
    prices) instead of coa_per_year, then the remaining years use coa_per_year
    (the 4-year school). Inflation still compounds from year 0 across the whole
    span, so a university year lands at coa_per_year*(1+r)**year_index -- i.e.
    the 4-year sticker has inflated by the time the student transfers into it.
    cc_years=0 (the default) is the original single-institution behaviour.

    finance_cc_years=False models the "no-loan community college" rule: the CC
    years contribute $0 to the loan (paid out of pocket / Pell / from wages),
    so only the university years are financed. The CC rows are still emitted
    with their `coa` (for the year-by-year display and for summing the CC
    out-of-pocket cost) -- they just carry loan_amount=0. Keeping the CC years
    in the loop (rather than dropping them) is what positions the university
    years at the correct inflated year_index."""
    schedule = []
    for year_index in range(years):
        is_cc = year_index < cc_years
        base = cc_coa_per_year if is_cc else coa_per_year
        coa_this_year = base * (1 + inflation_rate) ** year_index
        if is_cc and not finance_cc_years:
            loan_amount = 0.0
        else:
            loan_amount = max(coa_this_year - personal_contribution_per_year - grants_per_year, 0)
        schedule.append({"year": year_index + 1, "coa": coa_this_year, "loan_amount": loan_amount,
                         "phase": "community_college" if is_cc else "university"})
    return schedule


def compute_total_loan_amount(coa_per_year: float, personal_contribution_per_year: float,
                               grants_per_year: float, inflation_rate: float,
                               years: int = UNDERGRAD_YEARS,
                               cc_years: int = 0, cc_coa_per_year: float = 0.0,
                               finance_cc_years: bool = True) -> float:
    """Total loan across `years` of enrollment -- see
    compute_loan_schedule_by_year for the year-by-year math this sums (including
    the cc_years/cc_coa_per_year community-college-transfer path and the
    finance_cc_years no-loan-CC rule).
    Grants & Scholarships reduces the loan the same way Personal
    Contribution does, but -- unlike Personal Contribution -- is never
    added to total_investment (the ROI% denominator) in
    compute_scenario_results, since it's free third-party money, not
    something the student/family gave up."""
    schedule = compute_loan_schedule_by_year(coa_per_year, personal_contribution_per_year,
                                              grants_per_year, inflation_rate, years,
                                              cc_years=cc_years, cc_coa_per_year=cc_coa_per_year,
                                              finance_cc_years=finance_cc_years)
    return sum(row["loan_amount"] for row in schedule)


def compute_scenario_results(major_name: str, loan_amount: float,
                              interest_rate: float, repayment_strategy: str,
                              personal_contribution: float = 0.0,
                              col_index: float = 100.0,
                              roi_window_years: int = ROI_WINDOW_YEARS,
                              hs_wage_index: float = 1.0,
                              enrollment_years: int = 0,
                              working_years: int = 0) -> dict:
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

    roi_window_years (the visitor's chosen ROI Horizon) goes to BOTH the
    repayment simulation and calculate_roi, and they have to agree: the
    repayment side decides how many payments land inside the window, the ROI
    side decides how many years of earnings it counts. Pass it to only one
    and you get a silent mismatch -- e.g. 30 years of a doctor's earnings
    against 10 years of their payments -- that looks like a plausible number
    and isn't. Note the repayment *strategy* named "Standard 10-Year" is a
    real 10-year loan term and is unrelated to this window.
    """
    effective_principal = get_effective_principal(major_name, loan_amount)
    total_investment = effective_principal + personal_contribution
    if repayment_strategy == "Standard 10-Year":
        repayment_result = calculate_standard_repayment(
            effective_principal, interest_rate, roi_window_years=roi_window_years)
        strategy_label = "Standard 10-Year"
    else:
        repayment_result = calculate_idr_repayment(
            effective_principal, interest_rate, major_name, roi_window_years=roi_window_years)
        strategy_label = "Income-Driven Repayment"
    roi_result = calculate_roi(major_name, repayment_result["total_paid_in_roi_window"],
                                total_investment, col_index=col_index, years=roi_window_years,
                                hs_wage_index=hs_wage_index,
                                personal_contribution=personal_contribution,
                                enrollment_years=enrollment_years,
                                working_years=working_years)
    return {
        "major": major_name,
        "strategy_label": strategy_label,
        "effective_principal": effective_principal,
        "personal_contribution": personal_contribution,
        "total_investment": total_investment,
        # Stamp the enrollment-cost assumptions onto the scenario so every
        # re-derivation off this dict (break-even, apprenticeship, the PDF)
        # reuses the exact values it was computed under, rather than each call
        # site re-reading the toggle and risking a mismatch -- the same class
        # of bug the hs_wage_index threading fixed.
        "enrollment_years": enrollment_years,
        "working_years": working_years,
        "repayment_result": repayment_result,
        "roi_result": roi_result,
    }


# Bisection bounds for the break-even search, in dollars of undergrad loan.
# The upper bound sits far past any realistic undergraduate debt on purpose:
# a major whose break-even lies beyond it is reported as such rather than
# clipped to the bound, so an implausible number never reads as a real one.
BREAKEVEN_SEARCH_MAX_LOAN = 1_000_000.0
BREAKEVEN_SEARCH_TOLERANCE = 50.0  # dollars; well under the precision the model claims


@st.cache_data(show_spinner=False)
def find_breakeven_loan(major_name: str, interest_rate: float, repayment_strategy: str,
                         roi_window_years: int = ROI_WINDOW_YEARS,
                         col_index: float = 100.0,
                         career_data_source: str = "National",
                         hs_wage_index: float = 1.0,
                         personal_contribution: float = 0.0,
                         enrollment_years: int = 0,
                         working_years: int = 0) -> dict:
    """The undergraduate loan at which `major_name` stops beating a debt-free
    high school graduate — i.e. where earnings_premium crosses zero.

    Found by bisection because the repayment engines aren't invertible:
    IDR/RAP payments are income-driven with forgiveness, so "what was repaid
    inside the window" is the output of a simulation rather than a formula.
    Bisection is valid because earnings_premium is monotonically decreasing
    in loan size — more debt strictly means more repaid inside the window,
    and nothing else in the model depends on the loan.

    Returns a dict, not a float, because the interesting answers aren't
    numbers. A major can already be behind at zero debt ("never"), or still
    ahead at an absurd one ("beyond_search_max" — which is most majors under
    IDR, where the income-driven payment cap makes the principal nearly
    irrelevant within the window). Rendering either as "your break-even is
    $X" would print a falsehood.

    col_index doesn't move the crossing — the cost-of-living adjustment
    divides both sides of the comparison by the same index, scaling the
    premium without relocating its zero. It's threaded through anyway so this
    always calls compute_scenario_results exactly as the rest of the page does.

    hs_wage_index, personal_contribution and enrollment_years all DO move the
    crossing, so each is a real parameter that must match the scenario being
    displayed. hs_wage_index scales the HS-grad baseline up to the selected
    city's wage level -- it lands on only one side of the comparison, so a
    high-wage metro pushes the break-even down. personal_contribution is a
    fixed cash outflow subtracted from the major's side (see calculate_roi),
    so more of it also pushes the break-even down. enrollment_years adds the
    degree-seeker's foregone-earnings years to the HS baseline only, so it too
    pushes the break-even down. working_years (part-time-while-working CC) adds
    HS-wage earnings back to the major side, pushing the break-even UP. Passing
    the page's displayed value for one but the default for another is exactly
    the bug this signature exists to prevent: the verdict ("worth it") would be
    computed against a different baseline than the earnings-premium number
    printed beside it. All of these are still independent of the loan, so
    earnings_premium stays monotonic in loan size and the bisection remains
    valid.

    Cached because the on-screen render calls it on every Streamlit rerun and
    a bisection is ~15 full amortisation simulations.

    career_data_source is never read in the body — it exists purely to key
    the cache. st.cache_data hashes arguments, but the work here depends on
    the MAJOR_DATA global, which is rebuilt when the visitor switches the
    Career Salary Data source (a Software Developer earns differently in
    California than nationally). Without this parameter the cache would
    happily serve a national break-even to someone who just switched to
    California.
    """
    def premium_at(loan: float) -> float:
        return compute_scenario_results(
            major_name, loan, interest_rate, repayment_strategy,
            personal_contribution=personal_contribution,
            col_index=col_index, roi_window_years=roi_window_years,
            hs_wage_index=hs_wage_index,
            enrollment_years=enrollment_years,
            working_years=working_years,
        )["roi_result"]["earnings_premium"]

    if premium_at(0.0) <= 0:
        return {"status": "never", "breakeven_loan": None}
    if premium_at(BREAKEVEN_SEARCH_MAX_LOAN) > 0:
        return {"status": "beyond_search_max", "breakeven_loan": None}

    lo, hi = 0.0, BREAKEVEN_SEARCH_MAX_LOAN
    while hi - lo > BREAKEVEN_SEARCH_TOLERANCE:
        mid = (lo + hi) / 2
        if premium_at(mid) > 0:
            lo = mid
        else:
            hi = mid
    return {"status": "ok", "breakeven_loan": round((lo + hi) / 2, 2)}


def breakeven_summary(major_name: str, loan_amount: float, interest_rate: float,
                       repayment_strategy: str, roi_window_years: int = ROI_WINDOW_YEARS,
                       col_index: float = 100.0,
                       career_data_source: str = "National",
                       hs_wage_index: float = 1.0,
                       personal_contribution: float = 0.0,
                       enrollment_years: int = 0,
                       working_years: int = 0) -> dict:
    """find_breakeven_loan framed against what this visitor is actually
    borrowing, shared by the on-screen section and its PDF counterpart so
    the two can't drift.

    Returns None for `headline` when the break-even shouldn't be shown at
    all. That's the sub-baccalaureate case: for an occupation BLS says needs
    less than a bachelor's, "this degree stops paying off at $X" is not
    unfavourable, it's malformed — the model charged four financed years to
    reach a job that never asked for them. The apprenticeship module already
    makes that point properly (see SUB_BACHELORS_EDUCATION_LEVELS), so this
    defers to it rather than printing a number that answers no question.
    """
    typical_education = MAJOR_DATA.get(major_name, {}).get("typical_education", "")
    if typical_education in SUB_BACHELORS_EDUCATION_LEVELS:
        return {"headline": None, "detail": None, "status": "not_applicable"}

    result = find_breakeven_loan(major_name, interest_rate, repayment_strategy,
                                  roi_window_years, col_index,
                                  career_data_source=career_data_source,
                                  hs_wage_index=hs_wage_index,
                                  personal_contribution=personal_contribution,
                                  enrollment_years=enrollment_years,
                                  working_years=working_years)
    years = roi_window_years
    # Career-mode names are plural BLS occupations ("Software Developers")
    # while Major-mode names are singular ("Computer Science"), so any verb
    # agreeing with the name is wrong in one mode or the other: "Software
    # Developers still pays off" / "Computer Science still pay off". Two rules
    # keep both readable, and violating either has already shipped once:
    #   - headlines are subject-less ("Still pays off at $X")
    #   - details name the major in a PREPOSITIONAL slot ("For {major}, this
    #     comes out ahead"), never as the subject of a verb
    # Dropping the name entirely is not the answer either -- that leaves an
    # unanchored "this path" and the reader can't tell which path.
    if result["status"] == "never":
        return {
            "headline": "No — not at any loan amount",
            "detail": (
                f"Over {years} years, this path earns less than a debt-free high school "
                f"graduate does — even with no loan at all. Borrowing less doesn't change "
                f"that; only a longer horizon or a different path would."
            ),
            "status": "never", "breakeven_loan": None, "headroom": None,
            "positive": False, "label": "Worth a rethink",
        }
    if result["status"] == "beyond_search_max":
        return {
            "headline": "Yes — at any realistic loan amount",
            "detail": (
                f"Over {years} years this path stays ahead of a debt-free high school graduate "
                f"even past {fmt_money(BREAKEVEN_SEARCH_MAX_LOAN)} of debt. Under Income-Driven "
                f"Repayment that's usually because the payment is capped by your income rather "
                f"than your balance — the debt outlives this window rather than disappearing."
            ),
            "status": "beyond_search_max", "breakeven_loan": None, "headroom": None,
            "positive": True, "label": "Good news",
        }

    breakeven = result["breakeven_loan"]
    headroom = breakeven - loan_amount
    if headroom >= 0:
        # Deliberately NOT "you could borrow $X more". That was the original
        # wording and it reads as an invitation -- a student skimming it sees
        # permission to take on more debt, from a tool whose entire purpose is
        # making debt legible. The margin is a safety margin, not an
        # allowance, and for a high-earning major the break-even ($628,677 for
        # Computer Science) is a number nobody borrows anyway, so quoting the
        # gap as spendable headroom is both encouraging and meaningless.
        #
        # Lead with the verdict at the debt they actually have; state the line
        # second, as a fact rather than a target.
        # The raw break-even is often a number nobody would ever borrow
        # ($628,677 for Computer Science), which reads as noise rather than
        # reassurance. Expressing it as a multiple of what they're actually
        # borrowing is what makes it land: "3x what you're borrowing" is a
        # fact about THEM, "$628,677" is a fact about Mars.
        multiple = breakeven / loan_amount if loan_amount > 0 else None
        # Three tiers, because "comfortably" should mean it. A break-even at 2x+
        # the loan is a genuine cushion and reads as reassurance; a break-even
        # barely above the loan is a squeaker and saying "comfortable" there
        # would oversell exactly the way this tool tries not to. "Pays off" is
        # this app's established idiom for "is worth the debt" (cf. "Doesn't
        # pay off"), not a literal claim about retiring the balance -- the loan
        # amortises regardless; what's true is the degree beats skipping it.
        # Headlines stay subject-less (see the rule above): naming the major
        # here would give "Software Developers comfortably pays off" -- plural
        # occupation, singular verb. The major goes in the detail's
        # prepositional slot instead.
        if multiple is not None and multiple >= 2:
            headline = f"Yes — comfortably worth your {fmt_money(loan_amount)} loan"
            detail = (
                f"For {major_name}, this comes out well ahead of a debt-free high school "
                f"graduate over {years} years — it earns back more than the loan costs you. "
                f"It would take {fmt_money(breakeven)} of loans, about {multiple:.0f}× what "
                f"you're borrowing, before that stopped being true."
            )
        elif multiple is not None and multiple >= 1.5:
            headline = f"Yes — worth your {fmt_money(loan_amount)} loan"
            detail = (
                f"For {major_name}, this comes out ahead of a debt-free high school graduate "
                f"over {years} years — it earns back more than the loan costs you. It would take "
                f"{fmt_money(breakeven)} of loans, about half again what you're borrowing, before "
                f"that stopped being true."
            )
        else:
            headline = f"Yes, but only just — worth your {fmt_money(loan_amount)} loan"
            detail = (
                f"For {major_name}, this comes out ahead of a debt-free high school graduate "
                f"over {years} years, but the margin is thin: it stops being worth it at "
                f"{fmt_money(breakeven)} of loans, and you're already at {fmt_money(loan_amount)}."
            )
    else:
        # State the break-even as an actionable CEILING, not just a fact. In
        # the positive case a margin framed as "borrow $X more" reads as an
        # invitation (removed earlier for that reason) -- but here the visitor
        # is already OVER the line, so naming the cap points toward LESS debt,
        # which is the safe direction. "Keep loans under $X" is the one number
        # a student in this case can actually act on.
        headline = f"No — not at {fmt_money(loan_amount)}"
        detail = (
            f"For {major_name}, this falls behind a debt-free high school graduate over "
            f"{years} years. To come out ahead, total loans would need to stay under "
            f"{fmt_money(breakeven)} — you're {fmt_money(abs(headroom))} above that ceiling. "
            f"A longer horizon, a cheaper school, or Income-Driven Repayment can each move the line."
        )
    return {"headline": headline, "detail": detail, "status": "ok",
            "breakeven_loan": breakeven, "headroom": headroom,
            # positive drives the render: green success box vs amber warning.
            # A celebratory banner on a "No" would cheerlead the optimism bias
            # this tool exists to correct, so the tone tracks the verdict, not
            # the mere presence of a break-even.
            "positive": headroom >= 0,
            "label": "Good news" if headroom >= 0 else "Worth a rethink"}


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


def get_loan_to_income_risk_tier(loan_to_takehome_pct: float, effective_tax_rate: float) -> dict:
    """Classifies a "Student Loan Payment / Take-Home" ratio (already expressed as
    % of NET/take-home pay) against the real, gross-income-based guidelines
    in LOAN_TO_INCOME_GROSS_*_PCT, converted onto this scenario's own net
    basis: `gross_pct / (1 - effective_tax_rate)` -- e.g. a person paying an
    effective_tax_rate of 30% who's at the 10%-of-gross "manageable" line is
    also at 10%/0.70 ≈ 14.3% of their OWN take-home pay, not a generic
    assumed conversion factor. Returns the tier label, a hex color for the
    on-screen number, and the two converted thresholds (for the tooltip)."""
    net_basis = max(1.0 - effective_tax_rate, 0.01)  # floor avoids a div-by-~0 blowup at unrealistic tax rates
    manageable_threshold = LOAN_TO_INCOME_GROSS_MANAGEABLE_PCT / net_basis
    caution_threshold = LOAN_TO_INCOME_GROSS_CAUTION_PCT / net_basis
    if loan_to_takehome_pct <= manageable_threshold:
        tier, color = "Manageable", "#1a7f37"
    elif loan_to_takehome_pct <= caution_threshold:
        tier, color = "Elevated", "#b35900"
    else:
        tier, color = "High", "#c0392b"
    return {"tier": tier, "color": color, "manageable_threshold": manageable_threshold, "caution_threshold": caution_threshold}


def get_monthly_payment_for_stage(repayment_result: dict, strategy: str, target_month: int) -> float:
    """The loan payment at a given career-stage snapshot. If the loan is
    already paid off or forgiven by target_month, the payment is $0 for
    either strategy -- Standard's constant monthly_payment is only valid
    while the loan is still active.

    Strictly greater-than, not >=, and the boundary is not academic: it is
    the default view. A Standard 10-Year loan's final payment falls in month
    120, and the Mid-Career (Year 10) snapshot asks for exactly month 120
    ((9+1)*12). Treating "paid off at month 120" as "no payment in month
    120" made the app report a $0 monthly payment, a $0 loan slice on the
    Payment vs. Disposable Income chart, and disposable income overstated by
    the entire payment -- while the Loan Information section directly above
    still showed $2,062/month. Year 10 spans months 109-120 and every one of
    them is a payment month; the loan is retired BY month 120, not BEFORE it.
    """
    if target_month > repayment_result["payoff_years"] * 12:
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

# Passed to every st.plotly_chart(...) call below. The modebar (camera/zoom/
# pan/autoscale icons) is tiny, touch-unfriendly, and irrelevant for a
# read-only report chart -- hiding it declutters both mobile and desktop.
PLOTLY_CHART_CONFIG = {"displayModeBar": False}

def build_balance_chart(schedule_df: pd.DataFrame, strategy_label: str):
    fig = px.line(
        schedule_df, x="year", y="balance",
        title="Loan Balance Over Time",
        labels={"year": "Years", "balance": "Remaining Balance ($)"},
    )
    fig.update_traces(line=dict(width=3))
    fig.update_layout(yaxis_tickprefix="$", hovermode="x unified", title_font_size=14)
    return fig


def build_roi_bar_chart(hs_net_position: float, major_net_position: float, major_name: str,
                         roi_window_years: int):
    y_label = f"{roi_window_years}-Year Net Position ($)"
    comparison_df = pd.DataFrame({
        "Group": ["High School Graduate", major_name],
        y_label: [hs_net_position, major_net_position],
    })
    fig = px.bar(
        comparison_df, x="Group", y=y_label, color="Group",
        title=f"{roi_window_years}-Year Net Position vs. High School Baseline",
        text_auto=".2s",
    )
    fig.update_layout(
        yaxis_tickprefix="$", showlegend=False, title_x=0.5, title_xanchor="center", title_font_size=14,
    )
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
        title="Loan Balance Over Time",
        labels={"year": "Years", "balance": "Remaining Balance ($)"},
    )
    fig.update_layout(yaxis_tickprefix="$", hovermode="x unified", title_font_size=14)
    return fig


def build_scenario_comparison_roi_chart(hs_net_position: float,
                                         net_a: float, label_a: str,
                                         net_b: float, label_b: str,
                                         roi_window_years: int):
    """3-bar version of build_roi_bar_chart: HS-grad baseline plus both
    scenarios, for comparing net financial position directly."""
    y_label = f"{roi_window_years}-Year Net Position ($)"
    comparison_df = pd.DataFrame({
        "Group": ["High School Graduate", label_a, label_b],
        y_label: [hs_net_position, net_a, net_b],
    })
    fig = px.bar(
        comparison_df, x="Group", y=y_label, color="Group",
        title=f"{roi_window_years}-Year Net Position: Scenario Comparison",
        text_auto=".2s",
    )
    fig.update_layout(
        yaxis_tickprefix="$", showlegend=False, title_x=0.5, title_xanchor="center", title_font_size=14,
    )
    return fig


def build_takehome_pie_chart(take_home: dict):
    """Pie chart of how gross salary splits between take-home pay and each
    tax category -- "slices of a whole" is a more intuitive framing for a
    high-school audience than a waterfall's running subtraction. No legend:
    each slice already labels itself (textinfo="percent+label"), and an
    external legend has nowhere to go on a narrow screen without pushing
    the pie itself out of the visible frame."""
    fig = px.pie(
        names=["Take-Home Pay", "Federal Tax", "State Tax", "FICA"],
        values=[take_home["net_take_home"], take_home["federal_tax"],
                 take_home["state_tax"], take_home["fica_tax"]],
        title="Where Your Salary Actually Goes",
    )
    fig.update_traces(textinfo="percent+label", automargin=True)
    fig.update_layout(showlegend=False, title_font_size=14)
    return fig


def build_takehome_vs_loan_chart(monthly_net_take_home: float, monthly_payment: float):
    """Pie chart splitting monthly take-home pay into student loan payment
    vs. remaining disposable income -- while the payment still fits inside
    take-home pay. A pie chart can't represent a payment that *exceeds*
    take-home pay (no valid slice set sums past 100%), so in that case this
    returns a simple 2-bar comparison of Take-Home Pay vs. Required Student
    Loan Payment instead, which can show the overage naturally. No legend,
    same reasoning as build_takehome_pie_chart -- each slice already labels
    itself. Deliberately no custom margin= override here -- Plotly's own
    defaults (plus automargin for label overflow) are what
    build_takehome_pie_chart uses, and these two charts should always
    render at the same size."""
    if monthly_payment <= monthly_net_take_home:
        remaining = monthly_net_take_home - monthly_payment
        # "vs. Disposable Income" was vague: "vs." reads as two separate
        # things, when the pie is one whole (monthly take-home pay) split into
        # two, and "disposable income" never says it means "what's left after
        # the loan". Title now names the whole; slices use plain words.
        fig = px.pie(
            names=["Student Loan Payment", "What's Left to Spend"],
            values=[monthly_payment, remaining],
            title="Your Monthly Take-Home Pay: Loan vs. What's Left",
        )
        fig.update_traces(textinfo="percent+label", automargin=True)
        fig.update_layout(showlegend=False, title_font_size=14)
        return fig
    fig = px.bar(
        x=["Take-Home Pay", "Required Student Loan Payment"],
        y=[monthly_net_take_home, monthly_payment],
        title="Monthly Student Loan Payment Exceeds Take-Home Pay",
    )
    fig.update_layout(yaxis_title="Monthly $", xaxis_title=None, title_font_size=14)
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


# The report's palette, matching the on-screen app so a downloaded PDF looks
# like it came from the same product rather than from reportlab's defaults.
PDF_ACCENT = colors.HexColor("#1a56db")     # headings, rules, table headers
PDF_INK = colors.HexColor("#1f2430")        # body text
PDF_MUTED = colors.HexColor("#667085")      # captions, page furniture
PDF_RULE = colors.HexColor("#d8dce5")       # hairlines, table grid
PDF_BAND = colors.HexColor("#f5f7fb")       # alternating table rows


def _draw_pdf_header_footer(canvas, doc):
    """Page decoration for every page of every generated PDF -- passed to
    SimpleDocTemplate.build() as onFirstPage/onLaterPages, which reportlab
    calls once per page with the low-level canvas (flowables like _pdf_table
    can't draw outside their own frame, so page furniture always goes
    through this canvas-level hook instead).

    Carries the tool's name, the page number, and the disclaimer on EVERY
    page, because a printed report gets separated: a parent reading page 3
    should still see what produced it and that it's an estimate, without
    having to find page 1.
    """
    canvas.saveState()
    page_width, page_height = doc.pagesize

    # Header: accent rule + wordmark, so the page reads as the app's output.
    canvas.setStrokeColor(PDF_ACCENT)
    canvas.setLineWidth(2)
    canvas.line(doc.leftMargin, page_height - 44, page_width - doc.rightMargin, page_height - 44)
    canvas.setFont("Helvetica-Bold", 8)
    canvas.setFillColor(PDF_ACCENT)
    canvas.drawString(doc.leftMargin, page_height - 40, "STUDENT LOAN PAYOFF & MAJOR ROI CALCULATOR")
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(PDF_MUTED)
    canvas.drawRightString(page_width - doc.rightMargin, page_height - 40, APP_URL.replace("https://", ""))

    # Footer: disclaimer left, page number right, hairline above both.
    canvas.setStrokeColor(PDF_RULE)
    canvas.setLineWidth(0.5)
    canvas.line(doc.leftMargin, 46, page_width - doc.rightMargin, 46)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(PDF_MUTED)
    canvas.drawString(doc.leftMargin, 34, "Educational estimate — not financial advice. Figures are averages and will differ from any individual's actual outcome.")
    canvas.drawString(doc.leftMargin, 24, f"Generated {now_local().strftime('%B %d, %Y at %I:%M %p %Z')}")
    canvas.setFont("Helvetica-Bold", 8)
    canvas.setFillColor(PDF_INK)
    canvas.drawRightString(page_width - doc.rightMargin, 24, f"Page {canvas.getPageNumber()}")
    canvas.restoreState()


def _pdf_styles() -> dict:
    """The report's type scale, defined once and shared by both generators.

    reportlab's getSampleStyleSheet() is serviceable but generic -- stock
    sizes, no colour, and a centred Title that reads like a default. These
    are the app's own hierarchy: an accent-coloured section heading with a
    rule under it, muted captions, and a left-aligned cover title.
    """
    base = getSampleStyleSheet()
    return {
        "cover_title": ParagraphStyle(
            "cover_title", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=23, leading=27, textColor=PDF_INK, alignment=0, spaceAfter=2),
        "cover_sub": ParagraphStyle(
            "cover_sub", parent=base["Normal"], fontName="Helvetica",
            fontSize=10.5, leading=15, textColor=PDF_MUTED, spaceAfter=0),
        "section": ParagraphStyle(
            "section", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=13, leading=16, textColor=PDF_ACCENT, spaceBefore=14, spaceAfter=6),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontName="Helvetica",
            fontSize=9.5, leading=13, textColor=PDF_INK),
        "caption": ParagraphStyle(
            "caption", parent=base["Normal"], fontName="Helvetica",
            fontSize=8, leading=11, textColor=PDF_MUTED),
    }


def _pdf_breakeven_block(breakeven: dict, styles: dict, scenario_label: str = None) -> list:
    """The 'Is this debt worth it?' section for a report, mirroring the
    on-screen banner: the question, the verdict word ("Good news." / "Worth a
    rethink."), then the headline and detail. Shared by both generators so the
    single and compare PDFs can't drift from each other or from the page.

    Returns [] for the sub-baccalaureate case (headline None), where the
    on-screen block is silent too. scenario_label prefixes the heading in
    Compare Mode ("Scenario A — Is this debt worth it?") so two of these on
    one page stay distinguishable.

    No colour: reportlab has no equivalent of st.success/st.warning here, and
    the verdict word plus the accent-blue heading already carry the tone. The
    green/amber box is an on-screen affordance, not information.
    """
    if not breakeven["headline"]:
        return []
    prefix = f"{scenario_label} — " if scenario_label else ""
    parts = [Spacer(1, 10), Paragraph(_strip_emoji(f"🎯 {prefix}Is this debt worth it?"), styles["section"])]
    if breakeven.get("label"):
        parts.append(Paragraph(f"<b>{xml_escape(breakeven['label'])}.</b>", styles["body"]))
    parts.append(Paragraph(f"<b>{xml_escape(breakeven['headline'])}</b>", styles["body"]))
    parts.append(Paragraph(xml_escape(breakeven["detail"]), styles["body"]))
    return parts


def _pdf_sources_section(styles: dict, roi_window_years: int, uses_training_debt: bool = False,
                          underemployment_majors: list = None) -> list:
    """A "where these numbers come from" section, closing every report.

    The app's on-screen Methodology section already carries this, and the
    project's rule is that every number shown should be traceable to a
    citation there -- but the PDF was the one surface that broke it. That
    matters most for this artifact specifically: the report is what a
    student hands to a parent, detached from the app, and "$56,743
    take-home" with no attribution is just an assertion. A parent's first
    reasonable question is "says who?", and the answer shouldn't require
    going back to the website.

    Kept to the sources actually used to produce the figures on the page,
    with the full methodology one URL away rather than reproduced here.
    """
    rows = [
        ["Figure", "Source"],
        ["Salaries by occupation",
         "U.S. Bureau of Labor Statistics, Occupational Employment and Wage Statistics (OEWS). "
         "Starting salary uses the 25th-percentile wage; mid-career uses the median."],
        ["High school graduate baseline",
         "U.S. Bureau of Labor Statistics, Current Population Survey — median usual weekly earnings "
         "for full-time workers age 25+ with a high school diploma and no college ($946/week, Q3 2024), "
         "annualised. Wage growth of 2%/yr is an assumption, not a BLS figure."],
        ["Cost of attendance & college debt",
         "U.S. Department of Education, College Scorecard."],
        ["Federal & state income tax",
         "IRS 2024 federal brackets and standard deduction; published 2024 state brackets."],
        ["Cost-of-living adjustment",
         "U.S. Bureau of Economic Analysis, Regional Price Parities (2023)."],
        ["Underemployment",
         "Federal Reserve Bank of New York, The Labor Market for Recent College Graduates "
         "(updated February 2026), from Census ACS/IPUMS and DOL O*NET, covering "
         f"{UNDEREMPLOYMENT_MAJOR_COUNT} majors."],
    ]
    if uses_training_debt:
        rows.append([
            "Professional-school debt & training",
            "AAMC (median medical school debt and resident stipend, 2024); "
            "ABA Young Lawyers Division (average law school debt, 2024); "
            "ADA/ADEA Survey of Dental School Seniors (average debt among indebted graduates, 2024). "
            "Residency is modelled as a representative 3 years; real programmes run 3–7.",
        ])
    # The single most important caveat gets its own paragraph above the
    # table, not a row inside it -- the report is read detached from the app,
    # and "assumes you work in your field" is the assumption every figure on
    # every preceding page rests on.
    #
    # underemployment_majors is a list of (scenario_label, major) pairs. In
    # Major mode each major has its OWN underemployment rate, so a compare
    # report needs one paragraph per scenario -- previously it showed only
    # Scenario A's. In Career mode the text is national and shared, so the
    # callers pass None and it renders once. Identical majors collapse to one.
    if underemployment_majors:
        seen, disclosure_paras = set(), []
        for label, mjr in underemployment_majors:
            if mjr in seen:
                continue
            seen.add(mjr)
            text = underemployment_disclosure(mjr, for_pdf=True)
            # Only label when there's more than one distinct major to
            # distinguish -- a lone paragraph doesn't need "Scenario A:".
            prefix = f"<b>{xml_escape(label)}:</b> " if label and len({m for _, m in underemployment_majors}) > 1 else ""
            disclosure_paras.append(Paragraph(prefix + text, styles["body"]))
    else:
        disclosure_paras = [Paragraph(underemployment_disclosure(None, for_pdf=True), styles["body"])]
    return [
        Spacer(1, 14),
        Paragraph("What these numbers assume", styles["section"]),
        *disclosure_paras,
        Spacer(1, 10),
        Paragraph("Where these numbers come from", styles["section"]),
        _pdf_table(rows, col_ratios=[0.24, 0.76]),
        Spacer(1, 6),
        Paragraph(
            f"All figures are modelled estimates over {roi_window_years} years, not predictions, and "
            f"describe published averages rather than any individual's outcome. Full methodology, "
            f"assumptions and citations: {APP_URL}",
            styles["caption"],
        ),
    ]


def _pdf_rule(width: float = None) -> Table:
    """A thin accent rule, used under section headings. A 1-row Table is the
    least fragile way to draw a horizontal line as a flowable -- reportlab's
    HRFlowable exists but doesn't respect the frame width as reliably when
    the story is built across multiple page sizes."""
    rule = Table([[""]], colWidths=[width or PDF_CONTENT_WIDTH], rowHeights=[1])
    rule.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PDF_RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return rule


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


# Matches SimpleDocTemplate(pagesize=letter)'s default 1-inch margins on
# every side -- no _pdf_table below is ever allowed to exceed this width, so
# a table can never spill past the page edge regardless of how long its
# header/cell text is.
PDF_CONTENT_WIDTH = letter[0] - 2 * inch
PDF_CELL_FONT_SIZE = 9
PDF_CELL_MIN_WIDTH = 60  # floor per column, so a short table's cells never get unreadably cramped
PDF_CELL_H_PADDING = 12  # matches the 6pt LEFTPADDING + 6pt RIGHTPADDING applied below

_PDF_CELL_STYLE = ParagraphStyle("pdf_cell", fontName="Helvetica", fontSize=PDF_CELL_FONT_SIZE, leading=11)
_PDF_CELL_BOLD_STYLE = ParagraphStyle("pdf_cell_bold", fontName="Helvetica-Bold", fontSize=PDF_CELL_FONT_SIZE, leading=11)
# Header-row cells sit on the accent band, so their text must knock out to
# white. TableStyle's TEXTCOLOR can't do this -- each cell is a Paragraph,
# which carries its own colour.
_PDF_CELL_HEADER_STYLE = ParagraphStyle("pdf_cell_header", fontName="Helvetica-Bold",
                                         fontSize=PDF_CELL_FONT_SIZE, leading=11,
                                         textColor=colors.white)

_PDF_MONEY_FORMATTER = mticker.FuncFormatter(lambda value, _pos: f"${value:,.0f}")


def _pdf_image_from_figure(fig, max_width: float = PDF_CONTENT_WIDTH) -> Image:
    """Rasterize a matplotlib figure to a reportlab Image flowable, scaled
    to fit the PDF's content width while preserving aspect ratio. Always
    closes the figure afterward -- this runs inside a long-lived Streamlit
    server process, so leaked open figures would accumulate across every
    PDF download instead of being garbage collected like a short-lived
    script's would."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    width_px, height_px = PILImage.open(io.BytesIO(buf.getvalue())).size
    return Image(buf, width=max_width, height=max_width * height_px / width_px)


def build_pdf_balance_chart(schedule_df: pd.DataFrame, strategy_label: str) -> Image:
    """PDF counterpart to build_balance_chart -- simplified redraw for
    print, not required to be pixel-identical to the on-screen interactive
    version."""
    fig, ax = plt.subplots(figsize=(6, 3.5))
    ax.plot(schedule_df["year"], schedule_df["balance"], linewidth=2.5)
    ax.set_title("Loan Balance Over Time")
    ax.set_xlabel("Years")
    ax.set_ylabel("Remaining Balance ($)")
    ax.yaxis.set_major_formatter(_PDF_MONEY_FORMATTER)
    ax.grid(True, alpha=0.3)
    return _pdf_image_from_figure(fig)


def build_pdf_comparison_balance_chart(schedule_a: pd.DataFrame, label_a: str,
                                        schedule_b: pd.DataFrame, label_b: str) -> Image:
    """PDF counterpart to build_comparison_balance_chart."""
    fig, ax = plt.subplots(figsize=(6, 3.5))
    ax.plot(schedule_a["year"], schedule_a["balance"], linewidth=2.5, label=label_a)
    ax.plot(schedule_b["year"], schedule_b["balance"], linewidth=2.5, label=label_b)
    ax.set_title("Loan Balance Over Time")
    ax.set_xlabel("Years")
    ax.set_ylabel("Remaining Balance ($)")
    ax.yaxis.set_major_formatter(_PDF_MONEY_FORMATTER)
    ax.grid(True, alpha=0.3)
    ax.legend()
    return _pdf_image_from_figure(fig)


def build_pdf_roi_bar_chart(hs_net_position: float, major_net_position: float, major_name: str,
                             roi_window_years: int) -> Image:
    """PDF counterpart to build_roi_bar_chart."""
    fig, ax = plt.subplots(figsize=(6, 3.5))
    groups = ["High School Graduate", major_name]
    values = [hs_net_position, major_net_position]
    ax.bar(groups, values, color=["#636EFA", "#EF553B"])
    ax.set_title(f"{roi_window_years}-Year Net Position vs. High School Baseline")
    ax.set_ylabel(f"{roi_window_years}-Year Net Position ($)")
    ax.yaxis.set_major_formatter(_PDF_MONEY_FORMATTER)
    ax.tick_params(axis="x", labelsize=9)
    fig.autofmt_xdate(rotation=10, ha="center")
    return _pdf_image_from_figure(fig)


def build_pdf_scenario_comparison_roi_chart(hs_net_position: float,
                                             net_a: float, label_a: str,
                                             net_b: float, label_b: str,
                                             roi_window_years: int) -> Image:
    """PDF counterpart to build_scenario_comparison_roi_chart."""
    fig, ax = plt.subplots(figsize=(6, 3.5))
    groups = ["High School Graduate", label_a, label_b]
    values = [hs_net_position, net_a, net_b]
    ax.bar(groups, values, color=["#636EFA", "#EF553B", "#00CC96"])
    ax.set_title(f"{roi_window_years}-Year Net Position: Scenario Comparison")
    ax.set_ylabel(f"{roi_window_years}-Year Net Position ($)")
    ax.yaxis.set_major_formatter(_PDF_MONEY_FORMATTER)
    ax.tick_params(axis="x", labelsize=9)
    fig.autofmt_xdate(rotation=10, ha="center")
    return _pdf_image_from_figure(fig)


def build_pdf_takehome_pie_chart(take_home: dict) -> Image:
    """PDF counterpart to build_takehome_pie_chart."""
    fig, ax = plt.subplots(figsize=(6, 3.5))
    labels = ["Take-Home Pay", "Federal Tax", "State Tax", "FICA"]
    values = [take_home["net_take_home"], take_home["federal_tax"],
              take_home["state_tax"], take_home["fica_tax"]]
    ax.pie(values, labels=labels, autopct="%1.0f%%", startangle=90)
    ax.set_title("Where Your Salary Actually Goes")
    return _pdf_image_from_figure(fig)


def build_pdf_takehome_vs_loan_chart(monthly_net_take_home: float, monthly_payment: float) -> Image:
    """PDF counterpart to build_takehome_vs_loan_chart -- same pie-or-bar-
    fallback branch condition (a pie can't represent a payment that
    exceeds take-home pay)."""
    fig, ax = plt.subplots(figsize=(6, 3.5))
    if monthly_payment <= monthly_net_take_home:
        remaining = monthly_net_take_home - monthly_payment
        ax.pie(
            [monthly_payment, remaining],
            labels=["Student Loan Payment", "What's Left to Spend"],
            autopct="%1.0f%%", startangle=90,
        )
        ax.set_title("Your Monthly Take-Home Pay: Loan vs. What's Left")
    else:
        ax.bar(["Take-Home Pay", "Required Student Loan Payment"],
               [monthly_net_take_home, monthly_payment], color=["#636EFA", "#EF553B"])
        ax.set_title("Monthly Student Loan Payment Exceeds Take-Home Pay")
        ax.set_ylabel("Monthly $")
        ax.yaxis.set_major_formatter(_PDF_MONEY_FORMATTER)
    return _pdf_image_from_figure(fig)


def _pdf_table(rows: list, header: bool = True, full_width: bool = False,
                col_ratios: list = None) -> Table:
    """A bordered reportlab Table, centered on the page. Each column is
    sized to its own widest cell's natural text width (so a short table --
    e.g. a 2-column module summary -- stays compact and visibly centered,
    not stretched edge-to-edge); if the natural total would exceed
    PDF_CONTENT_WIDTH, every column is scaled down proportionally to fit
    exactly within it instead, so a table can never spill past the page
    edge no matter how long its header/cell text is. Each cell is wrapped
    in a Paragraph (rather than left as a bare string) so text that no
    longer fits after scaling wraps onto a second line instead of
    overflowing. `header=True` bolds/shades row 0 (tabular data with column
    headers), `header=False` bolds column 0 instead (a plain key/value
    table, e.g. the profile summary). Cell text is XML-escaped since
    Paragraph parses its content as markup -- a school name like "Texas
    A&M" would otherwise break Paragraph's parser."""
    num_cols = len(rows[0]) if rows else 1

    def _is_bold(r, c):
        return (header and r == 0) or (not header and c == 0)

    def _cell(value, bold, on_accent=False):
        if on_accent:
            style = _PDF_CELL_HEADER_STYLE
        else:
            style = _PDF_CELL_BOLD_STYLE if bold else _PDF_CELL_STYLE
        return Paragraph(xml_escape(str(value)), style)

    wrapped_rows = [
        [_cell(cell, bold=_is_bold(r, c), on_accent=(header and r == 0))
         for c, cell in enumerate(row)]
        for r, row in enumerate(rows)
    ]

    natural_widths = [
        max(
            (stringWidth(xml_escape(str(row[c])), "Helvetica-Bold" if _is_bold(r, c) else "Helvetica",
                         PDF_CELL_FONT_SIZE)
             for r, row in enumerate(rows)),
            default=0,
        ) + PDF_CELL_H_PADDING
        for c in range(num_cols)
    ]
    natural_widths = [max(w, PDF_CELL_MIN_WIDTH) for w in natural_widths]
    total_natural = sum(natural_widths)
    if col_ratios:
        col_widths = [PDF_CONTENT_WIDTH * r for r in col_ratios]
    elif total_natural > PDF_CONTENT_WIDTH or full_width:
        # Scale to fit exactly: down when the natural width would overflow,
        # up when the caller asked for a full-width table.
        scale = PDF_CONTENT_WIDTH / total_natural
        col_widths = [w * scale for w in natural_widths]
    else:
        col_widths = natural_widths

    table = Table(wrapped_rows, colWidths=col_widths, hAlign="CENTER")
    # Horizontal rules only, no vertical grid: the column gutters already
    # separate the data, and dropping the verticals turns a boxed-in
    # spreadsheet into something that reads like a report.
    style = [
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, PDF_RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), PDF_CELL_H_PADDING / 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), PDF_CELL_H_PADDING / 2),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    if header:
        # Accent header band with knocked-out white text.
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), PDF_ACCENT),
            ("LINEBELOW", (0, 0), (-1, 0), 0, PDF_ACCENT),
            ("TOPPADDING", (0, 0), (-1, 0), 6),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ]
        # Zebra-band the body rows so a long per-year table stays trackable
        # across the page without vertical rules.
        for row_index in range(1, len(wrapped_rows)):
            if row_index % 2 == 0:
                style.append(("BACKGROUND", (0, row_index), (-1, row_index), PDF_BAND))
    else:
        # Key/value table: tint the label column instead of a header row.
        style.append(("BACKGROUND", (0, 0), (0, -1), PDF_BAND))
    table.setStyle(TableStyle(style))
    return table


def _cc_info_for_pdf(cc_mode, cc_state_key, cost_per_year, oop, cc_years):
    """Small {mode,state_label,cost,oop,cc_years} bundle for the PDF profile's
    community-college disclosure rows (see _pdf_profile_rows). Returns None when
    no CC path is active, so the rows are simply omitted."""
    if cc_mode not in ("fulltime", "parttime"):
        return None
    state_label = ("National average" if cc_state_key == "__national__"
                   else US_STATES.get(cc_state_key, "National average"))
    return {"mode": cc_mode, "state_label": state_label,
            "cost": cost_per_year, "oop": oop, "cc_years": cc_years}


def _pdf_profile_rows(major_name, school_name, in_state, coa_per_year,
                       personal_contribution_per_year, grants_per_year,
                       interest_rate_pct, repayment_strategy_label,
                       career_stage=None, city_name=None, start_year=None,
                       cc_info=None) -> list:
    rows = [
        ["Profession", major_name],
        ["School", school_name or "(not entered)"],
        ["In-State", "Yes" if in_state else "No"],
    ]
    if city_name is not None:
        rows.append(["City / Metro Area", city_name])
    if career_stage is not None:
        rows.append(["Career Stage Snapshot", career_stage])
    if start_year is not None:
        rows.append(["Year Starting Undergraduate School", str(start_year)])
    # Community-college path disclosure: without these rows the report shows a
    # single 4-year Cost of Attendance and a reduced loan with no explanation of
    # where the reduction came from. Only added when a CC path is active.
    if cc_info and cc_info.get("mode") in ("fulltime", "parttime"):
        _mode_label = ("Full-time, then transfer" if cc_info["mode"] == "fulltime"
                       else "Part-time while working, then transfer")
        rows.append([f"Community College Path ({cc_info['cc_years']} yrs)", _mode_label])
        rows.append(["Community College",
                     f"{cc_info['state_label']} — {fmt_money(cc_info['cost'])}/yr, paid out of "
                     f"pocket ({fmt_money(cc_info['oop'])} total, no loan)"])
    _coa_label = ("Cost of Attendance (per year, 4-year school)"
                  if cc_info and cc_info.get("mode") in ("fulltime", "parttime")
                  else "Cost of Attendance (per year)")
    rows += [
        [_coa_label, fmt_money(coa_per_year)],
        ["Personal Contribution (per year)", fmt_money(personal_contribution_per_year)],
        ["Grants & Scholarships (per year)", fmt_money(grants_per_year)],
        ["Average Loan Interest Rate", fmt_pct(interest_rate_pct)],
        ["Repayment Strategy", repayment_strategy_label],
    ]
    return rows


def _pdf_module_sections(module_context: dict, scenario_a: dict = None, major_name_a: str = None,
                          interest_rate_a: float = None, scenario_b: dict = None, major_name_b: str = None,
                          interest_rate_b: float = None, col_index: float = 100.0,
                          key_suffix_a: str = "a", key_suffix_b: str = "b",
                          roi_window_years: int = ROI_WINDOW_YEARS) -> list:
    """Optional PDF section(s) for whichever advanced modules were active --
    guarded per-module (see build_module_context) so a PDF generated with
    every module off is unchanged from before these modules existed.
    scenario_a/b, major_name_a/b, interest_rate_a/b, col_index, and
    key_suffix_a/b are only used to redraw the 2026-forecasting and
    Trade-Apprenticeship modules' chart images (recomputed here, never
    stored in module_context -- that dict is also spread directly into
    Supabase inserts elsewhere, so it must stay JSON-serializable
    scalars only, never a DataFrame or chart object)."""
    if not module_context:
        return []
    styles = _pdf_styles()
    elements = []
    if module_context.get("prestige_mode_active"):
        rows = [["Scenario", "Selected College Tier"], ["A", module_context.get("scenario_a_prestige_tier", "")]]
        if "scenario_b_prestige_tier" in module_context:
            rows.append(["B", module_context["scenario_b_prestige_tier"]])
        elements += [
            Spacer(1, 12), Paragraph("College Prestige & Cost Estimator", styles["section"]),
            _pdf_table(rows),
        ]
    if module_context.get("ai_mode_active"):
        rows = [["Scenario", "AI Task Exposure Risk Level"], ["A", module_context.get("scenario_a_ai_risk_level", "")]]
        if "scenario_b_ai_risk_level" in module_context:
            rows.append(["B", module_context["scenario_b_ai_risk_level"]])
        elements += [
            Spacer(1, 12), Paragraph("AI Employability Risk Analysis", styles["section"]),
            _pdf_table(rows),
        ]
    if module_context.get("future_forecasting_active"):
        rows = [["Scenario", "2026 Plan Selected"], ["A", module_context.get("future_plan_selected", "")]]
        if "scenario_b_future_plan_selected" in module_context:
            rows.append(["B", module_context["scenario_b_future_plan_selected"]])
        elements += [
            Spacer(1, 12), Paragraph("2026 Federal Loan Framework & Macro Forecasting", styles["section"]),
            _pdf_table(rows),
        ]
        if scenario_a is not None:
            for suffix, scenario, major_name, rate, plan_key in [
                (key_suffix_a, scenario_a, major_name_a, interest_rate_a, "future_plan_selected"),
                (key_suffix_b, scenario_b, major_name_b, interest_rate_b, "scenario_b_future_plan_selected"),
            ]:
                if scenario is None or plan_key not in module_context:
                    continue
                dependents = st.session_state.get(f"rap_dependents_{suffix}", 0)
                result, roi_2026 = compute_future_plan_result(
                    scenario, major_name, rate, module_context[plan_key], dependents, col_index=col_index,
                    roi_window_years=roi_window_years,
                )
                elements += [
                    Spacer(1, 12),
                    build_pdf_balance_chart(result["schedule"], module_context[plan_key]),
                    Spacer(1, 12),
                    build_pdf_roi_bar_chart(roi_2026["hs_net_position"], roi_2026["major_net_position"], major_name,
                                             roi_window_years),
                ]
    if module_context.get("apprenticeship_active"):
        elements += [
            Spacer(1, 12),
            Paragraph("Alternative Pathway: Trade Apprenticeship (Illustrative Benchmark)", styles["section"]),
            _pdf_table([
                [f"{roi_window_years}-Yr Net Position (COL-Adjusted)", "Earnings Premium vs. HS Grad"],
                [fmt_money(module_context["apprenticeship_net_position"]),
                 fmt_money(module_context["apprenticeship_earnings_premium"])],
            ]),
        ]
        if scenario_a is not None:
            elements += [
                Spacer(1, 12),
                build_pdf_scenario_comparison_roi_chart(
                    scenario_a["roi_result"]["hs_net_position"],
                    scenario_a["roi_result"]["major_net_position"], major_name_a,
                    module_context["apprenticeship_net_position"], module_context["apprenticeship_label"],
                    roi_window_years,
                ),
            ]
    return elements


def generate_pdf_report_single(major, city, school_name_a, in_state_a, career_stage_label,
                                coa_per_year_a, personal_contribution_per_year_a, grants_per_year_a,
                                interest_rate, repayment_strategy, loan_amount, loan_schedule_a,
                                scenario, take_home, gross, disposable_nominal,
                                disposable_col_adjusted, module_context: dict = None,
                                start_year_a=None, monthly_payment=None, col_index: float = 100.0,
                                roi_window_years: int = ROI_WINDOW_YEARS, cc_info_a=None) -> bytes:
    """PDF mirroring the on-screen single-scenario view: profile summary,
    Loan Information (+ per-year table + balance chart), Real-World
    Take-Home (+ take-home charts), and the Financial Position section (+ ROI
    chart)."""
    styles = _pdf_styles()
    repayment_result = scenario["repayment_result"]
    roi_result = scenario["roi_result"]

    story = [
        # Cover block: what this is, for whom, over what horizon. Previously
        # the report opened straight into "Your Profile" under a stock
        # centred Title, so a parent handed page 1 had no framing.
        Paragraph("Student Loan Payoff &amp; Major ROI Report", styles["cover_title"]),
        Paragraph(
            f"<b>{xml_escape(major)}</b> &nbsp;·&nbsp; {xml_escape(school_name_a or 'No school selected')}"
            f" &nbsp;·&nbsp; {xml_escape(city)}",
            styles["cover_sub"],
        ),
        Paragraph(
            f"Modelled over {roi_window_years} years after high school, against a debt-free "
            f"high school graduate. All figures adjusted for cost of living in {xml_escape(city)}.",
            styles["cover_sub"],
        ),
        Spacer(1, 8),
        _pdf_rule(),
        Spacer(1, 4),
        Paragraph("Your Profile", styles["section"]),
        _pdf_table(
            _pdf_profile_rows(major, school_name_a, in_state_a, coa_per_year_a,
                               personal_contribution_per_year_a, grants_per_year_a,
                               interest_rate, repayment_strategy, career_stage_label, city,
                               start_year=start_year_a, cc_info=cc_info_a),
            header=False, full_width=True,
        ),
        Spacer(1, 12),
        Paragraph(_strip_emoji(f"💳 Loan Information — {scenario['strategy_label']}"), styles["section"]),
        _pdf_table(full_width=True, rows=[
            ["Year", "Cost of Attendance", "Loan Amount This Year"],
            *[[f"{row['year']} ({start_year_a + row['year'] - 1})" if start_year_a is not None else row["year"],
               fmt_money(row["coa"]), fmt_money(row["loan_amount"])] for row in loan_schedule_a],
        ]),
        Spacer(1, 6),
        Paragraph(f"Total Loan Amount (all {UNDERGRAD_YEARS} years): {fmt_money(loan_amount)}", styles["body"]),
        Spacer(1, 6),
        _pdf_table(full_width=True, rows=[
            ["Monthly Payment", "Payoff Timeline", "Total Interest Paid"],
            [
                fmt_money(repayment_result["monthly_payment"]) if "monthly_payment" in repayment_result else "Varies (IDR)",
                f"{repayment_result['payoff_years']:.1f} yrs",
                fmt_money(repayment_result["total_interest"]),
            ],
        ]),
        Spacer(1, 12),
        build_pdf_balance_chart(repayment_result["schedule"], scenario["strategy_label"]),
        Spacer(1, 12),
        Paragraph(_strip_emoji(f"🏙️ Real-World Take-Home — {major}, {career_stage_label} in {city}"), styles["section"]),
        _pdf_table(full_width=True, rows=[
            ["Gross Salary", "Take-Home Pay (annual)", "Monthly Disposable", "COL-Adjusted Disposable"],
            [fmt_money(gross), fmt_money(take_home["net_take_home"]),
             fmt_money(disposable_nominal), fmt_money(disposable_col_adjusted)],
        ]),
    ]
    if gross > 0 and monthly_payment is not None:
        story += [
            Spacer(1, 12),
            build_pdf_takehome_pie_chart(take_home),
            Spacer(1, 12),
            build_pdf_takehome_vs_loan_chart(take_home["net_take_home"] / 12, monthly_payment),
        ]
    story += [
        Spacer(1, 12),
        Paragraph(_strip_emoji(f"📊 {roi_window_years}-Year Financial Position"), styles["section"]),
        _pdf_table([
            [f"High School Grad — {roi_window_years}-Yr Net Position",
             f"{major} — {roi_window_years}-Yr Net Position", "Earnings Premium (COL-Adjusted)"],
            [fmt_money(roi_result["hs_net_position"]), fmt_money(roi_result["major_net_position"]),
             fmt_money(roi_result["earnings_premium"])],
        ]),
        Paragraph(
            f"<b>Earnings Premium</b> is the bottom line: how much more (or less) money you would "
            f"have after {roi_window_years} years by going into {major} (after paying off the loan) "
            f"instead of skipping college and working as a debt-free high school graduate. It is the "
            f"difference between the two Net Position figures, both adjusted for the cost of living in "
            f"{city} -- that is what 'COL-Adjusted' means.",
            styles["caption"]),
        Spacer(1, 12),
        build_pdf_roi_bar_chart(roi_result["hs_net_position"], roi_result["major_net_position"], major,
                                 roi_window_years),
    ]

    # Mirrors the on-screen break-even banner -- same breakeven_summary call,
    # same heading and verdict word, so the report reads as the same section
    # the visitor saw rather than a stray headline. Silent for sub-bachelor's
    # occupations, exactly as on screen.
    breakeven = breakeven_summary(
        major, loan_amount, interest_rate, repayment_strategy,
        roi_window_years=roi_window_years, col_index=col_index,
        career_data_source=career_data_source,
        hs_wage_index=get_metro_wage_index(city),
        personal_contribution=scenario["personal_contribution"],
        enrollment_years=scenario["enrollment_years"],
        working_years=scenario["working_years"],
    )
    story += _pdf_breakeven_block(breakeven, styles)

    story += _pdf_module_sections(
        module_context, scenario_a=scenario, major_name_a=major, interest_rate_a=interest_rate,
        col_index=col_index, key_suffix_a="single", roi_window_years=roi_window_years,
    )
    # Only cite the professional-school sources when this major actually uses
    # them -- listing AAMC on a Software Developer's report is noise.
    story += _pdf_sources_section(
        styles, roi_window_years,
        uses_training_debt=bool(MAJOR_DATA.get(major, {}).get("additional_training_debt")
                                or MAJOR_DATA.get(major, {}).get("unpaid_training_years")),
        underemployment_majors=([(None, major)] if dataset_mode == DATASET_MODE_MAJOR else None),
    )

    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=letter).build(
        story, onFirstPage=_draw_pdf_header_footer, onLaterPages=_draw_pdf_header_footer,
    )
    return buffer.getvalue()


def _pdf_scenario_metrics_table(scenario: dict, roi_window_years: int) -> Table:
    repayment_result = scenario["repayment_result"]
    roi_result = scenario["roi_result"]
    return _pdf_table(full_width=True, rows=[
        ["Total Loan", "Monthly Payment", "Payoff Timeline", "Total Interest Paid",
         f"{roi_window_years}-Yr Earnings Premium (COL-Adj.)"],
        [
            fmt_money(scenario["effective_principal"]),
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
                                 module_context: dict = None, start_year_a=None, start_year_b=None,
                                 col_index: float = 100.0,
                                 roi_window_years: int = ROI_WINDOW_YEARS,
                                 loan_amount_a: float = 0.0, loan_amount_b: float = 0.0,
                                 career_data_source: str = "National",
                                 cc_info_a=None, cc_info_b=None) -> bytes:
    """PDF mirroring the on-screen Compare Mode view: both scenarios'
    profile summaries + metric tables, per-scenario break-even, plus the
    loan-balance and net-position comparison charts.

    loan_amount_a/b and career_data_source are explicit parameters, not read
    from module globals: the break-even's verdict compares the break-even
    against the slider loan, and grabbing whichever loan_amount the module
    last set would silently score Scenario B against A's loan. This function
    already takes each scenario's other inputs as A/B pairs for exactly this
    reason."""
    styles = _pdf_styles()
    story = [
        # Same cover treatment as the single-scenario report -- see the
        # comment there. The disclaimer isn't repeated in the body because
        # _draw_pdf_header_footer now carries it on every page.
        Paragraph("Student Loan Payoff &amp; Major ROI Report", styles["cover_title"]),
        Paragraph(
            f"<b>{xml_escape(scenario_a['major'])}</b> vs <b>{xml_escape(scenario_b['major'])}</b>"
            f" &nbsp;·&nbsp; {xml_escape(city)}",
            styles["cover_sub"],
        ),
        Paragraph(
            f"Two paths compared over {roi_window_years} years after high school, each against a "
            f"debt-free high school graduate. All figures adjusted for cost of living in {xml_escape(city)}.",
            styles["cover_sub"],
        ),
        Spacer(1, 8),
        _pdf_rule(),
        Spacer(1, 4),
        Paragraph(
            f"<b>Earnings Premium</b> (shown for each scenario below) is the bottom line: how much "
            f"more (or less) money you would have after {roi_window_years} years by taking that path "
            f"instead of skipping college and working as a debt-free high school graduate — with both "
            f"sides adjusted for the cost of living in {city} ('COL-Adjusted').",
            styles["caption"]),
        Spacer(1, 6),
        Paragraph(f"Scenario A: {scenario_a['major']} — {scenario_a['strategy_label']}", styles["section"]),
        _pdf_table(
            _pdf_profile_rows(major, school_name_a, in_state_a, coa_per_year_a,
                               personal_contribution_per_year_a, grants_per_year_a,
                               interest_rate, repayment_strategy, city_name=city,
                               start_year=start_year_a, cc_info=cc_info_a),
            header=False, full_width=True,
        ),
        Spacer(1, 6),
        _pdf_scenario_metrics_table(scenario_a, roi_window_years),
        # Per-scenario break-even, mirroring the on-screen compare panels. The
        # single report had this and the compare one silently didn't -- the
        # same one-branch-only gap the arm-parity fix already chased on screen.
        *_pdf_breakeven_block(
            breakeven_summary(major, loan_amount_a, interest_rate, repayment_strategy,
                              roi_window_years=roi_window_years, col_index=col_index,
                              career_data_source=career_data_source,
                              hs_wage_index=get_metro_wage_index(city),
                              personal_contribution=scenario_a["personal_contribution"],
                              enrollment_years=scenario_a["enrollment_years"],
                              working_years=scenario_a["working_years"]),
            styles, scenario_label="Scenario A"),
        Spacer(1, 12),
        Paragraph(f"Scenario B: {scenario_b['major']} — {scenario_b['strategy_label']}", styles["section"]),
        _pdf_table(
            _pdf_profile_rows(major_b, school_name_b, in_state_b, coa_per_year_b,
                               personal_contribution_per_year_b, grants_per_year_b,
                               interest_rate_b, repayment_strategy_b, city_name=city,
                               start_year=start_year_b, cc_info=cc_info_b),
            header=False, full_width=True,
        ),
        Spacer(1, 6),
        _pdf_scenario_metrics_table(scenario_b, roi_window_years),
        *_pdf_breakeven_block(
            breakeven_summary(major_b, loan_amount_b, interest_rate_b, repayment_strategy_b,
                              roi_window_years=roi_window_years, col_index=col_index,
                              career_data_source=career_data_source,
                              hs_wage_index=get_metro_wage_index(city),
                              personal_contribution=scenario_b["personal_contribution"],
                              enrollment_years=scenario_b["enrollment_years"],
                              working_years=scenario_b["working_years"]),
            styles, scenario_label="Scenario B"),
        Spacer(1, 12),
        build_pdf_comparison_balance_chart(
            scenario_a["repayment_result"]["schedule"], f"A: {scenario_a['major']}",
            scenario_b["repayment_result"]["schedule"], f"B: {scenario_b['major']}",
        ),
        Spacer(1, 12),
        build_pdf_scenario_comparison_roi_chart(
            scenario_a["roi_result"]["hs_net_position"],
            scenario_a["roi_result"]["major_net_position"], f"A: {scenario_a['major']}",
            scenario_b["roi_result"]["major_net_position"], f"B: {scenario_b['major']}",
            roi_window_years,
        ),
    ]
    story += _pdf_module_sections(
        module_context, scenario_a=scenario_a, major_name_a=major, interest_rate_a=interest_rate,
        scenario_b=scenario_b, major_name_b=major_b, interest_rate_b=interest_rate_b,
        col_index=col_index, roi_window_years=roi_window_years,
    )
    story += _pdf_sources_section(
        styles, roi_window_years,
        uses_training_debt=any(
            MAJOR_DATA.get(m, {}).get("additional_training_debt")
            or MAJOR_DATA.get(m, {}).get("unpaid_training_years")
            for m in (major, major_b)
        ),
        underemployment_majors=(
            [("Scenario A", major), ("Scenario B", major_b)]
            if dataset_mode == DATASET_MODE_MAJOR else None
        ),
    )

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
# Uses window.top, not window.parent -- Streamlit Community Cloud nests this
# component inside an additional wrapping iframe, so window.parent only
# reaches that intermediate frame (with its own internal /~/+/ URL) instead
# of the real page. That's exactly why this always fell back to UTC on the
# deployed app regardless of the visitor's actual timezone: the "tz" query
# param below was being written to the wrapper iframe's own address, never
# to the page's real URL that get_shared_default() actually reads. window.top
# always reaches the real outermost browsing context no matter how many
# iframe layers exist in between (confirmed via a live browser test against
# the deployed app).
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
            const doc = window.top.document;
            const buttons = doc.querySelectorAll("button");
            for (const b of buttons) {
                if (b.textContent.trim() === "Set Timezone") return b;
            }
            return null;
        }
        const detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
        const params = new URLSearchParams(window.top.location.search);
        if (params.get("tz") !== detected) {
            params.set("tz", detected);
            const newUrl = window.top.location.pathname + "?" + params.toString();
            window.top.history.replaceState(null, "", newUrl);
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
# ?test=1 marks this as a developer/test session: NONE of the interaction
# logging (pageview, scenario events, PDF/share/survey) is written to Supabase,
# so your own testing never contaminates the research data. Set before the
# pageview log below so even that first write is skipped, and made sticky for
# the whole session -- a later rerun that drops the query param (e.g. after
# "Share Scenario" rewrites the URL) keeps the flag on. Rides the same
# get_shared_default query-param mechanism as ?admin=1.
if "test_mode" not in st.session_state:
    st.session_state.test_mode = get_shared_default("test", "0") == "1"

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

# Three independent, optional modules -- each defaults off, and the app
# behaves exactly as it did before any of them existed when left off. Their
# widgets render at the bottom of the sidebar (after Career), but Financing
# below needs to know enable_prestige_mode before that point to decide
# whether to show a school lookup or a college-tier picker -- so each
# flag's current value is read from session_state here, before its widget
# exists, exactly like Career Salary Data's radio further down. See the
# Methodology footer for what each module models and, just as importantly,
# what it deliberately does NOT claim.
st.session_state.setdefault("enable_prestige_mode", False)
st.session_state.setdefault("enable_ai_mode", False)
st.session_state.setdefault("enable_future_proofing", False)
enable_prestige_mode = st.session_state["enable_prestige_mode"]
enable_ai_mode = st.session_state["enable_ai_mode"]
enable_future_proofing = st.session_state["enable_future_proofing"]
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
    # everything else in this section builds on that number. Many real
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
        help="The full sticker price for your first year (Year 1) at this "
             "school -- tuition, fees, room & board, books, everything -- "
             "before subtracting scholarships or what you pay yourself. "
             "Years 2-4 are projected from this using the estimated COA "
             "inflation rate below. Auto-fills if we found your school "
             "above.",
    )
start_year_options_a = list(range(now_local().year, now_local().year + 8))
shared_start_year_a = get_shared_int("start_year", now_local().year)
default_start_year_a_index = (
    start_year_options_a.index(shared_start_year_a) if shared_start_year_a in start_year_options_a else 0
)
start_year_a = st.sidebar.selectbox(
    "Year Starting Undergraduate School", start_year_options_a, index=default_start_year_a_index,
    key="start_year_a",
    help="If you won't start college right away, Cost of Attendance above "
         "gets projected forward to this year using the estimated COA "
         "inflation rate below, before growing further across all 4 years "
         "of enrollment. Leave at the current year for no adjustment.",
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
         "This amount does not need to be repaid back to the grantor.",
)
# Community-college path: None / full-time transfer / part-time while working.
# (Replaces the old single "Start at community college" checkbox; legacy shared
# links with cc_a=1 map to the full-time transfer mode.)
_legacy_cc_a = get_shared_default("cc_a", "0") == "1"
st.session_state.setdefault(
    "cc_mode_a", get_shared_default("cc_mode_a", "fulltime" if _legacy_cc_a else "none"))
cc_mode_a = st.sidebar.radio(
    "Community college path",
    options=["none", "fulltime", "parttime"],
    format_func=lambda c: {
        "none": "None — start at the 4-year school",
        "fulltime": "Full-time community college, then transfer (2+2)",
        "parttime": "Part-time community college while working, then transfer",
    }[c],
    key="cc_mode_a",
    help=f"Model the first {COMMUNITY_COLLEGE_YEARS} years at a community "
         "college, then transferring to the 4-year school above to finish the "
         "SAME bachelor's -- earnings and the degree are unchanged, only the "
         "cost of those years drops. Community college is assumed paid without "
         "loans (Pell/work/out-of-pocket), so it adds nothing to your debt. "
         "'Part-time while working' means you work full-time during the "
         "community-college years (earning, not foregoing income) -- its "
         "earnings advantage shows up when 'count foregone earnings' is on. "
         "Put a different path in each scenario to compare them. See Methodology.",
)
cc_transfer_a = cc_mode_a != "none"
is_parttime_a = cc_mode_a == "parttime"
cc_years_a = COMMUNITY_COLLEGE_YEARS if cc_transfer_a else 0
university_years_a = max(UNDERGRAD_YEARS - cc_years_a, 0)
if cc_transfer_a:
    # Default CC state: the selected 4-year school's state (you transfer within
    # a state), then the work city's state, then national. coa_match_a.get is a
    # graceful no-op until the college dataset carries STABBR (see
    # clean_college_scorecard.py) -- falls through to city/national meanwhile.
    _school_state_a = coa_match_a.get("STABBR") if coa_match_a is not None else None
    if _school_state_a not in US_STATES:
        _school_state_a = None
    _city_a = st.session_state.get("city_select")
    _city_state_a = CITY_DATA.get(_city_a, {}).get("state_key") if _city_a else None
    if _city_state_a not in US_STATES:
        _city_state_a = None
    _shared_state_a = get_shared_default("cc_state_a", "")
    _default_state_a = (_shared_state_a if _shared_state_a in US_STATES or _shared_state_a == "__national__"
                        else (_school_state_a or _city_state_a or "__national__"))
    st.session_state.setdefault("cc_state_a", _default_state_a)
    cc_state_key_a = st.sidebar.selectbox(
        "Community College State",
        ["__national__"] + sorted(US_STATES, key=lambda k: US_STATES[k]),
        format_func=lambda k: "National average" if k == "__national__" else US_STATES[k],
        key="cc_state_a",
        help="Community-college tuition varies widely by state. Defaults to "
             "your school's state (then your work city's), and sets the cost "
             "below. Source: NCES via the Education Data Initiative (2025).",
    )
    _state_cost_a = community_college_cost_for_state(
        None if cc_state_key_a == "__national__" else cc_state_key_a)
    # Cost auto-fills from the state, but a manual (or shared-link) override
    # survives until the state itself changes -- same pattern as the COA/loan
    # auto-fill below.
    st.session_state.setdefault("cc_coa_per_year_a", get_shared_int("cc_coa_a", int(_state_cost_a)))
    st.session_state.setdefault("cc_state_cost_seen_a", _state_cost_a)
    if st.session_state["cc_state_cost_seen_a"] != _state_cost_a:
        st.session_state["cc_state_cost_seen_a"] = _state_cost_a
        st.session_state["cc_coa_per_year_a"] = int(_state_cost_a)
    cc_coa_per_year_a = st.sidebar.number_input(
        "Community College Cost (per year, $)", min_value=0, max_value=100000, step=250,
        key="cc_coa_per_year_a",
        help="Average annual in-district tuition & fees for the selected "
             "state's community colleges (NCES). Edit to your local college. "
             "Paid out of pocket -- it is NOT added to the loan, but it does "
             "count as a real cost (personal contribution) in the ROI.",
    )
else:
    cc_coa_per_year_a = 0.0
    cc_state_key_a = "__national__"
# Loan amount is derived, not entered: Cost of Attendance minus whatever
# isn't borrowed, per financed year. With a community-college path the CC years
# add $0 to the loan (paid out of pocket) -- only the university_years are
# financed -- and the CC tuition becomes an out-of-pocket personal contribution.
control_type_a = coa_match_a["control_type"] if coa_match_a is not None else None
inflation_rate_a = (
    DEFAULT_COA_INFLATION_RATE if enable_prestige_mode
    else estimate_coa_inflation_rate(school_name_a, scorecard_api_key, control_type_a)
)
# Projects today's Cost of Attendance forward to the year enrollment
# actually starts, using the same real inflation estimate that already
# grows COA across the 4 undergrad years below -- a no-op (multiplier of
# exactly 1.0) when start_year_a is the current year, so this changes
# nothing for the common case of starting right away.
years_until_start_a = max(start_year_a - now_local().year, 0)
effective_coa_per_year_a = coa_per_year_a * (1 + inflation_rate_a) ** years_until_start_a
effective_cc_coa_per_year_a = cc_coa_per_year_a * (1 + inflation_rate_a) ** years_until_start_a
# Build the schedule once, then derive both the (university-only) loan and the
# CC out-of-pocket from it -- single source of truth, no drift.
_schedule_a = compute_loan_schedule_by_year(
    effective_coa_per_year_a, personal_contribution_per_year_a, grants_per_year_a, inflation_rate_a,
    cc_years=cc_years_a, cc_coa_per_year=effective_cc_coa_per_year_a, finance_cc_years=False)
computed_loan_amount_a = sum(r["loan_amount"] for r in _schedule_a)
cc_oop_a = sum(r["coa"] for r in _schedule_a if r["phase"] == "community_college")
# Foregone-earnings option (widget rendered further down; read from state, per
# this file's established before-the-widget pattern). enrollment_years extends
# the HS baseline; working_years credits the part-time CC years back to the
# major side. Both gate on the option; enrollment_years == UNDERGRAD_YEARS in
# every mode when it's on (cc_years + university_years), so no-CC is unchanged.
_foregone_on = st.session_state.get("count_foregone_earnings", False)
enrollment_years_a = (cc_years_a + university_years_a) if _foregone_on else 0
working_years_a = cc_years_a if (is_parttime_a and _foregone_on) else 0
# Double-count guard: the per-year family contribution applies only to the
# financed university years; the CC tuition (cc_oop_a) is a separate additive
# out-of-pocket cost. No CC (university_years=4, cc_oop=0) => pc_per_year*4,
# exactly the original value.
personal_contribution = personal_contribution_per_year_a * university_years_a + cc_oop_a
if years_until_start_a > 0:
    coa_projection_note = (
        f"Today's Year 1 COA: {fmt_money(coa_per_year_a)} → projected to "
        f"{fmt_money(effective_coa_per_year_a)} by {start_year_a} "
        f"(est. {fmt_pct(inflation_rate_a * 100)} COA inflation/yr × {years_until_start_a} yrs). "
    )
else:
    coa_projection_note = ""
if cc_transfer_a:
    _work_note_a = "working full-time, " if is_parttime_a else ""
    cc_note_a = (
        f"{COMMUNITY_COLLEGE_YEARS} yrs community college ({_work_note_a}"
        f"{fmt_money(effective_cc_coa_per_year_a)}/yr, no loan → {fmt_money(cc_oop_a)} out-of-pocket), "
        f"then {university_years_a} yrs at the 4-year school ({fmt_money(effective_coa_per_year_a)}/yr, financed). "
    )
else:
    cc_note_a = ""
st.sidebar.caption((
    f"{coa_projection_note}"
    f"{cc_note_a}"
    f"→ **{fmt_money(computed_loan_amount_a)}** loan, **{fmt_money(personal_contribution)}** personal "
    f"(incl. {fmt_money(cc_oop_a)} community college)"
    if cc_transfer_a else
    f"{coa_projection_note}"
    f"Year 1 ({start_year_a}): {fmt_money(effective_coa_per_year_a)} COA − "
    f"{fmt_money(personal_contribution_per_year_a)} personal "
    f"− {fmt_money(grants_per_year_a)} grants → est. {fmt_pct(inflation_rate_a * 100)} COA inflation/yr "
    f"→ over {UNDERGRAD_YEARS} years: **{fmt_money(computed_loan_amount_a)}** loan, **{fmt_money(personal_contribution)}** personal"
).replace("$", r"\$"))
# Pre-fills with the calculated total above, but the user can type any other
# amount to override it (e.g. the real total from a financial aid offer,
# which won't match this simplified per-year model exactly). Refreshes back
# to the calculated total whenever that total itself changes -- detected by
# comparing against the last calculated total this box was filled with, so
# a manual override survives reruns that don't touch COA/Personal
# Contribution/Grants/school, but not one that does -- matching how the
# Cost of Attendance field's own school auto-fill behaves (see
# _autofill_coa).
if st.session_state.get("computed_loan_amount_a_seen") != computed_loan_amount_a:
    st.session_state["computed_loan_amount_a_seen"] = computed_loan_amount_a
    st.session_state["loan_amount_a"] = int(computed_loan_amount_a)
st.session_state.setdefault("loan_amount_a", int(computed_loan_amount_a))
loan_amount = st.sidebar.number_input(
    "Total Loan Amount ($)", min_value=0, max_value=1000000, step=500,
    key="loan_amount_a",
    help="Pre-filled with the calculated total above (Cost of Attendance "
         "minus Personal Contribution and Grants & Scholarships, summed "
         "over 4 years). You can override this with any other amount -- "
         "for example, the real total from a financial aid offer -- and "
         "that amount is used for every calculation below instead of the "
         "calculated total.",
)
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

# How far out every comparison on this page looks. Was a fixed 10 years, and
# that horizon quietly decided the answer for any career that trains before
# it earns: Medicine spends 4 years in school and 3 in residency, so a
# 10-year view counts 3 years of an attending's salary against 7 of training
# -- while repaying med school inside the same window -- and reports a doctor
# as ~$146k BEHIND someone who skipped college. Stretch the window to 15 and
# the same model says +$469k. The old fixed number wasn't measuring that
# outcome so much as choosing it, which is the exact information asymmetry
# this tool exists to attack. Only 3 of 836 occupations model a training
# delay (Medicine, Law, Athletic Training), but they're the ones where the
# horizon matters most.
#
# Not to be confused with the "Standard 10-Year" repayment strategy above:
# that's a real 10-year loan term, and it doesn't move with this.
# setdefault + key rather than index=, so on_change can read the new value
# back out of session_state -- passing index= alongside key= also trips
# Streamlit's widget-default-conflict warning (see CLAUDE.md).
_shared_horizon = get_shared_int("horizon", ROI_WINDOW_YEARS)
st.session_state.setdefault(
    "roi_horizon_select",
    _shared_horizon if _shared_horizon in ROI_HORIZON_OPTIONS else ROI_WINDOW_YEARS,
)
roi_horizon_years = st.sidebar.selectbox(
    "ROI Horizon", ROI_HORIZON_OPTIONS, key="roi_horizon_select",
    on_change=log_horizon_change,
    format_func=lambda y: f"{y} years",
    help="How far into the future every comparison on this page looks. "
         "Careers that train before they earn (medicine, law) look worst at "
         "10 years, because that's mostly training -- try 20 or 30 to see "
         "the payoff those years are buying.",
)

st.sidebar.subheader("💼 Career")

# Which BLS OEWS geographic release backs the career dropdown below --
# National (every state combined into one nationwide figure per occupation)
# or California (that state's own wages, which run higher for many careers,
# e.g. tech and healthcare). Affects every curated-major lookup too, since
# MAJOR_DATA is rebuilt from this choice on every rerun -- picking a source
# here is a data-source preference for the whole session, not per-scenario.
# The widget itself renders at the bottom of this section (after Career
# Stage Snapshot) -- its value is read from session_state here, before that
# widget exists, so Target Profession's options below can be built from the
# right MAJOR_DATA even on the very first render.
#
# Career mode only: Major mode's NY Fed data is national, since the NY Fed
# publishes no state breakdown. The radio is disabled rather than hidden in
# Major mode, so the sidebar doesn't reflow on every toggle.
#
# Defaults to National, for three reasons. It's a publicly shared tool, so
# most visitors aren't Californian. It's the only geography Major mode can
# offer, so a National default means the two modes are comparable out of the
# box rather than differing by both dataset AND geography -- comparing them
# at a California default mixes the two, which overstates the major-vs-career
# gap by more than 2x. And the companion paper's simulation study is
# national, so this keeps the app's default figures and the paper's figures
# the same numbers.
career_source_options = ["National", "California"]
shared_career_source = get_shared_default("career_source", "National")
st.session_state.setdefault(
    "career_source_radio",
    shared_career_source if shared_career_source in career_source_options else "National",
)
career_data_source = st.session_state["career_source_radio"]
careers_csv_path = CAREERS_CSV_PATH_CA if career_data_source == "California" else CAREERS_CSV_PATH_NATIONAL

# Which question the visitor is asking: "what if I study X?" (Major, NY Fed's
# 73 majors) or "what if I become X?" (Career, BLS's 836 occupations). Read
# from session_state before its own widget renders, same as the career source
# above, so the Target Profession dropdown below can be built from the right
# dataset on the very first pass.
#
# Defaults to Major because that's the choice a 17-year-old is actually
# making -- they pick a major and a school, and the occupation is a
# consequence they're guessing at. Career mode is the richer dataset and
# stays one click away for anyone who does have a specific job in mind.
dataset_mode_options = [DATASET_MODE_MAJOR, DATASET_MODE_CAREER]
shared_dataset_mode = get_shared_default("mode", DATASET_MODE_MAJOR)
st.session_state.setdefault(
    "dataset_mode_radio",
    shared_dataset_mode if shared_dataset_mode in dataset_mode_options else DATASET_MODE_MAJOR,
)
dataset_mode = st.session_state["dataset_mode_radio"]

# City drives the wage dataset now, not just the cost-of-living index, so it
# must be resolved before MAJOR_DATA is built. Its widget renders further
# down (after Target Profession), hence the same read-from-session_state
# -first pattern used for the two controls above.
city_options = list(CITY_DATA.keys())
shared_city = get_shared_default("city", "San Francisco, CA")
st.session_state.setdefault(
    "city_select", shared_city if shared_city in city_options else "San Francisco, CA")
city = st.session_state["city_select"]

# Metro wages are Career mode only: the NY Fed publishes no geography, so
# Major mode's salaries are national and only its cost-of-living adjustment
# varies by city.
# city goes to BOTH modes. Career mode uses it to look up real per-metro
# occupation wages; Major mode uses it to scale its national NY Fed figures
# by the city's wage index. Passing it to only one was a bug: the HS baseline
# in calculate_roi is scaled by that same index either way, so withholding it
# here left Major mode comparing a NATIONAL graduate salary against a San
# Francisco high-school baseline -- the mirror image of the asymmetry this
# whole change exists to remove, and it drove Computer Science in SF to a
# -$122,146 premium.
MAJOR_DATA = build_major_data(careers_csv_path, mode=dataset_mode, city=city)

# Major mode is a single national dataset -- the NY Fed doesn't publish
# per-state figures -- so fall back to Career mode's data if the majors CSV
# is missing rather than rendering an empty dropdown.
if not MAJOR_DATA:
    MAJOR_DATA = build_major_data(careers_csv_path, mode=DATASET_MODE_CAREER, city=city)
    dataset_mode = DATASET_MODE_CAREER
    st.session_state["dataset_mode_radio"] = DATASET_MODE_CAREER

# Defaults below assume a popular, concrete profile (Software Developer in
# San Francisco, in-state at UC Berkeley, 10 years in) instead of generic
# empty/first-alphabetical values, so there's something real on screen
# before a visitor touches anything -- see get_suggested_coa_per_year()
# usage further up for how Cost of Attendance's default is derived from
# the same school/in-state choice rather than a flat placeholder.
major_options = sorted(MAJOR_DATA.keys())
_default_a = DEFAULT_SELECTION_A[dataset_mode]
shared_major = get_shared_default("major", _default_a)
# A shared link made in one mode can name a selection the other mode doesn't
# have ("Software Developers" simply isn't a major), so fall back through the
# mode's own default before giving up on index 0.
default_major_index = major_options.index(shared_major) if shared_major in major_options else (
    major_options.index(_default_a) if _default_a in major_options else 0
)
# on_change records that this visitor picked the major themselves, rather
# than being shown the default above. Without it a student whose profession
# genuinely IS the default looks identical in the data to one who never
# touched the dropdown at all -- same single row saying "Software
# Developers" -- and the research can't tell an answer from an absence. This
# is deliberately instrumentation rather than a forced choice: making the
# selectbox start empty would observe the same thing, at the cost of the
# blank-page-until-you-pick friction this app exists to avoid. See
# get_major_explicitly_selected (section 2b).
major = st.sidebar.selectbox(
    SELECTION_LABEL[dataset_mode], major_options, index=default_major_index,
    on_change=mark_major_explicitly_selected,
    help="Pick what you're evaluating -- this determines the salary numbers "
         "used everywhere else in the app. Instead of scrolling, click the "
         "box and type part of the name to jump straight to it.",
)
typical_education_a = MAJOR_DATA.get(major, {}).get("typical_education", "")
if typical_education_a in SUB_BACHELORS_EDUCATION_LEVELS:
    st.sidebar.caption((
        f"ℹ️ {major}'s typical entry-level education (BLS: "
        f"\"{typical_education_a}\") is below a bachelor's degree. This "
        "app's Cost of Attendance/loan model below still assumes 4 years "
        "of undergraduate cost -- see Alternative Pathway: Trade "
        "Apprenticeship (if enabled) for a comparison using this "
        "profession's real path instead."
    ).replace("$", r"\$"))
if enable_prestige_mode:
    # Apply the tier's salary premium (chosen above, in Financing) to
    # Scenario A's major -- see get_prestige_adjusted_major_name for why
    # this is a synthetic MAJOR_DATA entry rather than a new parameter
    # threaded through every calculation.
    major = get_prestige_adjusted_major_name(major, prestige_tier_a)

# No index= here: session_state already holds this widget's value (seeded via
# setdefault up in the Career section, where MAJOR_DATA needed it) and passing
# both would trigger Streamlit's widget-default-conflict warning.
city = st.sidebar.selectbox(
    "City / Metro Area", city_options, key="city_select",
    help="Where you plan to live and work after graduating. In Career mode "
         "this sets BOTH the wages (your metro's own BLS figures) and the "
         "cost-of-living adjustment -- so a higher-paying, pricier city can "
         "come out ahead or behind on its own merits. Major mode's wages are "
         "national, since the NY Fed publishes no per-city breakdown.",
)
# Computed here (not just where it's first used, further down) so it's
# available for every compute_scenario_results() call in section 5 --
# including Compare Mode's, which run before the Real-World Take-Home
# section that used to be the only place this was computed.
city_info = CITY_DATA[city]

# Which point in this major's career the Real-World Take-Home section
# (5d) snapshots -- has no functional dependency on School/In-State or
# Financing above, so its position here is purely about profile layout
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

# Rendered last in this section: its current value was already read from
# session_state above (before Target Profession) so MAJOR_DATA could be
# built in time. No index= here since session_state already holds this
# widget's value (seeded via setdefault above) -- passing both would
# trigger Streamlit's widget-policy warning.
# Same read-before-render pattern as Career Salary Data below: the value was
# taken from session_state up in Financing so the dropdown's options could be
# built from the right dataset on the first pass.
dataset_mode = st.sidebar.radio(
    "Choose by", dataset_mode_options, key="dataset_mode_radio",
    help="Major: what people who studied that subject actually earn, "
         "including those who ended up working outside it (NY Fed, 73 "
         "majors). This is the choice you're actually making at 17. "
         "Career: what people already doing a specific job earn (BLS, 836 "
         "occupations) -- richer, but it assumes you get that job.",
)
if dataset_mode == DATASET_MODE_MAJOR:
    st.sidebar.caption(
        "Salaries reflect everyone who studied this — including the "
        f"{UNDEREMPLOYMENT_OVERALL_PCT:.0f}% of graduates who end up in jobs that don't need a degree."
    )
else:
    st.sidebar.caption(
        "Salaries assume you land this job. Switch to **Major** to see what "
        "everyone who studied a subject earns, not just those working in it."
    )

career_data_source = st.sidebar.radio(
    "Career Salary Data", career_source_options, key="career_source_radio",
    disabled=(dataset_mode == DATASET_MODE_MAJOR),
    help="National: nationwide BLS OEWS wage estimates (cleaned_careers.csv). "
         "California: that state's own BLS OEWS wage estimates "
         "(cleaned_careers_ca.csv), generated via `data_pipeline.py ... --state CA`. "
         "Applies to Career mode only -- the NY Fed's per-major data is national.",
)

# Rendered last in the sidebar: each flag's current value was already read
# from session_state above (before Financing) so Financing could branch on
# it in time -- see that comment for why. No value= here since
# session_state already holds each widget's value (seeded via setdefault
# above) -- passing both would trigger Streamlit's widget-policy warning.
with st.sidebar.expander("🧪 Advanced Analysis Settings"):
    enable_prestige_mode = st.checkbox(
        "Enable College Prestige & Cost Estimator", key="enable_prestige_mode",
        help="Replace the manual school/Cost of Attendance fields above with "
             "a college-tier picker that also applies a modeled (not "
             "guaranteed) salary premium by tier -- see Methodology for "
             "sourcing and caveats.",
    )
    enable_ai_mode = st.checkbox(
        "Enable AI Employability Risk Analysis", key="enable_ai_mode",
        help="Show a modeled AI task-exposure estimate for your chosen "
             "major's occupation group, based on published research -- see "
             "Methodology.",
    )
    enable_future_proofing = st.checkbox(
        "Enable 2026 Regulatory & Macro Forecasting", key="enable_future_proofing",
        help="Preview the real 2026 federal repayment plans (Repayment "
             "Assistance Plan and Tiered Standard Plan) and a real "
             "cost-of-living comparison across cities -- see Methodology.",
    )
    enable_apprenticeship = st.checkbox(
        "Enable Trade Apprenticeship Comparison", key="enable_apprenticeship",
        help="Show a real DOL/BLS-sourced benchmark comparing a registered "
             "apprenticeship's 10-year financial position against your "
             "chosen major and a high school graduate -- see Methodology.",
    )
    enable_foregone_earnings = st.checkbox(
        "Count foregone earnings during enrollment", key="count_foregone_earnings",
        help=f"Charge the ~{UNDERGRAD_YEARS} years of wages a student gives up "
             "while enrolled full-time -- usually the single largest real cost "
             "of a degree, bigger than tuition -- against the degree. The "
             "debt-free high school graduate (and, when shown, the trade "
             "apprentice) is credited with those head-start years of income, "
             "so every path is compared from age 18 rather than from "
             "graduation. This lowers each degree's earnings premium and "
             "break-even. Off by default. See Methodology.",
    )

# The in-enrollment opportunity cost is now applied PER SCENARIO (its
# enrollment_years_a/_b and working_years_a/_b are computed up in the Scenario
# A/B financing blocks, which read this checkbox's value from
# st.session_state["count_foregone_earnings"] before the widget renders here --
# the established before-the-widget pattern). A single global setting couldn't
# express a part-time-CC scenario next to a direct one, so there's no global
# enrollment_years_setting anymore. `enable_foregone_earnings` is kept for any
# direct readers below.

st.sidebar.divider()

# Admin Analytics View is hidden by default -- a real (but invisible)
# Streamlit button is the only way to actually flip admin_revealed, since
# that's what makes the click go through Streamlit's normal widget/rerun
# machinery instead of trying to fake session state from raw JS. The
# injected script below just finds this button by its exact text and
# calls .click() on it when Ctrl+Shift+A is pressed; the CSS block hides
# its wrapping container so it's never visible or in the way. Listens on
# window.top, not window.parent -- Streamlit Community Cloud nests this
# component inside an additional wrapping iframe, so a listener on
# window.parent.document only ever sees keydown events that occur inside
# that intermediate frame, never the ones the visitor's browser actually
# dispatches on the real page. window.top always reaches the real
# outermost browsing context no matter how many iframe layers exist in
# between.
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
            const doc = window.top.document;
            const buttons = doc.querySelectorAll("button");
            for (const b of buttons) {
                if (b.textContent.trim() === "Reveal Admin Panel") return b;
            }
            return null;
        }
        window.top.document.addEventListener("keydown", function (e) {
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
# Initial state is randomly assigned (see get_experiment_arm) so the
# contrast-framing hypothesis is testable rather than confounded by who
# chooses to look. In the "contrast" arm the page loads with the
# dual-scenario view already open, pairing the default Software Developers
# against Scenario B's default Humanities; in "single" it loads as one
# scenario. Either way the visitor can toggle it freely -- this is the
# assigned condition, not an enforced one, so analysis is intent-to-treat.
#
# An explicit ?compare= from a shared link beats the randomiser: someone
# opening a link to a comparison must see that comparison, or the link is
# broken. Those sessions are identifiable (their arm and their initial
# state disagree) and belong outside the randomised analysis. Hence
# get_shared_default(..., None) -- distinguishing "absent" from "0", which
# a "0" fallback could not.
_shared_compare = get_shared_default("compare", None)
if _shared_compare is not None:
    _default_compare = _shared_compare == "1"
else:
    _default_compare = get_experiment_arm() == "contrast"
# setdefault + no value= is this file's established pattern for a keyed
# widget (see the session-state notes in CLAUDE.md): value= would apply
# only on first render anyway and triggers Streamlit's default-conflict
# warning once session_state holds the key.
st.session_state.setdefault("compare_mode", _default_compare)
compare_mode = st.sidebar.checkbox(
    "🔀 Compare Two Scenarios", key="compare_mode", on_change=log_compare_toggle,
    help="Turn this on to compare two different majors, schools, or loan "
         "setups side by side instead of looking at just one.",
)

if compare_mode:
    with st.sidebar.expander("⚖️ Scenario B (for comparison)", expanded=True):
        _default_b = DEFAULT_SELECTION_B[dataset_mode]
        shared_major_b = get_shared_default("major_b", _default_b)
        default_major_b_index = major_options.index(shared_major_b) if shared_major_b in major_options else (
            major_options.index(_default_b) if _default_b in major_options else 0
        )
        major_b = st.selectbox(
            SELECTION_LABEL[dataset_mode], major_options, index=default_major_b_index, key="major_b",
            help="Pick the career you're evaluating -- this determines the "
                 "salary numbers used everywhere else in the app. There are "
                 "hundreds of options, so instead of scrolling, click the "
                 "box and type part of your major or career to jump "
                 "straight to it.",
        )
        typical_education_b = MAJOR_DATA.get(major_b, {}).get("typical_education", "")
        if typical_education_b in SUB_BACHELORS_EDUCATION_LEVELS:
            st.caption((
                f"ℹ️ {major_b}'s typical entry-level education (BLS: "
                f"\"{typical_education_b}\") is below a bachelor's degree. "
                "This app's Cost of Attendance/loan model below still "
                "assumes 4 years of undergraduate cost -- see Alternative "
                "Pathway: Trade Apprenticeship (if enabled) for a "
                "comparison using this profession's real path instead."
            ).replace("$", r"\$"))

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
                help="The full sticker price for your first year (Year 1) at "
                     "this school -- tuition, fees, room & board, books, "
                     "everything -- before subtracting scholarships or what "
                     "you pay yourself. Years 2-4 are projected from this "
                     "using the estimated COA inflation rate below. "
                     "Auto-fills if we found your school above.",
            )
        start_year_options_b = list(range(now_local().year, now_local().year + 8))
        shared_start_year_b = get_shared_int("start_year_b", now_local().year)
        default_start_year_b_index = (
            start_year_options_b.index(shared_start_year_b) if shared_start_year_b in start_year_options_b else 0
        )
        start_year_b = st.selectbox(
            "Year Starting Undergraduate School", start_year_options_b, index=default_start_year_b_index,
            key="start_year_b",
            help="If you won't start college right away, Cost of Attendance above "
                 "gets projected forward to this year using the estimated COA "
                 "inflation rate below, before growing further across all 4 years "
                 "of enrollment. Leave at the current year for no adjustment.",
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
                 "borrow. This amount does not need to be repaid back to "
                 "the grantor.",
        )
        _legacy_cc_b = get_shared_default("cc_b", "0") == "1"
        st.session_state.setdefault(
            "cc_mode_b", get_shared_default("cc_mode_b", "fulltime" if _legacy_cc_b else "none"))
        cc_mode_b = st.radio(
            "Community college path",
            options=["none", "fulltime", "parttime"],
            format_func=lambda c: {
                "none": "None — start at the 4-year school",
                "fulltime": "Full-time community college, then transfer (2+2)",
                "parttime": "Part-time community college while working, then transfer",
            }[c],
            key="cc_mode_b",
            help=f"Model the first {COMMUNITY_COLLEGE_YEARS} years at a "
                 "community college, then transferring to finish the SAME "
                 "bachelor's. Community college is assumed paid without loans, "
                 "so it adds nothing to the debt. 'Part-time while working' "
                 "means you work full-time during the community-college years. "
                 "See Methodology.",
        )
        cc_transfer_b = cc_mode_b != "none"
        is_parttime_b = cc_mode_b == "parttime"
        cc_years_b = COMMUNITY_COLLEGE_YEARS if cc_transfer_b else 0
        university_years_b = max(UNDERGRAD_YEARS - cc_years_b, 0)
        if cc_transfer_b:
            _school_state_b = coa_match_b.get("STABBR") if coa_match_b is not None else None
            if _school_state_b not in US_STATES:
                _school_state_b = None
            _city_b = st.session_state.get("city_select")
            _city_state_b = CITY_DATA.get(_city_b, {}).get("state_key") if _city_b else None
            if _city_state_b not in US_STATES:
                _city_state_b = None
            _shared_state_b = get_shared_default("cc_state_b", "")
            _default_state_b = (_shared_state_b if _shared_state_b in US_STATES or _shared_state_b == "__national__"
                                else (_school_state_b or _city_state_b or "__national__"))
            st.session_state.setdefault("cc_state_b", _default_state_b)
            cc_state_key_b = st.selectbox(
                "Community College State",
                ["__national__"] + sorted(US_STATES, key=lambda k: US_STATES[k]),
                format_func=lambda k: "National average" if k == "__national__" else US_STATES[k],
                key="cc_state_b",
                help="Community-college tuition varies widely by state. Defaults "
                     "to your school's state (then work city's) and sets the "
                     "cost below. Source: NCES via Education Data Initiative.",
            )
            _state_cost_b = community_college_cost_for_state(
                None if cc_state_key_b == "__national__" else cc_state_key_b)
            st.session_state.setdefault("cc_coa_per_year_b", get_shared_int("cc_coa_b", int(_state_cost_b)))
            st.session_state.setdefault("cc_state_cost_seen_b", _state_cost_b)
            if st.session_state["cc_state_cost_seen_b"] != _state_cost_b:
                st.session_state["cc_state_cost_seen_b"] = _state_cost_b
                st.session_state["cc_coa_per_year_b"] = int(_state_cost_b)
            cc_coa_per_year_b = st.number_input(
                "Community College Cost (per year, $)", min_value=0, max_value=100000, step=250,
                key="cc_coa_per_year_b",
                help="Average annual in-district tuition & fees for the selected "
                     "state's community colleges (NCES). Paid out of pocket -- "
                     "not added to the loan, but counts as a real cost in the ROI.",
            )
        else:
            cc_coa_per_year_b = 0.0
            cc_state_key_b = "__national__"
        control_type_b = coa_match_b["control_type"] if coa_match_b is not None else None
        inflation_rate_b = (
            DEFAULT_COA_INFLATION_RATE if enable_prestige_mode
            else estimate_coa_inflation_rate(school_name_b, scorecard_api_key, control_type_b)
        )
        years_until_start_b = max(start_year_b - now_local().year, 0)
        effective_coa_per_year_b = coa_per_year_b * (1 + inflation_rate_b) ** years_until_start_b
        effective_cc_coa_per_year_b = cc_coa_per_year_b * (1 + inflation_rate_b) ** years_until_start_b
        _schedule_b = compute_loan_schedule_by_year(
            effective_coa_per_year_b, personal_contribution_per_year_b, grants_per_year_b, inflation_rate_b,
            cc_years=cc_years_b, cc_coa_per_year=effective_cc_coa_per_year_b, finance_cc_years=False)
        computed_loan_amount_b = sum(r["loan_amount"] for r in _schedule_b)
        cc_oop_b = sum(r["coa"] for r in _schedule_b if r["phase"] == "community_college")
        _foregone_on_b = st.session_state.get("count_foregone_earnings", False)
        enrollment_years_b = (cc_years_b + university_years_b) if _foregone_on_b else 0
        working_years_b = cc_years_b if (is_parttime_b and _foregone_on_b) else 0
        personal_contribution_b = personal_contribution_per_year_b * university_years_b + cc_oop_b
        if years_until_start_b > 0:
            coa_projection_note_b = (
                f"Today's Year 1 COA: {fmt_money(coa_per_year_b)} → projected to "
                f"{fmt_money(effective_coa_per_year_b)} by {start_year_b} "
                f"(est. {fmt_pct(inflation_rate_b * 100)} COA inflation/yr × {years_until_start_b} yrs). "
            )
        else:
            coa_projection_note_b = ""
        if cc_transfer_b:
            _work_note_b = "working full-time, " if is_parttime_b else ""
            cc_note_b = (
                f"{COMMUNITY_COLLEGE_YEARS} yrs community college ({_work_note_b}"
                f"{fmt_money(effective_cc_coa_per_year_b)}/yr, no loan → {fmt_money(cc_oop_b)} out-of-pocket), "
                f"then {university_years_b} yrs at the 4-year school ({fmt_money(effective_coa_per_year_b)}/yr, financed). "
            )
        else:
            cc_note_b = ""
        st.caption((
            f"{coa_projection_note_b}"
            f"{cc_note_b}"
            f"→ **{fmt_money(computed_loan_amount_b)}** loan, **{fmt_money(personal_contribution_b)}** personal "
            f"(incl. {fmt_money(cc_oop_b)} community college)"
            if cc_transfer_b else
            f"{coa_projection_note_b}"
            f"Year 1 ({start_year_b}): {fmt_money(effective_coa_per_year_b)} COA − "
            f"{fmt_money(personal_contribution_per_year_b)} personal "
            f"− {fmt_money(grants_per_year_b)} grants → est. {fmt_pct(inflation_rate_b * 100)} COA inflation/yr "
            f"→ over {UNDERGRAD_YEARS} years: **{fmt_money(computed_loan_amount_b)}** loan, **{fmt_money(personal_contribution_b)}** personal"
        ).replace("$", r"\$"))
        # See Scenario A's identical pattern (above) for why this compares
        # against the last-seen calculated total rather than always
        # resetting or never resetting.
        if st.session_state.get("computed_loan_amount_b_seen") != computed_loan_amount_b:
            st.session_state["computed_loan_amount_b_seen"] = computed_loan_amount_b
            st.session_state["loan_amount_b"] = int(computed_loan_amount_b)
        st.session_state.setdefault("loan_amount_b", int(computed_loan_amount_b))
        loan_amount_b = st.number_input(
            "Total Loan Amount ($)", min_value=0, max_value=1000000, step=500,
            key="loan_amount_b",
            help="Pre-filled with the calculated total above (Cost of "
                 "Attendance minus Personal Contribution and Grants & "
                 "Scholarships, summed over 4 years). You can override this "
                 "with any other amount -- for example, the real total from "
                 "a financial aid offer -- and that amount is used for "
                 "every calculation below instead of the calculated total.",
        )
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
    "**Free · anonymous · no sign-up** — an educational estimate, not financial "
    "advice. Salary and cost figures are illustrative."
)
if st.session_state.get("test_mode"):
    st.warning("🧪 **Test mode** (`?test=1`) — this session's interactions are **not** being logged to the research dataset.")
st.info(
    "👈 **Update your profile in the sidebar** -- profession, school, loan terms, "
    "anything. Everything below updates instantly as you change it, no button to click."
)

# Collapsed on purpose. This app's whole premise is that real numbers are on
# screen before you touch anything -- there is deliberately no "calculate"
# button -- so a guide that interrupts that is worse than no guide. Costs
# nothing to a visitor who doesn't want it, one click for one who's lost.
# The st.info above is the one-second version; this is the sixty-second one.
#
# Aimed at a student landing cold. Before this, a first-time visitor got three
# lines of orientation (title, disclaimer, the banner above) and everything
# else was either a hover-only help= tooltip -- invisible on a phone -- or
# 4,000 words of Methodology at the very bottom of the page.
with st.expander("❓ New here? Start with this"):
    st.markdown((
        f"""
**What this compares.** Two futures: you, after this major and this loan — and someone
who skipped college, took no debt, and started working right away. Both adjusted for what
it costs to live in your city. Everything on this page is some version of that one
comparison.

**What to do.** Pick your major and school on the left. Numbers update as you change
them. Nothing is saved, there's no login, and you can't break it — try the majors you're
actually deciding between.

**The two settings that change the answer most:**

- **Choose by: Major or Career.** *Major* is what everyone who studied that subject
  earns — including the {UNDEREMPLOYMENT_OVERALL_PCT:.0f}% of graduates who end up in jobs
  that don't need a degree. *Career* is what people already doing that job earn, which
  assumes you become one of them. Same nominal path, about $233,000 apart over 10 years.
  Major is the honest default; Career is the richer data.
- **ROI Horizon.** How far ahead to look. This matters more than it sounds: careers that
  train before they earn look terrible at 10 years, because 10 years is mostly training.
  Medicine comes out **$146,000 behind** a high school graduate at 10 years, and
  **$3.5 million ahead** at 30. Same data. The only thing that changed is where you stop
  counting.

**Two senses of "worth it."** This does answer whether a major is worth it *financially* —
whether the extra earnings beat the cost of the debt. That's the **"Is this debt worth it?"**
verdict at the top. What it can't answer is whether it's worth it *to you*: a field you'd
love for less money can easily beat a lucrative one you'd dread, and only you can weigh
that trade. Every number here is also an average for a whole major, not a prediction about
you personally. Sources and assumptions are in **📚 Methodology & Sources** at the bottom.
        """
    ).replace("$", r"\$"))

# Reserved here, at the top of the page, so the Download PDF Report / Share
# Scenario buttons render in this position even though the code that fills
# them in runs much later (after the PDF bytes and share params are actually
# computed) -- st.container() is position-anchored: content written into a
# container later in the script still renders wherever the container was
# first created, not wherever that code physically executes.
top_actions_container = st.container()

# The break-even verdict, anchored high on the page (same position-anchored
# st.container() trick as top_actions_container above): it's the one output a
# student can act on -- "is this debt worth it, yes or no" -- so it leads
# rather than sitting under the ROI chart where it was easy to miss. Filled
# from the single-scenario branch once the scenario is computed. Compare Mode
# leaves it empty and shows a per-column verdict in each panel instead, since
# a single top banner can't answer for two scenarios at once.
breakeven_banner_container = st.container()

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
                f"Out-of-state Cost of Attendance: {fmt_money(coa_match['out_of_state_coa'])} "
                f"({now_local().year})"
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


def render_takehome_block(scenario: dict, major_name: str, career_stage_key: int,
                           career_stage_label: str, city_name: str, city: dict,
                           show_charts: bool = True, heading: bool = True) -> dict:
    """Compute and render one scenario's take-home snapshot, returning the
    figures the caller needs (the PDF generators take gross/take_home/
    disposable/monthly_payment as arguments).

    Extracted so Compare Mode can render it too. It previously couldn't:
    compare_mode took a separate branch that skipped take-home entirely, and
    since that branch is the randomly assigned contrast arm, half of all
    visitors never saw their disposable income at all. Copying the block into
    the compare branch instead of extracting it is exactly the drift this
    codebase already warns about (see CLAUDE.md on the chart twins), so both
    branches call this.

    show_charts=False drops the two pie charts for the narrow Compare Mode
    columns while keeping every NUMBER. That's the one deliberate asymmetry
    left between the arms: the figures are identical, the redundant chart is
    not repeated four times on one page. The charts encode the same split the
    ratio metric states numerically, so no information is lost.
    """
    if heading:
        st.subheader(f"🏙️ Real-World Take-Home — {major_name}, {career_stage_label} in {city_name}")

    gross = get_annual_salary_for_year(major_name, career_stage_key)
    take_home = calculate_take_home_pay(gross, city["state_key"], city["local_tax_rate"])
    target_month = (career_stage_key + 1) * 12
    monthly_payment = get_monthly_payment_for_stage(
        scenario["repayment_result"], scenario["strategy_label"], target_month)
    disposable_nominal = take_home["net_take_home"] / 12 - monthly_payment
    disposable_col_adjusted = adjust_for_cost_of_living(disposable_nominal, city["col_index"])

    if gross == 0:
        st.info(f"At this career stage, {major_name} has $0 gross income (still in training) — see Methodology for why.")

    # Four across when there's a full page; stacked inside a Compare column,
    # where four metrics side by side would be unreadable.
    cols = st.columns(4) if show_charts else st.columns(2)
    cols[0].metric("Gross Salary", fmt_money(gross))
    cols[1].metric(
        "Take-Home Pay (annual, after tax)",
        fmt_money(take_home["net_take_home"]),
        delta=fmt_pct(take_home["effective_tax_rate"] * 100) + " effective tax rate" if gross > 0 else None,
    )
    cols[2 if show_charts else 0].metric("Monthly Disposable Income", fmt_money(disposable_nominal))
    cols[3 if show_charts else 1].metric(
        "COL-Adjusted Disposable Income", fmt_money(disposable_col_adjusted),
        help="Normalized to national-average purchasing power, so cities are comparable",
    )

    if not take_home["state_modeled"]:
        st.caption("State tax: N/A (National Average city has no specific state to model)")
    if disposable_nominal < 0:
        st.warning("At this salary, city, and loan combination, disposable income is negative.")

    monthly_net_take_home = take_home["net_take_home"] / 12
    if gross > 0:
        if show_charts:
            st.plotly_chart(build_takehome_pie_chart(take_home),
                             use_container_width=True, config=PLOTLY_CHART_CONFIG)
            st.plotly_chart(
                build_takehome_vs_loan_chart(monthly_net_take_home, monthly_payment),
                use_container_width=True, config=PLOTLY_CHART_CONFIG,
                key=f"takehome_vs_loan_{major_name}_{career_stage_key}",
            )
        # Student Loan Payment / Take-Home Pay -- the same split the charts
        # encode visually, stated as a number. Guarded against a $0 take-home
        # edge case rather than assuming gross > 0 implies positive net pay.
        ratio = monthly_payment / monthly_net_take_home * 100 if monthly_net_take_home > 0 else None
        if ratio is not None:
            risk = get_loan_to_income_risk_tier(ratio, take_home["effective_tax_rate"])
            st.markdown(
                f"""
                <div>
                    <div style="font-size: 0.875rem; color: #808495;">Student Loan Payment / Take-Home Ratio</div>
                    <div style="font-size: 2rem; font-weight: 600; color: {risk['color']}; line-height: 1.2;">
                        {fmt_pct(ratio)}
                    </div>
                    <div style="font-size: 0.8rem; color: {risk['color']};">{risk['tier']}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            # A hover title="..." tooltip is invisible on touch devices -- shown
            # as a permanent caption instead, matching every other explanation
            # in this app. Abbreviated in the narrow Compare columns.
            if show_charts:
                st.caption((
                    f"This is a ratio: your monthly loan payment as a percentage of your "
                    "monthly take-home pay (the same split shown in the chart above). "
                    "Industry guideline (converted to your take-home basis using this "
                    "scenario's own effective tax rate): under "
                    f"{fmt_pct(risk['manageable_threshold'])} is considered manageable "
                    "(common student-loan-budgeting guidance -- e.g. SoFi); over "
                    f"{fmt_pct(risk['caution_threshold'])} matches the standard 36%-of-gross-"
                    "income \"qualified borrower\" debt-to-income ceiling mortgage lenders use "
                    "for ALL debts combined. Over 100% means the payment exceeds your take-home "
                    "pay."
                ))
            else:
                st.caption(
                    f"Monthly loan payment as a share of monthly take-home pay. Under "
                    f"{fmt_pct(risk['manageable_threshold'])} is considered manageable."
                )
        else:
            st.metric("Student Loan Payment / Take-Home Ratio", "N/A")

    return {
        "gross": gross, "take_home": take_home, "monthly_payment": monthly_payment,
        "disposable_nominal": disposable_nominal,
        "disposable_col_adjusted": disposable_col_adjusted,
    }


def render_scenario_panel(column, scenario: dict, label: str, roi_window_years: int,
                           loan_amount: float, interest_rate: float, repayment_strategy: str,
                           col_index: float, career_data_source_name: str,
                           hs_wage_index: float = 1.0):
    """Render one scenario's metric cards, break-even and underemployment note
    into a layout column. Used twice by Compare Mode (Scenario A / Scenario B)
    so their markup can't drift apart from being hand-copied -- this is the
    same card layout section 5c uses for the single-scenario view, just
    parameterized and column-scoped.

    roi_window_years is a required parameter rather than a global read: the
    metric below used to hardcode "10-Year Earnings Premium", which silently
    mislabelled a 30-year figure as 10-year once the horizon became
    selectable. The label has to move with the number.

    Carries the break-even because Compare Mode previously had none -- and
    Compare Mode is the randomly assigned contrast arm, so half of all
    visitors were losing the single most decision-relevant output in the app
    purely by coin flip. That made the two arms differ by five features rather
    than by the contrast alone, which is what the paper's H2 claims to
    measure. Two break-evens side by side is also the better version of the
    feature: it's exactly the comparison the contrast condition exists to
    provoke.
    """
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
            f"{roi_window_years}-Year Earnings Premium (COL-Adjusted)",
            fmt_money(roi_result["earnings_premium"]),
            delta=fmt_pct(roi_result["roi_pct"]) + " ROI" if roi_result["roi_pct"] is not None else None,
        )
        if repayment_result["forgiven_amount"] > 0:
            st.warning(
                f"{fmt_money(repayment_result['forgiven_amount'])} forgiven after {IDR_MAX_TERM_YEARS} years."
            )

        # loan_amount/interest_rate/repayment_strategy are parameters rather
        # than globals because Scenario B has its own -- reading the globals
        # here would silently compute B's break-even from A's loan.
        breakeven = breakeven_summary(
            scenario["major"], loan_amount, interest_rate, repayment_strategy,
            roi_window_years=roi_window_years, col_index=col_index,
            career_data_source=career_data_source_name,
            hs_wage_index=hs_wage_index,
            personal_contribution=scenario["personal_contribution"],
            enrollment_years=scenario["enrollment_years"],
        )
        if breakeven["headline"]:
            st.markdown("**🎯 Is this debt worth it?**")
            st.markdown(breakeven["headline"].replace("$", r"\$"))
            st.caption(breakeven["detail"].replace("$", r"\$"))

        # Major mode's underemployment rate is per-major, so A and B carry
        # genuinely different numbers and each column needs its own. Career
        # mode's text is national and identical for both, so the compare
        # branch renders that once below the columns instead of twice -- see
        # the call site.
        if dataset_mode == DATASET_MODE_MAJOR:
            st.caption(underemployment_disclosure(scenario["major"]))


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


def compute_future_plan_result(scenario: dict, major_name: str, interest_rate: float,
                                future_plan: str, dependents: int, col_index: float = 100.0,
                                roi_window_years: int = ROI_WINDOW_YEARS) -> tuple:
    """Recomputes a 2026 plan's repayment schedule + ROI position -- shared
    by the on-screen render (_render_plan, inside
    render_future_proofing_section) and the PDF's module-section chart
    building, so both call the exact same numbers instead of risking
    drift between two copies of this logic. Pure function, no Streamlit
    widget calls, safe to call a second time outside the on-screen
    closure with the same inputs."""
    effective_principal = scenario["effective_principal"]
    if future_plan == "2026 Tiered Standard Plan":
        term_years = calculate_tiered_standard_term(effective_principal)
        result = calculate_standard_repayment(effective_principal, interest_rate, term_years,
                                               roi_window_years=roi_window_years)
    else:
        result = simulate_rap_schedule(effective_principal, interest_rate, major_name, dependents,
                                        roi_window_years=roi_window_years)
    roi_result_2026 = calculate_roi(major_name, result["total_paid_in_roi_window"],
                                     scenario["total_investment"], col_index=col_index,
                                     years=roi_window_years)
    return result, roi_result_2026


def render_future_proofing_section(scenario_a: dict, major_name_a: str, interest_rate_a: float,
                                    scenario_b: dict = None, major_name_b: str = None,
                                    interest_rate_b: float = None, col_index: float = 100.0,
                                    roi_window_years: int = ROI_WINDOW_YEARS) -> dict:
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
        dependents = 0
        if future_plan == plan_options[0]:
            result, roi_result_2026 = compute_future_plan_result(
                scenario, major_name, interest_rate, future_plan, dependents, col_index=col_index,
                roi_window_years=roi_window_years,
            )
            term_years = calculate_tiered_standard_term(effective_principal)
            cols = st.columns(3)
            cols[0].metric("Fixed Term (by balance)", f"{term_years} yrs")
            cols[1].metric("Monthly Payment", fmt_money(result["monthly_payment"]))
            cols[2].metric("Total Interest Paid", fmt_money(result["total_interest"]))
        else:
            dependents = st.number_input(
                "Dependents", min_value=0, max_value=10, value=0, key=f"rap_dependents_{key_suffix}",
                help="Reduces your RAP payment by $50/month per dependent (real OBBBA provision).",
            )
            result, roi_result_2026 = compute_future_plan_result(
                scenario, major_name, interest_rate, future_plan, dependents, col_index=col_index,
                roi_window_years=roi_window_years,
            )
            gross_year1 = get_annual_salary_for_year(major_name, 0)
            rap = calculate_rap_payment(gross_year1, dependents)
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

        st.plotly_chart(
            build_balance_chart(result["schedule"], future_plan),
            use_container_width=True, key=f"future_balance_chart_{key_suffix}", config=PLOTLY_CHART_CONFIG,
        )

        st.markdown(f"**{roi_window_years}-Year Financial Position Under This Plan**")
        st.caption(
            "Recomputed using this 2026 plan's actual payments, instead of "
            "your selected Repayment Strategy above -- same COL-adjusted "
            f"comparison as the main {roi_window_years}-Year Financial Position section."
        )
        pos_cols = st.columns(3)
        pos_cols[0].metric(
            f"High School Grad — {roi_window_years}-Yr Net Position (No Loan)",
            fmt_money(roi_result_2026["hs_net_position"]),
        )
        pos_cols[1].metric(f"{major_name} — {roi_window_years}-Yr Net Position", fmt_money(roi_result_2026["major_net_position"]))
        pos_cols[2].metric(
            "Earnings Premium (COL-Adjusted)", fmt_money(roi_result_2026["earnings_premium"]),
            delta=fmt_pct(roi_result_2026["roi_pct"]) + " ROI" if roi_result_2026["roi_pct"] is not None else None,
        )
        st.plotly_chart(
            build_roi_bar_chart(roi_result_2026["hs_net_position"], roi_result_2026["major_net_position"], major_name,
                                 roi_window_years),
            use_container_width=True, key=f"future_roi_chart_{key_suffix}", config=PLOTLY_CHART_CONFIG,
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
    render_centered_table(pd.DataFrame(col_rows))

    return context


def render_apprenticeship_section(scenario_a: dict, major_name_a: str, col_index: float = 100.0,
                                   roi_window_years: int = ROI_WINDOW_YEARS) -> dict:
    """Alternative Pathway: Trade Apprenticeship (Illustrative Benchmark) --
    independent module. When major_name_a itself typically requires less
    than a bachelor's degree (per BLS's typical-entry-level-education data,
    see SUB_BACHELORS_EDUCATION_LEVELS), shows that profession's own real
    BLS earnings without this app's usual 4-year-loan assumption instead of
    the generic national trade benchmark -- a more specific, more accurate
    comparison for that exact path. See APPRENTICESHIP_* constants and
    calculate_apprenticeship_roi for the generic benchmark's sourcing."""
    st.subheader("🔨 Alternative Pathway: Trade Apprenticeship (Illustrative Benchmark)")
    typical_education = MAJOR_DATA.get(major_name_a, {}).get("typical_education", "")
    uses_own_profession_data = typical_education in SUB_BACHELORS_EDUCATION_LEVELS
    # Read the foregone-earnings assumption off the scenario it's compared
    # against, so the trade path and the degree share one age-18 timeline (see
    # calculate_roi / calculate_apprenticeship_roi). 0 when the option is off.
    # working_years deliberately does NOT propagate here: it's a major-side-only
    # effect of the degree-seeker's part-time community college, and the
    # apprentice path (and the shared hs_net_position, which is unchanged across
    # CC modes) is independent of whether the degree was reached via CC.
    enrollment_years = scenario_a.get("enrollment_years", 0)

    if uses_own_profession_data:
        st.caption(
            f"{major_name_a}'s typical entry-level education (BLS: "
            f"\"{typical_education}\") is below a bachelor's degree, so this "
            "shows YOUR chosen profession's own real BLS earnings without "
            "the 4-year loan this app otherwise assumes -- not the generic "
            "national trade benchmark. See Methodology for citations."
        )
        quick_facts = st.columns(2)
        quick_facts[0].metric("Starting Salary (BLS)", fmt_money(MAJOR_DATA[major_name_a]["starting_salary"]))
        quick_facts[1].metric("Assumed Loan for This Path", fmt_money(0))
        # A sub-bachelor's profession works immediately -- it forgoes no
        # enrollment years -- so the option must not penalise it. Extending
        # BOTH sides by enrollment_years (via a longer window, enrollment_years
        # left at 0) keeps the age-18 timeline consistent with the degree while
        # charging this path no foregone-earnings gap.
        alt_result = calculate_roi(major_name_a, 0, 0, col_index=col_index,
                                   years=roi_window_years + enrollment_years)
        alt_net_position = alt_result["major_net_position"]
        alt_earnings_premium = alt_result["earnings_premium"]
        alt_label = f"{major_name_a} (No 4-Yr Loan)"
    else:
        st.caption(
            "A single national reference point from U.S. Dept. of Labor sources "
            "-- not this app's per-major BLS pipeline -- for comparing college "
            "against not going. Typical program length ranges 1-6 years "
            "depending on the trade. See Methodology for exact citations and "
            "caveats."
        )
        quick_facts = st.columns(4)
        quick_facts[0].metric("Year 1 Training Wage", fmt_money(APPRENTICESHIP_YEAR1_SALARY))
        quick_facts[1].metric("Starting Salary After Completion", fmt_money(APPRENTICESHIP_COMPLETION_SALARY))
        quick_facts[2].metric("Typical Program Length", f"~{APPRENTICESHIP_TRAINING_YEARS} yrs")
        quick_facts[3].metric("Typical Training Debt", fmt_money(APPRENTICESHIP_TYPICAL_DEBT))
        apprenticeship_result = calculate_apprenticeship_roi(
            scenario_a["roi_result"]["hs_net_position"], col_index=col_index,
            years=roi_window_years, enrollment_years=enrollment_years,
        )
        alt_net_position = apprenticeship_result["apprentice_net_position"]
        alt_earnings_premium = apprenticeship_result["earnings_premium"]
        alt_label = "Trade Apprenticeship"

    st.markdown(f"**{roi_window_years}-Year Financial Position: {alt_label} vs. Your Path**")
    st.caption(
        "Same High School Graduate baseline and cost-of-living adjustment "
        "used everywhere else on this page." if uses_own_profession_data else
        "Same High School Graduate baseline and cost-of-living adjustment "
        "used everywhere else on this page -- apprenticeship earnings ramp "
        "from the training wage to the completion salary above, then grow "
        "at this app's existing high-school-grad wage-growth assumption "
        "(no BLS per-occupation trajectory exists past completion)."
    )
    pos_cols = st.columns(3)
    pos_cols[0].metric(
        f"High School Grad — {roi_window_years}-Yr Net Position (No Loan)",
        fmt_money(scenario_a["roi_result"]["hs_net_position"]),
    )
    pos_cols[1].metric(
        f"{major_name_a} — {roi_window_years}-Yr Net Position", fmt_money(scenario_a["roi_result"]["major_net_position"]),
    )
    pos_cols[2].metric(f"{alt_label} — {roi_window_years}-Yr Net Position", fmt_money(alt_net_position))
    st.plotly_chart(
        build_scenario_comparison_roi_chart(
            scenario_a["roi_result"]["hs_net_position"],
            scenario_a["roi_result"]["major_net_position"], major_name_a,
            alt_net_position, alt_label, roi_window_years,
        ),
        use_container_width=True, key="apprenticeship_roi_chart", config=PLOTLY_CHART_CONFIG,
    )

    return {
        "apprenticeship_active": True,
        "apprenticeship_net_position": alt_net_position,
        "apprenticeship_earnings_premium": alt_earnings_premium,
        "apprenticeship_used_profession_data": uses_own_profession_data,
        "apprenticeship_label": alt_label,
    }


def build_module_context(prestige_tier_a=None, prestige_tier_b=None,
                          ai_context: dict = None, future_context: dict = None,
                          apprenticeship_context: dict = None) -> dict:
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
    if apprenticeship_context:
        context.update(apprenticeship_context)
    return context


if compare_mode:
    st.subheader("⚖️ Scenario Comparison")
    scenario_a = compute_scenario_results(major, loan_amount, interest_rate, repayment_strategy,
                                           personal_contribution, city_info["col_index"],
                                           roi_window_years=roi_horizon_years,
                                           hs_wage_index=get_metro_wage_index(city),
                                           enrollment_years=enrollment_years_a,
                                           working_years=working_years_a)
    scenario_b = compute_scenario_results(major_b, loan_amount_b, interest_rate_b, repayment_strategy_b,
                                           personal_contribution_b, city_info["col_index"],
                                           roi_window_years=roi_horizon_years,
                                           hs_wage_index=get_metro_wage_index(city),
                                           enrollment_years=enrollment_years_b,
                                           working_years=working_years_b)

    col_a, col_b = st.columns(2)
    render_scenario_panel(
        col_a, scenario_a, "A", roi_horizon_years,
        loan_amount, interest_rate, repayment_strategy,
        city_info["col_index"], career_data_source,
        hs_wage_index=get_metro_wage_index(city),
    )
    render_scenario_panel(
        col_b, scenario_b, "B", roi_horizon_years,
        loan_amount_b, interest_rate_b, repayment_strategy_b,
        city_info["col_index"], career_data_source,
        hs_wage_index=get_metro_wage_index(city),
    )

    # Career mode's underemployment text is national and identical for both
    # scenarios, so it renders once here rather than twice inside the panels.
    # Major mode's is per-major and lives in the panel instead.
    if dataset_mode == DATASET_MODE_CAREER:
        st.info(underemployment_disclosure(None))

    # Take-home, per scenario. Compare Mode had none of this: the contrast arm
    # is randomly assigned, so half of all visitors never saw their disposable
    # income, which made the two arms differ by more than the contrast H2
    # claims to measure. Charts off -- the columns are narrow and the same
    # split is stated numerically by the ratio metric.
    st.subheader(f"🏙️ Real-World Take-Home — {career_stage_label} in {city}")
    th_col_a, th_col_b = st.columns(2)
    with th_col_a:
        st.markdown(f"**A: {scenario_a['major']}**")
        render_takehome_block(scenario_a, major, career_stage_key, career_stage_label,
                               city, city_info, show_charts=False, heading=False)
    with th_col_b:
        st.markdown(f"**B: {scenario_b['major']}**")
        render_takehome_block(scenario_b, major_b, career_stage_key, career_stage_label,
                               city, city_info, show_charts=False, heading=False)

    st.plotly_chart(
        build_comparison_balance_chart(
            scenario_a["repayment_result"]["schedule"], f"A: {scenario_a['major']}",
            scenario_b["repayment_result"]["schedule"], f"B: {scenario_b['major']}",
        ),
        use_container_width=True, config=PLOTLY_CHART_CONFIG,
    )
    st.plotly_chart(
        build_scenario_comparison_roi_chart(
            scenario_a["roi_result"]["hs_net_position"],
            scenario_a["roi_result"]["major_net_position"], f"A: {scenario_a['major']}",
            scenario_b["roi_result"]["major_net_position"], f"B: {scenario_b['major']}",
            roi_horizon_years,
        ),
        use_container_width=True, config=PLOTLY_CHART_CONFIG,
    )

    ai_context = {}
    if enable_ai_mode:
        ai_context = render_ai_risk_section(major, major_b)

    future_context = {}
    if enable_future_proofing:
        future_context = render_future_proofing_section(scenario_a, major, interest_rate,
                                                          scenario_b, major_b, interest_rate_b,
                                                          col_index=city_info["col_index"], roi_window_years=roi_horizon_years)

    apprenticeship_context = {}
    if enable_apprenticeship:
        apprenticeship_context = render_apprenticeship_section(
            scenario_a, major, col_index=city_info["col_index"], roi_window_years=roi_horizon_years)

    module_context = build_module_context(
        prestige_tier_a if enable_prestige_mode else None,
        prestige_tier_b if enable_prestige_mode else None,
        ai_context, future_context, apprenticeship_context,
    )

    # Runs on every rerun; maybe_log_scenario_event dedupes so only an actual
    # major/school change writes a row. Built here rather than inside the
    # download/share on_click lambdas below because a switch is an event in
    # its own right -- most visitors who change their mind never click
    # anything afterward, and those are exactly the ones worth recording.
    maybe_log_scenario_event({**build_scenario_context(
        major, loan_amount, interest_rate, repayment_strategy, personal_contribution,
        school_name_a, inflation_rate_a, grants_per_year_a, scenario_a,
        roi_horizon_years=roi_horizon_years,
        compare_mode=True, major_b=major_b, loan_amount_b=loan_amount_b,
        interest_rate_b=interest_rate_b, repayment_strategy_b=repayment_strategy_b,
        personal_contribution_b=personal_contribution_b, school_name_b=school_name_b,
        inflation_rate_b=inflation_rate_b, grants_per_year_b=grants_per_year_b,
        scenario_b=scenario_b, start_year_a=start_year_a, start_year_b=start_year_b,
    ), **module_context})

    compare_pdf_bytes = generate_pdf_report_compare(
        city, major, school_name_a, in_state_a, coa_per_year_a, personal_contribution_per_year_a,
        grants_per_year_a, interest_rate, repayment_strategy, scenario_a,
        major_b, school_name_b, in_state_b, coa_per_year_b, personal_contribution_per_year_b,
        grants_per_year_b, interest_rate_b, repayment_strategy_b, scenario_b,
        module_context=module_context, start_year_a=start_year_a, start_year_b=start_year_b,
        col_index=city_info["col_index"], roi_window_years=roi_horizon_years,
        loan_amount_a=loan_amount, loan_amount_b=loan_amount_b,
        career_data_source=career_data_source,
        cc_info_a=_cc_info_for_pdf(cc_mode_a, cc_state_key_a, effective_cc_coa_per_year_a, cc_oop_a, cc_years_a),
        cc_info_b=_cc_info_for_pdf(cc_mode_b, cc_state_key_b, effective_cc_coa_per_year_b, cc_oop_b, cc_years_b),
    )
    with top_actions_container:
        compare_pdf_col, compare_share_col = st.columns(2)
        compare_pdf_col.download_button(
            "📄 Download PDF Report", data=compare_pdf_bytes,
            file_name=f"{major.replace(' ', '_')}_vs_{major_b.replace(' ', '_')}_comparison_report.pdf",
            mime="application/pdf", use_container_width=True, key="download_pdf_compare",
            on_click=lambda: save_pdf_download({**build_scenario_context(
                major, loan_amount, interest_rate, repayment_strategy, personal_contribution,
                school_name_a, inflation_rate_a, grants_per_year_a, scenario_a,
                roi_horizon_years=roi_horizon_years,
                compare_mode=True, major_b=major_b, loan_amount_b=loan_amount_b,
                interest_rate_b=interest_rate_b, repayment_strategy_b=repayment_strategy_b,
                personal_contribution_b=personal_contribution_b, school_name_b=school_name_b,
                inflation_rate_b=inflation_rate_b, grants_per_year_b=grants_per_year_b,
                scenario_b=scenario_b, start_year_a=start_year_a, start_year_b=start_year_b,
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
                start_year_a=start_year_a, start_year_b=start_year_b,
                roi_horizon_years=roi_horizon_years,
                cc_mode_a=cc_mode_a, cc_state_a=cc_state_key_a, cc_coa_per_year_a=cc_coa_per_year_a,
                cc_mode_b=cc_mode_b, cc_state_b=cc_state_key_b, cc_coa_per_year_b=cc_coa_per_year_b,
            ))
            save_scenario_share({**build_scenario_context(
                major, loan_amount, interest_rate, repayment_strategy, personal_contribution,
                school_name_a, inflation_rate_a, grants_per_year_a, scenario_a,
                roi_horizon_years=roi_horizon_years,
                compare_mode=True, major_b=major_b, loan_amount_b=loan_amount_b,
                interest_rate_b=interest_rate_b, repayment_strategy_b=repayment_strategy_b,
                personal_contribution_b=personal_contribution_b, school_name_b=school_name_b,
                inflation_rate_b=inflation_rate_b, grants_per_year_b=grants_per_year_b,
                scenario_b=scenario_b, start_year_a=start_year_a, start_year_b=start_year_b,
            ), **module_context})
            components.html(COPY_URL_TO_CLIPBOARD_JS, height=0)
            st.success("Shareable link copied to your clipboard! Paste it anywhere to share this exact comparison.")
else:
    scenario = compute_scenario_results(major, loan_amount, interest_rate, repayment_strategy,
                                         personal_contribution, city_info["col_index"],
                                         roi_window_years=roi_horizon_years,
                                         hs_wage_index=get_metro_wage_index(city),
                                         enrollment_years=enrollment_years_a,
                                         working_years=working_years_a)
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
        effective_coa_per_year_a, personal_contribution_per_year_a, grants_per_year_a, inflation_rate_a,
        cc_years=cc_years_a, cc_coa_per_year=effective_cc_coa_per_year_a, finance_cc_years=False
    )
    st.caption(
        "Here's how your loan builds up year by year -- Cost of Attendance "
        "grows by the estimated inflation rate each year, while Personal "
        "Contribution and Grants & Scholarships stay the same."
    )
    render_centered_table(pd.DataFrame([
        {"Year": f"{row['year']} ({start_year_a + row['year'] - 1})",
         "Cost of Attendance": fmt_money(row["coa"]),
         "Loan Amount This Year": fmt_money(row["loan_amount"])}
        for row in loan_schedule_a
    ]))
    st.metric(f"Total Loan Amount (all {UNDERGRAD_YEARS} years)", fmt_money(loan_amount))
    if abs(loan_amount - computed_loan_amount_a) >= 1:
        st.caption((
            f"You overrode the calculated total ({fmt_money(computed_loan_amount_a)}) in the "
            "sidebar -- the table above still shows the calculated year-by-year breakdown, "
            "but every calculation below uses your overridden total instead."
        ).replace("$", r"\$"))

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

    st.plotly_chart(
        build_balance_chart(repayment_result["schedule"], strategy_label),
        use_container_width=True, config=PLOTLY_CHART_CONFIG,
    )

    # ---- 5d. Real-World Take-Home Snapshot --------------------------------
    # Rendered via the shared helper so Compare Mode shows the same figures --
    # see render_takehome_block. The returned values feed the PDF below.
    _th = render_takehome_block(
        scenario, major, career_stage_key, career_stage_label, city, city_info,
    )
    gross = _th["gross"]
    take_home = _th["take_home"]
    monthly_payment = _th["monthly_payment"]
    disposable_nominal = _th["disposable_nominal"]
    disposable_col_adjusted = _th["disposable_col_adjusted"]

    # ---- 5e. Financial Position (horizon per the sidebar's ROI Horizon) -----

    st.subheader(f"📊 {roi_horizon_years}-Year Financial Position")
    st.caption((
        f"This compares two paths over your first {roi_horizon_years} years after high school: going into "
        f"**{major}** (paying off the loan above along the way) vs. skipping college and "
        f"working right away as a high school graduate who takes on **no loan of their own**. "
        f"Both numbers are adjusted for the cost of living in **{city}** -- that's what "
        f"**\"COL-Adjusted\"** means -- so it's a fair, apples-to-apples comparison of real "
        f"spending power, not just which raw number is bigger. **Earnings Premium** is simply "
        f"the difference between the two: how much more (or less) you'd have after "
        f"{roi_horizon_years} years by choosing {major} instead of skipping college."
    ).replace("$", r"\$"))

    investment_caption = get_total_investment_caption(scenario)
    if investment_caption:
        st.caption(investment_caption)

    position_cols = st.columns(3)
    position_cols[0].metric(
        f"High School Grad — {roi_horizon_years}-Yr Net Position (No Loan)", fmt_money(roi_result["hs_net_position"]),
    )
    position_cols[1].metric(f"{major} — {roi_horizon_years}-Yr Net Position", fmt_money(roi_result["major_net_position"]))
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

    st.plotly_chart(
        build_roi_bar_chart(roi_result["hs_net_position"], roi_result["major_net_position"], major,
                             roi_horizon_years),
        use_container_width=True, config=PLOTLY_CHART_CONFIG,
    )

    # The break-even: how much debt this path can carry before it stops
    # beating a high school graduate, framed against what's actually being
    # borrowed. Everything above answers "how did this turn out?"; this
    # answers "how much room do I have?", which is the question a student
    # can still act on. Returns headline=None for sub-bachelor's occupations,
    # where the whole comparison is malformed -- see breakeven_summary.
    breakeven = breakeven_summary(
        major, loan_amount, interest_rate, repayment_strategy,
        roi_window_years=roi_horizon_years, col_index=city_info["col_index"],
        career_data_source=career_data_source,
        hs_wage_index=get_metro_wage_index(city),
        personal_contribution=personal_contribution,
        enrollment_years=scenario["enrollment_years"],
        working_years=scenario["working_years"],
    )
    if breakeven["headline"]:
        # Rendered into the container anchored high on the page rather than
        # here, so the verdict leads instead of trailing the ROI chart. The
        # callout colour tracks the verdict: st.success (green) reads as the
        # "great news" a good result deserves, st.warning (amber) keeps a bad
        # result sober -- a green banner on a "No" would be exactly the
        # optimism-bias cheerleading this tool exists to counter.
        with breakeven_banner_container:
            box = st.success if breakeven["positive"] else st.warning
            # The verdict word ("Good news." / "Worth a rethink.") gets its
            # own line under the question, then the headline, then the detail --
            # three visual tiers, skimmable top to bottom.
            box(
                f"**🎯 Is this debt worth it?**\n\n"
                f"**{breakeven['label']}.**\n\n"
                f"**{breakeven['headline']}**  \n{breakeven['detail']}".replace("$", r"\$")
            )

    # Sits directly under the position/premium numbers on purpose: this is the
    # assumption those numbers rest on, and it belongs beside them rather than
    # buried in Methodology where nobody reads it.
    st.info(underemployment_disclosure(major if dataset_mode == DATASET_MODE_MAJOR else None))

    # Which geography the salary above actually came from. BLS suppresses
    # roughly a fifth of occupation-by-metro cells, so those fall back to a
    # national wage -- and a national number standing in for a local one,
    # unlabelled, is the same class of hidden assumption as the
    # underemployment rate was.
    if dataset_mode == DATASET_MODE_CAREER and city != "National Average":
        if MAJOR_DATA.get(major, {}).get("wage_geography") == city:
            st.caption(
                f"💡 Salaries are **{city}**'s own BLS figures, not national ones — so this "
                f"weighs {city}'s pay against {city}'s cost of living."
            )
        else:
            st.caption(
                f"⚠️ BLS doesn't publish a separate **{major}** wage for {city} (too few "
                f"workers to report), so the salary above is the **national** figure adjusted "
                f"for {city}'s cost of living. Treat it as an approximation."
            )

    ai_context = {}
    if enable_ai_mode:
        ai_context = render_ai_risk_section(major)

    future_context = {}
    if enable_future_proofing:
        future_context = render_future_proofing_section(scenario, major, interest_rate,
                                                          col_index=city_info["col_index"], roi_window_years=roi_horizon_years)

    apprenticeship_context = {}
    if enable_apprenticeship:
        apprenticeship_context = render_apprenticeship_section(
            scenario, major, col_index=city_info["col_index"], roi_window_years=roi_horizon_years)

    module_context = build_module_context(
        prestige_tier_a if enable_prestige_mode else None, None, ai_context, future_context,
        apprenticeship_context,
    )

    # See the Compare Mode branch above -- same dedupe-on-rerun logging, for
    # the single-scenario path.
    maybe_log_scenario_event({**build_scenario_context(
        major, loan_amount, interest_rate, repayment_strategy, personal_contribution,
        school_name_a, inflation_rate_a, grants_per_year_a, scenario,
        roi_horizon_years=roi_horizon_years,
        start_year_a=start_year_a,
    ), **module_context})

    single_pdf_bytes = generate_pdf_report_single(
        major, city, school_name_a, in_state_a, career_stage_label,
        coa_per_year_a, personal_contribution_per_year_a, grants_per_year_a,
        interest_rate, repayment_strategy, loan_amount, loan_schedule_a,
        scenario, take_home, gross, disposable_nominal, disposable_col_adjusted,
        module_context=module_context, start_year_a=start_year_a, monthly_payment=monthly_payment,
        col_index=city_info["col_index"], roi_window_years=roi_horizon_years,
        cc_info_a=_cc_info_for_pdf(cc_mode_a, cc_state_key_a, effective_cc_coa_per_year_a, cc_oop_a, cc_years_a),
    )
    with top_actions_container:
        single_pdf_col, single_share_col = st.columns(2)
        single_pdf_col.download_button(
            "📄 Download PDF Report", data=single_pdf_bytes,
            file_name=f"{major.replace(' ', '_')}_payoff_report.pdf", mime="application/pdf",
            use_container_width=True, key="download_pdf_single",
            on_click=lambda: save_pdf_download({**build_scenario_context(
                major, loan_amount, interest_rate, repayment_strategy, personal_contribution,
                school_name_a, inflation_rate_a, grants_per_year_a, scenario,
                roi_horizon_years=roi_horizon_years,
                start_year_a=start_year_a,
            ), **module_context}),
        )
        if single_share_col.button("🔗 Share Scenario", use_container_width=True, key="share_scenario_single"):
            st.query_params.from_dict(build_share_params(
                career_data_source, major, city, school_name_a, in_state_a, career_stage_label,
                coa_per_year_a, personal_contribution_per_year_a, grants_per_year_a,
                interest_rate, repayment_strategy, False, start_year_a=start_year_a,
                roi_horizon_years=roi_horizon_years,
                cc_mode_a=cc_mode_a, cc_state_a=cc_state_key_a, cc_coa_per_year_a=cc_coa_per_year_a,
            ))
            save_scenario_share({**build_scenario_context(
                major, loan_amount, interest_rate, repayment_strategy, personal_contribution,
                school_name_a, inflation_rate_a, grants_per_year_a, scenario,
                roi_horizon_years=roi_horizon_years,
                start_year_a=start_year_a,
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
                                                   personal_contribution, city_info["col_index"],
                                                   roi_window_years=roi_horizon_years,
                                                   hs_wage_index=get_metro_wage_index(city),
                                                   enrollment_years=enrollment_years_a,
                                                   working_years=working_years_a)
            # major_b/loan_amount_b/etc. only exist as script variables when
            # compare_mode is on (they're assigned inside that sidebar
            # expander) -- referencing them outside an "if compare_mode:"
            # guard would raise NameError, so Scenario B's args are only
            # ever built when there's a Scenario B to build them from.
            compare_mode_kwargs = {}
            if compare_mode:
                scenario_b = compute_scenario_results(major_b, loan_amount_b, interest_rate_b, repayment_strategy_b,
                                                       personal_contribution_b, city_info["col_index"],
                                                       roi_window_years=roi_horizon_years,
                                                       hs_wage_index=get_metro_wage_index(city),
                                                       enrollment_years=enrollment_years_b,
                                                       working_years=working_years_b)
                compare_mode_kwargs = dict(
                    compare_mode=True, major_b=major_b, loan_amount_b=loan_amount_b,
                    interest_rate_b=interest_rate_b, repayment_strategy_b=repayment_strategy_b,
                    personal_contribution_b=personal_contribution_b, school_name_b=school_name_b,
                    inflation_rate_b=inflation_rate_b, grants_per_year_b=grants_per_year_b,
                    scenario_b=scenario_b, start_year_b=start_year_b,
                )
            context = {
                **build_scenario_context(
                    major, loan_amount, interest_rate, repayment_strategy, personal_contribution,
                    school_name_a, inflation_rate_a, grants_per_year_a, scenario_a,
                    roi_horizon_years=roi_horizon_years,
                    start_year_a=start_year_a, **compare_mode_kwargs,
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
| Computer Science | Software Developers (15-1252) | $105,210 | $135,980 |
| Nursing | Registered Nurses (29-1141) | $80,330 | $97,550 |
| Business | Business Operations Specialists, All Other (13-1199) | $62,640 | $83,050 |
| Finance | Financial and Investment Analysts (13-2051) | $79,290 | $102,740 |
| Humanities | Market Research Analysts & Marketing Specialists (13-1161) | $58,350 | $78,760 |
| Arts | Fine Artists, incl. Painters/Sculptors/Illustrators (27-1013) | $37,560 | $55,490 |
| Sports Management | Coaches and Scouts (27-2022) | $35,330 | $47,320 |
| Exercise Science | Exercise Physiologists (29-1128) | $49,620 | $59,460 |
| Athletic Training | Athletic Trainers (29-9091) | $55,130 | $62,520 |
| Medicine | Family Medicine Physicians (29-1215) | $162,420 | $244,180 |
| Law | Lawyers (23-1011) | $102,990 | $159,670 |

Source: [bls.gov/oes/2025/may](https://www.bls.gov/oes/2025/may/) (occupation
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

Target Profession also surfaces each occupation's typical entry-level
education, per BLS Employment Projections' "Typical Education Needed for
Entry" data ([bls.gov/oes/additional.htm](https://www.bls.gov/oes/additional.htm)):
selecting a profession that typically requires less than a bachelor's
degree shows a disclosure, since this app's Cost of Attendance/loan model
otherwise assumes 4 years of undergraduate cost for every major. It's
kept in the dropdown rather than removed -- it's still a real career a
student might be evaluating. See Alternative Pathway: Trade Apprenticeship
below, which uses that profession's own real BLS earnings instead of the
generic national trade benchmark whenever this applies.

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

**The biggest assumption here: that you work in your field.** Every salary
on this page is what someone in that career actually earns — and it assumes
you become one of them. Nationally, that's true for only about 6 in 10
college graduates. The Federal Reserve Bank of New York tracks
*underemployment* — graduates working jobs that don't require a degree at
all — and finds **39% overall**, ranging from **13% (Nursing)** to **66%
(Criminal Justice)** across 73 majors
([Source: NY Fed](https://www.newyorkfed.org/research/college-labor-market),
updated February 2026, from Census ACS and DOL O*NET data).

We show that as a disclosure rather than folding it into the math, and the
reason is worth being straight about. That data is organized by *major*
(Psychology), while this calculator is organized by *career* (Clinical and
Counseling Psychologists) — so applying a per-career underemployment rate
would mean guessing at which majors feed which careers. We'd rather tell
you the assumption than invent a number to hide it. Read every figure below
as "if you land the job," not "you will land the job."

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

**Taxes.** A salary is the amount *before* taxes, but what actually pays a
loan is your **take-home pay** — what's left after taxes come out of each
paycheck — so we subtract real taxes to get there. We use the actual 2024
federal income-tax rates for someone filing on their own with no kids (IRS
Rev. Proc. 2023-34), plus **FICA** — the Social Security and Medicare taxes
taken out of every U.S. paycheck (6.2% for Social Security, up to a $168,600
income cap, and 1.45% for Medicare). To keep it simple we don't model
itemized deductions, tax credits, or the extra Medicare tax on income above
$200K (none of the careers here pay that much). For state tax, we
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
comes from real government data on how far a dollar actually goes in each
city (the U.S. Bureau of Economic Analysis's Regional Price Parities, 2023),
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

**How the "Student Loan Payment / Take-Home Ratio" color coding works.** This
percentage (shown below the take-home chart) is color-coded against two real, commonly-cited
guidelines — Green ("Manageable") means your loan payment is at or under 10%
of your gross (before-tax) monthly income, a widely-cited student-loan
budgeting guideline
(e.g. [SoFi](https://www.sofi.com/learn/content/percentage-of-income-towards-student-loans/)).
Red ("High") means it's over 36% — the limit mortgage lenders use for *all*
of a borrower's debt payments added together, so at that level this one
student loan is already eating the entire share of income a lender expects
to cover every debt you have. Orange ("Elevated") is everything in between.
Those guidelines are normally written as a percentage of *before-tax*
income, but this app measures the payment against your *take-home* (after-tax)
pay — the money you'd actually have to make the payment with. So we shift both
cutoffs onto a take-home basis using this scenario's real tax rate (the share
of income that goes to taxes), rather than a one-size-fits-all guess. Hover
over the percentage to see the exact cutoffs for your scenario.

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

**Community-college path ("2+2").** The "Community college path" selector
models spending the first """ + str(COMMUNITY_COLLEGE_YEARS) + """ years at a community college and then
transferring to the 4-year school to finish the **same** bachelor's degree.
The degree, earnings, and the ~4-year enrollment timeline are identical to
the direct-to-4-year path — only the cost and how it's paid change. There
are two on modes:

- **Full-time, then transfer.** You attend community college full-time for
  """ + str(COMMUNITY_COLLEGE_YEARS) + """ years, then transfer. These are still foregone-earnings years (you're
  enrolled, not working), exactly like the direct path.
- **Part-time while working, then transfer.** You work full-time and attend
  community college part-time, so those years are **not** foregone earnings —
  you're earning roughly a high-school-graduate wage — and then you attend the
  4-year school full-time to finish. This is usually the most financially
  favorable path. Its earnings advantage only shows up when *Count foregone
  earnings during enrollment* is on (which puts every path on one age-18
  timeline); its lower debt shows up either way.

**Community college is assumed paid without loans.** In both modes the
community-college years add **$0 to the loan** — most community-college
students don't borrow (it's low-cost, and Pell grants or part-time work
cover it). Only the two university years are financed. The community-college
tuition is still counted as a real cost: it enters the ROI as an out-of-pocket
*personal contribution* (money you gave up, but with no interest), and it is
**not** double-counted against your per-year Personal Contribution, which the
model applies only to the financed university years.

**State-level community-college cost.** Community-college tuition varies
widely by state — from roughly $1,400/yr (California) to $8,000/yr (South
Dakota) — so the **Community College State** dropdown sets the default cost
from average annual in-district tuition & fees for that state (national
average **""" + fmt_money(COMMUNITY_COLLEGE_COA_DEFAULT) + """/yr**). It defaults to your selected 4-year school's
state (then your work city's state), on the assumption you attend community
college where you'll transfer. Source: National Center for Education
Statistics (NCES), Digest of Education Statistics, via the Education Data
Initiative (educationdata.org), 2025. These are tuition & fees, not a full
Cost of Attendance, so the modeled figure reflects the tuition a transfer
student living at home pays; the field is editable for any other situation.
This deliberately does **not** apply a transfer-student earnings penalty —
the resulting degree is treated as identical to one earned by starting at the
4-year school (optimistic but common; real transfer outcomes vary). To
compare paths directly, set a different path in each scenario in Compare
Mode.

**Accounting for a delayed start.** "Year Starting Undergraduate School"
lets you model not starting college right away. If you pick a future year,
the Cost of Attendance you entered (today's price) is first projected
forward to that year using the same estimated COA inflation rate described
above, *before* it's grown further across the 4 years of enrollment —
`Effective Year-1 COA = Cost of Attendance × (1 + inflation rate)^(years
until start)`. Leaving it at the current year changes nothing. Note that
only Cost of Attendance is projected this way — starting/mid-career
salaries, taxes, take-home pay, and cost-of-living figures throughout this
tool are intentionally kept in **today's real dollars**, not projected
forward, since there's no equally well-sourced wage-inflation estimate to
apply the same way. This makes every dollar figure here a real (inflation-
adjusted), apples-to-apples comparison rather than a nominal one.

**Overriding the Total Loan Amount.** The "Total Loan Amount ($)" field
right above Average Loan Interest Rate is pre-filled with that calculated
total, but you can type over it with any other number — for example, the
real total from an actual financial aid offer letter, which won't match
this simplified per-year model exactly. Once you do, every calculation on
this page uses your typed number instead of the calculated one (the
per-year table above it still shows the calculated breakdown, for
reference). Your override sticks across reruns, but refreshes back to the
newly calculated total the next time you change Cost of Attendance,
Personal Contribution, Grants & Scholarships, or your school -- the same
way the Cost of Attendance field itself auto-fills from a school lookup
until you type over it.

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

**Advanced Analysis Settings (optional, off by default).** Five extra
modules live in a sidebar expander. Each one is opt-in, and the calculator
behaves exactly as described above when all five are left off.

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
  above) — not a separate, flat percentage assumption.
- **Alternative Pathway: Trade Apprenticeship.** A single national
  reference point, not this app's per-major BLS pipeline. Year-1 training
  wage ($52,000) and average starting salary upon completion ($86,000) are
  apprenticeship.gov's own published statistics (the completion figure is
  footnoted there as sourced from Kansas Dept. of Commerce CRIS reporting —
  a single state's data, not a national census, though it's DOL's own
  current national benchmark reference). Between those two points, pay is
  modeled as ramping up at a constant rate across the typical training
  period — real registered-apprenticeship pay schedules do step up as
  apprentices progress, per BLS Career Outlook, ["Apprenticeships: Outlook
  and wages in selected occupations"](https://www.bls.gov/careeroutlook/2019/article/apprenticeships-outlook-wages-update.htm)
  (2019), which also documents typical program length (~4 years, range 1-6)
  and that apprentices are paid wages during training rather than charged
  tuition — hence $0 typical training debt. After completion, earnings grow
  at this app's existing high-school-graduate wage-growth assumption (see
  above), since no BLS per-occupation trajectory exists past that point.
  **This is one illustrative national benchmark, not a personalized
  estimate for any specific trade or apprenticeship program.**
- **Count foregone earnings during enrollment.** By default this calculator
  starts its earnings clock at *graduation*: it compares a graduate's first
  N years of post-degree salary against a high-school graduate's same N
  years, and captures only the *tuition/debt* cost of the degree. But the
  largest real cost of a bachelor's degree is usually not tuition — it's the
  roughly four years of wages given up while enrolled full-time, during which
  the debt-free high-school graduate is already working, earning raises, and
  banking that income. Turning this option on adds those ~4 foregone years
  (UNDERGRAD_YEARS) to the high-school baseline, so every path is compared on
  one consistent timeline that starts at **age 18** rather than at
  graduation. Concretely: the high-school graduate is credited with ~4 extra
  years of earnings at the front, the degree-seeker earns nothing during
  enrollment, and — when the Trade Apprenticeship module is also on — the
  apprentice, who *is* paid during those years, is credited with them too (a
  job that doesn't need a 4-year degree is likewise never charged for time it
  didn't spend in school). This lowers each degree's earnings premium and
  break-even, often by a lot, and is the more complete way to compare. It
  only changes the *earnings* side of the comparison — the tuition and debt
  you put in stay the same — so the ROI% still reads as "how much you come out
  ahead for every dollar of tuition," now counting the wages you skipped to be
  in school. One simplification to know about: the totals are just each year's
  real (inflation-adjusted) dollars added up — the model doesn't treat a dollar
  earned 10 years from now as worth less than a dollar today (what economists
  call "discounting"). It's a straightforward apples-to-apples earnings
  comparison, not a formal net-present-value calculation.

*This tool produces educational estimates for a student research project,
not financial advice. Figures are national averages/percentiles and will not
reflect any individual's actual salary, cost of living, or loan terms.*
        """
    st.markdown(methodology_text.replace("$", r"\$"))

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

import contextlib
import hashlib
import html
import io
import math
import re
import sys
import uuid
from datetime import datetime, timezone
from xml.sax.saxutils import escape as xml_escape
from zoneinfo import ZoneInfo

import certifi
import matplotlib
matplotlib.use("Agg")  # must precede importing pyplot -- no display/browser needed
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd
import plotly.express as px
# graph_objects, not express: the wage-distribution histogram needs per-bar
# widths (OEWS's percentile bins are unequal), which px.bar has no parameter
# for.
import plotly.graph_objects as go
import requests
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
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
PHYSICIAN_TITLES = [
    "Anesthesiologists", "Cardiologists", "Emergency Medicine Physicians",
    "Family Medicine Physicians", "Neurologists", "Obstetricians and Gynecologists",
    "Ophthalmologists, Except Pediatric", "Orthopedic Surgeons, Except Pediatric",
    "Pediatric Surgeons", "Physicians, Pathologists", "Psychiatrists", "Radiologists",
]
for _title in PHYSICIAN_TITLES:
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
DENTIST_TITLES = ["Oral and Maxillofacial Surgeons", "Prosthodontists",
                  "Dentists, All Other Specialists"]
for _title in DENTIST_TITLES:
    ADVANCED_TRAINING_OVERLAY[_title] = dict(_DENTIST_TRAINING)

# Which professional program each of these paths attends, keying
# data/professional_debt_clean.csv. Built from the same title lists above
# rather than retyped, so a title added there cannot be forgotten here.
#
# The debt figures in CURATED_MAJOR_DATA and the two overlays above are now
# NATIONAL FALLBACKS, used when the visitor has not named a school or the
# school publishes no figure -- not "the figure the app uses". Where a school
# is named, its own median replaces them and the spread is large: medical
# school debt runs from $47,503 to $330,479 across schools.
PROFESSIONAL_PROGRAM_BY_OCCUPATION = {
    **{title: "medicine" for title in PHYSICIAN_TITLES},
    **{title: "dentistry" for title in DENTIST_TITLES},
    "Medicine": "medicine",
    "Law": "law",
}


# BLS OEWS-sourced careers from data_pipeline.py's output, in the same
# {major_name: {starting_salary, median_salary}} shape as the curated dict
# above, so every existing calculation (get_major_growth_rate,
# get_annual_salary_for_year, etc.) works on them identically -- no
# special-casing needed anywhere else in the app. Two geographic scopes are
# available. The national file is the base MAJOR_DATA is built from; the state
# and metro files below overlay it (see build_major_data).
CAREERS_CSV_PATH_NATIONAL = "cleaned_careers.csv"
# Kept for analyze_model.py, which still offers a --state CA run against this
# single-state file. The app itself no longer reads it: see
# STATE_CAREERS_CSV_PATH, which covers every state and is selected by the
# city rather than by a sidebar control.
CAREERS_CSV_PATH_CA = "cleaned_careers_ca.csv"

# Per-STATE occupation wages, via `data_pipeline.py --all-states`. Long-format,
# one row per (state, occupation), same shape as the metro file.
#
# This is the middle rung of a three-level fallback: national -> state -> metro,
# each overlaying the one before, so every occupation shows the finest geography
# BLS actually publishes for it. Metros carry a median 82% of occupations
# (72-92% by city); before this file existed the other ~18% dropped straight to
# a national average even though the state figure was sitting right there.
#
# It also replaced a sidebar control that shouldn't have been one. "Career
# Salary Data: National / California" let a visitor pick a wage basis
# independently of the city they'd already chosen, so California + New York was
# reachable -- and the 51 occupations New York suppresses then showed
# California wages while the page labelled them national figures (Craft
# Artists: $46,080 nationally, shown as $100,540). A state isn't a preference,
# it's a fact about the selected city, so it's derived from it now.
STATE_CAREERS_CSV_PATH = "data/state_careers_clean.csv"

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


def wage_percentiles_from_row(row) -> dict:
    """OEWS's published wage distribution for one occupation, as
    {p10, p25, p50, p75, p90}, or None if this CSV doesn't carry the full set.

    Returns None rather than a partial dict on purpose: every consumer draws a
    distribution, and a distribution missing a percentile isn't a smaller
    chart, it's a wrong-shaped one. getattr with a default keeps a cleaned CSV
    generated before data_pipeline.py carried these columns working -- it just
    shows no distribution, the same way an older CSV shows no
    typical_education.
    """
    values = {
        "p10": getattr(row, "a_pct10", None), "p25": getattr(row, "a_pct25", None),
        "p50": getattr(row, "a_median", None), "p75": getattr(row, "a_pct75", None),
        "p90": getattr(row, "a_pct90", None),
    }
    if any(v is None or pd.isna(v) for v in values.values()):
        return None
    return {k: float(v) for k, v in values.items()}


@st.cache_data
def load_bls_careers(csv_path: str) -> dict:
    try:
        careers_df = pd.read_csv(csv_path)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return {}
    return {
        row.occ_title: {
            "starting_salary": row.a_pct25, "median_salary": row.a_median,
            "wage_percentiles": wage_percentiles_from_row(row),
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
        # wage_percentiles rides along with the wage pair because the
        # distribution is as local as the median is -- a New York nurse's
        # spread is not the national spread. build_major_data's overlay then
        # replaces the national distribution wholesale for cities BLS
        # publishes, and leaves the national one in place for cities it
        # suppresses, which is exactly the same fallback the wages take.
        row.occ_title: {"starting_salary": row.a_pct25, "median_salary": row.a_median,
                        "wage_percentiles": wage_percentiles_from_row(row)}
        for row in city_rows.itertuples()
    }


@st.cache_data
def load_hs_age_profile(csv_path: str) -> list:
    """Age bands for high-school graduates, ascending, from
    build_hs_age_profile.py. [] when the file is absent -- callers must render
    nothing rather than fail, since this is disclosure, not model input."""
    try:
        df = pd.read_csv(csv_path)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return []
    return df.sort_values("age_low").to_dict("records")


def hs_age_factor(age: int) -> float:
    """This age's wage as a multiple of the all-ages (25+) median, from the
    CPS age profile. 1.0 when the profile is missing, which makes every caller
    degrade to the flat baseline rather than to nonsense.

    Clamped at both ends: an age below the youngest published band takes that
    band's ratio, and an age above the oldest takes that one's. The profile
    starts at 18 and ends at 64, so within this app's ~18-48 window only the
    bottom clamp can fire, and only if a caller asks about someone younger than
    a high school graduate can be.

    Reads ratio_to_25plus rather than the profile's dollar medians, for the
    same reason hs_young_wage_disclosure does: the ratio is a shape and
    survives a vintage change, while the dollars are pinned to the microdata's
    income year and would silently reintroduce the cross-vintage mixing this
    codebase has been bitten by twice.
    """
    bands = load_hs_age_profile(HS_AGE_PROFILE_CSV_PATH)
    if not bands:
        return 1.0
    for band in bands:
        try:
            if int(band["age_low"]) <= age <= int(band["age_high"]):
                return float(band["ratio_to_25plus"])
        except (KeyError, TypeError, ValueError):
            return 1.0
    try:
        if age < int(bands[0]["age_low"]):
            return float(bands[0]["ratio_to_25plus"])
        return float(bands[-1]["ratio_to_25plus"])
    except (KeyError, TypeError, ValueError):
        return 1.0


def hs_wage_for_timeline_year(year_index: int, hs_wage_index: float,
                               baseline_start_age: int = None) -> float:
    """The high-school baseline's wage in year `year_index` of the comparison
    timeline.

    baseline_start_age=None keeps the original flat behaviour: one all-ages
    median grown at HS_GRAD_GROWTH_RATE. Passing an age turns on the age-aware
    baseline, which additionally scales each year by that age's share of the
    all-ages median.

    The two rates are doing different jobs once this is on, which is the point.
    HS_GRAD_GROWTH_RATE stops standing for "raises AND cost-of-living" and
    becomes calendar drift only -- how the whole wage distribution moves over
    time -- because the raises a person gets for getting older now come from
    the profile instead. The arithmetic on the drift term is unchanged; what
    changes is that a real age-earnings curve is layered on top of it.
    """
    wage = HS_GRAD_SALARY * hs_wage_index * (1 + HS_GRAD_GROWTH_RATE) ** year_index
    if baseline_start_age is None:
        return wage
    return wage * hs_age_factor(baseline_start_age + year_index)


def baseline_start_age_for(program_years: int, enrollment_years: int) -> int:
    """The age the high-school baseline's timeline starts at.

    The offset is the subtle part, so it lives in one place. With foregone
    earnings counted the timeline starts the year the graduate would have
    started working, so year 0 is age 18. Without it the comparison starts at
    graduation, so year 0 is age 18 + however long the program ran -- the high
    school graduate is the same age as the graduate at that moment, just with
    more years of earnings behind them.

    There is no longer an off switch: the app always compares against the age
    curve, because a flat age-25+ median is simply the wrong figure for an
    18-year-old and there is no reading of the data on which it is right.
    calculate_roi still accepts baseline_start_age=None for the flat behaviour,
    which is what analyze_model.py gets by not passing one -- that keeps the
    paper able to reproduce the pre-curve model on demand.
    """
    if enrollment_years:
        return HS_GRAD_START_AGE
    return HS_GRAD_START_AGE + program_years


def hs_young_wage_disclosure() -> str:
    """One sentence sizing the gap between the all-ages baseline and what a
    young high school graduate actually earns. Empty string when the profile
    is missing, so the paragraph around it still reads.

    Scales HS_GRAD_SALARY by the band's ratio_to_25plus rather than quoting the
    profile's own dollar median. The ratio is a shape and survives a vintage
    change; the dollars are in the microdata's income year and quoting them
    beside a baseline from a newer quarter would mix vintages -- the exact
    error that made the metro wages read as a pay cut earlier in this file's
    history. Doing it this way also means refreshing HS_GRAD_SALARY alone keeps
    this sentence correct.
    """
    bands = load_hs_age_profile(HS_AGE_PROFILE_CSV_PATH)
    if not bands:
        return ""
    band = bands[0]
    try:
        ratio = float(band["ratio_to_25plus"])
        low, high = int(band["age_low"]), int(band["age_high"])
    except (KeyError, TypeError, ValueError):
        return ""
    if not 0 < ratio < 1:
        # A ratio at or above 1 would make the sentence claim young workers
        # out-earn the all-ages median, which would mean the profile is wrong
        # rather than surprising. Say nothing instead.
        return ""
    article = "an" if str(low).startswith(("8", "11", "18")) else "a"
    return (
        f" In Census microdata for that exact group, {article} "
        f"{low}-to-{high}-year-old high school graduate working full time earns "
        f"about **{(1 - ratio) * 100:.0f}% less** than that — roughly "
        f"{fmt_money(HS_GRAD_SALARY * ratio)} against the baseline above."
    )


@st.cache_data
def load_state_wages(csv_path: str, state: str) -> dict:
    """One state's own BLS wages, as {occ_title: {starting_salary,
    median_salary, wage_percentiles}}, from `data_pipeline.py --all-states`.

    Same shape and same rules as load_metro_wages -- wages only, since
    everything else about an occupation is a property of the occupation rather
    than of where it's done. Returns {} for an unknown state or a missing file,
    which callers must treat as "use the national figure", so a deploy without
    the state file degrades to the previous behaviour instead of to nonsense.
    """
    if not state:
        return {}
    try:
        df = pd.read_csv(csv_path)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return {}
    state_rows = df[df["state"] == state]
    return {
        row.occ_title: {"starting_salary": row.a_pct25, "median_salary": row.a_median,
                        "wage_percentiles": wage_percentiles_from_row(row)}
        for row in state_rows.itertuples()
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
            # Representative occupation group for the optional AI Employability
            # Risk module (majors have no SOC code of their own -- see
            # NYFED_MAJOR_SOC_GROUP). None for the few majors left unmapped, which
            # then honestly show "Unknown".
            "soc_major_group": NYFED_MAJOR_SOC_GROUP.get(row.major),
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
    # occupation-by-city cells) fall back to the STATE figure, and only to the
    # national one if the state suppresses them too. Each level is FLAGGED via
    # wage_geography, so the page can say which figure it's showing instead of
    # passing a non-local number off as local. Curated majors have neither a
    # state nor a metro equivalent and stay national by the same rule.
    #
    # Order is load-bearing: national is the spine that defines the dropdown
    # (a state file alone would drop the occupations that state suppresses out
    # of the list entirely), then state overlays it, then metro overlays that.
    # Applying them in any other order would let a coarser geography overwrite
    # a finer one.
    state = CITY_DATA.get(city, {}).get("state_key") if city else None
    if state:
        state_wages = load_state_wages(STATE_CAREERS_CSV_PATH, state)
        for occupation, wages in state_wages.items():
            if occupation in data:
                data[occupation] = {**data[occupation], **wages,
                                     "wage_geography": US_STATES.get(state, state),
                                     "wage_geography_level": "state"}

    if city:
        metro_wages = load_metro_wages(METRO_CAREERS_CSV_PATH, city)
        for occupation, wages in metro_wages.items():
            if occupation in data:
                data[occupation] = {**data[occupation], **wages, "wage_geography": city,
                                     "wage_geography_level": "metro"}

    for major_name, training_fields in ADVANCED_TRAINING_OVERLAY.items():
        if major_name in data:
            data[major_name] = {**data[major_name], **training_fields}
    return data

# Baseline comparison group: a high school graduate (no college) who takes on
# no loans. Annual figure is real BLS Current Population Survey data: median
# usual weekly earnings for full-time wage and salary workers age 25+ with a
# high school diploma and no college -- $994/week in 2026 Q2, annualized as
# $994 * 52. BLS does not publish a matching by-experience wage growth
# trajectory for this group, so growth_rate remains a modest assumption
# reflecting ordinary cost-of-living/seniority raises rather than freezing pay
# for a decade.
#
# To refresh: CPS series LEU0252917300, quarterly, from BLS's own public API
# (api.bls.gov/publicAPI/v1/timeseries/data/LEU0252917300). Cite the series ID
# rather than a news-release URL -- the "Usual Weekly Earnings" release lives
# at one address that is overwritten every quarter, so a link pinned to a
# figure goes stale silently while still resolving. bls.gov itself returns 403
# to programmatic fetches; the API host does not.
#
# The series is noisy quarter to quarter (it fell from $977 to $953 across the
# 2024->2025 turn), so a single quarter's move is not a trend. Note also that
# 2025 Q4 does not exist: October 2025 CPS data was never collected due to the
# federal government shutdown, and BLS did not produce that quarter. The 2025
# annual average is an 11-month figure.
HS_GRAD_SALARY = 51688
HS_GRAD_GROWTH_RATE = 0.02

# Age-earnings profile for that same population, from build_hs_age_profile.py.
# Read for DISCLOSURE ONLY -- it does not feed the model, and the ROI numbers
# are identical whether or not this file exists. HS_GRAD_SALARY is an all-ages
# (25+) median while the app compares someone aged roughly 18 to 32, and this
# is what lets the Methodology state the size of that gap as a measured figure
# instead of a hardcoded one that goes stale the moment either the baseline or
# the microdata is refreshed.
HS_AGE_PROFILE_CSV_PATH = "data/hs_age_profile.csv"

# The age a high school graduate starts working, and therefore the age the
# baseline's timeline begins at when foregone earnings are counted. 18 rather
# than 17 or 19 because that's when the app's own "one consistent timeline
# starting at age 18" framing begins (see the foregone-earnings option).
HS_GRAD_START_AGE = 18

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


def graduate_salary_disclosure(typical_education: str) -> str:
    """What the salary on this page does and does not account for, when the
    path includes graduate study.

    The app has no way to model what a master's ADDS. `typical_education` is
    the occupation's entry requirement, not the visitor's credential, and the
    BLS median is for people already doing the job -- so the figure shown is
    already "what someone with this degree earns", not a bachelor's salary the
    degree then lifts. The failure mode to avoid is a visitor reading the
    premium as the return ON the master's, when it is the return on the whole
    path from high school.

    Returns "" for undergraduate paths so the sentence never appears where it
    would be noise.
    """
    if not is_graduate_education(typical_education):
        return ""
    extra = graduate_years_for_education(typical_education)
    level = "master's" if typical_education == CREDENTIAL_MASTERS else "doctorate"
    return (
        f"BLS says this career is entered with a {level}, so the cost above "
        f"includes {extra} more years of school on top of a bachelor's, and "
        "the comparison runs from high school. The salary is what people "
        f"already in this job earn — it is not a bachelor's salary that the "
        f"{level} then raises, and the app cannot model what the degree adds "
        "on its own."
    )


def render_graduate_salary_disclosure(typical_education: str) -> None:
    text = graduate_salary_disclosure(typical_education)
    if text:
        st.caption(text)


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
            pct_phrase = bold(f"{rate:.0f}% who end up in jobs that don't require a college degree")
            return (
                f"The salary above isn't a best-case number — it's what people who studied "
                f"{major_name} actually earn on average, and that average includes the "
                f"{pct_phrase} (and usually earn less). Those lower earners are counted in, not "
                f"left out, so this reflects the real range of outcomes for {major_name} graduates "
                f"— not just the ones who land a job in their field."
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


def render_major_careers(major_name: str, compact: bool = False) -> None:
    """Render a short "Careers this major commonly leads to" block for the
    selected major, into the current Streamlit container.

    Answers the "what jobs does this major lead to?" question from data the app
    already owns: the major's SOC major group (already on every major via
    NYFED_MAJOR_SOC_GROUP) is used to pull real BLS occupations in that field,
    each with its real median wage (careers_for_major). No external careers
    source is copied -- the reference site that inspired this is copyrighted and
    paywalled, so only the idea is reused, from public BLS data in the repo.

    Renders nothing when the major has no mapped SOC group (Interdisciplinary
    Studies, Liberal Arts -- which genuinely span the whole labour market) or the
    group has no label, exactly as underemployment_disclosure degrades. Shared by
    the single-scenario view and Compare Mode's per-scenario panel so both result
    branches show it identically (an asymmetry between them is an H2 confound);
    compact=True drops it to a caption for Compare Mode's narrow columns.

    Deliberately uses the National BLS dataset regardless of the Career-source
    radio, so the "leads to" set for a major is stable across geographies -- the
    caption says so.
    """
    group = MAJOR_DATA.get(major_name, {}).get("soc_major_group")
    label = (AI_EXPOSURE_BY_SOC_GROUP.get(group) or {}).get("label") if group else None
    if not label:
        return
    examples = careers_for_major(group, CAREERS_CSV_PATH_NATIONAL)
    if not examples:
        return
    lines = "  \n".join(f"• {title} — {fmt_money(median)} median" for title, median in examples)
    caption = (
        f"Example occupations in the **{label}** field — a representative sample "
        "from the U.S. Bureau of Labor Statistics, not an exhaustive or guaranteed "
        "list. A major spreads across many paths; these are common destinations, "
        "shown at national median pay."
    ).replace("$", r"\$")
    body = lines.replace("$", r"\$")
    if compact:
        st.caption("**💼 Careers this major commonly leads to**  \n" + body)
        st.caption(caption)
    else:
        with st.expander("💼 Careers this major commonly leads to"):
            st.markdown(body)
            st.caption(caption)


def get_wage_distribution_context(occupation_name: str) -> dict:
    """Everything both the on-screen and PDF wage-distribution charts need for
    one occupation, or None if it has no published distribution.

    Career mode only. OEWS publishes percentiles per *occupation*; a major is
    not an occupation, and the NY Fed major wages have no percentile
    equivalent at all -- so Major mode has nothing to draw and this returns
    None there rather than inventing a spread from a single median.

    Kept separate from the chart builders so the "is there one?" decision is
    made once, identically, on both surfaces.
    """
    if dataset_mode != DATASET_MODE_CAREER:
        return None
    entry = MAJOR_DATA.get(occupation_name, {})
    percentiles = entry.get("wage_percentiles")
    if not build_wage_distribution(percentiles):
        return None
    # The national figures for the same occupation, so the chart can put the
    # local distribution against the country's. Read from the national CSV
    # rather than MAJOR_DATA because the state/metro overlay has already
    # replaced MAJOR_DATA's entry wholesale -- asking it for "the national
    # percentiles" would hand back the local ones. None when the local wage IS
    # the national one, which is what suppresses the second row.
    national = None
    if entry.get("wage_geography_level") in ("metro", "state"):
        national_entry = load_bls_careers(CAREERS_CSV_PATH_NATIONAL).get(occupation_name, {})
        candidate = national_entry.get("wage_percentiles")
        if build_wage_distribution(candidate):
            national = candidate
    return {
        "percentiles": percentiles,
        "occupation_name": occupation_name,
        "modelled_start": entry.get("starting_salary"),
        # Which geography these percentiles describe -- the metro overlay
        # replaces them wholesale for cities BLS publishes, so the label has
        # to follow the data rather than the selected city.
        "geography_label": entry.get("wage_geography") or "national",
        "national_percentiles": national,
    }


def wage_distribution_rows(occupation_name: str) -> int:
    """How many geography rows this occupation's chart will draw: 2 when the
    city has its own published wages and a national row goes beneath, 1 when
    the national figure is all there is, 0 when there's no chart at all.

    Compare Mode uses the larger of its two scenarios so both columns reserve
    the same vertical space. Without that, a national-only occupation beside a
    metro one draws a shorter chart, and the two national curves -- the one
    thing genuinely common to both columns -- sit at different heights."""
    context = get_wage_distribution_context(occupation_name)
    if not context:
        return 0
    return 2 if context.get("national_percentiles") else 1


def render_wage_geography_note(occupation_name: str) -> None:
    """Which geography the salary shown for this occupation actually came from.

    BLS suppresses roughly a fifth of occupation-by-metro cells, so those fall
    back a level -- and a non-local number standing in for a local one,
    unlabelled, is the same class of hidden assumption as the underemployment
    rate was.

    Three outcomes, not two: the metro's own figure, the state's (metro
    suppresses it, state publishes it), or the national one (both suppress it).
    Naming the level matters -- the two-branch version this replaced said
    "national" for anything that wasn't metro, which became a false statement
    the moment a state layer existed underneath it.

    Shared between both result branches. It was previously inline in the
    single-scenario branch only, so Compare Mode -- the randomly assigned
    contrast arm -- never showed it: half of visitors got a state or national
    wage with nothing saying so, which is exactly the asymmetry CLAUDE.md
    warns turns into an H2 confound.
    """
    if dataset_mode != DATASET_MODE_CAREER or city == "National Average":
        return
    entry = MAJOR_DATA.get(occupation_name, {})
    level = entry.get("wage_geography_level")
    if level == "metro":
        st.caption(
            f"💡 Salaries are **{city}**'s own BLS figures, not national ones — so this "
            f"weighs {city}'s pay against {city}'s cost of living."
        )
    elif level == "state":
        st.caption(
            f"💡 BLS doesn't publish a separate **{occupation_name}** wage for {city} (too "
            f"few workers to report there), so the salary above is "
            f"**{entry.get('wage_geography')}**'s statewide figure — closer than a "
            f"national average, and adjusted for {city}'s cost of living."
        )
    else:
        st.caption(
            f"⚠️ BLS doesn't publish a separate **{occupation_name}** wage for {city} or for "
            f"its state (too few workers to report), so the salary above is the **national** "
            f"figure adjusted for {city}'s cost of living. Treat it as an approximation."
        )


# One string, used by the single-scenario view and by Compare Mode's
# once-below-the-columns render, so the two can't drift into different
# explanations of the same picture.
#
# Deliberately plain: the audience is a 17-year-old, and the previous wording
# ("the area under each curve is the share of workers") was both harder to
# read AND untrue of the curve now drawn -- the shape is stylised, with its
# apex pinned to the median, so height carries no worker count. Nothing here
# may imply it does.
WAGE_DISTRIBUTION_CAPTION = (
    "The median is the midpoint — half of these workers earn less, half earn "
    "more. The curve shows the range around it: it runs from what the "
    "lowest-paid 10% earn up to what the top 10% earn. "
    "[BLS OEWS percentiles; see Methodology]"
)


def render_wage_distribution(occupation_name: str, compact: bool = False,
                             caption: bool = True, row_slots: int = None) -> None:
    """The wage-distribution histogram, rendered identically from both result
    branches.

    Called from the single-scenario branch and from render_scenario_panel, for
    the same reason render_major_careers is: compare_mode IS the randomly
    assigned contrast arm, so anything one branch shows and the other doesn't
    becomes a difference between the arms that the paper's H2 doesn't account
    for. Renders nothing at all in Major mode, on both branches equally.
    """
    context = get_wage_distribution_context(occupation_name)
    if not context:
        return
    figure = build_wage_distribution_chart(**context, row_slots=row_slots)
    if figure is None:
        return
    if compact:
        # Drop the title (each compare column is already labelled with its
        # scenario) and shorten the plot, but keep the bottom margin: it has to
        # clear the x-title and the tail note, which get clipped without it.
        # title_text="" rather than title=None -- Plotly renders a None title
        # as the literal string "undefined".
        #
        # Height scales with the row count rather than being a fixed 320: a
        # two-geography chart squashed into one row's worth of space overlaps
        # its own money labels. Derived from the trace count so it can't fall
        # out of step with how many rows the builder actually drew.
        # The left margin must survive this override: it holds the row labels,
        # and dropping it also squeezes the p10 money label off the canvas.
        # The right margin is new here -- p90's label sits outside the curve,
        # which the full-width layout absorbs and a compare column doesn't.
        rows_drawn = max(row_slots or 0,
                          2 if context.get("national_percentiles") else 1)
        figure.update_layout(title_text="", height=180 + 110 * rows_drawn,
                              margin=dict(t=40, b=110, l=120, r=60))
    st.plotly_chart(figure, use_container_width=True, config=PLOTLY_CHART_CONFIG)
    if caption:
        st.caption(WAGE_DISTRIBUTION_CAPTION)


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
# The Extended Standard plan: a fixed payment stretched to 25 years, for
# borrowers with enough balance to qualify. Included in the existing-loan
# comparison because for someone NOT seeking forgiveness it is often the lowest
# fixed payment available, and leaving it out would make the comparison look
# like RAP is the only way to a manageable monthly figure.
EXTENDED_STANDARD_TERM_YEARS = 25

# Public Service Loan Forgiveness: 120 qualifying monthly payments -- ten years,
# and they need not be consecutive -- while working full time for a government
# or qualifying not-for-profit employer.
#
# Which plans COUNT is the part that decides everything else. Every
# income-driven plan qualifies (RAP, IBR, ICR, PAYE) and so does the 10-year
# Standard plan. The Tiered Standard Plan does NOT, and neither does Extended
# -- studentaid.gov says so explicitly, and says Tiered Standard does not even
# count toward TEPSLF.
#
# The 10-year Standard plan is a trap worth naming rather than modelling away:
# it qualifies, but it also retires the loan in exactly 120 payments, so there
# is nothing left to forgive. Someone counting on PSLF has to be on an
# income-driven plan for it to be worth anything.
# [Source: studentaid.gov/manage-loans/forgiveness-cancellation/public-service]
PSLF_QUALIFYING_PAYMENTS = 120
PSLF_QUALIFYING_YEARS = PSLF_QUALIFYING_PAYMENTS // 12
ROI_WINDOW_YEARS = 10

# Federal Direct (Subsidized + Unsubsidized) borrowing limits. These cap how
# much of a year's need can be met with federal Direct loans; anything above is
# "unmet need" that has to come from Direct PLUS or private/alternative loans at
# a higher rate (modeled as the gap-financing tranche, see split_loan_financing).
# Annual limits are combined sub+unsub by year in school; dependent students
# whose parents can't get PLUS get the independent limits in practice, so the
# toggle covers both. Aggregate = lifetime ceiling. Source: U.S. Dept. of
# Education / studentaid.gov (Federal Student Aid), 2024-25.
FEDERAL_DIRECT_ANNUAL_LIMITS = {
    "dependent":   {1: 5500, 2: 6500, 3: 7500, 4: 7500},   # year 4+ stays 7500
    "independent": {1: 9500, 2: 10500, 3: 12500, 4: 12500},
}
FEDERAL_DIRECT_AGGREGATE_CAP = {"dependent": 31000, "independent": 57500}
# Direct PLUS for PARENTS, post-OBBBA. Before July 1, 2026 this was "cost of
# attendance minus other aid received" -- i.e. no ceiling in practice, which is
# how this app modelled the gap tranche. It is now a real limit, and BOTH halves
# bind: four years at the annual limit is $80,000, above the $65,000 a family
# may borrow in total for one student. So the aggregate is what actually caps a
# four-year degree, and capping only the annual figure would overstate federal
# borrowing capacity by $15,000.
# [Source: studentaid.gov OBBBA definitions, "PLUS loans for parents", annual
# and aggregate tables, page updated 2026-07-06.]
PARENT_PLUS_ANNUAL_LIMIT = 20000
PARENT_PLUS_AGGREGATE_LIMIT = 65000   # per student, across BOTH parents combined
# Loans first disbursed on or after this date take the new limits.
PARENT_PLUS_LIMIT_EFFECTIVE_YEAR = 2026

# PROFESSIONAL-degree borrowing, post-OBBBA. Every path in this app carrying
# additional_training_debt is a professional degree -- MD, DDS/DMD, JD -- so
# these are the limits that apply to medical, dental and law school debt.
#
# The structural change is that Direct PLUS for graduate and professional
# borrowers is GONE. Before OBBBA a professional student borrowed $20,500/yr
# unsubsidized and put everything above it on Grad PLUS at "COA minus aid",
# which is why this app modelled that debt as gap financing -- correct then.
# Now the unsubsidized limit is $50,000/yr up to a $200,000 aggregate and there
# is no PLUS behind it, so that debt is a Direct Unsubsidized loan up to the
# cap and private money beyond it.
#
# Note the aggregate covers graduate/professional study only -- undergraduate
# borrowing does not count against it (the pre-OBBBA $138,500 did include
# undergrad; the replacement does not).
# [Source: studentaid.gov OBBBA definitions, "Professional students" annual and
# aggregate tables, page updated 2026-07-06.]
# Graduate (non-professional) borrowing, post-OBBBA. Lower on both counts than
# the professional limits below, and the aggregate covers graduate study only --
# undergraduate borrowing does not count against it.
GRADUATE_ANNUAL_UNSUB_LIMIT = 20500
GRADUATE_AGGREGATE_LIMIT = 100000
PROFESSIONAL_ANNUAL_UNSUB_LIMIT = 50000
PROFESSIONAL_AGGREGATE_LIMIT = 200000
# Direct Unsubsidized for graduate/professional borrowers, loans first disbursed
# 2026-07-01 to 2027-06-30. A constant rather than a sidebar input: the
# "Federal Direct rate" field sits in the undergraduate financing block and is
# about the undergraduate loan, and silently reusing it here would price
# medical school 1.5 points cheaper than the government does.
# [Source: studentaid.gov/understand-aid/types/loans/interest-rates]
PROFESSIONAL_DIRECT_RATE = 8.07
# There is also a $257,500 lifetime maximum across a student's own subsidized,
# unsubsidized and grad/professional PLUS borrowing. It is deliberately NOT
# modelled: the component caps above already bind well below it on every path
# this app has (the largest reachable federal total is $45,000 undergrad +
# $200,000 professional = $245,000), so implementing it would add a branch that
# can never fire. Parent PLUS is not counted toward it -- that is the parent's
# loan, not the student's.
# Loan origination (disbursement) fees, applied as a principal gross-up: the fee
# is deducted at disbursement, so you repay slightly more than you receive.
# Direct Sub/Unsub 1.057%, Direct PLUS 4.228% (loans disbursed 2024-25).
ORIGINATION_FEE = {"federal": 0.01057, "gap": 0.04228}
# Default interest rates for the two tranches (user-editable). Federal ~ recent
# undergraduate Direct rate; gap between recent PLUS (~9%) and private.
DEFAULT_FEDERAL_RATE = 6.5
DEFAULT_GAP_RATE = 8.5

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

# Enrollment length by BLS typical-entry-education, for occupations whose real
# program isn't four years. Only levels with a defensible standard length
# belong here.
#
# An associate's degree is two years essentially everywhere, so charging four
# years of Cost of Attendance to reach one overstated the debt by roughly
# double at a public school, and by ~23x for the common case of a two-year
# program done at a community college against a private four-year COA.
#
# Zero is a real length, not a missing one. BLS says 430 of the 825
# occupations in this dataset are entered with a high school diploma or no
# credential at all -- 52% of the list -- and the model charged every one of
# them four years of tuition and four years of foregone wages for a degree the
# job never asks for. Zero years means no loan, no enrollment gap, and a
# timeline that starts at 18 for both sides of the comparison. The earnings
# comparison itself still stands and stays interesting: Nuclear Power Reactor
# Operators need only a diploma and still out-earn the baseline heavily. What
# goes away is a cost that was never incurred.
#
# "Postsecondary nondegree award" and "Some college, no degree" stay out. The
# first spans a six-week certificate and an eighteen-month program; the second
# has no defined end at all. A guess there would be indistinguishable from data
# -- see MISMODELLED_EDUCATION_LEVELS.
# Graduate levels are ADDITIONAL years on top of a bachelor's, not a total.
# A master's is 2 years after 4; a doctorate 5 after 4. Keeping them here as
# the additional figure and adding UNDERGRAD_YEARS in program_years_for_education
# is what lets baseline_start_age_for and the loan schedule agree about when
# the person actually starts earning.
#
# 5 years for a doctorate is a placeholder, not a finding -- real programmes run
# 4 to 8. It is editable wherever it is shown, and the caption says so.
GRADUATE_ADDITIONAL_YEARS = {
    "Master's degree": 2,
    "Doctoral or professional degree": 5,
}

PROGRAM_YEARS_BY_EDUCATION = {
    "Associate's degree": 2,
    "High school diploma or equivalent": 0,
    "No formal educational credential": 0,
    **{level: UNDERGRAD_YEARS + extra
       for level, extra in GRADUATE_ADDITIONAL_YEARS.items()},
}

# The levels this app still models with the wrong program length -- i.e.
# everything it hasn't been taught a real length for. This gates the "we're
# charging you four years you don't need" disclosure and the break-even
# suppression, so teaching the app a new length above automatically stops
# treating that level as broken.
#
# It used to be SUB_BACHELORS_EDUCATION_LEVELS minus the known lengths, which
# made it structurally incapable of ever flagging a GRADUATE level: 113 of the
# 825 occupations in the careers file are master's or doctoral, every one was
# charged four undergraduate years, and the one mechanism for saying so could
# not reach them. Now it is every level the app has an opinion about, minus the
# ones with a real length -- which is the question the name was always asking.
ALL_EDUCATION_LEVELS = SUB_BACHELORS_EDUCATION_LEVELS | set(GRADUATE_ADDITIONAL_YEARS)
MISMODELLED_EDUCATION_LEVELS = (
    ALL_EDUCATION_LEVELS - set(PROGRAM_YEARS_BY_EDUCATION)
)

# Which credential an occupation's BLS entry-education implies, for the loan
# limits. Graduate borrowing has its own annual and aggregate caps and its own
# Direct rate, and no Parent PLUS at all.
GRADUATE_EDUCATION_LEVELS = set(GRADUATE_ADDITIONAL_YEARS)

# What the visitor says they are studying, where the app cannot derive it.
# Career mode reads BLS's typical_education and needs no input; Major mode has
# no education field at all (a major is not an occupation), so it must ask.
# The values map onto the same GRADUATE_ADDITIONAL_YEARS above so both routes
# produce identical program lengths.
CREDENTIAL_BACHELORS = "Bachelor's degree"
CREDENTIAL_MASTERS = "Master's degree"
CREDENTIAL_DOCTORAL = "Doctoral or professional degree"
CREDENTIAL_OPTIONS = [CREDENTIAL_BACHELORS, CREDENTIAL_MASTERS, CREDENTIAL_DOCTORAL]
CREDENTIAL_LABELS = {
    CREDENTIAL_BACHELORS: "Bachelor's",
    CREDENTIAL_MASTERS: "Master's",
    CREDENTIAL_DOCTORAL: "Doctorate",
}
# The credential key used in data/graduate_debt_clean.csv.
CREDENTIAL_DATA_KEY = {
    CREDENTIAL_MASTERS: "master",
    CREDENTIAL_DOCTORAL: "doctoral",
}


def is_graduate_education(typical_education: str) -> bool:
    """True when this occupation is entered with a master's or doctorate, so
    the graduate loan limits apply rather than the undergraduate ones."""
    return (typical_education or "") in GRADUATE_EDUCATION_LEVELS


def program_years_for_education(typical_education: str) -> int:
    """How many years of enrollment the cost model should charge for an
    occupation with this BLS typical-entry-education. UNDERGRAD_YEARS for
    anything without a specific length, which is every bachelor's-and-above
    occupation and every major."""
    return PROGRAM_YEARS_BY_EDUCATION.get(typical_education or "", UNDERGRAD_YEARS)


def program_years_for_context(typical_education: str, returning: bool = False) -> int:
    """Years of enrollment to charge, for THIS visitor rather than for the
    path in the abstract.

    A first-time student pursuing a master's-level career needs the bachelor's
    too, so the figure is 4 + 2. A RETURNING student going back for a master's
    already holds the bachelor's -- they attend and pay for the graduate years
    only. Charging them the full 6 bills them for a degree they have, which is
    what this app did the moment graduate lengths were introduced.

    A returning student pursuing a BACHELOR'S is the ordinary undergraduate
    case and keeps the full length: they do not have that degree yet.
    """
    total = program_years_for_education(typical_education)
    if not returning:
        return total
    graduate = graduate_years_for_education(typical_education)
    return graduate if graduate else total


def graduate_years_for_education(typical_education: str) -> int:
    """Of the total above, how many years are GRADUATE study. Zero for every
    undergraduate path. The loan model needs the split because the two halves
    have different annual limits, different aggregate caps and different rates
    -- and because Parent PLUS exists for one and not the other."""
    return GRADUATE_ADDITIONAL_YEARS.get(typical_education or "", 0)


def program_years_for_major(major_name: str) -> int:
    """Enrollment length for a selection, read off MAJOR_DATA. The counterpart
    to resolve_program_years for code that runs *after* the Career section has
    built that dict -- the results page and the PDF generators. Both funnel
    through program_years_for_education so the two paths can't disagree about
    what an associate's degree costs."""
    return program_years_for_education(
        MAJOR_DATA.get(major_name, {}).get("typical_education"))

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

# Every cc_mode that means "some or all of this happens at a community
# college". Gated on in four separate places (the chart label, the on-screen
# note, the PDF bundle and the PDF rows), so it lives here rather than being
# spelled out inline each time -- a mode added to the radio and missed in one
# of those reads as a straight four-year start in that one surface only.
CC_PATH_MODES = ("fulltime", "parttime", "associate")


def cc_path_options(program_years: int) -> tuple:
    """(options, labels) for the Community college path radio, given how long
    the selected program actually runs.

    A transfer is meaningless when the whole program fits inside community
    college: an associate's degree earned there *is* the degree, and there's no
    four-year school to move on to. So a two-year program gets an explicit
    no-transfer option in place of the 2+2 one, rather than a fourth choice
    sitting alongside it that quietly does the same thing.

    Worth being precise about what this changes: once cc_years got clamped to
    the program length, "full-time community college, then transfer" already
    produced exactly this outcome for a two-year program -- two community
    college years, no university years, no loan. The arithmetic was right and
    the label was a lie, promising a transfer that never happened and a "2+2"
    that was really 2+0. This makes the option say what the model already did.
    """
    if program_years <= COMMUNITY_COLLEGE_YEARS:
        return (
            ["none", "associate", "parttime"],
            {
                "none": "None — earn the whole degree at the school above",
                "associate": f"Full-time community college — the entire "
                             f"{program_years}-year degree, no transfer",
                "parttime": "Part-time community college while working — no transfer",
            },
        )
    return (
        ["none", "fulltime", "parttime"],
        {
            "none": "None — start at the 4-year school",
            "fulltime": "Full-time community college, then transfer (2+2)",
            "parttime": "Part-time community college while working, then transfer",
        },
    )


def reconcile_cc_mode(state_key: str, options: list) -> None:
    """Keep a stored cc_mode valid when the option list changes under it.

    Switching the selected occupation between a bachelor's one and an
    associate's one swaps "fulltime" for "associate"; leaving the stale value
    in session_state makes Streamlit raise on a radio whose current value isn't
    among its options. The two mean the same thing to the visitor (full-time
    community college), so they map across rather than silently resetting a
    chosen path back to "none".
    """
    current = st.session_state.get(state_key)
    if current in options:
        return
    equivalent = {"fulltime": "associate", "associate": "fulltime"}.get(current)
    st.session_state[state_key] = equivalent if equivalent in options else "none"


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

# {state_key: metro} for the states holding exactly ONE of the cities above.
# Built rather than hand-listed so adding a city to CITY_DATA can't leave a
# stale mapping behind: a second Ohio metro would drop OH from this dict
# automatically instead of silently keeping whichever was written down first.
#
# Deliberately excludes states with more than one metro. California, Texas
# and Ohio hold nine cities between them, and a school's state does not say
# which -- a Los Angeles school and a San Francisco school are both "CA", and
# guessing between them would put the wrong wage level AND the wrong cost of
# living on the page. The committed college dataset carries STABBR but no
# city (see clean_college_scorecard.py), so state is the finest granularity
# available offline; those states are left to the visitor.
def _single_metro_states() -> dict:
    by_state = {}
    for metro, info in CITY_DATA.items():
        # "National Average" carries state_key None -- it is a pseudo-city, not
        # anywhere a school can be, so it must not become the answer for a
        # school whose STABBR is missing.
        if info["state_key"] in US_STATES:
            by_state.setdefault(info["state_key"], []).append(metro)
    return {state: metros[0] for state, metros in by_state.items() if len(metros) == 1}


SINGLE_METRO_BY_STATE = _single_metro_states()


def metro_for_school(coa_match) -> str:
    """The app's metro area for the state a school sits in, or None when the
    school is unknown, its state holds several metros, or it holds none.

    "Where you study" is not "where you work", and this only seeds the City /
    Metro Area control -- which drives post-graduation wages and cost of
    living -- with the likelier of the two. The visitor can always change it,
    and the sidebar says so whenever this has fired."""
    if coa_match is None:
        return None
    state = coa_match.get("STABBR")
    return SINGLE_METRO_BY_STATE.get(state) if state in US_STATES else None

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

# ---- Pre/post impact measurement -------------------------------------------
# The exit survey asks whether the tool changed the visitor's view -- one
# retrospective self-report, asked after everything, requiring them to
# introspect on a change they may never have noticed. There has never been
# anything to difference it against: no question is asked anywhere before the
# numbers appear.
#
# These two get asked TWICE -- once above the results and once inside the exit
# survey -- so the paper can report a measured shift rather than a remembered
# one. Both are ordered categories, so a pair yields a signed step difference.
#
# Bands rather than a free-numeric borrowing field on purpose: a text box
# placed after the app has just displayed a loan figure measures how well the
# visitor read that figure, not what they intend to borrow.
#
# WILLINGNESS, not expectation, for the same reason and more sharply. The
# sidebar shows a loan amount at page load, so "how much do you expect to
# borrow?" asked afterwards is partly a reading test of a number already on
# screen. The MOST you would be willing to take on is a threshold the app
# never states anywhere, so the post answer has to be generated rather than
# recalled -- which is the whole point of asking twice.
#
# "Not sure" / "Haven't decided" are real answers and are scored as their own
# category, never as a midpoint. Someone moving from "Haven't decided" to a
# band is the clearest evidence the tool did something, and averaging it into
# a number would erase exactly that.
#
# Stored as CODES, not these display labels. analyze_survey.py's
# PERCEPTION_ORDER is a hand-copy of app.py's radio list, so rewording one
# side silently reindexes the cross-tab to NaN -- survivable for a category
# count, not for an ORDINAL item whose analysis subtracts two values, where a
# broken map yields a wrong number instead of an obvious blank.
PRESURVEY_SCHOOLS_OPTIONS = {
    "0": "s0", "1": "s1", "2": "s2", "3": "s3", "4": "s4",
    "5 or more": "s5plus", "Not sure": "unsure",
}
PRESURVEY_BORROWING_OPTIONS = {
    "Nothing — I don't want to borrow": "n0",
    "Up to $10,000": "b1",
    "$10,000-$30,000": "b2",
    "$30,000-$60,000": "b3",
    "$60,000-$100,000": "b4",
    "More than $100,000": "b5",
    "Haven't decided": "undecided",
}

# Asked before the numbers and again after. The post wording deliberately does
# NOT ask "did this change your mind?" -- that invites the respondent to report
# the change they think is expected of them. Asking for a current state and
# differencing it ourselves keeps the inference on our side of the instrument.
PRESURVEY_SCHOOLS_QUESTION = "How many colleges are you seriously considering right now?"
PRESURVEY_BORROWING_QUESTION = (
    "What's the most total student debt you'd be willing to take on for this path?")
POSTSURVEY_SCHOOLS_QUESTION = "How many colleges are you seriously considering now?"
POSTSURVEY_BORROWING_QUESTION = (
    "Now, what's the most total student debt you'd be willing to take on for this path?")

# Asked FIRST, in the pre block, because it decides what else to ask. The
# option set gains "Counselor": the outreach this app is built around goes to
# high-school and community-college counsellors, and until now they had to
# file themselves under Teacher or Other.
PRESURVEY_ROLE_OPTIONS = ["Student", "Parent", "Counselor", "Teacher", "Other"]

# Roles for whom "the most debt you'd be willing to take on" is not a question
# about themselves. A counsellor answering it is either guessing on a
# student's behalf or answering about their own long-past finances -- either
# way it is not the quantity the paired test differences, and averaging it in
# would add noise to the one measure the design exists to produce.
#
# They still get the schools question: a counsellor advising students plausibly
# does hold a consideration set, and widening it is exactly what the tool is
# for.
#
# Teacher is arguably in the same position and is deliberately NOT listed --
# flagged for a decision rather than assumed, since a teacher could as easily
# be a parent of a college-bound child answering for themselves.
ROLES_WITHOUT_BORROWING = {"Counselor"}

# Floor on research participation. The CALCULATOR stays open to everyone -- it
# is a public information tool and nothing about using it is research -- but
# the survey instruments are not offered to a student below this age.
#
# 18 is the load-bearing number, not a rounded-up 17. At 18 a respondent is an
# adult in every state but Alabama and Nebraska (19) and consents for
# themselves, which takes the study out of Subpart D entirely: no parental
# permission, no child assent, and the anonymous-survey exemption at
# 45 CFR 46.104(d)(2) becomes available -- it is unavailable for children,
# reaching them only for educational tests and non-participant observation.
# At 17 none of that is true; a 17-year-old is a child under 45 CFR 46.402(a),
# and every one of those requirements still applies. The floor is the
# difference between a study that needs parental permission and one that does
# not.
#
# The cost is real and belongs in the paper rather than a footnote: the
# intended population is high-school seniors, most of whom are 16-17. A
# visitor graduating in 2028-2030 is 14-16 today and cannot participate at
# all. Any claim about "high school students" must be read as "students
# already 18" -- an older, smaller, and differently-situated group than the
# one the tool was built for.
#
# Only asked of Students. Parent/Counselor/Teacher are adult roles by
# construction -- asking them to attest reads as an accusation and collects
# nothing.
RESEARCH_MIN_AGE = 18
# Every role, not just Student. The consent says "You must be 18 or over" with
# no qualification, and enforcing it for one role made the check narrower than
# the promise -- an under-18 selecting "Other" submitted with no age check at
# all. Kept as a set rather than collapsed to a boolean because the pre-survey
# still asks role FIRST and can react to it, where the exit form cannot.
ROLES_REQUIRING_AGE_ATTESTATION = set(PRESURVEY_ROLE_OPTIONS)

# Lower-case codes for the log line, so a reworded option label cannot
# silently change what a stored value means.
_ROLE_CODES = {role: role.lower() for role in PRESURVEY_ROLE_OPTIONS}

# Bumped whenever an option set or question wording changes. Without it,
# "declined the pre" (version set, answers NULL) and "predates the pre"
# (version NULL) are the same NULL, and the denominator of the pre-response
# rate is silently wrong. Same reasoning as hs_baseline_age_aware: keep
# writing a near-constant column because it is the only thing telling two
# eras apart.
PRESURVEY_INSTRUMENT_VERSION = "v1"

# Who gets asked, by default. The pre-survey renders ABOVE the results and is
# the only real friction in the instrument; the exit survey sits at the very
# bottom, below the charts and Methodology, where a visitor who does not scroll
# never meets it. So the default is pre off, post on: ordinary traffic gets a
# calculator with nothing in the way and still yields perception_change, the
# item H1 and H2 are measured on.
#
# ?research=1 overrides BOTH to on -- see research_link(). Recruitment links
# already carry a ?src= tag, so &research=1 costs nothing to distribute, and it
# means paired pre/post data comes from people who were actually recruited.
#
# Constants, not an admin checkbox: st.session_state is per-visitor, so a
# checkbox would switch the survey off for the admin alone and for nobody else.
# Flipping these is a commit, which also puts the on/off history in git --
# "when was the instrument running" is analysis metadata, and without it a
# later reader cannot tell a quiet period from a period when nothing was asked.
PRESURVEY_ENABLED = False
POSTSURVEY_ENABLED = True

# ---- Budget-first school search (fields of study) ---------------------------
# The 38 two-digit CIP families the College Scorecard reports program flags
# for, as carried in data/college_coa_clean.csv's programs_* columns (see
# clean_college_scorecard.py). Titles are shortened from NCES's official CIP
# series titles for a sidebar-width control.
#
# Two digits is the ONLY granularity available for a whole-dataset filter.
# Finer 4-digit programs exist in the Scorecard API but only per school, one
# request each, so they cannot filter 5,035 rows -- the same constraint that
# keeps per-school earnings out of the filter.
#
# The consequence is real and is handled by LABELLING, not by pretending
# otherwise: six NY Fed majors (Accounting, Business Analytics, Business
# Management, Finance, General Business, Marketing) all live in family 52 and
# therefore return the same schools, and family 51 spans nursing through
# massage therapy. The UI names the FAMILY rather than the major, so an
# identical result set for Finance and Marketing reads as what it is -- one
# field of study -- instead of looking broken.
CIP_FAMILY_TITLES = {
    "01": "Agriculture & Related Sciences",
    "03": "Natural Resources & Conservation",
    "04": "Architecture",
    "05": "Area, Ethnic & Gender Studies",
    "09": "Communication & Journalism",
    "10": "Communications Technologies",
    "11": "Computer & Information Sciences",
    "12": "Personal & Culinary Services",
    "13": "Education",
    "14": "Engineering",
    "15": "Engineering Technologies",
    "16": "Foreign Languages & Literatures",
    "19": "Family & Consumer Sciences",
    "22": "Legal Professions & Studies",
    "23": "English Language & Literature",
    "24": "Liberal Arts & General Studies",
    "25": "Library Science",
    "26": "Biological & Biomedical Sciences",
    "27": "Mathematics & Statistics",
    "29": "Military Technologies",
    "30": "Multi/Interdisciplinary Studies",
    "31": "Parks, Recreation & Fitness",
    "38": "Philosophy & Religious Studies",
    "39": "Theology & Religious Vocations",
    "40": "Physical Sciences",
    "41": "Science Technologies",
    "42": "Psychology",
    "43": "Homeland Security, Law Enforcement & Firefighting",
    "44": "Public Administration & Social Service",
    "45": "Social Sciences",
    "46": "Construction Trades",
    "47": "Mechanic & Repair Technologies",
    "48": "Precision Production",
    "49": "Transportation & Materials Moving",
    "50": "Visual & Performing Arts",
    "51": "Health Professions",
    "52": "Business, Management & Marketing",
    "54": "History",
}

# UI label -> (programs_* column suffix, nominal years). The years are the
# length of THAT credential, used to turn a per-year cost into a program
# total. They are deliberately NOT program_years_for_major: a bachelor's
# result list is four years regardless of which occupation the visitor has
# selected in the sidebar.
CREDENTIAL_LEVELS = {
    "Bachelor's degree": ("bachl", 4),
    "Associate's degree": ("assoc", 2),
    "Certificate (2-4 years)": ("cert4", 2),
    "Certificate (1-2 years)": ("cert2", 1),
    "Certificate (under 1 year)": ("cert1", 1),
}

# NY Fed major -> CIP family, for prefilling the search when the visitor is in
# Major mode. A major and a CIP family are both fields of STUDY, so this is a
# direct correspondence rather than a crosswalk.
#
# None where no single family is defensible. That follows SINGLE_METRO_BY_STATE,
# which refuses to guess between Los Angeles and San Francisco for a California
# school: a wrong prefill here is worse than no prefill, because the visitor
# would have to notice it was wrong before they could correct it.
#
# There is deliberately NO equivalent for Career mode's 836 occupations.
# Occupation -> field of study is the SOC-CIP crosswalk whose own documentation
# calls it conceptual rather than empirical, and which this codebase already
# declined to rely on for underemployment.
MAJOR_TO_CIP_FAMILY = {
    "Accounting": "52", "Advertising and Public Relations": "09",
    "Aerospace Engineering": "14", "Agriculture": "01",
    "Animal and Plant Sciences": "01", "Anthropology": "45",
    "Architecture": "04", "Art History": "50", "Biochemistry": "26",
    "Biology": "26", "Business Analytics": "52", "Business Management": "52",
    "Chemical Engineering": "14", "Chemistry": "40", "Civil Engineering": "14",
    "Commercial Art & Graphic Design": "50", "Communications": "09",
    "Computer Engineering": "14", "Computer Science": "11",
    "Construction Services": "46", "Criminal Justice": "43",
    "Early Childhood Education": "13", "Earth Sciences": "40",
    "Economics": "45", "Electrical Engineering": "14",
    "Elementary Education": "13", "Engineering Technologies": "15",
    "English Language": "23", "Environmental Studies": "03",
    "Ethnic Studies": "05", "Family and Consumer Sciences": "19",
    "Finance": "52", "Fine Arts": "50", "Foreign Language": "16",
    "General Business": "52", "General Education": "13",
    "General Engineering": "14", "General Social Sciences": "45",
    "Geography": "45", "Health Services": "51", "History": "54",
    "Industrial Engineering": "14", "Information Systems & Management": "11",
    "Interdisciplinary Studies": "30", "International Affairs": "45",
    "Journalism": "09", "Leisure and Hospitality": "31", "Liberal Arts": "24",
    "Marketing": "52", "Mass Media": "09", "Mathematics": "27",
    "Mechanical Engineering": "14", "Medical Technicians": "51",
    "Miscellaneous Biological Science": "26", "Miscellaneous Education": "13",
    "Miscellaneous Engineering": "14", "Miscellaneous Physical Sciences": "40",
    "Miscellaneous Technologies": None,   # spans 10/15/41/47/48 -- no one family
    "Nursing": "51", "Nutrition Sciences": "51", "Performing Arts": "50",
    "Pharmacy": "51", "Philosophy": "38", "Physics": "40",
    "Political Science": "45", "Psychology": "42",
    "Public Policy and Law": "44", "Secondary Education": "13",
    "Social Services": "44", "Sociology": "45", "Special Education": "13",
    "Theology and Religion": "39", "Treatment Therapy": "51",
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


@st.cache_data(show_spinner=False)
def careers_for_major(soc_group: str, csv_path: str, limit: int = 6) -> list:
    """Example BLS occupations a major commonly leads to, for the "Careers this
    major leads to" section (Major mode).

    A NY Fed major already carries a 2-digit SOC major group (NYFED_MAJOR_SOC_
    GROUP); this returns real occupations from that same group in the app's own
    BLS dataset, each with its real median wage -- so "what does this major lead
    to" is answered from data already in the repo, not from any external (and in
    this case copyrighted/paywalled) careers reference.

    Restricted toward bachelor's-level+ roles: an SOC major group also contains
    sub-bachelor's occupations (e.g. Ushers under "Arts, Design, Entertainment,
    Sports & Media") that a four-year major doesn't lead to, and listing them
    would misrepresent the degree. If that filter leaves too few (<3) to be
    useful, fall back to the unfiltered group rather than show a near-empty list.

    Read via load_bls_careers (already cached) rather than re-reading the CSV.
    soc_group and csv_path are passed args, not globals, so the @st.cache_data
    key stays correct (same reason find_breakeven_loan takes its rate as an arg).
    Returns [(occ_title, median_salary), ...] sorted by median desc, capped at
    `limit`; empty list when the group is None/unknown or the dataset is missing.
    """
    if not soc_group:
        return []
    careers = load_bls_careers(csv_path)
    in_group = [
        (title, info["median_salary"])
        for title, info in careers.items()
        if info.get("soc_major_group") == soc_group and info.get("median_salary")
    ]
    # A 2-digit SOC major group spans every education level, so an unfiltered
    # list misrepresents the degree in BOTH directions: it pulls in sub-
    # bachelor's roles a four-year major doesn't lead to (Ushers under Arts),
    # AND advanced-degree roles a bachelor's alone doesn't reach (Pediatric
    # Surgeons under Nursing). The NY Fed majors are all bachelor's-level, so
    # prefer occupations BLS marks as bachelor's-entry -- the truest "what this
    # degree leads to". Degrade gracefully if that leaves too few: bachelor's-or-
    # above (drop only sub-bachelor's), then the whole group, so a small or
    # oddly-classified group still shows something rather than nothing.
    bachelors_only = [(t, m) for t, m in in_group
                      if careers[t].get("typical_education") == "Bachelor's degree"]
    bachelors_plus = [(t, m) for t, m in in_group
                      if careers[t].get("typical_education") not in SUB_BACHELORS_EDUCATION_LEVELS]
    if len(bachelors_only) >= 3:
        pool = bachelors_only
    elif len(bachelors_plus) >= 3:
        pool = bachelors_plus
    else:
        pool = in_group
    pool.sort(key=lambda tm: tm[1], reverse=True)
    return pool[:limit]

# The AI Employability Risk feature keys off SOC occupation major groups, which
# Career-mode (BLS) and CURATED_MAJOR_DATA carry directly. Major mode's NY Fed
# dataset is majors, not occupations, so it has no SOC code -- this maps each
# major to the occupation major group it most commonly leads to, so Major mode
# shows a representative exposure level instead of "Unknown". It's a deliberate
# approximation (a major spreads across many jobs, which is exactly what the NY
# Fed data measures) and is labeled as such on the page. Majors that genuinely
# span the whole labor market (Interdisciplinary Studies, Liberal Arts) are left
# out on purpose -- they honestly have no single representative occupation.
NYFED_MAJOR_SOC_GROUP = {
    "Accounting": "13", "Advertising and Public Relations": "27",
    "Aerospace Engineering": "17", "Agriculture": "19",
    "Animal and Plant Sciences": "19", "Anthropology": "19", "Architecture": "17",
    "Art History": "27", "Biochemistry": "19", "Biology": "19",
    "Business Analytics": "13", "Business Management": "11",
    "Chemical Engineering": "17", "Chemistry": "19", "Civil Engineering": "17",
    "Commercial Art & Graphic Design": "27", "Communications": "27",
    "Computer Engineering": "15", "Computer Science": "15",
    "Construction Services": "11", "Criminal Justice": "33",
    "Early Childhood Education": "25", "Earth Sciences": "19", "Economics": "19",
    "Electrical Engineering": "17", "Elementary Education": "25",
    "Engineering Technologies": "17", "English Language": "27",
    "Environmental Studies": "19", "Ethnic Studies": "19",
    "Family and Consumer Sciences": "25", "Finance": "13", "Fine Arts": "27",
    "Foreign Language": "27", "General Business": "13", "General Education": "25",
    "General Engineering": "17", "General Social Sciences": "19", "Geography": "19",
    "Health Services": "29", "History": "19", "Industrial Engineering": "17",
    "Information Systems & Management": "15", "International Affairs": "19",
    "Journalism": "27", "Leisure and Hospitality": "11", "Marketing": "13",
    "Mass Media": "27", "Mathematics": "15", "Mechanical Engineering": "17",
    "Medical Technicians": "29", "Miscellaneous Biological Science": "19",
    "Miscellaneous Education": "25", "Miscellaneous Engineering": "17",
    "Miscellaneous Physical Sciences": "19", "Miscellaneous Technologies": "17",
    "Nursing": "29", "Nutrition Sciences": "29", "Performing Arts": "27",
    "Pharmacy": "29", "Philosophy": "25", "Physics": "19",
    "Political Science": "19", "Psychology": "19", "Public Policy and Law": "23",
    "Secondary Education": "25", "Social Services": "21", "Sociology": "19",
    "Special Education": "25", "Theology and Religion": "21",
    "Treatment Therapy": "29",
}

# ---- 2026 Federal Repayment Plans: RAP & Tiered Standard (optional "Advanced Analysis" mode) -
# Real, enacted federal law: the One Big Beautiful Bill Act (H.R. 1, 2025)
# replaces existing IDR plans with the Repayment Assistance Plan (RAP) and
# introduces a Tiered Standard Plan, both effective for new federal loan
# borrowers July 1, 2026 (existing borrowers transition by July 1, 2028).
# Source: U.S. Dept. of Education, "Fact Sheet: The Trump Administration Is
# Simplifying Student Loan Repayment" (ed.gov), corroborated by CRS In Focus
# IF13075. Figures below are administratively simplified, like this app's
# existing IDR model -- see the Methodology footer for the same caveat.
RAP_DEPENDENT_REDUCTION = 50  # $/month per dependent
RAP_MIN_PAYMENT = 10  # $/month floor on the payment ITSELF, after the
                      # dependent deduction -- not just the lowest AGI band.
                      # studentaid.gov: "your monthly payment amount can never
                      # be less than $10."
RAP_MAX_TERM_YEARS = 30  # forgiveness after 360 on-time payments
RAP_PRINCIPAL_MATCH_CAP = 50  # $/month government principal-match subsidy

# Which income-driven plan a borrower can actually get, by when they borrow.
# IBR -- what IDR_* above models -- is closed to loans originated on or after
# July 1, 2026. From then the income-driven plan is RAP. Offering IBR to a
# visitor starting in 2026 modelled a plan they cannot choose, on the app's own
# default start year.
# [Source: TICAS, "Comparing Income-Driven Repayment Plans", 2025-09-16.]
STANDARD_STRATEGY_LABEL = "Standard 10-Year"
IDR_STRATEGY_LABEL = "Income-Driven Repayment (IDR)"
RAP_STRATEGY_LABEL = "Repayment Assistance Plan (RAP)"
TIERED_STANDARD_STRATEGY_LABEL = "2026 Tiered Standard Plan"
RAP_FIRST_ORIGINATION_YEAR = 2026
# The pre-OBBBA pair. Reachable only via the Advanced Analysis toggle, or on a
# start year before the cutoff -- which the start-year list no longer offers.
LEGACY_STRATEGY_LABELS = [STANDARD_STRATEGY_LABEL, IDR_STRATEGY_LABEL]
# What a 2026 plan replaced, for mapping an older shared link onto the list a
# scenario actually offers. Standard 10-Year's successor is the Tiered Standard
# Plan; IBR's is RAP.
LEGACY_STRATEGY_SUCCESSOR = {
    STANDARD_STRATEGY_LABEL: TIERED_STANDARD_STRATEGY_LABEL,
    IDR_STRATEGY_LABEL: RAP_STRATEGY_LABEL,
}


REPAYMENT_STRATEGY_HELP = (
    "The two plans OBBBA gives a borrower whose loans start on or after "
    "July 1, 2026. Repayment Assistance Plan (RAP): payment is 1-10% of your "
    "total income, all unpaid interest is waived, and any remainder is "
    "forgiven after 30 years -- forgiven amounts are taxable income that year. "
    "2026 Tiered Standard Plan: a fixed payment over a term set by how much "
    "you owe, forgiving nothing. The pre-2026 plans (Standard 10-Year and "
    "IBR-style IDR) are closed to new loans; tick 'Compare against pre-2026 "
    "plans' under Advanced Analysis to add them back for comparison. "
    "See Methodology."
)


def income_driven_label_for(start_year) -> str:
    """The income-driven plan available to someone starting in `start_year`.

    Keyed on the start year rather than each year's disbursement: the app
    models one balance at one plan, and a borrower already enrolled before the
    cutoff keeps the old regime under OBBBA's interim exception anyway. A 2025
    starter whose later years cross into RAP is the case this simplifies, and
    the Methodology says so.
    """
    try:
        return (RAP_STRATEGY_LABEL if int(start_year) >= RAP_FIRST_ORIGINATION_YEAR
                else IDR_STRATEGY_LABEL)
    except (TypeError, ValueError):
        return RAP_STRATEGY_LABEL


def repayment_strategy_options_for(start_year, include_legacy: bool = False) -> list:
    """The plans this scenario can actually be repaid under.

    For a start year on or after the cutoff those are OBBBA's two: RAP first,
    because the income-driven plan is the one whose payment a borrower can
    influence and the one this app exists to reason about, then the Tiered
    Standard Plan. Standard 10-Year and IBR are not offered -- a loan
    originated then cannot be repaid under either -- unless the visitor asks
    for them via Advanced Analysis, which is there for comparing against the
    old rules rather than for pretending they still apply.
    """
    if income_driven_label_for(start_year) != RAP_STRATEGY_LABEL:
        return list(LEGACY_STRATEGY_LABELS)
    options = [RAP_STRATEGY_LABEL, TIERED_STANDARD_STRATEGY_LABEL]
    return options + LEGACY_STRATEGY_LABELS if include_legacy else options


def resolve_shared_strategy(shared_value, options) -> str:
    """A strategy label from a shared link, mapped onto the options this
    scenario actually offers.

    A link built under one start year can name the other era's plan. Falling
    back to index 0 would silently turn an income-driven scenario into a
    Standard one -- the strategy is the whole point of such a link, so map
    income-driven to income-driven and only then give up.
    """
    if shared_value in options:
        return shared_value
    if not shared_value:
        return options[0]
    # Map a superseded plan onto the one that replaced it, rather than dropping
    # to index 0. A link built under the old rules names a real choice; turning
    # "Standard 10-Year" into RAP because RAP happens to be first would invert
    # what it was sharing.
    successor = LEGACY_STRATEGY_SUCCESSOR.get(shared_value)
    if successor in options:
        return successor
    if shared_value in (IDR_STRATEGY_LABEL, RAP_STRATEGY_LABEL, "Income-Driven Repayment"):
        return RAP_STRATEGY_LABEL if RAP_STRATEGY_LABEL in options else options[0]
    if shared_value in (STANDARD_STRATEGY_LABEL, TIERED_STANDARD_STRATEGY_LABEL):
        for candidate in (TIERED_STANDARD_STRATEGY_LABEL, STANDARD_STRATEGY_LABEL):
            if candidate in options:
                return candidate
    return options[0]

# Who is going to school. The app has always modelled one person -- an
# 18-year-old starting a first degree, measured against a debt-free high school
# graduate -- and that is now the minority case. Returning mode measures the
# visitor against their own current salary instead.
#
# These sit in section 1, not beside their radio in section 4, because
# counterfactual_vocab() below names the baseline in prose and has to know
# which one is in play -- and section 2 is the half analyze_model.py execs.
STUDENT_MODE_FIRST = "Straight from high school"
STUDENT_MODE_RETURNING = "Going back to school"
STUDENT_MODE_OPTIONS = [STUDENT_MODE_FIRST, STUDENT_MODE_RETURNING]
RETURNING_STOP_WORK = "No — I'll study full-time"
RETURNING_KEEP_WORKING = "Yes — evenings, online or part-time"
RETURNING_ENROLLMENT_OPTIONS = [RETURNING_KEEP_WORKING, RETURNING_STOP_WORK]


# ============================================================
# 2. HELPER FUNCTIONS
# ============================================================

# ---- 2a. Formatting -----------------------------------------------------

# What the ROI figures are measured AGAINST, in words. The model already swaps
# the baseline when returning mode is on (calculate_roi's baseline_curve), but
# every sentence describing it was written when there was only one baseline, so
# a 49-year-old on $200k was told she "earns less than a debt-free high school
# graduate" -- correct arithmetic under a label naming the wrong person.
#
# One dict, read by the on-screen page, the PDF and the break-even verdicts
# alike, for the same reason the chart builders share their data: three sets of
# hand-written strings drift, and the drift is invisible because each one reads
# fine on its own.
_COUNTERFACTUAL_FIRST = {
    "baseline_noun": "a debt-free high school graduate",
    "metric_label": "High School Grad",
    "legend_label": "High School Graduate",
    "no_loan_suffix": " (No Loan)",
    "window_phrase": "your first {years} years after high school",
    "instead_of": "skipping college and working right away",
    "instead_of_short": "skipping college",
    "head_start": "the high school graduate was working while you were enrolled",
}
_COUNTERFACTUAL_RETURNING = {
    # Not "debt-free": a returning student's existing loans are owed on either
    # path, so the baseline carries them too and they cancel out of the
    # premium. Saying "debt-free" here would describe a person who doesn't
    # exist in this comparison.
    "baseline_noun": "staying in your current job",
    "metric_label": "Staying Put",
    "legend_label": "Your Current Path",
    # "No NEW loan" -- the existing debt is in both paths, so the distinction
    # this suffix draws is about the degree's loan, not about being debt-free.
    "no_loan_suffix": " (No New Loan)",
    "window_phrase": "the next {years} years",
    "instead_of": "staying where you are",
    "instead_of_short": "staying where you are",
    "head_start": "your current job kept paying while you were enrolled",
}


def counterfactual_vocab() -> dict:
    """The words for whichever baseline this session is being measured against.

    Reads session_state defensively: analyze_model.py execs sections 1-2
    outside a Streamlit runtime, and it models first-time students only, so the
    high-school vocabulary is the correct fallback there rather than an error.
    """
    try:
        returning = st.session_state.get("student_mode_radio") == STUDENT_MODE_RETURNING
    except Exception:
        returning = False
    return _COUNTERFACTUAL_RETURNING if returning else _COUNTERFACTUAL_FIRST


def fmt_money(value):
    return f"${value:,.0f}"


def fmt_money_k(value) -> str:
    """A money axis tick in thousands: 250000 -> "$250k".

    Plotly picks its own SI prefix and flips to "M" once a series passes a
    million, so a ten-year net position read "$0.2M ... $1M" while the loan
    balance beside it read "$2k ... $10k" -- two money axes on one page in two
    different units. Fixing the unit to thousands makes them directly
    comparable, and "$250k" is the register the rest of this app already
    speaks in.

    Sub-thousand values keep their dollars ("$500"), since rounding them to
    "$1k" or "$0k" would be worse than the inconsistency."""
    if value is None:
        return ""
    # Sign outside the dollar sign: "-$49k", not "$-49k". Negative values are
    # not an edge case here -- a training-heavy path like medicine sits below
    # zero for years on the net-position chart.
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if magnitude < 1000:
        return f"{sign}${magnitude:,.0f}"
    return f"{sign}${magnitude / 1000:,.0f}k"


def money_k_ticks(values) -> tuple:
    """(tickvals, ticktext) in thousands for a Plotly money axis, spanning the
    data. Plotly has no "always use k" option -- tickformat is a d3 format
    string and cannot divide -- so the ticks are placed explicitly."""
    finite = [v for v in values if v is not None and v == v]
    if not finite:
        return [], []
    low, high = min(0, min(finite)), max(finite)
    span = high - low or 1
    # A step from the 1/2/5 ladder that yields roughly 6 ticks.
    rough = span / 6
    magnitude = 10 ** math.floor(math.log10(rough)) if rough > 0 else 1
    step = next((m * magnitude for m in (1, 2, 5, 10) if m * magnitude >= rough),
                 10 * magnitude)
    start = math.floor(low / step) * step
    vals, v = [], start
    while v <= high + step * 0.5:
        vals.append(v); v += step
    return vals, [fmt_money_k(v) for v in vals]


def fmt_pct(value):
    return f"{value:.1f}%"


# Colour for the panel headings that say WHICH scenario or career stage a
# block of numbers belongs to (Compare Mode's "A: ..." / "B: ...", and the
# career stages in Real-World Take-Home). Plain bold body text left those
# reading as part of the numbers below them rather than as the label telling
# you what you were looking at.
#
# There is no [theme] block in .streamlit/config.toml, so the app renders in
# whichever theme the visitor has set. This is a mid-tone blue picked to clear
# WCAG's 3:1 large-text contrast threshold against BOTH, measured: 3.68:1 on
# the light theme's white and 5.14:1 on the dark theme's #0E1117 (3.28:1 and
# 4.03:1 against their respective secondary/sidebar backgrounds). A colour
# tuned for one theme disappears on the other, which is the trap here -- the
# dark theme is what you see while developing.
PANEL_HEADING_COLOR = "#3B82F6"


def panel_heading(text: str, level: int = 1) -> None:
    """A scenario/stage heading, rendered into the current container.

    Shared by every result panel so the compare columns, the take-home stages
    and the module sections can't drift into three different weights -- the
    same reason the blocks themselves go through shared render helpers.

    level=1 names a scenario ("A: Computer Science"); level=2 names something
    nested inside one (a career stage). In Compare Mode both appear in the
    same column, one inside the other, so they must not render identically --
    at equal size the stage reads as a sibling of the scenario rather than as
    part of it. Sizes differ rather than colours: two blues close enough to
    sit together would be a distinction nobody can see.

    html.escape because school and major names reach this from the College
    Scorecard and from a free-text school box, and this writes raw HTML."""
    size = "1.15rem" if level == 1 else "1.0rem"
    space = "0.5rem 0 0.7rem" if level == 1 else "0.35rem 0 0.5rem"
    st.markdown(
        f"<div style='color:{PANEL_HEADING_COLOR};font-size:{size};"
        f"font-weight:700;margin:{space};'>{html.escape(text)}</div>",
        unsafe_allow_html=True,
    )


def financing_summary_text(financing: dict) -> str:
    """One-line federal-vs-gap breakdown of the cap-and-gap split (Option A), or
    None when there's no gap (the whole loan fit under the federal Direct cap, or
    Simplified mode where no split was done). Shared by the on-screen Loan
    Information display and the PDF so they can't drift."""
    if not financing or financing.get("gap_principal", 0) <= 0:
        return None
    fee_note = ", incl. fees" if financing.get("fees_included") else ""
    private = financing.get("private_principal", 0) or 0
    if private > 0:
        # Three tranches, because the third one is the finding: money the
        # federal government will not lend at all, at any rate.
        plus = financing.get("plus_principal", 0) or 0
        plus_text = (f"{fmt_money(plus)} Direct PLUS "
                     f"@{fmt_pct(financing['gap_rate'])} + ") if plus > 0 else ""
        # An independent student has no Parent PLUS at all, so the tranche is
        # absent rather than zero -- printing "$0 Direct PLUS" would suggest a
        # loan they could have taken more of.
        gap_text = f"{plus_text}{fmt_money(private)} private @{fmt_pct(financing['gap_rate'])}"
    else:
        gap_text = (f"{fmt_money(financing['gap_principal'])} gap financing "
                    f"(PLUS/private) @{fmt_pct(financing['gap_rate'])}")
    # When professional debt is present the federal tranche spans TWO published
    # rates (undergraduate ~6.5%, graduate/professional 8.07%). Reporting the
    # combined figure "@6.5%" would put the undergraduate rate on $200,000 of
    # medical school in the one line a reader checks the arithmetic against.
    prof_fed = financing.get("professional_federal_principal", 0) or 0
    if prof_fed > 0:
        federal_text = (
            f"{fmt_money(financing['undergrad_federal_principal'])} federal Direct "
            f"@{fmt_pct(financing['federal_rate'])} + {fmt_money(prof_fed)} "
            f"grad/professional Direct @{fmt_pct(financing['professional_rate'])}"
        )
    else:
        federal_text = (f"{fmt_money(financing['federal_principal'])} federal Direct "
                        f"@{fmt_pct(financing['federal_rate'])}")
    return (
        f"Financed as {federal_text} + {gap_text}{fee_note} "
        f"→ {fmt_pct(financing['blended_rate'])} blended"
    )


def render_forgiveness_note(repayment_result: dict, strategy_label: str = None,
                             compact: bool = False) -> None:
    """The forgiveness figure, and the fact that it is taxable income.

    Shared by both result branches. It was two inline copies saying only that
    the balance is forgiven, which read as a clean write-off -- and since
    2026-01-01 a discharged balance is taxed as ordinary income in the year it
    is discharged. On a professional degree that number is large enough for the
    tax alone to be a six-figure event, so stating the forgiveness without the
    tax is the more misleading of the two.

    The app does not model the tax: it lands decades out, at a rate set by
    income and law neither of which is knowable now, and inventing a figure
    would be worse than naming the liability. [Source: TICAS, "Comparing
    Income-Driven Repayment Plans", 2025-09-16.]
    """
    # RAP's interest subsidy, where it actually did something. Worth showing
    # precisely because it is NOT a property of the plan: a borrower whose
    # payment covers the interest has none of it waived, and telling them the
    # plan waives interest would be misleading. This is what it was worth to
    # THEM.
    waived = repayment_result.get("waived_interest", 0) or 0
    if waived > 0:
        st.caption(
            f"RAP waived {fmt_money(waived)} of interest your payments didn't "
            "cover — that is the plan's subsidy, and it is already reflected in "
            "the interest figure above.".replace("$", chr(92) + "$")
        )
    forgiven = repayment_result.get("forgiven_amount", 0) or 0
    if forgiven <= 0:
        return
    # The term is the PLAN's, not a constant: RAP forgives at 30 years and IBR
    # at 20, so naming one number for both would misdate the write-off -- and
    # the tax on it -- by a decade.
    is_rap = strategy_label == RAP_STRATEGY_LABEL
    term = RAP_MAX_TERM_YEARS if is_rap else IDR_MAX_TERM_YEARS
    plan = "RAP" if is_rap else "IDR"
    if compact:
        st.warning(
            f"{fmt_money(forgiven)} forgiven after {term} years — "
            "taxable as income that year, and not modelled here."
            .replace("$", chr(92) + "$")
        )
        return
    st.warning(
        f"Under {plan}, {fmt_money(forgiven)} of principal remains unpaid after "
        f"{term} years and is forgiven. **Since January 1, 2026 a "
        "discharged balance is taxed as ordinary income in the year it is "
        "discharged**, so this is a bill deferred rather than cancelled — the "
        "tax on it is not included in any figure on this page."
        .replace("$", chr(92) + "$")
    )


def render_financing_note(financing: dict) -> None:
    """On-screen version of financing_summary_text: the breakdown caption, plus
    a hard error when any of the loan cannot be borrowed federally at all, plus
    a warning when the non-forgivable share is large."""
    text = financing_summary_text(financing)
    if not text:
        return
    st.caption(text.replace("$", r"\$"))
    private = financing.get("private_principal", 0) or 0
    if private > 0:
        st.error(
            f"**{fmt_money(private)} of this has no federal loan available.** Since "
            f"July 1, 2026 a parent may borrow at most {fmt_money(PARENT_PLUS_ANNUAL_LIMIT)} "
            f"per year and {fmt_money(PARENT_PLUS_AGGREGATE_LIMIT)} in total for one "
            "student in Direct PLUS, on top of the student's own Direct limit. Anything "
            "beyond that has to come from a private lender, family money, or not at all. "
            "Private loans are credit-priced and usually cost more than the rate modelled "
            "here, and they carry no income-driven repayment or forgiveness — so this "
            "estimate is, if anything, optimistic.".replace("$", r"\$")
        )
    if financing.get("gap_share", 0) > 0.4:
        st.warning(
            f"About {fmt_pct(financing['gap_share'] * 100)} of this loan is Direct PLUS or "
            "private. Those carry the higher rate above and are **not** eligible for "
            "income-driven repayment or forgiveness — under an IDR or RAP strategy they are "
            "repaid in full on an ordinary fixed schedule alongside the federal part, and "
            "nothing about them is written off at the end of the term."
        )


def _pdf_financing_flowables(financing: dict, styles: dict) -> list:
    """The financing_summary_text breakdown as a reportlab flowable list (empty
    when there's no gap) -- shared by both PDF builders."""
    text = financing_summary_text(financing)
    if not text:
        return []
    out = [Paragraph(xml_escape(text), styles["caption"])]
    # The on-screen view shows this as an error box; the PDF is often the copy
    # a parent actually reads, so it cannot be the one place the warning is
    # missing.
    private = financing.get("private_principal", 0) or 0
    if private > 0:
        out.append(Paragraph(xml_escape(
            f"{fmt_money(private)} of this has no federal loan available. Since July 1, "
            f"2026 a parent may borrow at most {fmt_money(PARENT_PLUS_ANNUAL_LIMIT)} per "
            f"year and {fmt_money(PARENT_PLUS_AGGREGATE_LIMIT)} in total for one student "
            "in Direct PLUS, on top of the student's own Direct limit. Anything beyond "
            "that must come from a private lender, family money, or not at all. Private "
            "loans are credit-priced and usually cost more than the rate modelled here, "
            "and carry no income-driven repayment or forgiveness."), styles["caption"]))
    return out


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


def income_for_year(major_name: str, year_index: int,
                     annual_income: float = None, income_growth: float = 0.03) -> float:
    """The borrower's income in a given year, from a major OR from a figure
    they gave us.

    The income-driven simulators originally derived income from a major,
    because every borrower they modelled was hypothetical. Someone already in
    repayment has no major -- they have a salary. Passing it explicitly keeps
    those simulators usable for both without a second copy of the amortisation.

    Two scalars rather than a callable, deliberately: find_breakeven_loan is
    @st.cache_data, a lambda is unhashable, and a function crossing that
    boundary would either raise or be keyed by object identity and cache the
    wrong answer. Same reasoning returning_student_curve documents.
    """
    if annual_income is not None:
        return float(annual_income) * ((1 + income_growth) ** max(year_index, 0))
    return get_annual_salary_for_year(major_name, year_index)


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


def get_effective_principal(major_name: str, loan_amount: float,
                             professional_debt: float = None) -> float:
    """The true total debt behind a major's salary, including any
    professional-school debt beyond the undergrad loan slider (e.g.
    Medicine's median medical school debt). Used as the actual loan
    principal AND the ROI% denominator -- see calculate_roi."""
    # professional_debt=None means "use the national figure" -- the pre-picker
    # behaviour, and what analyze_model.py gets by not passing one.
    if professional_debt is None:
        professional_debt = MAJOR_DATA[major_name].get("additional_training_debt", 0)
    return loan_amount + professional_debt


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


def apply_starting_salary_override(major_name: str, entered: float) -> None:
    """Re-anchor a major's whole salary curve to a figure the visitor entered.

    The BLS number is what EVERYONE in an occupation earns -- it is not what a
    50-year-old entering it earns in year one. A career-changer typically starts
    below it and may never reach it, and the app has no data for that, so it
    lets the visitor say rather than guessing on their behalf.

    Scales starting_salary AND median_salary by the same ratio, exactly as the
    metro wage index and the prestige multiplier already do. Overriding only
    the start would silently change the implied growth rate, because
    get_major_growth_rate derives it from median/starting -- entering a lower
    figure would make the model grow FASTER to the same median, which is the
    opposite of what a late entrant should expect. Scaling both preserves the
    curve's shape and moves only its level.

    Builds a NEW dict rather than mutating. load_bls_careers is cached and its
    inner dicts are shared references, so an in-place edit here would rewrite
    one visitor's salary for everyone until the cache cleared.
    """
    base = MAJOR_DATA.get(major_name)
    if not base or not entered or entered <= 0:
        return
    current = base.get("starting_salary") or 0
    if current <= 0:
        return
    ratio = entered / current
    MAJOR_DATA[major_name] = {
        **base,
        "starting_salary": entered,
        "median_salary": base["median_salary"] * ratio,
        "salary_overridden": True,
    }


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


def escape_money_markdown(label: str) -> str:
    """Escape dollar signs so Streamlit renders them as text, not LaTeX.

    A label like "$10,000-$25,000" carries a MATCHED PAIR of "$", which
    Streamlit's markdown treats as a maths delimiter -- the band renders
    italicised, without its dollar signs, as though it were an equation. Single
    "$" elsewhere in the app is unaffected because it is unpaired, which is why
    this has not bitten before.

    Used via format_func so the stored and logged value stays clean: the
    backslashes are a display concern and must not reach Supabase.
    """
    return label.replace("$", r"\$")


def presurvey_code(options: dict, label: str) -> str:
    """The stable code for a display label, or "skip" when unanswered.

    Everything stored or logged goes through here. The display strings are
    free to be reworded -- for clarity, or because a band changes -- without
    silently reindexing an ordinal analysis that subtracts two values."""
    return options.get(label, "skip") if label else "skip"


def log_presurvey(answered: bool) -> None:
    """Record the pre-question outcome to usage_logs.

    usage_logs takes any new action string with no migration -- the same reason
    log_horizon_change writes here rather than adding a column. That matters
    more than usual for this one: writing the pre-answer ONLY into the survey
    row would make it invisible for every visitor who answers it and then
    leaves without submitting the exit survey, which is most of them. The
    drop-off between the two is itself a finding about engagement, and it can
    only be measured if the pre exists independently.

    Answers are also held in session_state and copied onto the survey row when
    one is submitted, so a completed pair lives on a single row for analysis.
    """
    if not answered:
        log_usage_event("presurvey_skipped")
        return
    role = st.session_state.get("presurvey_role") or "unset"
    schools = presurvey_code(PRESURVEY_SCHOOLS_OPTIONS,
                             st.session_state.get("presurvey_schools"))
    # "n_a" rather than "skip": for a counsellor the question was never put,
    # which is a different fact from a visitor who was asked and declined.
    borrowing = ("n_a" if role in ROLES_WITHOUT_BORROWING
                 else presurvey_code(PRESURVEY_BORROWING_OPTIONS,
                                      st.session_state.get("presurvey_borrowing")))
    # seq > 1 means the visitor had already switched major or school before
    # answering, so their "pre" is really post-interaction. One token, and it
    # is the only way to tell those rows apart later.
    seq = st.session_state.get("scenario_event_seq", 0)
    log_usage_event(
        f"presurvey_answered:role={presurvey_code(_ROLE_CODES, role)}"
        f":considering={schools}:borrow={borrowing}"
        f":seq={seq}:arm={get_experiment_arm()}:v={PRESURVEY_INSTRUMENT_VERSION}")


def research_link() -> bool:
    """Did this visitor arrive through a recruitment link (?research=1)?

    LATCHED into session_state on first read, exactly as test_mode and
    get_traffic_source are, and for the same reason: "Share Scenario" calls
    st.query_params.from_dict, which REPLACES the whole query string. A live
    re-read would switch the instrument off mid-session for the one visitor
    engaged enough to share -- precisely the person whose answers are worth
    having.

    NOTE FOR ANYONE READING THE HISTORY: ?research=1 meant the OPPOSITE of this
    until 2026-08-01. It was an ethics gate then -- the instrument was hidden
    from everyone because no human-subjects determination existed. That
    determination now exists, so showing the instrument to all visitors is
    PERMITTED; restricting the pre-survey to recruitment links is a decision
    about FRICTION, not about ethics. Same spelling, different meaning.
    """
    if "research_mode" not in st.session_state:
        st.session_state.research_mode = get_shared_default("research", "0") == "1"
    return bool(st.session_state.research_mode)


def presurvey_enabled() -> bool:
    """Whether to render the before-you-look questions for this visitor."""
    return PRESURVEY_ENABLED or research_link()


def postsurvey_enabled() -> bool:
    """Whether to render the exit survey for this visitor."""
    return POSTSURVEY_ENABLED or research_link()


def research_participation_allowed() -> bool:
    """Whether this visitor may be offered a research instrument.

    False only for a self-identified student who has not attested to meeting
    RESEARCH_MIN_AGE. Everyone else -- including a visitor who never
    answered the role question at all -- is allowed, because an unanswered
    role is not a claim to be a minor and refusing on that basis would
    suppress most of the sample for no gain.

    The CALCULATOR is never gated on this. It is a public information tool;
    using it is not participating in research, and withholding it from a
    16-year-old would defeat the point of having built it."""
    if st.session_state.get("presurvey_role") not in ROLES_REQUIRING_AGE_ATTESTATION:
        return True
    return bool(st.session_state.get("presurvey_age_ok"))


def build_instrument_context(post_schools: str, post_borrowing: str,
                              respondent_role: str) -> dict:
    """The seven paired-measurement columns for one survey row.

    Pre values come from session_state, where the pre block left them; post
    values are passed in from the form. Both sides go through presurvey_code,
    so a reworded option label cannot change what a stored value means -- the
    analysis subtracts these, and a broken label map would yield a wrong
    number rather than an obvious blank.

    Three states are kept distinct and must stay that way downstream:
      answered    -> a code
      skip        -> asked, declined
      n_a         -> never asked (the role does not take the question)
    and separately, all-NULL pre columns with instrument_version set means the
    pre block was never shown at all.
    """
    pre_answered = bool(st.session_state.get("presurvey_answered"))
    borrowing_applies = respondent_role not in ROLES_WITHOUT_BORROWING
    return {
        "pre_schools_considered": presurvey_code(
            PRESURVEY_SCHOOLS_OPTIONS, st.session_state.get("presurvey_schools")
        ) if pre_answered else None,
        "pre_borrow_willingness": (
            (presurvey_code(PRESURVEY_BORROWING_OPTIONS,
                            st.session_state.get("presurvey_borrowing"))
             if borrowing_applies else "n_a") if pre_answered else None),
        "post_schools_considered": presurvey_code(
            PRESURVEY_SCHOOLS_OPTIONS, post_schools),
        "post_borrow_willingness": (
            presurvey_code(PRESURVEY_BORROWING_OPTIONS, post_borrowing)
            if borrowing_applies else "n_a"),
        "pre_skipped": bool(st.session_state.get("presurvey_skipped")),
        # Only meaningful for the roles asked to attest. True for everyone
        # else would imply we checked, which we did not.
        "age_attested": (bool(st.session_state.get("presurvey_age_ok"))
                          if respondent_role in ROLES_REQUIRING_AGE_ATTESTATION
                          else None),
        "instrument_version": PRESURVEY_INSTRUMENT_VERSION,
    }


def render_presurvey() -> None:
    """The two before-you-look questions, above the results.

    Deliberately NOT a gate. This app's premise is that real numbers are on
    screen before you touch anything, so a prompt that withholds them to
    collect data would trade the thing the tool is for against the thing the
    paper wants. It renders, it can be skipped in one click, and the results
    below render either way.

    Rendered at module level, outside both section 5c branches -- the same
    reason the exit survey is safe. get_experiment_arm() assigns ~half of
    visitors to Compare Mode, so anything rendered inside one branch and not
    the other becomes a difference between the arms that H2 does not claim.

    Skipped and unanswered are tracked separately. A missing answer must mean
    "we don't know", never "they had nothing to say" -- the same distinction
    major_explicitly_selected exists to preserve for the major.
    """
    # No test_mode gate here on purpose: log_usage_event already returns early
    # on it, so a ?test=1 session sees the real prompt and writes nothing --
    # matching save_survey_response, which returns True without inserting so
    # the thank-you UX still appears. Suppressing the render instead would make
    # the one feature that must be verified in a browser unverifiable there.
    if not presurvey_enabled():
        return
    if st.session_state.get("presurvey_answered") or st.session_state.get("presurvey_skipped"):
        return

    # One row per session, not per rerun -- this function runs on every pass.
    if not st.session_state.get("presurvey_shown_logged"):
        st.session_state["presurvey_shown_logged"] = True
        log_usage_event("presurvey_shown")

    # A bordered container, not an expander. An expander offers a collapse
    # control, and a collapsed prompt is one the visitor never returns to --
    # it reads as chrome to dismiss rather than as part of the page. This is
    # deliberately the ONLY change to how hard the prompt is to ignore: it is
    # not a gate, and must not become one. The consent text promises "the
    # calculator works exactly the same whether you answer or not", and Skip
    # stays a single click for exactly that reason.
    with st.container(border=True):
        st.markdown("#### 📝 Two quick questions before you start (optional)")
        st.caption(
            "Answering these before you explore lets us measure whether tools like "
            "this actually change anything — we ask the same two at the end. Skip "
            "if you'd rather just get to the numbers."
        )
        # index=None so "unanswered" is a real state. A radio defaulting to its
        # first option would record "0 colleges" for anyone who ignored it,
        # which is the same answer-vs-absence failure the exit survey's
        # role/graduation-year dropdowns still have.
        # Role first: it decides whether the borrowing question is asked at
        # all. Plain radios rather than a form precisely so this reacts --
        # inside st.form nothing reruns until submit, and the borrowing
        # question could not appear or disappear in response.
        st.radio("I am a...", PRESURVEY_ROLE_OPTIONS, index=None,
                  horizontal=True, key="presurvey_role")

        role = st.session_state.get("presurvey_role")

        # Age gate, students only. Rendered before the rest so an ineligible
        # visitor is never asked a research question at all -- collecting the
        # answers and discarding them afterwards would still be collecting
        # them.
        if role in ROLES_REQUIRING_AGE_ATTESTATION:
            st.checkbox(f"I am {RESEARCH_MIN_AGE} or older", key="presurvey_age_ok")
            if not st.session_state.get("presurvey_age_ok"):
                st.info(
                    f"These questions are for people {RESEARCH_MIN_AGE} and over. "
                    "**Everything else on this page works exactly the same** — "
                    "scroll on down, the calculator is yours to use."
                )
                if st.button("Got it"):
                    st.session_state["presurvey_skipped"] = True
                    log_usage_event("presurvey_ineligible_minor")
                    st.rerun()
                return

        st.radio(PRESURVEY_SCHOOLS_QUESTION, list(PRESURVEY_SCHOOLS_OPTIONS),
                  index=None, horizontal=True, key="presurvey_schools")

        borrowing_applies = role not in ROLES_WITHOUT_BORROWING
        if borrowing_applies:
            st.radio(PRESURVEY_BORROWING_QUESTION, list(PRESURVEY_BORROWING_OPTIONS),
                      index=None, key="presurvey_borrowing",
                      format_func=escape_money_markdown)
        elif st.session_state.get("presurvey_borrowing"):
            # Cleared rather than left dangling: a visitor who answered the
            # borrowing question and THEN picked Counselor would otherwise
            # have a stale answer silently ride along to Supabase.
            st.session_state["presurvey_borrowing"] = None

        save_col, skip_col = st.columns([1, 3])
        answered_required = (
            bool(role)
            and bool(st.session_state.get("presurvey_schools"))
            and (not borrowing_applies or bool(st.session_state.get("presurvey_borrowing")))
        )
        # Primary on Save, default on Skip. Both stay one click -- the weighting
        # says which is more useful to the research, not which is permitted.
        if save_col.button("Save answers", disabled=not answered_required,
                            type="primary", use_container_width=True):
            st.session_state["presurvey_answered"] = True
            log_presurvey(answered=True)
            st.rerun()
        if skip_col.button("Skip"):
            st.session_state["presurvey_skipped"] = True
            log_presurvey(answered=False)
            st.rerun()


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

    LATCHED into session_state on first read, not re-read from the URL each
    time, for the same reason test_mode is: "Share Scenario"
    calls st.query_params.from_dict, which REPLACES the whole query string. A
    live read meant every row written after a share lost its attribution --
    silently, and looking exactly like organic traffic, which is the one thing
    this field exists to distinguish. The share is also a high-intent action,
    so the rows most worth attributing were the ones losing it.

    Latching means the tag describes the visit as it ARRIVED, which is what a
    recruitment channel is. Editing ?src= mid-session without a reload no
    longer changes it -- correct: one visit came from one place.
    """
    if "traffic_source" not in st.session_state:
        # Seeded here rather than only in section 3 so the value cannot depend
        # on which caller runs first -- log_usage_event("pageview") fires early
        # and must latch the same tag the later writers see.
        st.session_state["traffic_source"] = get_shared_default("src", None)
    return st.session_state["traffic_source"]


# Every action that represents one visit landing. Split so calculator and
# repayment-page traffic can be told apart -- but anything asking "how many
# people came at all" must use BOTH, or it silently undercounts the moment the
# repayment link is shared.
PAGEVIEW_ACTIONS = ("pageview", "pageview_repayment")


def repayment_page_requested() -> bool:
    """Whether this visit asked for the standalone repayment tool.

    Reads the query param directly so it can be called before the session latch
    exists -- the pageview logger runs long before section 5. The latch below
    is seeded FROM this, so the two cannot disagree about which page a visit was.
    """
    return get_shared_default("tool", "") == "repayment"


def mark_interaction(field: str):
    """Log each control a visitor touches, ONCE PER FIELD per session.

    ~92% of sessions are pageview-only -- they never move a control -- so the
    question worth answering is what the engaged minority actually do, and
    nothing in the data answered it: scenario_events records where a session
    LANDED, never which fields moved it there, and it only fires on a
    major/school change, so city and financing edits wrote nothing at all.

    Per FIELD, not per session. The earlier version logged only the very first
    touch, which made one click and twenty indistinguishable. Per-field keeps
    that first touch derivable -- it is the earliest interaction: row in the
    session -- while also showing breadth.

    Per field is also what keeps a slider safe: dragging "Total Loan Amount"
    fires on_change repeatedly, but the guard is keyed on the field, so it
    produces exactly one row no matter how far it is dragged. That was the
    original reason for only-first, and it is preserved without the cost.

    Relies on the property mark_major_explicitly_selected documents: Streamlit
    fires on_change only on a real interaction, never on the initial render and
    never on reruns other widgets trigger. So an untouched control never
    appears, and "absent" means "not touched" rather than "not instrumented".

    Bounded by construction: at most one row per control, realistically 3-8 for
    an engaged session.
    """
    seen = st.session_state.setdefault("_interactions_logged", set())
    if field in seen:
        return
    seen.add(field)
    log_usage_event(f"interaction:{field}")


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


def report_write_failure(what: str, error: Exception) -> None:
    """Print why a Supabase write failed, without changing what the visitor sees.

    Every writer in this file catches broadly and returns False, so a page load
    keeps working when the database is unreachable -- that part is deliberate.
    What it also did was discard the reason, which makes the two failures that
    matter indistinguishable from each other and from a healthy no-op: a
    missing column (PGRST204, the whole row rejected) looks exactly like a
    network timeout, which looks exactly like nobody having used the feature.

    Goes to the server console, which is the Streamlit Cloud log -- never to
    the page. A visitor should not be shown a PostgREST error, and the app's
    own error text stays as it is.
    """
    print(f"[supabase] {what} failed: {type(error).__name__}: {error}", file=sys.stderr)


def json_safe_row(row: dict) -> dict:
    """Make one insert payload safe to serialise, replacing values JSON cannot
    represent with None. Every Supabase writer runs its row through this.

    Two value kinds reach these rows from pandas and break the insert, both of
    them silently and both of them taking the WHOLE row with them:

    - **NaN / Infinity.** Python's json.dumps emits bare `NaN` and `Infinity`,
      which are not valid JSON. PostgREST answers PGRST102 "Empty or invalid
      json" and rejects the entire row -- not just that field. A missing wage
      or an unreported Cost of Attendance is an ordinary gap in the federal
      data, so this is reachable from normal use, and it presents to the
      visitor as "Something went wrong saving your response".
    - **numpy scalars.** np.float64 happens to survive because it subclasses
      float; np.int64 does not subclass int and raises TypeError before the
      request is even sent. A UNITID or any integer column read out of a
      DataFrame row is an np.int64.

    None rather than 0 is deliberate: the value is unknown, and every one of
    these columns is nullable. Writing 0 would fabricate a measurement, which
    is worse than recording that there wasn't one -- the same reasoning that
    keeps traffic_source NULL instead of "direct".
    """
    clean = {}
    for key, value in row.items():
        # numpy scalars expose .item(); this covers int64/float64/bool_ without
        # importing numpy, which app.py does not otherwise need.
        if hasattr(value, "item") and not isinstance(value, (str, bytes)):
            try:
                value = value.item()
            except (ValueError, AttributeError):
                pass
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            value = None
        elif value is not None and value is not True and value is not False:
            # pandas NA/NaT are not floats and fail isnan; pd.isna answers for
            # them. Guarded to scalars -- pd.isna on a list returns an array.
            try:
                if pd.isna(value):
                    value = None
            except (TypeError, ValueError):
                pass
        clean[key] = value
    return clean


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
                [json_safe_row({
                    "timestamp": now_local().isoformat(), "session_id": get_session_id(),
                    "traffic_source": get_traffic_source(), "action": action})],
                count="None",
            ),
            ttl=0,
        )
    except Exception:
        pass


def save_survey_response(respondent_role: str, hs_graduation_year: str,
                          perception_change: str, feedback_text: str, context: dict,
                          instrument: dict = None) -> bool:
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
            # The paired pre/post measurement. Survey-only, so it is a named
            # argument like the four above rather than a member of context --
            # context is spread into four tables, and these columns exist on
            # one. Placed before **context so a context key added later cannot
            # silently overwrite a measurement.
            **(instrument or {}),
            **context,
        }
        execute_query(
            conn.table("survey_responses").insert([json_safe_row(row)], count="None"),
            ttl=0,
        )
        return True
    except Exception as error:
        report_write_failure("survey_responses insert", error)
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
        # The three fields below read module-level globals the same way
        # dataset_mode does (assigned in section 4 before the results run), so
        # no caller signature changes. They used to live only in share-link
        # params (build_share_params), which meant the admin dashboard could
        # not break usage down by any of them. career_data_source is only
        # meaningful in Career mode (the radio is disabled in Major mode);
        # read it together with dataset_mode when aggregating.
        "career_data_source": career_data_source,                      # derived state name, or National
        # Where the numbers came from. Without these three, the outcome
        # columns in this row cannot be read: every earnings_premium and
        # roi_pct is cost-of-living adjusted to `city` and metro-scaled to that
        # city's wages, so a $292,603 premium in San Francisco and a $292,603
        # premium at National Average are different claims and nothing else
        # here distinguishes them. Module globals, same as dataset_mode.
        "city": city,
        # Which Cost of Attendance fed the loan. $34,200/year apart at Berkeley
        # alone, so scenario_a_loan_amount is ambiguous without it.
        "scenario_a_in_state": in_state_a,
        # national / state / metro -- which geography published the wage. The
        # app already tells the visitor via render_wage_geography_note, so
        # before this the visitor knew something the dataset did not.
        #
        # Resolved to a literal "national" rather than left as the absent key
        # it actually is. build_major_data stamps the level only when a state
        # or metro overlay REPLACES the national spine, so an un-overlaid
        # occupation carries no key -- in San Francisco that is 39 of 836. A
        # raw .get() would write None for those, which is the same value Major
        # mode writes for a completely different reason (NY Fed entries have no
        # OEWS geography at all), and the two are not the same fact. Making the
        # Career case explicit means NULL has exactly one meaning per mode:
        # "not applicable", never "national".
        # Returning-student mode. student_mode is the one that changes what
        # every OTHER column in this row means: a "Going back to school" row's
        # earnings_premium is measured against baseline_salary_now, not against
        # a high school graduate, and the two must never be pooled without
        # conditioning on it.
        "student_mode": student_mode,
        "current_age": (int(st.session_state["current_age"])
                         if is_returning and st.session_state.get("current_age") else None),
        # NULL in returning mode until BOTH are entered -- the app deliberately
        # keeps the old baseline until then, so NULL here means "still measured
        # against a high school graduate", not "missing data".
        "baseline_salary_now": (float(st.session_state["current_salary"])
                                 if is_returning and returning_baseline_ready() else None),
        "baseline_salary_in_10y": (float(st.session_state["salary_no_degree_10y"])
                                    if is_returning and returning_baseline_ready() else None),
        # Excluded from scenario_a_loan_amount and from the ROI by design; it
        # moves payoff_age and the monthly payment only.
        "existing_debt": (float(st.session_state.get("existing_debt", 0) or 0)
                           if is_returning else None),
        "payoff_age": payoff_age_for(
            scenario_a, st.session_state.get("current_age") if is_returning else None,
            program_years_for_major(major)),
        "wage_geography_level": (
            (MAJOR_DATA.get(major, {}).get("wage_geography_level") or "national")
            if dataset_mode == DATASET_MODE_CAREER else None),
        # The two switches that change what every ROI figure in this row MEANS.
        # Neither was logged before, which was survivable while both defaulted
        # off; it stops being survivable the moment one defaults on, because
        # rows would then differ from earlier ones with nothing recording why.
        # Read both when aggregating -- see migrations.sql.
        # Constant True since the switch was removed -- the app has no flat-
        # baseline mode any more. Still written, because rows from before the
        # age curve carry NULL/false and the column is what tells the two eras
        # apart; dropping it would make them indistinguishable.
        "hs_baseline_age_aware": True,
        "count_foregone_earnings": bool(st.session_state.get("count_foregone_earnings", False)),
        "loan_mode": st.session_state.get("loan_mode", "Simplified"),  # Simplified / Detailed
        "cc_mode_a": cc_mode_a,                                         # none / fulltime / parttime
        # The professional school, and the debt figure it produced. Both are
        # needed: the name alone cannot be re-resolved later (the dataset is
        # regenerated from new Scorecard releases), and the figure alone cannot
        # say whether it was a school median or the national fallback.
        # NULL means this path attends no professional school at all.
        # Credential and the graduate split. scenario_a_program_years already
        # existed, but it is uninterpretable without these: 6 means "bachelor's
        # plus a master's", not "a six-year bachelor's", and only credential
        # says which.
        #
        # In Major mode Scenario B shares A's credential -- there is one radio,
        # on the reasoning that a visitor comparing two majors is one person
        # choosing one level. In Career mode each side derives its own from BLS.
        "credential_a": _typical_education_a or None,
        "credential_b": (resolve_typical_education(
            "major_b", DEFAULT_SELECTION_B[DATASET_MODE_CAREER], share_param="major_b")
            or st.session_state.get("credential_a")) if compare_mode else None,
        "graduate_years_a": graduate_years_a or None,
        "graduate_years_b": (graduate_years_b or None) if compare_mode else None,
        # Which school's median was used as the loan, and the figure. NULL when
        # the visitor entered their own cost instead -- which is the common
        # case, since only ~20% of school x field cells publish one.
        "grad_school_a": (st.session_state.get("grad_school_a")
                          if graduate_debt_a else None),
        "graduate_debt_a": graduate_debt_a or None,
        "prof_school_a": st.session_state.get("prof_school_a")
                          if professional_program_for(major) else None,
        "prof_school_b": st.session_state.get("prof_school_b")
                          if compare_mode and professional_program_for(major_b) else None,
        "professional_debt_a": resolve_professional_debt(
            major, st.session_state.get("prof_school_a")) or None,
        "professional_debt_b": (resolve_professional_debt(
            major_b, st.session_state.get("prof_school_b")) or None) if compare_mode else None,
        # The horizon every roi_pct/earnings_premium below was computed over.
        # Without it those columns aren't comparable across rows: a 30-year
        # ROI and a 10-year ROI are different quantities wearing the same
        # column name, and pooling them would be meaningless.
        "roi_horizon_years": roi_horizon_years,
        "scenario_a_school_name": school_name_a or None,
        "scenario_a_major": major,
        "scenario_a_loan_amount": loan_amount,
        # How that loan figure was arrived at. Without this the number alone
        # can't say whether it was reported as-is, scaled down for a shorter
        # program, built from cost, or zeroed because the career needs no
        # degree -- and from this deploy the same school+career can yield a
        # different figure than it did before. reported_debt keeps the raw
        # Scorecard value so a scaled row stays auditable; program_years lets
        # rows be split by era. See migrations.sql.
        "scenario_a_loan_basis": loan_basis_a,
        "scenario_a_reported_debt": reported_debt_a,
        "scenario_a_program_years": program_years_a,
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
            # cc_mode_b is a module global only in the compare branch (assigned
            # in section 5c), so it is referenced only here, inside compare_mode.
            "cc_mode_b": cc_mode_b,   # none / fulltime / parttime
            # Inside compare_mode for the same reason cc_mode_b is: in_state_b
            # is assigned in the compare branch of the sidebar, so referencing
            # it unconditionally would raise NameError with compare off.
            "scenario_b_in_state": in_state_b,
            "scenario_b_school_name": school_name_b or None,
            "scenario_b_major": major_b,
            "scenario_b_loan_amount": loan_amount_b,
            "scenario_b_loan_basis": loan_basis_b,
            "scenario_b_reported_debt": reported_debt_b,
            "scenario_b_program_years": program_years_b,
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


def build_share_params(career_data_source, major, city, school_name_a, in_state_a,
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
        # No career_source: the wage basis follows the city, so `city` already
        # carries it. Emitting a derived value would recreate a field nothing
        # reads back -- and older links that still carry ?career_source= are
        # simply ignored, which is the right outcome now that a state is not
        # something a visitor can choose independently of where they live.
        "major": major,
        "city": city,
        "school": school_name_a,
        "in_state": "1" if in_state_a else "0",
        "coa": str(coa_per_year_a),
        "pc": str(personal_contribution_per_year_a),
        "grants": str(grants_per_year_a),
        "rate": str(interest_rate),
        "strategy": repayment_strategy,
        "compare": "1" if compare_mode else "0",
        "start_year": str(start_year_a),
        "horizon": str(roi_horizon_years),
        # The Loan estimate mode (Simplified/Detailed) is global, so it rides in
        # the base params rather than the compare-only block. Read from the
        # persistent preference key, not the possibly-forced effective value, so
        # a shared "Simplified" link stays Simplified when it lands on a school
        # that does have reported debt.
        "loan_mode": st.session_state.get("loan_mode", "Simplified"),
        # Cap-and-gap financing inputs (Detailed mode). gap_rate is per-scenario;
        # dependency is global. Read from session_state, like loan_mode.
        "gap_rate": str(st.session_state.get("gap_rate_a", DEFAULT_GAP_RATE)),
        "dependency": st.session_state.get("loan_dependency", "dependent"),
        "cc_mode_a": cc_mode_a,
        "cc_state_a": cc_state_a,
        "cc_coa_a": str(cc_coa_per_year_a),
    }
    # Returning-student mode. Read from session_state rather than added to this
    # function's signature, the same way loan_mode/dependency are -- they have
    # no Scenario B counterpart and no caller holds them as locals.
    #
    # These are not optional the way a slider position is: the mode decides what
    # the whole page is COMPARING AGAINST (a debt-free 18-year-old, or the
    # visitor's own current salary). A link that drops them doesn't recreate a
    # slightly different scenario -- it silently answers a different question
    # with the same school and major on screen. Short tokens, not the display
    # labels, because the enrollment labels carry em dashes.
    # Foregone earnings and the three Advanced Analysis toggles. Same class of
    # field as the mode above and same reason to carry them: "count foregone
    # earnings" decides whether every path is compared from age 18 or from
    # graduation, and the Advanced toggles change the salaries and costs on
    # screen. A link that drops them looks like the sender's scenario and is
    # not. Found by check_share_coverage.py, not by anyone noticing.
    params["foregone"] = "1" if st.session_state.get("count_foregone_earnings") else "0"
    params["deps"] = str(st.session_state.get("rap_dependents", 0))
    params["legacy"] = "1" if st.session_state.get("enable_legacy_plans") else "0"
    # Professional school. Only meaningful for the paths that attend one, so
    # emitted only when set -- a link for Software Developers carrying an empty
    # medical-school param would be noise.
    # Credential and graduate school. The credential decides the program
    # length and the loan limits, so a link that drops it answers a different
    # question -- emitted whenever it is not the default.
    if st.session_state.get("credential_a", CREDENTIAL_BACHELORS) != CREDENTIAL_BACHELORS:
        params["cred"] = st.session_state["credential_a"]
    if st.session_state.get("grad_school_a", GRADUATE_SCHOOL_NATIONAL) != GRADUATE_SCHOOL_NATIONAL:
        params["grad_school"] = st.session_state["grad_school_a"]
    if st.session_state.get("prof_school_a", PROFESSIONAL_SCHOOL_NATIONAL) != PROFESSIONAL_SCHOOL_NATIONAL:
        params["prof_school"] = st.session_state["prof_school_a"]
    if compare_mode and st.session_state.get(
            "prof_school_b", PROFESSIONAL_SCHOOL_NATIONAL) != PROFESSIONAL_SCHOOL_NATIONAL:
        params["prof_school_b"] = st.session_state["prof_school_b"]
    params["prestige"] = "1" if st.session_state.get("enable_prestige_mode") else "0"
    params["ai"] = "1" if st.session_state.get("enable_ai_mode") else "0"
    params["future"] = "1" if st.session_state.get("enable_future_proofing") else "0"
    # In prestige mode the tier REPLACES the school, so ?school= holds a tier
    # label that no school lookup can resolve -- the tier params are what make
    # such a link reconstructable.
    if st.session_state.get("enable_prestige_mode"):
        if st.session_state.get("prestige_tier_a") is not None:
            params["prestige_tier"] = st.session_state["prestige_tier_a"]
        if compare_mode and st.session_state.get("prestige_tier_b") is not None:
            params["prestige_tier_b"] = st.session_state["prestige_tier_b"]

    _shared_returning = st.session_state.get("student_mode_radio") == STUDENT_MODE_RETURNING
    params["smode"] = "returning" if _shared_returning else "first"
    if _shared_returning:
        params.update({
            "age": str(st.session_state.get("current_age", 30)),
            "cur_sal": str(st.session_state.get("current_salary", 0)),
            "sal10": str(st.session_state.get("salary_no_degree_10y", 0)),
            "debt": str(st.session_state.get("existing_debt", 0)),
            "debt_rate": str(st.session_state.get("existing_debt_rate", DEFAULT_FEDERAL_RATE)),
            "enroll": ("working"
                       if st.session_state.get("returning_enrollment") == RETURNING_KEEP_WORKING
                       else "fulltime"),
        })
        # Only present once a major with a BLS figure has been picked.
        if "starting_salary_override" in st.session_state:
            params["sso"] = str(st.session_state["starting_salary_override"])
    if compare_mode:
        params.update({
            "major_b": major_b,
            "school_b": school_name_b,
            "in_state_b": "1" if in_state_b else "0",
            "coa_b": str(coa_per_year_b),
            "pc_b": str(personal_contribution_per_year_b),
            "grants_b": str(grants_per_year_b),
            "rate_b": str(interest_rate_b),
            "gap_rate_b": str(st.session_state.get("gap_rate_b", DEFAULT_GAP_RATE)),
            "strategy_b": repayment_strategy_b,
            "start_year_b": str(start_year_b),
            "cc_mode_b": cc_mode_b,
            "cc_state_b": cc_state_b,
            "cc_coa_b": str(cc_coa_per_year_b),
        })
    return params


def session_query_params() -> dict:
    """Params describing the SESSION rather than the scenario, merged back in
    when "Share Scenario" replaces the whole query string.

    Only flags that fail DANGEROUSLY when lost belong here, which today is
    test alone. Losing it does not merely turn a feature off: the next reload
    of that URL is a live session writing to the production Supabase, and the
    rows it writes are indistinguishable from real visitors' -- the exact
    contamination already on record in migrations.sql. Carrying it makes the
    button safe to press while testing.

    Deliberately NOT carried:

    - **src.** It is now latched in session_state, so the sharer keeps their
      own attribution without the URL. Putting it in the shared link would
      stamp the RECIPIENT with the sharer's recruitment channel -- someone who
      arrived from a forwarded link did not come from that counsellor's class,
      and recording that they did is fabricated attribution, worse than the
      NULL it replaces.
    - **admin.** Fails safe when dropped -- the dashboard stays hidden -- and
      should not ride along to a stranger.
    - **research.** Deliberately NOT carried, even though it now does something
      again. The sharer keeps it via research_link()'s session_state latch, so
      dropping it from the URL costs them nothing; the RECIPIENT of a shared
      link was never recruited, and handing them the pre-survey because someone
      else was would put non-recruited answers into the paired sample.

      Its meaning inverted on 2026-08-01: it used to HIDE the instrument (an
      ethics gate, before the human-subjects determination existed) and now
      SHOWS it (a friction decision, after). Either way it stays out of a
      shared link -- for opposite reasons, which is worth knowing before
      "simplifying" this.
    """
    return {"test": "1"} if st.session_state.get("test_mode") else {}


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
            conn.table("pdf_downloads").insert([json_safe_row(row)], count="None"),
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
            conn.table("scenario_shares").insert([json_safe_row(row)], count="None"),
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

    The signature is deliberately only the major/school selections (A and B,
    undergraduate and professional), not the whole scenario: those are the
    choices "switching" refers to, and
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
    # city joins the signature because it changes EVERY outcome number in the
    # row -- the cost-of-living index and the metro wage basis both -- so a
    # session moving San Francisco -> National Average produced an entirely
    # different set of figures and recorded none of it. It is safe to include
    # for the reason the financing fields are not: it is a discrete selectbox,
    # so it cannot fire per-tick the way a slider drag would. That distinction,
    # not "how important is the field", is what governs what belongs here.
    # The professional school joins for the same reason city did, and by the
    # same test: it is a discrete selectbox, so it cannot fire per-tick. It
    # earns its place more than most -- it changes the largest single number in
    # a medical or dental scenario (medicine's median debt ranges $47,503 to
    # $330,479 across schools), and comparing two of them is precisely the
    # switch this table exists to catch. Without it, a visitor moving from a
    # private medical school to a state one changed their whole loan and left
    # no row at all: same major, same undergraduate school, so the signature
    # never moved.
    signature = (
        context.get("scenario_a_major"), context.get("scenario_a_school_name"),
        context.get("scenario_b_major"), context.get("scenario_b_school_name"),
        context.get("prof_school_a"), context.get("prof_school_b"),
        context.get("city"),
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
            conn.table("scenario_events").insert([json_safe_row(row)], count="None"),
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


def apply_shared_flag(param_name: str, state_key: str) -> None:
    """Apply a shared link's boolean flag, on a fresh visit AND on a same-tab
    URL change.

    `st.session_state.setdefault(key, get_shared_default(...))` -- the pattern
    every other shared field uses -- silently fails for these. Streamlit reads
    query params client-side, so pasting a new URL into an open tab reruns the
    script without creating a new session: the key already exists, setdefault
    is a no-op, and the link's value never lands. Verified in production, where
    ?legacy=1 gave all four repayment plans in a fresh tab and only two in a
    reused one.

    Re-applying on every rerun would be worse -- the checkbox would spring back
    the instant the visitor unticked it, since the URL still says 1. So this
    fires only when the param's value CHANGES from what was last applied,
    which is exactly once per navigation. After that the widget owns the key.

    Only for booleans that also have a checkbox. A field with no widget cannot
    be fought over and does not need this.
    """
    raw = st.query_params.get(param_name)
    if raw is None:
        return
    seen_key = f"_shared_flag_{param_name}"
    if st.session_state.get(seen_key) == raw:
        return
    st.session_state[seen_key] = raw
    st.session_state[state_key] = raw == "1"


def get_user_timezone() -> str:
    """The visitor's browser-detected IANA timezone (e.g. "America/Denver"),
    from st.context.timezone. Falls back to UTC if a browser ever supplies
    something zoneinfo doesn't recognize.

    LATCHED into session_state on first sight, like test_mode, admin_revealed
    and get_traffic_source. "Share Scenario" calls st.query_params.from_dict,
    which REPLACES the whole query string -- and build_share_params does not
    emit tz (it is a browser fact, not a scenario field), so a live re-read
    would return "UTC" for every timestamp written after a share. The JS does
    re-add the param on a later rerun, but that is a race this function should
    not depend on winning twice.

    st.context.timezone is the PRIMARY source and needs no round-trip -- the
    frontend sends it with the initial connection, so it is populated on the
    very first render, before anything is drawn or logged.

    The ?tz= param is a fallback, kept because it still arrives on old links.
    It cannot be the primary source: the JS that sets it uses
    history.replaceState, which changes the browser's URL without telling the
    SERVER, so st.query_params never saw the value on the visit that detected
    it. Proven by isolation -- a real page load carrying ?tz=America/Los_Angeles
    produced a "01:14 PM PDT" PDF footer, while a fresh visit whose URL the JS
    had just rewritten to the identical string produced "08:13 PM UTC" from the
    same code path. The URL looked right in the address bar the whole time,
    which is why this survived so long.

    Reads the param directly rather than through get_shared_default so an
    empty ?tz= cannot overwrite a good latched value with "".
    """
    try:
        detected = getattr(st.context, "timezone", None) or st.query_params.get("tz", "")
        if detected:
            st.session_state["_user_timezone"] = detected
        return st.session_state.get("_user_timezone", "UTC")
    except Exception:
        # No Streamlit runtime (analyze_model.py execs this section). UTC is
        # the right answer there -- there is no visitor to be local to.
        return "UTC"


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


SUPABASE_PAGE_SIZE = 1000        # PostgREST's default max-rows ceiling


def load_table_safe(table_name: str, columns: list) -> pd.DataFrame:
    """Read ALL rows from a Supabase table, tolerating any connection/query
    failure (e.g. secrets not configured yet) by returning an empty frame.

    Paginated because PostgREST caps a plain select at 1,000 rows and says so
    only in a Content-Range header the client discards. Before this, the admin
    dashboard silently saw the first 1,000 rows of each table and nothing else
    -- and the rows it dropped were the NEWEST, so a growing table presents as
    "recent traffic stopped" rather than as truncation. usage_logs crossed the
    ceiling at 1,040 rows, which is how it surfaced: a ?src= tag applied that
    afternoon was absent from the by-source table while being plainly present
    in the database.

    Reads until a short page comes back. The hard stop is a runaway guard, not
    an expected limit; if a table ever legitimately exceeds it, this needs a
    date filter rather than a bigger number, because loading it all into a
    Streamlit rerun stops being sensible well before that point.
    """
    try:
        conn = get_supabase_connection()
        rows, start = [], 0
        while start < 200_000:
            result = execute_query(
                conn.table(table_name).select("*")
                    .range(start, start + SUPABASE_PAGE_SIZE - 1),
                ttl=0)
            batch = result.data or []
            rows.extend(batch)
            if len(batch) < SUPABASE_PAGE_SIZE:
                break
            start += SUPABASE_PAGE_SIZE
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=columns)
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
PROFESSIONAL_DEBT_PATH = "data/graduate_debt_clean.csv"
# Label for the picker's first option -- the national fallback, named rather
# than left blank so "no school chosen" cannot be mistaken for "no debt".
PROFESSIONAL_SCHOOL_NATIONAL = "National average (no specific school)"
GRADUATE_SCHOOL_NATIONAL = "I'll enter my own cost"


@st.cache_data(show_spinner=False)
def load_coa_dataset() -> pd.DataFrame:
    """Load the pre-cleaned local COA dataset, tolerating a missing file
    (e.g. before it's been generated) by returning an empty frame."""
    try:
        return pd.read_csv(COA_DATASET_PATH)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        # Must name every column any caller reads, not just the COA ones. An
        # empty frame missing STABBR made metro_for_school raise rather than
        # degrade, and the same would now apply to the search's programs_*
        # and CURROPER columns.
        return pd.DataFrame(columns=[
            "INSTNM", "STABBR", "control_type", "in_state_coa", "out_of_state_coa",
            "NPCURL", "UNITID", "CITY", "CURROPER", "DISTANCEONLY", "ADM_RATE",
        ] + [f"programs_{suffix}" for suffix, _ in CREDENTIAL_LEVELS.values()])


@st.cache_data(show_spinner=False)
def load_professional_debt() -> pd.DataFrame:
    """Per-school median debt for medical, dental and law school
    (build_professional_debt.py), or an empty frame when the file is absent so
    a deploy without it loses a feature rather than a page -- the same contract
    load_coa_dataset has.

    NOTE this is a SEPARATE dataset from the undergraduate one and cannot be
    replaced by it. college_coa_clean.csv drops any institution with no
    undergraduate cost of attendance, which is every graduate-only school:
    Icahn School of Medicine and Mayo Clinic have zero rows there. They are in
    this file, which is the point.
    """
    try:
        # program_key must stay a string: for graduate rows it IS the 2-digit
        # CIP family ("01", "11"), and reading it as a number drops the leading
        # zero and stops matching MAJOR_TO_CIP_FAMILY.
        return pd.read_csv(PROFESSIONAL_DEBT_PATH,
                           dtype={"CIPCODE": str, "program_key": str})
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return pd.DataFrame(columns=["UNITID", "INSTNM", "CONTROL", "control_type",
                                     "CIPCODE", "CREDLEV", "credential",
                                     "program_key", "debt_median", "debt_10yr_payment"])


def graduate_schools_for(cip_family: str, credential: str) -> list:
    """Schools publishing a median for this CIP family at this credential.

    Separate from professional_schools_for because the two are keyed
    differently: a professional program is one of three named occupations,
    while a graduate one is a 2-digit CIP family reached through
    MAJOR_TO_CIP_FAMILY. Same file, same columns, different question.
    """
    if not cip_family or not credential:
        return []
    df = load_professional_debt()
    if df.empty or "credential" not in df.columns:
        return []
    match = df[(df["credential"] == credential) & (df["program_key"] == cip_family)]
    return sorted(match["INSTNM"].dropna().unique())


def graduate_debt_for(cip_family: str, credential: str, school_name: str):
    """That school's median graduate debt for this field, or None.

    None means "no published figure", which must fall back to asking the
    visitor -- never to zero. Only 20% of school x field cells publish a
    master's median and 6% a doctoral one, so the absent case is the common
    one and has to be a first-class path rather than an edge.
    """
    if not (cip_family and credential and school_name):
        return None
    df = load_professional_debt()
    if df.empty or "credential" not in df.columns:
        return None
    match = df[(df["credential"] == credential)
               & (df["program_key"] == cip_family)
               & (df["INSTNM"] == school_name)]
    if match.empty:
        return None
    value = pd.to_numeric(match.iloc[0]["debt_median"], errors="coerce")
    return float(value) if pd.notna(value) and value > 0 else None


PROFESSIONAL_SCHOOL_LABEL = {
    "medicine": "Medical school",
    "law": "Law school",
    "dentistry": "Dental school",
}


def render_graduate_debt_caption(debt, credential_key, school_name, container=None) -> None:
    """What the graduate figure is, and what it is NOT.

    The number is median debt at graduation, not a price: it is already net of
    scholarships, assistantships and family money. Saying so matters because
    the visitor is about to see it used as a loan, and because they can
    override it with a real cost -- which is a different quantity measuring a
    different thing.
    """
    if container is None:
        container = st
    if not school_name or school_name == GRADUATE_SCHOOL_NATIONAL:
        container.caption(
            "No school selected, so the cost below is whatever you enter. "
            "No federal dataset publishes graduate cost of attendance."
        )
        return
    if not debt:
        container.caption(
            f"{school_name} publishes no figure for this field at this level, "
            "so the cost below is whatever you enter."
        )
        return
    cap = GRADUATE_AGGREGATE_LIMIT
    text = (f"{fmt_money(debt)} median debt at graduation for this field at "
            f"{school_name} — what graduates actually borrowed, so already net "
            "of scholarships and assistantships. Not a sticker price.")
    if debt > cap:
        text += (f" {fmt_money(debt - cap)} of it is above the {fmt_money(cap)} "
                 "federal graduate ceiling and would be private borrowing.")
    container.caption(text.replace("$", chr(92) + "$"))


def professional_debt_caption(major_name: str, school_name: str, debt: float) -> str:
    """One line saying which figure is in play and where it came from.

    Shared by the sidebar and both result branches: the number changes the
    whole loan, so a visitor must be able to see whether they are looking at
    their school's median or a national average, without opening Methodology.
    """
    if not debt:
        return ""
    national = national_professional_debt(major_name)
    if not school_name or school_name == PROFESSIONAL_SCHOOL_NATIONAL:
        return (f"Using the national average of {fmt_money(national)} for "
                f"{'medical' if professional_program_for(major_name) == 'medicine' else 'this'} "
                "school debt. Pick your school to use its own figure.")
    delta = debt - national
    direction = "above" if delta > 0 else "below"
    over_cap = max(debt - PROFESSIONAL_AGGREGATE_LIMIT, 0)
    text = (f"{fmt_money(debt)} median debt at {school_name} — "
            f"{fmt_money(abs(delta))} {direction} the national average.")
    if over_cap > 0:
        # The part that cannot be a federal loan at all is the actionable half
        # of this number, and it is invisible in the median itself.
        text += (f" {fmt_money(over_cap)} of it is above the "
                 f"{fmt_money(PROFESSIONAL_AGGREGATE_LIMIT)} federal cap and would be "
                 "private borrowing.")
    return text


def render_professional_debt_caption(major_name, school_name, debt, container=None) -> None:
    text = professional_debt_caption(major_name, school_name, debt)
    if text:
        (container or st).caption(text.replace("$", chr(92) + "$"))


def professional_program_for(major_name: str):
    """The program key ("medicine"/"law"/"dentistry") this occupation attends,
    or None if it needs no professional school."""
    return PROFESSIONAL_PROGRAM_BY_OCCUPATION.get(major_name)


def professional_schools_for(program_key: str) -> list:
    """School names offering this program, for the picker's option list."""
    if not program_key:
        return []
    df = load_professional_debt()
    if df.empty:
        return []
    # Filter on credential as well: the same file now holds graduate rows whose
    # program_key is a 2-digit CIP family, and without this a professional
    # lookup could match one.
    if "credential" in df.columns:
        df = df[df["credential"] == "professional"]
    return sorted(df[df["program_key"] == program_key]["INSTNM"].dropna().unique())


def national_professional_debt(major_name: str) -> float:
    """The hand-curated national figure for this path -- AAMC/ABA/ADEA. The
    fallback whenever no school is named or the named school publishes none."""
    try:
        return float(MAJOR_DATA.get(major_name, {}).get("additional_training_debt", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def resolve_professional_debt(major_name: str, school_name: str = None) -> float:
    """What this path's professional school actually costs to finance.

    The named school's median where there is one, else the national constant.
    Every consumer must take the value from HERE and from nowhere else: the
    model reads this debt in three independent places
    (get_effective_principal, professional_debt_cap, and split_loan_financing's
    professional_principal), and if any one of them re-derives it the loan, the
    federal cap and the tranche split describe different debts. That failure is
    silent and it flatters the result.

    A missing or privacy-suppressed school must fall back to the national
    figure, never to zero: professional_debt_cap treats a falsy debt as "no
    professional tranche" and returns a cap of 0.0, which split_loan_financing
    reads as a real cap rather than "unset" -- pushing the whole tranche to
    private while the principal simultaneously loses the debt. Two different
    wrong answers from one absent row. Suppressed schools are dropped at build
    time so they never reach the picker, and this is the second line of defence.
    """
    national = national_professional_debt(major_name)
    program_key = professional_program_for(major_name)
    if not program_key or not school_name or school_name == PROFESSIONAL_SCHOOL_NATIONAL:
        return national
    df = load_professional_debt()
    if df.empty:
        return national
    if "credential" in df.columns:
        df = df[df["credential"] == "professional"]
    match = df[(df["program_key"] == program_key) & (df["INSTNM"] == school_name)]
    if match.empty:
        return national
    value = pd.to_numeric(match.iloc[0]["debt_median"], errors="coerce")
    return float(value) if pd.notna(value) and value > 0 else national


def search_schools_by_budget(cip_family: str, credential: str,
                              max_coa_per_year: float, home_state: str = None,
                              states: tuple = None, control_types: tuple = None,
                              limit: int = 50,
                              min_coa_per_year: float = 0.0) -> pd.DataFrame:
    """Schools that teach `cip_family` at `credential` for at most
    `max_coa_per_year`, cheapest first. The inverse of the app's normal
    question: not "what does the school I named cost" but "what could I attend
    for this, in this field".

    Prices each row against `home_state` INDIVIDUALLY -- in-state where the
    school sits in that state, out-of-state everywhere else. This cannot be one
    flag for the whole search the way it can for a single named school: a
    result set routinely spans nine or more states and the visitor is resident
    in exactly one of them. Pricing them all in-state understates 28% of the
    dataset (every public school; median $7,080/yr, ~$28k over four years) and
    lets a school clear a budget it does not actually fit.

    `home_state=None` means the visitor did not say, and everything is priced
    out-of-state. That is the conservative direction on purpose: it can only
    overstate cost, and an overstated cost drops a school the visitor could
    afford, where an understated one recommends a school they cannot.

    Sorted by COST and nothing else, deliberately. Every salary figure in this
    app comes from the occupation or major dataset -- no school attribute
    touches the earnings side -- so a "best value" or ROI ordering here would
    be the cost ordering wearing an outcome's name. For the same reason the ROI
    model is NOT run per row: 181 model runs per search would be slow AND
    misleading.

    Excludes schools flagged as no longer operating. Surfacing a closed
    institution as somewhere a 17-year-old could enrol is this feature's worst
    failure mode, and it is not something the visitor could be expected to
    check.

    Returns an empty frame when nothing matches, which is a real and useful
    answer -- "your budget admits nothing in this field" is the finding, not an
    error -- so callers must render that case rather than hiding it.
    """
    coa_df = load_coa_dataset()
    if coa_df.empty or not cip_family or credential not in CREDENTIAL_LEVELS:
        return pd.DataFrame()

    suffix, nominal_years = CREDENTIAL_LEVELS[credential]
    program_column = f"programs_{suffix}"
    if (program_column not in coa_df.columns
            or "in_state_coa" not in coa_df.columns
            or "out_of_state_coa" not in coa_df.columns):
        return pd.DataFrame()

    coa_df = coa_df.copy()
    # The rate that applies to THIS visitor at THIS school. Resolved before the
    # budget filter, so affordability and the sort both run on the price the
    # visitor would actually be charged rather than on the cheaper of the two.
    at_home = (coa_df["STABBR"] == home_state) if home_state else False
    coa_df["is_home_state"] = at_home
    coa_df["coa_per_year"] = coa_df["out_of_state_coa"].where(
        ~coa_df["is_home_state"], coa_df["in_state_coa"])
    # Both figures are populated for all 5,035 rows today; this keeps a
    # half-reported row costed rather than silently dropped by the notna filter
    # if a future Scorecard release stops publishing one of them.
    coa_df["coa_per_year"] = coa_df["coa_per_year"].fillna(coa_df["in_state_coa"])

    # Anchored on the pipe delimiters. Codes are fixed 2-digit today, which
    # makes a plain substring match equivalent (verified across the dataset) --
    # this keeps that true rather than relying on it.
    teaches = coa_df[program_column].fillna("").str.contains(
        rf"(?:^|\|){re.escape(str(cip_family))}(?:\||$)", regex=True)
    # A FLOOR as well as a ceiling. The ceiling alone answers "what can I
    # afford"; the floor answers "what does this cost", which is the question
    # someone asks when the cheap end is not what they are shopping for. It also
    # unsticks the result cap: results are the cheapest `limit` matches, so
    # without a floor an expensive school can be invisible however high the
    # ceiling goes -- 751 schools teach engineering and the 50 cheapest are all
    # under $24,602.
    affordable = (coa_df["coa_per_year"].notna()
                  & (coa_df["coa_per_year"] <= max_coa_per_year)
                  & (coa_df["coa_per_year"] >= min_coa_per_year))
    open_now = coa_df["CURROPER"].fillna(1) != 0

    matches = coa_df[teaches & affordable & open_now]
    if states:
        matches = matches[matches["STABBR"].isin(states)]
    if control_types:
        matches = matches[matches["control_type"].isin(control_types)]

    matches = matches.sort_values("coa_per_year").head(limit).copy()
    matches["total_program_cost"] = matches["coa_per_year"] * nominal_years
    return matches.reset_index(drop=True)


def find_school_coa(school_name: str, coa_df: pd.DataFrame, unitid=None):
    """Case-insensitive lookup by institution name: exact match first, then
    falls back to a substring match. By the time this is called, school_name
    is already a disambiguated single name (see find_matching_schools /
    _resolve_school_name below), so the substring fallback only really
    matters for the 1-match case; it's kept as a safety net. Returns None
    if nothing matches -- expected for a school outside the local dataset."""
    # A pinned UNITID wins outright. It is the only way to tell two schools
    # sharing a name apart, and without it the fallbacks below can only guess
    # -- .iloc[0] on a name shared by a California and a Kansas institution
    # picks one arbitrarily and reports its cost as though it were certain.
    pinned = _school_row_by_unitid(unitid, coa_df)
    if pinned is not None:
        return pinned
    if not school_name or coa_df.empty:
        return None
    names_lower = coa_df["INSTNM"].str.lower()
    query_lower = school_name.strip().lower()
    exact = coa_df[names_lower == query_lower]
    if not exact.empty:
        return exact.iloc[0]
    partial = coa_df[names_lower.str.contains(query_lower, regex=False)]
    return partial.iloc[0] if not partial.empty else None


# Two free, authoritative federal tools that let a student replace the app's
# school-average sticker COA with their own personalized figures (see the
# "🎯 Get Your Real Numbers" section on the main page). The SAI estimator is
# universal; the per-school Net Price Calculator link comes from the NPCURL
# column in the COA dataset, with this directory as the fallback when we have
# no URL on file for a given school.
SAI_ESTIMATOR_URL = "https://studentaid.gov/aid-estimator/"
NPC_DIRECTORY_URL = "https://collegecost.ed.gov/net-price"


def normalize_npc_url(raw) -> str:
    """Clean a raw NPCURL value from the COA dataset into a clickable https
    link, or return None if there's nothing usable. Scorecard stores these
    inconsistently -- ~45% omit the http(s):// scheme (bare domains like
    "www.school.edu/npc") and some contain literal spaces -- so we prepend a
    scheme when missing and percent-encode spaces. The raw value is left as-is
    in the CSV (traceable); normalization happens only here, at display time."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() == "nan":
        return None
    if not text.lower().startswith(("http://", "https://")):
        text = "https://" + text
    return text.replace(" ", "%20")


def get_school_npc_url(school_name: str, unitid=None) -> str:
    """Resolve a school's own Net Price Calculator URL from the local COA
    dataset, normalized for display. Returns None when the school isn't in the
    dataset or has no URL on file -- callers fall back to NPC_DIRECTORY_URL."""
    if not school_name:
        return None
    coa_match = find_school_coa(school_name, load_coa_dataset(), unitid=unitid)
    if coa_match is None:
        return None
    return normalize_npc_url(coa_match.get("NPCURL"))


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
    matches = coa_df.loc[names_lower.str.contains(search_term, regex=False)]
    if matches.empty:
        return []
    # UNITIDs, not names. Names are NOT unique: 67 of them are shared by 153
    # different institutions, and this used to return .unique() names -- so two
    # "Southwestern College" rows collapsed to one, the picker below never
    # fired, and find_school_coa silently took whichever row happened to sort
    # first. The California campus and the Kansas one differ by $36,328 a year.
    #
    # Returning one entry per INSTITUTION is what makes the existing
    # disambiguation picker able to fire for these at all.
    ordered = matches.sort_values(["INSTNM", "STABBR", "CITY"], na_position="last")
    return [int(u) for u in ordered["UNITID"].head(limit) if pd.notna(u)]


def school_option_label(unitid: int, coa_df: pd.DataFrame) -> str:
    """How one institution is shown in the picker.

    Plain name when that name is unique, "Name (City, ST)" when it is not.
    Qualifying every school would be noise; qualifying only the ambiguous ones
    puts the distinguishing detail exactly where a reader needs it -- and
    "Southwestern College" alone is unanswerable, which is the whole bug.
    """
    row = _school_row_by_unitid(unitid, coa_df)
    if row is None:
        return str(unitid)
    name = row["INSTNM"]
    if (coa_df["INSTNM"] == name).sum() <= 1:
        return name
    city, state = row.get("CITY"), row.get("STABBR")
    where = ", ".join(str(part) for part in (city, state) if pd.notna(part) and str(part))
    return f"{name} ({where})" if where else name


def _school_row_by_unitid(unitid, coa_df: pd.DataFrame):
    """The single row for a UNITID, or None. UNITID is IPEDS's institution key
    and is genuinely unique, which INSTNM is not."""
    if unitid is None or coa_df.empty or "UNITID" not in coa_df.columns:
        return None
    try:
        hit = coa_df[coa_df["UNITID"] == int(unitid)]
    except (TypeError, ValueError):
        return None
    return None if hit.empty else hit.iloc[0]


def school_name_for_unitid(unitid, coa_df: pd.DataFrame):
    """The plain institution name for a UNITID.

    Deliberately NOT the disambiguated label. school_name flows into the
    Scorecard API query, the ?school= share parameter and a logged Supabase
    column; putting "(Chula Vista, CA)" into those would change what three
    external things receive. The city stays in the picker, where it is read by
    a human, and the pin below is what carries the identity."""
    row = _school_row_by_unitid(unitid, coa_df)
    return None if row is None else row["INSTNM"]


def _resolve_school_name(search_key: str, pick_key: str) -> str:
    """The effectively-selected school right now: the picker's current
    choice if the search text matched 2+ schools (a picker is showing),
    the single match if there's exactly one, or the raw search text
    otherwise (no match -- the student's free-typed entry, used as-is)."""
    search_text = st.session_state.get(search_key, "")
    matches = find_matching_schools(search_text, load_coa_dataset())
    chosen = _resolve_school_unitid(search_key, pick_key)
    if chosen is not None:
        return school_name_for_unitid(chosen, load_coa_dataset()) or search_text
    return search_text


def _resolve_school_unitid(search_key: str, pick_key: str):
    """The UNITID of the currently-selected institution, or None when the
    typed text matched nothing in the dataset.

    Separate from _resolve_school_name because the two answer different
    questions and only one of them is safe to send outward. The NAME goes to
    the Scorecard API, the share link and Supabase; the UNITID stays inside
    the app and is what actually identifies which of two same-named schools
    the visitor meant."""
    search_text = (st.session_state.get(search_key) or "").strip()
    if not search_text:
        return None
    matches = find_matching_schools(search_text, load_coa_dataset())
    if not matches:
        return None
    if len(matches) >= 2:
        picked = st.session_state.get(pick_key)
        return picked if picked in matches else matches[0]
    return matches[0]


def get_suggested_coa_per_year(school_name: str, in_state: bool, unitid=None):
    """Cost of Attendance (in-state or out-of-state, per `in_state`) for a
    school in the local COA dataset, for auto-filling a scenario's per-year
    cost -- or None if the school has no match in the dataset."""
    match = find_school_coa(school_name, load_coa_dataset(), unitid=unitid)
    if match is None:
        return None
    return float(match["in_state_coa"] if in_state else match["out_of_state_coa"])


def _apply_pending_school() -> None:
    """Move a school chosen in the search results into the sidebar's own state.

    The search block lives in section 5 (main page), which Streamlit runs
    AFTER section 4 (sidebar) -- so its button cannot assign school_search_a
    directly; by then the widget exists and Streamlit raises. The button
    instead parks a value and reruns, and this runs at the top of the sidebar
    block on the next pass, before the widget is created.

    Clears the picker too: a stale school_pick_a from the previous search
    would either not be among the new options, or -- worse -- silently select
    the wrong institution of a same-named pair.
    """
    pending = st.session_state.pop("_pending_school", None)
    if not pending:
        return
    name, unitid, is_home_state = pending
    st.session_state["school_search_a"] = name
    st.session_state.pop("school_pick_a", None)
    if unitid is not None:
        # Seed the picker so an ambiguous name lands on the institution that
        # was actually clicked, not on whichever sorts first.
        st.session_state["school_pick_a"] = int(unitid)
    # Adopt the residency the search result was priced at, so the sidebar
    # agrees with the row that was clicked. Legal here only because the
    # in_state_a widget is created further down the sidebar than this call.
    st.session_state["in_state_a"] = is_home_state
    suggested = get_suggested_coa_per_year(name, is_home_state, unitid=unitid)
    if suggested is not None:
        st.session_state["coa_per_year_a"] = int(suggested)


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
    # Pinned, so switching the picker between two same-named schools actually
    # changes the cost that lands in the field. Without it both options
    # autofilled the same arbitrary row and the picker looked broken.
    suggested = get_suggested_coa_per_year(
        resolved_school_name, st.session_state.get(in_state_key, False),
        unitid=_resolve_school_unitid(search_key, pick_key),
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
        # predominant degree gates the Simplified scaling: 1=certificate,
        # 2=associate, 3=bachelor, 4=graduate. At a 2-year institution the
        # institution-wide median ALREADY describes associate's completers, so
        # scaling it would halve a figure that was right -- see
        # simplified_debt_scale.
        "fields": ("school.name,latest.aid.median_debt.completers.overall,"
                    "school.degrees_awarded.predominant"),
        "api_key": api_key,
    }
    try:
        # Pin TLS verification to certifi's CA bundle rather than the system
        # default. The python.org macOS build ships without a usable root store,
        # so a plain requests.get raises SSLCertVerificationError locally --
        # which fetch_* would swallow as "no data", silently disabling the
        # college-reported loan (Simplified mode) on every local run.
        response = requests.get(COLLEGE_SCORECARD_URL, params=params, timeout=6, verify=certifi.where())
        response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            return None
        top = results[0]
        return {
            "name": top.get("school.name"),
            "median_debt": top.get("latest.aid.median_debt.completers.overall"),
            "predominant_degree": top.get("school.degrees_awarded.predominant"),
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
        # Pin TLS verification to certifi's CA bundle rather than the system
        # default. The python.org macOS build ships without a usable root store,
        # so a plain requests.get raises SSLCertVerificationError locally --
        # which fetch_* would swallow as "no data", silently disabling the
        # college-reported loan (Simplified mode) on every local run.
        response = requests.get(COLLEGE_SCORECARD_URL, params=params, timeout=6, verify=certifi.where())
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
    # Nothing borrowed, nothing to repay. Without this the loop below still
    # runs one month and reports a 0.1-year payoff on a $0 loan -- previously a
    # curiosity only reachable by typing 0, now the resting state for the 430
    # occupations BLS says need no degree.
    if principal <= 0:
        return {
            "monthly_payment": 0.0,
            "total_interest": 0.0,
            "payoff_years": 0.0,
            "schedule": pd.DataFrame([{"month": 0, "year": 0.0, "balance": 0.0}]),
            "total_paid_in_roi_window": 0.0,
            "forgiven_amount": 0.0,
        }

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

def _close_schedule_at_forgiveness(schedule_rows: list, max_months: int) -> None:
    """Mark the end of a forgiven loan without duplicating a month.

    The balance goes to zero because the remainder was written off. If the loop
    already emitted this month, that row is updated in place; appending a
    second row for the same month made _merge_balance_schedules -- which
    dedupes keep="last" -- throw away the real final payment.
    """
    if schedule_rows and schedule_rows[-1]["month"] == max_months:
        schedule_rows[-1]["balance"] = 0.0
        return
    schedule_rows.append({"month": max_months, "year": max_months / 12,
                          "balance": 0.0, "payment": 0.0})


def calculate_idr_repayment(principal: float, annual_rate_pct: float,
                             major_name: str,
                             annual_income: float = None, income_growth: float = 0.03,
                             starting_interest: float = 0.0,
                             living_adjustment: float = IDR_LIVING_ADJUSTMENT,
                             payment_rate: float = IDR_PAYMENT_RATE,
                             max_term_years: int = IDR_MAX_TERM_YEARS,
                             max_months: int = None,
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
    # `principal` is the whole balance owed; starting_interest says how much of
    # it is already-accrued unpaid interest rather than borrowed money. Default
    # zero, which is the prospective case -- nobody has accrued anything yet.
    principal_balance = max(principal - starting_interest, 0.0)
    interest_balance = min(max(starting_interest, 0.0), principal)
    balance = principal_balance + interest_balance
    total_interest = 0.0
    total_paid_in_roi_window = 0.0
    forgiven_amount = 0.0
    schedule_rows = []
    # `max_months` overrides the year-based term so a borrower who has ALREADY
    # made qualifying payments can be modelled with only the months they have
    # left. Months, not years, because servicers report a payment COUNT and
    # rounding it to a year moves forgiveness by up to eleven payments.
    max_months = max_term_years * 12 if max_months is None else int(max_months)

    for month in range(1, max_months + 1):
        year_index = (month - 1) // 12
        current_salary = income_for_year(major_name, year_index, annual_income, income_growth)
        discretionary_monthly = max((current_salary / 12) - (living_adjustment / 12), 0.0)
        payment = discretionary_monthly * payment_rate

        # Interest accrues on PRINCIPAL, not on principal plus already-accrued
        # interest. Unpaid federal interest does not capitalise while it sits
        # there -- that is the whole reason a servicer shows the two apart, and
        # the reason a borrower coming off SAVE cares which pool their balance
        # is in. Charging it on the total compounded interest onto interest and
        # made the starting_interest input change nothing at all: it moved
        # dollars between two pools that were taxed identically.
        interest = principal_balance * monthly_rate
        # Cap the payment at what is actually owed. The balance already floored
        # at zero, but the full monthly payment was still being recorded, so the
        # month the loan clears charged a whole payment when only part of one
        # was due -- overstating what the borrower hands over, by up to a full
        # payment on every loan. Caught by check_repayment_invariants.py, which
        # is exactly the identity it exists to enforce.
        payment = min(payment, balance + interest)

        # Two pools, not one. A payment covers accrued interest first and only
        # then principal -- the ordinary convention, and the thing that makes an
        # income-driven balance GROW when the payment falls short: the shortfall
        # lands in the interest pool while principal does not move at all. One
        # balance can show that it grew; only the split shows why.
        interest_balance += interest
        to_interest = min(payment, interest_balance)
        interest_balance -= to_interest
        principal_balance = max(principal_balance - (payment - to_interest), 0.0)
        balance = principal_balance + interest_balance

        total_interest += interest
        if month <= roi_window_years * 12:
            total_paid_in_roi_window += payment

        schedule_rows.append({"month": month, "year": month / 12, "balance": balance,
                              "payment": payment,
                              "principal_balance": principal_balance,
                              "interest_balance": interest_balance})
        if balance <= 0:
            break
    else:
        # Loop exhausted max_term_years without reaching a zero balance:
        # remaining principal is forgiven under the IDR plan.
        forgiven_amount = balance
        balance = 0.0
        _close_schedule_at_forgiveness(schedule_rows, max_months)

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
    """One month's Repayment Assistance Plan (RAP) payment: 1% of AGI per
    $10,000 AGI band above $10,000 (so $10,001-20,000 -> 1%, $20,001-30,000
    -> 2%, ... $90,001-100,000 -> 9%), capped at 10% above $100,000 -- then
    reduced by $50/month per dependent, and floored at $10/month.

    THE $10 FLOOR IS THE WHOLE BOTTOM OF THE SCHEDULE, not a special case for
    the lowest band. studentaid.gov's chart, footnote:

        "You can subtract $50 from the monthly payment amount for each
         dependent you claim on your federal income tax return, but your
         monthly payment amount can never be less than $10."

    One floor doing two jobs. It is why the published $0-$10,000 row reads a
    flat $10.00 (1% of $10,000 is only $8.33/month), and it is what bounds the
    dependent deduction. This floored the deduction at $0 instead, so a
    borrower with any dependents and a low income was shown a $0 payment --
    reported from the live app.

    BOUNDARIES BELONG TO THE LOWER BAND. The published brackets are
    $X,001-$Y,000, so $50,000 is the TOP of the 4% bracket, not the bottom of
    the 5% one. `agi // 10000` put every exact multiple of $10,000 one band too
    high -- $50,000 charged 5% ($208.33) against a published bracket maximum of
    $166.67, a 25% overstatement on the roundest figure a visitor can type.
    Subtracting 1 first is integer-exact and needs no ceil/float rounding.

    Verified against every row and both edges of the published chart by
    check_rap_payment_table.py.
    """
    if agi <= 10000:
        base_payment = float(RAP_MIN_PAYMENT)
        applied_pct = None
    else:
        band = min(int((agi - 1) // 10000), 10)
        applied_pct = band / 100
        base_payment = agi * applied_pct / 12
    payment = max(base_payment - dependents * RAP_DEPENDENT_REDUCTION,
                  float(RAP_MIN_PAYMENT))
    return {"monthly_payment": payment, "applied_pct": applied_pct, "base_payment": base_payment}


def simulate_rap_schedule(principal: float, annual_rate_pct: float, major_name: str,
                           dependents: int = 0,
                           annual_income: float = None, income_growth: float = 0.03,
                           max_term_years: int = RAP_MAX_TERM_YEARS,
                           max_months: int = None,
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
    total_interest = 0.0
    waived_interest = 0.0
    # Principal the GOVERNMENT paid down, not the borrower: RAP tops the
    # reduction up to $50/month when their own payment does less. Tracked so
    # the money-in/money-out identity closes -- without it, payments plus
    # forgiveness fall short of principal plus interest by exactly this, and an
    # invariant check cannot tell that from a real accounting bug.
    government_match = 0.0
    schedule_rows = []
    # `max_months` overrides the year-based term so a borrower who has ALREADY
    # made qualifying payments can be modelled with only the months they have
    # left. Months, not years, because servicers report a payment COUNT and
    # rounding it to a year moves forgiveness by up to eleven payments.
    max_months = max_term_years * 12 if max_months is None else int(max_months)

    for month in range(1, max_months + 1):
        year_index = (month - 1) // 12
        agi = income_for_year(major_name, year_index, annual_income, income_growth)
        payment = calculate_rap_payment(agi, dependents)["monthly_payment"]
        interest = balance * monthly_rate
        # Same final-month cap as the IDR simulator -- see the note there.
        # Applied BEFORE the interest split below so the waived figure cannot
        # be computed against a payment the borrower never made.
        payment = min(payment, balance + interest)
        # RAP waives the interest a payment does NOT cover. Interest the
        # payment DOES cover is paid by the borrower and is a real cost -- so
        # the split is min/max, not "all of it is free". Reporting the whole
        # accrual as waived was wrong in the direction that flatters the plan,
        # and wrong by the largest amount for borrowers whose payment always
        # exceeds the interest: they have nothing waived at all.
        total_interest += min(payment, interest)
        waived_interest += max(interest - payment, 0.0)
        principal_reduction = payment - interest
        if principal_reduction < RAP_PRINCIPAL_MATCH_CAP:
            topped_up = min(balance, RAP_PRINCIPAL_MATCH_CAP)
            government_match += max(topped_up - max(principal_reduction, 0.0), 0.0)
            principal_reduction = topped_up
        balance = max(balance - principal_reduction, 0.0)
        if month <= roi_window_years * 12:
            total_paid_in_roi_window += payment
        # `payment` is emitted for the same reason the IDR simulator emits it:
        # a RAP payment moves with income, so payment_for_month and the
        # schedule merge cannot reconstruct it from a flat figure. Without this
        # column, promoting RAP to a selectable strategy raises a KeyError in
        # the take-home charts rather than at import.
        schedule_rows.append({"month": month, "year": month / 12,
                              "balance": balance, "payment": payment})
        if balance <= 0:
            break
    else:
        forgiven_amount = balance
        balance = 0.0
        # Zero the LAST row rather than appending another one for the same
        # month. Appending duplicated month max_months, and _merge_balance_
        # schedules dedupes with keep="last" -- so the real final payment was
        # silently replaced by the closing row's 0.00 whenever a combined
        # schedule was built. Worth $150 on the case that surfaced it, and
        # invisible in every figure except the accounting identity.
        _close_schedule_at_forgiveness(schedule_rows, max_months)

    schedule_df = pd.DataFrame(schedule_rows)
    return {
        # Interest the borrower actually paid. NOT zero: RAP's subsidy covers
        # only the shortfall between the payment and the accrual. A borrower
        # earning enough that the payment always exceeds the interest has none
        # of it waived and pays every dollar -- $165,109 on a $190,000 loan in
        # the case that surfaced this.
        "total_interest": total_interest,
        # What the subsidy was actually worth. Zero for that same borrower,
        # which is the point: it is a figure about this scenario, not a
        # property of the plan.
        "waived_interest": waived_interest,
        "government_match": government_match,
        "payoff_years": schedule_df["month"].iloc[-1] / 12,
        "schedule": schedule_df,
        "total_paid_in_roi_window": total_paid_in_roi_window,
        "forgiven_amount": forgiven_amount,
    }


# ---- 2f. 10-Year ROI ------------------------------------------------------

def returning_student_curve(current_salary: float, salary_in_10_years: float,
                            hs_wage_index: float = 1.0):
    """The baseline for someone already working: what they'd earn WITHOUT the
    degree, year by year.

    Replaces the debt-free-high-school-graduate curve, which is meaningless for
    a 49-year-old going back for a master's -- her alternative was her existing
    job at her existing salary, not being a teenager. The article this was built
    for makes the point directly: 24.6M federal borrowers are 35+ against 20.2M
    under 35, and policy (and this app) assumed the 20-something.

    Two anchors, because staying put is not standing still: today's salary and
    what they expect in ten years without the degree. Linear between them, then
    the same annual growth beyond -- deliberately NOT compounding from year 0,
    which would let a small entered growth rate balloon over a 30-year horizon
    and quietly make the degree look bad.

    NOT scaled by hs_wage_index. That index moves a NATIONAL median to a city;
    a salary the visitor typed is already their real, local pay, and scaling it
    would inflate a figure that needs no adjusting -- the same
    double-counting the metro-wage comment in calculate_roi warns about,
    arriving from the other direction. The argument is accepted and ignored so
    callers can pass it uniformly.
    """
    annual_step = (salary_in_10_years - current_salary) / 10.0

    def wage(year_index: int) -> float:
        return max(current_salary + annual_step * year_index, 0.0)

    return wage


def calculate_roi(major_name: str, total_loan_payments_in_window: float,
                   total_investment: float, col_index: float = 100.0,
                   years: int = ROI_WINDOW_YEARS,
                   hs_wage_index: float = 1.0,
                   personal_contribution: float = 0.0,
                   enrollment_years: int = 0,
                   working_years: int = 0,
                   baseline_start_age: int = None,
                   baseline_curve=None) -> dict:
    """
    ROI = (major's cumulative earnings over `years`, minus loan payments made
    in that window, minus any personal_contribution) compared against the
    baseline's cumulative earnings over the same window -- a debt-free high
    school graduate by default, or, when `baseline_curve` is supplied, the
    visitor's own salary had they not gone back to school. Everything the
    page SAYS about that baseline comes from counterfactual_vocab(), which
    has to be kept on the same side of this switch. `total_investment` is the ROI%
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
    #
    # baseline_start_age (None = off) makes each of those years use that age's
    # own wage rather than one all-ages median -- see hs_wage_for_timeline_year.
    # ONE callable, used for both sums below. The two used to repeat the same
    # hs_wage_for_timeline_year call, and CLAUDE.md warns that they only cancel
    # in the premium if computed identically -- so a returning-student baseline
    # that replaced one and not the other would invent an earnings premium out
    # of nothing, silently. Binding it once makes that mistake unavailable
    # rather than merely documented.
    baseline_wage = baseline_curve or (
        lambda y: hs_wage_for_timeline_year(y, hs_wage_index, baseline_start_age))

    hs_cumulative_earnings = sum(
        baseline_wage(y) for y in range(years + enrollment_years)
    )
    # Part-time-while-working community-college years: the major side earns a
    # HS-equivalent wage for the first `working_years` of the timeline (front,
    # growing from year 0 -- identical terms to the HS baseline's first
    # working_years, so they cancel in the premium). 0 unless the part-time CC
    # path is on AND the foregone-earnings option is on.
    # Age-aware on the same terms as the baseline above, and that is load-
    # bearing rather than tidiness: these are the same person at the same ages,
    # and the comment above only holds -- they cancel in the premium -- if both
    # sides are computed identically. Scaling one and not the other would
    # invent an earnings premium out of the part-time community-college path.
    major_working_earnings = sum(
        baseline_wage(y) for y in range(working_years)
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


def cumulative_loan_paid_by_year(repayment_result: dict, years: int) -> list:
    """Loan dollars paid from month 1 through the end of each year 1..years.

    The three repayment simulators emit different schedules -- IDR carries a
    per-month `payment` column because its payment moves with income, while
    Standard and RAP carry only the balance. So this reads the payment column
    when it exists and reconstructs from the flat monthly payment when it
    doesn't, rather than assuming one shape.

    Naturally capped at payoff: every schedule stops at the month the balance
    hits zero, so years past that repeat the final cumulative total instead of
    charging payments that were never made.
    """
    schedule = repayment_result.get("schedule")
    if schedule is None or schedule.empty:
        return [0.0] * years
    months = schedule["month"]
    if "payment" in schedule.columns:
        paid = schedule["payment"].cumsum()
    else:
        paid = months * repayment_result.get("monthly_payment", 0.0)

    totals = []
    for year in range(1, years + 1):
        within = paid[months <= year * 12]
        totals.append(float(within.iloc[-1]) if len(within) else 0.0)
    return totals


def build_net_position_series(scenario: dict, col_index: float, hs_wage_index: float,
                               years: int) -> list:
    """Net position at the end of each year 1..years, for this scenario and for
    the high-school baseline it's measured against: [{year, major, hs}].

    Every point comes from calculate_roi with the window shortened to that
    year, rather than from a second formula written for the chart. That is the
    whole point -- a hand-rolled trajectory would be a third implementation of
    the ROI model (after the on-screen and PDF paths) and would drift from the
    headline figure it sits beside. Year `years` here is identical to the
    metric above the chart by construction.

    total_investment is passed as 0 because only the two net positions are
    read; roi_pct comes back None and is discarded.
    """
    paid_by_year = cumulative_loan_paid_by_year(scenario["repayment_result"], years)
    points = []
    for year in range(1, years + 1):
        result = calculate_roi(
            scenario["major"], paid_by_year[year - 1], 0,
            col_index=col_index, years=year, hs_wage_index=hs_wage_index,
            personal_contribution=scenario["personal_contribution"],
            enrollment_years=scenario["enrollment_years"],
            working_years=scenario["working_years"],
            baseline_start_age=scenario.get("baseline_start_age"),
        )
        points.append({"year": year,
                       "major": result["major_net_position"],
                       "hs": result["hs_net_position"]})
    return points


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


def undergraduate_schedule(schedule: list, graduate_years: int) -> list:
    """The undergraduate rows of a loan schedule.

    Graduate years are the LAST `graduate_years` entries -- you do the
    bachelor's first. federal_direct_cap indexes its annual limits by position
    in this list as class standing, so handing it the whole schedule counted
    graduate years as 1st- and 2nd-year undergraduate ones: a returning
    master's student picked up $12,000 of undergraduate capacity they cannot
    borrow, on top of the correct $41,000 graduate figure.
    """
    if graduate_years <= 0:
        return schedule
    return schedule[:max(len(schedule) - graduate_years, 0)]


def graduate_direct_cap(graduate_years: int) -> float:
    """Total Direct Unsubsidized a GRADUATE student can borrow for their own
    graduate study: $20,500 a year against a $100,000 aggregate.

    Separate from federal_direct_cap because that function indexes the
    undergraduate schedule by class standing (1st year $5,500, 2nd $6,500 ...),
    a dimension graduate borrowing does not have -- it is one flat annual
    figure. Feeding graduate years through the undergraduate table gave a
    master's student $12,000 of federal capacity instead of $41,000 and pushed
    the difference into the private tranche.
    """
    return min(max(graduate_years, 0) * GRADUATE_ANNUAL_UNSUB_LIMIT,
               GRADUATE_AGGREGATE_LIMIT)


def federal_direct_cap(schedule: list, dependency: str) -> float:
    """Total federal Direct (sub+unsub) a student can borrow across the financed
    years of `schedule` (the per-year list from compute_loan_schedule_by_year).
    Community-college years borrow $0 (they're paid out of pocket, phase
    "community_college"), so only "university" rows count, each at its
    class-standing annual limit (1st/2nd/3rd/4th year). Bounded by the lifetime
    aggregate cap. Anything a family needs above this is gap financing."""
    limits = FEDERAL_DIRECT_ANNUAL_LIMITS.get(dependency, FEDERAL_DIRECT_ANNUAL_LIMITS["dependent"])
    total = 0.0
    for row in schedule:
        if row.get("phase") == "university":
            total += limits[min(row["year"], 4)]
    return min(total, FEDERAL_DIRECT_AGGREGATE_CAP.get(dependency, FEDERAL_DIRECT_AGGREGATE_CAP["dependent"]))


def parent_plus_cap(schedule: list, dependency: str, start_year: int = None,
                     graduate_years: int = 0) -> float:
    """Total Direct PLUS a PARENT can borrow across the financed years of
    `schedule`, post-OBBBA. Mirrors federal_direct_cap: only "university" rows
    count, each at the annual limit, bounded by the aggregate.

    Zero for an independent student, and that is not an edge case being
    swept aside -- Parent PLUS is borrowed BY A PARENT for a dependent
    undergraduate. An independent student has no parent borrowing on their
    behalf, so every dollar above their (higher) Direct limit is private money.
    Returning students are independent almost by definition, which makes this
    the common case for the very borrowers the article was about.

    Years disbursed before the effective date keep the old "cost of attendance
    minus other aid" rule, which had no practical ceiling, so they contribute
    an unbounded amount rather than $20,000. The interim exception -- which
    keeps the pre-OBBBA limits for a student already enrolled on 2026-06-30 who
    had already taken a Direct Loan -- is NOT modelled: it can only loosen the
    cap, it requires facts the app never asks for, and it cannot apply to the
    prospective students this app is for.
    """
    if dependency != "dependent" or graduate_years > 0:
        # Parent PLUS is for a DEPENDENT UNDERGRADUATE. A graduate student is
        # independent for federal aid by definition, so no parent borrows on
        # their behalf and Grad PLUS -- the loan that used to fill this role --
        # was abolished by OBBBA. This used to be gated on the dependency radio
        # alone, which is an undergraduate question, so a master's student who
        # left it on "dependent" was handed up to $65,000 of a loan that cannot
        # exist.
        return 0.0
    total = 0.0
    for row in schedule:
        if row.get("phase") != "university":
            continue
        year = row.get("year", 1)
        calendar_year = (start_year + year - 1) if start_year is not None else None
        if calendar_year is not None and calendar_year < PARENT_PLUS_LIMIT_EFFECTIVE_YEAR:
            return float("inf")   # pre-OBBBA: COA minus aid, no usable ceiling
        total += PARENT_PLUS_ANNUAL_LIMIT
    return min(total, PARENT_PLUS_AGGREGATE_LIMIT)


def professional_debt_cap(major_name: str, professional_debt: float = None) -> float:
    """How much of a major's `additional_training_debt` can be borrowed from
    the federal government, post-OBBBA: $50,000 per year of professional school
    (its `unpaid_training_years`), bounded by the $200,000 aggregate.

    Zero-length or absent training means no professional tranche, and the
    caller falls back to treating the whole loan as undergraduate.

    Medicine (M.D.), Dentistry (D.D.S./D.M.D.) and Law (LL.B./J.D.) -- the three
    groups this app models -- are on ED's ORIGINAL 11-field professional-degree
    list AND on the expanded list the 2026-06-24 court stay temporarily added,
    so they qualify under either outcome of the litigation. That is why the cap
    can be applied to them unconditionally.

    Programs at the margin are a different matter: the stay temporarily brought
    in Nursing (M.S.N./D.N.P.), Physical and Occupational Therapy, Athletic
    Training, the psychology doctorates and others, and ED is appealing. If the
    appeal succeeds the list reverts to the original 11. Any such program would
    need its own entry here with its own program length -- never a silent
    default, because the answer changes with the case.
    [Source: NASFAA, "Temporary Changes to Professional Student Loan Limits",
    updated 2026-07-30.]
    """
    entry = MAJOR_DATA.get(major_name, {})
    debt = (entry.get("additional_training_debt", 0)
            if professional_debt is None else professional_debt)
    if not debt:
        return 0.0
    years = entry.get("unpaid_training_years", 0)
    return min(years * PROFESSIONAL_ANNUAL_UNSUB_LIMIT, PROFESSIONAL_AGGREGATE_LIMIT)


def simplified_debt_scale(program_years: int, predominant_degree=None,
                           dependency: str = "dependent") -> float:
    """How much of a school's institution-wide median completer debt a
    `program_years`-year program should be charged. 1.0 = charge it in full.

    Simplified mode takes the loan straight from College Scorecard's
    `median_debt.completers.overall`. That figure has NO time dimension -- it
    is cumulative federal debt at exit, for whatever program each completer
    took -- so at a four-year institution it describes a four-year path, and
    handing it unchanged to a two-year career overstates the debt by more than
    double.

    Two conditions must BOTH hold before scaling:

      1. The program is shorter than UNDERGRAD_YEARS.
      2. The school predominantly awards bachelor's degrees or higher
         (predominant_degree >= 3). This one is easy to miss and matters more.
         At a community college "institution-wide" already MEANS associate's
         completers -- there is no four-year blend to correct -- so scaling
         would halve a figure that was already right. Verified against live
         Scorecard data: Santa Monica College reports $6,450 with predominant
         2, Washtenaw $13,310 with predominant 1; both are already two-year
         figures. And associate's career + community college is exactly the
         pairing this app steers people toward, so the naive version would be
         most wrong where the app is most confident.

    The ratio is cumulative federal Direct limits, `program_years` against
    four, computed by reusing federal_direct_cap so it inherits the aggregate
    clamp and cannot drift from the Detailed-mode cap logic. Two years gives
    0.444, three gives 0.722.

    On why that and not a plain years/UNDERGRAD_YEARS: 0.444 is NOT more
    accurate than 0.500. For a median borrower at a low-cost school it is cost
    that binds, not the federal limit. Its only claim is that it is derived
    from a published federal schedule rather than picked, and that it is
    dependency-invariant -- dependent 12,000/27,000 and independent
    20,000/45,000 are both 0.4444, so `dependency` cannot swing it for any
    length this app can produce. That matters because in Simplified mode the
    dependency radio isn't even rendered, so the value may be a default the
    visitor never saw.
    """
    if program_years >= UNDERGRAD_YEARS or program_years <= 0:
        return 1.0
    try:
        if int(predominant_degree) < 3:
            return 1.0
    except (TypeError, ValueError):
        # Unknown predominant level: don't scale. A wrong scale-down is worse
        # than a known-conservative overstatement, since the app's whole
        # failure mode is understating what a degree costs.
        return 1.0
    span = lambda n: federal_direct_cap(
        [{"year": i, "phase": "university"} for i in range(1, n + 1)], dependency)
    full = span(UNDERGRAD_YEARS)
    return span(program_years) / full if full else 1.0


def loan_amount_label(loan_basis: str, program_years: int) -> str:
    """Label for the Total Loan Amount figure, matching how it was derived.

    The previous label was `f"Total Loan Amount (all {program_years} years)"`
    unconditionally, which was wrong in two different ways at once. For a
    Simplified figure it asserted a length the Scorecard number does not have
    -- that median is cumulative debt at exit with no time dimension at all --
    and once associate's degrees started resolving to 2 it printed "all 2
    years" over an unscaled four-year institution-wide figure.
    """
    if loan_basis == "no_program":
        return "Total Loan Amount (no degree required)"
    if loan_basis == "reported_scaled":
        return f"Estimated Loan Amount ({program_years}-year program)"
    if loan_basis == "reported":
        return "Total Loan Amount (school-reported)"
    if loan_basis == "graduate_reported":
        # Like "reported", this figure has no time dimension -- it is debt at
        # graduation for this field at this school, so naming a year count over
        # it would assert something Scorecard does not measure.
        return "Total Loan Amount (graduate, school-reported)"
    return f"Total Loan Amount (all {program_years} years)"


def split_loan_financing(effective_principal: float, federal_cap: float,
                          federal_rate: float, gap_rate: float,
                          include_fees: bool = True,
                          plus_cap: float = None,
                          professional_principal: float = 0.0,
                          professional_cap: float = None,
                          professional_rate: float = None) -> dict:
    """Split a loan into a federal Direct tranche (up to federal_cap) and a
    higher-rate gap tranche (PLUS/private) for whatever's above the cap, then
    reduce them to a single fee-adjusted principal + principal-weighted blended
    rate to feed the existing repayment engine (Option A).

    Professional-school debt (`professional_principal`) is capped and priced
    separately -- see professional_debt_cap. It used to ride in the gap tranche
    as a Grad PLUS proxy, which was right until OBBBA abolished Grad PLUS.
    Fees are a disbursement gross-up: you repay a bit more than you receive.
    Returns the tranche figures plus what the engine should use.

    `plus_cap` splits that gap tranche again, into the part a parent can
    actually borrow from the government (Direct PLUS, now capped -- see
    parent_plus_cap) and the part that has to come from somewhere else. Before
    OBBBA the gap was all federally borrowable at "COA minus aid", so one
    tranche was the whole story; it no longer is, and the model said a family
    could federally borrow sums that are now simply not available to them.

    Pass None to keep the single undifferentiated gap tranche -- that is what
    Simplified mode and analyze_model.py get, and it is the pre-OBBBA behaviour
    unchanged.
    """
    # Undergraduate and professional debt are separate pools with separate
    # ceilings, and must be capped separately rather than against one pooled
    # limit. Pooling lets unused undergraduate headroom absorb professional
    # debt that has its own, already-exhausted cap: a $10,000 undergrad loan
    # plus $205,000 of medical school would report all $215,000 as federal,
    # hiding the $5,000 that is not federally borrowable at all.
    # professional_cap=None means "no professional handling" -- the whole loan
    # is treated as undergraduate, which is the pre-OBBBA behaviour exactly
    # (that debt rode in the gap tranche as a Grad PLUS proxy). Simplified mode
    # and analyze_model.py take this path.
    professional = (min(max(professional_principal, 0.0), max(effective_principal, 0.0))
                    if professional_cap is not None else 0.0)
    undergraduate = max(effective_principal, 0.0) - professional

    ug_federal = min(undergraduate, max(federal_cap, 0.0))
    ug_rest = undergraduate - ug_federal
    if plus_cap is None:
        plus_principal, ug_private = ug_rest, 0.0
    else:
        plus_principal = min(ug_rest, max(plus_cap, 0.0))
        ug_private = ug_rest - plus_principal

    # Parent PLUS is for dependent UNDERGRADUATES, so it can never cover
    # professional debt -- and with Grad PLUS abolished there is no federal
    # loan behind this tranche at all once the unsubsidized cap is reached.
    prof_federal = min(professional, max(professional_cap or 0.0, 0.0))
    prof_private = professional - prof_federal

    federal_principal = ug_federal + prof_federal
    private_principal = ug_private + prof_private
    gap_principal = plus_principal + private_principal
    fed_fee = ORIGINATION_FEE["federal"] if include_fees else 0.0
    gap_fee = ORIGINATION_FEE["gap"] if include_fees else 0.0
    # Graduate/professional Direct Unsubsidized is its own published rate, well
    # above the undergraduate one (8.07% vs 6.52% for 2026-27). Pricing medical
    # school at the undergraduate rate would make it look ~1.5 points cheaper
    # than it is, which is exactly the direction this app must not err in. Same
    # 1.057% origination fee -- that is a Direct Sub/Unsub fee at any level.
    prof_rate = professional_rate if professional_rate is not None else federal_rate
    ug_federal_financed = ug_federal * (1 + fed_fee)
    prof_federal_financed = prof_federal * (1 + fed_fee)
    federal_financed = ug_federal_financed + prof_federal_financed
    # The 4.228% origination fee is a Direct PLUS fee, so it applies to the PLUS
    # portion only. Private lenders overwhelmingly charge no origination fee --
    # grossing private money up by a federal fee would invent a cost. Note this
    # cuts the OTHER way from the rate simplification below, and by much less.
    plus_financed = plus_principal * (1 + gap_fee)
    private_financed = private_principal
    gap_financed = plus_financed + private_financed
    financed_principal = federal_financed + gap_financed
    # Both non-federal tranches are priced at gap_rate, because the app has one
    # non-federal rate input and inventing a private-loan spread would be an
    # unsourced number presented as a finding. Private student loans generally
    # cost MORE than Direct PLUS and are credit-priced, so this understates the
    # private tranche -- stated in the disclosure rather than silently absorbed,
    # since understating cost is this app's own worst failure mode.
    if financed_principal > 0:
        blended_rate = (ug_federal_financed * federal_rate
                        + prof_federal_financed * prof_rate
                        + gap_financed * gap_rate) / financed_principal
    else:
        blended_rate = federal_rate
    return {
        "federal_principal": federal_principal,
        "undergrad_federal_principal": ug_federal,
        "professional_federal_principal": prof_federal,
        "professional_rate": prof_rate,
        "gap_principal": gap_principal,
        "plus_principal": plus_principal,
        "private_principal": private_principal,
        "financed_principal": financed_principal,
        "blended_rate": blended_rate,
        "federal_rate": federal_rate,
        "gap_rate": gap_rate,
        "fees_included": include_fees,
        "gap_share": (gap_principal / effective_principal) if effective_principal > 0 else 0.0,
        "private_share": (private_principal / effective_principal) if effective_principal > 0 else 0.0,
        # The two pools that matter for FORGIVENESS, already fee-grossed and
        # rate-weighted so a caller can amortise each on its own terms:
        #
        #   forgivable    -- the student's own Direct loans (undergraduate
        #                    Sub/Unsub + graduate/professional Unsub). These are
        #                    the only loans an income-driven plan can forgive.
        #   nonforgivable -- Parent PLUS and private money. Parent PLUS is the
        #                    parent's loan and is not IDR-eligible; private
        #                    loans are outside the federal system entirely.
        #                    Neither is ever written off at the end of a term.
        "forgivable_principal": federal_financed,
        "forgivable_rate": ((ug_federal_financed * federal_rate
                             + prof_federal_financed * prof_rate) / federal_financed
                            if federal_financed > 0 else federal_rate),
        "nonforgivable_principal": gap_financed,
        "nonforgivable_rate": gap_rate,
    }


def _merge_balance_schedules(a, b, a_flat=0.0, b_flat=0.0):
    """Two amortisation schedules added into one, month by month.

    Needed because the balance chart and cumulative_loan_paid_by_year read the
    schedule directly: hand back only one tranche's schedule and the chart
    draws half the debt while the metrics above it describe all of it.

    Balances are forward-filled past the shorter schedule's payoff, where its
    final row is already zero, so the sum stays correct after one loan clears.
    The per-month `payment` column is reconstructed for whichever side lacks it
    (only the IDR simulator emits one) from that side's flat payment while it
    still owes something -- otherwise a combined schedule would report the
    income-driven payment alone as if the private loan cost nothing.
    """
    # Deduplicate on month before indexing. The IDR and RAP simulators append a
    # closing zero-balance row at max_months when the term runs out and the
    # remainder is forgiven -- and when the loop itself already emitted that
    # month, the schedule carries it twice. Reindexing on a duplicated label
    # raises, so this crashed for exactly the scenarios where forgiveness
    # happens, which is the case the split exists to model. keep="last" takes
    # the closing row, which is the post-forgiveness balance.
    a = a.drop_duplicates(subset="month", keep="last")
    b = b.drop_duplicates(subset="month", keep="last")
    months = sorted(set(a["month"]).union(set(b["month"])))

    def balances(df):
        return df.set_index("month")["balance"].reindex(months).ffill().fillna(0.0)

    def payments(df, flat):
        if "payment" in df.columns:
            return df.set_index("month")["payment"].reindex(months).fillna(0.0)
        # No per-month column: this side pays `flat` for exactly the months it
        # appears in, and nothing after it clears.
        owed = df.set_index("month")["balance"].reindex(months)
        return pd.Series([flat if pd.notna(v) else 0.0 for v in owed], index=months)

    out = pd.DataFrame({"month": months})
    out["year"] = out["month"] / 12
    out["balance"] = (balances(a).values + balances(b).values)

    # Carry the principal/unpaid-interest split through the merge. Without this
    # the combined schedule loses the columns entirely and the stacked chart
    # silently falls back to a single line -- which is exactly the case that
    # needs it, since a federal tranche on an income-driven plan is where the
    # interest pool grows. A tranche that tracks no split (Standard, and the
    # private side generally) contributes its whole balance as principal and
    # nothing as interest, which is true of it.
    def component(df, column):
        if column in df.columns:
            return df.set_index("month")[column].reindex(months).ffill().fillna(0.0)
        if column == "principal_balance":
            return balances(df)
        return pd.Series(0.0, index=months)

    if ("principal_balance" in a.columns) or ("principal_balance" in b.columns):
        out["principal_balance"] = (component(a, "principal_balance").values
                                    + component(b, "principal_balance").values)
        out["interest_balance"] = (component(a, "interest_balance").values
                                   + component(b, "interest_balance").values)
    if "payment" in a.columns or "payment" in b.columns:
        out["payment"] = (payments(a, a_flat).values + payments(b, b_flat).values)
    return out


def combine_repayment_results(primary: dict, secondary: dict) -> dict:
    """Two separately-amortised loans presented as the one bill a person pays.

    Sums what adds (payment, interest, amount forgiven, paid-in-window) and
    takes the LATER payoff, because you are free when the last loan clears.

    Amortising separately rather than blending into one rate is the same
    reasoning split_loan_financing's blend already documents, applied one level
    up: a blend is only honest for tranches taken and repaid on the SAME terms.
    An existing balance isn't, and neither is a private loan sitting beside an
    income-driven federal one.
    """
    if not secondary:
        return dict(primary)
    out = dict(primary)
    for key in ("monthly_payment", "total_interest", "total_paid_in_roi_window",
                "forgiven_amount"):
        if key in primary and key in secondary:
            out[key] = primary[key] + secondary[key]
    out["payoff_years"] = max(primary["payoff_years"], secondary["payoff_years"])
    if "schedule" in primary and "schedule" in secondary:
        out["schedule"] = _merge_balance_schedules(
            primary["schedule"], secondary["schedule"],
            primary.get("monthly_payment", 0.0), secondary.get("monthly_payment", 0.0))
    return out


def compute_scenario_results(major_name: str, loan_amount: float,
                              interest_rate: float, repayment_strategy: str,
                              personal_contribution: float = 0.0,
                              col_index: float = 100.0,
                              roi_window_years: int = ROI_WINDOW_YEARS,
                              hs_wage_index: float = 1.0,
                              enrollment_years: int = 0,
                              working_years: int = 0,
                              baseline_start_age: int = None,
                              federal_cap: float = None, gap_rate: float = None,
                              plus_cap: float = None,
                              dependents: int = 0,
                              professional_debt: float = None,
                              include_fees: bool = False,
                              baseline_salary_now: float = None,
                              baseline_salary_in_10y: float = None,
                              existing_debt: float = 0.0,
                              existing_debt_rate: float = None) -> dict:
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
    # Resolved once, here, and passed to all three consumers. See
    # resolve_professional_debt: re-deriving it in any of them makes the
    # principal, the federal cap and the tranche split describe different debts.
    _professional_debt = (professional_debt if professional_debt is not None
                          else MAJOR_DATA.get(major_name, {}).get("additional_training_debt", 0))
    effective_principal = get_effective_principal(major_name, loan_amount, _professional_debt)
    total_investment = effective_principal + personal_contribution
    # Cap-and-gap financing (Option A): when a federal Direct cap and a gap rate
    # are supplied (Detailed mode), split the loan into the capped federal
    # tranche and a higher-rate gap tranche, then repay a single fee-adjusted
    # principal at the blended rate. With no cap/gap (the default, e.g. Simplified
    # mode or legacy callers) it's the original single-rate behavior untouched.
    if federal_cap is not None and gap_rate is not None:
        financing = split_loan_financing(
            effective_principal, federal_cap, interest_rate, gap_rate, include_fees,
            plus_cap=plus_cap,
            # Derived here rather than threaded through every caller: this
            # function already knows the major, and get_effective_principal
            # above folded that debt into the principal a line earlier.
            professional_principal=_professional_debt,
            professional_cap=professional_debt_cap(major_name, _professional_debt),
            professional_rate=PROFESSIONAL_DIRECT_RATE)
        principal_for_repayment = financing["financed_principal"]
        rate_for_repayment = financing["blended_rate"]
    else:
        financing = None
        principal_for_repayment = effective_principal
        rate_for_repayment = interest_rate
    if repayment_strategy == "Standard 10-Year":
        repayment_result = calculate_standard_repayment(
            principal_for_repayment, rate_for_repayment, roi_window_years=roi_window_years)
        strategy_label = "Standard 10-Year"
    elif repayment_strategy == TIERED_STANDARD_STRATEGY_LABEL:
        # OBBBA's replacement for Standard 10-Year: a fixed payment, but over a
        # term set by how much is owed (calculate_tiered_standard_term). It
        # forgives nothing, so unlike RAP and IDR it needs no forgivable-pool
        # split -- both pools are repaid in full either way, which is also what
        # keeps the blended rate legitimate here.
        _tiered_term = calculate_tiered_standard_term(principal_for_repayment)
        repayment_result = calculate_standard_repayment(
            principal_for_repayment, rate_for_repayment, _tiered_term,
            roi_window_years=roi_window_years)
        strategy_label = TIERED_STANDARD_STRATEGY_LABEL
    elif repayment_strategy == RAP_STRATEGY_LABEL:
        # RAP forgives at 30 years, so it takes the same forgivable-pool split
        # as IDR. `dependents` is a real parameter rather than a session_state
        # read because find_breakeven_loan is @st.cache_data and calls this --
        # a value read inside would not key the cache, so the break-even would
        # go stale the moment the visitor changed it.
        if financing and financing.get("nonforgivable_principal", 0) > 0:
            federal_part = simulate_rap_schedule(
                financing["forgivable_principal"], financing["forgivable_rate"],
                major_name, dependents, roi_window_years=roi_window_years)
            nonfederal_part = calculate_standard_repayment(
                financing["nonforgivable_principal"], financing["nonforgivable_rate"],
                roi_window_years=roi_window_years)
            repayment_result = combine_repayment_results(federal_part, nonfederal_part)
        else:
            repayment_result = simulate_rap_schedule(
                principal_for_repayment, rate_for_repayment, major_name, dependents,
                roi_window_years=roi_window_years)
        strategy_label = RAP_STRATEGY_LABEL
    elif financing and financing.get("nonforgivable_principal", 0) > 0:
        # Income-driven repayment writes off whatever is left at the end of the
        # term -- but ONLY on the student's own federal Direct loans. Parent
        # PLUS is the parent's loan and is not IDR-eligible; private loans are
        # outside the federal system altogether. Running one blended balance
        # through the IDR simulator forgave both, which is the single most
        # flattering thing this model could do to a plan that is mostly private
        # money: the larger the private tranche, the bigger the imaginary
        # write-off. Post-OBBBA that tranche is often most of the loan.
        #
        # So the two pools are amortised on their own terms and added: the
        # federal part income-driven and forgivable, the rest on an ordinary
        # fixed schedule that runs to completion.
        federal_part = calculate_idr_repayment(
            financing["forgivable_principal"], financing["forgivable_rate"],
            major_name, roi_window_years=roi_window_years)
        nonfederal_part = calculate_standard_repayment(
            financing["nonforgivable_principal"], financing["nonforgivable_rate"],
            roi_window_years=roi_window_years)
        repayment_result = combine_repayment_results(federal_part, nonfederal_part)
        strategy_label = "Income-Driven Repayment"
    else:
        repayment_result = calculate_idr_repayment(
            principal_for_repayment, rate_for_repayment, major_name, roi_window_years=roi_window_years)
        strategy_label = "Income-Driven Repayment"
    # NEW borrowing only, deliberately. An existing balance is paid whether or
    # not this degree happens, so it sits on BOTH sides of the comparison and
    # cancels; charging it to the degree would make the degree look worse than
    # it is and tell someone not to go back to school because of a loan they
    # already have -- an answer given for a reason that has nothing to do with
    # the decision in front of them.
    # Built here from SCALARS rather than accepting a callable, because
    # find_breakeven_loan is @st.cache_data and calls this function: a lambda is
    # not hashable, so a callable crossing that boundary would either raise or
    # be keyed by object identity, which caches the wrong answer. Two floats key
    # the cache correctly and cannot go stale when the visitor edits either.
    baseline_curve = (
        returning_student_curve(baseline_salary_now, baseline_salary_in_10y)
        if baseline_salary_now is not None and baseline_salary_in_10y is not None
        else None)

    roi_result = calculate_roi(major_name, repayment_result["total_paid_in_roi_window"],
                                total_investment, col_index=col_index, years=roi_window_years,
                                hs_wage_index=hs_wage_index,
                                baseline_start_age=baseline_start_age,
                                personal_contribution=personal_contribution,
                                enrollment_years=enrollment_years,
                                working_years=working_years,
                                baseline_curve=baseline_curve)

    # The other half of that split: what the visitor actually pays each month,
    # and when they are actually free, DOES include the existing balance --
    # that burden is the whole subject. Amortised separately rather than blended
    # into one rate: split_loan_financing's blend is for two tranches of the
    # same new loan taken and repaid together, whereas an existing balance is
    # partly repaid and carries its own rate, so blending would misstate both
    # the payment and the payoff date.
    existing_result = None
    if existing_debt and existing_debt > 0:
        existing_rate = existing_debt_rate if existing_debt_rate is not None else interest_rate
        if repayment_strategy == TIERED_STANDARD_STRATEGY_LABEL:
            existing_result = calculate_standard_repayment(
                existing_debt, existing_rate,
                calculate_tiered_standard_term(existing_debt),
                roi_window_years=roi_window_years)
        elif repayment_strategy == STANDARD_STRATEGY_LABEL:
            existing_result = calculate_standard_repayment(
                existing_debt, existing_rate, roi_window_years=roi_window_years)
        elif repayment_strategy == RAP_STRATEGY_LABEL:
            existing_result = simulate_rap_schedule(
                existing_debt, existing_rate, major_name, dependents,
                roi_window_years=roi_window_years)
        else:
            existing_result = calculate_idr_repayment(
                existing_debt, existing_rate, major_name, roi_window_years=roi_window_years)

    # Same combiner as the forgivable/non-forgivable split above -- it also
    # takes the LATER payoff, which is what returning-student mode reports, and
    # merges the schedules so the balance chart shows the whole debt rather
    # than only the new loan.
    combined_repayment = combine_repayment_results(repayment_result, existing_result)

    return {
        "major": major_name,
        "strategy_label": strategy_label,
        "effective_principal": effective_principal,
        "personal_contribution": personal_contribution,
        "total_investment": total_investment,
        # Stamp the enrollment-cost assumptions onto the scenario so every
        # re-derivation off this dict (break-even, the net-position chart,
        # the PDF)
        # reuses the exact values it was computed under, rather than each call
        # site re-reading the toggle and risking a mismatch -- the same class
        # of bug the hs_wage_index threading fixed.
        "enrollment_years": enrollment_years,
        "working_years": working_years,
        # Stamped for the same reason the two above are: the results page and
        # the PDF both describe this scenario's schooling, and re-deriving the
        # education level at each call site would let them disagree about
        # whether graduate study is in play.
        "typical_education": MAJOR_DATA.get(major_name, {}).get("typical_education") or "",
        # None when the age-aware baseline is off. Stamped for the same reason
        # the two above are: every re-derivation off this dict (the break-even,
        # the net-position chart, the PDF) must reuse the value the scenario
        # was actually computed under.
        "baseline_start_age": baseline_start_age,
        # repayment_result stays NEW borrowing only -- every existing consumer,
        # including the ROI above and the break-even, depends on that meaning.
        # combined_repayment is what the visitor is shown; it EQUALS
        # repayment_result when there is no existing debt, so display code needs
        # no conditional and cannot accidentally show the wrong one.
        "repayment_result": repayment_result,
        "existing_debt": existing_debt or 0.0,
        "existing_debt_result": existing_result,
        "combined_repayment": combined_repayment,
        "baseline_curve_used": baseline_curve is not None,
        "roi_result": roi_result,
        # None unless the cap-and-gap split was applied (Detailed mode); the
        # results page / PDF show the federal-vs-gap breakdown when it's set.
        "financing": financing,
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
                         working_years: int = 0,
                         baseline_start_age: int = None,
                         federal_cap: float = None, plus_cap: float = None, gap_rate: float = None, dependents: int = 0,
                         professional_debt: float = None,
                         include_fees: bool = False,
                         baseline_salary_now: float = None,
                         baseline_salary_in_10y: float = None) -> dict:
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
    city, and with it the state/metro wage overlays (a Software Developer
    earns differently in Austin than in New York). Without this parameter the
    cache would happily serve one city's break-even to someone who just
    switched to another.
    """
    def premium_at(loan: float) -> float:
        return compute_scenario_results(
            major_name, loan, interest_rate, repayment_strategy,
            personal_contribution=personal_contribution,
            col_index=col_index, roi_window_years=roi_window_years,
            hs_wage_index=hs_wage_index,
            enrollment_years=enrollment_years,
            working_years=working_years,
            baseline_start_age=baseline_start_age,
            federal_cap=federal_cap, plus_cap=plus_cap, gap_rate=gap_rate, dependents=dependents, professional_debt=professional_debt, include_fees=include_fees,
            # The same baseline the ROI used. Without this the break-even would
            # be solved against the high-school-graduate curve while the premium
            # beside it used the visitor's own salary -- two numbers on one
            # screen quietly answering different questions.
            baseline_salary_now=baseline_salary_now,
            baseline_salary_in_10y=baseline_salary_in_10y,
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
                       working_years: int = 0,
                       baseline_start_age: int = None,
                       federal_cap: float = None, plus_cap: float = None, gap_rate: float = None, dependents: int = 0,
                         professional_debt: float = None,
                       include_fees: bool = False,
                       baseline_salary_now: float = None,
                       baseline_salary_in_10y: float = None) -> dict:
    """find_breakeven_loan framed against what this visitor is actually
    borrowing, shared by the on-screen section and its PDF counterpart so
    the two can't drift.

    Returns None for `headline` when the break-even shouldn't be shown at
    all: "this degree stops paying off at $X" is malformed when the model
    charged four financed years to reach a job that never asked for them.

    That gate is MISMODELLED_EDUCATION_LEVELS, not every sub-baccalaureate
    level. An associate's degree now costs the two years it actually takes
    (PROGRAM_YEARS_BY_EDUCATION), so its break-even is a real number about a
    real program and is shown. The levels still charged four wrong years are
    the ones with no defensible standard length -- those stay suppressed.
    """
    _cf = counterfactual_vocab()
    typical_education = MAJOR_DATA.get(major_name, {}).get("typical_education", "")
    # Two different reasons to stay silent, both ending in the same place.
    # MISMODELLED: we're charging a length we don't believe, so the number
    # would be built on a wrong premise. Zero program years: there is no degree
    # to weigh, so "this degree stops paying off at $X" has no referent at all
    # -- a career you can enter with a diploma doesn't have a debt ceiling.
    if (typical_education in MISMODELLED_EDUCATION_LEVELS
            or program_years_for_education(typical_education) == 0):
        return {"headline": None, "detail": None, "status": "not_applicable"}

    result = find_breakeven_loan(major_name, interest_rate, repayment_strategy,
                                  roi_window_years, col_index,
                                  career_data_source=career_data_source,
                                  hs_wage_index=hs_wage_index,
                                  personal_contribution=personal_contribution,
                                  enrollment_years=enrollment_years,
                                  working_years=working_years,
                                  baseline_start_age=baseline_start_age,
                                  federal_cap=federal_cap, plus_cap=plus_cap, gap_rate=gap_rate, dependents=dependents, professional_debt=professional_debt,
                                  include_fees=include_fees,
                                  baseline_salary_now=baseline_salary_now,
                                  baseline_salary_in_10y=baseline_salary_in_10y)
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
                f"Over {years} years, this path earns less than {_cf['baseline_noun']} "
                f"does — even with no loan at all. Borrowing less doesn't change "
                f"that; only a longer horizon or a different path would."
            ),
            "status": "never", "breakeven_loan": None, "headroom": None,
            "positive": False, "label": "Worth a rethink",
        }
    if result["status"] == "beyond_search_max":
        return {
            "headline": "Yes — at any realistic loan amount",
            "detail": (
                f"Over {years} years this path stays ahead of {_cf['baseline_noun']} "
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
                f"For {major_name}, this comes out well ahead of {_cf['baseline_noun']} "
                f"over {years} years — it earns back more than the loan costs you. "
                f"It would take {fmt_money(breakeven)} of loans, about {multiple:.0f}× what "
                f"you're borrowing, before that stopped being true."
            )
        elif multiple is not None and multiple >= 1.5:
            headline = f"Yes — worth your {fmt_money(loan_amount)} loan"
            detail = (
                f"For {major_name}, this comes out ahead of {_cf['baseline_noun']} "
                f"over {years} years — it earns back more than the loan costs you. It would take "
                f"{fmt_money(breakeven)} of loans, about half again what you're borrowing, before "
                f"that stopped being true."
            )
        else:
            headline = f"Yes, but only just — worth your {fmt_money(loan_amount)} loan"
            detail = (
                f"For {major_name}, this comes out ahead of {_cf['baseline_noun']} "
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
            f"For {major_name}, this falls behind {_cf['baseline_noun']} over "
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
    schedule = repayment_result["schedule"]
    # Standard is flat, and so is anything whose schedule carries no per-month
    # payment column. Testing for the column rather than for the strategy NAME
    # is what lets a new income-driven plan be added without this silently
    # reading a column that isn't there.
    if strategy in (STANDARD_STRATEGY_LABEL, TIERED_STANDARD_STRATEGY_LABEL) \
            or "payment" not in schedule.columns:
        return repayment_result.get("monthly_payment", 0.0)
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

# The share of workers falling between consecutive published percentiles.
# These are definitional, not estimates: if p25 is the 25th percentile and p50
# the 50th, then exactly 25% of workers earn between them. This is the whole
# reason a distribution is derivable from OEWS at all.
WAGE_DISTRIBUTION_BINS = [
    ("p10", "p25", 0.15),
    ("p25", "p50", 0.25),
    ("p50", "p75", 0.25),
    ("p75", "p90", 0.15),
]

# The two open-ended tails. Each holds a known share of workers but has no
# published bound on the far side, so neither can be drawn as a bar -- a bar
# needs a width, and any width chosen for these would be invented. They're
# annotated in words instead.
WAGE_DISTRIBUTION_TAIL_SHARE = 0.10


def build_wage_distribution(percentiles: dict) -> list:
    """Turn OEWS's five published percentiles into area-truthful histogram
    bins: [{low, high, share, density}], ordered low to high.

    OEWS publishes percentiles and a mean, never microdata, so the frequency
    counts a histogram normally needs simply don't exist -- and no amount of
    processing conjures them. What the percentiles *do* pin down exactly is
    how many workers sit between any two of them. Turning that into a bar
    means dividing each bin's share of workers by its dollar width, so the
    bar's AREA is the share and its HEIGHT is a density. Plotting share as
    height instead would be wrong in a way that reads as right: the bins have
    unequal widths, so equal-height bars would imply a $11k-wide range and a
    $25k-wide range hold the same number of people per dollar when the wider
    one is less than half as dense.

    That distinction is the point of the chart. A career whose middle bins are
    tall and narrow pays most people close to the median; one whose bins are
    low and wide pays the same median to a much more scattered workforce, and
    the median alone can't tell those apart.

    Returns [] if any percentile is missing or the sequence isn't strictly
    increasing -- a non-monotonic set means a suppressed or malformed row, and
    a bin of zero or negative width would divide by zero or draw backwards.
    """
    if not percentiles:
        return []
    ordered = ["p10", "p25", "p50", "p75", "p90"]
    try:
        values = [float(percentiles[key]) for key in ordered]
    except (KeyError, TypeError, ValueError):
        return []
    # NaN before monotonicity: every comparison against NaN is False, so a
    # suppressed percentile would sail through the ordering check below and
    # produce a bar of NaN width -- a broken chart rather than no chart.
    if any(pd.isna(value) for value in values):
        return []
    if any(b <= a for a, b in zip(values, values[1:])):
        return []

    bins = []
    for low_key, high_key, share in WAGE_DISTRIBUTION_BINS:
        low, high = float(percentiles[low_key]), float(percentiles[high_key])
        bins.append({
            "low": low, "high": high, "share": share,
            "density": share / (high - low),
        })
    return bins


def balance_split_is_informative(schedule_df: pd.DataFrame) -> bool:
    """Whether splitting the balance into principal and unpaid interest says
    anything.

    It does NOT for most plans. Under Standard, Extended or Tiered Standard
    every payment covers that month's interest, so the unpaid-interest pool is
    always zero and a stacked chart would be one solid colour -- strictly worse
    than the line it replaced. Under RAP the unpaid part is waived rather than
    accrued, so the same applies.

    It says a great deal under an income-driven plan whose payment falls short:
    principal sits still while the interest pool balloons. On $190,000 at 6.5%
    for a $38,000 earner, principal stays at $190,000 for nineteen years while
    unpaid interest grows to $366,046 -- a balance that nearly triples with
    every dollar of the growth being interest. A single line shows only that it
    grew.
    """
    if not {"principal_balance", "interest_balance"} <= set(schedule_df.columns):
        return False
    interest = schedule_df["interest_balance"]
    if interest.max() <= 0:
        return False
    # A trivial sliver -- one month's accrual before the first payment lands --
    # is not worth a second colour and a legend.
    return interest.max() > 0.02 * max(schedule_df["balance"].max(), 1.0)


def build_balance_chart(schedule_df: pd.DataFrame, strategy_label: str):
    if balance_split_is_informative(schedule_df):
        stacked = schedule_df.melt(
            id_vars="year", value_vars=["principal_balance", "interest_balance"],
            var_name="component", value_name="amount")
        stacked["component"] = stacked["component"].map(
            {"principal_balance": "Principal", "interest_balance": "Unpaid interest"})
        fig = px.area(
            stacked, x="year", y="amount", color="component",
            title="Loan Balance Over Time — principal vs unpaid interest",
            labels={"year": "Years", "amount": "Remaining Balance ($)",
                    "component": ""},
            color_discrete_map={"Principal": "#4C78A8", "Unpaid interest": "#E45756"},
        )
        _tickvals, _ticktext = money_k_ticks(schedule_df["balance"])
        fig.update_layout(
            hovermode="x unified", title_font_size=14,
            yaxis=dict(tickmode="array", tickvals=_tickvals, ticktext=_ticktext),
        )
        return fig
    fig = px.line(
        schedule_df, x="year", y="balance",
        title="Loan Balance Over Time",
        labels={"year": "Years", "balance": "Remaining Balance ($)"},
    )
    fig.update_traces(line=dict(width=3))
    _tickvals, _ticktext = money_k_ticks(schedule_df["balance"])
    fig.update_layout(
        hovermode="x unified", title_font_size=14,
        yaxis=dict(tickmode="array", tickvals=_tickvals, ticktext=_ticktext),
    )
    return fig


def net_position_frame(scenarios: list, col_index: float, hs_wage_index: float,
                        roi_window_years: int) -> pd.DataFrame:
    """Tidy {year, Series, Net Position} frame for the net-position chart, from
    one or two (label, scenario) pairs.

    The high-school baseline is emitted once when both scenarios produce the
    same one, and twice -- labelled by scenario -- when they don't. They differ
    only when the two paths have different enrollment lengths AND foregone
    earnings is on, since the baseline is credited the years the graduate spends
    enrolled. Rare, but drawing a single line then would quietly show one
    scenario's baseline as if it were both.
    """
    series = {}
    baselines = {}
    for label, scenario in scenarios:
        points = build_net_position_series(scenario, col_index, hs_wage_index, roi_window_years)
        series[label] = [p["major"] for p in points]
        baselines[label] = [p["hs"] for p in points]

    # The legend names the baseline too, and it is the one place a returning
    # student sees it plotted rather than described -- so it comes from the
    # same vocabulary as the prose, not from a literal.
    baseline_name = counterfactual_vocab()["legend_label"]
    labels = list(baselines)
    if len(labels) > 1 and baselines[labels[0]] != baselines[labels[1]]:
        for label in labels:
            series[f"{baseline_name} ({label} timeline)"] = baselines[label]
    else:
        series[baseline_name] = baselines[labels[0]]

    rows = []
    for label, values in series.items():
        for year, value in enumerate(values, start=1):
            rows.append({"year": year, "Series": label, "Net Position": value})
    return pd.DataFrame(rows)


def build_net_position_chart(frame: pd.DataFrame, roi_window_years: int,
                              baseline_head_start_years: int = 0):
    """Net position year by year, for every path on the page plus the
    high-school baseline.

    Replaces the endpoint bar chart. A bar pair could only say who was ahead at
    year N; the shape says *when* that became true, which is the question a
    student is actually asking. It also makes the training-debt majors legible:
    Medicine spends years below zero and below the baseline before crossing,
    and a single year-10 bar reports that crossing as though it were the whole
    story.

    Takes a prebuilt frame rather than scenarios so the single-scenario and
    compare cases render through one function instead of two that can drift
    apart.
    """
    fig = px.line(
        frame, x="year", y="Net Position", color="Series", markers=True,
        # "Net position" is accounting vocabulary; the reader is 17. The
        # quantity is cumulative earnings minus loan payments, so say that.
        # The frame's COLUMN stays "Net Position" -- it is the key the PDF
        # twin and net_position_frame both read -- and only the displayed
        # name changes here.
        title="Cumulative Gross Pay minus loan payments (Tax not considered)",
        # "after graduation" rather than "after starting": with foregone
        # earnings counted, year 1 is the graduate's first working year while
        # the baseline already carries the enrolled years' wages, so the two
        # series do NOT begin level. Saying "starting" would misread that head
        # start as the degree simply being behind.
        labels={"year": "Years after graduation",
                 "Net Position": "Cumulative gross pay minus loan payments ($)"},
    )
    fig.update_traces(line=dict(width=3))
    # Zero line: the training-debt paths sit below it for years, and "below
    # zero" is a different statement from "below the baseline".
    fig.add_hline(y=0, line=dict(color="#999999", width=1, dash="dot"))
    _tickvals, _ticktext = money_k_ticks(frame["Net Position"])
    fig.update_layout(
        title_font_size=14, hovermode="x unified",
        # Explicit ticks, not yaxis_tickprefix: Plotly's own SI prefix flips to
        # "M" past a million, which put this axis in different units from the
        # loan-balance chart directly above it.
        yaxis=dict(tickmode="array", tickvals=_tickvals, ticktext=_ticktext),
        legend=dict(orientation="h", yanchor="bottom", y=-0.35, xanchor="center", x=0.5),
        margin=dict(t=80 if baseline_head_start_years else 60, b=90),
    )
    if baseline_head_start_years:
        # Without this the baseline's opening lead reads as a modelling error
        # rather than as the head start the foregone-earnings option exists to
        # represent.
        fig.add_annotation(
            x=0, y=1.10, xref="paper", yref="paper", showarrow=False,
            xanchor="left", font=dict(size=11, color="#666666"),
            text=(f"Baseline starts {baseline_head_start_years} years ahead — "
                  f"{counterfactual_vocab()['head_start']}."),
        )
    fig.update_xaxes(dtick=1 if roi_window_years <= 15 else 5)
    return fig


def cc_chart_label_suffix(cc_mode) -> str:
    """Compact community-college-path tag appended to a scenario's label in the
    side-by-side comparison charts (on screen and in the PDF), so a 2+2 transfer
    scenario is distinguishable from a straight four-year one right on the chart.
    Empty for a four-year start (cc_mode 'none' or None), matching the panels'
    render_cc_path_note, which shows nothing then too."""
    if cc_mode == "fulltime":
        return " (via comm. college)"
    if cc_mode == "associate":
        return " (comm. college only)"
    if cc_mode == "parttime":
        return " (via comm. college, working)"
    return ""


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
    _tickvals, _ticktext = money_k_ticks(combined["balance"])
    fig.update_layout(
        hovermode="x unified", title_font_size=14,
        yaxis=dict(tickmode="array", tickvals=_tickvals, ticktext=_ticktext),
        # Legend below rather than at the right, matching the net-position
        # chart. Occupation names run long ("News Analysts, Reporters, and
        # Journalists"), and a right-hand legend takes its width out of the
        # plot -- squeezing the curves this chart exists to show, and worst
        # exactly when the two labels are longest.
        legend=dict(orientation="h", yanchor="bottom", y=-0.35,
                     xanchor="center", x=0.5, title_text=""),
        margin=dict(t=60, b=90),
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
    # Dollar amount alongside the percentage on each slice, matching the
    # Loan-vs-What's-Left pie. %{value:$,.0f} is d3 currency formatting.
    fig.update_traces(
        texttemplate="%{label}<br>%{value:$,.0f} (%{percent})", automargin=True)
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
        # Show the dollar amount alongside the percentage on each slice, not
        # just percent -- "$4,631 (60%)" is more actionable than "60%" alone
        # when the whole point is how much cash the payment actually consumes.
        # %{value:$,.0f} is d3 currency formatting, matching fmt_money's style.
        fig.update_traces(
            texttemplate="%{label}<br>%{value:$,.0f} (%{percent})", automargin=True)
        fig.update_layout(showlegend=False, title_font_size=14)
        return fig
    fig = px.bar(
        x=["Take-Home Pay", "Required Student Loan Payment"],
        y=[monthly_net_take_home, monthly_payment],
        title="Monthly Student Loan Payment Exceeds Take-Home Pay",
    )
    fig.update_layout(yaxis_title="Monthly $", xaxis_title=None, title_font_size=14)
    return fig


# Curve height at each published percentile. The apex is pinned to the median
# and both ends close on the baseline, which is how O*NET draws its local-wage
# chart and what this deliberately matches.
#
# This makes the curve a STYLISED SHAPE, not a density. An earlier version
# plotted true density -- share divided by dollar width -- which peaked
# wherever workers were packed tightest, usually BELOW the median because wage
# bands widen as they rise. That was the more informative curve and it is not
# what this is. Consequences to keep straight:
#   * Height means nothing on its own. Only the marked percentiles carry
#     numbers; the shape carries position and spread.
#   * Nothing may claim the tall part is where pay clusters, or that area is
#     the share of workers. Both were true of the density version and are
#     false of this one. See the captions and the Methodology section.
WAGE_CURVE_HEIGHTS = (0.0, 0.5, 1.0, 0.5, 0.0)
WAGE_CURVE_PERCENTILES = ("p10", "p25", "p50", "p75", "p90")


def wage_ridgeline_curve(percentiles: dict, points: int = 160) -> tuple:
    """(xs, ys) tracing one occupation's pay from the 10th to the 90th
    percentile as a smooth hump peaking at the median, matching O*NET's
    local-wage chart. ys run 0..1, so every row draws at the same height and
    the comparison between rows is purely horizontal -- which is the point:
    does this geography pay more, and over how wide a range.

    Shared by the Plotly and matplotlib versions so both draw the SAME
    geometry. Each smoothing its own curve is the chart-twin drift CLAUDE.md
    warns about, and here it would put visibly different shapes on screen and
    in the PDF from identical data.

    Interpolation is a cosine smoothstep between adjacent percentiles rather
    than a linear pass followed by a moving average. The smoothstep passes
    exactly through every anchor, so the apex is exactly 1.0 at exactly the
    median; an averaging filter would round that apex down and drift it off
    the median, which is the one point this shape exists to mark. It also
    cannot overshoot, so no curve dips below its baseline.

    The curve closes at p10 and p90 because that is where published data ends,
    NOT because nobody earns beyond them -- 10% sit past each end, which
    wage_distribution_tail_notes states in words beside every chart.

    Pure Python on purpose. scipy would offer a monotone interpolant but is
    NOT in requirements.txt, and this file only gets to use what production
    installs.
    """
    # build_wage_distribution is the shared validity gate: it rejects missing,
    # NaN and non-monotonic percentile sets, which would otherwise draw a
    # backwards or broken curve.
    if not build_wage_distribution(percentiles):
        return [], []

    ax = [float(percentiles[k]) for k in WAGE_CURVE_PERCENTILES]
    ay = list(WAGE_CURVE_HEIGHTS)
    low, high = ax[0], ax[-1]
    step = (high - low) / (points - 1)
    xs = [low + step * i for i in range(points)]

    ys = []
    for x in xs:
        if x <= ax[0]:
            ys.append(ay[0]); continue
        if x >= ax[-1]:
            ys.append(ay[-1]); continue
        for i in range(len(ax) - 1):
            if ax[i] <= x <= ax[i + 1]:
                span = ax[i + 1] - ax[i]
                t = 0.0 if span == 0 else (x - ax[i]) / span
                eased = 0.5 - 0.5 * math.cos(math.pi * t)
                ys.append(ay[i] + eased * (ay[i + 1] - ay[i]))
                break
    return xs, ys


def wage_ridgeline_rows(percentiles: dict, geography_label: str,
                         national_percentiles: dict = None) -> list:
    """The rows a ridgeline draws, local first: [{label, xs, ys, percentiles}].

    Two rows when the selected city resolved to a metro or state wage, one
    when it was already the national figure -- comparing national against
    itself would draw the same curve twice and imply a difference that isn't
    there. build_major_data's overlay decides which, so this follows the data
    rather than the selected city (see get_wage_distribution_context)."""
    rows = []
    for label, pct in ((geography_label, percentiles),
                        ("United States", national_percentiles)):
        if not pct:
            continue
        xs, ys = wage_ridgeline_curve(pct)
        if xs:
            rows.append({"label": label, "xs": xs, "ys": ys, "percentiles": pct})
    return rows


def wage_distribution_tail_notes(percentiles: dict) -> tuple:
    """The two open-ended tails as plain sentences, shared by the on-screen and
    PDF versions so the wording can't drift between them."""
    return (
        f"10% earn less than {fmt_money(percentiles['p10'])}",
        f"10% earn more than {fmt_money(percentiles['p90'])}",
    )


PANEL_WAGE_LOCAL_COLOR = "#E8843C"      # local geography, warm -- reads as "yours"
PANEL_WAGE_NATIONAL_COLOR = "#4C78A8"   # national, the app's existing chart blue


def build_wage_distribution_chart(percentiles: dict, occupation_name: str,
                                   modelled_start: float = None,
                                   geography_label: str = None,
                                   national_percentiles: dict = None,
                                   row_slots: int = None):
    """Where an occupation's pay actually lands, as one filled curve per
    geography on a shared wage axis. Returns None when nothing can be built,
    so callers render nothing rather than an empty axis.

    Replaces a single-series histogram. The bars answered only "how spread out
    is this job's pay", when the question a visitor is actually holding is
    "does it pay better HERE" -- the app already picks a metro or state wage
    for them and says so in a caption, but nothing showed what that choice was
    worth. Two curves on one axis show the whole shift at once: how much
    higher, and over how much wider a range.

    Every row peaks at the same height by construction (see
    WAGE_CURVE_HEIGHTS), so the comparison between rows is purely horizontal.
    Height is not a quantity here and must not be read as one -- this shape is
    stylised to match O*NET's, with its apex on the median.

    The y-axis is unlabelled and untick-ed for that reason: there is nothing
    to label. The marked percentiles carry every number; the shape carries
    position and spread.

    modelled_start marks where the app's own starting-salary assumption sits.
    That's the honest bit: this app projects ten years from a single number,
    and showing that number's position among real wages says more about the
    projection's uncertainty than any disclaimer.
    """
    rows = wage_ridgeline_rows(percentiles, geography_label, national_percentiles)
    if not rows:
        return None

    # row_slots lets a caller reserve vertical space it isn't using, so a
    # national-only occupation lines up with a metro+national one beside it.
    # Rows are drawn bottom-up with the national row last, so an empty slot on
    # top leaves the two national curves on the same baseline rather than at
    # different heights per column.
    slots = max(row_slots or 0, len(rows))
    peak = max(max(r["ys"]) for r in rows) or 1.0
    # Each row sits on its own baseline. row_height exceeds fill_scale by
    # enough that a full-height curve still clears the baseline above it --
    # at 1.0/0.78 a tall row's peak landed level with the next row's p10
    # marker and their money labels overprinted each other.
    row_height, fill_scale = 1.25, 0.78
    colors = [PANEL_WAGE_LOCAL_COLOR, PANEL_WAGE_NATIONAL_COLOR] if len(rows) > 1 \
        else [PANEL_WAGE_NATIONAL_COLOR]

    fig = go.Figure()
    for i, row in enumerate(reversed(rows)):        # first row drawn topmost
        base = i * row_height
        color = colors[len(rows) - 1 - i]
        ys = [base + (y / peak) * fill_scale for y in row["ys"]]
        # A closed polygon with fill="toself" rather than tozeroy/tonexty:
        # both of those fill relative to the axis or the previous trace, so
        # once rows are stacked they spill outside their own band. This one
        # is self-contained and can't be affected by trace order.
        fig.add_trace(go.Scatter(
            x=row["xs"] + row["xs"][::-1],
            y=ys + [base] * len(row["xs"]),
            mode="lines", line=dict(width=0), fill="toself",
            fillcolor=_rgba(color, 0.45), hoverinfo="skip", showlegend=False,
        ))
        fig.add_trace(go.Scatter(
            x=row["xs"], y=ys, mode="lines", line=dict(color=color, width=2),
            name=row["label"], hoverinfo="skip", showlegend=False,
        ))
        fig.add_trace(go.Scatter(
            x=[row["xs"][0], row["xs"][-1]], y=[base, base], mode="lines",
            line=dict(color=color, width=1), hoverinfo="skip", showlegend=False,
        ))
        pct = row["percentiles"]
        # p10 reads left of its marker and p90 right of it, so the outer two
        # labels sit outside the silhouette instead of on top of the curve --
        # and, more to the point, out of the way of the neighbouring row's.
        marks = [("p10", "middle left"), ("p50", "top center"), ("p90", "middle right")]
        mx = [pct[k] for k, _ in marks]
        my = [base + (_density_at(row, pct[k]) / peak) * fill_scale for k, _ in marks]
        # A median line PER ROW, drawn only across that row's own band. One
        # full-height line can't work here: each geography has its own median
        # and a single line would have to pick one, or straddle both and
        # belong to neither. Two short lines also put the shift between them
        # on the page, which is the comparison the chart exists for.
        median_y = base + (_density_at(row, pct["p50"]) / peak) * fill_scale
        fig.add_trace(go.Scatter(
            x=[pct["p50"], pct["p50"]], y=[base, median_y], mode="lines",
            line=dict(color=color, width=2, dash="dot"),
            hoverinfo="skip", showlegend=False,
        ))
        fig.add_trace(go.Scatter(
            x=mx, y=my, mode="markers",
            marker=dict(color=color, size=8, line=dict(color="white", width=1.5)),
            hovertemplate="%{customdata}: %{x:$,.0f}<extra></extra>",
            customdata=["10th percentile", "median", "90th percentile"],
            showlegend=False,
        ))
        # Money labels as LAYOUT annotations, not scatter text. Plotly clips
        # trace text at the plot-area edge, which chopped "$89,980" to "i,980"
        # in Compare Mode's narrow columns however wide the margin got.
        # Annotations are drawn over the whole canvas and are never clipped.
        for (key, _), x_val, y_val, anchor, shift in zip(
                marks, mx, my, ("right", "center", "left"), (-11, 0, 11)):
            fig.add_annotation(
                x=x_val, y=y_val, xref="x", yref="y", showarrow=False,
                xanchor=anchor, yanchor="bottom" if key == "p50" else "middle",
                yshift=8 if key == "p50" else 0, xshift=shift,
                text=fmt_money(x_val), font=dict(size=11, color=color))
        fig.add_annotation(x=0, y=base + fill_scale / 2, xref="paper", yref="y",
                            xanchor="right", showarrow=False, text=f"<b>{row['label']}</b>",
                            font=dict(size=12, color=color), xshift=-8)

    if modelled_start:
        fig.add_vline(x=modelled_start, line=dict(color="#E45756", width=2, dash="dash"))
        # Sits low in the top row's band, deliberately below every median
        # label. Those sit at each curve's apex, and at equal height the two
        # read as the same kind of marker -- which they are not: the median is
        # a published BLS figure, this is the single number the app's own
        # ten-year projection starts from.
        fig.add_annotation(x=modelled_start, y=(len(rows) - 1) * row_height + 0.05, yref="y",
                            showarrow=False, yanchor="bottom",
                            text=f"Starting salary {fmt_money(modelled_start)}",
                            font=dict(size=11, color="#E45756"),
                            # Sitting low in the band puts this on top of the
                            # fill, where red on orange is hard to read.
                            bgcolor="rgba(255,255,255,0.78)", borderpad=2)

    below, above = wage_distribution_tail_notes(percentiles)
    where = rows[0]["label"]
    _x_lo = min(r["xs"][0] for r in rows)
    _x_hi = max(r["xs"][-1] for r in rows)
    _x_pad = (_x_hi - _x_lo) * 0.13
    fig.update_layout(
        title=f"Where {occupation_name} pay actually lands — {where}"
               + (" vs the U.S." if len(rows) > 1 else ""),
        title_font_size=14,
        # Pad the x-range rather than relying on margins alone. The p10/p90
        # money labels are anchored outside their markers, and Plotly clips
        # scatter text at the PLOT AREA edge, not the canvas edge -- so a
        # wider margin moves the axis inward without stopping "$89,980" from
        # being chopped to "i,980". Padding the domain puts the labels inside.
        xaxis=dict(title="Annual wage", tickprefix="$", tickformat=",",
                    range=[_x_lo - _x_pad, _x_hi + _x_pad]),
        yaxis=dict(title=None, showticklabels=False, showgrid=False, zeroline=False,
                    range=[-0.12, (slots - 1) * row_height + fill_scale + 0.34]),
        height=200 + 130 * slots,
        # Left margin holds the row labels; bottom clears the x-title AND the
        # tail note under it, which is clipped out of the plot at the default.
        margin=dict(t=70, b=110, l=130, r=70),
        annotations=list(fig.layout.annotations) + [dict(
            x=0, y=-0.34, xref="paper", yref="paper", showarrow=False,
            xanchor="left", yanchor="top", font=dict(size=11, color="#666666"),
            text=f"◄ {below}   ·   {above} ►   (for {where})",
        )],
    )
    return fig


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def _density_at(row: dict, x: float) -> float:
    """The drawn curve's height at a wage, so a marker sits ON the silhouette.
    Named for the density curve this used to sample; the shape is now stylised
    (WAGE_CURVE_HEIGHTS) and the returned number is a drawing coordinate with
    no units, useful only for placing a marker or a median line."""
    xs, ys = row["xs"], row["ys"]
    if x <= xs[0] or x >= xs[-1]:
        return 0.0
    for i in range(len(xs) - 1):
        if xs[i] <= x <= xs[i + 1]:
            span = xs[i + 1] - xs[i]
            t = 0.0 if span == 0 else (x - xs[i]) / span
            return ys[i] + t * (ys[i + 1] - ys[i])
    return 0.0


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


def _pdf_major_careers_section(underemployment_majors: list, styles: dict) -> list:
    """PDF flowables for "Careers this major commonly leads to" -- the report
    twin of the on-screen render_major_careers. Major mode only: it reuses the
    (label, major) pairs the sources section already builds, which are None in
    Career mode. One line per distinct major that maps to a labelled SOC group
    with example occupations; returns [] when nothing qualifies (e.g. only
    unmapped majors like Interdisciplinary Studies), so the section simply
    doesn't appear -- matching the on-screen behaviour. Kept in sync with
    render_major_careers per the two-parallel-implementations rule."""
    if not underemployment_majors:
        return []
    multi = len({m for _, m in underemployment_majors}) > 1
    seen, lines = set(), []
    for label, mjr in underemployment_majors:
        if mjr in seen:
            continue
        seen.add(mjr)
        group = MAJOR_DATA.get(mjr, {}).get("soc_major_group")
        group_label = (AI_EXPOSURE_BY_SOC_GROUP.get(group) or {}).get("label") if group else None
        examples = careers_for_major(group, CAREERS_CSV_PATH_NATIONAL) if group_label else []
        if not examples:
            continue
        listing = ", ".join(f"{xml_escape(title)} ({fmt_money(median)})" for title, median in examples)
        prefix = f"<b>{xml_escape(label)}:</b> " if label and multi else ""
        lines.append(Paragraph(prefix + listing, styles["body"]))
    if not lines:
        return []
    return [
        Spacer(1, 10),
        Paragraph("Careers this major commonly leads to", styles["section"]),
        Paragraph(
            "Example occupations in the field each major most commonly leads to, at national "
            "median pay (U.S. Bureau of Labor Statistics) — a representative sample, not an "
            "exhaustive or guaranteed list.",
            styles["caption"],
        ),
        Spacer(1, 4),
        *lines,
    ]


def _pdf_sources_section(styles: dict, roi_window_years: int, uses_training_debt: bool = False,
                          underemployment_majors: list = None,
                          uses_community_college: bool = False) -> list:
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
        ["Wage distribution chart",
         "The same OEWS release, which publishes five wage percentiles (10th, 25th, 50th, 75th, "
         "90th) per occupation and no individual records. The curve is drawn through those five "
         "points, peaking at the median, in the style O*NET uses; its height is illustrative "
         "rather than a count of workers. Where your city has its own published figures, the "
         "national curve is drawn beneath it. The bottom and top 10% have no published bound and "
         "are stated in words rather than drawn."],
        ["High school graduate baseline",
         "U.S. Bureau of Labor Statistics, Current Population Survey — median usual weekly earnings "
         "for full-time workers age 25+ with a high school diploma and no college ($994/week, "
         "2026 Q2, series LEU0252917300), "
         "annualised. This is an all-ages median, not a young graduate's starting pay, so the "
         "comparison is against a typical working adult without a degree — the more demanding test. "
         "Wage growth of 2%/yr is an assumption, not a BLS figure."],
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
    if uses_community_college:
        rows.append([
            "Community-college costs",
            "Average in-district tuition & fees by state, National Center for Education Statistics "
            "(NCES) via the Education Data Initiative (2025). Community-college years are modelled as "
            "paid without loans; the resulting degree is treated as identical to one earned by "
            "starting at the 4-year school.",
        ])
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

    # PDF twin of the on-screen "Careers this major commonly leads to" section
    # (render_major_careers). Major mode only -- built from the same
    # underemployment_majors list, which is None in Career mode. Keep in sync
    # with render_major_careers, per the two-parallel-chart-implementations rule.
    careers_flowables = _pdf_major_careers_section(underemployment_majors, styles)

    return [
        PageBreak(),
        Paragraph("What these numbers assume", styles["section"]),
        *disclosure_paras,
        *careers_flowables,
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
# Thousands variant for money AXES, matching the on-screen charts. The plain
# formatter above stays for the wage axis, where the values are salaries a
# reader wants in full dollars.
_PDF_MONEY_K_FORMATTER = mticker.FuncFormatter(lambda value, _pos: fmt_money_k(value))


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


def build_pdf_wage_distribution_chart(percentiles: dict, occupation_name: str,
                                       modelled_start: float = None,
                                       geography_label: str = None,
                                       national_percentiles: dict = None,
                                       row_slots: int = None,
                                       max_width: float = PDF_CONTENT_WIDTH) -> Image:
    """PDF counterpart to build_wage_distribution_chart. Returns None when
    nothing can be built, matching its on-screen twin so the caller's "skip
    it" branch is the same on both surfaces.

    Both take their geometry from wage_ridgeline_rows, so the curve shapes
    cannot drift -- the smoothing happens once, in shared code, rather than
    twice. What stays hand-kept in sync is the annotation wording and which
    reference lines are drawn (see CLAUDE.md on the chart twins).
    """
    rows = wage_ridgeline_rows(percentiles, geography_label, national_percentiles)
    if not rows:
        return None

    # Mirrors the Plotly twin: reserved slots, not drawn rows, so the two
    # renderers size a chart identically for the same inputs.
    slots = max(row_slots or 0, len(rows))
    peak = max(max(r["ys"]) for r in rows) or 1.0
    row_height, fill_scale = 1.25, 0.78
    colors = ([PANEL_WAGE_LOCAL_COLOR, PANEL_WAGE_NATIONAL_COLOR] if len(rows) > 1
              else [PANEL_WAGE_NATIONAL_COLOR])

    fig, ax = plt.subplots(figsize=(6, 2.1 + 1.15 * slots))
    for i, row in enumerate(reversed(rows)):
        base = i * row_height
        color = colors[len(rows) - 1 - i]
        ys = [base + (y / peak) * fill_scale for y in row["ys"]]
        ax.fill_between(row["xs"], base, ys, color=color, alpha=0.45, linewidth=0)
        ax.plot(row["xs"], ys, color=color, linewidth=1.6)
        ax.plot([row["xs"][0], row["xs"][-1]], [base, base], color=color, linewidth=0.8)
        pct = row["percentiles"]
        # Median line across this row's own band only -- see the Plotly twin
        # for why a single full-height line can't serve two geographies.
        median_y = base + (_density_at(row, pct["p50"]) / peak) * fill_scale
        ax.plot([pct["p50"], pct["p50"]], [base, median_y], color=color,
                 linewidth=1.6, linestyle=":")
        # p10 left of its marker, p90 right of it, median above: the outer two
        # sit outside the silhouette and clear of the neighbouring row.
        for key, ha, va, dx, dy in (("p10", "right", "center", -6, 0),
                                     ("p50", "center", "bottom", 0, 4),
                                     ("p90", "left", "center", 6, 0)):
            x = pct[key]
            y = base + (_density_at(row, x) / peak) * fill_scale
            ax.plot([x], [y], marker="o", markersize=5, color=color,
                     markeredgecolor="white", markeredgewidth=1.2)
            # parse_math=False: matplotlib reads a matched pair of "$" as a
            # mathtext expression, so two money labels in one string raise.
            ax.annotate(fmt_money(x), xy=(x, y), xytext=(dx, dy),
                         textcoords="offset points", ha=ha, va=va,
                         fontsize=7.5, color=color, parse_math=False)
        ax.annotate(row["label"], xy=(0, base + fill_scale / 2),
                     xycoords=("axes fraction", "data"), xytext=(-8, 0),
                     textcoords="offset points", ha="right", va="center",
                     fontsize=8.5, fontweight="bold", color=color,
                     annotation_clip=False, parse_math=False)

    if modelled_start:
        ax.axvline(modelled_start, color="#E45756", linewidth=1.6, linestyle="--")
        # Same placement reasoning as the Plotly twin: low in the top row's
        # band, below every median label.
        ax.annotate(f"Starting salary {fmt_money(modelled_start)}",
                     xy=(modelled_start, (len(rows) - 1) * row_height + 0.05), ha="center",
                     va="bottom", fontsize=7.5, color="#E45756", parse_math=False,
                     # Same reason as the Plotly twin: the label now overlaps
                     # the fill it used to sit above.
                     bbox=dict(facecolor="white", edgecolor="none", alpha=0.78, pad=1.4))

    where = rows[0]["label"]
    title = f"Where {occupation_name} pay actually lands - {where}"
    if len(rows) > 1:
        title += " vs the U.S."
    ax.set_title(title, fontsize=10.5, pad=16)
    ax.set_xlabel("Annual wage")
    ax.xaxis.set_major_formatter(_PDF_MONEY_FORMATTER)
    # Cap the tick count: at print width the default locator packs in enough
    # "$110,000"-length labels to run them into each other.
    ax.xaxis.set_major_locator(mticker.MaxNLocator(nbins=5, prune="both"))
    ax.set_ylim(-0.12, (slots - 1) * row_height + fill_scale + 0.34)
    # Same padding reasoning as the Plotly twin: the outer money labels sit
    # beyond their markers and would otherwise be trimmed at the axes edge.
    _x_lo = min(r["xs"][0] for r in rows)
    _x_hi = max(r["xs"][-1] for r in rows)
    _x_pad = (_x_hi - _x_lo) * 0.13
    ax.set_xlim(_x_lo - _x_pad, _x_hi + _x_pad)
    # Same reasoning as the Plotly twin: every row peaks at the same height by
    # construction, so there is no quantity here worth printing.
    ax.set_yticks([])
    ax.spines[["left", "right", "top"]].set_visible(False)

    below, above = wage_distribution_tail_notes(percentiles)
    ax.annotate(f"{below}  -  {above}  (for {where})", xy=(0, -0.30),
                 xycoords="axes fraction", fontsize=8, color="#666666",
                 annotation_clip=False, parse_math=False)
    return _pdf_image_from_figure(fig, max_width=max_width)


def _pdf_wage_distribution_block(occupation_name: str, styles: dict,
                                  scenario_label: str = None,
                                  max_width: float = PDF_CONTENT_WIDTH) -> list:
    """The wage-distribution chart plus its caption, for a report. Shared by
    both generators for the same reason _pdf_breakeven_block is.

    Returns [] whenever the on-screen render_wage_distribution would also show
    nothing (Major mode, or an occupation with no published distribution), so
    the report never gains or loses a section relative to the page the visitor
    actually saw.

    Recomputed here from MAJOR_DATA rather than carried in module_context:
    that dict is spread straight into Supabase inserts and must stay
    JSON-scalar-only, so a percentile dict has no business in it (see
    CLAUDE.md).
    """
    context = get_wage_distribution_context(occupation_name)
    if not context:
        return []
    chart = build_pdf_wage_distribution_chart(**context, max_width=max_width)
    if chart is None:
        return []

    heading = "What this job actually pays"
    if scenario_label:
        heading = f"Scenario {scenario_label} -- {heading}"
    return [
        Spacer(1, 12),
        Paragraph(heading, styles["section"]),
        chart,
        Paragraph(
            "The median is the midpoint -- half of these workers earn less, half earn "
            "more. The curve shows the range around it: it runs from what the "
            "lowest-paid 10% earn up to what the top 10% earn. Source: BLS OEWS "
            "published wage percentiles.",
            styles["caption"]),
    ]


def build_pdf_balance_chart(schedule_df: pd.DataFrame, strategy_label: str) -> Image:
    """PDF counterpart to build_balance_chart -- simplified redraw for
    print, not required to be pixel-identical to the on-screen interactive
    version."""
    fig, ax = plt.subplots(figsize=(6, 3.5))
    ax.plot(schedule_df["year"], schedule_df["balance"], linewidth=2.5)
    ax.set_title("Loan Balance Over Time")
    ax.set_xlabel("Years")
    ax.set_ylabel("Remaining Balance ($)")
    ax.yaxis.set_major_formatter(_PDF_MONEY_K_FORMATTER)
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
    ax.yaxis.set_major_formatter(_PDF_MONEY_K_FORMATTER)
    ax.grid(True, alpha=0.3)
    ax.legend()
    return _pdf_image_from_figure(fig)


def build_pdf_net_position_chart(frame: pd.DataFrame, roi_window_years: int) -> Image:
    """PDF counterpart to build_net_position_chart. Takes the same prebuilt
    frame, so the two can't disagree about the trajectory -- what is hand-kept
    in sync is the styling and the zero line (see CLAUDE.md on the chart
    twins)."""
    fig, ax = plt.subplots(figsize=(6, 3.5))
    for label, group in frame.groupby("Series", sort=False):
        ax.plot(group["year"], group["Net Position"], marker="o", markersize=3,
                linewidth=2, label=label)
    ax.axhline(0, color="#999999", linewidth=1, linestyle=":")
    ax.set_title("Cumulative Gross Pay minus loan payments (Tax not considered)",
                  fontsize=11)
    ax.set_xlabel("Years after graduation")
    ax.set_ylabel("Cumulative gross pay minus loan payments ($)")
    ax.yaxis.set_major_formatter(_PDF_MONEY_K_FORMATTER)
    ax.grid(True, alpha=0.3)
    legend = ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.22),
                       ncol=2, frameon=False, fontsize=8)
    for text in legend.get_texts():
        text.set_parse_math(False)
    return _pdf_image_from_figure(fig)


def build_pdf_takehome_pie_chart(take_home: dict, max_width: float = PDF_CONTENT_WIDTH) -> Image:
    """PDF counterpart to build_takehome_pie_chart. max_width lets the caller
    render it at half width so the two take-home charts sit side by side on one
    page (a full-width pair is taller than a page and would split)."""
    fig, ax = plt.subplots(figsize=(4.5, 4.0))
    labels = ["Take-Home Pay", "Federal Tax", "State Tax", "FICA"]
    values = [take_home["net_take_home"], take_home["federal_tax"],
              take_home["state_tax"], take_home["fica_tax"]]
    # Dollar amount alongside the percentage, in sync with the on-screen
    # build_takehome_pie_chart. autopct only gets the percentage, so recover
    # each slice's dollars from it via the total (sum of all four slices).
    _pie_total = sum(values)
    ax.pie(
        values, labels=labels, startangle=90,
        autopct=lambda pct: f"${pct / 100 * _pie_total:,.0f}\n({pct:.0f}%)",
    )
    ax.set_title("Where Your Salary Actually Goes")
    return _pdf_image_from_figure(fig, max_width=max_width)


def build_pdf_takehome_vs_loan_chart(monthly_net_take_home: float, monthly_payment: float,
                                      max_width: float = PDF_CONTENT_WIDTH) -> Image:
    """PDF counterpart to build_takehome_vs_loan_chart -- same pie-or-bar-
    fallback branch condition (a pie can't represent a payment that
    exceeds take-home pay)."""
    fig, ax = plt.subplots(figsize=(4.5, 4.0))
    if monthly_payment <= monthly_net_take_home:
        remaining = monthly_net_take_home - monthly_payment
        # Kept in sync with the on-screen build_takehome_vs_loan_chart, which
        # now labels each slice with its dollar amount as well as the percent.
        # matplotlib's autopct only gets the percentage, so recover the dollar
        # value from it via the slice total (payment + remaining).
        _pie_total = monthly_payment + remaining
        ax.pie(
            [monthly_payment, remaining],
            labels=["Student Loan Payment", "What's Left to Spend"],
            autopct=lambda pct: f"${pct / 100 * _pie_total:,.0f}\n({pct:.0f}%)",
            startangle=90,
        )
        ax.set_title("Your Monthly Take-Home Pay: Loan vs. What's Left")
    else:
        ax.bar(["Take-Home Pay", "Required Student Loan Payment"],
               [monthly_net_take_home, monthly_payment], color=["#636EFA", "#EF553B"])
        ax.set_title("Monthly Student Loan Payment Exceeds Take-Home Pay")
        ax.set_ylabel("Monthly $")
        ax.yaxis.set_major_formatter(_PDF_MONEY_FORMATTER)
    return _pdf_image_from_figure(fig, max_width=max_width)


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
    if cc_mode not in CC_PATH_MODES:
        return None
    state_label = ("National average" if cc_state_key == "__national__"
                   else US_STATES.get(cc_state_key, "National average"))
    return {"mode": cc_mode, "state_label": state_label,
            "cost": cost_per_year, "oop": oop, "cc_years": cc_years}


def _pdf_profile_rows(major_name, school_name, in_state, coa_per_year,
                       personal_contribution_per_year, grants_per_year,
                       interest_rate_pct, repayment_strategy_label,
                       city_name=None, start_year=None,
                       cc_info=None, loan_source: str = "personal",
                       professional_school=None, professional_debt=None,
                       typical_education: str = None) -> list:
    rows = [
        ["Profession", major_name],
        ["School", school_name or "(not entered)"],
        ["In-State", "Yes" if in_state else "No"],
    ]
    # The professional school is a second, separate school -- the "School" row
    # above is the undergraduate one. Naming only that would let a report show
    # UC Berkeley beside $99,160 of Harvard medical school debt with nothing
    # saying so.
    _grad_extra = graduate_years_for_education(typical_education or "")
    if _grad_extra:
        _level = ("Master's" if (typical_education or "") == CREDENTIAL_MASTERS
                  else "Doctorate")
        rows.append(["Degree level",
                     f"{_level} — {_grad_extra} years beyond a bachelor's"])
    _prof_program = professional_program_for(major_name)
    if _prof_program and professional_debt:
        _label = PROFESSIONAL_SCHOOL_LABEL[_prof_program]
        if professional_school and professional_school != PROFESSIONAL_SCHOOL_NATIONAL:
            rows.append([_label, f"{professional_school} — {fmt_money(professional_debt)} median debt"])
        else:
            rows.append([_label, f"National average — {fmt_money(professional_debt)}"])
    if city_name is not None:
        rows.append(["City / Metro Area", city_name])
    if start_year is not None:
        rows.append(["Year Starting Undergraduate School", str(start_year)])
    # Community-college path disclosure: without these rows the report shows a
    # single 4-year Cost of Attendance and a reduced loan with no explanation of
    # where the reduction came from. Only added when a CC path is active.
    if cc_info and cc_info.get("mode") in CC_PATH_MODES:
        _mode_label = {
            "fulltime": "Full-time, then transfer",
            "associate": "Full-time, entire degree — no transfer",
            "parttime": "Part-time while working, then transfer",
        }[cc_info["mode"]]
        rows.append([f"Community College Path ({cc_info['cc_years']} yrs)", _mode_label])
        rows.append(["Community College",
                     f"{cc_info['state_label']} — {fmt_money(cc_info['cost'])}/yr, paid out of "
                     f"pocket ({fmt_money(cc_info['oop'])} total, no loan)"])
    # The 4-year-school qualifier only makes sense when there IS a transfer --
    # an associate-only path never reaches one, and its COA row describes the
    # school that would have been used had the visitor not chosen this path.
    _coa_label = ("Cost of Attendance (per year, 4-year school)"
                  if cc_info and cc_info.get("mode") in ("fulltime", "parttime")
                  else "Cost of Attendance (per year)")
    rows.append([_coa_label, fmt_money(coa_per_year)])
    # In Simplified mode (loan_source == "college") the loan is the school's
    # reported median debt, so Personal Contribution and Grants don't feed it --
    # they're hidden in the sidebar and would only show stale/zero values here,
    # so leave them out of the report too.
    if loan_source != "college":
        rows.append(["Personal Contribution (per year)", fmt_money(personal_contribution_per_year)])
        rows.append(["Grants & Scholarships (per year)", fmt_money(grants_per_year)])
    rows += [
        ["Federal Direct rate", fmt_pct(interest_rate_pct)],
        ["Repayment Strategy", repayment_strategy_label],
    ]
    return rows


def _pdf_module_sections(module_context: dict, scenario_a: dict = None, major_name_a: str = None,
                          interest_rate_a: float = None, scenario_b: dict = None, major_name_b: str = None,
                          interest_rate_b: float = None, col_index: float = 100.0,
                          hs_wage_index: float = 1.0,
                          key_suffix_a: str = "a", key_suffix_b: str = "b",
                          roi_window_years: int = ROI_WINDOW_YEARS) -> list:
    """Optional PDF section(s) for whichever advanced modules were active --
    guarded per-module (see build_module_context) so a PDF generated with
    every module off is unchanged from before these modules existed.
    scenario_a/b, major_name_a/b, interest_rate_a/b, col_index, and
    key_suffix_a/b are only used to redraw the 2026-forecasting module's
    chart images (recomputed here, never
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
            PageBreak(), Paragraph("College Prestige & Cost Estimator", styles["section"]),
            _pdf_table(rows),
        ]
    if module_context.get("ai_mode_active"):
        rows = [["Scenario", "AI Task Exposure Risk Level"], ["A", module_context.get("scenario_a_ai_risk_level", "")]]
        if "scenario_b_ai_risk_level" in module_context:
            rows.append(["B", module_context["scenario_b_ai_risk_level"]])
        elements += [
            PageBreak(), Paragraph("AI Employability Risk Analysis", styles["section"]),
            _pdf_table(rows),
        ]
    if module_context.get("future_forecasting_active"):
        elements += [
            PageBreak(), Paragraph("2026 Federal Repayment Plans (RAP & Tiered Standard)", styles["section"]),
        ]
        if scenario_a is not None:
            for suffix, scenario, major_name, rate, label in [
                (key_suffix_a, scenario_a, major_name_a, interest_rate_a,
                 "Scenario A" if scenario_b is not None else None),
                (key_suffix_b, scenario_b, major_name_b, interest_rate_b, "Scenario B"),
            ]:
                if scenario is None:
                    continue
                dependents = st.session_state.get(f"rap_dependents_{suffix}", 0)
                tiered_res, tiered_roi = compute_future_plan_result(
                    scenario, major_name, rate, "2026 Tiered Standard Plan", dependents,
                    col_index=col_index, hs_wage_index=hs_wage_index,
                    roi_window_years=roi_window_years)
                rap_res, rap_roi = compute_future_plan_result(
                    scenario, major_name, rate, "2026 Repayment Assistance Plan (RAP)", dependents,
                    col_index=col_index, hs_wage_index=hs_wage_index,
                    roi_window_years=roi_window_years)
                term_years = calculate_tiered_standard_term(scenario["effective_principal"])
                rap_pay = calculate_rap_payment(get_annual_salary_for_year(major_name, 0), dependents)
                if label:
                    elements.append(Paragraph(f"<b>{label}: {xml_escape(major_name)}</b>", styles["body"]))
                elements += [
                    _pdf_table([
                        ["Plan", "Monthly", "Payoff / Forgiveness", "Interest", "Forgiven (30yr)",
                         f"{roi_window_years}-Yr Premium"],
                        ["Tiered Standard", fmt_money(tiered_res["monthly_payment"]), f"{term_years} yrs",
                         fmt_money(tiered_res["total_interest"]), "-", fmt_money(tiered_roi["earnings_premium"])],
                        ["RAP (Yr-1 income)", fmt_money(rap_pay["monthly_payment"]),
                         f"{rap_res['payoff_years']:.1f} yrs", "$0 (waived)",
                         fmt_money(rap_res["forgiven_amount"]), fmt_money(rap_roi["earnings_premium"])],
                    ]),
                    Spacer(1, 8),
                    build_pdf_comparison_balance_chart(tiered_res["schedule"], "Tiered Standard",
                                                        rap_res["schedule"], "RAP"),
                    Spacer(1, 12),
                ]
    return elements


def _pdf_resources_section(styles: dict, schools: list) -> list:
    """Mirror the on-screen "🎯 Get Your Real Numbers" section in the report:
    point the reader at the two free federal tools that replace the app's
    school-average sticker inputs with personalized figures, and say which
    input each result belongs in. One shared implementation, called from both
    the single and compare builders so the two can't drift apart (same reason
    the on-screen version is a shared helper).

    schools: list of (label, school_name) -- label is None for the single
    report, "Scenario A"/"Scenario B" for the compare report."""
    # One net-price link, not a redundant pair, when both scenarios point at the
    # same school (or there's only one scenario). Mirrors the on-screen dedupe.
    distinct_names = {name for _, name in schools if name}
    if len(distinct_names) <= 1:
        schools = [(None, next(iter(distinct_names), None))]
    parts = [
        # Starts its own page -- it's a self-contained "what to do next" section,
        # so it reads better clear of the loan tables above it.
        PageBreak(),
        Paragraph(_strip_emoji("🎯 Get Your Real Numbers"), styles["section"]),
        Paragraph(
            "The cost and aid figures above are school-wide averages. Two free, official "
            "tools give you your own personalized numbers instead:",
            styles["body"]),
    ]
    for label, name in schools:
        npc_href = xml_escape(get_school_npc_url(name) or NPC_DIRECTORY_URL)
        prefix = (f"<b>{xml_escape(label)} — your real cost after aid:</b> " if label
                  else "<b>Your real cost after aid:</b> ")
        parts.append(Paragraph(
            prefix +
            f'<a href="{npc_href}" color="blue">open the net price calculator</a>. It gives your '
            "net price — the cost after grants &amp; scholarships. Enter that as Cost of Attendance, "
            "and set Grants &amp; Scholarships to $0 (the net price already removed them).",
            styles["body"]))
    sai_href = xml_escape(SAI_ESTIMATOR_URL)
    parts.append(Paragraph(
        "<b>Your family contribution (SAI):</b> "
        f'<a href="{sai_href}" color="blue">open the Federal Student Aid Estimator</a> to estimate '
        "your Student Aid Index, and enter it as Personal Contribution (per year). It lowers the "
        "loan on top of the net price above — that is correct, not double-counting.",
        styles["body"]))
    return parts


def generate_pdf_report_single(major, city, school_name_a, in_state_a, takehome_stages,
                                coa_per_year_a, personal_contribution_per_year_a, grants_per_year_a,
                                interest_rate, repayment_strategy, loan_amount, loan_schedule_a,
                                scenario, module_context: dict = None,
                                start_year_a=None, monthly_payment=None, col_index: float = 100.0,
                              loan_basis_a: str = "cost_based", reported_debt_a=None,
                                roi_window_years: int = ROI_WINDOW_YEARS, cc_info_a=None,
                                loan_source_a: str = "personal",
                                federal_cap_a: float = None, plus_cap_a: float = None, gap_rate_a: float = None, dependents: int = 0,
                                professional_debt_a: float = None,
                                professional_school_a: str = None,
                                include_fees: bool = False) -> bytes:
    """PDF mirroring the on-screen single-scenario view: profile summary,
    Loan Information (+ per-year table + balance chart), Real-World
    Take-Home (+ take-home charts), and the Financial Position section (+ ROI
    chart)."""
    styles = _pdf_styles()
    _cf = counterfactual_vocab()
    repayment_result = scenario["repayment_result"]
    roi_result = scenario["roi_result"]

    # Decision-3 parity with the on-screen view: the per-year COA->loan table
    # appears only when the cost-based personal calc is the loan in use. With
    # the college-reported default, that table would contradict the total, so a
    # one-line note replaces it (the Total Loan Amount line below still shows the
    # figure actually used).
    if loan_basis_a == "no_program":
        loan_detail = [Paragraph(
            "BLS lists no degree requirement for this career, so no tuition is financed "
            "and none is charged against it.", styles["body"])]
    elif loan_basis_a == "reported_scaled":
        loan_detail = [Paragraph(
            f"Loan is an ESTIMATE. College Scorecard reports {fmt_money(reported_debt_a)} "
            "for this school -- one institution-wide median across completers of every "
            "credential length, with no per-credential breakdown -- scaled by the ratio of "
            "cumulative federal Direct borrowing limits for this program's length. Not a "
            "reported figure. Use Detailed mode in the app to model your own cost and aid.",
            styles["body"])]
    elif loan_source_a == "college":
        loan_detail = [Paragraph(
            "Loan is this school's median completer debt (College Scorecard) -- the median "
            "amount graduates who borrowed leave with, across every credential length -- "
            "not a per-year cost buildup. Use "
            "Switch to Detailed mode in the app to model your own cost and aid instead.",
            styles["body"])]
    else:
        loan_detail = [_pdf_table(full_width=True, rows=[
            ["Year", "Cost of Attendance", "Loan Amount This Year"],
            *[[f"{row['year']} ({start_year_a + row['year'] - 1})" if start_year_a is not None else row["year"],
               fmt_money(row["coa"]), fmt_money(row["loan_amount"])] for row in loan_schedule_a],
        ])]

    financing_line = _pdf_financing_flowables(scenario.get("financing"), styles)

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
            f"Modelled over {_cf['window_phrase'].format(years=roi_window_years)}, against "
            f"{_cf['baseline_noun']}. All figures adjusted for cost of living in {xml_escape(city)}.",
            styles["cover_sub"],
        ),
        Spacer(1, 8),
        _pdf_rule(),
        Spacer(1, 4),
        Paragraph("Your Profile", styles["section"]),
        _pdf_table(
            _pdf_profile_rows(major, school_name_a, in_state_a, coa_per_year_a,
                               personal_contribution_per_year_a, grants_per_year_a,
                               interest_rate, repayment_strategy, city,
                               start_year=start_year_a, cc_info=cc_info_a, loan_source=loan_source_a,
                               professional_school=professional_school_a,
                               professional_debt=professional_debt_a,
                               typical_education=scenario.get("typical_education")),
            header=False, full_width=True,
        ),
        PageBreak(),
        Paragraph(_strip_emoji(f"💳 Loan Information — {scenario['strategy_label']}"), styles["section"]),
        *loan_detail,
        Spacer(1, 6),
        # Label comes from the basis the sidebar already resolved -- deliberately
        # NOT recomputed here from program_years_for_major, which would be a
        # second independent derivation and exactly the twin-drift CLAUDE.md
        # warns about for the chart pairs.
        Paragraph(f"{loan_amount_label(loan_basis_a, program_years_for_major(major))}: "
                   f"{fmt_money(loan_amount)}", styles["body"]),
        *financing_line,
        Spacer(1, 6),
        # A fourth column only when there is forgiveness -- same condition as
        # the on-screen metric, so the report and the page cannot disagree
        # about whether this scenario has any.
        _pdf_table(full_width=True, rows=(
            [["Monthly Payment", "Payoff Timeline", "Total Interest Paid", "Loan Forgiven"],
             [fmt_money(repayment_result["monthly_payment"]) if "monthly_payment" in repayment_result else "Varies (IDR)",
              f"{repayment_result['payoff_years']:.1f} yrs",
              fmt_money(repayment_result["total_interest"]),
              fmt_money(repayment_result["forgiven_amount"])]]
            if (repayment_result.get("forgiven_amount", 0) or 0) > 0 else
            [["Monthly Payment", "Payoff Timeline", "Total Interest Paid"],
             [fmt_money(repayment_result["monthly_payment"]) if "monthly_payment" in repayment_result else "Varies (IDR)",
              f"{repayment_result['payoff_years']:.1f} yrs",
              fmt_money(repayment_result["total_interest"])]]
        )),
        Spacer(1, 12),
        build_pdf_balance_chart(repayment_result["schedule"], scenario["strategy_label"]),
        # "Get Your Real Numbers" starts its own page (PageBreak lives in
        # _pdf_resources_section) -- placed after the complete Loan Information
        # section so it no longer splits it.
        *_pdf_resources_section(styles, [(None, school_name_a)]),
        Spacer(1, 12),
        Paragraph(_strip_emoji(f"🏙️ Real-World Take-Home — {major} in {city}"), styles["section"]),
        # One row per career stage, matching the on-screen block, which renders
        # them side by side rather than behind a selector.
        _pdf_table(full_width=True, rows=[
            ["Career Stage", "Gross Salary", "Take-Home Pay (annual)",
             "Monthly Disposable", "COL-Adjusted Disposable"],
            *[[label, fmt_money(f["gross"]), fmt_money(f["take_home"]["net_take_home"]),
               fmt_money(f["disposable_nominal"]), fmt_money(f["disposable_col_adjusted"])]
              for label, f in takehome_stages],
        ]),
    ]
    # One row of charts per career stage, mirroring the on-screen columns. The
    # two implementations share no code (see CLAUDE.md on the chart twins), so
    # this has to be changed in step with render_takehome_block or the report
    # silently shows a different stage than the page.
    _drawable = [(label, figs) for label, figs in takehome_stages
                 if figs["gross"] > 0 and figs["monthly_payment"] is not None]
    if _drawable:
        # Both charts for a stage side by side, so each stage's pair fits on a
        # single page -- stacked at full width they're each taller than half a
        # page and the pair overflows, which forced a split.
        _chart_w = (PDF_CONTENT_WIDTH - 18) / 2
        story += [
            Spacer(1, 12),
            Paragraph(_strip_emoji(
                "Each block below is one career stage. The loan payment is the same "
                "dollar amount in both -- what changes is how much of your pay it takes."),
                styles["caption"]),
        ]
        for _label, _figs in _drawable:
            _take_home = _figs["take_home"]
            story += [
                Spacer(1, 8),
                Paragraph(_strip_emoji(_label), styles["section"]),
                KeepTogether(Table(
                    [[build_pdf_takehome_pie_chart(_take_home, max_width=_chart_w),
                      build_pdf_takehome_vs_loan_chart(
                          _take_home["net_take_home"] / 12, _figs["monthly_payment"],
                          max_width=_chart_w)]],
                    colWidths=[PDF_CONTENT_WIDTH / 2, PDF_CONTENT_WIDTH / 2],
                    hAlign="CENTER",
                    style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                      ("ALIGN", (0, 0), (-1, -1), "CENTER")]),
                )),
            ]
    story += [
        Spacer(1, 12),
        Paragraph(_strip_emoji(f"📊 {roi_window_years}-Year Financial Position"), styles["section"]),
        _pdf_table([
            [f"{_cf['metric_label']} — {roi_window_years}-Yr Net Position{_cf['no_loan_suffix']}",
             f"{major} — {roi_window_years}-Yr Net Position", "Earnings Premium (COL-Adjusted)"],
            [fmt_money(roi_result["hs_net_position"]), fmt_money(roi_result["major_net_position"]),
             fmt_money(roi_result["earnings_premium"])],
        ]),
        Paragraph(
            f"<b>Earnings Premium</b> is the bottom line: how much more (or less) money you would "
            f"have after {roi_window_years} years by going into {major} (after paying off the loan) "
            f"instead of {_cf['instead_of']}. It is the "
            f"difference between the two Net Position figures, both adjusted for the cost of living in "
            f"{city} -- that is what 'COL-Adjusted' means.",
            styles["caption"]),
        Spacer(1, 12),
        build_pdf_net_position_chart(
            net_position_frame([(major, scenario)], col_index,
                                get_metro_wage_index(city), roi_window_years),
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
        baseline_start_age=scenario["baseline_start_age"],
        professional_debt=professional_debt_a,
        federal_cap=federal_cap_a, plus_cap=plus_cap_a, gap_rate=gap_rate_a, dependents=dependents, include_fees=include_fees,
            **breakeven_kwargs())
    story += _pdf_breakeven_block(breakeven, styles)
    story += _pdf_wage_distribution_block(major, styles)

    story += _pdf_module_sections(
        module_context, scenario_a=scenario, major_name_a=major, interest_rate_a=interest_rate,
        col_index=col_index, hs_wage_index=get_metro_wage_index(city),
        key_suffix_a="single", roi_window_years=roi_window_years,
    )
    # Only cite the professional-school sources when this major actually uses
    # them -- listing AAMC on a Software Developer's report is noise.
    story += _pdf_sources_section(
        styles, roi_window_years,
        uses_training_debt=bool(MAJOR_DATA.get(major, {}).get("additional_training_debt")
                                or MAJOR_DATA.get(major, {}).get("unpaid_training_years")),
        underemployment_majors=([(None, major)] if dataset_mode == DATASET_MODE_MAJOR else None),
        uses_community_college=bool(cc_info_a),
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
                                 cc_info_a=None, cc_info_b=None,
                                 loan_source_a: str = "personal",
                                 loan_source_b: str = "personal",
                                 federal_cap_a: float = None, plus_cap_a: float = None, gap_rate_a: float = None, dependents: int = 0,
                                professional_debt_a: float = None,
                                professional_school_a: str = None,
                                 federal_cap_b: float = None, plus_cap_b: float = None, gap_rate_b: float = None, professional_debt_b: float = None, professional_school_b: str = None,
                                 include_fees: bool = False) -> bytes:
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
    _cf = counterfactual_vocab()
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
            f"Two paths compared over {_cf['window_phrase'].format(years=roi_window_years)}, each "
            f"against {_cf['baseline_noun']}. All figures adjusted for cost of living in {xml_escape(city)}.",
            styles["cover_sub"],
        ),
        Spacer(1, 8),
        _pdf_rule(),
        Spacer(1, 4),
        Paragraph(
            f"<b>Earnings Premium</b> (shown for each scenario below) is the bottom line: how much "
            f"more (or less) money you would have after {roi_window_years} years by taking that path "
            f"instead of {_cf['instead_of']} — with both "
            f"sides adjusted for the cost of living in {city} ('COL-Adjusted').",
            styles["caption"]),
        Spacer(1, 6),
        Paragraph(f"Scenario A: {scenario_a['major']} — {scenario_a['strategy_label']}", styles["section"]),
        _pdf_table(
            _pdf_profile_rows(major, school_name_a, in_state_a, coa_per_year_a,
                               personal_contribution_per_year_a, grants_per_year_a,
                               interest_rate, repayment_strategy, city_name=city,
                               start_year=start_year_a, cc_info=cc_info_a, loan_source=loan_source_a,
                               professional_school=professional_school_a,
                               professional_debt=professional_debt_a,
                               typical_education=scenario_a.get("typical_education")),
            header=False, full_width=True,
        ),
        Spacer(1, 6),
        _pdf_scenario_metrics_table(scenario_a, roi_window_years),
        *_pdf_financing_flowables(scenario_a.get("financing"), styles),
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
                              working_years=scenario_a["working_years"],
                              baseline_start_age=scenario_a["baseline_start_age"],
                              professional_debt=professional_debt_a,
                              federal_cap=federal_cap_a, plus_cap=plus_cap_a, gap_rate=gap_rate_a, dependents=dependents, include_fees=include_fees,
            **breakeven_kwargs()),
            styles, scenario_label="Scenario A"),
        PageBreak(),
        Paragraph(f"Scenario B: {scenario_b['major']} — {scenario_b['strategy_label']}", styles["section"]),
        _pdf_table(
            _pdf_profile_rows(major_b, school_name_b, in_state_b, coa_per_year_b,
                               personal_contribution_per_year_b, grants_per_year_b,
                               interest_rate_b, repayment_strategy_b, city_name=city,
                               start_year=start_year_b, cc_info=cc_info_b, loan_source=loan_source_b,
                               professional_school=professional_school_b,
                               professional_debt=professional_debt_b),
            header=False, full_width=True,
        ),
        Spacer(1, 6),
        _pdf_scenario_metrics_table(scenario_b, roi_window_years),
        *_pdf_financing_flowables(scenario_b.get("financing"), styles),
        *_pdf_breakeven_block(
            breakeven_summary(major_b, loan_amount_b, interest_rate_b, repayment_strategy_b,
                              roi_window_years=roi_window_years, col_index=col_index,
                              career_data_source=career_data_source,
                              hs_wage_index=get_metro_wage_index(city),
                              personal_contribution=scenario_b["personal_contribution"],
                              enrollment_years=scenario_b["enrollment_years"],
                              working_years=scenario_b["working_years"],
                              baseline_start_age=scenario_b["baseline_start_age"],
                              professional_debt=professional_debt_b,
                              federal_cap=federal_cap_b, plus_cap=plus_cap_b, gap_rate=gap_rate_b, dependents=dependents, include_fees=include_fees,
            **breakeven_kwargs()),
            styles, scenario_label="Scenario B"),
        PageBreak(),
        Paragraph(_strip_emoji("📊 Side-by-Side Charts"), styles["section"]),
        Spacer(1, 6),
        build_pdf_comparison_balance_chart(
            scenario_a["repayment_result"]["schedule"], f"A: {scenario_a['major']}{cc_chart_label_suffix((cc_info_a or {}).get('mode'))}",
            scenario_b["repayment_result"]["schedule"], f"B: {scenario_b['major']}{cc_chart_label_suffix((cc_info_b or {}).get('mode'))}",
        ),
        Spacer(1, 12),
        build_pdf_net_position_chart(
            net_position_frame(
                [(f"A: {scenario_a['major']}{cc_chart_label_suffix((cc_info_a or {}).get('mode'))}", scenario_a),
                 (f"B: {scenario_b['major']}{cc_chart_label_suffix((cc_info_b or {}).get('mode'))}", scenario_b)],
                col_index, get_metro_wage_index(city), roi_window_years),
            roi_window_years,
        ),
        *_pdf_resources_section(styles, [("Scenario A", school_name_a), ("Scenario B", school_name_b)]),
    ]
    # One per scenario, labelled -- in Career mode A and B are different
    # occupations with genuinely different spreads, which is the comparison
    # this chart is most useful for.
    story += _pdf_wage_distribution_block(major, styles, scenario_label="A")
    story += _pdf_wage_distribution_block(major_b, styles, scenario_label="B")
    story += _pdf_module_sections(
        module_context, scenario_a=scenario_a, major_name_a=major, interest_rate_a=interest_rate,
        scenario_b=scenario_b, major_name_b=major_b, interest_rate_b=interest_rate_b,
        col_index=col_index, hs_wage_index=get_metro_wage_index(city),
        roi_window_years=roi_window_years,
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
        uses_community_college=bool(cc_info_a) or bool(cc_info_b),
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

# Shared by every components.html snippet that needs to reach the Streamlit
# app itself -- its query params, or a hidden button in its DOM.
#
# Neither window.top nor a fixed window.parent is correct on its own. Locally
# there is one iframe layer (the component inside the app page) so the two
# coincide; on Community Cloud the app is itself wrapped, giving
#     component  <  app frame (/~/+/)  <  wrapper page (/)
# and window.top is then the WRAPPER -- not where Streamlit reads query params,
# and not where its buttons live. Climbing until a known button is found works
# at any nesting depth and assumes nothing about how many layers the host adds.
#
# The rule this encodes: window.top for BROWSER EVENTS (keystrokes land on the
# outermost page), the app frame for anything STREAMLIT ITSELF READS.
FIND_APP_FRAME_JS = """
    function findAppFrame(buttonLabel) {
        let w = window;
        for (let i = 0; i < 6; i++) {
            try {
                for (const b of w.document.querySelectorAll("button")) {
                    if (b.textContent.trim() === buttonLabel) return w;
                }
            } catch (e) { /* cross-origin frame -- keep climbing */ }
            if (w === w.parent) break;
            w = w.parent;
        }
        return null;
    }
    function clickAppButton(buttonLabel) {
        const w = findAppFrame(buttonLabel);
        if (!w) return false;
        for (const b of w.document.querySelectorAll("button")) {
            if (b.textContent.trim() === buttonLabel) { b.click(); return true; }
        }
        return false;
    }
"""


# The visitor's timezone comes from st.context.timezone (see
# get_user_timezone in section 2). There is no JS round-trip here any more.
#
# There used to be: a hidden "Set Timezone" button, plus a component script that
# detected the zone, wrote it to the URL with history.replaceState, and clicked
# the button to force a rerun that would "pick up the newly-set query param".
# It never worked. replaceState changes the browser's address bar WITHOUT
# telling the server, and Streamlit sends the frontend's query params as
# captured at page load -- so the rerun the click produced read exactly the
# same params as before it. The URL looked right the whole time, which is why
# this survived two rounds of fixing.
#
# Proven by isolation before removing it: a real page load carrying
# ?tz=America/Los_Angeles produced a "01:14 PM PDT" PDF footer, while a fresh
# visit whose URL that script had just rewritten to the byte-identical string
# produced "08:13 PM UTC" from the same code path. The earlier fix in this spot
# -- targeting the app frame rather than window.top -- was diagnosing the wrong
# layer of a mechanism that could not work at any layer.
#
# st.context.timezone arrives with the initial connection, so it is populated
# on the FIRST render. That also fixes the caveat the old note conceded but
# could not solve: the pageview logged below no longer has to land in UTC.
# ?tz= is still read as a fallback for links that carry it.
#
# FIND_APP_FRAME_JS above is still used -- the admin-reveal keydown listener
# needs it. The frame-climbing rule it encodes remains correct; it was the
# replaceState half that was doomed.

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

# The pre/post research instrument renders for everyone. It was held behind a
# ?research=1 gate from 2026-07-31 to 2026-08-01 because the human-subjects
# determination had not been obtained; that determination now exists (recorded
# in migrations.sql and the paper's section 5.1b), so the gate is gone.
#
# Two protections that were NEVER the IRB gate remain, and removing them is a
# separate and much worse decision:
#   - research_participation_allowed() still withholds the instrument from a
#     self-identified student who has not attested to RESEARCH_MIN_AGE.
#   - The consent notice still renders above the form, before anything is
#     answered.
# The calculator itself has never been gated on either, and must not be: using
# a public information tool is not participating in research.

# an expander see "pageview_logged" already set and skip logging again.
if "pageview_logged" not in st.session_state:
    # Flag BEFORE the write, not after. log_usage_event performs a network
    # insert, and the timezone round-trip clicks its hidden button mid-run --
    # Streamlit then interrupts the script before the flag is set, and the
    # rerun re-enters this guard and logs a second pageview. Real browsers run
    # that JS and doubled; the automated loads that never execute it did not,
    # so the inflation landed specifically on tagged recruitment traffic, which
    # is the traffic a campaign gets judged on.
    #
    # Setting the flag first means an interrupted write loses ONE pageview
    # rather than duplicating it. That is the right way to be wrong here: an
    # undercount by a row is recoverable arithmetic, a double-count silently
    # doubles a headline number. presurvey_shown_logged has always done it in
    # this order, which is why it never doubled -- the contrast is what
    # identified the bug.
    st.session_state.pageview_logged = True
    # A distinct action for the standalone repayment page. Without it those
    # visits land in the same "pageview" bucket as the calculator's, and the
    # two are different populations answering different questions -- pooling
    # them would quietly inflate calculator traffic the moment the repayment
    # link is shared anywhere.
    #
    # Reads the query param rather than the latch below, because the latch is
    # set hundreds of lines later and this fires first. Safe: a pageview
    # describes the visit as it ARRIVED, which is exactly what the param says
    # on the first render, and pageview_logged stops any rerun re-entering.
    log_usage_event("pageview_repayment" if repayment_page_requested() else "pageview")

if "survey_submitted" not in st.session_state:
    st.session_state.survey_submitted = False

# Pre-question state. answered and skipped are tracked separately on purpose:
# both hide the prompt, but only one of them means the visitor had something
# to say. Neither is seeded onto the radios themselves -- those stay at
# index=None so "unanswered" survives as a distinct third state.
for _presurvey_flag in ("presurvey_answered", "presurvey_skipped", "presurvey_shown_logged"):
    if _presurvey_flag not in st.session_state:
        st.session_state[_presurvey_flag] = False

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

# Resolved HERE, at the top of section 4, because the Financing block below
# reads is_returning when it computes working_years -- well before this
# section's own widget renders. Same read-before-the-widget pattern
# dataset_mode and city already use, and the reason the constants sit above
# rather than beside the radio.
# Who is going to school. The app has always modelled exactly one person -- an
# 18-year-old starting a first degree, measured against a debt-free high school
# graduate -- and that is now the minority case: 24.6M federal borrowers are 35+
# against 20.2M under 35, and over-50s owe more on average than under-35s. For
# someone going back at 49 the high-school-graduate counterfactual is
# meaningless; her alternative was her existing job at her existing salary.
#
# Read from session_state before the widget renders, the same pattern
# dataset_mode uses, because the Financing block above needs it. The mode's
# own constants live in section 1 -- section 2's counterfactual_vocab() needs
# them, and section 2 is what analyze_model.py execs.
#
# A shared link carries the mode as a short token. Map it back through the
# options list rather than trusting the URL: a hand-edited ?smode=xyz must fall
# back to the default, not hand st.radio a value that isn't in its options --
# which raises, for everyone on that link. Same reasoning as reconcile_cc_mode.
st.session_state.setdefault(
    "student_mode_radio",
    STUDENT_MODE_RETURNING if get_shared_default("smode", "first") == "returning"
    else STUDENT_MODE_FIRST)
student_mode = st.session_state["student_mode_radio"]
is_returning = student_mode == STUDENT_MODE_RETURNING
st.session_state.setdefault("current_age", get_shared_int("age", 30))
# Seeded to 0, NOT to a plausible-looking salary. A seeded $50k produced a
# 4,950% ROI on the default San Francisco scenario, because $50k is below what
# a high school graduate earns there -- so the app invented a spectacular
# return for someone who had entered nothing. An unanswered question must look
# unanswered.
st.session_state.setdefault("current_salary", get_shared_int("cur_sal", 0))
st.session_state.setdefault("salary_no_degree_10y", get_shared_int("sal10", 0))
st.session_state.setdefault("existing_debt", get_shared_int("debt", 0))
st.session_state.setdefault("existing_debt_rate", get_shared_float("debt_rate", DEFAULT_FEDERAL_RATE))
st.session_state.setdefault(
    "returning_enrollment",
    RETURNING_STOP_WORK if get_shared_default("enroll", "working") == "fulltime"
    else RETURNING_KEEP_WORKING)


# Global styling for every number_input in the sidebar (Scenario A and B
# alike): hide the +/- stepper buttons, and show a $ or % unit prefix on
# the left based on which one appears in the widget's own label -- every
# number_input in this app has exactly one or the other (e.g. "Cost of
# Attendance (per year, $)", "Federal Direct rate (%)"), and
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
# exists, the same pattern the Career section's controls use. See the
# Methodology footer for what each module models and, just as importantly,
# what it deliberately does NOT claim.
# Each of these is a boolean carried in a shared link. `setdefault` alone is
# NOT enough for them: Streamlit reads query params client-side, so editing a
# URL in an already-open tab reruns the script WITHOUT starting a new session.
# The key is then already present, setdefault does nothing, and the link's flag
# is silently ignored -- which is how a link with ?legacy=1 opened in an
# existing tab showed only the two 2026 plans while the same link in a fresh
# tab showed all four.
#
# apply_shared_flag re-applies the param whenever its VALUE changes, which
# covers both a fresh session and a same-tab navigation, while still letting a
# visitor's own toggling win afterwards -- a plain "URL always wins" would
# re-tick the box on the next rerun and make the checkbox unusable.
# Written out one per line rather than looped: check_share_coverage.py reads
# these statically, and a loop hides the param/key pairing from it -- which is
# a fair signal that the loop was too clever for a wiring the guard exists to
# police.
st.session_state.setdefault("enable_legacy_plans", False)
apply_shared_flag("legacy", "enable_legacy_plans")
enable_legacy_plans = st.session_state["enable_legacy_plans"]
st.session_state.setdefault("enable_prestige_mode", False)
apply_shared_flag("prestige", "enable_prestige_mode")
st.session_state.setdefault("enable_ai_mode", False)
apply_shared_flag("ai", "enable_ai_mode")
st.session_state.setdefault("enable_future_proofing", False)
apply_shared_flag("future", "enable_future_proofing")
# Seeded here rather than at its checkbox, which renders far below several
# blocks that already read this key before the widget exists.
st.session_state.setdefault("count_foregone_earnings", False)
apply_shared_flag("foregone", "count_foregone_earnings")
enable_prestige_mode = st.session_state["enable_prestige_mode"]
enable_ai_mode = st.session_state["enable_ai_mode"]
enable_future_proofing = st.session_state["enable_future_proofing"]
prestige_tier_a = None
prestige_tier_b = None


def resolve_program_years(selection_key: str, fallback: str,
                           share_param: str = None, returning: bool = False) -> int:
    """Enrollment length for whichever occupation this scenario currently has
    selected, resolved from session_state before the Career section builds
    MAJOR_DATA.

    Financing renders above Career, so the cost model runs before MAJOR_DATA
    exists -- the loan was therefore computed without knowing which occupation
    it was paying for, which is exactly why an associate's-degree career was
    charged four years of tuition. Reading the selection early is the same
    before-the-widget pattern dataset_mode and city already use; the
    typical_education lookup goes straight to the cached careers CSV rather
    than to MAJOR_DATA, since that dict isn't built yet.

    Always the national file: typical entry-level education is a property of
    the occupation, not of where it's practised, so the state and metro
    overlays carry wages only and have nothing to say about program length.

    Major mode has no education field at all (a major isn't an occupation), and
    an unrecognised or not-yet-chosen selection falls through to
    UNDERGRAD_YEARS -- so anything this can't resolve keeps the old behaviour.
    """
    education = resolve_typical_education(selection_key, fallback, share_param)
    if not education:
        # Major mode: no BLS education level exists, so fall back to whatever
        # the visitor selected in the credential radio (Bachelor's by default,
        # which reproduces the old behaviour exactly).
        education = st.session_state.get("credential_a", CREDENTIAL_BACHELORS)
    return program_years_for_context(education, returning)


def resolve_typical_education(selection_key: str, fallback: str,
                               share_param: str = None) -> str:
    """The BLS entry-education for the current selection, resolved before the
    Career section builds MAJOR_DATA. Empty string outside Career mode, where a
    major is not an occupation and carries no education level -- everything
    downstream then keeps the undergraduate defaults."""
    # dataset_mode_radio is seeded ~700 lines below this, so on the first render
    # of a shared link it does not exist yet and every ?mode=Career link was
    # read as Major mode for one pass -- which returned "" and priced a
    # two-year or six-year programme as four undergraduate years. Read the
    # link's own value when session_state has nothing, exactly as the selection
    # below does.
    mode = st.session_state.get("dataset_mode_radio") or get_shared_default(
        "mode", DATASET_MODE_MAJOR)
    if mode != DATASET_MODE_CAREER:
        # Major mode -- see resolve_program_years. Returning "" rather than a
        # credential keeps this function honest about what BLS says; the caller
        # decides whether to substitute the visitor's own answer.
        return ""
    # On the FIRST render of a shared link, session_state has no selection yet
    # -- the Career section seeds major_select_a hundreds of lines below this,
    # while the financing block above needs the length now. Falling straight to
    # `fallback` meant a ?major= link was priced as the DEFAULT occupation for
    # one render: a link to Dental Hygienists showed four years instead of two,
    # and every one of the 113 graduate-level occupations showed four instead
    # of six or nine. Reading the link's own value closes that.
    selection = st.session_state.get(selection_key)
    if not selection and share_param:
        selection = get_shared_default(share_param, None)
    selection = selection or fallback
    careers = load_bls_careers(CAREERS_CSV_PATH_NATIONAL)
    return careers.get(selection, {}).get("typical_education") or ""


# Scenario B's own selection is made above its financing block, so it could read
# it directly -- it goes through the same helper anyway so both scenarios can't
# drift apart on how a program length is decided.
# In returning mode the visitor's own credential wins over the occupation's
# BLS entry level: someone going back for an MBA to move into a bachelor's-level
# job is describing their SCHOOLING, and BLS describes the JOB. In first-time
# mode the BLS level is the better answer and the radio is not shown.
# Reads the link directly when session_state has nothing: the credential radio
# is seeded in the Career section, ~800 lines below this, so on the first render
# of a shared link ?cred= has not landed yet -- the same trap ?mode= and
# ?major= hit above. Without this a returning master's link was priced as a
# four-year bachelor's for one render.
_education_source_a = (
    (st.session_state.get("credential_a")
     or get_shared_default("cred", CREDENTIAL_BACHELORS))
    if is_returning else None)
if _education_source_a not in CREDENTIAL_OPTIONS:
    _education_source_a = CREDENTIAL_BACHELORS if is_returning else None
program_years_a = (program_years_for_context(_education_source_a, True)
                   if _education_source_a else resolve_program_years(
                       "major_select_a", DEFAULT_SELECTION_A[DATASET_MODE_CAREER],
                       share_param="major"))
# How many of those years are GRADUATE study. Drives the loan limits, hides the
# community-college path, and shifts the high-school baseline's start age.
_typical_education_a = (_education_source_a or resolve_typical_education(
    "major_select_a", DEFAULT_SELECTION_A[DATASET_MODE_CAREER], share_param="major")
    or st.session_state.get("credential_a", CREDENTIAL_BACHELORS))
graduate_years_a = graduate_years_for_education(_typical_education_a)
# The school's own median graduate debt, resolved HERE rather than at its
# widget: the loan basis below runs before the Career section, the same reason
# resolve_program_years reads ahead. The widget further down only renders the
# control and the caption; this is the value the model uses.
_credential_key_early_a = CREDENTIAL_DATA_KEY.get(_typical_education_a)
_cip_family_early_a = (MAJOR_TO_CIP_FAMILY.get(
                           st.session_state.get("major_select_a")
                           or get_shared_default("major", None))
                       if st.session_state.get("dataset_mode_radio") == DATASET_MODE_MAJOR
                       else None)
graduate_debt_a = (graduate_debt_for(_cip_family_early_a, _credential_key_early_a,
                                      st.session_state.get("grad_school_a"))
                   if _credential_key_early_a and _cip_family_early_a else None)
program_years_b = resolve_program_years(
    "major_b", DEFAULT_SELECTION_B[DATASET_MODE_CAREER], share_param="major_b")
graduate_years_b = graduate_years_for_education(resolve_typical_education(
    "major_b", DEFAULT_SELECTION_B[DATASET_MODE_CAREER], share_param="major_b"))

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
        "College Tier Selection", prestige_tier_options, index=default_tier_a_index, key="prestige_tier_a", on_change=lambda: mark_interaction("prestige_tier_a"),
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
    # setdefault rather than value=, because the school search below writes
    # this key directly. Streamlit raises if a widget carries both a default
    # and a key whose state is assigned elsewhere -- the pattern CLAUDE.md
    # describes, and the reason coa_per_year_a already works this way.
    _apply_pending_school()
    st.session_state.setdefault("school_search_a",
                                 get_shared_default("school", "UC Berkeley"))
    school_search_a = st.sidebar.text_input(
        "Target Undergraduate School", placeholder="e.g. University of Michigan",
        key="school_search_a",
        on_change=lambda: (mark_interaction("school_a"),
                            _autofill_coa("school_search_a", "school_pick_a", "in_state_a", "coa_per_year_a")),
        help="Type a school name to auto-fill Cost of Attendance below from "
             "real government data, if we have it on file. If your school "
             "isn't found, just enter Cost of Attendance yourself.",
    )
    matching_schools_a = find_matching_schools(school_search_a, load_coa_dataset())
    if len(matching_schools_a) >= 2:
        # Options are UNITIDs; format_func renders the human label. The label
        # carries "(City, ST)" only for names that are actually shared, which
        # is what makes two "Southwestern College" entries distinguishable
        # instead of identical.
        st.sidebar.selectbox(
            f"Multiple schools matched \"{school_search_a}\" -- pick yours:",
            matching_schools_a, key="school_pick_a",
            format_func=lambda u: school_option_label(u, load_coa_dataset()),
            on_change=lambda: (mark_interaction("school_a"),
                            _autofill_coa("school_search_a", "school_pick_a", "in_state_a", "coa_per_year_a")),
        )
    school_name_a = _resolve_school_name("school_search_a", "school_pick_a")
    school_unitid_a = _resolve_school_unitid("school_search_a", "school_pick_a")

    # setdefault rather than value=, because _apply_pending_school writes this
    # key when a search result is applied. A widget carrying both a default and
    # a Session State write is the conflict Streamlit warns about on every such
    # apply -- same conversion school_search_a needed for the same reason.
    st.session_state.setdefault("in_state_a", get_shared_default("in_state", "1") == "1")
    in_state_a = st.sidebar.checkbox(
        "In-State Student?", key="in_state_a",
        on_change=lambda: (mark_interaction("school_a"),
                            _autofill_coa("school_search_a", "school_pick_a", "in_state_a", "coa_per_year_a")),
        help="Check this if you'd pay in-state tuition at the school above. "
             "Changes the auto-filled Cost of Attendance and how fast tuition "
             "is estimated to grow each year.",
    )
    coa_match_a = (find_school_coa(school_name_a, load_coa_dataset(), unitid=school_unitid_a)
                    if school_name_a else None)
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
    # The Cost of Attendance widget itself renders below, in the Detailed-mode
    # (loan_source == "personal") input block, once the Loan estimate toggle has
    # been resolved -- so the sidebar can lead with the loan default.

# The college-reported loan default: the median cumulative federal debt that
# graduates of this school who borrowed leave with (College Scorecard, via the
# cached fetch_median_debt). None in Prestige Mode (no real school) or when the
# school has no reported figure -- the loan then falls back to the cost-based
# personal calculation below, exactly as the app worked before this default.
_debt_lookup_a = {} if enable_prestige_mode else (fetch_median_debt(school_name_a, scorecard_api_key) or {})
median_debt_a = _debt_lookup_a.get("median_debt")
predominant_degree_a = _debt_lookup_a.get("predominant_degree")

# ---- Loan estimate mode: Simplified vs Detailed ------------------------
# One global toggle drives both scenarios. Simplified uses the school's
# college-reported median debt (no cost/aid inputs needed); Detailed builds the
# loan from Cost of Attendance minus Personal Contribution and Grants and shows
# the year-by-year breakdown. Rendered here -- after Scenario A's school is
# resolved -- so the disable gate can see whether a reported figure exists.
# Scenario B reads the same st.session_state["loan_mode"]; the gate keys off
# Scenario A (B is resolved later inside its own expander), a documented
# simplification: the rare "A has no reported debt but B does" case uses Detailed
# for both, which is safe and still accurate.
simplified_available = median_debt_a is not None
st.session_state.setdefault("loan_mode", get_shared_default("loan_mode", "Simplified"))
if simplified_available:
    loan_mode = st.sidebar.radio(
        "Loan estimate (both scenarios)",
        options=["Simplified", "Detailed"],
        format_func=lambda m: {
            "Simplified": "Simplified — use the school's reported debt",
            "Detailed": "Detailed — estimate from my cost & aid",
        }[m],
        key="loan_mode", on_change=lambda: mark_interaction("loan_mode"),
        help="Simplified uses the median debt graduates who borrowed leave this "
             "school with (College Scorecard) -- no cost or aid inputs needed. "
             "Detailed builds the loan from Cost of Attendance minus your Personal "
             "Contribution and Grants, and shows the year-by-year breakdown. "
             "Applies to both scenarios in Compare Mode.",
    )
    effective_loan_mode = loan_mode
else:
    # No reported debt for this school (Prestige tier, a school not in College
    # Scorecard, or the live lookup being down) -- Simplified can't produce a
    # number, so it's disabled and Detailed is used. The stored "loan_mode"
    # preference is left untouched (this uses a separate display key) so it
    # returns if a reported figure does.
    st.sidebar.radio(
        "Loan estimate (both scenarios)", options=["Detailed"],
        format_func=lambda m: "Detailed — estimate from my cost & aid",
        key="loan_mode_unavailable_display", disabled=True,
        help="Simplified needs a school with reported debt in College Scorecard. "
             "We don't have one for this selection, so the loan is estimated from "
             "your cost & aid below.",
    )
    st.sidebar.caption("Simplified is unavailable for this school — using Detailed.")
    effective_loan_mode = "Detailed"

# Dependent vs independent sets the federal Direct borrowing cap, which decides
# how much of a Detailed-mode loan is capped federal debt vs higher-rate gap
# financing (PLUS/private). Global -- one student, same status across scenarios.
# Only rendered in Detailed (Simplified uses reported federal-only debt, no cap).
st.session_state.setdefault("loan_dependency", get_shared_default("dependency", "dependent"))
if effective_loan_mode == "Detailed":
    loan_dependency = st.sidebar.radio(
        "Dependency status (both scenarios)",
        options=["dependent", "independent"],
        format_func=lambda d: {"dependent": "Dependent (parents' info on FAFSA)",
                                "independent": "Independent"}[d],
        key="loan_dependency", on_change=lambda: mark_interaction("loan_dependency"),
        help="Sets your federal Direct loan limit -- about $27,000 total over four "
             "years if dependent, $45,000 if independent. Need above that limit is "
             "modeled as higher-rate gap financing (Direct PLUS or private loans).",
    )
else:
    loan_dependency = st.session_state["loan_dependency"]

loan_source_a = "college" if (effective_loan_mode == "Simplified" and median_debt_a is not None) else "personal"

# Cost & aid inputs render inline only when the cost-based (Detailed/personal)
# path is what's driving this scenario's loan. When hidden (Simplified, using the
# reported figure) the values are read from session_state -- seeded here -- so
# effective COA, the per-year schedule, computed_loan_amount_a, and share links
# stay defined.
_start_year_opts_a = list(range(now_local().year, now_local().year + 8))
st.session_state.setdefault("start_year_a", get_shared_int("start_year", now_local().year))
if st.session_state["start_year_a"] not in _start_year_opts_a:
    st.session_state["start_year_a"] = now_local().year
st.session_state.setdefault("personal_contribution_per_year_a", get_shared_int("pc", 0))
st.session_state.setdefault("grants_per_year_a", get_shared_int("grants", 0))
st.session_state.setdefault("gap_rate_a", get_shared_float("gap_rate", DEFAULT_GAP_RATE))
# Streamlit drops a widget's value from session_state the moment the widget
# stops rendering, so toggling to Simplified (which hides these inputs) would
# reset Cost of Attendance to 0 the next time Detailed re-creates the box.
# Re-affirming each key every run converts it from a widget-owned value back to
# a persistent one, so entered/auto-filled figures survive the hide/show cycle.
for _k in ("coa_per_year_a", "start_year_a",
           "personal_contribution_per_year_a", "grants_per_year_a", "gap_rate_a"):
    if _k in st.session_state:
        st.session_state[_k] = st.session_state[_k]
if loan_source_a == "personal":
    if not enable_prestige_mode:
        coa_per_year_a = st.sidebar.number_input(
            "Cost of Attendance (per year, $)", min_value=0, max_value=100000, step=500,
            key="coa_per_year_a", on_change=lambda: mark_interaction("coa_per_year_a"),
            help="The full sticker price for your first year (Year 1) at this "
                 "school -- tuition, fees, room & board, books, everything -- "
                 "before subtracting scholarships or what you pay yourself. "
                 "Years 2-4 are projected from this using the estimated COA "
                 "inflation rate. Auto-fills if we found your school above.",
        )
    start_year_a = st.sidebar.selectbox(
        "Year Starting Undergraduate School", _start_year_opts_a,
        key="start_year_a", on_change=lambda: mark_interaction("start_year_a"),
        help="If you won't start college right away, Cost of Attendance "
             "gets projected forward to this year using the estimated COA "
             "inflation rate, before growing further across all 4 years "
             "of enrollment. Leave at the current year for no adjustment.",
    )
    personal_contribution_per_year_a = st.sidebar.number_input(
        "Personal Contribution (per year, $)", min_value=0, max_value=100000, step=500,
        key="personal_contribution_per_year_a", on_change=lambda: mark_interaction("personal_contribution_per_year_a"),
        help="Also called the Student Aid Index (SAI) -- the amount your family "
             "is expected to contribute. Savings or family money toward this "
             "year's cost that you did NOT borrow. Subtracted (with Grants) from "
             "Cost of Attendance to get the loan; it counts in the ROI% "
             "denominator without accruing interest.",
    )
    grants_per_year_a = st.sidebar.number_input(
        "Grants & Scholarships (per year, $)", min_value=0, max_value=100000, step=500,
        key="grants_per_year_a", on_change=lambda: mark_interaction("grants_per_year_a"),
        help="Grant or scholarship aid that reduces what you need to borrow. "
             "This amount does not need to be repaid back to the grantor.",
    )
else:
    # Simplified: inputs hidden; use the seeded / last-known values.
    coa_per_year_a = st.session_state["coa_per_year_a"]
    start_year_a = st.session_state["start_year_a"]
    personal_contribution_per_year_a = st.session_state["personal_contribution_per_year_a"]
    grants_per_year_a = st.session_state["grants_per_year_a"]
# Community-college path: None / full-time transfer / part-time while working.
# (Replaces the old single "Start at community college" checkbox; legacy shared
# links with cc_a=1 map to the full-time transfer mode.)
_legacy_cc_a = get_shared_default("cc_a", "0") == "1"
st.session_state.setdefault(
    "cc_mode_a", get_shared_default("cc_mode_a", "fulltime" if _legacy_cc_a else "none"))
# A career needing no degree has no program for a community college to deliver,
# so the selector is hidden rather than shown with nonsense options
# (cc_path_options(0) would offer "the entire 0-year degree"). Forced to "none"
# so every downstream cc_* derivation stays defined and zero.
#
# A GRADUATE path is hidden for a harder reason than nonsense copy. No community
# college awards a master's, and the clamp below is written for undergraduate
# lengths: at program_years 2 it gave cc_years=2 and university_years=0, pricing
# an entire master's at community-college tuition and financing $0 of it. The
# gate used to be `== 0` alone, so a graduate length walked straight into that.
if program_years_a == 0 or graduate_years_a > 0:
    st.session_state["cc_mode_a"] = "none"
    cc_mode_a = "none"
else:
    _cc_options_a, _cc_labels_a = cc_path_options(program_years_a)
    reconcile_cc_mode("cc_mode_a", _cc_options_a)
    cc_mode_a = st.sidebar.radio(
        "Community college path",
        options=_cc_options_a,
        format_func=lambda c: _cc_labels_a[c],
        key="cc_mode_a", on_change=lambda: mark_interaction("cc_mode_a"),
        help=(
            f"This profession is entered with a {program_years_a}-year degree, which a "
            "community college can award on its own -- so there's no transfer, and "
            "choosing this models the WHOLE program at community-college prices. "
            if program_years_a <= COMMUNITY_COLLEGE_YEARS else
            f"Model the first {COMMUNITY_COLLEGE_YEARS} years at a community "
            "college, then transferring to the 4-year school above to finish the "
            "SAME bachelor's -- earnings and the degree are unchanged, only the "
            "cost of those years drops. "
        ) +
        "Community college is assumed paid without loans "
        "(Pell/work/out-of-pocket), so it adds nothing to your debt. "
        "'Part-time while working' means you work full-time during the "
        "community-college years (earning, not foregoing income) -- its "
        "earnings advantage shows up when 'count foregone earnings' is on. "
        "Put a different path in each scenario to compare them. See Methodology.",
    )
cc_transfer_a = cc_mode_a != "none"
is_parttime_a = cc_mode_a == "parttime"
# Clamped to the program length: a 2-year program done at a community college
# is entirely community college, not 2 years of CC plus a negative number of
# university years.
# Clamp against the UNDERGRADUATE portion only. Graduate years are never
# transferable from a community college, so they must not be eligible to be
# clamped away even if a graduate path ever reaches here.
cc_years_a = (min(COMMUNITY_COLLEGE_YEARS, program_years_a - graduate_years_a)
              if cc_transfer_a else 0)
university_years_a = max(program_years_a - cc_years_a, 0)
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
        key="cc_state_a", on_change=lambda: mark_interaction("cc_state_a"),
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
        key="cc_coa_per_year_a", on_change=lambda: mark_interaction("cc_coa_per_year_a"),
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
    years=program_years_a,
    cc_years=cc_years_a, cc_coa_per_year=effective_cc_coa_per_year_a, finance_cc_years=False)
computed_loan_amount_a = sum(r["loan_amount"] for r in _schedule_a)
cc_oop_a = sum(r["coa"] for r in _schedule_a if r["phase"] == "community_college")
# Federal Direct cap for the cap-and-gap split -- summed annual limits over the
# financed years. Only meaningful in Detailed (Simplified's median debt is
# already federal-only); None there so compute_scenario_results skips the split.
# Undergraduate Direct plus, for a graduate path, the separate graduate Direct
# Unsubsidized capacity. They are different schedules against different
# aggregates, so they are computed apart and added rather than run through one
# table -- see graduate_direct_cap.
# When the whole programme IS the graduate study -- a returning student going
# back for a master's -- there are no undergraduate years in the schedule, so
# federal_direct_cap contributes nothing and the graduate cap is the whole
# capacity. The addition still holds; it is just 0 + graduate.
federal_cap_a = (federal_direct_cap(
                     undergraduate_schedule(_schedule_a, graduate_years_a), loan_dependency)
                 + graduate_direct_cap(graduate_years_a)) if loan_source_a == "personal" else None
# How much of the remainder a PARENT can still borrow federally. Paired with
# federal_cap_a everywhere it travels -- omitting it silently restores the
# pre-OBBBA "gap financing is unlimited" model for that one code path.
plus_cap_a = (parent_plus_cap(_schedule_a, loan_dependency, start_year_a,
                              graduate_years=graduate_years_a)
              if loan_source_a == "personal" else None)
# Foregone-earnings option (widget rendered further down; read from state, per
# this file's established before-the-widget pattern). enrollment_years extends
# the HS baseline; working_years credits the part-time CC years back to the
# major side. Both gate on the option; enrollment_years == UNDERGRAD_YEARS in
# every mode when it's on (cc_years + university_years), so no-CC is unchanged.
_foregone_on = st.session_state.get("count_foregone_earnings", False)
enrollment_years_a = (cc_years_a + university_years_a) if _foregone_on else 0
working_years_a = cc_years_a if (is_parttime_a and _foregone_on) else 0

# Returning students answer the same question the community-college path asks,
# but about their own job rather than a transfer plan, so it overrides
# working_years here rather than adding a parallel mechanism. Keeping the salary
# means the whole enrolment period is worked, so the foregone penalty cancels
# exactly -- which is what the CC part-time path already models.
if is_returning and _foregone_on:
    working_years_a = (enrollment_years_a
                        if st.session_state.get("returning_enrollment") == RETURNING_KEEP_WORKING
                        else 0)
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
        f"{cc_years_a} yrs community college ({_work_note_a}"
        f"{fmt_money(effective_cc_coa_per_year_a)}/yr, no loan → {fmt_money(cc_oop_a)} out-of-pocket)"
        # No transfer clause when the program is short enough to finish at the
        # community college -- a 2-year associate's has no 4-year school to
        # transfer into, and university_years_a is 0.
        + (f", then {university_years_a} yrs at the 4-year school "
           f"({fmt_money(effective_coa_per_year_a)}/yr, financed). "
           if university_years_a else " — the whole program. ")
    )
else:
    cc_note_a = ""
# Simplified takes the loan from the school's reported median debt, so this
# cost-based build-up describes a calculation that isn't driving anything --
# printing it invites the reader to reconcile it against the Total Loan Amount
# just below, two numbers that were never meant to agree. Detailed still shows
# it in full, because there it IS the loan.
#
# The community-college line survives either way: its out-of-pocket total is a
# real cost that enters total_investment in both modes, so suppressing it would
# hide money the visitor actually spends.
if loan_source_a == "personal":
    _loan_note_a = (
        f"{coa_projection_note}"
        f"{cc_note_a}"
        f"→ **{fmt_money(computed_loan_amount_a)}** cost-based loan estimate, **{fmt_money(personal_contribution)}** personal "
        f"(incl. {fmt_money(cc_oop_a)} community college)"
        if cc_transfer_a else
        f"{coa_projection_note}"
        f"Year 1 ({start_year_a}): {fmt_money(effective_coa_per_year_a)} COA − "
        f"{fmt_money(personal_contribution_per_year_a)} personal "
        f"− {fmt_money(grants_per_year_a)} grants → est. {fmt_pct(inflation_rate_a * 100)} COA inflation/yr "
        f"→ over {program_years_a} years: **{fmt_money(computed_loan_amount_a)}** cost-based loan estimate, **{fmt_money(personal_contribution)}** personal"
    )
elif cc_transfer_a:
    # Deliberately NOT cc_note_a: that string ends "...then N yrs at the 4-year
    # school ($X/yr, financed)", which is a Detailed-mode statement. In
    # Simplified nothing is financed at the per-year COA -- the loan is the
    # school's flat reported median debt -- so reusing it would describe a
    # calculation that isn't running. Only the out-of-pocket half survives,
    # because that IS still charged, via total_investment.
    _loan_note_a = (
        f"{cc_years_a} yrs community college ({_work_note_a}"
        f"{fmt_money(effective_cc_coa_per_year_a)}/yr, no loan) → "
        f"**{fmt_money(cc_oop_a)}** paid out of pocket, counted as your personal "
        f"contribution on top of the loan below."
    )
else:
    _loan_note_a = ""
if _loan_note_a:
    st.sidebar.caption(_loan_note_a.replace("$", r"\$"))
# The loan field default follows the active loan source (set by the Loan estimate
# toggle above): the college-reported median debt in Simplified, the cost-based
# personal calculation in Detailed.
#
# loan_basis_a records WHICH of those produced the number, because by the time
# it reaches a caption, a PDF or a Supabase row the figure alone can't say
# whether it was reported as-is, scaled, or built from cost. All three names are
# assigned on every branch so nothing downstream can NameError -- this is a flat
# script, and the PDF call site reads them unconditionally.
reported_debt_a = int(median_debt_a) if median_debt_a is not None else None
simplified_scale_a = 1.0
if program_years_a == 0:
    # No degree, so no college debt to carry -- the school's reported figure
    # describes people who did attend, and none of it applies here. Detailed
    # already lands on 0 through an empty schedule; this is the Simplified
    # equivalent, made explicit rather than left to the median.
    default_loan_a = 0
    loan_basis_a = "no_program"
elif graduate_debt_a:
    # A published graduate median for this school and field. Used as the LOAN,
    # not as a Cost of Attendance: Scorecard measures debt at graduation, which
    # is already net of scholarships, assistantships and family money. Dividing
    # it into a per-year "cost" would claim something it does not measure, and
    # grants would then be subtracted from it a second time. This is the same
    # treatment Simplified mode gives the school-reported median, and it is
    # overridable in the Total Loan Amount field like any other basis.
    #
    # Note it does NOT go through simplified_debt_scale: that scales an
    # institution-wide undergraduate median by a ratio of undergraduate Direct
    # limits, which has nothing to say about graduate borrowing.
    default_loan_a = int(round(graduate_debt_a))
    loan_basis_a = "graduate_reported"
elif loan_source_a == "college":
    simplified_scale_a = simplified_debt_scale(
        program_years_a, predominant_degree_a, loan_dependency)
    default_loan_a = int(round(reported_debt_a * simplified_scale_a))
    loan_basis_a = "reported_scaled" if simplified_scale_a < 1.0 else "reported"
else:
    default_loan_a = int(computed_loan_amount_a)
    loan_basis_a = "cost_based"
# Re-seed the overridable field whenever the active default itself moves -- a
# school change or a mode switch flips it; editing cost/aid moves the personal
# figure only when Detailed is driving. A manual override survives reruns that
# don't move the default (same seen-value guard as before, keyed on the active
# default).
if st.session_state.get("default_loan_a_seen") != default_loan_a:
    st.session_state["default_loan_a_seen"] = default_loan_a
    st.session_state["loan_amount_a"] = default_loan_a
st.session_state.setdefault("loan_amount_a", default_loan_a)
loan_amount = st.sidebar.number_input(
    "Total Loan Amount ($)", min_value=0, max_value=1000000, step=500,
    key="loan_amount_a", on_change=lambda: mark_interaction("loan_amount_a"),
    help="In Simplified mode this is the median debt graduates who borrowed leave "
         "this school with (College Scorecard); in Detailed mode it's the cost-based "
         "total (Cost of Attendance minus Personal Contribution and Grants, over 4 "
         "years). Override with any amount -- e.g. a real financial aid offer -- and "
         "that's used everywhere below.",
)
if loan_basis_a == "no_program":
    st.sidebar.caption(
        "No loan: BLS says this job needs no degree, so there's no tuition to finance. "
        "Type an amount above if you're borrowing for training anyway."
    )
elif loan_basis_a == "reported_scaled":
    # Never attach "the median debt graduates leave with" to a scaled number --
    # that sentence describes the raw figure, and the raw figure is shown.
    st.sidebar.caption((
        f"Estimated: College Scorecard reports {fmt_money(reported_debt_a)} for "
        f"{school_name_a} — institution-wide, across completers of every credential "
        f"length. Scaled to {fmt_money(default_loan_a)} for this {program_years_a}-year "
        "program. An estimate, not a reported figure."
    ).replace("$", r"\$"))
elif loan_source_a == "college":
    st.sidebar.caption((
        f"Simplified: median debt for graduates of {school_name_a} who borrowed "
        f"({fmt_money(default_loan_a)}, College Scorecard). Switch to Detailed to "
        "estimate from your own cost & aid instead."
    ).replace("$", r"\$"))
interest_rate = st.sidebar.number_input(
    "Federal Direct rate (%)", min_value=0.0, max_value=20.0,
    value=get_shared_float("rate", DEFAULT_FEDERAL_RATE), step=0.1,
    # No key on this widget, so it is named explicitly here rather than being
    # picked up with the keyed ones. on_change does not require a key.
    on_change=lambda: mark_interaction("interest_rate_a"),
    help="Rate on federal Direct (Subsidized/Unsubsidized) loans -- the first "
         "~$27k over four years (dependent). 6.5% is a placeholder for the recent "
         "undergraduate Direct rate; it resets every July 1. In Simplified mode "
         "this rate applies to the whole reported federal debt.",
)
# Gap financing rate: only shown/used in Detailed, for need above the federal cap
# (Direct PLUS or private). Simplified reads the seeded value but never uses it.
if loan_source_a == "personal":
    gap_rate_a = st.sidebar.number_input(
        "Gap financing rate (%)", min_value=0.0, max_value=25.0,
        step=0.1, key="gap_rate_a", on_change=lambda: mark_interaction("gap_rate_a"),
        help="Rate on borrowing above the federal Direct cap -- Direct PLUS "
             "(~9% + 4.2% fee) or private/alternative loans. Applied to the "
             "'gap' tranche; the app blends it with the federal rate above.",
    )
else:
    gap_rate_a = st.session_state["gap_rate_a"]
# The income-driven option depends on this scenario's start year, so the list
# changes under the widget when the visitor moves that year. It therefore needs
# a key and reconciliation rather than a bare index: with a key, a stored label
# that is no longer in the options makes Streamlit RAISE (the same trap
# reconcile_cc_mode exists for), and without one, changing the year would reset
# an income-driven choice to Standard -- silently answering a different
# question. resolve_shared_strategy maps income-driven to income-driven.
repayment_strategy_options = repayment_strategy_options_for(start_year_a, enable_legacy_plans)
# Fallback None, NOT a plan name. Defaulting this to "Standard 10-Year" meant a
# visitor with no ?strategy= was treated as having asked for Standard, which the
# successor mapping then turned into Tiered Standard -- so the intended default
# of RAP was unreachable on a fresh visit. Absent means "no preference": take
# the list's first entry.
shared_repayment_strategy = get_shared_default("strategy", None)
st.session_state.setdefault(
    "repayment_strategy_a",
    resolve_shared_strategy(shared_repayment_strategy, repayment_strategy_options))
st.session_state["repayment_strategy_a"] = resolve_shared_strategy(
    st.session_state["repayment_strategy_a"], repayment_strategy_options)
repayment_strategy = st.sidebar.selectbox(
    "Repayment Strategy", repayment_strategy_options,
    key="repayment_strategy_a",
    on_change=lambda: mark_interaction("repayment_strategy_a"),
    help=REPAYMENT_STRATEGY_HELP,
)

# Dependent children, for RAP only -- it reduces the monthly payment by $50 per
# child. One figure for both scenarios, like Dependency status: it is a fact
# about the borrower, not about which school they pick.
#
# Shown only when RAP is actually in play, because no other plan modelled here
# uses it: the IBR-style IDR model has no dependants term, and Standard is flat.
# Scenario B's strategy is read from session_state before its own widget
# renders (the established before-the-widget pattern), so a comparison where
# only B is on RAP still gets the control.
st.session_state.setdefault("rap_dependents", get_shared_int("deps", 0))
_rap_in_use = (repayment_strategy == RAP_STRATEGY_LABEL
               or (st.session_state.get("compare_mode")
                   and st.session_state.get("repayment_strategy_b") == RAP_STRATEGY_LABEL))
if _rap_in_use:
    rap_dependents = st.sidebar.number_input(
        "Dependent children (both scenarios)", min_value=0, max_value=10, step=1,
        key="rap_dependents", on_change=lambda: mark_interaction("rap_dependents"),
        help="RAP lowers your monthly payment by $50 per dependent child. "
             "Counted for RAP only -- no other plan here uses it. Leaving this "
             "at 0 overstates the payment for anyone with children.",
    )
else:
    rap_dependents = st.session_state["rap_dependents"]

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
    on_change=lambda: (mark_interaction("roi_horizon"), log_horizon_change()),
    format_func=lambda y: f"{y} years",
    help="How far into the future every comparison on this page looks. "
         "Careers that train before they earn (medicine, law) look worst at "
         "10 years, because that's mostly training -- try 20 or 30 to see "
         "the payoff those years are buying.",
)

st.sidebar.subheader("💼 Career")

# The wage basis is no longer a sidebar control. It used to be "Career Salary
# Data: National / California", chosen independently of the city -- which made
# California + New York reachable, and the ~51 occupations New York suppresses
# then showed California wages while the page called them national figures
# (Craft Artists: $46,080 nationally, displayed as $100,540). A state is a fact
# about the selected city, not a preference, so it's derived from it.
#
# The national file stays the base that MAJOR_DATA is built from -- it defines
# the dropdown's full 825-occupation list. build_major_data then overlays the
# city's state and the city's metro on top, finest geography winning.
careers_csv_path = CAREERS_CSV_PATH_NATIONAL

# Which question the visitor is asking: "what if I study X?" (Major, NY Fed's
# 73 majors) or "what if I become X?" (Career, BLS's 836 occupations). Seeded
# here and read once so MAJOR_DATA can be built below; the radio itself is the
# first thing rendered in this section, just after that build (see there for
# why it can't move any earlier).
#
# Note this is NOT the read-before-render pattern the career source above uses
# -- resolve_program_years up in Financing does read this key that way, but
# within this section the widget genuinely renders before every control that
# depends on it.
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

# One place the baseline arguments are assembled, spread into every
# compute_scenario_results / find_breakeven_loan call. Threading them by hand
# through five call sites is exactly how hs_wage_index went missing from
# compute_future_plan_result and put a 76% overstatement on screen -- a dict
# spread cannot be half-applied.
def returning_kwargs() -> dict:
    """Baseline + existing-debt arguments, or empty in first-time mode."""
    if not is_returning:
        return {}
    # Existing debt applies as soon as it is entered; the BASELINE only swaps
    # once she has said what she earns. Swapping to a zero baseline would
    # compare the degree against earning nothing and report a spectacular
    # return -- the app would be answering a question she has not been asked yet.
    kwargs = {
        "existing_debt": float(st.session_state.get("existing_debt", 0) or 0),
        "existing_debt_rate": float(st.session_state.get("existing_debt_rate")
                                     if st.session_state.get("existing_debt", 0) else 0) or None,
    }
    if returning_baseline_ready():
        kwargs["baseline_salary_now"] = float(st.session_state["current_salary"])
        kwargs["baseline_salary_in_10y"] = float(st.session_state["salary_no_degree_10y"])
    return kwargs


def _sync_foregone_to_enrollment() -> None:
    """Turn foregone earnings on when the visitor says they'll stop working.

    "I'll stop working" and "count the salary I give up" are the same
    statement, so having one set and the other clear is a contradiction the app
    used to only WARN about -- leaving the biggest cost of going back out of
    every figure unless the visitor found a checkbox in another section.

    Fires on the radio's CHANGE, not on every rerun, and that distinction is
    the whole design. Re-asserting it each pass would make the checkbox
    unusable: untick it and it springs back next rerun. This way it is a
    default that follows the choice, and unticking it afterwards sticks -- at
    which point the existing warning reappears and says what is missing. The
    same reasoning apply_shared_flag uses for a link's flags.

    Deliberately one-directional. Choosing to keep working does NOT switch
    foregone earnings off: a part-time student still gives up some earnings,
    and the CC part-time path uses the same option for the same reason.
    """
    if st.session_state.get("returning_enrollment") == RETURNING_STOP_WORK:
        st.session_state["count_foregone_earnings"] = True


def returning_baseline_ready() -> bool:
    """Both salary answers present. Until then the comparison stays on the
    high-school-graduate baseline and the page says so, rather than silently
    measuring a degree against zero."""
    return bool(st.session_state.get("current_salary", 0)
                and st.session_state.get("salary_no_degree_10y", 0))


def breakeven_kwargs() -> dict:
    """The baseline half only -- find_breakeven_loan solves for NEW borrowing,
    so an existing balance has no place in it."""
    if not is_returning or not returning_baseline_ready():
        return {}
    return {
        "baseline_salary_now": float(st.session_state["current_salary"]),
        "baseline_salary_in_10y": float(st.session_state["salary_no_degree_10y"]),
    }


# City drives the wage dataset now, not just the cost-of-living index, so it
# must be resolved before MAJOR_DATA is built. Its widget renders further
# down (after Target Profession), hence the same read-from-session_state
# -first pattern used for the two controls above.
city_options = list(CITY_DATA.keys())
shared_city = get_shared_default("city", "San Francisco, CA")
st.session_state.setdefault(
    "city_select", shared_city if shared_city in city_options else "San Francisco, CA")

# Seed the metro from the school's state, re-running whenever the school
# changes. Writing city_select here is safe only because its widget is
# instantiated further down (after Target Profession) -- Streamlit raises if a
# key is assigned once its widget exists.
#
# This DOES overwrite a metro the visitor picked deliberately, which is why
# render_inferred_city_note puts a line under the control saying so. The metro
# drives post-graduation wages and cost of living, and "where you study" is
# not "where you work", so the inference is stated rather than silent.
#
# A shared link carries an explicit city that must survive the visit opening
# it: seeding _city_school with that link's school makes the first render
# count as "no change", so the link's city stands.
if "city" in st.query_params:
    st.session_state.setdefault("_city_school", school_name_a)
if st.session_state.get("_city_school") != school_name_a:
    st.session_state["_city_school"] = school_name_a
    _inferred_metro = metro_for_school(coa_match_a)
    if _inferred_metro:
        st.session_state["city_select"] = _inferred_metro
        st.session_state["_city_inferred"] = (school_name_a, _inferred_metro)
    else:
        st.session_state.pop("_city_inferred", None)

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
#
# This write has to happen BEFORE the radio below is instantiated: Streamlit
# raises if a widget's session_state key is assigned to after the widget
# exists. It's the reason the radio renders here rather than immediately after
# the subheader -- everything above it in this section is computation, not
# output, so it still lands first on screen.
if not MAJOR_DATA:
    MAJOR_DATA = build_major_data(careers_csv_path, mode=DATASET_MODE_CAREER, city=city)
    dataset_mode = DATASET_MODE_CAREER
    st.session_state["dataset_mode_radio"] = DATASET_MODE_CAREER

# First control in the Career section: it decides what the dropdown below is
# even a list of, so asking it first matches the order the visitor thinks in
# ("what am I choosing?" then "which one?"). No index= -- session_state already
# holds this widget's value from the setdefault above, and passing both would
# trigger Streamlit's widget-default-conflict warning.
# Rendered before "Choose by" because it changes what the whole section means:
# in returning mode the comparison is against the visitor's own salary, not a
# high school graduate's.
st.sidebar.radio(
    "Who is going to school?", STUDENT_MODE_OPTIONS, key="student_mode_radio",
    on_change=lambda: mark_interaction("student_mode_radio"),
    help="Straight from high school compares against a debt-free high school "
          "graduate. Going back to school compares against your own current "
          "pay -- which is the honest question if you already have a job.",
)

if is_returning:
    # Everything here is something the visitor knows about themselves. The app
    # asks rather than infers, because the alternative is guessing at a career
    # history it has no data for.
    st.sidebar.number_input(
        "Your age now", min_value=18, max_value=80, step=1, key="current_age",
        on_change=lambda: mark_interaction("current_age"),
        help="Used to say when you'd finish repaying -- 'repaid at 63' rather "
              "than 'payoff 14 years'.",
    )
    st.sidebar.number_input(
        "Your salary now ($/yr)", min_value=0, max_value=1_000_000, step=1_000,
        key="current_salary", on_change=lambda: mark_interaction("current_salary"),
        help="What you earn today. This replaces the high-school-graduate "
              "figure as the thing the degree is measured against.",
    )
    st.sidebar.number_input(
        "Your salary in 10 years WITHOUT the degree ($/yr)",
        min_value=0, max_value=1_000_000, step=1_000,
        key="salary_no_degree_10y",
        on_change=lambda: mark_interaction("salary_no_degree_10y"),
        help="Staying put isn't standing still. Leaving this at your current "
              "salary assumes no raises ever, which flatters the degree.",
    )
    st.sidebar.number_input(
        "Existing student debt ($)", min_value=0, max_value=1_000_000, step=1_000,
        key="existing_debt",
        on_change=lambda: mark_interaction("existing_debt"),
        help="Any student loan you already owe. It is added to your monthly "
              "payment and payoff date, but NOT charged against this degree -- "
              "you'd be paying it either way.",
    )
    if st.session_state.get("existing_debt", 0):
        st.sidebar.number_input(
            "Rate on that existing debt (%)", min_value=0.0, max_value=20.0,
            step=0.1, key="existing_debt_rate",
            on_change=lambda: mark_interaction("existing_debt_rate"),
        )
    # Two options, not three, because the model only has two states: whether
    # the salary continues during study. "Part-time" and "evenings/online"
    # would be the same arithmetic under different names, and offering a choice
    # that changes nothing is worse than not offering it.
    #
    # This is the biggest lever on the answer for a returning student -- for
    # someone on $60k, foregone earnings dwarf tuition -- which is exactly why
    # it is asked rather than assumed.
    st.sidebar.radio(
        "While you study, will you keep working?",
        RETURNING_ENROLLMENT_OPTIONS, key="returning_enrollment",
        on_change=lambda: (mark_interaction("returning_enrollment"),
                           _sync_foregone_to_enrollment()),
        help="Stopping work means giving up your salary for the length of the "
              "programme, which is usually the largest single cost of going "
              "back -- larger than tuition.",
    )

    # The radio above only bites when foregone earnings are being counted. If
    # they are off and she plans to stop working, the app is ignoring the
    # largest cost of the decision, and it should say so rather than quietly
    # producing a flattering number.
    if (st.session_state.get("returning_enrollment") == RETURNING_STOP_WORK
            and not st.session_state.get("count_foregone_earnings", True)):
        st.sidebar.warning(
            "You've said you'll stop working, but **foregone earnings are "
            "switched off** below. The salary you'd give up is usually the "
            "biggest cost of going back — these figures leave it out entirely."
        )

    # Says which comparison is actually running. Without this the visitor sees
    # returning-student inputs on screen and assumes the figures already use
    # them, when a blank salary leaves the old baseline in force.
    if not returning_baseline_ready():
        st.sidebar.warning(
            "Enter both salaries above to compare against **your own pay**. "
            "Until then the figures still compare against a debt-free high "
            "school graduate, which understates what this degree has to beat."
        )

dataset_mode = st.sidebar.radio(
    "Choose by", dataset_mode_options, key="dataset_mode_radio", on_change=lambda: mark_interaction("dataset_mode_radio"),
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
# Keyed (and seeded by assignment rather than index=) so the Financing section
# above can read the current selection before this widget renders -- see
# resolve_program_years. Passing both key and index= is what triggers
# Streamlit's widget-default-conflict warning, hence the assignment.
#
# Re-pinned on a MODE SWITCH, not merely when the stored name is absent from
# the new list. A validity check alone isn't enough: a handful of names exist
# in both datasets ("Computer Science" is an NY Fed major and also present in
# the 836-entry career set), so switching Major -> Career would silently leave
# the visitor on their Major-mode pick while the number behind that identical
# label quietly became a different dataset's. Keying on the mode restores what
# index= did before this widget was given a key -- land on the new mode's own
# default.
if (st.session_state.get("major_select_a") not in major_options
        or st.session_state.get("major_select_a_mode") != dataset_mode):
    st.session_state["major_select_a"] = major_options[default_major_index]
st.session_state["major_select_a_mode"] = dataset_mode
major = st.sidebar.selectbox(
    SELECTION_LABEL[dataset_mode], major_options, key="major_select_a",
    on_change=lambda: (mark_interaction("major"), mark_major_explicitly_selected()),
    help="Pick what you're evaluating -- this determines the salary numbers "
         "used everywhere else in the app. Instead of scrolling, click the "
         "box and type part of the name to jump straight to it.",
)
# Which medical/dental/law school, for the paths that attend one. Placed here
# for the same reason as the salary override below -- it needs the chosen
# occupation -- and it must resolve BEFORE
# get_prestige_adjusted_major_name further down rewrites `major` to
# "Family Medicine Physicians (Tier 1)", which no occupation lookup matches.
#
# Hidden in prestige mode: that mode replaces the school with a tier label and
# skips the Scorecard lookup entirely, so there is no school to name.
# The credential radio. Career mode derives it from BLS typical_education and
# needs no input; Major mode has no education field, so it must ask. Rendered
# BEFORE the professional picker so the two can't both claim the same scenario.
# Shown in Major mode (no BLS level exists to derive from) and in returning
# mode (the visitor's schooling is not the occupation's entry requirement).
if (dataset_mode != DATASET_MODE_CAREER or is_returning) and not enable_prestige_mode:
    st.session_state.setdefault(
        "credential_a", get_shared_default("cred", CREDENTIAL_BACHELORS))
    if st.session_state["credential_a"] not in CREDENTIAL_OPTIONS:
        st.session_state["credential_a"] = CREDENTIAL_BACHELORS
    st.sidebar.radio(
        "What are you studying for?", CREDENTIAL_OPTIONS,
        format_func=lambda c: CREDENTIAL_LABELS[c],
        key="credential_a", on_change=lambda: mark_interaction("credential_a"),
        help="What YOU are going back to study -- which need not match the "
             "entry requirement of the job you're aiming at. Going back for a "
             "master's charges the 2 graduate years only, at graduate loan "
             "limits, because you already hold the bachelor's. Doctorate "
             "defaults to 5 years, a placeholder: real programmes run 4 to 8. "
             "See Methodology."
             if is_returning else
             "A master's is modelled as 2 years on top of a bachelor's, a "
             "doctorate as 5 -- so the cost, the foregone earnings and the "
             "loan limits all change. The doctoral figure is a placeholder: "
             "real programmes run 4 to 8 years. See Methodology.",
    )

# Graduate school debt, where the visitor named a school and a field that
# publishes one. Only reachable in Major mode: the lookup is keyed on
# MAJOR_TO_CIP_FAMILY, and app.py has no occupation-to-CIP crosswalk for Career
# mode (it deliberately declines to build one).
_credential_key_a = CREDENTIAL_DATA_KEY.get(_typical_education_a)
_cip_family_a = MAJOR_TO_CIP_FAMILY.get(major) if dataset_mode == DATASET_MODE_MAJOR else None
if _credential_key_a and _cip_family_a and not enable_prestige_mode:
    _grad_options_a = [GRADUATE_SCHOOL_NATIONAL] + graduate_schools_for(
        _cip_family_a, _credential_key_a)
    st.session_state.setdefault("grad_school_a",
                                get_shared_default("grad_school", GRADUATE_SCHOOL_NATIONAL))
    st.session_state.setdefault("_grad_key_a", (_cip_family_a, _credential_key_a))
    if (st.session_state["_grad_key_a"] != (_cip_family_a, _credential_key_a)
            or st.session_state["grad_school_a"] not in _grad_options_a):
        st.session_state["grad_school_a"] = GRADUATE_SCHOOL_NATIONAL
    st.session_state["_grad_key_a"] = (_cip_family_a, _credential_key_a)
    if len(_grad_options_a) > 1:
        st.sidebar.selectbox(
            "Graduate school", _grad_options_a,
            key="grad_school_a", on_change=lambda: mark_interaction("grad_school_a"),
            help="Median debt this school's graduates in your field leave with "
                 "(College Scorecard). Only about a fifth of school-and-field "
                 "combinations publish a figure, so many schools are absent -- "
                 "leave this on the default and enter your own cost instead.",
        )
        # graduate_debt_a was resolved before the financing block; re-reading
        # it here would let the two disagree after a mid-rerun change.
        render_graduate_debt_caption(graduate_debt_a, _credential_key_a,
                                      st.session_state["grad_school_a"], st.sidebar)

_program_key_a = None if enable_prestige_mode else professional_program_for(major)
professional_debt_a = None
if _program_key_a:
    _prof_options_a = [PROFESSIONAL_SCHOOL_NATIONAL] + professional_schools_for(_program_key_a)
    # Re-pin when the program changes, or a medical school rides along into a
    # law scenario -- and worse, a stored value absent from the new options
    # makes Streamlit RAISE on a keyed widget (the reconcile_cc_mode trap).
    st.session_state.setdefault("prof_school_a",
                                get_shared_default("prof_school", PROFESSIONAL_SCHOOL_NATIONAL))
    # Seed the memory from the link's own program so a fresh visit counts as
    # "no change" -- otherwise the re-pin below fires on the first render and
    # discards ?prof_school=, the trap _salary_override_major closes for ?sso=.
    st.session_state.setdefault("_prof_program_a", _program_key_a)
    if (st.session_state["_prof_program_a"] != _program_key_a
            or st.session_state["prof_school_a"] not in _prof_options_a):
        st.session_state["prof_school_a"] = PROFESSIONAL_SCHOOL_NATIONAL
    st.session_state["_prof_program_a"] = _program_key_a
    st.sidebar.selectbox(
        PROFESSIONAL_SCHOOL_LABEL[_program_key_a], _prof_options_a,
        key="prof_school_a", on_change=lambda: mark_interaction("prof_school_a"),
        help="Median debt that this school's graduates leave with, from College "
             "Scorecard. It replaces the national average, and the spread is "
             "wide -- medical school debt runs from about $48,000 to $330,000 "
             "depending on where you go. Leave on the national average if you "
             "don't know yet. See Methodology.",
    )
    professional_debt_a = resolve_professional_debt(major, st.session_state["prof_school_a"])
    render_professional_debt_caption(major, st.session_state["prof_school_a"],
                                      professional_debt_a, st.sidebar)

# Returning students only. Placed here because it needs the chosen major to
# pre-fill from, and it must run BEFORE anything reads MAJOR_DATA's salary --
# apply_starting_salary_override rewrites the entry the whole model reads
# through get_annual_salary_for_year.
if is_returning and major in MAJOR_DATA:
    _bls_start = MAJOR_DATA[major].get("starting_salary", 0)
    st.session_state.setdefault("starting_salary_override", get_shared_int("sso", int(_bls_start)))
    # A shared link's ?sso= must survive the visit that opens it: seed the
    # re-pin's memory with the link's own major so the first render counts as
    # "no change". Without this the setdefault above stores the shared figure
    # and the re-pin immediately overwrites it with the BLS median -- the same
    # trap _city_school exists to close for ?city=.
    if "sso" in st.query_params:
        st.session_state.setdefault("_salary_override_major", major)
    # Re-pin when the major changes, or the previous occupation's figure rides
    # along silently -- the same stale-value trap the major dropdown's mode
    # re-pinning exists to prevent.
    if st.session_state.get("_salary_override_major") != major:
        st.session_state["_salary_override_major"] = major
        st.session_state["starting_salary_override"] = int(_bls_start)
    st.sidebar.number_input(
        "Your expected starting salary ($/yr)",
        min_value=0, max_value=1_000_000, step=1_000,
        key="starting_salary_override",
        on_change=lambda: mark_interaction("starting_salary_override"),
        help="Pre-filled with the BLS figure for this occupation. Change it if "
              "you expect to start somewhere else.",
    )
    st.sidebar.caption(
        f"The {fmt_money(_bls_start)} pre-filled here is what **everyone** in this "
        "occupation earns at entry level — not what someone entering it mid-career "
        "earns in year one. Career-changers commonly start below it, and some never "
        "reach the median. If you have a real offer or a local posting, that number "
        "is better than this one.".replace("$", chr(92) + "$")
    )
    _entered = st.session_state.get("starting_salary_override")
    if _entered and _entered != _bls_start:
        apply_starting_salary_override(major, float(_entered))

typical_education_a = MAJOR_DATA.get(major, {}).get("typical_education", "")
if typical_education_a in MISMODELLED_EDUCATION_LEVELS:
    st.sidebar.caption((
        f"ℹ️ The typical entry-level education for {major} (BLS: "
        f"\"{typical_education_a}\") is below a bachelor's degree. This "
        f"app's Cost of Attendance/loan model below still assumes "
        f"{UNDERGRAD_YEARS} years of undergraduate cost, because BLS doesn't "
        "publish a standard length for this level -- treat the debt figures as "
        "an upper bound."
    ).replace("$", r"\$"))
elif program_years_a == 0:
    # A different statement from the two above: not "we're charging the wrong
    # length" and not "we're charging a shorter one", but "there is nothing to
    # charge". Said plainly, because a $0 loan with no explanation reads as a
    # bug rather than as the answer.
    st.sidebar.caption((
        f"ℹ️ BLS gives the typical entry-level education for {major} as "
        f"\"{typical_education_a}\" -- no degree required. So there's no "
        "tuition, no loan, and no years of foregone wages charged against it. "
        "The salary comparison below still applies."
    ).replace("$", r"\$"))
elif is_graduate_education(typical_education_a):
    # Graduate levels joined PROGRAM_YEARS_BY_EDUCATION when they were given
    # real lengths, which dropped them into the sub-bachelor's branch below --
    # telling a visitor that a Master's degree "is below a bachelor's degree".
    # They need their own sentence, saying the opposite thing.
    st.sidebar.caption((
        f"ℹ️ The typical entry-level education for {major} (BLS: "
        f"\"{typical_education_a}\") is ABOVE a bachelor's, so costs below cover "
        f"{program_years_for_education(typical_education_a)} years -- a bachelor's "
        f"plus {graduate_years_for_education(typical_education_a)} more -- and the "
        "graduate loan limits apply."
    ).replace("$", r"\$"))
elif typical_education_a in PROGRAM_YEARS_BY_EDUCATION:
    # Not a warning: the cost model matches the real program here, so this
    # says what it's charging rather than apologising for what it isn't.
    # Phrased around the name rather than with a possessive or a verb: Career-
    # mode names are plural BLS occupations ("Air Traffic Controllers"), so
    # "{major}'s" renders as "Controllers's" and "{major} typically needs"
    # disagrees in number. "The ... for {major}" sidesteps both.
    st.sidebar.caption((
        f"ℹ️ The typical entry-level education for {major} (BLS: "
        f"\"{typical_education_a}\") is below a bachelor's degree, so costs "
        f"below are modelled over {program_years_for_education(typical_education_a)} "
        f"years rather than {UNDERGRAD_YEARS} -- and the community-college path "
        "covers the whole program."
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
    "City / Metro Area", city_options, key="city_select", on_change=lambda: mark_interaction("city_select"),
    help="Where you plan to live and work after graduating. In Career mode "
         "this sets BOTH the wages (your metro's own BLS figures) and the "
         "cost-of-living adjustment -- so a higher-paying, pricier city can "
         "come out ahead or behind on its own merits. Major mode's wages are "
         "national, since the NY Fed publishes no per-city breakdown.",
)
# Say so when the metro above was set from the school rather than chosen. The
# tuple has to still match the live selection: once the visitor moves the
# control themselves, the value is theirs and this note would be a lie.
if st.session_state.get("_city_inferred") == (school_name_a, city):
    st.sidebar.caption(
        f"Set to {city} because {school_name_a} is there. This is where you'd "
        "*work*, not where you study — change it if you plan to leave. It "
        "re-sets whenever you pick a different school."
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
# No "Career Stage Snapshot" control any more: the Real-World Take-Home
# section renders every stage in CAREER_STAGE_OPTIONS side by side. A radio
# that sampled one stage at a time made the year-1-vs-year-10 comparison
# something you had to toggle for and hold in your head -- nothing on the page
# ever showed both -- and its default sat on Mid-Career, so the harder year was
# the one a visitor had to go looking for. Old share links carrying ?stage= are
# simply ignored; get_shared_default is only consulted for inputs that still
# exist, so a stale key costs nothing.

# Replaces the old National/California radio. It is NOT a widget any more --
# the wage basis follows the selected city -- but the value is still computed,
# for two reasons that both still hold:
#
#  1. It keys the break-even cache. find_breakeven_loan's work depends on the
#     MAJOR_DATA global, which st.cache_data can't see; without an argument
#     that moves when the wages move, the cache would serve a New York
#     break-even to someone who just switched to Austin.
#  2. It's logged to Supabase (career_data_source) and shown in the admin
#     dashboard. The column is retained rather than dropped, with its meaning
#     changed from a chosen source to the derived basis -- see migrations.sql,
#     and treat rows either side of that change as different series.
_career_state = CITY_DATA.get(city, {}).get("state_key")
career_data_source = US_STATES.get(_career_state, "National") if _career_state else "National"

# Rendered last in the sidebar: each flag's current value was already read
# from session_state above (before Financing) so Financing could branch on
# it in time -- see that comment for why. No value= here since
# session_state already holds each widget's value (seeded via setdefault
# above) -- passing both would trigger Streamlit's widget-policy warning.
with st.sidebar.expander("🧪 Advanced Analysis Settings"):
    enable_prestige_mode = st.checkbox(
        "Enable College Prestige & Cost Estimator", key="enable_prestige_mode", on_change=lambda: mark_interaction("enable_prestige_mode"),
        help="Replace the manual school/Cost of Attendance fields above with "
             "a college-tier picker that also applies a modeled (not "
             "guaranteed) salary premium by tier -- see Methodology for "
             "sourcing and caveats.",
    )
    enable_ai_mode = st.checkbox(
        "Enable AI Employability Risk Analysis", key="enable_ai_mode", on_change=lambda: mark_interaction("enable_ai_mode"),
        help="Show a modeled AI task-exposure estimate for your chosen "
             "major's occupation group, based on published research -- see "
             "Methodology.",
    )
    enable_future_proofing = st.checkbox(
        "Enable 2026 Federal Repayment Plans (RAP & Tiered)", key="enable_future_proofing", on_change=lambda: mark_interaction("enable_future_proofing"),
        help="Compare the two real 2026 federal repayment plans side by side -- "
             "the Repayment Assistance Plan (RAP) and the Tiered Standard Plan, "
             "both effective July 1, 2026. See Methodology.",
    )
    enable_legacy_plans = st.checkbox(
        "Compare against pre-2026 repayment plans", key="enable_legacy_plans",
        on_change=lambda: mark_interaction("enable_legacy_plans"),
        help="Adds Standard 10-Year and IBR-style Income-Driven Repayment back "
             "to the Repayment Strategy dropdown. Both are closed to loans "
             "originated on or after July 1, 2026, so they are a comparison "
             "against the old rules -- not plans you could choose. Off by "
             "default for that reason.",
    )
    enable_foregone_earnings = st.checkbox(
        "Count foregone earnings during enrollment", key="count_foregone_earnings", on_change=lambda: mark_interaction("count_foregone_earnings"),
        help=f"Charge the ~{UNDERGRAD_YEARS} years of wages a student gives up "
             "while enrolled full-time -- usually the single largest real cost "
             "of a degree, bigger than tuition -- against the degree. "
             + ("Your current salary is credited for those years, so the "
                "comparison starts today rather than at graduation. "
                if is_returning else
                "The debt-free high school graduate is credited with those "
                "head-start years of income, so every path is compared from "
                "age 18 rather than from graduation. ")
             + "This lowers each degree's earnings premium and "
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
    f"""
    <script>
    (function() {{
        {FIND_APP_FRAME_JS}
        // The listener stays on window.top: keystrokes are dispatched on the
        // outermost page the visitor is actually focused in, so a listener any
        // lower never sees them. The BUTTON is a different matter -- it lives
        // in the Streamlit app's DOM, one frame down from the wrapper, so
        // looking for it on window.top.document found nothing on the deployed
        // app and Ctrl+Shift+A silently did nothing there. Same snippet, two
        // different frames, on purpose.
        window.top.document.addEventListener("keydown", function (e) {{
            if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === "a") {{
                e.preventDefault();
                clickAppButton("Reveal Admin Panel");
            }}
        }});
    }})();
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
    "🔀 Compare Two Scenarios", key="compare_mode",
    on_change=lambda: (mark_interaction("compare_toggle"), log_compare_toggle()),
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
        # Same mode-switch re-pin as Scenario A above, and for the same reason
        # -- a name valid in both datasets would otherwise survive a switch and
        # change meaning underneath the visitor. Also drops index=, which was
        # being passed alongside key= and triggering Streamlit's
        # widget-default-conflict warning.
        if (st.session_state.get("major_b") not in major_options
                or st.session_state.get("major_b_mode") != dataset_mode):
            st.session_state["major_b"] = major_options[default_major_b_index]
        st.session_state["major_b_mode"] = dataset_mode
        major_b = st.selectbox(
            SELECTION_LABEL[dataset_mode], major_options, key="major_b", on_change=lambda: mark_interaction("major_b"),
            help="Pick the career you're evaluating -- this determines the "
                 "salary numbers used everywhere else in the app. There are "
                 "hundreds of options, so instead of scrolling, click the "
                 "box and type part of your major or career to jump "
                 "straight to it.",
        )
        # Scenario B's own professional school -- A and B can be different
        # careers, so it cannot share A's picker. Same placement rule: before
        # get_prestige_adjusted_major_name rewrites major_b further down.
        _program_key_b = None if enable_prestige_mode else professional_program_for(major_b)
        professional_debt_b = None
        if _program_key_b:
            _prof_options_b = [PROFESSIONAL_SCHOOL_NATIONAL] + professional_schools_for(_program_key_b)
            st.session_state.setdefault(
                "prof_school_b",
                get_shared_default("prof_school_b", PROFESSIONAL_SCHOOL_NATIONAL))
            st.session_state.setdefault("_prof_program_b", _program_key_b)
            if (st.session_state["_prof_program_b"] != _program_key_b
                    or st.session_state["prof_school_b"] not in _prof_options_b):
                st.session_state["prof_school_b"] = PROFESSIONAL_SCHOOL_NATIONAL
            st.session_state["_prof_program_b"] = _program_key_b
            st.selectbox(
                PROFESSIONAL_SCHOOL_LABEL[_program_key_b], _prof_options_b,
                key="prof_school_b", on_change=lambda: mark_interaction("prof_school_b"),
                help="Median debt this school's graduates leave with (College "
                     "Scorecard). Comparing two schools for the same career is "
                     "what this control is for.",
            )
            professional_debt_b = resolve_professional_debt(
                major_b, st.session_state["prof_school_b"])
            render_professional_debt_caption(
                major_b, st.session_state["prof_school_b"], professional_debt_b)

        typical_education_b = MAJOR_DATA.get(major_b, {}).get("typical_education", "")
        if typical_education_b in MISMODELLED_EDUCATION_LEVELS:
            st.caption((
                f"ℹ️ The typical entry-level education for {major_b} (BLS: "
                f"\"{typical_education_b}\") is below a bachelor's degree. "
                f"This app's Cost of Attendance/loan model below still "
                f"assumes {UNDERGRAD_YEARS} years of undergraduate cost, because "
                "BLS doesn't publish a standard length for this level -- treat "
                "the debt figures as an upper bound."
            ).replace("$", r"\$"))
        elif program_years_b == 0:
            st.caption((
                f"ℹ️ BLS gives the typical entry-level education for {major_b} as "
                f"\"{typical_education_b}\" -- no degree required, so no tuition, "
                "loan or foregone wages are charged against it."
            ).replace("$", r"\$"))
        elif is_graduate_education(typical_education_b):
            st.caption((
                f"ℹ️ The typical entry-level education for {major_b} (BLS: "
                f"\"{typical_education_b}\") is ABOVE a bachelor's, so costs cover "
                f"{program_years_for_education(typical_education_b)} years -- a "
                f"bachelor's plus "
                f"{graduate_years_for_education(typical_education_b)} more."
            ).replace("$", r"\$"))
        elif typical_education_b in PROGRAM_YEARS_BY_EDUCATION:
            st.caption((
                f"ℹ️ The typical entry-level education for {major_b} (BLS: "
                f"\"{typical_education_b}\") is below a bachelor's degree, so "
                f"costs below are modelled over "
                f"{program_years_for_education(typical_education_b)} years "
                f"rather than {UNDERGRAD_YEARS}."
            ).replace("$", r"\$"))

        st.subheader("💰 Financing")
        if enable_prestige_mode:
            shared_tier_b = get_shared_default("prestige_tier_b", prestige_tier_options[0])
            default_tier_b_index = (
                prestige_tier_options.index(shared_tier_b) if shared_tier_b in prestige_tier_options else 0
            )
            prestige_tier_b = st.selectbox(
                "College Tier Selection", prestige_tier_options, index=default_tier_b_index, key="prestige_tier_b", on_change=lambda: mark_interaction("prestige_tier_b"),
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
                on_change=lambda: (mark_interaction("school_b"),
                                    _autofill_coa("school_search_b", "school_pick_b", "in_state_b", "coa_per_year_b")),
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
                    format_func=lambda u: school_option_label(u, load_coa_dataset()),
                    on_change=lambda: (mark_interaction("school_b"),
                                    _autofill_coa("school_search_b", "school_pick_b", "in_state_b", "coa_per_year_b")),
                )
            school_name_b = _resolve_school_name("school_search_b", "school_pick_b")
            school_unitid_b = _resolve_school_unitid("school_search_b", "school_pick_b")

            in_state_b = st.checkbox(
                "In-State Student?", value=get_shared_default("in_state_b", "1") == "1", key="in_state_b",
                on_change=lambda: (mark_interaction("school_b"),
                                    _autofill_coa("school_search_b", "school_pick_b", "in_state_b", "coa_per_year_b")),
                help="Check this if you'd pay in-state tuition at the school "
                     "above. Changes the auto-filled Cost of Attendance and how "
                     "fast tuition is estimated to grow each year.",
            )
            coa_match_b = (find_school_coa(school_name_b, load_coa_dataset(), unitid=school_unitid_b)
                            if school_name_b else None)
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
            # (COA widget renders below, inside the loan-source-gated block.)
        # ---- Scenario B loan source (uses the global Loan estimate toggle) ----
        # Scenario B honors the same effective_loan_mode set in Scenario A's
        # section, but resolves college-vs-personal against its OWN reported debt,
        # so B falls back to Detailed inputs when its school has no figure even
        # while A stays Simplified.
        _debt_lookup_b = {} if enable_prestige_mode else (fetch_median_debt(school_name_b, scorecard_api_key) or {})
        median_debt_b = _debt_lookup_b.get("median_debt")
        predominant_degree_b = _debt_lookup_b.get("predominant_degree")
        loan_source_b = "college" if (effective_loan_mode == "Simplified" and median_debt_b is not None) else "personal"
        _start_year_opts_b = list(range(now_local().year, now_local().year + 8))
        st.session_state.setdefault("start_year_b", get_shared_int("start_year_b", now_local().year))
        if st.session_state["start_year_b"] not in _start_year_opts_b:
            st.session_state["start_year_b"] = now_local().year
        st.session_state.setdefault("personal_contribution_per_year_b", get_shared_int("pc_b", 0))
        st.session_state.setdefault("grants_per_year_b", get_shared_int("grants_b", 0))
        st.session_state.setdefault("gap_rate_b", get_shared_float("gap_rate_b", DEFAULT_GAP_RATE))
        # See Scenario A: re-affirm so hiding these inputs (Simplified) doesn't
        # let Streamlit garbage-collect the values and reset COA to 0 on the
        # next Detailed render.
        for _k in ("coa_per_year_b", "start_year_b",
                   "personal_contribution_per_year_b", "grants_per_year_b", "gap_rate_b"):
            if _k in st.session_state:
                st.session_state[_k] = st.session_state[_k]
        if loan_source_b == "personal":
            if not enable_prestige_mode:
                coa_per_year_b = st.number_input(
                    "Cost of Attendance (per year, $)", min_value=0, max_value=100000, step=500,
                    key="coa_per_year_b", on_change=lambda: mark_interaction("coa_per_year_b"),
                    help="The full sticker price for your first year (Year 1) at "
                         "this school -- tuition, fees, room & board, books, "
                         "everything -- before subtracting scholarships or what "
                         "you pay yourself. Years 2-4 are projected from this "
                         "using the estimated COA inflation rate. "
                         "Auto-fills if we found your school above.",
                )
            start_year_b = st.selectbox(
                "Year Starting Undergraduate School", _start_year_opts_b,
                key="start_year_b", on_change=lambda: mark_interaction("start_year_b"),
                help="If you won't start college right away, Cost of Attendance "
                     "gets projected forward to this year using the estimated COA "
                     "inflation rate, before growing further across all 4 years "
                     "of enrollment. Leave at the current year for no adjustment.",
            )
            personal_contribution_per_year_b = st.number_input(
                "Personal Contribution (per year, $)", min_value=0, max_value=100000, step=500,
                key="personal_contribution_per_year_b", on_change=lambda: mark_interaction("personal_contribution_per_year_b"),
                help="Also called the Student Aid Index (SAI) -- the amount your "
                     "family is expected to contribute. Savings or family money "
                     "toward this year's cost that wasn't borrowed. Subtracted "
                     "(with Grants) from Cost of Attendance to get the loan.",
            )
            grants_per_year_b = st.number_input(
                "Grants & Scholarships (per year, $)", min_value=0, max_value=100000, step=500,
                key="grants_per_year_b", on_change=lambda: mark_interaction("grants_per_year_b"),
                help="Grant or scholarship aid that reduces what you need to "
                     "borrow. This amount does not need to be repaid back to "
                     "the grantor.",
            )
        else:
            # Simplified: inputs hidden; use the seeded / last-known values.
            coa_per_year_b = st.session_state["coa_per_year_b"]
            start_year_b = st.session_state["start_year_b"]
            personal_contribution_per_year_b = st.session_state["personal_contribution_per_year_b"]
            grants_per_year_b = st.session_state["grants_per_year_b"]
        _legacy_cc_b = get_shared_default("cc_b", "0") == "1"
        st.session_state.setdefault(
            "cc_mode_b", get_shared_default("cc_mode_b", "fulltime" if _legacy_cc_b else "none"))
        # Hidden at zero program years, and for graduate paths -- see Scenario A
        # for why the second one matters (the clamp zeroes the loan).
        if program_years_b == 0 or graduate_years_b > 0:
            st.session_state["cc_mode_b"] = "none"
            cc_mode_b = "none"
        else:
            _cc_options_b, _cc_labels_b = cc_path_options(program_years_b)
            reconcile_cc_mode("cc_mode_b", _cc_options_b)
            cc_mode_b = st.radio(
                "Community college path",
                options=_cc_options_b,
                format_func=lambda c: _cc_labels_b[c],
                key="cc_mode_b", on_change=lambda: mark_interaction("cc_mode_b"),
                help=(
                    f"This profession is entered with a {program_years_b}-year degree, "
                    "which a community college can award on its own -- no transfer, so "
                    "this models the WHOLE program at community-college prices. "
                    if program_years_b <= COMMUNITY_COLLEGE_YEARS else
                    f"Model the first {COMMUNITY_COLLEGE_YEARS} years at a "
                    "community college, then transferring to finish the SAME "
                    "bachelor's. "
                ) +
                "Community college is assumed paid without loans, "
                "so it adds nothing to the debt. 'Part-time while working' "
                "means you work full-time during the community-college years. "
                "See Methodology.",
            )
        cc_transfer_b = cc_mode_b != "none"
        is_parttime_b = cc_mode_b == "parttime"
        cc_years_b = (min(COMMUNITY_COLLEGE_YEARS, program_years_b - graduate_years_b)
                      if cc_transfer_b else 0)
        university_years_b = max(program_years_b - cc_years_b, 0)
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
                key="cc_state_b", on_change=lambda: mark_interaction("cc_state_b"),
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
                key="cc_coa_per_year_b", on_change=lambda: mark_interaction("cc_coa_per_year_b"),
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
            years=program_years_b,
            cc_years=cc_years_b, cc_coa_per_year=effective_cc_coa_per_year_b, finance_cc_years=False)
        computed_loan_amount_b = sum(r["loan_amount"] for r in _schedule_b)
        cc_oop_b = sum(r["coa"] for r in _schedule_b if r["phase"] == "community_college")
        federal_cap_b = (federal_direct_cap(
                             undergraduate_schedule(_schedule_b, graduate_years_b), loan_dependency)
                         + graduate_direct_cap(graduate_years_b)
                         if loan_source_b == "personal" else None)
        plus_cap_b = (parent_plus_cap(_schedule_b, loan_dependency, start_year_b,
                                      graduate_years=graduate_years_b)
                      if loan_source_b == "personal" else None)
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
                f"{cc_years_b} yrs community college ({_work_note_b}"
                f"{fmt_money(effective_cc_coa_per_year_b)}/yr, no loan → {fmt_money(cc_oop_b)} out-of-pocket)"
                + (f", then {university_years_b} yrs at the 4-year school "
                   f"({fmt_money(effective_coa_per_year_b)}/yr, financed). "
                   if university_years_b else " — the whole program. ")
            )
        else:
            cc_note_b = ""
        # Same rule as Scenario A -- see there for why.
        if loan_source_b == "personal":
            _loan_note_b = (
                f"{coa_projection_note_b}"
                f"{cc_note_b}"
                f"→ **{fmt_money(computed_loan_amount_b)}** cost-based loan estimate, **{fmt_money(personal_contribution_b)}** personal "
                f"(incl. {fmt_money(cc_oop_b)} community college)"
                if cc_transfer_b else
                f"{coa_projection_note_b}"
                f"Year 1 ({start_year_b}): {fmt_money(effective_coa_per_year_b)} COA − "
                f"{fmt_money(personal_contribution_per_year_b)} personal "
                f"− {fmt_money(grants_per_year_b)} grants → est. {fmt_pct(inflation_rate_b * 100)} COA inflation/yr "
                f"→ over {program_years_b} years: **{fmt_money(computed_loan_amount_b)}** cost-based loan estimate, **{fmt_money(personal_contribution_b)}** personal"
            )
        elif cc_transfer_b:
            # See Scenario A for why this doesn't reuse cc_note_b.
            _loan_note_b = (
                f"{cc_years_b} yrs community college ({_work_note_b}"
                f"{fmt_money(effective_cc_coa_per_year_b)}/yr, no loan) → "
                f"**{fmt_money(cc_oop_b)}** paid out of pocket, counted as your personal "
                f"contribution on top of the loan below."
            )
        else:
            _loan_note_b = ""
        if _loan_note_b:
            st.caption(_loan_note_b.replace("$", r"\$"))
        # Mirrors Scenario A: default to the college-reported median debt when
        # available, else the cost-based personal calc. See A's block for why
        # the seen-guard is keyed on the active default. loan_source_b was already
        # resolved above (from the global toggle + B's own reported debt); here we
        # just pick the matching default figure.
        # Mirrors Scenario A -- see there for why all three names are assigned
        # on every branch.
        reported_debt_b = int(median_debt_b) if median_debt_b is not None else None
        simplified_scale_b = 1.0
        if program_years_b == 0:
            default_loan_b = 0
            loan_basis_b = "no_program"
        elif loan_source_b == "college":
            simplified_scale_b = simplified_debt_scale(
                program_years_b, predominant_degree_b, loan_dependency)
            default_loan_b = int(round(reported_debt_b * simplified_scale_b))
            loan_basis_b = "reported_scaled" if simplified_scale_b < 1.0 else "reported"
        else:
            default_loan_b = int(computed_loan_amount_b)
            loan_basis_b = "cost_based"
        if st.session_state.get("default_loan_b_seen") != default_loan_b:
            st.session_state["default_loan_b_seen"] = default_loan_b
            st.session_state["loan_amount_b"] = default_loan_b
        st.session_state.setdefault("loan_amount_b", default_loan_b)
        loan_amount_b = st.number_input(
            "Total Loan Amount ($)", min_value=0, max_value=1000000, step=500,
            key="loan_amount_b", on_change=lambda: mark_interaction("loan_amount_b"),
            help="In Simplified mode this is the median debt graduates who borrowed "
                 "leave this school with (College Scorecard); in Detailed mode it's "
                 "the cost-based total (Cost of Attendance minus Personal Contribution "
                 "and Grants, over 4 years). Override with any amount -- e.g. a real "
                 "financial aid offer -- used everywhere below.",
        )
        if loan_basis_b == "no_program":
            st.caption(
                "No loan: BLS says this job needs no degree, so there's no tuition "
                "to finance."
            )
        elif loan_basis_b == "reported_scaled":
            st.caption((
                f"Estimated: College Scorecard reports {fmt_money(reported_debt_b)} for "
                f"{school_name_b} — institution-wide, across every credential length. "
                f"Scaled to {fmt_money(default_loan_b)} for this {program_years_b}-year "
                "program. An estimate, not a reported figure."
            ).replace("$", r"\$"))
        elif loan_source_b == "college":
            st.caption((
                f"Simplified: median debt for graduates of {school_name_b} who borrowed "
                f"({fmt_money(default_loan_b)}, College Scorecard). Switch to Detailed to "
                "estimate from your own cost & aid instead."
            ).replace("$", r"\$"))
        if enable_prestige_mode:
            major_b = get_prestige_adjusted_major_name(major_b, prestige_tier_b)
        interest_rate_b = st.number_input(
            "Federal Direct rate (%)", min_value=0.0, max_value=20.0,
            value=get_shared_float("rate_b", DEFAULT_FEDERAL_RATE), step=0.1,
            key="interest_rate_b", on_change=lambda: mark_interaction("interest_rate_b"),
            help="Rate on federal Direct loans (the first ~$27k over four years, "
                 "dependent). 6.5% is a placeholder for the recent undergraduate "
                 "Direct rate; it resets every July 1.",
        )
        if loan_source_b == "personal":
            gap_rate_b = st.number_input(
                "Gap financing rate (%)", min_value=0.0, max_value=25.0,
                step=0.1, key="gap_rate_b", on_change=lambda: mark_interaction("gap_rate_b"),
                help="Rate on borrowing above the federal Direct cap -- Direct "
                     "PLUS or private/alternative loans. Blended with the federal "
                     "rate above.",
            )
        else:
            gap_rate_b = st.session_state["gap_rate_b"]
        # Scenario B's own start year decides its options -- it used to reuse
        # Scenario A's list, so a B starting in 2025 was offered whatever plan
        # A's year implied. Same reconcile-before-the-widget treatment as A.
        repayment_strategy_options_b = repayment_strategy_options_for(start_year_b, enable_legacy_plans)
        shared_repayment_strategy_b = get_shared_default("strategy_b", None)
        st.session_state.setdefault(
            "repayment_strategy_b",
            resolve_shared_strategy(shared_repayment_strategy_b, repayment_strategy_options_b))
        st.session_state["repayment_strategy_b"] = resolve_shared_strategy(
            st.session_state["repayment_strategy_b"], repayment_strategy_options_b)
        repayment_strategy_b = st.selectbox(
            "Repayment Strategy", repayment_strategy_options_b,
            key="repayment_strategy_b", on_change=lambda: mark_interaction("repayment_strategy_b"),
            help=REPAYMENT_STRATEGY_HELP,
        )


# ---- 2m. Repayment comparison for an EXISTING balance ------------------
# Defined here rather than beside the other section-5 renderers because
# ?tool=repayment serves this on its own and has to call it before the
# calculator renders. Pure helpers -- no scenario, no module globals.

# Payments already made under ANY income-driven plan carry forward INTO RAP,
# but RAP payments do not generally carry back OUT. studentaid.gov:
#
#   "If you change from one IDR plan to another, your repayment period might
#    also change. For example, if you're enrolled in the PAYE Plan, which has a
#    20-year repayment period, and you subsequently enroll in RAP, which has a
#    30-year repayment period, then your payments under the PAYE Plan will count
#    toward discharge under RAP, but your repayment period would increase from
#    20 to 30 years."
#
#   "...payments made under RAP won't count toward discharge under the IBR, ICR
#    or PAYE plans, with the following exception: If the monthly payment amount
#    while under RAP is greater than or equal to the 10-year Standard Repayment
#    Plan monthly payment amount, then the month can count toward the IBR, ICR,
#    and PAYE plans."
#
# The second rule is the one worth putting on screen. It is written so it
# almost never fires for the borrowers RAP is aimed at: the low payment that
# makes RAP attractive is exactly what fails the >= 10-year-Standard test. So
# the lower the income, the more nearly irreversible the switch -- the opposite
# of what "there's an exception" sounds like.
def rap_months_counting_back(rap_result: dict, standard_monthly: float) -> dict:
    """How many RAP months would still count toward IBR/ICR/PAYE if the
    borrower switched back, by the >= 10-year-Standard-payment test.

    Returns counts rather than a verdict: "0 of 360" is the finding, and it is
    a much sharper statement than "switching may be irreversible".
    """
    schedule = rap_result.get("schedule")
    if schedule is None or schedule.empty or "payment" not in schedule.columns:
        return {"counting": 0, "total": 0, "share": 0.0}
    total = int(len(schedule))
    counting = int((schedule["payment"] >= standard_monthly - 0.005).sum())
    return {"counting": counting, "total": total,
            "share": counting / total if total else 0.0}


def compare_existing_loan_plans(balance: float, rate: float, annual_income: float,
                                 dependents: int = 0, forgivable: bool = True,
                                 starting_interest: float = 0.0,
                                 pslf: bool = False,
                                 prior_payments: int = 0) -> list:
    """Every repayment plan a borrower with an EXISTING balance could be on.

    Pure computation, no Streamlit, so it can be tested directly -- and it
    reuses the same four simulators the prospective side uses rather than
    growing a second amortisation. The only reason it needs new code at all is
    that those simulators derived income from a major; income_for_year now lets
    them take a salary instead.

    `forgivable` is False for Parent PLUS, which is not eligible for RAP or
    IBR at all. Returning the rows anyway with a note would invite a borrower
    to compare against plans they cannot join.
    """
    # Under PSLF the income-driven plans stop at 120 payments instead of running
    # their full 20- or 30-year term. That is the entire mechanism: same payment,
    # same accrual, the balance is simply written off ten years in.
    idr_term = PSLF_QUALIFYING_YEARS if pslf else IDR_MAX_TERM_YEARS
    rap_term = PSLF_QUALIFYING_YEARS if pslf else RAP_MAX_TERM_YEARS

    # Qualifying payments already made shorten what is LEFT, on the
    # income-driven rows only. The fixed-term plans are unaffected: Standard,
    # Extended and Tiered forgive nothing, so there is no clock to have made
    # progress against -- their term is just how long the balance takes to
    # amortise, and the balance entered is already net of what has been paid.
    #
    # Under PSLF the same subtraction applies to the 120-payment count, which
    # is why this is computed from the term resolved above rather than from
    # the constants.
    prior = max(int(prior_payments or 0), 0)
    idr_months = max(idr_term * 12 - prior, 0)
    rap_months = max(rap_term * 12 - prior, 0)

    rows = []
    std = calculate_standard_repayment(balance, rate, STANDARD_TERM_YEARS)
    rows.append(("Standard (10-year)", std,
                 "Qualifies for PSLF — but it also clears the loan in exactly 120 "
                 "payments, so there is nothing left to forgive."
                 if pslf else "Fixed payment. No forgiveness."))
    ext = calculate_standard_repayment(balance, rate, EXTENDED_STANDARD_TERM_YEARS)
    rows.append((f"Extended Standard ({EXTENDED_STANDARD_TERM_YEARS}-year)", ext,
                 "Does NOT qualify for PSLF." if pslf else
                 "Fixed payment stretched out. No forgiveness, more interest."))
    tiered_term = calculate_tiered_standard_term(balance)
    tiered = calculate_standard_repayment(balance, rate, tiered_term)
    rows.append((f"2026 Tiered Standard ({tiered_term}-year)", tiered,
                 "Does NOT qualify for PSLF, or even for TEPSLF." if pslf else
                 "Fixed payment over a term set by your balance."))
    if forgivable:
        rap = simulate_rap_schedule(balance, rate, None, dependents,
                                     annual_income=annual_income,
                                     max_term_years=rap_term,
                                     max_months=rap_months)
        # The count-back finding is deliberately NOT put in this cell. The
        # "What it is" column is the last one in a six-column dataframe and
        # Streamlit clips it -- the row renders as "...forgiven at 30 y" with
        # the warning invisible. It is surfaced below the table instead, by
        # render_existing_loan_comparison calling rap_months_counting_back.
        rows.append(("Repayment Assistance Plan (RAP)", rap,
                     f"Qualifies. Unpaid interest waived, remainder forgiven at "
                     f"{PSLF_QUALIFYING_PAYMENTS} payments." if pslf else
                     f"1-10% of total income, minimum ${RAP_MIN_PAYMENT}/month. "
                     "Unpaid interest waived. Remainder forgiven at 30 years."))
        idr = calculate_idr_repayment(balance, rate, None, annual_income=annual_income,
                                       starting_interest=starting_interest,
                                       max_term_years=idr_term,
                                       max_months=idr_months)
        rows.append(("IBR-style income-driven", idr,
                     f"Qualifies. Remainder forgiven at {PSLF_QUALIFYING_PAYMENTS} "
                     "payments." if pslf else
                     "10% of income above a $22,000 allowance. Forgiven at 20 years. "
                     "Closed to loans originated on or after July 1, 2026."))
    return rows


def render_existing_loan_comparison(always_open: bool = False) -> None:
    """Plan comparison for someone already in repayment.

    A different question from the rest of the app, which asks whether a degree
    is worth borrowing for. This one takes the borrowing as given and asks what
    to do about it -- so it lives in its own expander, takes its own inputs and
    shares none of the scenario machinery.
    """
    # Open by default on its own page: a visitor who followed a link TO this
    # tool should not have to click to reach it.
    with st.expander("💸 Already have loans? Compare repayment plans",
                     expanded=always_open):
        st.caption(
            "For a balance you already owe. Everything else on this page is "
            "about whether to borrow in the first place — this is about what "
            "to do once you have."
        )
        c1, c2, c3 = st.columns(3)
        balance = c1.number_input("Current balance ($)", min_value=0, max_value=2_000_000,
                                   step=1_000, key="existing_balance")
        rate = c2.number_input("Interest rate (%)", min_value=0.0, max_value=20.0,
                                step=0.1, key="existing_rate")
        income = c3.number_input("Your annual income ($)", min_value=0, max_value=2_000_000,
                                  step=1_000, key="existing_income",
                                  help="Adjusted gross income. The income-driven plans "
                                       "size their payment from it; the fixed plans ignore it.")
        c4, c5, c6 = st.columns(3)
        deps = c4.number_input("Dependent children", min_value=0, max_value=10, step=1,
                                key="existing_dependents",
                                help="RAP lowers the payment by $50/month each.")
        accrued = c5.number_input(
            "of which unpaid interest ($)", min_value=0, max_value=2_000_000, step=1_000,
            key="existing_accrued_interest",
            help="If your servicer shows principal and accrued interest separately "
                 "-- common after a period on SAVE, where interest never "
                 "capitalised -- put the interest part here. It changes how "
                 "payments are applied and is shown separately on the balance chart. "
                 "Leave at 0 if you only know one number.")
        forgivable = c6.checkbox(
            "These are my own federal Direct loans", value=True, key="existing_forgivable",
            help="Untick for Parent PLUS or private loans. Parent PLUS is not "
                 "eligible for RAP or IBR, and private loans are outside the "
                 "federal system entirely, so the income-driven rows are hidden.")

        pslf = st.checkbox(
            "I work full-time for a government or 501(c)(3) employer (PSLF)",
            key="existing_pslf", disabled=not forgivable,
            help="Public Service Loan Forgiveness writes off whatever is left after "
                 f"{PSLF_QUALIFYING_PAYMENTS} qualifying monthly payments -- ten years, "
                 "and they need not be consecutive. Only Direct Loans qualify, so this "
                 "is unavailable for Parent PLUS and private loans.")
        if not forgivable:
            st.caption(
                "PSLF is unavailable here: it covers Direct Loans only. Parent PLUS "
                "for parents — and any consolidation containing one — cannot qualify."
            )

        # Months, not years: servicers report a qualifying-payment COUNT, and
        # rounding it to a year moves forgiveness by up to eleven payments.
        prior_payments = st.number_input(
            "Qualifying payments already made (months)", min_value=0, max_value=480,
            step=1, key="existing_prior_payments", disabled=not forgivable,
            help="From your servicer or the IDR tracker on StudentAid.gov. "
                 "Payments you made under ANY income-driven plan count toward "
                 "discharge under RAP, so they shorten what is left — the "
                 "income-driven rows below already subtract them. Leave at 0 if "
                 "you have never been on an income-driven plan, or don't know. "
                 "The fixed-term plans ignore this: they forgive nothing, so "
                 "there is no clock to have made progress against.")

        if not balance or not rate:
            st.info("Enter a balance and a rate to compare plans.")
            return

        # A payment count that has already reached a plan's term means the
        # balance is dischargeable now, not that it pays off in six weeks.
        # Without this the row reads "0.1 yrs" -- an artefact of the single
        # closing row the simulators emit so the balance chart has something to
        # draw, not a finding.
        if forgivable and prior_payments >= IDR_MAX_TERM_YEARS * 12:
            st.warning(
                f"You've entered {prior_payments} qualifying payments, which is "
                f"already at or past the {IDR_MAX_TERM_YEARS}-year IBR/ICR/PAYE "
                f"term ({IDR_MAX_TERM_YEARS * 12} payments)"
                + (f" and the {RAP_MAX_TERM_YEARS}-year RAP term "
                   f"({RAP_MAX_TERM_YEARS * 12})."
                   if prior_payments >= RAP_MAX_TERM_YEARS * 12 else ".")
                + " If those payments qualified, the remaining balance should "
                "already be dischargeable — the near-zero payoff figures below "
                "are that, not a real repayment period. Check your count with "
                "your servicer rather than acting on this page."
            )

        # Counted here, not when the expander renders. Reaching this line means
        # a balance AND a rate were entered and a comparison is on screen --
        # opening an expander to look is not "using the module", and counting
        # that would make the figure meaningless.
        mark_interaction("module_repayment_comparison")
        rows = compare_existing_loan_plans(balance, rate, income, deps, forgivable,
                                            starting_interest=accrued,
                                            pslf=pslf and forgivable,
                                            prior_payments=prior_payments)
        st.dataframe(pd.DataFrame([{
            "Plan": label,
            "Monthly": (fmt_money(r["monthly_payment"]) if "monthly_payment" in r
                        else fmt_money(first_payment_of(r))),
            "Payoff": f"{r['payoff_years']:.1f} yrs",
            "Total interest": fmt_money(r["total_interest"]),
            "Forgiven": fmt_money(r["forgiven_amount"]) if r["forgiven_amount"] else "—",
            # Only RAP has a subsidy, so every other row is an em dash rather
            # than $0 -- "$0" would read as a subsidy that failed rather than a
            # plan that has none.
            "Interest waived": (fmt_money(r["waived_interest"])
                                if r.get("waived_interest") else
                                ("$0" if "RAP" in label else "—")),
            "What it is": note,
        } for label, r, note in rows]), hide_index=True, use_container_width=True)

        # The one-way door, given its own block because it is the single most
        # decision-relevant fact on this page and it inverts against intuition:
        # the low payment that makes RAP attractive is exactly what fails the
        # ">= 10-year Standard" test, so the LOWER the income the more nearly
        # irreversible the switch.
        _rap_row = next((r for label, r, _ in rows if "RAP" in label), None)
        _std_row = next((r for label, r, _ in rows if label.startswith("Standard")), None)
        if _rap_row is not None and _std_row is not None:
            _back = rap_months_counting_back(_rap_row, _std_row["monthly_payment"])
            if _back["total"] and _back["counting"] == 0:
                st.warning(
                    f"**Moving to RAP is close to a one-way door for you.** None of "
                    f"the {_back['total']} RAP payments modelled above would count "
                    f"toward IBR/ICR/PAYE if you switched back — a month only counts "
                    f"when the RAP payment is at least the 10-year Standard payment "
                    f"of {fmt_money(_std_row['monthly_payment'])}, and at this income "
                    f"RAP never reaches it. The lower your payment, the more you "
                    f"give up by switching."
                )
            elif _back["total"] and _back["share"] < 1:
                st.info(
                    f"**Switching back would cost you some credit.** "
                    f"{_back['counting']} of {_back['total']} RAP payments "
                    f"({_back['share']:.0%}) would count toward IBR/ICR/PAYE if you "
                    f"returned — only months where the RAP payment reaches the "
                    f"10-year Standard payment of {fmt_money(_std_row['monthly_payment'])} "
                    f"count."
                )

        if forgivable:
            st.caption(
                f"**Payoff is time from today**, not from when you first borrowed"
                + (f" — the {prior_payments} payments you have already made are "
                   "subtracted from the income-driven rows." if prior_payments else ".")
                + "  \n**Switching plans is not symmetric.** Payments you made "
                "under any income-driven plan count toward discharge under RAP, "
                "but moving to RAP also moves your finish line out to RAP's 30 "
                "years — a PAYE borrower 20 years in does not finish sooner by "
                "switching. Going the other way, RAP payments count toward "
                "IBR/ICR/PAYE only in months where the RAP payment was at least "
                "the 10-year Standard payment, which for most income-driven "
                "borrowers is never.  \n"
                "**And the way back is closing.** ICR and PAYE terminate on "
                "July 1, 2028, leaving IBR as the only plan RAP credit could "
                "return to — and IBR is itself shut to loans originated on or "
                "after July 1, 2026. Sources: studentaid.gov guidance on "
                "changing IDR plans; TICAS, *Upcoming Changes to Income-Driven "
                "Repayment Plans*."
            )

        # A chart for whichever plan the visitor wants to look at. Without one,
        # the principal/unpaid-interest split had nowhere to appear -- which is
        # how an input that fed only that split came to look like it did
        # nothing.
        plan_labels = [label for label, _, _ in rows]
        chosen = st.selectbox("Show the balance over time for", plan_labels,
                               key="existing_chart_plan")
        chosen_result = next(r for label, r, _ in rows if label == chosen)
        st.plotly_chart(build_balance_chart(chosen_result["schedule"], chosen),
                         use_container_width=True, config=PLOTLY_CHART_CONFIG,
                         key="existing_balance_chart")
        if accrued > 0 and not balance_split_is_informative(chosen_result["schedule"]):
            st.caption(
                f"This plan clears your {fmt_money(accrued)} of unpaid interest early, "
                "so the chart shows a single balance from then on. It still costs you "
                "less than the same balance would as principal — interest is charged on "
                "principal only, and unpaid interest does not compound while it sits "
                "there.".replace("$", chr(92) + "$")
            )

        if pslf and forgivable:
            st.success(
                f"**PSLF changes which plan wins.** The income-driven rows above now "
                f"forgive at {PSLF_QUALIFYING_PAYMENTS} payments instead of 20 or 30 "
                "years, so the plan with the LOWEST payment usually costs least overall "
                "— the opposite of the answer without PSLF. Standard 10-Year qualifies "
                "but retires the loan in exactly 120 payments, leaving nothing to "
                "forgive; Extended and Tiered Standard do not qualify at all.\n\n"
                "Unlike an income-driven discharge, studentaid.gov attaches its "
                "\"you may owe income tax on the forgiven amount\" warning to IDR "
                "forgiveness and not to PSLF."
            )

        render_rap_subsidy_answer(rows)
        st.caption(
            "Simplified models of the real plans, not your servicer's figures — payments "
            "here come from this app's own formulas and your actual bill will differ. "
            "Forgiven balances are taxable as ordinary income in the year they are "
            "discharged, and that tax is **not** included above. Extra payments are not "
            "modelled: paying more than the minimum shortens every row, and under RAP it "
            "also forfeits the subsidy in any month the extra covers the interest."
        )


def first_payment_of(result: dict) -> float:
    """Month-1 payment for a plan whose payment moves with income."""
    schedule = result.get("schedule")
    if schedule is None or "payment" not in schedule.columns or schedule.empty:
        return 0.0
    return float(schedule["payment"].iloc[0])


def render_rap_subsidy_answer(rows: list) -> None:
    """What RAP's interest subsidy is actually worth to THIS borrower.

    The headline question for anyone weighing RAP, and one the plan's own
    description answers misleadingly: "unpaid interest is waived" sounds like a
    benefit to everyone, when a borrower whose payment covers the interest gets
    nothing. Stating the figure is the only way to tell those apart.
    """
    rap = next((r for label, r, _ in rows if "RAP" in label), None)
    if rap is None:
        return
    waived = rap.get("waived_interest", 0) or 0
    if waived > 0:
        st.success(
            f"**RAP's interest subsidy is worth {fmt_money(waived)} to you.** That is "
            "interest your payment doesn't cover, which RAP writes off instead of "
            "letting it accrue.".replace("$", chr(92) + "$")
        )
    else:
        st.info(
            "**RAP's interest subsidy is worth nothing to you.** It only waives interest "
            "your payment fails to cover, and at this income your payment covers all of "
            "it. RAP may still win on the monthly figure — but not for that reason."
        )


# ============================================================
# 5. MAIN PAGE
# ============================================================

# ?tool=repayment serves the repayment-plan comparison on its own, as a
# shareable link that is not the college calculator. They answer different
# questions -- whether to borrow, versus what to do about a balance you already
# have -- and someone sent the second should not have to scroll past the first.
#
# Latched like test_mode: Share Scenario replaces the whole query string, so a
# live re-read would drop the visitor back into the calculator mid-session.
if "repayment_only" not in st.session_state:
    st.session_state.repayment_only = repayment_page_requested()
repayment_only = st.session_state.repayment_only

if repayment_only:
    # The sidebar still executes -- it defines names section 5 reads -- but it
    # describes a scenario this page is not about, so it is hidden rather than
    # shown empty. Cheaper and far less invasive than making 2,000 lines of
    # module-level sidebar code conditional.
    st.markdown("<style>section[data-testid='stSidebar']{display:none;}</style>",
                unsafe_allow_html=True)
    st.title("💸 Compare Student Loan Repayment Plans")
    st.caption(
        "**Free · anonymous · no sign-up** — an educational estimate, not financial "
        "advice. For a balance you already owe."
    )
else:
    st.title("🎓 Student Loan Payoff & Major ROI Calculator")
    st.caption(
        "**Free · anonymous · no sign-up** — an educational estimate, not financial "
        "advice. Salary and cost figures are illustrative."
    )
if st.session_state.get("test_mode"):
    st.warning("🧪 **Test mode** (`?test=1`) — this session's interactions are **not** being logged to the research dataset.")

# The before-you-look questions. Above the results because that is the only
# place a "before" measurement can be taken, and skippable because the results
# are the point -- see render_presurvey.
#
# Above the "update your profile" banner too: that banner is an instruction
# about the sidebar, and a visitor who reads it acts on it and never comes back
# up. Putting the one-time question first costs the banner nothing -- it is
# still the next thing on screen -- and is the whole of what "more prominent"
# means here. It is NOT a gate; everything below renders regardless.
# The research instrument belongs to the college-decision flow. A visitor on
# the repayment page was never recruited for it and is answering a different
# question, so showing it would put unrelated answers in the paired sample.
if not repayment_only:
    render_presurvey()

# Everything below this point is the college calculator. On ?tool=repayment we
# render the repayment comparison instead and stop -- the sidebar is already
# hidden above, so the page is that tool and nothing else.
if repayment_only:
    render_existing_loan_comparison(always_open=True)
    st.caption(
        "Looking at whether a degree is worth borrowing for instead? "
        "[Open the full calculator](./)."
    )
    st.stop()

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

**What to do.** Pick your major and school on the left. Your loan fills in automatically
from what graduates of that school typically borrow (**Simplified**); switch **Loan
estimate** to **Detailed** to build it from your own cost and aid instead. Numbers update
as you change them. Nothing is saved, there's no login, and you can't break it — try the
majors you're actually deciding between. Don't know your real cost or family contribution?
The **🎯 Get Your Real Numbers** section lower down links to two free official tools.

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

# ---- 5a. Admin Analytics Dashboard: aggregation helpers -------------------

def _admin_parse_dates(df: pd.DataFrame) -> pd.Series:
    """usage_logs timestamps are ISO strings stamped in the visitor's own
    timezone (now_local), so offsets vary row to row -- utc=True normalizes
    them to one axis, errors='coerce' turns anything unparseable into NaT
    rather than raising. Returns a Series of python dates aligned to df.index."""
    return pd.to_datetime(df["timestamp"], utc=True, errors="coerce").dt.date


def _admin_final_per_session(events_df: pd.DataFrame) -> pd.DataFrame:
    """One row per session_id -- the visitor's LAST scenario_events row (max
    event_seq) -- so the breakdowns read as 'what each distinct visitor ended
    up configuring', not raw event volume (a session that tried five majors
    would otherwise count five times). Order by event_seq, never timestamp:
    timestamps come from the visitor's own clock and can tie or run backwards."""
    if events_df.empty or "session_id" not in events_df.columns:
        return events_df
    df = events_df.copy()
    if "event_seq" in df.columns:
        df["_seq"] = pd.to_numeric(df["event_seq"], errors="coerce").fillna(-1)
        df = df.sort_values("_seq")
    df = df.drop_duplicates(subset="session_id", keep="last")
    return df.drop(columns=[c for c in ["_seq"] if c in df.columns])


def _admin_count_table(df: pd.DataFrame, column: str, label: str,
                        missing: str = "(none)") -> None:
    """value_counts on one column, rendered via render_centered_table. NULL or
    empty values fold into `missing` (newly-logged columns are all-NULL on
    historical rows, so this is the common case at first). Emits a 'No data
    yet' caption and returns when the column is absent or the frame is empty."""
    if df.empty or column not in df.columns:
        st.caption("No data yet.")
        return
    series = df[column].astype("object").where(df[column].notna(), missing)
    series = series.replace("", missing)
    counts = series.value_counts().reset_index()
    counts.columns = [label, "Count"]
    render_centered_table(counts)


def _admin_n_sessions(*dfs: pd.DataFrame) -> int:
    """Distinct session_ids across the union of the given tables -- the funnel
    counts visitors, not rows, and a visitor can appear in several tables."""
    parts = [d["session_id"] for d in dfs if not d.empty and "session_id" in d.columns]
    if not parts:
        return 0
    return int(pd.concat(parts, ignore_index=True).dropna().nunique())


# ---- 5a. Admin Analytics Dashboard (hidden behind sidebar checkbox) ------

if admin_enabled:
    st.subheader("📊 Admin Analytics Dashboard")

    # load_table_safe does select("*"); the columns= list is only the fallback
    # frame's shape when a table can't be read, so it names what each panel needs.
    usage_df = load_table_safe(
        "usage_logs", columns=["timestamp", "action", "traffic_source", "session_id"])
    events_df = load_table_safe(
        "scenario_events",
        columns=["timestamp", "session_id", "event_seq", "dataset_mode",
                 "career_data_source", "loan_mode", "cc_mode_a", "scenario_a_major",
                 "scenario_a_loan_amount", "scenario_a_repayment_strategy",
                 "roi_horizon_years", "experiment_arm"])
    pdf_downloads_df = load_table_safe("pdf_downloads", columns=["timestamp", "session_id"])
    scenario_shares_df = load_table_safe("scenario_shares", columns=["timestamp", "session_id"])
    survey_df = load_table_safe("survey_responses", columns=["timestamp", "session_id"])

    # One row per distinct visitor's final configuration -- the basis for every
    # "what visitors configured" breakdown below.
    final_df = _admin_final_per_session(events_df)

    # Pageviews and "everything logged" are separate numbers and were being
    # conflated: the old single "Total App Interactions" was len(usage_df),
    # i.e. every row of every kind -- pageviews, presurvey_shown, searches and
    # the interaction: events -- which now reads as if it meant interactions in
    # the specific sense those events introduced. Split, and both named for
    # what they actually count.
    # BOTH pageview actions: this metric answers "how many visits", and a
    # repayment-page visit is a visit. The split is reported separately below
    # rather than by quietly dropping one of them here.
    _pageviews = usage_df[usage_df["action"].isin(PAGEVIEW_ACTIONS)] if (
        not usage_df.empty and "action" in usage_df.columns) else pd.DataFrame()
    _repay_views = int((usage_df["action"] == "pageview_repayment").sum()) if (
        not usage_df.empty and "action" in usage_df.columns) else 0
    _visits = (int(_pageviews["session_id"].dropna().nunique())
               if "session_id" in _pageviews.columns else 0)

    col1, col2, col3, col4, col5 = st.columns(5)
    # Unique visits as the delta rather than a sixth column: the two numbers
    # only mean anything read together, and they differ for two reasons worth
    # seeing side by side (see the caption).
    col1.metric("Pageviews", len(_pageviews),
                delta=f"{_visits} unique visits", delta_color="off")
    col2.metric("Logged Events", len(usage_df))
    col3.metric("Survey Responses", len(survey_df))
    col4.metric("PDF Downloads", len(pdf_downloads_df))
    col5.metric("Scenario Shares", len(scenario_shares_df))
    st.caption(
        "**Pageviews** counts both landing actions -- `pageview` (the calculator) "
        f"and `pageview_repayment` (the standalone `?tool=repayment` page, "
        f"{_repay_views} of them). They are logged separately because they are "
        "different populations asking different questions, but a visit is a visit, "
        "so this total counts both. "
        "**Unique visits** de-duplicates "
        "them by session. They diverge for two reasons, neither of them traffic: "
        "rows written before `session_id` existed cannot be de-duplicated at all "
        f"({int(usage_df['session_id'].isna().sum()) if not usage_df.empty and 'session_id' in usage_df.columns else 0} "
        "of them), and until 2026-08-01 a real browser logged **two** pageviews "
        "per visit — a race between the write and its guard, since fixed. Both "
        "inflate the left number only. **Logged Events** is every row of every "
        "kind, which is what this panel used to call \"App Interactions\"."
    )

    st.divider()

    # (a) App interactions over time
    st.markdown("#### 📈 App interactions over time")
    _daily = _admin_parse_dates(usage_df).dropna() if (
        not usage_df.empty and "timestamp" in usage_df.columns) else pd.Series([], dtype=object)
    if _daily.empty:
        st.caption("No data yet.")
    else:
        by_day = _daily.value_counts().sort_index()
        chart_df = pd.DataFrame({"Interactions": by_day.values},
                                index=pd.to_datetime(list(by_day.index)))
        st.bar_chart(chart_df)

    # (b) App interactions by traffic source (?src= tag)
    st.markdown("#### 🔗 Traffic by source")
    st.caption(
        "From the `?src=` tag on the link visitors arrived through; organic "
        "visits carry none. **Sorted by pageviews** -- the reach a channel "
        "actually delivered. *Unique visits* de-duplicates by session; *Logged "
        "events* counts every row of every kind, so one engaged visitor can "
        "outweigh several who bounced.\n\n"
        "**Do not compare `(organic)`'s two columns.** Rows written before "
        "`session_id` existed cannot be de-duplicated, and almost all of them "
        "are organic -- so its unique-visit figure counts only the newer rows "
        "while its pageview figure counts all of them. Tagged sources have no "
        "such gap; where their two columns differ it is the pre-2026-08-01 "
        "double-count, which is bounded and now fixed."
    )
    if usage_df.empty or "traffic_source" not in usage_df.columns:
        st.caption("No data yet.")
    else:
        _src = usage_df.copy()
        _src["Source"] = (_src["traffic_source"].astype("object")
                           .where(_src["traffic_source"].notna(), "(organic)")
                           .replace("", "(organic)"))
        _pv_only = (_src[_src["action"].isin(PAGEVIEW_ACTIONS)]
                if "action" in _src.columns else _src.iloc[0:0])
        _by_src = pd.DataFrame({
            "Pageviews": _pv_only.groupby("Source").size(),
            "Unique visits": (_pv_only.groupby("Source")["session_id"].nunique()
                               if "session_id" in _pv_only.columns else 0),
            "Logged events": _src.groupby("Source").size(),
        }).fillna(0).astype(int)
        # Sort on pageviews, then unique visits: two channels with equal reach
        # are not equally good, and the tie-break should favour the one that
        # brought distinct people rather than repeat loads.
        _by_src = (_by_src.sort_values(["Pageviews", "Unique visits"], ascending=False)
                          .reset_index())
        render_centered_table(_by_src)

    st.divider()
    st.markdown("#### 🎓 What visitors configured")
    st.caption(f"One row per distinct visitor ({len(final_df)} sessions with a "
               "scenario), taking each session's final selection.")

    # (e) Major vs Career
    st.markdown("**Chose by — Major vs Career**")
    _admin_count_table(final_df, "dataset_mode", "Mode")

    # (f) National vs California (Career mode only -- the radio is disabled in
    # Major mode, so a Major-mode row's value is just the inert default)
    st.markdown("**Wage dataset — National vs California**")
    st.caption("Career mode only; Major mode has no geography, so those "
               "sessions are excluded rather than counted as the default.")
    _career_only = final_df[final_df["dataset_mode"] == DATASET_MODE_CAREER] if (
        not final_df.empty and "dataset_mode" in final_df.columns) else final_df
    _admin_count_table(_career_only, "career_data_source", "Dataset")

    # (c) Community-college path
    st.markdown("**Community-college path**")
    _admin_count_table(final_df, "cc_mode_a", "CC mode")

    # (d) Loan estimate -- amount ranges AND Simplified/Detailed mode
    st.markdown("**Loan estimate — amount ranges**")
    _amounts = pd.to_numeric(final_df["scenario_a_loan_amount"], errors="coerce").dropna() if (
        not final_df.empty and "scenario_a_loan_amount" in final_df.columns) else pd.Series([], dtype=float)
    if _amounts.empty:
        st.caption("No data yet.")
    else:
        _bins = [-0.01, 0, 10_000, 25_000, 50_000, 100_000, float("inf")]
        _labels = ["$0", "≤ $10k", "≤ $25k", "≤ $50k", "≤ $100k", "> $100k"]
        _buckets = pd.cut(_amounts, bins=_bins, labels=_labels)
        _table = _buckets.value_counts().reindex(_labels).fillna(0).astype(int).reset_index()
        _table.columns = ["Loan amount", "Count"]
        render_centered_table(_table)

    st.markdown("**Loan estimate — Simplified vs Detailed**")
    _admin_count_table(final_df, "loan_mode", "Loan mode")

    st.divider()
    st.markdown("#### 🔎 Other breakdowns")

    # Top majors / careers chosen
    st.markdown("**Top 10 majors / careers chosen**")
    if final_df.empty or "scenario_a_major" not in final_df.columns or \
            final_df["scenario_a_major"].dropna().empty:
        st.caption("No data yet.")
    else:
        _top = final_df["scenario_a_major"].dropna().value_counts().head(10).reset_index()
        _top.columns = ["Major / career", "Count"]
        render_centered_table(_top)

    # Repayment strategy
    st.markdown("**Repayment strategy**")
    _admin_count_table(final_df, "scenario_a_repayment_strategy", "Strategy")

    # ROI horizon
    st.markdown("**ROI horizon (years)**")
    _admin_count_table(final_df, "roi_horizon_years", "Horizon (yrs)")

    # Experiment arm (H2 randomised assignment)
    st.markdown("**Experiment arm (H2 assignment)**")
    _admin_count_table(final_df, "experiment_arm", "Arm")

    # Engagement funnel -- distinct visitors reaching each stage
    st.markdown("**Engagement funnel (distinct sessions)**")
    _funnel = pd.DataFrame({
        "Stage": ["Visited (pageviews)", "Configured a scenario",
                  "Committed (PDF / share / survey)"],
        "Sessions": [_admin_n_sessions(usage_df),
                     _admin_n_sessions(events_df),
                     _admin_n_sessions(pdf_downloads_df, scenario_shares_df, survey_df)],
    })
    render_centered_table(_funnel)

    st.divider()

# ---- 5b. School Data Lookup (local COA dataset + College Scorecard API) --
# Cost of Attendance (in/out-of-state) comes from the local dataset built by
# clean_college_scorecard.py, run against the real College Scorecard
# institution file (data/college_coa_clean.csv, 5,000+ real schools).
# Median debt is still fetched live, which works for any school regardless of local
# dataset coverage. A matched school's COA also auto-fills that scenario's
# per-year Cost of Attendance field (in-state or out-of-state, per the
# In-State Student? checkbox) -- see _autofill_coa in section 2c.

def suggested_home_state(coa_df: pd.DataFrame, city_name: str) -> str:
    """Best available guess at where the visitor lives, for pricing search
    results. Returns None when nothing supports a guess -- which prices
    everything out-of-state, the safe direction.

    Two sources, strongest first:

    1. **In-state at the school they named.** That is not a guess at all but a
       direct statement about residency: you cannot be an in-state student
       somewhere you do not reside. Only usable when in_state_a is actually
       True -- an out-of-state student's school says nothing about home.
    2. **The selected metro's state.** Weaker, since the metro is where they
       want to WORK, and it is itself seeded from the school. Used only as a
       fallback.
    """
    if st.session_state.get("in_state_a"):
        row = find_school_coa(
            st.session_state.get("school_search_a", ""), coa_df,
            unitid=st.session_state.get("school_pick_a"))
        if row is not None and pd.notna(row.get("STABBR")):
            return str(row["STABBR"])
    metro_state = CITY_DATA.get(city_name, {}).get("state_key")
    return metro_state if metro_state in US_STATES else None


# The controls that make a search a SEARCH. Touching any of them is what
# separates "the visitor ran a query" from "the panel rendered".
SEARCH_CONTROL_KEYS = ("search_cip_family", "search_credential", "search_budget",
                        "search_home_state", "search_states")


def search_was_adjusted() -> bool:
    """Whether the visitor has touched any search control this session.

    render_school_search runs at MODULE level on every rerun, and Streamlit
    executes an expander's body even while it is collapsed -- so the search
    itself executes on every page load. In Major mode the field of study is
    prefilled from the visitor's major, so a query runs and, before this gate,
    was logged. Every session therefore recorded a school_search_run: 3 of 3 in
    the traffic that surfaced it, which made "sessions that ran a search" a
    synonym for "sessions that loaded the page" and gave the funnel a
    denominator of all traffic.

    Reuses the set mark_interaction already maintains rather than adding a
    second flag, so the two cannot disagree about what "touched" means.

    Note what this deliberately does NOT claim: a visitor who opens the panel,
    reads the prefilled results and changes nothing is not counted. Streamlit
    does not expose expander state, so that case is indistinguishable from
    never opening it, and counting it would put the old defect back in a
    smaller form. The metric is "adjusted a search control", which is narrower
    than "searched" and is what the data can actually support.
    """
    return bool(set(st.session_state.get("_interactions_logged", ()))
                & set(SEARCH_CONTROL_KEYS))




def render_school_search() -> None:
    """Budget-first school search: what could I attend, for this field, at this price?

    The inverse of everything else on this page. Every other surface starts
    from a school the visitor already named; this one starts from what they
    can pay. A seventeen-year-old knows a handful of school names, and the
    dataset holds 5,035.

    Rendered at MODULE LEVEL, after the school lookup and before the
    single/compare fork. That placement is the whole H2 story: nothing inside
    either result branch changes, so this cannot become a difference between
    the randomly-assigned arms. render_get_accurate_inputs documents the same
    reasoning for the same reason.

    Applies to Scenario A only. An "apply to A or B" control would render just
    in Compare Mode, reintroducing exactly the arm-dependent difference the
    placement avoids.
    """
    coa_df = load_coa_dataset()
    if coa_df.empty or "programs_bachl" not in coa_df.columns:
        return                      # dataset predates the program columns

    with st.expander("🔎 Find schools that fit a budget", expanded=False):
        st.caption(
            "Sorted by cost, and by nothing else. Every salary in this app comes "
            "from the occupation or major you picked — never from the school — so "
            "this can't tell you which of these leads to higher pay. What it can "
            "tell you is which ones teach your field at a price you could cover."
        )

        # Prefilled from the major ONLY in Major mode. A major and a CIP family
        # are both fields of study, so that is a correspondence. An occupation
        # is not, and mapping one to a field of study is the crosswalk this
        # codebase already declined to trust.
        default_family = (MAJOR_TO_CIP_FAMILY.get(major)
                           if dataset_mode == DATASET_MODE_MAJOR else None)
        families = sorted(CIP_FAMILY_TITLES, key=lambda code: CIP_FAMILY_TITLES[code])
        st.session_state.setdefault(
            "search_cip_family",
            default_family if default_family in CIP_FAMILY_TITLES else None)

        row_one, row_two = st.columns([3, 2])
        row_one.selectbox(
            "Field of study", families, key="search_cip_family",
            on_change=lambda: mark_interaction("search_cip_family"),
            index=None if st.session_state.get("search_cip_family") is None else None,
            format_func=lambda code: CIP_FAMILY_TITLES[code],
            placeholder="Pick a field",
            help="Fields come from the federal CIP classification, which is broader "
                  "than a major -- 'Business, Management & Marketing' covers "
                  "accounting, finance and marketing alike, so those return the "
                  "same schools.",
        )
        row_two.selectbox("Level", list(CREDENTIAL_LEVELS), key="search_credential",
                           on_change=lambda: mark_interaction("search_credential"))

        # A range, not a ceiling. The old control only asked "most I could pay",
        # which cannot express "what does this actually cost" -- and because
        # results are the cheapest `limit` matches, an expensive school stayed
        # invisible however high the ceiling went. Raising the FLOOR is what
        # surfaces them.
        #
        # New key: search_budget holds an int in any session already open, and
        # handing a range slider a stored int raises at render time.
        _seed_high = int(st.session_state.get("coa_per_year_a", 25_000))
        min_coa, max_coa = st.slider(
            "School Cost of Attendance (COA) — tuition, housing, everything",
            min_value=0, max_value=100_000,
            value=(0, min(max(_seed_high, 1_000), 100_000)), step=1_000,
            format="$%d", key="search_coa_range",
            on_change=lambda: mark_interaction("search_coa_range"),
            help="The whole yearly cost, not just tuition. Drag the LEFT handle "
                 "up to hide the cheapest schools — results are the cheapest "
                 "matches, so raising the floor is how you surface pricier ones "
                 "rather than raising the ceiling.",
        )
        budget = max_coa
        all_states = sorted({s for s in coa_df["STABBR"].dropna().unique()})
        home_col, states_col = st.columns([2, 3])

        # Asked once, here, rather than inherited from the sidebar's in-state
        # checkbox: that checkbox is one fact about the visitor and the ONE
        # school they named, and these results span many states. See
        # search_schools_by_budget for what pricing them all alike costs.
        st.session_state.setdefault(
            "search_home_state", suggested_home_state(coa_df, city))
        home_col.selectbox(
            "Where do you live?", all_states, key="search_home_state",
            on_change=lambda: mark_interaction("search_home_state"),
            index=None if st.session_state.get("search_home_state") is None else None,
            placeholder="Pick your state",
            help="Public schools charge residents far less. Without this, every "
                  "school is priced at its higher out-of-state rate.",
        )
        # Default the state filter to where the visitor lives. Without it the
        # search spans every state, and since results are the CHEAPEST 50 of
        # however many match, an expensive-but-obvious school could never
        # appear: 751 US schools award an engineering bachelor's, the 50
        # cheapest all cost under $24,602, and a Californian searching
        # engineering therefore never saw a single UC campus -- all nine are in
        # the data and all nine offer it. Scoping to one state takes 751 to
        # about 40 and the cap stops binding at all.
        #
        # setdefault, not a forced value: it seeds the first render and then
        # leaves the control alone, so clearing it to search nationally sticks.
        _home = st.session_state.get("search_home_state")
        if _home:
            st.session_state.setdefault("search_states", [_home])
        states = states_col.multiselect(
            "Limit to states (optional)", all_states, key="search_states",
            on_change=lambda: mark_interaction("search_states"),
            help="Defaults to your home state, where public schools charge you "
                  "the resident rate. Clear it to search the whole country -- "
                  "results are the cheapest matches, so a national search "
                  "surfaces low-cost schools rather than well-known ones.")

        family = st.session_state.get("search_cip_family")
        if not family:
            st.info("Pick a field of study to search.")
            return

        home_state = st.session_state.get("search_home_state")
        if not home_state:
            st.caption(
                "⚠️ Every school below is priced at its **out-of-state** rate, "
                "because you haven't said where you live. Public schools in your "
                "own state will be cheaper than shown — often by several thousand "
                "a year."
            )

        # Read once into a local: the search and the log must agree on which
        # credential was asked for, and reading session_state twice invites
        # them to disagree.
        credential = st.session_state.get("search_credential", "Bachelor's degree")
        results = search_schools_by_budget(
            family, credential, budget, home_state,
            states=tuple(states) or None, limit=25, min_coa_per_year=min_coa)

        # Only once the visitor has actually adjusted something -- see
        # search_was_adjusted. The results above still render either way; this
        # gates the LOG, not the feature.
        # Same criterion the search log already uses: a visitor who only opened
        # the expander has not used it. search_was_adjusted() is the existing
        # answer to "did they actually touch a control", so reuse it rather than
        # invent a second definition that could drift from it.
        if search_was_adjusted():
            mark_interaction("module_school_search")
            _log_school_search(family, budget, states, len(results), home_state,
                                level=CREDENTIAL_LEVELS.get(credential, (None,))[0])

        if results.empty:
            # A real answer, not an error state. "Your budget admits nothing in
            # this field" is the finding this feature exists to surface, and
            # hiding it would turn the most decision-relevant result into a
            # blank panel.
            st.warning(
                f"No schools teach **{CIP_FAMILY_TITLES[family]}** at that level "
                f"for {fmt_money(budget)}/year"
                + (f" in {', '.join(states)}" if states else "")
                + ". Raising the budget or widening the states will find some — "
                "and the fact that this combination has none is itself worth knowing."
            )
            return

        st.caption(
            f"{len(results)} school{'s' if len(results) != 1 else ''}, cheapest first. "
            "These are **sticker prices before aid** — a pricier school can end up "
            "cheaper once grants are applied, so treat this as a starting list and "
            "check each one's net price calculator below."
        )
        # Shown per row because it varies per row -- the visitor is resident in
        # one of these states and a visitor state elsewhere. Naming the rate is
        # what makes the price checkable against the school's own published
        # figures, which are always listed as two numbers.
        rate_label = results.apply(
            lambda row: "in-state"
            if row["is_home_state"] else
            ("out-of-state" if row["out_of_state_coa"] != row["in_state_coa"]
             else "same either way"), axis=1)
        table = pd.DataFrame({
            "School": results["INSTNM"],
            "Where": results["CITY"].fillna("") + ", " + results["STABBR"].fillna(""),
            "Type": results["control_type"],
            "Rate": rate_label,
            "Per year": results["coa_per_year"].map(fmt_money),
            "Whole program": results["total_program_cost"].map(fmt_money),
            "Admits": results["ADM_RATE"].map(
                lambda rate: f"{rate:.0%}" if pd.notna(rate) else "open / not reported"),
        })
        st.dataframe(table, use_container_width=True, hide_index=True)
        st.caption(
            "**Rate** is which price you'd be charged"
            + (f", based on living in {home_state}. " if home_state else ". ")
            + "*Same either way* means the school charges one price regardless — "
            "true of most private schools. **Admits** is the share of applicants "
            "accepted; blank means the school reports none, usually because it "
            "admits nearly everyone. It is shown for context and is never what "
            "the list is sorted by."
        )

        # One selectbox and one button, not a button per row: 25 widgets would
        # re-render every pass and need stable keys for no gain.
        choice = st.selectbox(
            "Use one of these as your school",
            list(results.index),
            format_func=lambda i: f"{results.at[i, 'INSTNM']} — "
                                   f"{fmt_money(results.at[i, 'coa_per_year'])}/yr",
            key="search_pick",
        )
        if st.button("Use this school", type="primary"):
            picked = results.loc[choice]
            # Carries the residency the row was PRICED at. Without it the
            # sidebar would autofill from its own in-state checkbox and could
            # show a different number than the row the visitor just clicked --
            # the in-state price of a school they'd attend out-of-state.
            st.session_state["_pending_school"] = (
                picked["INSTNM"],
                int(picked["UNITID"]) if pd.notna(picked.get("UNITID")) else None,
                bool(picked["is_home_state"]))
            # delta_coa is this feature's effect size in one number: what the
            # visitor was already modelling, minus what they just switched to.
            # Negative means the search moved them cheaper, which is the entire
            # claim the inverse search exists to support. Recorded here rather
            # than derived later because the PREVIOUS value is gone the moment
            # _apply_pending_school overwrites it on the next rerun.
            prev_coa = st.session_state.get("coa_per_year_a")
            delta_coa = (int(picked["coa_per_year"]) - int(prev_coa)
                          if prev_coa is not None else None)
            log_usage_event(
                f"school_search_apply:unitid={picked.get('UNITID')}"
                f":coa={int(picked['coa_per_year'])}"
                f":prev_coa={int(prev_coa) if prev_coa is not None else 'unset'}"
                f":delta_coa={delta_coa if delta_coa is not None else 'unset'}"
                f":in_state={int(bool(picked['is_home_state']))}")
            st.rerun()


def _log_school_search(family: str, budget: int, states: list, hit_count: int,
                        home_state: str = None, level: str = None) -> None:
    """Record a search, once per distinct query rather than once per rerun.

    This runs on every pass of the script, so without the dedupe a slider drag
    would write a row per tick -- the same reason maybe_log_scenario_event
    keeps a signature. Zero-result searches are logged deliberately: they are
    the direct evidence that a visitor's budget admits nothing in their field,
    which is the finding this feature exists to produce.
    """
    # home_state is part of the signature because it changes the PRICES, and
    # therefore which schools clear the budget -- two searches identical but
    # for residency are different searches with different answers.
    signature = (family, budget, tuple(states), hit_count, home_state, level)
    if st.session_state.get("_last_school_search") == signature:
        return
    st.session_state["_last_school_search"] = signature
    # level matters more than it looks: an associate's search and a bachelor's
    # search over the same field return different institutions entirely, and
    # which one a visitor ran is the whole distinction for the community-college
    # audience. Abbreviated to the CREDENTIAL_LEVELS suffix so the action string
    # stays short.
    log_usage_event(
        f"school_search_run:cip={family}:level={level or 'unset'}:budget={budget}"
        f":home={home_state or 'unset'}"
        f":states={'+'.join(states) if states else 'any'}:n={hit_count}")


def render_school_lookup(container, school_name: str, label: str, unitid=None):
    """Render one scenario's school lookup (COA match + median debt) into a
    layout container. Used once for the single-scenario view and twice
    (Scenario A / B) in Compare Mode, so the two can't drift apart from
    being hand-copied -- same reasoning as render_scenario_panel.

    label is None for a shared box (both compare scenarios are the same school),
    which drops the "Scenario A/B:" prefix since it applies to both."""
    with container:
        if not school_name:
            return
        coa_match = find_school_coa(school_name, load_coa_dataset(), unitid=unitid)
        # The API still gets the plain NAME -- Scorecard has no UNITID query
        # here, and the name is what it matches on. The pin disambiguates only
        # our own local row.
        debt_data = fetch_median_debt(school_name, scorecard_api_key)
        scenario_prefix = f"Scenario {label}: " if label else ""

        if coa_match is not None:
            coa_text = (
                f"**{scenario_prefix}{coa_match['INSTNM']}** ({coa_match['control_type']}) — "
                f"In-state Cost of Attendance: {fmt_money(coa_match['in_state_coa'])} | "
                f"Out-of-state Cost of Attendance: {fmt_money(coa_match['out_of_state_coa'])} "
                f"({now_local().year})"
            ).replace("$", r"\$")
            st.info(coa_text)
        else:
            no_match = (f"Scenario {label}: no" if label else "No") + \
                " Cost of Attendance match in the local dataset yet"
            st.caption(no_match + " (currently only a small sample of schools -- "
                       "see data/college_coa_clean.csv).")

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
    if compare_mode and school_name_a and school_name_a == school_name_b:
        # Both scenarios are the same school -- one COA box, not a duplicate pair.
        render_school_lookup(st.container(), school_name_a, None, unitid=school_unitid_a)
    elif compare_mode:
        lookup_col_a, lookup_col_b = st.columns(2)
        render_school_lookup(lookup_col_a, school_name_a, "A", unitid=school_unitid_a)
        render_school_lookup(lookup_col_b, school_name_b, "B", unitid=school_unitid_b)
    else:
        render_school_lookup(st.container(), school_name_a, "A", unitid=school_unitid_a)

    # Directly under the lookup, which has just printed what the named school
    # costs -- so the obvious next question ("is there anything cheaper that
    # teaches this?") is answerable in the same breath. Above the 5c fork, so
    # no result branch changes.
    render_school_search()
    # Also above the 5c fork, so both result branches get it and the H2 arms
    # cannot differ by it. Placed last because it answers a different question
    # from everything above -- what to do about debt you already have, rather
    # than whether to take it on.
    render_existing_loan_comparison()


def _npc_link_markdown(school_name: str) -> str:
    """One net-price-calculator markdown link for a school: the school's own
    calculator when we have its URL on file, otherwise the ED directory where
    they can search for it."""
    url = get_school_npc_url(school_name)
    if url and school_name:
        return f"[Open {school_name}'s net price calculator →]({url})"
    return f"[Find your school's net price calculator →]({NPC_DIRECTORY_URL})"


def render_get_accurate_inputs(school_name_a, school_name_b, compare_mode, prestige_mode):
    """Route users to the two free federal tools that turn the app's
    school-average sticker inputs into their own personalized figures, and
    spell out exactly which sidebar field each result goes into. Rendered once
    for everyone (after the results, just above the survey) at module level,
    outside the single/compare branches -- keeping it out of either branch is
    what stops it becoming an experiment-arm confound."""
    st.subheader("🎯 Get Your Real Numbers")
    st.caption(
        "The cost and aid figures here are school-wide averages. For a decision this "
        "big, it's worth five minutes replacing them with your own — both tools below "
        "are free, official, and only need a few inputs. They're separate government "
        "sites, not part of this app."
    )
    col_cost, col_sai = st.columns(2)

    with col_cost:
        st.markdown("**💵 Your real cost after aid**")
        if prestige_mode:
            # Prestige Mode holds a tier label, not a real school -- only the
            # directory link makes sense.
            st.markdown(f"[Find your school's net price calculator →]({NPC_DIRECTORY_URL})")
        elif compare_mode and school_name_a and school_name_a == school_name_b:
            # Both scenarios are the same school -- one link, not a redundant pair.
            st.markdown(_npc_link_markdown(school_name_a))
        elif compare_mode:
            st.markdown("Scenario A: " + _npc_link_markdown(school_name_a))
            st.markdown("Scenario B: " + _npc_link_markdown(school_name_b))
        else:
            st.markdown(_npc_link_markdown(school_name_a))
        st.caption(
            "Gives your **net price** — the cost after grants & scholarships. Enter that "
            "number as **Cost of Attendance** in the sidebar, and set **Grants & "
            "Scholarships to \\$0** (the net price already subtracted them — leaving a "
            "grants figure in would subtract aid twice)."
        )

    with col_sai:
        st.markdown("**🎓 Your family contribution (SAI)**")
        st.markdown(f"[Open the Federal Student Aid Estimator →]({SAI_ESTIMATOR_URL})")
        st.caption(
            "Estimates your **Student Aid Index (SAI)** — what your family is expected to "
            "put toward each year. Enter it as **Personal Contribution (per year)** in the "
            "sidebar. This lowers your loan on top of the net price above, and that's "
            "correct — net price doesn't remove the family contribution, so it's not "
            "double-counting."
        )

    st.divider()


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


def takehome_figures(scenario: dict, major_name: str, stage_key: int, city: dict) -> dict:
    """Every take-home number for ONE career stage. Pure computation, no
    rendering, so the on-screen block and the PDF builder read the same
    figures from one place instead of deriving them twice -- the chart-twin
    drift CLAUDE.md warns about, which here would put different take-home
    numbers on screen and in the download."""
    gross = get_annual_salary_for_year(major_name, stage_key)
    take_home = calculate_take_home_pay(gross, city["state_key"], city["local_tax_rate"])
    monthly_payment = get_monthly_payment_for_stage(
        scenario["repayment_result"], scenario["strategy_label"], (stage_key + 1) * 12)
    disposable_nominal = take_home["net_take_home"] / 12 - monthly_payment
    return {
        "gross": gross, "take_home": take_home, "monthly_payment": monthly_payment,
        "disposable_nominal": disposable_nominal,
        "disposable_col_adjusted": adjust_for_cost_of_living(
            disposable_nominal, city["col_index"]),
    }


def render_takehome_block(scenario: dict, major_name: str, city_name: str, city: dict,
                           show_charts: bool = True, heading: bool = True,
                           stage_layout: str = "columns") -> dict:
    """Compute and render one scenario's take-home figures for EVERY career
    stage side by side, returning {"stages": [(label, figures), ...]} for the
    PDF generators.

    Extracted so Compare Mode can render it too. It previously couldn't:
    compare_mode took a separate branch that skipped take-home entirely, and
    since that branch is the randomly assigned contrast arm, half of all
    visitors never saw their disposable income at all. Copying the block into
    the compare branch instead of extracting it is exactly the drift this
    codebase already warns about (see CLAUDE.md on the chart twins), so both
    branches call this.

    Both stages are rendered together because there is no chart anywhere that
    contrasts them. This replaced a sidebar "Career Stage Snapshot" radio that
    sampled one stage at a time: comparing year 1 against year 10 meant
    toggling it and holding two numbers in your head, and its default landed
    on Mid-Career -- the flattering end -- so the harder year was the one a
    visitor had to go looking for.

    stage_layout="stacked" is required inside Compare Mode's scenario columns:
    Streamlit allows only one level of column nesting, and those callers have
    already spent it on the A/B split.

    show_charts=False drops the pie charts for the narrow Compare Mode columns
    while keeping every NUMBER. That's the one deliberate asymmetry left
    between the arms: the figures are identical, the redundant chart is not
    repeated four times on one page. The charts encode the same split the
    ratio metric states numerically, so no information is lost.
    """
    if heading:
        st.subheader(f"🏙️ Real-World Take-Home — {major_name} in {city_name}")

    stages = list(CAREER_STAGE_OPTIONS.items())
    results = [(label, takehome_figures(scenario, major_name, key, city))
               for label, key in stages]

    containers = (st.columns(len(results)) if stage_layout == "columns"
                  else [contextlib.nullcontext()] * len(results))
    for i, ((label, figs), container) in enumerate(zip(results, containers)):
        with container:
            panel_heading(label, level=2)
            # Only the first stage carries the full threshold explanation --
            # repeating it under every stage is the same paragraph twice, and
            # the thresholds it cites differ only by that stage's effective
            # tax rate. Later stages get the one-line form.
            _render_takehome_stage(figs, major_name, verbose=show_charts and i == 0)

    # Every stage gets its own column of charts, so the shift between year 1
    # and year 10 is a left-right comparison rather than something the reader
    # has to hold in their head. It is the shift that carries the meaning: the
    # payment is a large share of a starting salary and a much smaller one of a
    # mid-career salary, and a single year-1 pie showed the worst moment as if
    # it were the whole story.
    #
    # Columns only when this block owns its nesting level. Streamlit allows one
    # level, and Compare Mode has already spent it on the A/B split -- it passes
    # stage_layout="stacked" AND show_charts=False today, but the guard is on
    # the layout rather than on show_charts so the two cannot drift into a
    # render-time crash.
    drawable = [(label, figs) for label, figs in results if figs["gross"] > 0]
    if show_charts and drawable:
        st.caption(
            "Each column is one career stage. The loan payment is the same "
            "dollar amount in both — what changes is how much of your pay it "
            "takes, which is the comparison worth making."
        )
        chart_cols = (st.columns(len(drawable)) if stage_layout == "columns"
                      else [contextlib.nullcontext()] * len(drawable))
        for (label, figs), container in zip(drawable, chart_cols):
            with container:
                panel_heading(label, level=3)
                st.plotly_chart(build_takehome_pie_chart(figs["take_home"]),
                                 use_container_width=True, config=PLOTLY_CHART_CONFIG,
                                 key=f"takehome_pie_{major_name}_{label}")
                st.plotly_chart(
                    build_takehome_vs_loan_chart(figs["take_home"]["net_take_home"] / 12,
                                                  figs["monthly_payment"]),
                    use_container_width=True, config=PLOTLY_CHART_CONFIG,
                    key=f"takehome_vs_loan_{major_name}_{label}",
                )

    return {"stages": results}


def _render_takehome_stage(figs: dict, major_name: str, verbose: bool = True) -> None:
    """One career stage's metrics and payment/take-home ratio, rendered into
    whatever container the caller is inside. Metrics stack vertically rather
    than spreading across st.columns: this is called once per stage, and the
    stages themselves already occupy the horizontal axis (or, in Compare Mode,
    a column that has no nesting budget left)."""
    gross, take_home = figs["gross"], figs["take_home"]
    monthly_payment = figs["monthly_payment"]

    if gross == 0:
        st.info(f"At this career stage, {major_name} has $0 gross income "
                "(still in training) — see Methodology for why.")

    st.metric("Gross Salary", fmt_money(gross))
    st.metric(
        "Take-Home Pay (annual, after tax)", fmt_money(take_home["net_take_home"]),
        delta=fmt_pct(take_home["effective_tax_rate"] * 100) + " effective tax rate" if gross > 0 else None,
    )
    st.metric("Monthly Disposable Income", fmt_money(figs["disposable_nominal"]))
    st.metric(
        "COL-Adjusted Disposable Income", fmt_money(figs["disposable_col_adjusted"]),
        help="Normalized to national-average purchasing power, so cities are comparable",
    )

    if not take_home["state_modeled"]:
        st.caption("State tax: N/A (National Average city has no specific state to model)")
    if figs["disposable_nominal"] < 0:
        st.warning("At this salary, city, and loan combination, disposable income is negative.")

    if gross <= 0:
        return

    # Student Loan Payment / Take-Home Pay -- the same split the charts encode
    # visually, stated as a number. Guarded against a $0 take-home edge case
    # rather than assuming gross > 0 implies positive net pay.
    monthly_net_take_home = take_home["net_take_home"] / 12
    ratio = monthly_payment / monthly_net_take_home * 100 if monthly_net_take_home > 0 else None
    if ratio is None:
        st.metric("Student Loan Payment / Take-Home Ratio", "N/A")
        return

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
    # A hover title="..." tooltip is invisible on touch devices -- shown as a
    # permanent caption instead, matching every other explanation in this app.
    # Abbreviated in the narrow Compare columns.
    if verbose:
        st.caption((
            "This is a ratio: your monthly loan payment as a percentage of your "
            "monthly take-home pay. Industry guideline (converted to your take-home "
            "basis using this scenario's own effective tax rate): under "
            f"{fmt_pct(risk['manageable_threshold'])} is considered manageable "
            "(common student-loan-budgeting guidance -- e.g. SoFi); over "
            f"{fmt_pct(risk['caution_threshold'])} matches the standard 36%-of-gross-"
            "income \"qualified borrower\" debt-to-income ceiling mortgage lenders use "
            "for ALL debts combined. Over 100% means the payment exceeds your take-home "
            "pay."
        ))
    else:
        st.caption(
            "Monthly loan payment as a share of monthly take-home pay. Under "
            f"{fmt_pct(risk['manageable_threshold'])} is considered manageable."
        )


def render_cc_path_note(cc_mode: str) -> None:
    """One-line community-college-path note rendered into the current container.

    Called from BOTH the single-scenario Loan Information section and the
    Compare Mode scenario panel, so a 2+2 transfer scenario is visibly labelled
    as one in either arm -- anything one branch shows and the other doesn't is
    an H2 confound. Renders nothing for a straight four-year start ('none'),
    matching the PDF's _cc_info_for_pdf, which omits the disclosure then too."""
    if cc_mode == "fulltime":
        st.caption(
            f"🏫 **Community-college path:** {COMMUNITY_COLLEGE_YEARS} years at a "
            "community college, then transfer to finish the same degree — the "
            "community-college years are paid out of pocket, not financed."
        )
    elif cc_mode == "associate":
        st.caption(
            "🏫 **Community-college path:** the entire degree at a community "
            "college — no transfer, because this profession is entered with a "
            "degree a community college awards on its own. Paid out of pocket, "
            "not financed."
        )
    elif cc_mode == "parttime":
        st.caption(
            f"🏫 **Community-college path:** {COMMUNITY_COLLEGE_YEARS} years at a "
            "community college **while working full-time**, then transfer to "
            "finish the same degree."
        )


def payoff_age_for(scenario: dict, current_age, program_years: int):
    """The age at which the LAST loan clears, or None when it cannot be said.

    "10.0 yrs" is not the question someone going back at 49 is asking; "repaid
    at 61" is. The article this was built for is entirely about debt outliving
    the ability to choose when to stop working, and the app already held every
    term needed to answer it.

    current_age + program_years + payoff, because repayment starts after the
    programme ends, not today. Uses combined_repayment, so an existing balance
    pushes the date out -- that balance is exactly what keeps the article's
    subjects working.

    None outside returning mode: current_age is only asked there, and inventing
    an age for an 18-year-old would be asserting something never entered.
    """
    if not current_age:
        return None
    repayment = scenario.get("combined_repayment") or scenario["repayment_result"]
    return float(current_age) + float(program_years or 0) + repayment["payoff_years"]


def render_payoff_age(scenario: dict, current_age, program_years: int,
                       retirement_age: int = 67) -> None:
    """Caption under the payoff metric. Shared by both 5c branches -- rendering
    it in one and not the other is an H2 confound, not a cosmetic gap."""
    age = payoff_age_for(scenario, current_age, program_years)
    if age is None:
        return
    if age >= retirement_age:
        st.warning(
            f"You'd be **{age:.0f}** when this is repaid — past the "
            f"{retirement_age} most people plan to retire at. The debt outlasts "
            "the working years you were counting on."
        )
    else:
        st.caption(f"You'd be **{age:.0f}** when this is fully repaid.")


def render_scenario_panel(column, scenario: dict, label: str, roi_window_years: int,
                           loan_amount: float, interest_rate: float, repayment_strategy: str,
                           col_index: float, career_data_source_name: str,
                           hs_wage_index: float = 1.0,
                           federal_cap: float = None, plus_cap: float = None, gap_rate: float = None, dependents: int = 0,
                         professional_debt: float = None,
                           include_fees: bool = False, cc_mode: str = "none",
                           wage_row_slots: int = None,
                           loan_basis: str = None, program_years: int = None,
                           current_age: int = None):
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
        panel_heading(f"Scenario {label}: {scenario['major']} — {scenario['strategy_label']}")
        render_cc_path_note(cc_mode)

        for caption in get_investment_captions(scenario):
            st.caption(caption)

        repayment_result = scenario["repayment_result"]
        roi_result = scenario["roi_result"]
        # The loan total comes FIRST, above the payment, for the same reason the
        # single-scenario branch orders it that way: the loan is the input and
        # the payment is a consequence of it. Leading with "$2,416/month"
        # invites anchoring on a figure that reads as manageable without the
        # $190,000 that produced it.
        #
        # It was missing from this panel entirely, which meant the ~half of
        # visitors get_experiment_arm() routes into Compare Mode never saw the
        # loan amount on screen at all -- while H1 is about borrowing. Same
        # asymmetry class as the break-even, take-home and wage-geography note
        # that were already moved into this helper for exactly this reason.
        #
        # loan_amount_label rather than a fixed string: it is what keeps the
        # label honest for a Simplified-mode figure ("school-reported", which
        # has no time dimension) and for the 430 occupations needing no degree.
        if loan_basis is not None:
            st.metric(loan_amount_label(loan_basis, program_years), fmt_money(loan_amount))
        # combined_repayment, not repayment_result: what the visitor pays and
        # when they are free includes any existing balance. It EQUALS
        # repayment_result when there is none, so this needs no conditional.
        shown = scenario.get("combined_repayment") or repayment_result
        st.metric(
            "Monthly Payment",
            fmt_money(shown["monthly_payment"]) if "monthly_payment" in shown else "Varies (IDR)",
        )
        st.metric("Payoff Timeline", f"{shown['payoff_years']:.1f} yrs")
        render_payoff_age(scenario, current_age, program_years)
        st.metric("Total Interest Paid", fmt_money(shown["total_interest"]))
        # Same gate as the single branch -- an asymmetry between the two is an
        # H2 confound, not a cosmetic difference.
        if (shown.get("forgiven_amount", 0) or 0) > 0:
            st.metric("Loan Forgiven", fmt_money(shown["forgiven_amount"]),
                      help="Balance written off at the end of the term. Taxable as "
                           "income that year; that tax is not modelled here.")
        if scenario.get("existing_debt"):
            st.caption(
                f"Includes {fmt_money(scenario['existing_debt'])} of student debt you "
                "already owe. That is in the payment and the payoff date, but not "
                "charged against this degree — you'd be repaying it either way."
                .replace("$", chr(92) + "$")
            )
        render_financing_note(scenario.get("financing"))
        st.metric(
            f"{roi_window_years}-Year Earnings Premium (COL-Adjusted)",
            fmt_money(roi_result["earnings_premium"]),
            delta=fmt_pct(roi_result["roi_pct"]) + " ROI" if roi_result["roi_pct"] is not None else None,
        )
        render_forgiveness_note(repayment_result, scenario.get("strategy_label"), compact=True)

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
            baseline_start_age=scenario["baseline_start_age"],
            federal_cap=federal_cap, plus_cap=plus_cap, gap_rate=gap_rate, dependents=dependents, professional_debt=professional_debt, include_fees=include_fees,
            **breakeven_kwargs())
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
            # Careers this major leads to -- compact, so each narrow compare
            # column shows its own. Same helper as the single-scenario view.
            render_major_careers(scenario["major"], compact=True)

        # Career mode's wage distribution and its geography note. Both are
        # per-occupation and therefore genuinely different between A and B, so
        # each column draws its own -- unlike the underemployment text above,
        # which is national in Career mode and rendered once below the columns.
        render_wage_distribution(scenario["major"], compact=True, caption=False,
                                  row_slots=wage_row_slots)
        render_wage_geography_note(scenario["major"])
        render_graduate_salary_disclosure(scenario.get("typical_education"))


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
    if dataset_mode == DATASET_MODE_MAJOR:
        st.caption(
            "In Major mode this is shown for the occupation group each major most "
            "commonly leads to — a representative approximation, since a major spreads "
            "across many jobs. Switch to Career mode to score a specific occupation."
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
                                hs_wage_index: float = 1.0,
                                roi_window_years: int = ROI_WINDOW_YEARS) -> tuple:
    """Recomputes a 2026 plan's repayment schedule + ROI position -- shared
    by the on-screen render (_render_plan, inside
    render_future_proofing_section) and the PDF's module-section chart
    building, so both call the exact same numbers instead of risking
    drift between two copies of this logic. Pure function, no Streamlit
    widget calls, safe to call a second time outside the on-screen
    closure with the same inputs."""
    effective_principal = scenario["effective_principal"]
    financing = scenario.get("financing")
    nonforgivable = (financing or {}).get("nonforgivable_principal", 0) or 0
    if future_plan == "2026 Tiered Standard Plan":
        term_years = calculate_tiered_standard_term(effective_principal)
        result = calculate_standard_repayment(effective_principal, interest_rate, term_years,
                                               roi_window_years=roi_window_years)
    elif nonforgivable > 0:
        # RAP is a federal plan and forgives at the end of its term, so it has
        # the same problem the main IDR path did: Parent PLUS and private money
        # are not eligible and must not be written off with the rest. Run RAP on
        # the student's own Direct loans and amortise the rest beside it.
        # Tiered Standard above needs no split -- it forgives nothing, so both
        # pools are already repaid in full.
        federal_part = simulate_rap_schedule(
            financing["forgivable_principal"], financing["forgivable_rate"],
            major_name, dependents, roi_window_years=roi_window_years)
        nonfederal_part = calculate_standard_repayment(
            financing["nonforgivable_principal"], financing["nonforgivable_rate"],
            roi_window_years=roi_window_years)
        result = combine_repayment_results(federal_part, nonfederal_part)
    else:
        result = simulate_rap_schedule(effective_principal, interest_rate, major_name, dependents,
                                        roi_window_years=roi_window_years)
    # enrollment_years/working_years/baseline_start_age come off the scenario
    # rather than defaulting: baseline_start_age's age offset is derived from
    # enrollment_years (see baseline_start_age_for), so passing one without the
    # other would place the baseline at the wrong age entirely. Before this
    # they all defaulted, which quietly compared these plans' premiums against
    # a no-head-start baseline while the rest of the page used one with it.
    #
    # hs_wage_index matters as much as the rest: without it this module
    # compared a city-scaled graduate salary against a NATIONAL high-school
    # baseline, so the metro wage premium landed on the degree's side of the
    # scale only -- the same asymmetry calculate_roi's own comment describes,
    # reappearing here because the argument simply wasn't forwarded.
    roi_result_2026 = calculate_roi(major_name, result["total_paid_in_roi_window"],
                                     scenario["total_investment"], col_index=col_index,
                                     years=roi_window_years,
                                     hs_wage_index=hs_wage_index,
                                     enrollment_years=scenario["enrollment_years"],
                                     working_years=scenario["working_years"],
                                     baseline_start_age=scenario["baseline_start_age"])
    return result, roi_result_2026


def render_future_proofing_section(scenario_a: dict, major_name_a: str, interest_rate_a: float,
                                    scenario_b: dict = None, major_name_b: str = None,
                                    interest_rate_b: float = None, col_index: float = 100.0,
                                    hs_wage_index: float = 1.0,
                                    roi_window_years: int = ROI_WINDOW_YEARS) -> dict:
    """2026 Federal Repayment Plans container: compares the RAP and Tiered
    Standard plans side by side (only rendered when enable_future_proofing is
    True). Returns the
    {column_name: value} fields for build_module_context. See the RAP_*
    constants and calculate_tiered_standard_term/calculate_rap_payment/
    simulate_rap_schedule (section 2e-2) for the real, cited mechanics
    behind these numbers."""
    st.subheader("⚖️ 2026 Federal Repayment Plans — RAP vs. Tiered Standard")
    st.caption(
        "Compares the two real 2026 federal repayment plans side by side -- the "
        "Repayment Assistance Plan (RAP) and the Tiered Standard Plan, created by "
        "the One Big Beautiful Bill Act (H.R. 1, 2025) and effective for new "
        "federal loan borrowers July 1, 2026. See Methodology for sourcing and "
        "important caveats before relying on these numbers."
    )

    def _render_plans(scenario, major_name, interest_rate, key_suffix):
        """Both 2026 plans compared side by side: RAP and Tiered Standard, on the
        same metrics and one overlaid balance chart, so a borrower sees the
        trade-off directly instead of flipping a dropdown."""
        dependents = st.number_input(
            "Dependents (for RAP)", min_value=0, max_value=10, value=0,
            key=f"rap_dependents_{key_suffix}",
            help="Reduces the RAP payment by $50/month per dependent (real OBBBA provision).",
        )
        tiered_res, tiered_roi = compute_future_plan_result(
            scenario, major_name, interest_rate, "2026 Tiered Standard Plan", dependents,
            col_index=col_index, hs_wage_index=hs_wage_index,
            roi_window_years=roi_window_years,
        )
        rap_res, rap_roi = compute_future_plan_result(
            scenario, major_name, interest_rate, "2026 Repayment Assistance Plan (RAP)", dependents,
            col_index=col_index, hs_wage_index=hs_wage_index,
            roi_window_years=roi_window_years,
        )
        term_years = calculate_tiered_standard_term(scenario["effective_principal"])
        rap_pay = calculate_rap_payment(get_annual_salary_for_year(major_name, 0), dependents)
        render_centered_table(pd.DataFrame([
            {"Plan": "Tiered Standard",
             "Monthly Payment": fmt_money(tiered_res["monthly_payment"]),
             "Payoff / Forgiveness": f"{term_years} yrs (fixed)",
             "Interest Paid": fmt_money(tiered_res["total_interest"]),
             "Forgiven (30 yr)": "—",
             f"{roi_window_years}-Yr Premium": fmt_money(tiered_roi["earnings_premium"])},
            {"Plan": "RAP (Year-1 income)",
             "Monthly Payment": fmt_money(rap_pay["monthly_payment"]),
             "Payoff / Forgiveness": f"{rap_res['payoff_years']:.1f} yrs",
             # Was hardcoded "$0 (waived)", which is the same error the
             # simulator carried: RAP waives only the interest a payment does
             # not cover, so a borrower whose payment exceeds the accrual pays
             # all of it. Read the computed figure like every other row does.
             "Interest Paid": fmt_money(rap_res["total_interest"]),
             "Forgiven (30 yr)": fmt_money(rap_res["forgiven_amount"]),
             f"{roi_window_years}-Yr Premium": fmt_money(rap_roi["earnings_premium"])},
        ]))
        st.caption(
            "Under **RAP**, payments track your income, **unpaid** interest is waived — the "
            "part your payment doesn't cover, which is nothing if you earn enough to cover it "
            "— and the government matches up to $50/month toward principal, so the balance "
            "never grows and anything left is forgiven after 30 years. **Tiered Standard** is a fixed "
            "payment over a term set by your balance. Premium is the COL-adjusted "
            f"{roi_window_years}-year earnings premium under each plan."
        )
        st.plotly_chart(
            build_comparison_balance_chart(tiered_res["schedule"], "Tiered Standard",
                                            rap_res["schedule"], "RAP"),
            use_container_width=True, key=f"future_compare_chart_{key_suffix}", config=PLOTLY_CHART_CONFIG,
        )
        return "Both (Tiered Standard & RAP)"

    if scenario_b is not None:
        col_a, col_b = st.columns(2)
        with col_a:
            panel_heading(f"Scenario A: {major_name_a}")
            plan_a = _render_plans(scenario_a, major_name_a, interest_rate_a, "a")
        with col_b:
            panel_heading(f"Scenario B: {major_name_b}")
            plan_b = _render_plans(scenario_b, major_name_b, interest_rate_b, "b")
        context = {
            "future_forecasting_active": True, "future_plan_selected": plan_a,
            "scenario_b_future_plan_selected": plan_b,
        }
        macro_major = major_name_a
    else:
        plan_a = _render_plans(scenario_a, major_name_a, interest_rate_a, "single")
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
    # The Trade Apprenticeship module was removed (see migrations.sql). Its
    # apprenticeship_* columns are retained in Supabase and simply stop being
    # written -- dropping them would destroy the history they already hold.
    return context


if compare_mode:
    st.subheader("⚖️ Scenario Comparison")
    scenario_a = compute_scenario_results(major, loan_amount, interest_rate, repayment_strategy,
                                           personal_contribution, city_info["col_index"],
                                           roi_window_years=roi_horizon_years,
                                           hs_wage_index=get_metro_wage_index(city),
                                           enrollment_years=enrollment_years_a,
                                           working_years=working_years_a,
                                           baseline_start_age=baseline_start_age_for(program_years_a, enrollment_years_a),
                                           federal_cap=federal_cap_a, plus_cap=plus_cap_a, gap_rate=gap_rate_a, dependents=rap_dependents, professional_debt=professional_debt_a, include_fees=True,
                                           **returning_kwargs())
    scenario_b = compute_scenario_results(major_b, loan_amount_b, interest_rate_b, repayment_strategy_b,
                                           personal_contribution_b, city_info["col_index"],
                                           roi_window_years=roi_horizon_years,
                                           hs_wage_index=get_metro_wage_index(city),
                                           enrollment_years=enrollment_years_b,
                                           working_years=working_years_b,
                                           baseline_start_age=baseline_start_age_for(program_years_b, enrollment_years_b),
                                           federal_cap=federal_cap_b, plus_cap=plus_cap_b, gap_rate=gap_rate_b, dependents=rap_dependents, professional_debt=professional_debt_b, include_fees=True,
                                           **returning_kwargs())

    # Both wage charts reserve the same number of geography rows, so the
    # national curve -- the one series genuinely common to A and B -- sits at
    # the same height in each column. Computed before either panel renders,
    # since neither can see the other's occupation.
    _wage_slots = max(wage_distribution_rows(scenario_a["major"]),
                       wage_distribution_rows(scenario_b["major"]))

    col_a, col_b = st.columns(2)
    render_scenario_panel(
        col_a, scenario_a, "A", roi_horizon_years,
        loan_amount, interest_rate, repayment_strategy,
        city_info["col_index"], career_data_source,
        hs_wage_index=get_metro_wage_index(city),
        federal_cap=federal_cap_a, plus_cap=plus_cap_a, gap_rate=gap_rate_a, dependents=rap_dependents, professional_debt=professional_debt_a, include_fees=True,
        cc_mode=cc_mode_a, wage_row_slots=_wage_slots,
        loan_basis=loan_basis_a, program_years=program_years_a,
        current_age=st.session_state.get("current_age") if is_returning else None,
    )
    render_scenario_panel(
        col_b, scenario_b, "B", roi_horizon_years,
        loan_amount_b, interest_rate_b, repayment_strategy_b,
        city_info["col_index"], career_data_source,
        hs_wage_index=get_metro_wage_index(city),
        federal_cap=federal_cap_b, plus_cap=plus_cap_b, gap_rate=gap_rate_b, dependents=rap_dependents, professional_debt=professional_debt_b, include_fees=True,
        cc_mode=cc_mode_b, wage_row_slots=_wage_slots,
        loan_basis=loan_basis_b, program_years=program_years_b,
        current_age=st.session_state.get("current_age") if is_returning else None,
    )

    # Career mode's underemployment text is national and identical for both
    # scenarios, so it renders once here rather than twice inside the panels.
    # Major mode's is per-major and lives in the panel instead.
    if dataset_mode == DATASET_MODE_CAREER:
        # Same reasoning for the wage-distribution explanation: the CHARTS are
        # per-occupation and genuinely differ between A and B, but the sentence
        # explaining how to read one is identical, and printing it under both
        # columns just doubled it.
        if any(get_wage_distribution_context(s["major"])
               for s in (scenario_a, scenario_b)):
            st.caption(WAGE_DISTRIBUTION_CAPTION)
        st.info(underemployment_disclosure(None))

    # Take-home, per scenario. Compare Mode had none of this: the contrast arm
    # is randomly assigned, so half of all visitors never saw their disposable
    # income, which made the two arms differ by more than the contrast H2
    # claims to measure. Charts off -- the columns are narrow and the same
    # split is stated numerically by the ratio metric.
    st.subheader(f"🏙️ Real-World Take-Home — {city}")
    th_col_a, th_col_b = st.columns(2)
    with th_col_a:
        panel_heading(f"A: {scenario_a['major']}")
        render_takehome_block(scenario_a, major, city, city_info,
                               show_charts=False, heading=False, stage_layout="stacked")
    with th_col_b:
        panel_heading(f"B: {scenario_b['major']}")
        render_takehome_block(scenario_b, major_b, city, city_info,
                               show_charts=False, heading=False, stage_layout="stacked")

    st.plotly_chart(
        build_comparison_balance_chart(
            scenario_a["repayment_result"]["schedule"], f"A: {scenario_a['major']}{cc_chart_label_suffix(cc_mode_a)}",
            scenario_b["repayment_result"]["schedule"], f"B: {scenario_b['major']}{cc_chart_label_suffix(cc_mode_b)}",
        ),
        use_container_width=True, config=PLOTLY_CHART_CONFIG,
    )
    st.plotly_chart(
        build_net_position_chart(
            net_position_frame(
                [(f"A: {scenario_a['major']}{cc_chart_label_suffix(cc_mode_a)}", scenario_a),
                 (f"B: {scenario_b['major']}{cc_chart_label_suffix(cc_mode_b)}", scenario_b)],
                city_info["col_index"], get_metro_wage_index(city), roi_horizon_years),
            roi_horizon_years,
            baseline_head_start_years=max(scenario_a["enrollment_years"],
                                           scenario_b["enrollment_years"]),
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
                                                          col_index=city_info["col_index"],
                                                          hs_wage_index=get_metro_wage_index(city),
                                                          roi_window_years=roi_horizon_years)

    module_context = build_module_context(
        prestige_tier_a if enable_prestige_mode else None,
        prestige_tier_b if enable_prestige_mode else None,
        ai_context, future_context,
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
        loan_source_a=loan_source_a, loan_source_b=loan_source_b,
        federal_cap_a=federal_cap_a, plus_cap_a=plus_cap_a, gap_rate_a=gap_rate_a, dependents=rap_dependents, professional_debt_a=professional_debt_a, professional_school_a=st.session_state.get('prof_school_a'),
        federal_cap_b=federal_cap_b, plus_cap_b=plus_cap_b, gap_rate_b=gap_rate_b, professional_debt_b=professional_debt_b, professional_school_b=st.session_state.get('prof_school_b'), include_fees=True,
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
            st.query_params.from_dict({**session_query_params(), **build_share_params(
                career_data_source, major, city, school_name_a, in_state_a,
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
            )})
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
                                         working_years=working_years_a,
                                         baseline_start_age=baseline_start_age_for(program_years_a, enrollment_years_a),
                                         federal_cap=federal_cap_a, plus_cap=plus_cap_a, gap_rate=gap_rate_a, dependents=rap_dependents, professional_debt=professional_debt_a, include_fees=True,
                                           **returning_kwargs())
    effective_principal = scenario["effective_principal"]
    repayment_result = scenario["repayment_result"]
    strategy_label = scenario["strategy_label"]
    roi_result = scenario["roi_result"]

    # ---- 5c-1. Loan Information --------------------------------------------

    st.subheader(f"💳 Loan Information — {strategy_label}")

    render_cc_path_note(cc_mode_a)

    loan_caption = get_loan_principal_caption(scenario)
    if loan_caption:
        st.caption(loan_caption)

    # Computed unconditionally (it's cheap) so the PDF builder always has it; the
    # on-screen table below is what's shown conditionally.
    loan_schedule_a = compute_loan_schedule_by_year(
        effective_coa_per_year_a, personal_contribution_per_year_a, grants_per_year_a, inflation_rate_a,
        years=program_years_a,
        cc_years=cc_years_a, cc_coa_per_year=effective_cc_coa_per_year_a, finance_cc_years=False
    )
    if loan_basis_a == "no_program":
        st.caption(
            "BLS lists no degree requirement for this career, so nothing is financed "
            "and no tuition is charged against it. The earnings comparison below still "
            "applies -- it's the cost side that goes to zero, not the pay."
        )
    elif loan_basis_a == "reported_scaled":
        st.caption((
            f"This uses **{fmt_money(default_loan_a)}** -- an **estimate**, not a reported "
            f"figure. College Scorecard publishes {fmt_money(reported_debt_a)} for "
            f"{school_name_a}, but that is one institution-wide median blending completers "
            "of every credential length, with no per-year or per-credential breakdown. We "
            f"scale it by the ratio of cumulative federal Direct borrowing limits, "
            f"{program_years_a} years against {UNDERGRAD_YEARS} "
            f"(**{simplified_scale_a * 100:.0f}%**), because the Scorecard figure counts "
            "**federal loans only** and federal limits are what bound federal borrowing. "
            "Direct PLUS and private borrowing aren't included either way, so a student "
            "who needed those owes more. Switch to Detailed mode to model your own cost, "
            "aid, and gap financing instead."
        ).replace("$", r"\$"))
    elif loan_source_a == "college":
        # The loan is the college-reported figure, not a per-year cost buildup, so
        # a year-by-year COA->loan table would contradict the total. Show the
        # reported number instead; the cost-based per-year breakdown appears only
        # when the personal calc is actually the loan in use.
        st.caption((
            f"This uses **{fmt_money(default_loan_a)}** -- the median debt graduates of "
            f"{school_name_a} who borrowed leave with (College Scorecard), across "
            "completers of every credential length. It counts "
            "**federal loans only** -- Direct PLUS and private borrowing aren't included, "
            "so a student who needed those owes more. Switch to Detailed mode in the "
            "sidebar to model your own cost, aid, and gap financing instead."
        ).replace("$", r"\$"))
    else:
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
    st.metric(loan_amount_label(loan_basis_a, program_years_a), fmt_money(loan_amount))
    # "Overridden" is measured against whichever default is active, so the note
    # only fires on a real manual change (not on the expected college-vs-personal
    # gap that exists by design).
    if abs(loan_amount - default_loan_a) >= 1:
        if loan_source_a == "college":
            st.caption((
                f"You changed the loan from the college-reported {fmt_money(default_loan_a)} to "
                f"{fmt_money(loan_amount)} in the sidebar -- every calculation below uses your amount."
            ).replace("$", r"\$"))
        else:
            st.caption((
                f"You overrode the calculated total ({fmt_money(computed_loan_amount_a)}) in the "
                "sidebar -- the table above still shows the calculated year-by-year breakdown, "
                "but every calculation below uses your overridden total instead."
            ).replace("$", r"\$"))

    # See the compare branch: combined_repayment includes any existing balance
    # and equals repayment_result when there is none.
    _shown = scenario.get("combined_repayment") or repayment_result
    # A fourth column only when there IS forgiveness. An always-present
    # "Forgiven: $0" would read as a plan feature that failed rather than one
    # that never applied -- and under Standard or Tiered Standard nothing is
    # forgivable at all, so the metric would be meaningless there.
    _forgiven = _shown.get("forgiven_amount", 0) or 0
    loan_metric_cols = st.columns(4 if _forgiven > 0 else 3)
    loan_metric_cols[0].metric(
        "Monthly Payment",
        fmt_money(_shown["monthly_payment"]) if "monthly_payment" in _shown else "Varies (IDR)",
    )
    loan_metric_cols[1].metric("Payoff Timeline", f"{_shown['payoff_years']:.1f} yrs")
    loan_metric_cols[2].metric("Total Interest Paid", fmt_money(_shown["total_interest"]))
    if _forgiven > 0:
        loan_metric_cols[3].metric(
            "Loan Forgiven", fmt_money(_forgiven),
            help="Balance written off at the end of the plan's term. Taxable as "
                 "ordinary income in the year it is discharged (since January 1, "
                 "2026), and that tax is not included in any figure here.",
        )
    render_payoff_age(scenario, st.session_state.get("current_age") if is_returning else None,
                       program_years_a)
    if scenario.get("existing_debt"):
        st.caption(
            f"Includes {fmt_money(scenario['existing_debt'])} of student debt you already "
            "owe. That is in the payment and the payoff date, but not charged against "
            "this degree — you'd be repaying it either way.".replace("$", chr(92) + "$")
        )

    render_financing_note(scenario.get("financing"))

    render_forgiveness_note(repayment_result, strategy_label)

    st.plotly_chart(
        build_balance_chart(repayment_result["schedule"], strategy_label),
        use_container_width=True, config=PLOTLY_CHART_CONFIG,
    )

    # ---- 5d. Real-World Take-Home Snapshot --------------------------------
    # Rendered via the shared helper so Compare Mode shows the same figures --
    # see render_takehome_block. The returned values feed the PDF below.
    _th = render_takehome_block(scenario, major, city, city_info)
    takehome_stages = _th["stages"]

    # ---- 5e. Financial Position (horizon per the sidebar's ROI Horizon) -----

    _cf = counterfactual_vocab()
    _cf_window = _cf["window_phrase"].format(years=roi_horizon_years)
    st.subheader(f"📊 {roi_horizon_years}-Year Financial Position")
    st.caption((
        f"This compares two paths over {_cf_window}: going into "
        f"**{major}** (paying off the loan above along the way) vs. {_cf['instead_of']} "
        f"and taking on **no loan for this degree**. "
        f"Both numbers are adjusted for the cost of living in **{city}** -- that's what "
        f"**\"COL-Adjusted\"** means -- so it's a fair, apples-to-apples comparison of real "
        f"spending power, not just which raw number is bigger. **Earnings Premium** is simply "
        f"the difference between the two: how much more (or less) you'd have after "
        f"{roi_horizon_years} years by choosing {major} instead of {_cf['instead_of_short']}."
    ).replace("$", r"\$"))

    investment_caption = get_total_investment_caption(scenario)
    if investment_caption:
        st.caption(investment_caption)

    position_cols = st.columns(3)
    position_cols[0].metric(
        f"{_cf['metric_label']} — {roi_horizon_years}-Yr Net Position{_cf['no_loan_suffix']}",
        fmt_money(roi_result["hs_net_position"]),
    )
    position_cols[1].metric(f"{major} — {roi_horizon_years}-Yr Net Position", fmt_money(roi_result["major_net_position"]))
    position_cols[2].metric(
        "Earnings Premium (COL-Adjusted)",
        fmt_money(roi_result["earnings_premium"]),
        delta=fmt_pct(roi_result["roi_pct"]) + " ROI" if roi_result["roi_pct"] is not None else None,
        help=f"How much more money you'd have after {roi_horizon_years} years by going into "
             f"this career instead of {_cf['instead_of']} -- "
             "bigger is better. \"COL-Adjusted\" means we've factored in how "
             "expensive it is to live in your chosen city, so this is a fair "
             "comparison no matter where you live.",
    )

    st.plotly_chart(
        build_net_position_chart(
            net_position_frame([(major, scenario)], city_info["col_index"],
                                get_metro_wage_index(city), roi_horizon_years),
            roi_horizon_years,
            baseline_head_start_years=scenario["enrollment_years"],
        ),
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
        baseline_start_age=scenario["baseline_start_age"],
        federal_cap=federal_cap_a, plus_cap=plus_cap_a, gap_rate=gap_rate_a, dependents=rap_dependents, professional_debt=professional_debt_a, include_fees=True,
            **breakeven_kwargs())
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

    # Careers this major leads to -- Major mode only (a Career-mode pick already
    # IS a specific occupation). Shared helper, also called from render_scenario_
    # panel so both result branches match.
    if dataset_mode == DATASET_MODE_MAJOR:
        render_major_careers(major)

    # What this occupation's pay actually spreads across, rather than the one
    # median the projection runs on. Career mode only (see
    # get_wage_distribution_context); the same call sits in
    # render_scenario_panel so Compare Mode shows it too.
    render_wage_distribution(major)

    # Which geography the salary above came from -- shared helper, called from
    # render_scenario_panel too so Compare Mode shows it as well.
    render_wage_geography_note(major)
    render_graduate_salary_disclosure(scenario.get("typical_education"))

    ai_context = {}
    if enable_ai_mode:
        ai_context = render_ai_risk_section(major)

    future_context = {}
    if enable_future_proofing:
        future_context = render_future_proofing_section(scenario, major, interest_rate,
                                                          col_index=city_info["col_index"],
                                                          hs_wage_index=get_metro_wage_index(city),
                                                          roi_window_years=roi_horizon_years)

    module_context = build_module_context(
        prestige_tier_a if enable_prestige_mode else None, None, ai_context, future_context,
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
        major, city, school_name_a, in_state_a, takehome_stages,
        coa_per_year_a, personal_contribution_per_year_a, grants_per_year_a,
        interest_rate, repayment_strategy, loan_amount, loan_schedule_a,
        scenario,
        module_context=module_context, start_year_a=start_year_a,
        col_index=city_info["col_index"], roi_window_years=roi_horizon_years,
        loan_source_a=loan_source_a,
        loan_basis_a=loan_basis_a, reported_debt_a=reported_debt_a,
        federal_cap_a=federal_cap_a, plus_cap_a=plus_cap_a, gap_rate_a=gap_rate_a, dependents=rap_dependents, professional_debt_a=professional_debt_a, professional_school_a=st.session_state.get('prof_school_a'), include_fees=True,
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
            st.query_params.from_dict({**session_query_params(), **build_share_params(
                career_data_source, major, city, school_name_a, in_state_a,
                coa_per_year_a, personal_contribution_per_year_a, grants_per_year_a,
                interest_rate, repayment_strategy, False, start_year_a=start_year_a,
                roi_horizon_years=roi_horizon_years,
                cc_mode_a=cc_mode_a, cc_state_a=cc_state_key_a, cc_coa_per_year_a=cc_coa_per_year_a,
            )})
            save_scenario_share({**build_scenario_context(
                major, loan_amount, interest_rate, repayment_strategy, personal_contribution,
                school_name_a, inflation_rate_a, grants_per_year_a, scenario,
                roi_horizon_years=roi_horizon_years,
                start_year_a=start_year_a,
            ), **module_context})
            components.html(COPY_URL_TO_CLIPBOARD_JS, height=0)
            st.success("Shareable link copied to your clipboard! Paste it anywhere to share this exact scenario.")

st.divider()

# "Get Your Real Numbers" sits here -- after the results, just above the survey.
# It's rendered at module level (outside the single/compare branches that rejoined
# above), so both experiment arms see it identically -- keeping it out of either
# branch is what stops it becoming an H2 confound. school_name_b only exists when
# Compare Mode is on (assigned inside the Scenario B sidebar expander); the
# conditional short-circuits so the name isn't looked up in single-scenario mode.
render_get_accurate_inputs(
    school_name_a, school_name_b if compare_mode else None, compare_mode, enable_prestige_mode)

# ---- 5e. Anonymous Impact Survey ------------------------------------------

# Hidden outright for a student who told the pre block they are under
# RESEARCH_MIN_AGE. Gating only the pre questions would have left the larger
# instrument -- the one carrying the free-text box -- fully open to exactly
# the visitors the floor exists to exclude.
#
# A visitor who never answered the role question still sees the survey; an
# unanswered role is not a claim to be a minor. That path is caught inside the
# form instead, where selecting Student requires the same attestation.
if (postsurvey_enabled() and not st.session_state.survey_submitted
        and research_participation_allowed()):
    st.subheader("📋 Help Us Measure Impact")
    # Consent, shown BEFORE the form rather than inside it. Inside an st.form
    # nothing renders until the form is constructed and nothing submits until
    # the button, so a notice in there is read at the same moment it is agreed
    # to -- which is not consent, it is a receipt.
    #
    # Deliberately short and in the app's own register. The elements 45 CFR
    # 46.116 requires for minimal-risk research are all here -- that it is
    # research, its purpose, what is asked, voluntariness, risks, benefits,
    # what is recorded, a contact, and an affirmative act -- but a wall of
    # legalese aimed at an 18-year-old is consent in form only.
    #
    # ACCURACY NOTE, do not "simplify" this away: the text says the SURVEY
    # saves nothing until submitted, which is true, and separately admits that
    # page views and scenario changes are recorded as you go, which is also
    # true (log_usage_event and maybe_log_scenario_event both fire before any
    # consent is given). An earlier draft said "nothing is saved until you
    # press Save" full stop, which would have been a false statement about the
    # page as a whole.
    with st.expander("Before you answer — what this is and what's recorded", expanded=False):
        st.markdown(
            f"""
These questions are part of a **research project** on whether tools like this change
how people think about college debt. Taking part is **voluntary** and takes about a
minute. The calculator works exactly the same whether you answer or not.

**What's recorded if you submit:** your answers, plus the scenario on screen at that
moment — school, major, loan amount, and the resulting figures. Separately, and as you
go, the app records that a page was opened and which majors and schools were tried.

**What's never recorded:** your name, email, IP address, or any account — there isn't
one. Each visit gets a random ID that is discarded when you close the tab, so two
visits cannot be linked to each other or to you.

**Risks and benefits:** no known risks beyond using any web page, and no direct benefit
to you. Results may be published in aggregate; nothing identifying anyone will appear.

**You can stop at any time** by not submitting, or by closing the tab. The survey itself
saves nothing until you press Submit.

Questions about the research? Contact **veervish11@gmail.com**.

*By submitting, you agree to take part. You must be {RESEARCH_MIN_AGE} or over.*
"""
        )
    # The age gate sits OUTSIDE the form, so it can react. Inside an st.form
    # nothing reruns until submit, which is why the in-form checkbox could only
    # ever refuse a submission after every question had been answered -- and
    # render_presurvey's own comment rejects exactly that: "an ineligible
    # visitor is never asked a research question at all -- collecting the
    # answers and discarding them afterwards would still be collecting them."
    # That held while the pre-survey ran first. With the pre-survey off by
    # default it stopped holding, and this restores it.
    #
    # Skipped when the pre block already attested, so a recruited visitor is
    # not asked twice.
    _pre_attested = bool(st.session_state.get("presurvey_role")
                          and st.session_state.get("presurvey_age_ok"))
    if not _pre_attested:
        gate_ok = st.checkbox(
            f"I am {RESEARCH_MIN_AGE} or older",
            key="survey_age_gate",
            help="These questions are research, and taking part is limited to "
                  f"people {RESEARCH_MIN_AGE} and over. The calculator above is "
                  "unaffected either way.",
        )
        if not gate_ok:
            st.caption(
                f"The questions appear once you confirm you're {RESEARCH_MIN_AGE} "
                "or over. **Nothing else on this page changes** — the calculator "
                "is yours to use regardless."
            )
    # Gated on the attestation above, so an ineligible visitor is never ASKED
    # a research question -- not asked and then refused. render_presurvey made
    # exactly this point ("collecting the answers and discarding them
    # afterwards would still be collecting them"), and it held only while the
    # pre-survey ran first. With the pre-survey off by default it stopped
    # holding, and this restores it.
    if _pre_attested or st.session_state.get("survey_age_gate"):
        with st.form("survey_form", clear_on_submit=True):
            # Asked here only if the pre block did not already get it. Asking the
            # same person their role twice in one session is not just redundant --
            # the two answers can disagree, and nothing in the schema says which
            # one the scenario columns were recorded under.
            #
            # index=None so an ignored dropdown stays distinguishable from an
            # answer. The old version defaulted to "Parent", which meant a row
            # could not tell a parent from someone who never touched the control
            # -- the answer-vs-absence failure major_explicitly_selected exists to
            # prevent for the major, and it silently inflated one category.
            _pre_role = st.session_state.get("presurvey_role")
            if _pre_role:
                st.caption(f"Answering as: **{_pre_role}** (from the questions at the top)")
                respondent_role = _pre_role
                # Already attested at the top, or this block would not render.
                form_age_ok = True
            else:
                respondent_role = st.selectbox(
                    "I am a...", PRESURVEY_ROLE_OPTIONS, index=None,
                    placeholder="Select one")
                # Shown unconditionally rather than only for Students: inside an
                # st.form nothing reruns until submit, so the checkbox cannot
                # appear in response to the role choice the way it does at the top
                # of the page. Asking everyone is the cost of that constraint --
                # it is only ENFORCED for the roles that need it, below.
                form_age_ok = st.checkbox(f"I am {RESEARCH_MIN_AGE} or older")
            # index=None for the same answer-vs-absence reason as the role above.
            # "Already graduated" is new and is not padding: the 18+ floor means a
            # participating student has often finished high school already, and
            # without it they must either pick a false year or leave the default.
            hs_graduation_year = st.selectbox(
                "Expected High School Graduation Year",
                ["Already graduated", "2026", "2027", "2028", "2029", "2030", "Not applicable"],
                index=None, placeholder="Select one",
            )

            # ---- The post half of the paired measurement -------------------------
            # Above perception_change deliberately. That item asks whether the tool
            # CHANGED anything, which is the most leading question on the page; a
            # respondent who answers it first has been told what the researcher is
            # looking for, and the paired items are the better measures. Let the
            # legacy item absorb the priming rather than spread it.
            #
            # Wording is present-tense state ("...now"), never "did this change
            # your mind?". We difference the two answers ourselves; asking the
            # respondent to report the change invites them to supply one.
            st.markdown("---")
            post_schools = st.radio(POSTSURVEY_SCHOOLS_QUESTION,
                                     list(PRESURVEY_SCHOOLS_OPTIONS),
                                     index=None, horizontal=True)

            # A counsellor is not answering about their own borrowing, so the
            # question is not put to them -- matching the pre block. Only
            # suppressible when the role is already known: inside a form nothing
            # reruns, so if the role is being chosen right here the question has to
            # render, and an answer from a counsellor is dropped at submit instead.
            _post_borrowing_applies = _pre_role not in ROLES_WITHOUT_BORROWING
            if _post_borrowing_applies:
                post_borrowing = st.radio(POSTSURVEY_BORROWING_QUESTION,
                                           list(PRESURVEY_BORROWING_OPTIONS),
                                           index=None, format_func=escape_money_markdown)
            else:
                post_borrowing = None
            st.markdown("---")

            perception_change = st.radio(
                "Did this tool change how you view your target major or university choice?",
                ["Yes - significantly", "Yes - slightly", "No - it confirmed my choice", "No - no impact"],
            )
            feedback_text = st.text_area("How did this data influence your thinking? (optional)")
            submitted = st.form_submit_button("Submit Feedback")

            # Enforced at submit because a form cannot react before it. Checked
            # BEFORE compute_scenario_results and before any write: an ineligible
            # submission must not be assembled and then discarded, since the
            # discarding is the only thing standing between it and Supabase.
            if submitted and respondent_role in ROLES_REQUIRING_AGE_ATTESTATION \
                    and not form_age_ok:
                st.warning(
                    f"This survey is for people {RESEARCH_MIN_AGE} and over, so this "
                    "response wasn't recorded. Everything else on the page is "
                    "unaffected — the calculator is yours to use."
                )
                log_usage_event("survey_blocked_minor")
                submitted = False

            if submitted:
                # Dropped rather than stored when the role turns out not to take
                # the question. Only reachable when the role was chosen inside the
                # form, where the widget could not be hidden reactively -- storing
                # it anyway would put a counsellor's borrowing answer in the same
                # column as a student's, which is the noise the exclusion exists
                # to prevent.
                if respondent_role in ROLES_WITHOUT_BORROWING:
                    post_borrowing = None

                # Coded once, so every consumer sees the same vocabulary as the
                # pre answers. "n_a" is not "skip": never asked and
                # asked-then-declined are different facts.
                postsurvey_codes = build_instrument_context(
                    post_schools, post_borrowing, respondent_role)

                # Recomputed fresh (cheap, pure functions, no API calls) rather
                # than reused from st.session_state, so the survey reflects
                # exact click-time state.
                scenario_a = compute_scenario_results(major, loan_amount, interest_rate, repayment_strategy,
                                                       personal_contribution, city_info["col_index"],
                                                       roi_window_years=roi_horizon_years,
                                                       hs_wage_index=get_metro_wage_index(city),
                                                       enrollment_years=enrollment_years_a,
                                                       working_years=working_years_a,
                                                       baseline_start_age=baseline_start_age_for(program_years_a, enrollment_years_a),
                                                       federal_cap=federal_cap_a, plus_cap=plus_cap_a, gap_rate=gap_rate_a, dependents=rap_dependents, professional_debt=professional_debt_a, include_fees=True,
                                               **returning_kwargs())
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
                                                           working_years=working_years_b,
                                                           baseline_start_age=baseline_start_age_for(program_years_b, enrollment_years_b),
                                                           federal_cap=federal_cap_b, plus_cap=plus_cap_b, gap_rate=gap_rate_b, dependents=rap_dependents, professional_debt=professional_debt_b, include_fees=True,
                                               **returning_kwargs())
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

                # Logged to usage_logs as well as (eventually) the survey row.
                # Same reasoning as the pre answers: this channel needs no
                # migration, so the paired measurement starts producing data
                # immediately, and a survey insert that fails silently -- every
                # writer here catches and returns False -- does not take the
                # answers with it. The survey-row copy arrives with the migration
                # and is what makes the pair atomic on one row.
                log_usage_event(
                    "postsurvey_answered"
                    f":considering={postsurvey_codes['post_schools_considered']}"
                    f":borrow={postsurvey_codes['post_borrow_willingness']}"
                    f":pre={'1' if st.session_state.get('presurvey_answered') else '0'}"
                    f":v={PRESURVEY_INSTRUMENT_VERSION}")

                saved = save_survey_response(respondent_role, hs_graduation_year,
                                              perception_change, feedback_text, context,
                                              instrument=postsurvey_codes)
                if saved:
                    st.session_state.survey_submitted = True
                    st.rerun()
                else:
                    st.error("Something went wrong saving your response -- please try again.")
elif st.session_state.survey_submitted:
    # Only for someone who actually submitted. This was a bare `else`, which
    # after the eligibility condition was added to the `if` above began
    # thanking under-18 visitors for a response that was never collected --
    # a false statement, and on precisely the surface where the app is making
    # claims about what it does with data.
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
student might be evaluating. Where BLS does publish a standard length, the
model uses it: an associate's degree is charged two years, and a job needing no
degree at all is charged none. See "How long we assume you're enrolled" below.

**Which geography a salary comes from.** You don't pick this — it follows the
city you pick. For each occupation we take the finest geography BLS actually
publishes: your **metro** if it reports that job, otherwise your **state**,
otherwise the **national** figure. Every one of those is real government data
for that place, not a national number scaled up, and the page says underneath
the salary which of the three you're looking at.

The fallbacks matter more than they sound. BLS won't publish a wage for a job
in a metro where too few people do it, and that's roughly a fifth of
occupations in a typical city — 227 of 836 in Austin, for instance. Those used
to drop straight to a national average; now all but 41 of them land on Texas's
own statewide figure first, which is much closer to the truth. Only when both
the metro and the state suppress a job do you see a national number, and it's
labelled as one.

This replaced a "Career Salary Data: National / California" control that let
you choose a wage basis independently of your city. That combination could
disagree with itself — picking California while living in New York showed
California wages for the jobs New York doesn't report, while the page called
them national figures.

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
  kicks in. On top of your loan we add medical school's own debt — **your
  school's median if you name one in the sidebar**, otherwise a national
  **$205,000** from AAMC's 2024 data
  ([source](https://www.aamc.org/data-reports/students-residents/report/physician-education-debt-and-cost-attend-medical-school)).
- **Law**: 3 years with no income (law school), then the real Lawyer
  salary from the table above kicks in. We add law school's debt on top of
  your loan — your school's median where you name one, otherwise a national
  **$130,000** from the ABA's 2024 survey
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

**"Careers this major commonly leads to" (Major mode).** Beside that
disclosure we list a few example occupations for the selected major, each at
its national median pay. These are real BLS occupations drawn from the same
occupation major group the major maps to (the SOC group described in the AI
module note below) — a **representative sample of the field, not an
exhaustive or guaranteed list**, since a major spreads across many jobs.
Wages are the national BLS medians already used throughout this app, shown at
the national level regardless of your selected city so the "leads to" set
stays stable. Occupations that a four-year degree doesn't
typically lead to (those BLS marks as needing less than a bachelor's) are
filtered out. This is our own summary of public BLS data — it is not drawn
from any subscription careers guide.

**How long we assume you're enrolled.** Cost of Attendance is a per-year
figure, so turning it into a total needs a program length. We use four years
for a bachelor's — and **two years for an occupation BLS says is typically
entered with an associate's degree**, because charging four years of tuition
to reach a two-year credential roughly doubles the debt, and against a private
four-year sticker price it overstates it by far more. That shorter length
flows through everything: the loan total, the federal borrowing cap (which is
set per year in school), the foregone-earnings option, and the break-even.
Picking the community-college path on a two-year program covers the whole
program rather than half of it.

**And zero years for a job that needs no degree.** BLS says 430 of the 825
occupations here are entered with a high school diploma or no credential at all
— 52% of the list — and until recently every one of them was charged four years
of tuition and four years of given-up wages for a degree the job never asks
for. They're now charged none: no loan, no enrollment gap, no break-even. What
doesn't change is the pay comparison, and that's the point — a nuclear power
reactor operator needs no degree and still earns far above the high-school
median. The cost side goes to zero; the earnings side stands.

We still don't guess at the remaining two levels. "Postsecondary nondegree
award" covers everything from a six-week certificate to an eighteen-month
program, and "some college, no degree" has no defined end at all. Those still
get four years, say so on screen, and have their break-even suppressed rather
than printing a number built on a length we don't believe.

**One more place length matters: the Simplified loan.** In Simplified mode the
loan is the school's median completer debt from College Scorecard. That is a
single institution-wide number covering everyone who finished — two-year and
four-year completers together — and it carries no per-year or per-credential
breakdown at all. For a two-year career at a four-year school, handing it over
unchanged charges roughly double. So we scale it by the ratio of cumulative
federal Direct borrowing limits, two years against four (44%), on the grounds
that the Scorecard figure counts federal loans only and federal limits are what
bound federal borrowing. It's shown as an **estimate**, with the raw published
figure beside it.

We do not scale it at a community college. There, "institution-wide" already
means two-year completers, so the reported number is already the right one and
halving it would introduce the very error we're trying to remove. The test is
the school's predominant credential, not the career's length.

Why not just look up debt for two-year programs directly? Scorecard does
publish it, per program of study — but only per individual CIP program, never
rolled up to a school-level associate's figure, and it's suppressed wherever
too few borrowers finished that exact program. At the community colleges where
this matters most, that's often every program. An estimate we can explain beat
a real number we mostly can't get.

**Where the pay actually lands (Target Profession mode).** Under the salary
figures we draw the spread of what people in that occupation really earn. BLS
publishes five points for each one — the 10th, 25th, 50th, 75th and 90th
percentile wage — and no individual worker records at all, so a true
count-the-people histogram isn't something anyone can build from it, us
included. What the percentiles *do* fix exactly is how many workers sit
between any two of them: a quarter of the workforce earns between the 25th and
the 50th, by definition. The curve is drawn through those five points and peaks
at the median, matching the style O*NET uses for the same data. Read it for
**position and spread** — where this job's pay sits, and how far it ranges — not
as a count of people: its height is illustrative, and it is deliberately the
same for every curve so that comparing two of them is a purely left-right
comparison. When your city has its own published figures, the national curve is
drawn beneath it, so you can see both how much higher local pay runs and how
much wider it spreads. The bottom and top 10% have no published cutoff on the
far side, so we state them in words rather than inventing a width for them.
Percentiles follow the same city as the salary above when BLS publishes them
for that metro. There's no equivalent for Intended Major mode: a major isn't
an occupation, and the wage data behind it has no percentiles, so the chart
simply doesn't appear there.

**What if you skip college? The high school graduate baseline.** This section
describes the *Straight from high school* mode. In *Going back to school* the
baseline is not this figure at all — it's the two salaries you enter yourself
(now, and in ten years without the degree), interpolated between, because
someone returning at 49 was never choosing against a teenager. Everything
below applies to the first-time path.

Every major
is compared against what a high school graduate earns, anchored to $51,688/year
— real median pay for full-time workers 25 and older who only finished high
school (based on $994/week in the second quarter of 2026, annualized). That
anchor sets the level; the next section explains why the figure actually used
in each year varies with age rather than sitting flat.
[Source: BLS Current Population Survey, series LEU0252917300](https://www.bls.gov/news.release/wkyeng.htm).
We assume this grows a modest 2%/year (a stand-in for normal raises and
cost-of-living bumps) since BLS doesn't publish a real year-by-year
trajectory for this group the way it does for individual careers.

**One thing to know about that baseline: it's an all-ages figure.** $51,688
is the median across *every* high school graduate aged 25 and up — someone
two years out of school and someone thirty years into a career, averaged
together. Earnings typically peak in the late 40s and early 50s, so that
blended median sits above what a young worker actually takes home. Meanwhile
the person this app models is roughly 18 to 32 across the whole comparison
window.""" + hs_young_wage_disclosure() + """

Using that flat figure anyway would cut both ways. Early on it is too
generous, which makes the degree look *worse* than it is. Later on it is too
stingy, since 2%/year is slower than real pay climbs in one's twenties. The two
errors run in opposite directions and partly cancel — but "partly cancel" is
not the same as "cancel", and neither error is one we have to accept.

**So the baseline follows an age curve, and that is now the default.** Rather
than one flat figure for every year, each year of the comparison uses that
age's own share of the all-ages median, from the same Census records: about
$32,000 at 18, $41,000 at 24, reaching the published all-ages figure around 36.
The BLS number still sets the *level* — only the *shape* comes from the
microdata — so refreshing one doesn't invalidate the other.

Be clear about which way this cuts. It **raises every degree's earnings
premium**, because the thing being compared against is no longer overstated in
the years when a student is enrolled and earning nothing. It can move a major
from "never worth it" to positive. That is the direction that flatters this
tool's own conclusion, so it is worth saying plainly why we do it anyway: a
median for 25-to-65-year-olds is simply the wrong number to compare an
18-year-old against, and choosing a figure we can demonstrate is wrong, in
order to look cautious, would be its own kind of dishonesty.

There is no setting for this. It briefly shipped as one, and that was the wrong
shape for it — an option implies the two answers are both defensible, and here
one of them isn't. If you want to see the comparison against the flat published
figure, the numbers are all above: the baseline would be $51,688 in every year
instead of climbing from about $32,000, which would make every degree on this
page look worse than the model actually thinks it is.

One knock-on: with the curve supplying the raises that come from getting older,
the 2%/year growth stops meaning "raises and cost-of-living together" and means
calendar drift only.

We still headline the published BLS number, because it's the one a reader can
look up and check. BLS itself only breaks earnings out by education for ages
25 and up, so there's no official under-25 figure for high school graduates;
the one quoted above comes from the underlying Census survey records rather
than a published table. What we won't do is manufacture a starting wage by running
our own 2%/year assumption backwards — that 2% describes how wages drift over
*calendar time*, not how one person's pay climbs with *age*, and the two
aren't interchangeable. So read
this comparison as "a degree versus a typical working adult without one,"
rather than "versus your classmate who skipped college." It's the more
demanding of the two tests.

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

**Which income-driven plan you can actually get depends on when you borrow.**
The IDR option above is modelled on **IBR**, and IBR is closed to loans
originated on or after **July 1, 2026** (the 2014 version covers loans from
July 1, 2014 to July 1, 2026; the original version, at 25 years, covers loans
before July 1, 2014). For borrowing from July 1, 2026 the income-driven plan
is the **Repayment Assistance Plan (RAP)**: 30 years rather than 20, **1–10%
of total income** rather than 10% of income above an allowance, a $10/month
minimum, all unpaid interest waived for the full term, and a $50/month
principal match. **The dropdown offers the plans you could actually be repaid under.** For a
start year of 2026 or later — which is every scenario the app can build, since
the start-year list begins at the current year — those are OBBBA's two, and
only those:

- **Repayment Assistance Plan (RAP)**, the default. 1–10% of total income, all
  unpaid interest waived, remainder forgiven at 30 years (and taxed). The
  payment never falls below **$10/month**, including after the $50-per-dependent
  reduction — so unlike IBR, RAP has no $0 payment. Below about $10,000 of
  income the $10 floor *is* the payment.
- **2026 Tiered Standard Plan.** A fixed payment over a term set by how much
  you owe. Forgives nothing, so nothing is taxed either.

**Switching between income-driven plans is not symmetric**, and the repayment
comparison models both directions. Payments made under any income-driven plan
count toward discharge under RAP — so the "Qualifying payments already made"
input subtracts them from the income-driven rows — but enrolling in RAP also
extends the repayment period to RAP's 30 years. In the other direction, RAP
payments count toward IBR/ICR/PAYE only in months where the RAP payment was at
least the 10-year Standard payment; for most income-driven borrowers that never
happens, which the page states in the terms of the visitor's own figures.

That return route is also time-limited: **ICR and PAYE terminate on July 1,
2028**, after which IBR is the only plan RAP credit could count toward — and
IBR is closed to loans originated on or after July 1, 2026, so a borrower whose
loans start after that date has RAP as their only income-driven option and no
plan to switch back to at all. Sources: studentaid.gov guidance on changing IDR
plans; TICAS, *Upcoming Changes to Income-Driven Repayment Plans*
(ticas.org).

**Standard 10-Year and IBR-style IDR are not offered**, because a loan
originated on or after July 1, 2026 cannot be repaid under either. They are
still modelled — tick **Compare against pre-2026 repayment plans** under
Advanced Analysis to add them back — but as a comparison against the old rules,
not as choices. A shared link naming a superseded plan is mapped to the plan
that replaced it (Standard 10-Year → Tiered Standard, IDR → RAP) rather than
being dropped to whatever happens to be first. (RAP also still appears under **Advanced
Analysis → 2026 Regulatory & Macro Forecasting**, which additionally models the
Tiered Standard Plan.) **Parent PLUS is not eligible for RAP**, which is why it
sits in the non-forgivable pool described above.

One simplification: the plan follows the **start** year, while in reality each
year's loans are judged by their own disbursement date, so a 2025 starter's
later years would fall under RAP. OBBBA's interim exception keeps such a
borrower on the old limits for up to three years, which is why the start year
is a defensible proxy rather than an arbitrary one.

**Forgiveness is taxed.** Since **January 1, 2026**, a balance discharged at
the end of an income-driven plan is **taxed as ordinary income in the year it
is discharged** — under RAP, IBR and the original IBR alike. This app does
**not** model that tax: it lands twenty or thirty years out, at a rate set by
income and by tax law neither of which is knowable now, and a made-up figure
would be worse than naming the liability. But it means a forgiveness figure is
a bill deferred, not a bill cancelled, and on a professional degree — where the
forgiven balance can exceed the amount borrowed — the tax alone can be a
six-figure event. Read every forgiveness number on this page with that in mind.
[Source: TICAS, "Comparing Income-Driven Repayment Plans", September 16,
2025](https://ticas.org/wp-content/uploads/2025/09/IDR-Plan-Chart-9.16.25.pdf).

**Cumulative Gross Pay minus loan payments.** These figures are **before
tax**. The ROI model sums each year's gross salary and subtracts the loan
payments made in that window — it never applies income tax, because tax
depends on where you live and filing status, and applying it to one side of
a comparison and not the other would distort it. The Real-World Take-Home
section above is where tax is modelled, on a single year at a time. The same
holds for the Earnings Premium and ROI% headline figures, which come from
this identical calculation.

The chart under the headline figures plots each path's position at the end
of every year, not just at year 10. That's
there because "who is ahead after ten years" and "when did they get ahead" are
different questions, and the second one is usually the one being decided. A
path that trains before it earns — medicine most of all — sits below zero for
years and then climbs steeply; an endpoint alone reports that as a single
verdict and hides the shape entirely. Every point is the same calculation as
the headline number with the window shortened to that year, so the last point
on the chart is exactly the figure above it, by construction rather than by
coincidence. With *Count foregone earnings* on, the baseline starts several
years ahead — whoever you are being compared against was earning while you
were enrolled, whether that is the high school graduate or the job you left —
and the chart says so above the plot, because that head start otherwise looks
like the degree simply being behind.

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

**What the two career stages in Real-World Take-Home mean.** That section
shows "Starting (Year 1)" and "Mid-Career (Year 10)" side by side. They are
two windows into one story, not two different stories: your loan payoff
schedule and your ROI numbers always simulate a full, real year-by-year path
from year 1, and neither stage restarts it from a different point. Year 1 is
the harder year — the loan payment takes its largest bite out of take-home
pay then — which is why the charts below the figures describe that stage.

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
rather than looked up live each time. Typical debt-at-graduation — the
median federal loan debt that graduates who borrowed leave with — is
looked up live instead, so it works for any school in College Scorecard's
database. Cost of Attendance is shown for context, but the debt figure now
does double duty: it's also the **default loan amount** (see the next
section). Each scenario has its own school
field, so Compare Mode can hold, say, "Computer Science at School A"
against "Computer Science at School B." When your school is found, Cost
of Attendance below auto-fills using in-state or out-of-state pricing,
based on whether you checked **In-State Student?**. If your school isn't
found, or you'd rather enter your own number, typing over the auto-filled
value always works — it won't get overwritten later.

**How your loan amount is actually calculated.** A **Loan estimate** toggle
in the sidebar lets you choose how the loan is built, and applies to both
scenarios in Compare Mode:

*Simplified* (the default) uses the school's **median completer debt** from
College Scorecard — the median federal loan debt that graduates of that
school who borrowed actually leave with. Because it's a real-world outcome,
you get a realistic answer just by picking a school, without entering any
cost or aid. One caveat: it counts federal loans among students who borrowed
*and* completed, so it can understate the total for someone who will also
take private or parent loans, or who won't finish — switch to Detailed to
model those.

*Detailed* builds the loan from your own **Cost of Attendance, Personal
Contribution, and Grants & Scholarships** (entered per year). Cost of
Attendance grows a little each year (an estimated inflation rate) while
Personal Contribution and Grants stay flat; each year's loan is whatever's
left after subtracting them, never below $0, summed across the program's real
length (4 years for a bachelor's, 2 for an associate's):
`Loan (Year N) = max(Cost of Attendance × (1 + inflation rate)^(N-1) − Personal Contribution − Grants & Scholarships, 0)`.
Detailed also shows the year-by-year breakdown. When a school has no reported
debt (one that reports none, the College Tier estimator, or the live lookup
being unavailable), Simplified isn't offered and Detailed is used.

Either way, whatever ends up in the **Total Loan Amount** field is what every
calculation on the page uses — you can also type any amount directly (e.g. a
real financial aid offer) to override it.

**Federal caps and gap financing (Detailed mode).** Federal Direct
(Subsidized/Unsubsidized) loans are capped: about **$5,500 / $6,500 /
$7,500 / $7,500** per year for a dependent undergraduate (higher if
independent), so roughly **$27,000 over four years** ($45,000 independent).
Need above that limit can't be met with Direct loans — it comes from **Direct
PLUS or private/alternative loans** at a higher rate, plus origination fees
(1.057% on Direct Subsidized/Unsubsidized, 4.228% on Direct PLUS — stable
since October 2020). So Detailed mode splits your loan into the capped federal
tranche (at your **Federal Direct rate**) and a **gap tranche** (at your **Gap
financing rate**), grosses each up for its fee, and repays the combined balance
at the principal-weighted **blended** rate — the breakdown is shown under the
loan metrics. The **Dependency status** toggle sets the cap; professional-school
debt (medicine, dentistry, law) is handled separately — see below. Interest
rates reset every July 1: for loans first disbursed in **2026–27** the
undergraduate Direct rate is **6.52%**, graduate/professional Direct
Unsubsidized is **8.07%**, and Direct PLUS is **9.07%**. The app defaults to
6.5% and 8.5% as round placeholders, so enter your own. Loan limits, rates, and fees: U.S. Department of Education / Federal
Student Aid ([studentaid.gov/understand-aid/types/loans/interest-rates](https://studentaid.gov/understand-aid/types/loans/interest-rates)
and [.../subsidized-unsubsidized](https://studentaid.gov/understand-aid/types/loans/subsidized-unsubsidized)).

**The gap is no longer all federally borrowable (OBBBA, July 1, 2026).** Direct
PLUS for parents used to be limited only by *cost of attendance minus other
aid* — in practice no ceiling, which is how the gap tranche above was
originally modelled. It is now capped at **$20,000 per year** and **$65,000 in
total** for one student, across both parents combined. Both halves bind, and
the aggregate is the one that decides a four-year degree: four years at the
annual limit would be $80,000, so $65,000 is the real ceiling. A dependent
undergraduate can therefore borrow about **$27,000 Direct + $65,000 Parent
PLUS = $92,000** of federal money for a four-year program; anything above that
is private borrowing, and the app now says so rather than quietly financing it
as though a federal loan existed. **Parent PLUS does not exist for an
independent student** — nobody is borrowing on their behalf — so their entire
gap above the $45,000 Direct limit is private. Undergraduate Direct limits
themselves are unchanged by OBBBA. Not modelled: the **interim exception**,
which preserves the old limits for a student already enrolled on June 30, 2026
who had already taken a Direct Loan — it can only loosen the cap, and it can't
apply to someone deciding where to enrol now.
[Source: studentaid.gov, OBBBA – Important Definitions, "PLUS loans for parents"
annual and aggregate tables](https://studentaid.gov/announcements-events/big-updates/definitions).

**Professional school: no more Grad PLUS (OBBBA, July 1, 2026).** Medical,
dental and law school debt used to be borrowed as $20,500/year unsubsidized
with everything above that on **Grad PLUS** at *cost of attendance minus other
aid* — no ceiling — which is why this app modelled it as gap financing. Direct
PLUS for graduate and professional borrowers **no longer exists**. The
unsubsidized limit rose to **$50,000/year** with a **$200,000 aggregate** for
professional study, and there is nothing federal behind it. So that debt is now
split at its own cap, at the published graduate/professional Direct rate
(8.07%), with the remainder private:

Against the national averages that gives **$5,000 private** for Medicine
($205,000 vs a $200,000 ceiling), **$93,900** for Dentistry ($293,900), and
nothing for Law ($130,000 against 3 × $50,000 = $150,000). **Name your actual
school in the sidebar and these change**, often a lot: across the schools that
publish a figure, **43% of medical and 78% of dental schools** sit above the
$200,000 ceiling, so for many the private share is larger than the national
average implies — and for some it is nothing at all.

**Graduate degrees (master's and doctoral).** BLS publishes the education a
career is normally *entered* with, and for **113 of the 825 occupations here**
that is a master's or a doctorate — Statisticians, Economists, Epidemiologists,
School Psychologists, Education Administrators and others. Those paths are
modelled as a bachelor's **plus** the graduate degree: **6 years** for a
master's, **9** for a doctorate. Everything follows from that — the tuition
charged, the years of foregone earnings, and the age the debt-free high school
graduate is compared from (24 and 27 rather than 22).

The **loan limits differ too**, and by more than the length does. Graduate
Direct Unsubsidized is **$20,500/year against a $100,000 aggregate** — one flat
annual figure, not the $5,500/$6,500/$7,500 undergraduate ladder — at the
graduate Direct rate of **8.07%**. And there is **no Parent PLUS**: it exists
only for dependent undergraduates, and Grad PLUS, which used to fill that gap,
was abolished by OBBBA. Anything above the graduate ceiling is private
borrowing.

In **Intended Major** mode there is no BLS education level to read (a major is
not an occupation), so the sidebar asks. The doctoral default of 5 years is a
placeholder — real programmes run 4 to 8 — and is editable.

**What the app cannot tell you**, and this matters for reading the premium: it
does not model what a graduate degree *adds*. The salary shown is what people
already in that occupation earn, which is a figure that already includes their
credential. So the earnings premium is the return on the whole path from high
school, not the return on the master's by itself. Separating those would need
salary data by credential *within* an occupation, which BLS does not publish.

**Where a graduate cost figure comes from, when there is one.** There is no
graduate cost of attendance in any federal dataset. What Scorecard does publish
is median **debt at graduation** by school and field, so where you name a school
and your field publishes a figure, that is offered as the loan — already net of
scholarships and assistantships, and overridable. Only about **a fifth** of
school-and-field combinations publish a master's median and **a sixteenth** a
doctoral one, so most of the time you will be entering your own cost. Same
caveats as the professional figures below: they include Grad PLUS, which no
longer exists, and they are pooled across award years.

**Where the school-specific figures come from.** College Scorecard publishes
cumulative debt at graduation for each school × field of study × credential
level. This app uses the **First Professional** level for medicine, law and
dentistry — 381 schools that publish a figure. The definition is the one this
model needs: *"cumulative loan debt only includes loans disbursed at the same
academic level as the evaluated credential level"*, so it is graduate borrowing
only and excludes the undergraduate loan charged separately above. It measures
what graduates actually **borrowed**, not what the school charges, so it is
already net of scholarships and family money — which is why Harvard's medical
school shows **$99,160** against a $205,000 national average, and why the
overall range runs from about **$48,000 to $330,000**.

Three limits worth knowing:

- **These medians include Grad PLUS**, which OBBBA abolished on July 1, 2026.
  So every figure describes borrowing that a student starting now **cannot
  replicate federally** — the app applies the new $200,000 ceiling to it and
  shows the excess as private, which is the honest translation, but the
  underlying number came from a world with a loan that no longer exists.
- **Not every school publishes one.** Small programs are privacy-suppressed —
  Yale Law is one — so they are absent from the picker and fall back to the
  national average rather than showing a wrong number.
- **The data is pooled across award years and is a few years old.** Treat it as
  a reliable signal of how schools differ *from each other*, and a rough one
  for the exact dollar figure.
[Source: College Scorecard, Most Recent Data by Field of Study, released
June 10, 2026](https://collegescorecard.ed.gov/data/).

There is **no graduate cost-of-attendance** anywhere in College Scorecard —
`COSTT4_A` and the tuition fields are undergraduate figures — which is why this
uses debt rather than cost. The undergraduate Cost of Attendance in the sidebar
prices the bachelor's degree only.

The aggregate covers graduate and professional study only — undergraduate
borrowing does not count against it, though the pre-OBBBA $138,500 limit it
replaced did. Graduate (non-professional) study has its own lower limits
($20,500/year, $100,000 aggregate); no path in this app carries graduate debt
that isn't professional, so those don't apply to anything shown here. There is
also a **$257,500 lifetime maximum** on a student's own borrowing, which the
caps above already keep every path in this app well below.
[Source: studentaid.gov, OBBBA – Important Definitions, "Professional students"
tables](https://studentaid.gov/announcements-events/big-updates/definitions).

*Worth knowing:* which programs count as "professional" is partly unsettled.
The Department of Education initially limited it to **11 fields**; on **June 24,
2026** a court stayed part of that definition and temporarily expanded the list
to include nursing (M.S.N./D.N.P.), physical and occupational therapy, athletic
training, the psychology doctorates and others. The Department is appealing,
final briefing is due **December 4**, and if the stay is lifted the list reverts
to the original 11. **Medicine, dentistry and law appear on both lists**, so the
figures above hold either way — but a student in one of the temporarily-added
programs could see their limit fall back to the graduate $20,500/year, possibly
mid-year. NASFAA's guidance is to have a backup plan before borrowing the full
$50,000 on the strength of the stay.
[Source: NASFAA, "Temporary Changes to Professional Student Loan Limits", updated
July 30, 2026](https://www.nasfaa.org/uploads/documents/OB3_Temp_Changes_Prof_Degree.pdf).

*One more ceiling this app can't see:* $50,000/year is the federal maximum, not
an entitlement — **schools may set their own lower limits** for a program, and
have the authority to do so. The model assumes the full amount is available, so
where a school caps it lower the private shortfall is larger than shown.

*A simplification worth knowing:* both non-federal tranches are priced at your
single **Gap financing rate**. Real private loans are credit-priced and
generally cost more than Direct PLUS, so the private portion above is if
anything *understated* — the app has one non-federal rate input and inventing
a spread would be a made-up number. The origination fee is applied to the PLUS
portion only, since private lenders generally charge none.

**Only federal Direct loans are forgiven.** Under **Income-Driven Repayment**
or **RAP**, whatever is left at the end of the term is written off — but that
applies to the student's own Direct loans only. **Parent PLUS is the parent's
loan and is not IDR-eligible; private loans are outside the federal system
entirely.** So the app amortises the two pools separately: the federal part on
the income-driven plan with forgiveness at the end, the PLUS-and-private part
on an ordinary fixed schedule that runs to completion. The payment you see is
the sum of both, and the payoff date is when the *later* one clears.

This matters more than it sounds. The model used to repay one blended balance,
which forgave private money along with federal — and because income-driven
payments can sit below the interest, the balance grew and the imaginary
write-off grew with it. On the Berkeley example above that was a **$464,000**
forgiveness that could never happen. Under OBBBA the non-forgivable share is
larger than it used to be, so this would only have got worse.

Simplified mode's median debt is **federal loans only** and excludes
PLUS/private entirely, so no split applies there.

**Getting your own numbers instead of school averages.** The Cost of
Attendance we auto-fill is a school-wide *average sticker price* — what a
typical student is charged before aid. Two free, official tools give you
your own figures, and the **🎯 Get Your Real Numbers** section near the top
links to both. Your school's **Net Price Calculator** (each U.S. college is
federally required to host one; find yours through the Department of
Education's directory at
[collegecost.ed.gov/net-price](https://collegecost.ed.gov/net-price)) returns
your *net price* — cost after grants and scholarships — which you enter as
Cost of Attendance while setting Grants & Scholarships to $0. The federal
[Student Aid Estimator](https://studentaid.gov/aid-estimator/) estimates your
Student Aid Index (SAI), which you enter as Personal Contribution. These
compose correctly: net price removes grants but not the family contribution,
so subtracting the SAI on top of it is not double-counting.

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

When the profession you picked is entered with an **associate's degree**, there
is nothing to transfer to — a community college awards that degree itself — so
the selector offers **"the entire degree, no transfer"** in place of the 2+2
option. Choosing it puts the whole program at community-college prices, which
is what most people in these fields actually do, and typically brings the loan
to **$0**. The comparison is still against the same high-school-graduate
baseline, so this isn't a way to make a career look good by spending less: the
earnings side is untouched.

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
(just above the Federal Direct rate) is pre-filled for you — with the
school's reported median debt in **Simplified** mode, or the cost-based
per-year total in **Detailed** mode — but you can type over it with any
other number, for example the real total from an actual financial aid offer
letter. Once you do, every calculation on this page uses your typed number
instead (in Detailed, the per-year table still shows the calculated
breakdown, for reference). Your override sticks across reruns, but refreshes
back to the pre-filled default the next time you change something that moves
it — switching schools or the Loan estimate mode, or (in Detailed) editing
Cost of Attendance, Personal Contribution, or Grants & Scholarships — the
same way the Cost of Attendance field itself auto-fills from a school lookup
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

**The two questions at the top of the page.** If you answer them, we save
your answers straight away — before you submit anything else — because the
whole point is to record what you thought *before* you saw the numbers. If
you skip them, we record that you skipped, which is a different fact from
never having been asked. Either way the calculator behaves identically; the
questions are optional and they never gate a single figure on this page.

Those questions are research, so they're for people 18 and over. If you tell
us you're a student under 18, we don't ask them and we don't show the survey
at the bottom either. Everything else still works.

**What we save when you submit the survey.** Each anonymous response
saves who's answering (Student/Parent/Counselor/Teacher/Other), an expected
high school graduation year, the two questions from the top asked a second
time — so a change can be measured rather than remembered — plus your exact
inputs and results at
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

**What gets recorded even if you never answer anything.** Opening the page
records that a page was opened, and changing your major or school records the
new selection. That happens as you browse, not when you submit — so it is
recorded whether or not you touch either set of questions. It carries no name,
no email, no IP address and no account. Each visit gets a random ID that is
thrown away when you close the tab, so two visits can't be linked to each
other or to you.

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
  dataset by closest starting salary — never invented. In **Career mode** each
  occupation has its own SOC group directly. In **Major mode**, a major isn't an
  occupation, so each major is mapped to the occupation group it most commonly
  leads to (e.g. Accounting → Business & Financial Operations, Mechanical
  Engineering → Architecture & Engineering) and the exposure shown is for that
  group — a representative approximation, clearly labeled as such, since a major
  spreads across many jobs. A few majors that span the whole labor market
  (Interdisciplinary Studies, Liberal Arts) are left unmapped and show no level.
- **2026 Federal Repayment Plans (RAP & Tiered Standard).** Compares two *real, enacted* federal
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
- **Count foregone earnings during enrollment.** By default this calculator
  starts its earnings clock at *graduation*: it compares a graduate's first
  N years of post-degree salary against a high-school graduate's same N
  years, and captures only the *tuition/debt* cost of the degree. But the
  largest real cost of a bachelor's degree is usually not tuition — it's the
  roughly four years of wages given up while enrolled full-time, during which
  the debt-free high-school graduate is already working, earning raises, and
  banking that income. Turning this option on adds those foregone years to the
  high-school baseline, so every path is compared on one consistent timeline
  that starts at **age 18** rather than at graduation. The number of years is
  the program's real length, not a flat four: an occupation BLS says is
  entered with an associate's degree is charged two years of cost and two
  years of foregone wages, because that is how long it takes. Concretely: the high-school graduate is credited with ~4 extra
  years of earnings at the front, the degree-seeker earns nothing during
  enrollment, and a career that needs no degree is never charged for time it
  didn't spend in school. This lowers each degree's earnings premium and
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

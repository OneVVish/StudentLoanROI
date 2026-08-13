#!/usr/bin/env python3
"""Ranked article ideas for the guides, from Reddit and from our own visitors.

    python3 analyze_reddit.py --check-auth      # verify credentials, one cheap call
    python3 analyze_reddit.py                   # digest from cache, or fetch if stale
    python3 analyze_reddit.py --refresh         # refetch, then rank
    python3 analyze_reddit.py --cache-only      # no network at all; reproduce a run
    python3 analyze_reddit.py --self-test       # synthetic fixture, no network, no keys
    python3 analyze_reddit.py --explain 3       # the full arithmetic for one cluster
    python3 analyze_reddit.py -o ideas.csv      # one row per cluster

Local-only, like analyze_survey.py, analyze_traffic.py and analyze_model.py --
never imported by app.py. Needs .streamlit/secrets.toml for the Apify token and
(for the first-party half) the Supabase connection.

WHAT THIS IS FOR. content/README.md says reads and likes are "warm signals for
what to write next", and nothing in this repo collected them for that purpose.
The one recorded precedent for choosing a topic is RETURNING_STUDENT_PLAN.md: a
real news article exposed a gap between the model and a real population, and
that became the piece. This generalises that -- harvest the questions people
are actually asking, cluster them, and rank by how well THIS APP can answer
them with a real number.

WHY ANSWERABILITY OUTWEIGHS ENGAGEMENT. Reddit's loudest student-loan content
is servicer horror stories, forgiveness politics and admissions drama. None of
it is a question app.py can answer, and all of it would out-score everything
else on engagement alone. So `answerable` carries the largest weight, and the
topics SCOPE.md says this app cannot answer are a PARTITION rather than a
penalty: they are printed with their engagement, in their own section, and can
never enter the ranked list however loud they get.

NO LLM RUNS HERE, and there is no `anthropic` dependency. Harvesting and
drafting are separate acts on purpose. This script ranks questions; a later
session reads analysis_output/reddit_idea_digest.md and writes the article from
this repo's datasets. That separation is what keeps the editorial rule
enforceable -- every number in a guide comes from a dataset, never from a post.

WHAT THIS SCRIPT DOES NOT DO. It does not write to content/, does not touch
data/, does not fetch comment trees, does not record post authors (CACHE_FIELDS
has no `author` key, deliberately -- see the comment there), does not post,
vote, or message anyone, and does not decide what to publish. It reads public
listings and ranks QUESTIONS.

ON THE SOURCE. Reddit's June 2026 Responsible Builder Policy requires explicit
approval before any Data API access, and unauthenticated requests are refused,
so the default path is an Apify actor. That is scraping rather than the
sanctioned API: Reddit's user agreement restricts automated collection, and
Apify leaves that judgment to the customer. What narrows it here is what we do
with the result -- the digest is local and gitignored, we extract THAT a
question is being asked rather than anyone's words, nothing is republished.
`--source api` is the sanctioned path for when approval lands; both sit behind
one adapter returning identical rows, so switching is a flag.

READ EACH SUBREDDIT'S RULES BEFORE THE FIRST REAL RUN. Several have rules about
automated collection and about research use. That is a human step and it is not
in this code. SUBREDDITS is an editable constant so dropping one costs nothing.
"""
import argparse
import json
import math
import re
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import certifi
import requests

SECRETS_PATH = Path(__file__).parent / ".streamlit" / "secrets.toml"
OUTPUT_DIR = Path(__file__).parent / "analysis_output"

# The digest is written as .md into analysis_output/, which is gitignored
# wholesale -- so it is private by construction with no new ignore rule. It can
# never be mistaken for a publishable post either: check_content.py and
# build_site.py both glob content/posts/*.md only, so nothing here is reachable
# by the build.
DIGEST_PATH = OUTPUT_DIR / "reddit_idea_digest.md"
CACHE_PATH = OUTPUT_DIR / "reddit_posts.jsonl"
META_PATH = OUTPUT_DIR / "reddit_posts.meta.json"

# Where the questions come from. Editable on purpose: if a subreddit's rules
# turn out to forbid automated collection, deleting the line is the whole fix.
SUBREDDITS = ("StudentLoans", "ApplyingToCollege", "financialaid", "college")

# top-of-year for perennial demand, top-of-month for what is live now. /new is
# deliberately absent: it is dominated by threads with no engagement yet, and
# every engagement term in the model would read those as zero demand rather
# than as too-early-to-tell.
WINDOWS = ("year", "month")

CACHE_MAX_AGE_DAYS = 7

# The Apify actor. Cheap, and swappable -- actors break when Reddit changes its
# defenses, which is why the field mapping below is a constant rather than
# inline attribute access.
APIFY_ACTOR = "practicaltools~apify-reddit-api"
APIFY_RUN_URL = "https://api.apify.com/v2/actors/{actor}/run-sync-get-dataset-items"
# A synchronous Apify run is cut off at 300 s with a 408, so the HTTP timeout
# sits just past it: we want the actor's own error, not a client-side one.
APIFY_TIMEOUT_S = 310

# Reddit's own API, for when Responsible Builder approval lands. The format is
# <platform>:<appid>:<version> (by /u/<username>) -- a generic or browser-style
# UA is the documented cause of aggressive rate limiting.
REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_API_BASE = "https://oauth.reddit.com"
REDDIT_UA_TEMPLATE = "python:worthmydegree-guide-ideas:v1.0 (by /u/{username})"

# Every field that reaches disk, built key by key -- never **item, because an
# actor's schema is not ours to trust and a spread would import whatever it
# happens to return.
#
# `author` IS DELIBERATELY ABSENT, and that absence is the strongest privacy
# enforcement in this tool: it cannot leak a username into a digest because it
# never writes one to disk. Comment trees are never fetched either, only the
# comment COUNT -- which cuts the request cost by an order of magnitude and
# keeps the richest seam of personal narrative out of the cache entirely.
# If you are about to add `author`, delete this paragraph first and say why.
CACHE_FIELDS = (
    "id", "subreddit", "title", "selftext_excerpt", "score", "num_comments",
    "created_utc", "permalink", "flair", "fetched_at", "source",
)

# Actors disagree about field names, so the mapping is data. Each entry is a
# tuple of candidate keys tried in order. Verified against
# practicaltools~apify-reddit-api on 2026-08-13 with --check-auth, which prints
# the raw keys the actor really returned.
#
# `username` IS IN THE ACTOR'S PAYLOAD and is deliberately not mapped, so it
# never reaches disk. That is the whitelist doing its job against a real
# response rather than against a design document.
ACTOR_FIELD_MAP = {
    "id": ("id", "postId", "parsedId"),
    "subreddit": ("parsedCommunityName", "communityName", "subreddit"),
    "title": ("title",),
    "selftext_excerpt": ("body", "selftext", "text"),
    "score": ("upVotes", "score", "ups"),
    "num_comments": ("numberOfComments", "num_comments", "commentCount"),
    "created_utc": ("createdAt", "created_utc", "created"),
    "permalink": ("url", "link", "permalink"),
    "flair": ("flair", "link_flair_text", "linkFlairText"),
}

# Fields no actor is required to supply. flair is absent from this one's
# payload entirely, and a missing flair is not a broken harvest -- nothing in
# the model reads it. Everything NOT listed here must map, or --check-auth
# fails: an unmapped score or date is a silently empty column.
OPTIONAL_CACHE_FIELDS = frozenset({"flair", "selftext_excerpt"})

# THE START URL MUST BE THE BARE SUBREDDIT. Adding the listing path you
# actually want -- /top/, or /top/?t=month -- does not narrow the result, it
# makes the actor IGNORE the URL and return posts from unrelated subreddits.
# Measured 2026-08-13: `/r/StudentLoans/top/` came back 201 with three posts
# from r/Fauxmoi, r/Millennials and r/meirl. Sorting belongs in `sort`/`time`,
# which do work (top/month returned 733-367 upvotes from the last four weeks;
# new/all returned same-day posts scoring 0-14).
#
# A wrong-subreddit response is the worst failure this tool has, because it
# succeeds: real posts, real engagement, plausible clusters, about the wrong
# communities entirely. harvest_apify therefore checks what came back against
# what it asked for and drops the mismatches loudly.
SUBREDDIT_URL = "https://www.reddit.com/r/{subreddit}/"

SELFTEXT_EXCERPT_CHARS = 500


# --- what this app can and cannot answer -----------------------------------
#
# TOPIC_VOCAB does two jobs with one dict: it is the clustering key AND the
# source of the `answerable` score. That is deliberate -- keeping them apart
# would mean maintaining two lists of the same topics, and the second would
# quietly stop matching the first.
#
# `backing` is not decoration. It lands VERBATIM in the digest, so the later
# drafting session starts from a named dataset rather than from memory -- the
# repo's editorial rule ("every number comes from a dataset") encoded as data.
# An entry claiming answerable 1.0 with an empty backing is a startup error.
#
# EXPECT TO EDIT THIS AFTER EVERY RUN. Anything the vocabulary does not know is
# capped at UNCHECKED_ANSWERABLE, so the ranking structurally over-rewards what
# we already thought of. The UNMATCHED section and the printed coverage
# percentage are the two things that make that bias visible.
ANSWERABLE_BACKED = 1.00      # a named dataset answers it
ANSWERABLE_ADJACENT = 0.60    # we have a figure that reframes it, not one that answers it
UNCHECKED_ANSWERABLE = 0.25   # emergent: nobody has checked whether we can source it
ANSWERABLE_NONE = 0.00        # SCOPE.md says we cannot answer this

TOPIC_VOCAB = {
    "parent-plus-caps": {
        "label": "Parent PLUS borrowing caps",
        "phrases": ("parent plus", "parentplus", "plus loan", "plus denied",
                    "plus denial", "aggregate cap", "65k", "20k a year"),
        "backing": "PARENT_PLUS_ANNUAL_LIMIT $20,000/yr + "
                   "PARENT_PLUS_AGGREGATE_LIMIT $65,000 per student across both "
                   "parents (app.py, 2026 rules); FEDERAL_DIRECT_ANNUAL_LIMITS "
                   "for the senior-year shortfall",
        "strong": ("parent plus", "parentplus", "plus denied", "plus denial"),
        "answerable": ANSWERABLE_BACKED,
    },
    "direct-loan-ladder": {
        "label": "What a student can borrow in their own name",
        "phrases": ("direct loan", "subsidized", "unsubsidized", "5500",
                    "7500", "borrowing limit", "federal limit"),
        "backing": "FEDERAL_DIRECT_ANNUAL_LIMITS (by year in school) and "
                   "FEDERAL_DIRECT_AGGREGATE_CAP dependent $31,000 / "
                   "independent $57,500 (app.py)",
        "strong": ("unsubsidized", "subsidized", "direct loan"),
        "answerable": ANSWERABLE_BACKED,
    },
    "how-much-debt-is-too-much": {
        "label": "How much debt is too much for this path",
        "phrases": ("worth it", "too much debt", "how much debt", "break even",
                    "is it worth", "worth the debt", "affordable"),
        "backing": "find_breakeven_loan -- the loan at which a path stops "
                   "beating a debt-free high school graduate. SCOPE.md ranks "
                   "this the app's #1 answer: a threshold you can hold against "
                   "a real aid offer",
        "strong": ("break even",),
        "answerable": ANSWERABLE_BACKED,
    },
    "major-vs-career": {
        "label": "The major you pick vs the job you are assuming",
        "phrases": ("what can i do with", "job prospects", "actually get a job",
                    "employable", "useless degree", "does the major matter"),
        "backing": "Major mode (73 NY Fed majors, includes graduates who never "
                   "entered the field) vs Career mode (836 BLS OEWS "
                   "occupations). SCOPE.md #2: CS the major reads $103,034 "
                   "against Software Developers at $336,192, a $233,158 gap "
                   "that IS the optimism bias",
        "strong": ("useless degree",),
        "answerable": ANSWERABLE_BACKED,
    },
    "is-college-worth-it-at-all": {
        "label": "Is any degree better than not going",
        "phrases": ("skip college", "not going to college", "worth going",
                    "trade school", "trades instead", "no degree", "drop out"),
        "backing": "Net position vs a debt-free high school graduate "
                   "(HS_GRAD_SALARY $51,688 + data/hs_age_profile.csv age "
                   "curve). SCOPE.md #3: wrong for 1 in 6 (16.7%) of "
                   "bachelor's-level occupations at ANY debt level",
        "strong": ("trade school", "skip college"),
        "answerable": ANSWERABLE_BACKED,
    },
    "repayment-plan-choice": {
        "label": "Which repayment plan, and what it changes",
        "phrases": ("rap", "repayment assistance", "idr", "income driven",
                    "income-driven", "ibr", "save plan", "standard plan",
                    "which repayment"),
        "backing": "simulate_rap_schedule / calculate_idr_repayment / "
                   "calculate_standard_repayment, verified against "
                   "studentaid.gov's published table by "
                   "check_rap_payment_table.py. SCOPE.md #9: 166 of 203 majors "
                   "stay ahead past $1M of debt under income-driven repayment",
        "strong": ("income driven", "income-driven", "repayment assistance", "ibr", "idr", "save plan"),
        "answerable": ANSWERABLE_BACKED,
    },
    "taxed-forgiveness": {
        "label": "The tax bill on forgiveness",
        "phrases": ("taxed on forgiveness", "tax bomb", "forgiven balance",
                    "25 years then", "30 years then", "discharge tax"),
        "backing": "discharge_tax_estimate -- tax(income + forgiven) minus "
                   "tax(income) at the projected discharge year, on real "
                   "federal brackets. Federal only, so it is a floor",
        "strong": ("tax bomb", "taxed on forgiveness", "discharge tax"),
        "answerable": ANSWERABLE_BACKED,
    },
    "cost-of-attendance-gap": {
        "label": "The gap between the sticker price and what you can borrow",
        # NOT a bare "gap": it matched "gap year", filing an emergent topic
        # under a vocabulary entry it has nothing to do with. A phrase this
        # generic needs its neighbours to mean anything.
        "phrases": ("cost of attendance", "coa", "sticker price", "net price",
                    "cant afford", "can't afford", "funding gap", "aid gap",
                    "cover the gap", "how do people pay", "how does anyone pay"),
        "backing": "data/college_coa_clean.csv -- 5,035 schools, in-state and "
                   "out-of-state, priced per row against the student's home "
                   "state by search_schools_by_budget",
        "strong": ("cost of attendance", "sticker price", "net price"),
        "answerable": ANSWERABLE_BACKED,
    },
    "high-cost-city": {
        "label": "Whether the higher salary survives the higher rent",
        "phrases": ("cost of living", "nyc salary", "bay area", "move to",
                    "worth moving", "expensive city", "col adjusted"),
        "backing": "CITY_DATA cost-of-living indices with per-metro BLS OEWS "
                   "wages (data/metro_careers_clean.csv). SCOPE.md #5: the two "
                   "are independently sourced, so it genuinely goes both ways",
        "strong": ("cost of living",),
        "answerable": ANSWERABLE_BACKED,
    },
    "take-home-pay": {
        "label": "What actually lands in the bank after tax and the payment",
        "phrases": ("take home", "after taxes", "net pay", "monthly payment",
                    "afford the payment", "paycheck"),
        "backing": "calculate_take_home on real federal and state brackets, "
                   "minus the modelled monthly payment -- the salary-flow bar "
                   "from gross down to spendable",
        "strong": ("take home",),
        "answerable": ANSWERABLE_BACKED,
    },
    "underemployment": {
        "label": "Graduates who never work in their field",
        "phrases": ("underemployed", "cant find a job in", "unrelated field",
                    "not using my degree", "barista"),
        "backing": "NY Fed underemployment: UNDEREMPLOYMENT_OVERALL_PCT 39.35%, "
                   "from 12.8% (Nursing) to 65.8% (Criminal Justice) across "
                   "UNDEREMPLOYMENT_MAJOR_COUNT 73 majors",
        "strong": ("underemployed",),
        "answerable": ANSWERABLE_BACKED,
    },
    "grad-school-debt": {
        "label": "What a master's or doctorate costs and returns",
        "phrases": ("grad school", "masters", "master's", "phd", "doctorate",
                    "worth a masters", "graduate school"),
        "backing": "data/graduate_tuition_clean.csv (IPEDS, tuition and fees "
                   "only -- IPEDS publishes NO graduate living costs) beside "
                   "data/graduate_debt_clean.csv borrowing medians; "
                   "GRADUATE_ADDITIONAL_YEARS for the length",
        "strong": ("grad school", "graduate school"),
        "answerable": ANSWERABLE_BACKED,
    },
    "professional-school-debt": {
        "label": "Medical, dental, law and MBA debt",
        "phrases": ("med school", "medical school", "dental school",
                    "law school", "mba", "vet school", "pharmacy school"),
        "backing": "data/professional_tuition_clean.csv per-programme prices "
                   "and data/graduate_debt_clean.csv per-school debt (medicine "
                   "runs $47,503-$330,479); ADVANCED_TRAINING_OVERLAY for "
                   "unpaid years and RESIDENT_STIPEND $65,000",
        "strong": ("med school", "medical school", "dental school", "law school", "mba", "vet school", "pharmacy school"),
        "answerable": ANSWERABLE_BACKED,
    },
    "community-college-transfer": {
        "label": "Starting at community college",
        "phrases": ("community college", "cc first", "2+2", "transfer",
                    "associates", "associate's", "juco"),
        "backing": "The cc_mode paths (2+2, part-time, full associate's, and "
                   "the community-college baccalaureate) priced against the "
                   "same school COA, with PROGRAM_YEARS_BY_EDUCATION lengths",
        "strong": ("community college",),
        "answerable": ANSWERABLE_BACKED,
    },
    # Added after the first real harvest: the emergent pass surfaced
    # "#num interest · interest rate · interest" as its own cluster with no
    # vocabulary entry behind it, so it was capped at UNCHECKED_ANSWERABLE
    # despite the app modelling this in detail. That is the feedback loop the
    # UNMATCHED section exists for, working as intended.
    "interest-rates": {
        "label": "What the interest actually costs, and when it capitalises",
        "phrases": ("interest rate", "interest rates", "accrued interest",
                    "unpaid interest", "interest accrues", "capitalized",
                    "capitalised", "capitalization", "origination fee",
                    "compound interest", "interest"),
        "strong": ("interest rate", "accrued interest", "capitalization",
                   "origination fee", "unpaid interest"),
        "backing": "DEFAULT_FEDERAL_RATE 6.5% and DEFAULT_GAP_RATE 8.5% as the "
                   "two modelled tranches, PROFESSIONAL_DIRECT_RATE 8.07% "
                   "(studentaid.gov), and ORIGINATION_FEE 1.057% federal / "
                   "4.228% PLUS applied as a principal gross-up. "
                   "split_loan_financing splits the loan across those rates and "
                   "every simulator reports total_interest; RAP waives the "
                   "interest a payment does not cover",
        "answerable": ANSWERABLE_BACKED,
    },
    # Also emergent on the first harvest ("full ride · ride · scholarship").
    #
    # ADJACENT, NOT BACKED, and the distinction is the point. The app can price
    # what an award DOES -- grants reduce the cost, personal contribution comes
    # out of the ROI denominator without being borrowed. It cannot tell anyone
    # where to find a scholarship or whether they will win one, and there is no
    # scholarship dataset in this repo. An article that promised the second
    # would be sourced from memory, which the editorial rule forbids.
    "scholarships-and-aid": {
        "label": "What an award is actually worth against the price",
        "phrases": ("scholarship", "scholarships", "full ride", "full-ride",
                    "merit aid", "merit scholarship", "pell", "pell grant",
                    "grant", "grants", "aid package", "financial aid package",
                    "need based aid", "need-based aid"),
        "strong": ("full ride", "full-ride", "merit aid", "pell grant",
                   "scholarship", "aid package"),
        "backing": "grants_per_year reduces the modelled cost directly, and "
                   "personal_contribution enters the ROI denominator WITHOUT "
                   "being borrowed (see get_effective_principal -- you pay no "
                   "interest on money you never borrowed). "
                   "data/college_coa_clean.csv gives the price the award is "
                   "measured against, and every school row carries its federal "
                   "net-price calculator link. NOT sourceable: where to find an "
                   "award, or the odds of winning one",
        "answerable": ANSWERABLE_ADJACENT,
    },
    # Third pair added from the leads section: "paid off · off #num · paid" and
    # "still owe · over #num · owe #num" were both emergent clusters with real
    # engagement and no entry behind them.
    "paying-it-off-early": {
        "label": "Paying more than the minimum, and what it buys",
        "phrases": ("paid off", "paid it off", "paid in full", "debt free",
                    "debt-free", "final payment", "last payment", "payoff",
                    "loan free", "snowball", "avalanche", "extra payment",
                    "extra payments", "pay it off early", "pay extra",
                    "lump sum"),
        "strong": ("paid in full", "debt free", "final payment", "avalanche",
                   "snowball", "extra payment", "lump sum"),
        "backing": "calculate_standard_repayment's monthly_payment_override "
                   "models a borrower paying MORE than required, and "
                   "simulate_fixed_avalanche targets the surplus at the "
                   "highest-rate note while retired notes roll their payment "
                   "forward. pivot_strategy_analysis prices the whole fork: "
                   "ride the minimum to a taxed discharge, or redirect the "
                   "freed payment at the balance. existing_extra_monthly (?rx=) "
                   "is the input. NOTE the modelled limit: extra payments on "
                   "the income-driven side change RAP's waiver and the "
                   "forgiveness clocks and are deliberately NOT modelled there",
        "answerable": ANSWERABLE_BACKED,
    },
    "balance-going-up": {
        "label": "Owing more than you borrowed",
        "phrases": ("negative amortization", "negative amortisation",
                    "balance went up", "balance grew", "balance keeps growing",
                    "owe more than", "owe more now", "still owe",
                    "never going down", "growing balance",
                    "interest outpaces"),
        "strong": ("negative amortization", "negative amortisation",
                   "balance went up", "balance grew", "owe more than",
                   "balance keeps growing"),
        "backing": "balance_split_is_informative and the stacked balance chart "
                   "exist for exactly this case: on $190,000 at 6.5% for a "
                   "$38,000 earner, principal sits at $190,000 for NINETEEN "
                   "YEARS while unpaid interest grows to $366,046. "
                   "calculate_idr_repayment carries the two-pool accounting "
                   "(starting_interest splits the balance), and simulate_rap_"
                   "schedule waives the interest a payment does not cover, "
                   "which is why RAP is the one plan where this cannot happen",
        "answerable": ANSWERABLE_BACKED,
    },
    "roi-horizon": {
        "label": "Whether the answer depends on how far out you look",
        # NOT bare "10 years" / "30 years". Those fired on six threads and not
        # one of them was a title match: a body saying "I've been paying for 10
        # years" is not someone asking whether the horizon changes the answer.
        "phrases": ("in the long run", "pay off eventually", "lifetime earnings",
                    "over 30 years", "worth it long term", "long term payoff",
                    "by the time i retire"),
        "backing": "The ROI Horizon control (10/15/20/30). SCOPE.md #4: "
                   "medicine flips from -$146,293 at 10 years to +$3,466,829 "
                   "at 30",
        "strong": ("lifetime earnings",),
        "answerable": ANSWERABLE_BACKED,
    },
}

# Topics this app cannot answer, each carrying the SCOPE.md line that rules it
# out. These are a PARTITION, not a penalty: matched clusters are printed with
# their engagement in their own section and can never enter the ranked list.
#
# Without this, the ranking would be a list of Reddit's loudest student-loan
# content -- servicer horror stories and forgiveness politics -- none of which
# this app can put a number on.
CANNOT_ANSWER = {
    "servicer-complaints": {
        "label": "Servicer conduct, lost paperwork, hold times",
        "phrases": ("mohela", "nelnet", "aidvantage", "servicer", "on hold",
                    "customer service", "lost my paperwork"),
        "why": "SCOPE.md: the app models repayment arithmetic, not servicer "
               "conduct. Nothing here can put a number on it.",
    },
    "pslf-paperwork": {
        "label": "PSLF eligibility and paperwork",
        "phrases": ("pslf", "public service loan", "qualifying employer",
                    "employment certification", "buyback"),
        "why": "The app models PSLF's 120-payment clock but holds no data on "
               "employer eligibility or processing, which is what these "
               "threads are actually about.",
    },
    "loan-politics": {
        "label": "Policy, courts and what might change",
        "phrases": ("supreme court", "congress", "biden", "trump", "election",
                    "will they cancel", "executive order", "lawsuit"),
        "why": "SCOPE.md: the app models the rules as they stand. Forecasting "
               "which survive litigation is not a dataset question.",
    },
    "refinance-shopping": {
        "label": "Which private lender to refinance with",
        "phrases": ("refinance", "refi", "sofi", "earnest", "lender",
                    "best rate", "credit score"),
        "why": "No lender dataset exists in this repo. Any figure would come "
               "from memory, which the editorial rule forbids.",
    },
    "admissions-chances": {
        "label": "Chance-me, essays and where to apply",
        "phrases": ("chance me", "chances", "essay", "waitlist", "deferred",
                    "reach school", "safety school", "ec's", "sat score"),
        "why": "SCOPE.md: the app compares schools on COST. It holds no "
               "admissions data beyond an institution-level admit rate.",
    },
    "school-vs-school-outcomes": {
        "label": "Is school A's degree worth more than school B's",
        "phrases": ("better school", "prestige", "ivy", "name brand",
                    "does it matter where"),
        "why": "SCOPE.md names this a REAL GAP: modelled salary does not vary "
               "by institution except through a thin prestige multiplier, so "
               "the app answers on cost alone and must not imply otherwise.",
    },
    "personal-prediction": {
        "label": "Will I personally earn this",
        "phrases": ("will i make", "can i expect to earn", "realistic salary "
                    "for me", "am i going to"),
        "why": "SCOPE.md: every figure is a population median or percentile. "
               "Optimism bias is what the tool exists to correct; it cannot "
               "tell one person whether they beat the median.",
    },
    "should-i-do-this": {
        "label": "Should I follow my passion",
        "phrases": ("follow my passion", "should i major in", "love the field",
                    "money isnt everything", "hate my major"),
        "why": "SCOPE.md: the tool can price the trade and explicitly must not "
               "imply it can judge whether the trade is worth making.",
    },
}


# --- credentials -----------------------------------------------------------
#
# Absent is fine, present-but-broken is fatal -- analyze_traffic.py's
# load_email_config rule. Absent has to be survivable because --self-test and
# --cache-only must run on a machine that has never held a credential.

def _read_secrets() -> dict:
    if not SECRETS_PATH.exists():
        return {}
    try:
        with open(SECRETS_PATH, "rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        sys.exit(f"{SECRETS_PATH} could not be read: {error}")


def load_apify_config():
    """The [apify] block, or None when it is absent."""
    block = _read_secrets().get("apify")
    if not block:
        return None
    if not block.get("token"):
        sys.exit("[apify] in .streamlit/secrets.toml has no `token` -- either "
                 "fill it in or remove the block. A half-filled credential is "
                 "worse than none: it fails at the first request, after the "
                 "run has already started.")
    return {"token": block["token"], "actor": block.get("actor", APIFY_ACTOR)}


def load_reddit_config():
    """The [reddit] block, or None. Only needed for --source api."""
    block = _read_secrets().get("reddit")
    if not block:
        return None
    missing = [k for k in ("client_id", "client_secret", "username")
               if not block.get(k)]
    if missing:
        sys.exit(f"[reddit] in .streamlit/secrets.toml is missing "
                 f"{', '.join(missing)} -- fill them in or remove the block.")
    return dict(block)


# --- fetching --------------------------------------------------------------

def _first_present(item: dict, keys: tuple):
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return None


def _as_epoch(value) -> float:
    """Actors return a creation time as epoch seconds OR an ISO string."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(text).timestamp()
    except (ValueError, TypeError):
        return 0.0


def apify_run_input(subreddit: str, window: str, max_items: int) -> dict:
    """The actor's input for one (subreddit, window) slice.

    The skip flags are not tuning. `skipComments`/`fetchPostComments` keep
    comment TREES out of the response entirely -- cheaper, and it keeps the
    richest seam of personal narrative out of the cache, which is the same
    decision CACHE_FIELDS makes about authors. `skipUserPosts` and
    `skipCommunity` stop the dataset carrying non-post rows that would then
    have to be filtered downstream by something that might forget.
    """
    return {
        "startUrls": [{"url": SUBREDDIT_URL.format(subreddit=subreddit)}],
        "sort": "top",
        "time": window,
        "maxItems": max_items,
        "skipComments": True,
        "fetchPostComments": False,
        "searchComments": False,
        "skipUserPosts": True,
        "skipCommunity": True,
        "includeNSFW": False,
    }


def normalize_apify_item(item: dict, source: str, fetched_at: str):
    """One actor item -> one cache row, or None if it is not a usable post.

    Every value is pulled through ACTOR_FIELD_MAP rather than read directly, so
    a new actor is a constant edit rather than a rewrite of this function.
    """
    # The dataset can carry comments, communities and users alongside posts,
    # and an ad is not a question anybody asked.
    if item.get("dataType") not in (None, "post"):
        return None
    if item.get("isAd"):
        return None
    row = {"fetched_at": fetched_at, "source": source}
    for field, candidates in ACTOR_FIELD_MAP.items():
        row[field] = _first_present(item, candidates)
    if not row.get("id") or not row.get("title"):
        return None
    row["created_utc"] = _as_epoch(row.get("created_utc"))
    row["score"] = int(row.get("score") or 0)
    row["num_comments"] = int(row.get("num_comments") or 0)
    row["subreddit"] = str(row.get("subreddit") or "").lstrip("r/")
    body = " ".join(str(row.get("selftext_excerpt") or "").split())
    row["selftext_excerpt"] = body[:SELFTEXT_EXCERPT_CHARS]
    # Built in CACHE_FIELDS order, and ONLY CACHE_FIELDS -- anything the actor
    # returned that is not on the whitelist is dropped here, including author.
    return {field: row.get(field) for field in CACHE_FIELDS}


def apify_run(cfg: dict, run_input: dict, timeout: int = APIFY_TIMEOUT_S):
    """One synchronous actor run -> its dataset items, or None.

    Degrades rather than raises, the app.py Scorecard idiom -- but the CALLER
    counts the failure, because a dropped page here is silent truncation of the
    corpus rather than one missing value, and a ranking computed with full
    confidence over a corpus that quietly lost a subreddit is the failure this
    whole tool would be worst at showing.
    """
    url = APIFY_RUN_URL.format(actor=cfg["actor"])
    try:
        response = requests.post(
            url, json=run_input, timeout=timeout, verify=certifi.where(),
            headers={"Authorization": f"Bearer {cfg['token']}",
                     "Content-Type": "application/json"})
        response.raise_for_status()
        items = response.json()
        return items if isinstance(items, list) else None
    except (requests.exceptions.RequestException, ValueError, KeyError) as error:
        print(f"  ! Apify run failed: {error}", file=sys.stderr)
        return None


def check_auth_apify(cfg: dict) -> int:
    """One cheap call, and print what the actor ACTUALLY returns.

    This exists to be run before anything downstream is built. ACTOR_FIELD_MAP
    is a guess about someone else's schema until this has printed the real keys
    -- and a wrong guess does not raise, it produces empty columns and a
    confident ranking over nothing.
    """
    print(f"Apify actor : {cfg['actor']}")
    print(f"Endpoint    : {APIFY_RUN_URL.format(actor=cfg['actor'])}")
    probe_sub = SUBREDDITS[0]
    run_input = apify_run_input(probe_sub, "month", 3)
    print(f"Input       : {json.dumps(run_input)}")
    items = apify_run(cfg, run_input)
    if items is None:
        print("\nFAILED -- no items returned. Check the token and the actor id.")
        return 1
    print(f"\nReturned {len(items)} item(s).")
    if not items:
        print("An empty list is not proof of success: the actor ran and found "
              "nothing, which is also what a wrong input schema looks like.")
        return 1
    print("\nRAW KEYS the actor returned (this is what ACTOR_FIELD_MAP must map):")
    for key in sorted(items[0].keys()):
        value = items[0][key]
        shown = str(value)
        if len(shown) > 60:
            shown = shown[:57] + "..."
        print(f"  {key:28} {shown}")
    print("\nMapped through ACTOR_FIELD_MAP:")
    row = normalize_apify_item(items[0], "check-auth", _now_iso())
    if row is None:
        print("  ! normalize_apify_item returned None -- id or title did not "
              "map. Fix ACTOR_FIELD_MAP against the raw keys above.")
        return 1
    for field in CACHE_FIELDS:
        value = str(row.get(field))
        if len(value) > 60:
            value = value[:57] + "..."
        missing = row.get(field) in (None, "", 0)
        marker = "  " if not missing else (" ~" if field in OPTIONAL_CACHE_FIELDS
                                           else " !")
        print(f"{marker}{field:28} {value}")
    unmapped = [f for f in CACHE_FIELDS
                if row.get(f) in (None, "") and f not in OPTIONAL_CACHE_FIELDS]
    if unmapped:
        print(f"\n! {len(unmapped)} required field(s) did not map: "
              f"{', '.join(unmapped)}")
        print("  Add the actor's real key to ACTOR_FIELD_MAP before harvesting: "
              "an unmapped column is silently empty, not an error.")
        return 1
    # A 201 with real posts from the wrong communities is this actor's
    # signature failure, so the probe checks WHICH subreddit answered.
    got = {str(normalize_apify_item(i, "probe", "x") or {}).lower() and
           (normalize_apify_item(i, "probe", "x") or {}).get("subreddit", "")
           for i in items}
    got = {s for s in got if s}
    if any(s.lower() != probe_sub.lower() for s in got):
        print(f"\n! asked for r/{probe_sub} and got {sorted(got)}.")
        print("  The actor is ignoring startUrls -- see the SUBREDDIT_URL "
              "comment. Do NOT harvest until this returns the right community.")
        return 1
    print(f"\nOK -- every required cache field mapped, and r/{probe_sub} "
          f"answered for r/{probe_sub}.")
    print(f"  Author field in the payload: "
          f"{'username' in items[0] and 'yes, and it is NOT mapped to disk' or 'absent'}")
    return 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- the cache -------------------------------------------------------------

def write_cache(rows: list, meta: dict) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    with open(CACHE_PATH, "w") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    META_PATH.write_text(json.dumps(meta, indent=1))


def read_cache():
    """(rows, meta) or (None, None). JSONL because post text carries newlines,
    commas and quotes, and JSONL has no quoting rules to get wrong."""
    if not CACHE_PATH.exists():
        return None, None
    rows = []
    with open(CACHE_PATH) as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    meta = {}
    if META_PATH.exists():
        try:
            meta = json.loads(META_PATH.read_text())
        except ValueError:
            meta = {}
    return rows, meta


def cache_age_days(meta: dict, now_ts: float) -> float:
    stamp = (meta or {}).get("fetched_at")
    if not stamp:
        return float("inf")
    try:
        fetched = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return float("inf")
    return max(0.0, (now_ts - fetched.timestamp()) / 86400.0)


def harvest_apify(cfg: dict, subreddits: tuple, windows: tuple,
                  max_items: int) -> tuple:
    """Every (subreddit, window) pair -> deduped cache rows, and a failure count.

    One actor run per pair rather than one big run: a synchronous Apify run is
    cut off at 300 s, and a failure then costs one slice rather than the whole
    harvest. The failures are COUNTED and surface in the digest header --
    a dropped slice is silent truncation of the corpus, not a missing value,
    and a ranking computed at full confidence over a corpus that quietly lost a
    subreddit is the one error this tool could not show you.
    """
    fetched_at = _now_iso()
    by_id, failures = {}, 0
    wrong_subreddit = 0
    for subreddit in subreddits:
        for window in windows:
            print(f"  r/{subreddit} top/{window} ...", file=sys.stderr, end=" ")
            items = apify_run(cfg, apify_run_input(subreddit, window, max_items))
            if items is None:
                failures += 1
                print("FAILED", file=sys.stderr)
                continue
            kept, stray = 0, 0
            for item in items:
                row = normalize_apify_item(item, f"top:{window}", fetched_at)
                if not row:
                    continue
                # What came back must be what we asked for. See SUBREDDIT_URL:
                # a URL shape this actor dislikes is answered with real posts
                # from unrelated communities and a 201, so nothing downstream
                # would ever notice.
                if (row["subreddit"] or "").lower() != subreddit.lower():
                    stray += 1
                    continue
                if row["id"] not in by_id:
                    by_id[row["id"]] = row
                    kept += 1
            wrong_subreddit += stray
            note = f"{len(items)} items, {kept} new"
            if stray:
                note += f", {stray} FROM THE WRONG SUBREDDIT (dropped)"
            print(note, file=sys.stderr)
    if wrong_subreddit:
        print(f"  ! {wrong_subreddit} post(s) came back from subreddits nobody "
              f"asked for and were dropped. If this is most of the harvest, the "
              f"actor is ignoring startUrls -- check SUBREDDIT_URL.",
              file=sys.stderr)
    meta = {
        "fetched_at": fetched_at, "source": "apify", "actor": cfg["actor"],
        "subreddits": list(subreddits), "windows": list(windows),
        "n_posts": len(by_id), "failed_slices": failures,
        "truncated": failures > 0,
    }
    return list(by_id.values()), meta


# --- what our own visitors ask ---------------------------------------------
#
# Reddit tells us what a large, loud, self-selected crowd is asking. This half
# tells us what the people who ACTUALLY LOADED THE CALCULATOR did, which is a
# smaller and much better-aimed sample -- and it needs no third party.
#
# Three signals, each keyed to the SAME TOPIC_VOCAB entries as the Reddit half,
# so the two kinds of evidence land on one ranked list rather than in two
# tables nobody reconciles.

# A zero-result school search is the strongest of the three. "No school teaches
# your field at that price" is a question a real visitor asked and got no
# answer to -- the app's own admission that it could not help. It maps to the
# cost-of-attendance topic plus whichever field was searched.
ZERO_RESULT_TOPIC = "cost-of-attendance-gap"

# scenario_events carries what people MODEL. A professional-school pick is a
# stronger statement of intent than a major pick, because it means they went
# looking for the picker.
GRADUATE_EDUCATION_TOPICS = {
    "Master's degree": "grad-school-debt",
    "Doctoral or professional degree": "professional-school-debt",
}

# usage_logs prefixes we read. Imported from app.py where possible rather than
# retyped -- but this script must run when that exec fails, so they are
# defaults, overridden by load_app_prefixes().
EDGE_PREFIXES = {"guide": "guide_view", "like": "article_like",
                 "share": "article_share"}


def load_app_prefixes() -> dict:
    """The edge action prefixes, from app.py rather than retyped.

    analyze_traffic.py execs app.py's section 1-2 prefix for exactly this
    reason: a constant copied into a second file is a constant that will
    diverge. Falls back to the literals above if the exec fails, because a
    digest is not worth taking down over a prefix.
    """
    try:
        import analyze_traffic
        ns = analyze_traffic.load_app_namespace()
        return {"guide": ns["GUIDE_ACTION_PREFIX"],
                "like": ns["LIKE_ACTION_PREFIX"],
                "share": ns["SHARE_ACTION_PREFIX"]}
    except Exception as error:                            # pragma: no cover
        print(f"  ! could not read the action prefixes from app.py ({error}); "
              f"using the built-in defaults", file=sys.stderr)
        return dict(EDGE_PREFIXES)


def fetch_first_party():
    """(usage_logs, scenario_events) as DataFrames, or (None, None).

    Absent credentials are survivable: the Reddit half still ranks, and the
    digest says the first-party column is missing rather than printing zeros
    that look like "nobody did this".
    """
    try:
        import analyze_survey
        client = analyze_survey.load_supabase_client()
        usage = analyze_survey.fetch_table(client, "usage_logs")
        events = analyze_survey.fetch_table(client, "scenario_events")
        return usage, events
    except SystemExit:
        raise
    except Exception as error:
        print(f"  ! first-party signals unavailable ({error})", file=sys.stderr)
        return None, None


def own_demand_signal(usage, events, cip_titles: dict = None) -> dict:
    """{topic_key: {"weight": float, "note": str}} from our own tables.

    Weights are COUNTS, not rates. A rate needs a denominator, and every
    denominator available here is either missing (edge rows carry no session)
    or means something different per signal -- so a rate would be a number
    dressed as a measurement. rank_pct turns the counts into a comparable
    column later, which is all the score needs.
    """
    import pandas as pd

    signal = {}

    def add(key, weight, note):
        if not key or key not in TOPIC_VOCAB or weight <= 0:
            return
        entry = signal.setdefault(key, {"weight": 0.0, "notes": []})
        entry["weight"] += float(weight)
        entry["notes"].append(note)

    # 1. Zero-result searches -- the app admitting it could not answer.
    #
    # CONDITION ON THE DATE. migrations.sql records three regimes for this
    # event: it did not exist before 2026-08-01, and between then and
    # 2026-08-02 a renamed budget key meant a visitor who adjusted only the
    # cost range wrote no row at all. Rows before the later date are a biased
    # sample, not a smaller one, so they are dropped rather than pooled.
    if usage is not None and not usage.empty and "action" in usage.columns:
        actions = usage["action"].astype(str)
        runs = usage[actions.str.startswith("school_search_run:")].copy()
        if not runs.empty and "timestamp" in runs.columns:
            stamps = pd.to_datetime(runs["timestamp"], errors="coerce", utc=True)
            runs = runs[stamps >= pd.Timestamp("2026-08-02", tz="UTC")]
        if not runs.empty:
            parsed = runs["action"].astype(str).str.extract(
                r"cip=(?P<cip>[^:]*).*?:n=(?P<hits>\d+)$")
            parsed["hits"] = pd.to_numeric(parsed["hits"], errors="coerce")
            empty = parsed[parsed["hits"] == 0]
            if len(empty):
                fields = [f for f in empty["cip"].dropna().unique()
                          if f and f != "None"]
                named = ", ".join(
                    (cip_titles or {}).get(f, f) for f in fields[:3])
                add(ZERO_RESULT_TOPIC, len(empty),
                    f"{len(empty)} search(es) returned NOTHING"
                    + (f" (fields: {named})" if named else ""))

        # 2. Guide engagement -- which of the published guides land.
        prefixes = load_app_prefixes()
        for kind, prefix in prefixes.items():
            rows = actions[actions.str.startswith(f"{prefix}:")]
            if rows.empty:
                continue
            slugs = rows.str.split("slug=").str[-1].value_counts()
            for slug, count in slugs.items():
                if slug == "index":
                    continue
                # A guide's reads are evidence for the topics ITS OWN subject
                # covers, which is what the front matter states. Matching the
                # slug's words against the vocabulary is the cheapest honest
                # link between the two.
                for key, entry in TOPIC_VOCAB.items():
                    if any(phrase_pattern(p).search(slug.replace("-", " "))
                           for p in entry["phrases"]):
                        # A share is worth more than a like, and a like far
                        # more than a read: each is a longer step to take.
                        weight = {"guide": 0.05, "like": 1.0, "share": 2.0}[kind]
                        add(key, count * weight,
                            f"{count} {kind}(s) on /{slug}")

    # 3. What people model. Ordered by event_seq, never timestamp -- the
    #    timestamps come from the visitor's own clock and can tie or move
    #    backwards across the timezone round-trip.
    if events is not None and not events.empty:
        if "event_seq" in events.columns:
            events = events.sort_values("event_seq")
        for column, topics in (("scenario_a_typical_education",
                                GRADUATE_EDUCATION_TOPICS),):
            if column in events.columns:
                for level, key in topics.items():
                    count = int((events[column] == level).sum())
                    add(key, count * 0.5,
                        f"{count} scenario(s) modelled at {level!r}")
        for column, key, note in (
                ("prof_school_a", "professional-school-debt",
                 "professional-school picker used"),
                ("cc_mode_a", "community-college-transfer",
                 "community-college path selected")):
            if column in events.columns:
                used = events[column].dropna()
                used = used[~used.astype(str).isin(("none", "None", ""))]
                add(key, len(used) * 0.5, f"{len(used)} scenario(s): {note}")

    return {k: {"weight": v["weight"], "note": "; ".join(v["notes"][:3])}
            for k, v in signal.items()}


# --- the score -------------------------------------------------------------
#
# Seven components, each 0-1, each printed in the digest WITH ITS RAW INPUT
# beside it. A single opaque number would be unarguable, and the whole point of
# this file is that a human overrules it.
#
# answerable is the largest because of what Reddit is: its loudest student-loan
# content is servicer horror stories and forgiveness politics, none of which
# this app can put a number on, all of which would out-score everything on
# engagement alone.
SCORE_WEIGHTS = {
    "answerable": 0.28,
    "demand": 0.18,
    "own_demand": 0.15,
    "breadth": 0.15,
    "recency": 0.10,
    "novelty": 0.08,
    "specific": 0.06,
}

MIN_THREADS = 3           # below this a cluster is an anecdote, not evidence
SIM_THRESHOLD = 0.28
RECENCY_HALFLIFE_DAYS = 120.0   # these guides are evergreen; 30 would bury them
NEAR_DUPLICATE_SIM = 0.45
SEASONAL_SHARE = 0.60


def score_cluster(components: dict) -> float:
    """IdeaScore, 0-100. Kept tiny and pure so the negative controls can assert
    the ARITHMETIC rather than just the ordering -- a weight edited without the
    docstring following should fail a test, not merely reorder a list."""
    return 100.0 * sum(SCORE_WEIGHTS[k] * float(components.get(k, 0.0))
                       for k in SCORE_WEIGHTS)


def rank_pct(values):
    """Percentile rank with averaged ties. pandas' rank(pct=True) without
    pulling pandas in for one column; scipy is not a dependency here.

    Rank rather than min-max because one outlier cluster would otherwise
    compress every other cluster into the bottom decile.
    """
    import numpy as np
    array = np.asarray(list(values), dtype=float)
    if array.size == 0:
        return array
    if array.size == 1:
        return np.ones(1)
    ranks = np.empty(array.size, dtype=float)
    ranks[array.argsort()] = np.arange(1, array.size + 1, dtype=float)
    for value in np.unique(array):
        mask = array == value
        if mask.sum() > 1:
            ranks[mask] = ranks[mask].mean()
    return ranks / array.size


# --- text ------------------------------------------------------------------

# analyze_survey.STOPWORDS is imported rather than re-declared -- a second copy
# of a word list is the chart-twin drift trap applied to vocabulary. These are
# the additions this corpus needs: forum furniture, and the words that are
# universal HERE and therefore carry no discriminating signal at all.
FORUM_STOPWORDS = {
    "reddit", "post", "edit", "update", "help", "advice", "question", "anyone",
    "please", "thanks", "guys", "tldr", "hi", "hello", "im", "ive", "dont",
    "college", "loan", "loans", "student", "students", "school", "money",
    "studentloans", "applyingtocollege", "financialaid", "any", "some", "there",
}

NUMBER_TOKEN = "#num"
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_NUM_RE = re.compile(r"\$?\d[\d,\.]*\s?[km%]?", re.I)
_NONWORD_RE = re.compile(r"[^a-z0-9#\s]+")
_HAS_NUMBER_RE = re.compile(r"\$\s?\d|\d+\s?%|\b\d{2,3}k\b|\b\d{4,}\b", re.I)
_QUESTION_OPENERS = ("how", "what", "can", "should", "is", "do", "why", "when",
                     "does", "are", "will", "would", "any")


def stopwords() -> set:
    """The shared list plus this corpus's own, resolved lazily.

    Imported from analyze_survey rather than copied. If that import ever fails
    the tool still runs -- a missing stopword list costs some noise in the
    emergent clusters, which is not worth taking the digest down for.
    """
    try:
        import analyze_survey
        base = set(analyze_survey.STOPWORDS)
    except Exception:                                    # pragma: no cover
        print("  ! could not import analyze_survey.STOPWORDS; using the "
              "forum list alone", file=sys.stderr)
        base = set()
    return base | FORUM_STOPWORDS


def normalize_text(text: str, collapse_numbers: bool = True) -> str:
    """Lowercase, strip URLs and punctuation; optionally collapse every number
    to #num.

    The collapse is what makes CLUSTERING work: without it the vocabulary
    explodes into thousands of single-occurrence dollar amounts and `$65,000`
    never matches `$65k` -- two ways of asking one question landing in two
    clusters. The numeric information is not lost, it is captured separately as
    `has_number` for the `specific` score.

    But it must NOT be applied to PHRASE MATCHING, and that is not a nicety.
    The fixture caught it: "65k" in the Parent PLUS phrase list normalized to
    "#num", which then matched every post containing any number at all -- so
    "RAP vs IBR for a 60k balance" was filed as evidence about Parent PLUS
    caps. A cluster silently inflated with threads about something else is
    exactly the failure this tool would be worst at showing, because the count
    goes UP and everything still looks plausible.
    """
    text = _URL_RE.sub(" ", (text or "").lower())
    if collapse_numbers:
        text = _NUM_RE.sub(f" {NUMBER_TOKEN} ", text)
    text = _NONWORD_RE.sub(" ", text)
    return " ".join(text.split())


def normalize_plain(text: str) -> str:
    """Normalization with the numbers left alone, for phrase matching."""
    return normalize_text(text, collapse_numbers=False)


def tokenize(text: str, stops: set) -> list:
    """Unigrams and bigrams in one space.

    Bigram-only was rejected: titles run 8-15 tokens, so a bigram-only vector
    has a handful of nonzeros and genuinely related pairs share none of them
    ("Parent PLUS denied" vs "denied for PLUS loan"). Bigrams are weighted 2x
    later, which is where "a bigram match is stronger evidence" is encoded.
    """
    words = [w for w in normalize_text(text).split()
             if w not in stops and len(w) > 1]
    grams = list(words)
    grams += [f"{a} {b}" for a, b in zip(words, words[1:])]
    return grams


def document_for(post: dict) -> str:
    """Title counted twice, then the body. The title IS the question; the body
    is context, and on Reddit it is often several paragraphs of it."""
    title = post.get("title") or ""
    body = (post.get("selftext_excerpt") or "")[:300]
    return f"{title} {title} {body}"


def build_tfidf(documents: list, stops: set):
    """Sublinear-TF, smoothed-IDF, L2-normalized vectors. numpy only.

    sklearn is not in requirements.txt and this repo does not add a dependency
    for a local script, so this is ~20 lines by hand. At this corpus size the
    dense matrix is a couple of million floats, so `X @ X.T` is one line and
    there is no case for sparse structures.
    """
    import numpy as np

    tokenized = [tokenize(d, stops) for d in documents]
    n_docs = len(tokenized)
    doc_freq = {}
    for grams in tokenized:
        for term in set(grams):
            doc_freq[term] = doc_freq.get(term, 0) + 1
    # A term in one document cannot cluster anything, and a term in nearly
    # every document cannot separate anything.
    min_df = 2 if n_docs < 60 else 3
    max_df = max(min_df + 1, int(0.4 * n_docs))
    vocab = sorted(t for t, df in doc_freq.items() if min_df <= df < max_df)
    if not vocab:
        return np.zeros((n_docs, 0)), []
    index = {term: i for i, term in enumerate(vocab)}
    matrix = np.zeros((n_docs, len(vocab)), dtype=float)
    for row, grams in enumerate(tokenized):
        counts = {}
        for term in grams:
            if term in index:
                counts[term] = counts.get(term, 0) + 1
        for term, count in counts.items():
            tf = 1.0 + math.log(count)
            idf = math.log((1.0 + n_docs) / (1.0 + doc_freq[term])) + 1.0
            # A bigram match is stronger evidence than a unigram match, and
            # this is the one line that says so.
            weight = 2.0 if " " in term else 1.0
            matrix[row, index[term]] = tf * idf * weight
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms, vocab


def post_engagement(post: dict) -> float:
    """log1p(score) + 1.5*log1p(comments).

    Log because Reddit score is roughly power-law -- one 12,000-point thread
    would otherwise own the entire ranking. Comments at 1.5x because an upvote
    is agreement and a COMMENT is a question being discussed, and we are mining
    for questions. On the log that 1.5 is a thumb on the scale, not a
    reweighting.
    """
    return (math.log1p(max(0, post.get("score") or 0))
            + 1.5 * math.log1p(max(0, post.get("num_comments") or 0)))


def has_number(post: dict) -> bool:
    return bool(_HAS_NUMBER_RE.search(f"{post.get('title','')} "
                                      f"{post.get('selftext_excerpt','')}"))


def is_question(post: dict) -> bool:
    title = (post.get("title") or "").strip().lower()
    return title.endswith("?") or title.startswith(_QUESTION_OPENERS)


# --- clustering ------------------------------------------------------------

_PHRASE_CACHE = {}


def phrase_pattern(phrase: str):
    """A phrase matcher anchored on WORD BOUNDARIES, not substrings.

    Plain `in` matching was the first implementation and it was wrong in a way
    the fixture caught: "rap" matched *therapy*, "coa" matched *coalition*, and
    "cc" matched *account*. Every one of those silently files a thread under a
    topic it is not about, which then reads as evidence.
    """
    if phrase not in _PHRASE_CACHE:
        _PHRASE_CACHE[phrase] = re.compile(
            r"(?<!\w)" + re.escape(normalize_plain(phrase)) + r"(?!\w)")
    return _PHRASE_CACHE[phrase]


def matched_phrases(post: dict, entry: dict) -> list:
    """Which of a topic's phrases this post actually contains."""
    haystack = normalize_plain(f"{post.get('title','')} "
                               f"{post.get('selftext_excerpt','')}")
    return [p for p in entry.get("phrases", ())
            if phrase_pattern(p).search(haystack)]


def match_vocabulary(posts: list, table: dict) -> dict:
    """{key: [post indices]}, MULTI-LABEL.

    A post joins a topic on a TITLE match, or on TWO distinct phrases anywhere.
    That rule is not fussiness -- it is what the first real harvest forced.

    Matching one phrase against title-plus-body put "Are colleges dumbing down
    their curriculum?" under the ROI-horizon topic, because its body happened
    to contain "10 years". Bodies run to 500 characters and generic phrases hit
    them constantly: of the roi-horizon matches, every single one came from the
    body and none from a title. The title IS the question; the body is context,
    and one generic phrase buried in context is not evidence of a topic.

    Two distinct phrases keeps the recall that matters -- a post titled "Need
    advice" whose body says "Parent PLUS" and "aggregate cap" is genuinely
    about Parent PLUS -- while one stray "10 years" no longer is.

    Still MULTI-LABEL: a thread about both PLUS caps and the cost-of-attendance
    gap is evidence for both. That inflates breadth unless disclosed, which is
    why overlapping clusters print how many threads they share.
    """
    hits = {key: [] for key in table}
    for i, post in enumerate(posts):
        title = normalize_plain(post.get("title") or "")
        body = normalize_plain(post.get("selftext_excerpt") or "")
        for key, entry in table.items():
            in_title = any(phrase_pattern(p).search(title)
                           for p in entry["phrases"])
            # A `strong` phrase is one that cannot plausibly mean anything else
            # here -- "parent plus", "unsubsidized", "cost of attendance". Those
            # are trusted in a body on their own; the title-or-two rule exists
            # for the ambiguous remainder, and applying it to everything cost
            # more than half the vocabulary coverage on the first real harvest
            # (42% -> 17%) for threads that were correctly matched all along.
            in_body_strong = any(phrase_pattern(p).search(body)
                                 for p in entry.get("strong", ()))
            distinct = sum(1 for p in entry["phrases"]
                           if phrase_pattern(p).search(title)
                           or phrase_pattern(p).search(body))
            if in_title or in_body_strong or distinct >= 2:
                hits[key].append(i)
    return {k: v for k, v in hits.items() if v}


def cluster_emergent(indices: list, matrix, sim_threshold: float,
                     engagement: list) -> list:
    """Greedy seeded agglomeration with a similarity floor and ONE hop.

    Not k-means: it needs a `k` nobody can justify, it is non-deterministic
    without a seed, and its centroids are synthetic points no human can read.

    Seeds are taken in descending engagement, so each cluster's label is a REAL
    TITLE rather than a bag of terms. The single expansion hop is the important
    constraint -- unbounded single-link chaining merges the whole corpus into
    one blob at any threshold low enough to be useful.

    Deterministic by construction: fixed input order, fixed threshold, no
    randomness anywhere. That is what makes --cache-only reproducible.
    """
    import numpy as np

    if not indices:
        return []
    order = sorted(indices, key=lambda i: -engagement[i])
    unassigned = set(indices)
    clusters = []
    for seed in order:
        if seed not in unassigned:
            continue
        unassigned.discard(seed)
        members = [seed]
        sims = matrix @ matrix[seed]
        first_hop = [i for i in sorted(unassigned, key=lambda i: -engagement[i])
                     if sims[i] >= sim_threshold]
        for i in first_hop:
            unassigned.discard(i)
            members.append(i)
        for parent in list(first_hop):                    # exactly one hop
            if not unassigned:
                break
            child_sims = matrix @ matrix[parent]
            for i in [j for j in sorted(unassigned, key=lambda j: -engagement[j])
                      if child_sims[j] >= sim_threshold]:
                unassigned.discard(i)
                members.append(i)
        clusters.append(members)
    return clusters


def top_terms(indices: list, matrix, vocab: list, limit: int = 3) -> list:
    import numpy as np
    if not vocab or not len(indices):
        return []
    summed = matrix[indices].sum(axis=0)
    best = np.argsort(-summed)[:limit]
    return [(vocab[i], round(float(summed[i]), 2)) for i in best if summed[i] > 0]


# --- assembly --------------------------------------------------------------

def load_guide_documents() -> list:
    """(slug, text) for every published guide, for the novelty comparison.

    Front matter is stripped: a guide's `description` is a summary of the same
    prose that follows it, so leaving it in double-counts the topic sentence.
    Missing directory returns [] -- novelty then reads 1.0 for everything,
    which is honest (nothing to be a duplicate OF) rather than a crash.
    """
    posts_dir = Path(__file__).parent / "content" / "posts"
    if not posts_dir.exists():
        return []
    guides = []
    for path in sorted(posts_dir.glob("*.md")):
        raw = path.read_text()
        body = raw.split("---\n", 2)[2] if raw.startswith("---\n") else raw
        guides.append((path.stem, body))
    return guides


def guide_front_matter_terms() -> dict:
    """{slug: normalized title + description}.

    Used to floor novelty when a topic is named outright in a guide's front
    matter. Cosine between an 8-word title and a 2,000-word article is
    unreliable in that direction, and the front matter is an EXACT statement of
    what the guide claims to cover -- so it overrules the geometry.
    """
    posts_dir = Path(__file__).parent / "content" / "posts"
    if not posts_dir.exists():
        return {}
    out = {}
    for path in sorted(posts_dir.glob("*.md")):
        raw = path.read_text()
        if not raw.startswith("---\n"):
            continue
        front = raw.split("---\n", 2)[1]
        fields = []
        for line in front.strip().splitlines():
            key, _, value = line.partition(":")
            if key.strip() in ("title", "description", "summary"):
                fields.append(value.strip())
        out[path.stem] = normalize_plain(" ".join(fields))
    return out


def seasonal_flag(posts: list) -> str:
    """"seasonal:Mar (7/11 threads, 2 years)" or "".

    Deliberately PRINTED BUT NOT SCORED. A March spike does not mean "write
    this now", it means "schedule this for March" -- which is a fact about
    timing, and folding it into a score would express it as urgency instead.
    """
    from collections import Counter
    months, years = Counter(), {}
    for post in posts:
        stamp = post.get("created_utc") or 0
        if not stamp:
            continue
        when = datetime.fromtimestamp(stamp, tz=timezone.utc)
        months[when.month] += 1
        years.setdefault(when.month, set()).add(when.year)
    if not months:
        return ""
    month, count = months.most_common(1)[0]
    total = sum(months.values())
    if count / total >= SEASONAL_SHARE and len(years[month]) >= 2:
        name = datetime(2000, month, 1).strftime("%b")
        return (f"seasonal:{name} ({count}/{total} threads, "
                f"{len(years[month])} years)")
    return ""


def build_clusters(posts: list, asof_ts: float, own_signal: dict = None,
                   sim_threshold: float = SIM_THRESHOLD,
                   min_threads: int = MIN_THREADS,
                   halflife: float = RECENCY_HALFLIFE_DAYS) -> dict:
    """Posts in, ranked clusters out. Pure -- no network, no clock, no state.

    asof_ts is passed rather than read from the clock so a run is reproducible:
    the same cache under the same --asof produces byte-identical output.
    """
    import numpy as np

    stops = stopwords()
    own_signal = own_signal or {}
    engagement = [post_engagement(p) for p in posts]

    guides = load_guide_documents()
    front_matter = guide_front_matter_terms()
    # Guides are vectorized IN THE SAME SPACE as the posts, by being extra rows
    # of one matrix. Two separate TF-IDF fits would put the two on different
    # bases and make every cosine between them meaningless.
    documents = [document_for(p) for p in posts] + [text for _, text in guides]
    matrix, vocab = build_tfidf(documents, stops)
    post_matrix = matrix[:len(posts)]
    guide_matrix = matrix[len(posts):]

    out_hits = match_vocabulary(posts, CANNOT_ANSWER)
    vocab_hits = match_vocabulary(posts, TOPIC_VOCAB)
    claimed = set()
    for members in vocab_hits.values():
        claimed.update(members)
    out_claimed = set()
    for members in out_hits.values():
        out_claimed.update(members)

    # The emergent pass sees ONLY what neither table knows. Leaving the matched
    # posts in would have it rediscover "parent plus" as a hot bigram and
    # present a cluster we already have a vocabulary entry for as a discovery.
    leftover = [i for i in range(len(posts))
                if i not in claimed and i not in out_claimed]
    emergent = cluster_emergent(leftover, post_matrix, sim_threshold, engagement)

    def describe(members: list, kind: str, key: str = None,
                 label: str = None) -> dict:
        group = [posts[i] for i in members]
        ages = [max(0.0, (asof_ts - (p.get("created_utc") or asof_ts)) / 86400.0)
                for p in group]
        subs = sorted({p.get("subreddit") or "?" for p in group})
        entry = (TOPIC_VOCAB.get(key) or CANNOT_ANSWER.get(key) or {}) if key else {}
        centroid = post_matrix[members].mean(axis=0) if len(vocab) else None
        best_guide, best_sim = None, 0.0
        if centroid is not None and len(guides):
            norm = np.linalg.norm(centroid)
            if norm > 0:
                sims = guide_matrix @ (centroid / norm)
                pos = int(np.argmax(sims))
                best_guide, best_sim = guides[pos][0], float(sims[pos])
        # The front matter overrules the geometry, in one direction only: it
        # can say "this IS covered", never "this is not".
        if key and front_matter:
            for slug, text in front_matter.items():
                if any(phrase_pattern(p).search(text)
                       for p in entry.get("phrases", ())):
                    best_guide = best_guide or slug
                    best_sim = max(best_sim, 1.0 - 0.20)
        return {
            "kind": kind,
            "topic_key": key,
            "label": label or entry.get("label") or "(unlabelled)",
            "members": members,
            "posts": group,
            "n_threads": len(group),
            "n_subs": len(subs),
            "subs": subs,
            "median_engagement": float(np.median([engagement[i] for i in members])),
            "median_score": float(np.median([p.get("score") or 0 for p in group])),
            "median_comments": float(np.median([p.get("num_comments") or 0
                                                for p in group])),
            "median_age_days": float(np.median(ages)) if ages else 0.0,
            "seasonal": seasonal_flag(group),
            "answerable": (entry.get("answerable", UNCHECKED_ANSWERABLE)
                           if kind != "out_of_scope" else ANSWERABLE_NONE),
            "backing": entry.get("backing", ""),
            "why_excluded": entry.get("why", ""),
            "nearest_guide": best_guide,
            "max_guide_sim": best_sim,
            "numeric_share": (sum(has_number(p) for p in group) / len(group)),
            "question_share": (sum(is_question(p) for p in group) / len(group)),
            "own_demand_raw": float(own_signal.get(key, {}).get("weight", 0.0)) if key else 0.0,
            "own_demand_note": own_signal.get(key, {}).get("note", "") if key else "",
            "top_terms": top_terms(members, post_matrix, vocab),
            "seed_title": max(group, key=lambda p: post_engagement(p)).get("title", ""),
            "seed_permalink": max(group, key=lambda p: post_engagement(p)).get("permalink", ""),
        }

    out_of_scope = [describe(m, "out_of_scope", k) for k, m in sorted(out_hits.items())]
    candidates = [describe(m, "vocab", k) for k, m in sorted(vocab_hits.items())]
    for members in emergent:
        terms = top_terms(members, post_matrix, vocab)
        label = " · ".join(f"{t} ({w})" for t, w in terms) or "(no shared terms)"
        candidates.append(describe(members, "emergent", None, label))

    # A topic evidenced only by our own visitors is a real cluster with zero
    # Reddit threads behind it -- it ranks on own_demand alone.
    for key, signal in sorted((own_signal or {}).items()):
        if key in vocab_hits or key not in TOPIC_VOCAB:
            continue
        if signal.get("weight", 0) <= 0:
            continue
        entry = TOPIC_VOCAB[key]
        candidates.append({
            "kind": "own", "topic_key": key, "label": entry["label"],
            "members": [], "posts": [], "n_threads": 0, "n_subs": 0, "subs": [],
            "median_engagement": 0.0, "median_score": 0.0, "median_comments": 0.0,
            "median_age_days": 0.0, "seasonal": "",
            "answerable": entry.get("answerable", UNCHECKED_ANSWERABLE),
            "backing": entry.get("backing", ""), "why_excluded": "",
            "nearest_guide": None, "max_guide_sim": 0.0,
            "numeric_share": 0.0, "question_share": 0.0,
            "own_demand_raw": float(signal.get("weight", 0.0)),
            "own_demand_note": signal.get("note", ""),
            "top_terms": [], "seed_title": "", "seed_permalink": "",
        })

    # Anything below the floor is evidence of nothing. A single thread is an
    # anecdote; repetition across independent threads is the entire premise.
    ranked = [c for c in candidates
              if c["n_threads"] >= min_threads or c["kind"] == "own"]
    thin = [c for c in candidates
            if c not in ranked and c["kind"] != "out_of_scope"]

    if ranked:
        demand = rank_pct([c["median_engagement"] for c in ranked])
        breadth_raw = [math.log1p(c["n_threads"]) * (1 + 0.25 * max(0, c["n_subs"] - 1))
                       for c in ranked]
        breadth = rank_pct(breadth_raw)
        # Ranked ONLY over the clusters that actually carry a signal, with the
        # rest pinned to 0. rank_pct over the whole column gives every
        # zero-evidence cluster the averaged tie-rank of the zeros -- 0.48 on
        # this corpus, which is a free half-point for having no evidence at
        # all, awarded to most of the list. Absent must mean absent.
        with_signal = [i for i, c in enumerate(ranked) if c["own_demand_raw"] > 0]
        own = [0.0] * len(ranked)
        if with_signal:
            scaled = rank_pct([ranked[i]["own_demand_raw"] for i in with_signal])
            for slot, i in enumerate(with_signal):
                own[i] = float(scaled[slot])
        any_own = bool(with_signal)
        for i, cluster in enumerate(ranked):
            cluster["demand"] = float(demand[i])
            cluster["breadth_raw"] = breadth_raw[i]
            cluster["breadth"] = float(breadth[i])
            cluster["own_demand"] = float(own[i]) if any_own else 0.0
            cluster["recency"] = 0.5 ** (cluster["median_age_days"] / halflife)
            cluster["novelty"] = max(0.0, 1.0 - cluster["max_guide_sim"])
            cluster["specific"] = (0.6 * cluster["numeric_share"]
                                   + 0.4 * cluster["question_share"])
            cluster["idea_score"] = score_cluster(cluster)
        ranked.sort(key=lambda c: -c["idea_score"])
    for i, cluster in enumerate(ranked, 1):
        cluster["cluster_id"] = i

    # Vocabulary matching is multi-label, so one thread can support several
    # clusters -- and it inflates breadth in every one of them. Undisclosed,
    # that presents a single piece of evidence as though it were two
    # independent ones. Computed after the ids are assigned so the note can
    # name the other cluster the way the reader sees it.
    for cluster in ranked:
        mine = set(cluster["members"])
        if not mine:
            continue
        shared = []
        for other in ranked:
            if other is cluster or not other["members"]:
                continue
            common = len(mine & set(other["members"]))
            if common:
                shared.append((common, other["cluster_id"], other["label"]))
        shared.sort(reverse=True)
        cluster["overlaps"] = "; ".join(
            f"shares {n} thread(s) with #{cid} ({label[:28]})"
            for n, cid, label in shared[:2])

    matched = len(claimed | out_claimed)
    return {
        "ranked": ranked,
        "out_of_scope": sorted(out_of_scope, key=lambda c: -c["median_engagement"]),
        "thin": sorted(thin, key=lambda c: -c["median_engagement"]),
        "n_posts": len(posts),
        "coverage": (matched / len(posts)) if posts else 0.0,
        "sim_threshold": sim_threshold,
        "min_threads": min_threads,
        "halflife": halflife,
    }


# --- the rules that travel with the output ---------------------------------
#
# These live in the DIGEST, not only in this docstring, because the session
# that writes the article reads the digest and never opens this file.
RULES_BLOCK = """\
  HOW TO USE THIS FILE -- READ BEFORE DRAFTING

  This is a PRIVATE working file under analysis_output/, which is gitignored.
  It never ships, and no part of it is publishable copy.

  The titles and counts below are EVIDENCE THAT A QUESTION IS BEING ASKED.
  They are not source material. When a guide is written from this digest:

    * do NOT quote or paraphrase any post, in any article, ever
    * do NOT name, link to, or describe a Redditor
    * do NOT reproduce post text -- not reworded, not anonymised,
      not "inspired by"
    * do NOT cite Reddit as a source, or write "parents are asking" /
      "one parent said"
    * DO write the article the QUESTION implies, from this repo's datasets

  Every number in a published guide comes from a dataset in this repo. The
  `answerable` line on each cluster names the dataset. A cluster below 1.00 has
  NOT been checked -- check it against SCOPE.md and data/*.csv before writing,
  or drop it.

  A cluster's rank is a claim about DEMAND, never about truth. Reddit is
  routinely wrong about federal loan rules. The digest ranks the question;
  app.py holds the answer."""


def _fmt_component(name: str, value: float, detail: str) -> str:
    return f"    {name:<11} {value:>4.2f}   {detail}"


def render_digest(result: dict, meta: dict, limit: int = 20) -> str:
    """The digest. Every component prints WITH ITS RAW INPUT beside it, so a
    surprising rank can be argued with rather than only accepted."""
    lines = []
    lines.append("REDDIT IDEA DIGEST -- worthmydegree.com guides")
    lines.append(f"run {meta.get('run_at', '?')}  ·  asof {meta.get('asof', '?')}"
                 f"  ·  source {meta.get('source', '?')}"
                 f"  ·  cache fetched {meta.get('fetched_at', '?')}")
    lines.append(f"{len(meta.get('subreddits', []))} subreddits · "
                 f"{result['n_posts']} threads · "
                 f"vocabulary coverage {result['coverage']:.0%}")
    weights = " ".join(f"{k} {v:.2f}" for k, v in SCORE_WEIGHTS.items())
    lines.append(f"weights: {weights}")
    lines.append(f"sim-threshold {result['sim_threshold']} · "
                 f"min-threads {result['min_threads']} · "
                 f"recency half-life {result['halflife']:.0f}d")
    lines.append(f"first-party: {result.get('own_status', 'not read')}")
    if meta.get("stale"):
        lines.append("")
        lines.append(f"  *** STALE -- cache is {meta['cache_age_days']:.0f} days "
                     f"old. Re-run with --refresh. ***")
    lines.append("")
    lines.append(RULES_BLOCK)
    lines.append("")
    lines.append(f"RANKED CLUSTERS  ({min(limit, len(result['ranked']))} of "
                 f"{len(result['ranked'])})")
    lines.append("")
    for cluster in result["ranked"][:limit]:
        head = f"#{cluster['cluster_id']}  {cluster['label']}"
        if len(head) > 55:
            head = head[:54] + "…"
        lines.append(f"{head:<57} IdeaScore {cluster['idea_score']:.1f}")
        if cluster["kind"] == "emergent":
            lines.append("    EMERGENT -- no TOPIC_VOCAB match")
        elif cluster["kind"] == "own":
            lines.append("    FROM YOUR OWN VISITORS -- no Reddit threads behind this")
        else:
            lines.append(f"    vocabulary: {cluster['topic_key']}")
        if cluster["answerable"] >= ANSWERABLE_BACKED:
            detail = f"BACKED BY: {cluster['backing']}"
        elif cluster["answerable"] <= UNCHECKED_ANSWERABLE:
            detail = ("UNCHECKED -- no vocabulary entry. Confirm the datasets "
                      "can back this BEFORE a word is written; if they can, "
                      "add a TOPIC_VOCAB entry.")
        else:
            detail = f"ADJACENT: {cluster['backing']}"
        wrapped = _wrap(detail, 62)
        lines.append(_fmt_component("answerable", cluster["answerable"],
                                    wrapped[0]))
        for chunk in wrapped[1:]:
            lines.append(f"                   {chunk}")
        lines.append(_fmt_component(
            "demand", cluster["demand"],
            f"median {cluster['median_score']:.0f} pts / "
            f"{cluster['median_comments']:.0f} comments"))
        lines.append(_fmt_component(
            "own_demand", cluster["own_demand"],
            cluster["own_demand_note"] or "no first-party signal for this topic"))
        lines.append(_fmt_component(
            "breadth", cluster["breadth"],
            f"{cluster['n_threads']} threads · " + ", ".join(cluster["subs"])))
        lines.append(_fmt_component(
            "recency", cluster["recency"],
            f"median age {cluster['median_age_days']:.0f}d"
            + (f" · {cluster['seasonal']}" if cluster["seasonal"] else "")))
        novelty_detail = f"max sim {cluster['max_guide_sim']:.2f}"
        if cluster["max_guide_sim"] >= NEAR_DUPLICATE_SIM and cluster["nearest_guide"]:
            novelty_detail = (f"COVERED BY {cluster['nearest_guide']} "
                              f"(sim {cluster['max_guide_sim']:.2f}) -- "
                              f"extend, do not repeat")
        elif cluster["nearest_guide"]:
            novelty_detail += f" vs {cluster['nearest_guide']}"
        lines.append(_fmt_component("novelty", cluster["novelty"], novelty_detail))
        lines.append(_fmt_component(
            "specific", cluster["specific"],
            f"{cluster['numeric_share']:.0%} name a figure · "
            f"{cluster['question_share']:.0%} are questions"))
        if cluster.get("overlaps"):
            lines.append(f"    {'overlaps':<11}        {cluster['overlaps']}")
        if cluster["posts"]:
            lines.append("")
            lines.append("    Evidence that this question is being ASKED "
                         "(private; never publishable):")
            top = sorted(cluster["posts"], key=lambda p: -post_engagement(p))[:3]
            entry = TOPIC_VOCAB.get(cluster["topic_key"]) or {}
            for post in top:
                title = (post.get("title") or "")[:58]
                lines.append(f"      r/{post.get('subreddit',''):<18} "
                             f"\"{title}\"  {post.get('score',0)}/"
                             f"{post.get('num_comments',0)}")
                # WHY this thread is here, when the title does not say so.
                # Reddit titles are narrative ("I've failed as a father") while
                # the topic sits in the body ("aspirations to go to med
                # school"), so a correct match reads as a bug. Printing the
                # phrase stops someone -- including a later me -- "fixing" a
                # matcher that is working.
                hit = matched_phrases(post, entry)
                if hit and not any(phrase_pattern(h).search(
                        normalize_plain(post.get("title") or "")) for h in hit):
                    lines.append(f"        └ matched in body: "
                                 f"{', '.join(hit[:2])}")
            if cluster["n_threads"] > 3:
                lines.append(f"      ... {cluster['n_threads'] - 3} more threads")
        lines.append("")

    if result["out_of_scope"]:
        lines.append("OUT OF SCOPE -- do not write (matched CANNOT_ANSWER)")
        lines.append("  Shown with engagement so declining them is a deliberate "
                     "act, not an oversight.")
        for cluster in result["out_of_scope"]:
            lines.append(f"  · {cluster['topic_key']:<28} "
                         f"{cluster['n_threads']} threads · median "
                         f"{cluster['median_score']:.0f} pts / "
                         f"{cluster['median_comments']:.0f} comments")
            for chunk in _wrap(cluster["why_excluded"], 68):
                lines.append(f"      {chunk}")
        lines.append("")

    if result["thin"]:
        lines.append(f"UNMATCHED & THIN -- leads, unranked "
                     f"(below --min-threads {result['min_threads']})")
        lines.append("  Single threads carry no breadth evidence and are "
                     "deliberately unranked. Read them as leads. If two runs a")
        lines.append("  month apart surface the same SHAPE of question here, "
                     "that is a vocabulary gap: add a TOPIC_VOCAB entry and it")
        lines.append("  will cluster next time.")
        for cluster in result["thin"][:15]:
            terms = ", ".join(t for t, _ in cluster["top_terms"]) or "-"
            lines.append(f"  · \"{(cluster['seed_title'] or '')[:56]}\"")
            lines.append(f"      r/{', '.join(cluster['subs'])} · "
                         f"{cluster['median_score']:.0f} pts / "
                         f"{cluster['median_comments']:.0f} comments · "
                         f"unique terms: {terms}")
        lines.append("")

    lines.append("NEXT STEP")
    lines.append("  Pick from RANKED CLUSTERS. At answerable 1.00 the BACKED BY "
                 "line names your sources: open them, take")
    lines.append("  the real figures, write the article. Below 1.00, settle the "
                 "sourcing question first or drop it.")
    lines.append("  Then: content/README.md.")
    return "\n".join(lines)


def _wrap(text: str, width: int) -> list:
    words, lines, current = (text or "").split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines or [""]


def explain(result: dict, cluster_id: int) -> str:
    """The full arithmetic for one cluster. The real defence against "the
    number is opaque", and the debugging tool when a weight edit surprises."""
    match = next((c for c in result["ranked"] if c["cluster_id"] == cluster_id),
                 None)
    if match is None:
        return f"No ranked cluster #{cluster_id}."
    lines = [f"#{cluster_id}  {match['label']}", ""]
    lines.append(f"{'component':<12}{'raw':>26}  {'norm':>6}  {'weight':>7}  "
                 f"{'contributes':>11}")
    raws = {
        "answerable": match["backing"][:24] or "unchecked",
        "demand": f"median engagement {match['median_engagement']:.2f}",
        "own_demand": f"{match['own_demand_raw']:.2f}",
        "breadth": f"{match['n_threads']} threads / {match['n_subs']} subs",
        "recency": f"median age {match['median_age_days']:.0f}d",
        "novelty": f"max guide sim {match['max_guide_sim']:.2f}",
        "specific": f"{match['numeric_share']:.0%} num / "
                    f"{match['question_share']:.0%} q",
    }
    total = 0.0
    for name, weight in SCORE_WEIGHTS.items():
        value = float(match.get(name, 0.0))
        contribution = 100.0 * weight * value
        total += contribution
        lines.append(f"{name:<12}{str(raws[name])[:26]:>26}  {value:>6.3f}  "
                     f"{weight:>7.2f}  {contribution:>11.2f}")
    lines.append(f"{'':<12}{'':>26}  {'':>6}  {'TOTAL':>7}  {total:>11.2f}")
    return "\n".join(lines)


CSV_COLUMNS = (
    "cluster_id", "kind", "topic_key", "label", "idea_score",
    "answerable", "backing", "why_excluded",
    "demand", "median_engagement", "median_score", "median_comments",
    "own_demand", "own_demand_raw", "own_demand_note",
    "breadth", "n_threads", "n_subs", "subs",
    "recency", "median_age_days", "seasonal",
    "novelty", "max_guide_sim", "nearest_guide",
    "specific", "numeric_share", "question_share",
    "top_terms", "seed_title", "seed_permalink", "thread_ids",
)


def write_csv(result: dict, path: str) -> None:
    """One row per cluster, EVERY section included.

    `kind` reconstructs the digest's sections, so the CSV is a superset of what
    was printed -- nothing about a cluster exists only in prose.
    """
    import csv

    everything = (result["ranked"] + result["out_of_scope"] + result["thin"])
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS,
                                extrasaction="ignore")
        writer.writeheader()
        for cluster in everything:
            row = {k: cluster.get(k, "") for k in CSV_COLUMNS}
            row["subs"] = ", ".join(cluster.get("subs", []))
            row["top_terms"] = ", ".join(t for t, _ in cluster.get("top_terms", []))
            # Ids, not titles: the CSV is for tracing a cluster back to its
            # threads, and a title column would be a second place post text
            # lives.
            row["thread_ids"] = " ".join(p.get("id", "")
                                         for p in cluster.get("posts", []))
            for key in ("idea_score", "demand", "own_demand", "breadth",
                        "recency", "novelty", "specific", "answerable",
                        "median_engagement", "median_age_days",
                        "numeric_share", "question_share", "max_guide_sim"):
                if isinstance(row.get(key), float):
                    row[key] = round(row[key], 4)
            writer.writerow(row)


# --- the fixture -----------------------------------------------------------
#
# Synthetic and hand-written, which matters twice: --self-test needs no network
# and no credentials, and no real person's words enter this repo.
FIXTURE_POSTS = [
    # Three phrasings of one question -- must land in ONE cluster.
    ("StudentLoans", "Parent PLUS denied in August, now what?", 318, 96, 40),
    ("financialaid", "Told we could borrow 20k a year, hit a cap senior year", 201, 74, 62),
    ("financialaid", "Is the 65k PLUS limit per parent or per student?", 112, 38, 88),
    # Cost of attendance.
    ("college", "COA is 78k and aid covered 20k. How does anyone pay this?", 240, 88, 30),
    ("ApplyingToCollege", "Sticker price vs net price, what am I missing?", 150, 40, 55),
    ("financialaid", "Cost of attendance went up 6% and my aid didn't", 98, 31, 120),
    # Repayment plans.
    ("StudentLoans", "RAP vs IBR for a 60k balance, which is cheaper?", 410, 150, 20),
    ("StudentLoans", "Income driven repayment: is the payment really capped?", 260, 90, 44),
    ("StudentLoans", "Switching to RAP, can I go back to IBR later?", 180, 61, 70),
    # Out of scope: servicer. Loud on purpose -- the negative control.
    ("StudentLoans", "My servicer lost my paperwork again", 5200, 900, 10),
    ("StudentLoans", "Three hours on hold with the servicer", 4800, 810, 18),
    ("StudentLoans", "Servicer says my payment count reset", 4400, 700, 25),
    # Out of scope: refinance.
    ("StudentLoans", "Best lender to refinance with right now?", 3100, 520, 12),
    ("StudentLoans", "Refi dropped my rate, credit score question", 2900, 480, 33),
    ("StudentLoans", "Is refinancing worth losing federal protections?", 2600, 450, 47),
    # Worth-it / break-even.
    ("ApplyingToCollege", "Is 90k of debt worth it for this major?", 300, 120, 26),
    ("college", "How much debt is too much for a teaching degree?", 220, 95, 51),
    ("ApplyingToCollege", "Worth the debt or go to state school?", 190, 70, 77),
    # Emergent: a topic no vocabulary entry knows.
    ("ApplyingToCollege", "Is a gap year financially stupid if I keep my aid?", 184, 88, 12),
    # Deliberately carries NO vocabulary phrase. It used to say "aid package
    # changed", which the scholarships-and-aid entry then claimed -- correctly,
    # but it pulled the post out of the emergent pass and collapsed the one
    # cluster this fixture exists to prove. A control for the emergent path has
    # to stay out of the vocabulary's reach as the vocabulary grows.
    ("college", "Took a gap year, deferring enrollment, now reapplying", 96, 41, 22),
    ("ApplyingToCollege", "Gap year then reapply, does aid reset?", 88, 30, 35),
    # Singletons -- must land in the unranked leads section.
    ("college", "Anyone else's dining plan a total ripoff?", 140, 60, 15),
    ("financialaid", "Verification selected me for the third year running", 75, 22, 41),
    ("college", "Roommate pays half what I do for the same dorm", 60, 19, 66),
]


# Posts whose BODY carries a topic phrase their title has nothing to do with.
# The original fixture had empty bodies, so it could not see the precision bug
# the first real harvest exposed -- one generic phrase in 500 characters of
# context was enough to file a thread under a topic. These are the control for
# that: each must stay OUT of the topic its body brushes against.
FIXTURE_BODY_TRAPS = [
    ("college", "Are colleges dumbing down their curriculum?",
     "Honestly the workload feels lighter than what my parents describe from "
     "10 years ago, and in 30 years nobody will care about any of this.",
     1326, 229, 24, "roi-horizon"),
    ("college", "Dorm wifi has been down all week",
     "I pay a monthly payment for housing that supposedly includes internet.",
     210, 55, 19, "take-home-pay"),
]


def fixture_posts(asof_ts: float) -> list:
    """The fixture as cache rows, aged relative to asof so recency is stable."""
    rows = []
    for i, (sub, title, score, comments, age_days) in enumerate(FIXTURE_POSTS):
        rows.append({
            "id": f"fix{i:03d}", "subreddit": sub, "title": title,
            "selftext_excerpt": "", "score": score, "num_comments": comments,
            "created_utc": asof_ts - age_days * 86400.0,
            "permalink": f"/r/{sub}/comments/fix{i:03d}/", "flair": None,
            "fetched_at": "1970-01-01T00:00:00Z", "source": "fixture",
        })
    for j, (sub, title, body, score, comments, age_days, _) in enumerate(
            FIXTURE_BODY_TRAPS):
        rows.append({
            "id": f"trap{j:03d}", "subreddit": sub, "title": title,
            "selftext_excerpt": body, "score": score, "num_comments": comments,
            "created_utc": asof_ts - age_days * 86400.0,
            "permalink": f"/r/{sub}/comments/trap{j:03d}/", "flair": None,
            "fetched_at": "1970-01-01T00:00:00Z", "source": "fixture",
        })
    return rows


_CITED_IDENTIFIER_RE = re.compile(r"\b(?:[a-z][a-z0-9]*_[a-z0-9_]+|[A-Z][A-Z0-9]*_[A-Z0-9_]+)\b")
_CITED_PATH_RE = re.compile(r"\bdata/[\w./-]+\.csv\b")

# Words that look like identifiers and are not. Kept explicit rather than
# loosening the pattern, because every entry here is a place the check has been
# deliberately switched off.
_NOT_IDENTIFIERS = frozenset({
    "net_price", "cost_of_attendance", "in_state", "out_of_state",
})


def unresolved_citations() -> list:
    """Backing strings that name a function, constant or dataset app.py and
    data/ do not contain.

    Substring presence in the source is enough: this is asking "can a reader
    open this", not "is it in scope here". A false pass on a name that appears
    in a comment is cheap; a false FAIL would make the check something people
    switch off.
    """
    app_path = Path(__file__).parent / "app.py"
    try:
        source = app_path.read_text()
    except OSError:                                        # pragma: no cover
        return []
    problems = []
    for key, entry in TOPIC_VOCAB.items():
        backing = entry.get("backing") or ""
        for name in set(_CITED_IDENTIFIER_RE.findall(backing)):
            if name in _NOT_IDENTIFIERS:
                continue
            if name not in source:
                problems.append(
                    f"TOPIC_VOCAB[{key!r}] cites {name!r}, which does not "
                    f"appear anywhere in app.py -- a citation nobody can open "
                    f"is inert, not broken: the digest prints it with "
                    f"confidence and the drafting session goes looking for a "
                    f"function that is not there")
        for path in set(_CITED_PATH_RE.findall(backing)):
            if not (Path(__file__).parent / path).exists():
                problems.append(
                    f"TOPIC_VOCAB[{key!r}] cites the dataset {path!r}, which "
                    f"does not exist")
    return problems


def self_test() -> int:
    """The whole pipeline over the fixture, with named assertions.

    Includes the three negative controls: an out-of-scope topic cannot rank
    however loud it is, answerability moves the score by exactly the weight it
    is given, and a near-duplicate is demoted AND says so.
    """
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)

    asof = datetime(2026, 8, 13, tzinfo=timezone.utc).timestamp()
    posts = fixture_posts(asof)
    result = build_clusters(posts, asof)
    ranked = result["ranked"]
    by_key = {c["topic_key"]: c for c in ranked if c["topic_key"]}

    # 1. Three phrasings of one question, one cluster.
    plus = by_key.get("parent-plus-caps")
    check(plus is not None and plus["n_threads"] >= 3,
          "the three Parent PLUS phrasings did not land in one cluster of 3+ "
          f"(got {plus['n_threads'] if plus else 'no cluster'})")

    # 2. NEGATIVE CONTROL (a): the loudest thing in the corpus is out of scope
    #    and must not appear in the ranked list at any engagement.
    out_keys = {c["topic_key"] for c in result["out_of_scope"]}
    check("servicer-complaints" in out_keys,
          "the servicer threads did not match CANNOT_ANSWER")
    check("servicer-complaints" not in by_key,
          "servicer-complaints RANKED -- the CANNOT_ANSWER partition is not "
          "holding, and the loudest threads in the corpus would top the list")
    check("refinance-shopping" not in by_key,
          "refinance-shopping RANKED despite matching CANNOT_ANSWER")
    loudest_ranked = max((c["median_score"] for c in ranked), default=0)
    check(loudest_ranked < 3000,
          f"a ranked cluster has median score {loudest_ranked:.0f} -- the "
          f"out-of-scope threads have leaked into the ranking")

    # 3. An unknown topic clusters as EMERGENT rather than being lost.
    emergent = [c for c in ranked if c["kind"] == "emergent"]
    check(any("gap" in c["label"] for c in emergent),
          "the gap-year threads did not form an emergent cluster "
          f"(emergent labels: {[c['label'] for c in emergent]})")

    # 4. Singletons are leads, not findings.
    thin_titles = " ".join(c["seed_title"] for c in result["thin"])
    check("dining plan" in thin_titles,
          "the lone dining-plan thread is not in the unranked leads section")
    check(all(c["n_threads"] >= MIN_THREADS for c in ranked if c["kind"] != "own"),
          "a cluster below --min-threads reached the ranked list")

    # 4b. NEGATIVE CONTROL (d): a topic phrase buried in a body must not, on
    #     its own, file a thread under that topic. This is the bug the first
    #     real harvest exposed and the empty-bodied fixture could not see.
    hits = match_vocabulary(posts, TOPIC_VOCAB)
    for j, (_, title, _, _, _, _, topic) in enumerate(FIXTURE_BODY_TRAPS):
        trap_index = len(FIXTURE_POSTS) + j
        check(trap_index not in hits.get(topic, []),
              f"\"{title}\" was filed under {topic!r} on a body phrase alone -- "
              f"one generic phrase in 500 characters of context is not evidence "
              f"of a topic, and this is how a cluster silently fills with "
              f"threads about something else")

    # 4c. NEGATIVE CONTROL (e): no first-party evidence must score ZERO on that
    #     component, not the averaged tie-rank of the zeros. Ranking the whole
    #     column handed every unevidenced cluster 0.48 on the real corpus -- a
    #     free half-point for having no evidence, awarded to most of the list.
    seeded = build_clusters(posts, asof, own_signal={
        "parent-plus-caps": {"weight": 12.0, "note": "fixture signal"}})
    with_signal = [c for c in seeded["ranked"] if c["own_demand_raw"] > 0]
    without = [c for c in seeded["ranked"] if c["own_demand_raw"] == 0]
    check(with_signal and all(c["own_demand"] > 0 for c in with_signal),
          "a cluster WITH a first-party signal scored 0 on own_demand")
    check(all(c["own_demand"] == 0.0 for c in without),
          "a cluster with NO first-party evidence scored above 0 on "
          "own_demand -- rank_pct is handing out the tie-rank of the zeros, "
          f"e.g. {[round(c['own_demand'], 2) for c in without][:3]}")

    # 5. NEGATIVE CONTROL (b): answerability moves the score by exactly its
    #    weight. Asserts the ARITHMETIC, so a weight edited without the
    #    docstring following fails here rather than quietly reordering.
    base = {k: 0.5 for k in SCORE_WEIGHTS}
    high = dict(base, answerable=ANSWERABLE_BACKED)
    low = dict(base, answerable=UNCHECKED_ANSWERABLE)
    delta = score_cluster(high) - score_cluster(low)
    expected = 100.0 * SCORE_WEIGHTS["answerable"] * (ANSWERABLE_BACKED
                                                      - UNCHECKED_ANSWERABLE)
    check(abs(delta - expected) < 1e-9,
          f"answerability delta is {delta:.4f}, expected {expected:.4f}")

    # 6. NEGATIVE CONTROL (c): a near-duplicate of a published guide is demoted
    #    AND says so. A silent demotion would be as wrong as none.
    if plus is not None:
        check(plus["max_guide_sim"] >= NEAR_DUPLICATE_SIM,
              f"the Parent PLUS cluster reads sim {plus['max_guide_sim']:.2f} "
              f"against the published parent-plus-senior-year guide -- the "
              f"novelty comparison is not seeing content/posts/")
        check(plus["novelty"] <= 1.0 - NEAR_DUPLICATE_SIM,
              "a near-duplicate of a published guide was not demoted")

    # 7. Determinism. Everything --cache-only and --asof promise rests on this.
    meta = {"run_at": "fixed", "asof": "fixed", "source": "fixture",
            "fetched_at": "fixed", "subreddits": SUBREDDITS}
    first = render_digest(build_clusters(posts, asof), meta)
    second = render_digest(build_clusters(posts, asof), meta)
    check(first == second,
          "two runs over the same posts produced different digests -- "
          "clustering is not deterministic and --asof guarantees nothing")

    # 8. The editorial rule is only enforceable if it travels with the output.
    check("do NOT quote or paraphrase any post" in first,
          "the digest does not carry RULES_BLOCK")
    check("author" not in " ".join(CACHE_FIELDS),
          "CACHE_FIELDS has grown an author field")

    # 9. Every backed claim is a citation, not an assertion.
    for key, entry in TOPIC_VOCAB.items():
        if entry.get("answerable", 0) >= ANSWERABLE_BACKED and not entry.get("backing"):
            failures.append(f"TOPIC_VOCAB[{key!r}] claims answerable 1.0 with "
                            f"no backing -- 'the app can answer this' must "
                            f"always be a citation")
    # 9b. ...and a citation must name something that EXISTS.
    #
    # This check exists because it caught a live one: repayment-plan-choice
    # cited `simulate_idr_schedule`, which app.py has never contained (the
    # function is calculate_idr_repayment). A backing that names a function
    # nobody can open is inert rather than broken -- it reads as a source, the
    # digest prints it with confidence, and the drafting session goes looking
    # for a file that is not there. Same failure as a mapping keyed on an
    # occupation title that does not exist: everything succeeds, and the thing
    # it was supposed to guarantee silently is not true.
    for bad in unresolved_citations():
        failures.append(bad)
    for key, entry in CANNOT_ANSWER.items():
        if not entry.get("why"):
            failures.append(f"CANNOT_ANSWER[{key!r}] has no `why` -- declining "
                            f"a topic must be a cited act")

    print(render_digest(result, meta))
    print()
    if failures:
        print(f"self-test: {len(failures)} failure(s)\n")
        for failure in failures:
            print(f"  {failure}\n")
        return 1
    print(f"self-test OK -- {len(posts)} fixture posts, {len(ranked)} ranked "
          f"cluster(s), {len(result['out_of_scope'])} out of scope, "
          f"{len(result['thin'])} lead(s).")
    print("  Four negative controls: the loudest threads in the corpus are out "
          "of scope and do not rank, answerability")
    print("  moves the score by exactly its weight, a near-duplicate of a "
          "published guide is demoted, and a topic phrase")
    print("  buried in a body does not on its own file a thread under that "
          "topic.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check-auth", action="store_true",
                    help="verify credentials with one cheap call and print the "
                         "field names the source actually returns. Run this "
                         "first; ACTOR_FIELD_MAP is a guess until it has.")
    ap.add_argument("--source", choices=("apify", "api"), default="apify",
                    help="where posts come from (default apify). `api` is the "
                         "official Reddit API and needs Responsible Builder "
                         "approval.")
    ap.add_argument("--self-test", action="store_true",
                    help="run the whole pipeline over a synthetic fixture. No "
                         "network, no credentials, and it carries the negative "
                         "controls -- run it after touching the model.")
    ap.add_argument("--refresh", action="store_true",
                    help="refetch even if the cache is fresh")
    ap.add_argument("--cache-only", action="store_true",
                    help="never touch the network. Fails if there is no cache. "
                         "This is the reproduce-a-run path and the "
                         "no-credentials path.")
    ap.add_argument("--asof", default=None,
                    help="compute recency as if today were this YYYY-MM-DD. "
                         "With --cache-only this makes a run reproducible.")
    ap.add_argument("--max-items", type=int, default=100,
                    help="items per subreddit per window (default 100). This "
                         "is what you are billed on.")
    ap.add_argument("--sim-threshold", type=float, default=SIM_THRESHOLD)
    ap.add_argument("--min-threads", type=int, default=MIN_THREADS,
                    help=f"clusters below this are leads, not findings "
                         f"(default {MIN_THREADS})")
    ap.add_argument("--halflife-days", type=float, default=RECENCY_HALFLIFE_DAYS)
    ap.add_argument("--limit", type=int, default=20,
                    help="ranked clusters to print (default 20)")
    ap.add_argument("--no-first-party", action="store_true",
                    help="skip the Supabase half (zero-result searches, "
                         "scenario_events, guide reads/likes/shares) and rank "
                         "on Reddit alone")
    ap.add_argument("--explain", type=int, default=None, metavar="ID",
                    help="print the full arithmetic for one ranked cluster")
    ap.add_argument("-o", "--output", default=None,
                    help="also write one row per cluster to this CSV")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if args.check_auth:
        if args.source == "apify":
            cfg = load_apify_config()
            if cfg is None:
                sys.exit("No [apify] block in .streamlit/secrets.toml -- see "
                         ".streamlit/secrets.toml.example for the shape.")
            return check_auth_apify(cfg)
        sys.exit("--source api is not implemented yet. Reddit's Responsible "
                 "Builder approval is the blocker, not this code.")

    if args.source == "api":
        sys.exit("--source api is not implemented yet. Use the default "
                 "(--source apify) until Reddit approval lands.")

    now_ts = datetime.now(timezone.utc).timestamp()
    asof_ts = (datetime.strptime(args.asof, "%Y-%m-%d")
               .replace(tzinfo=timezone.utc).timestamp()
               if args.asof else now_ts)

    rows, meta = read_cache()
    age = cache_age_days(meta, now_ts) if rows else float("inf")

    if args.cache_only:
        if not rows:
            sys.exit(f"No cache at {CACHE_PATH} and --cache-only was given. "
                     f"Run once without it to harvest.")
        print(f"Using cache: {len(rows)} posts, {age:.1f} days old.",
              file=sys.stderr)
    elif args.refresh or not rows or age > CACHE_MAX_AGE_DAYS:
        cfg = load_apify_config()
        if cfg is None:
            if rows:
                print(f"No [apify] block in {SECRETS_PATH}; using the "
                      f"{age:.0f}-day-old cache.", file=sys.stderr)
            else:
                sys.exit(f"No [apify] block in {SECRETS_PATH} and no cache at "
                         f"{CACHE_PATH}. See .streamlit/secrets.toml.example, "
                         f"then run --check-auth.")
        else:
            why = ("--refresh" if args.refresh
                   else "no cache" if not rows
                   else f"cache is {age:.0f} days old")
            print(f"Harvesting ({why}) ...", file=sys.stderr)
            rows, meta = harvest_apify(cfg, SUBREDDITS, WINDOWS, args.max_items)
            if not rows:
                sys.exit("Harvest returned nothing. Run --check-auth: an empty "
                         "result is also what a wrong input schema looks like.")
            write_cache(rows, meta)
            age = 0.0
            print(f"  wrote {len(rows)} posts to {CACHE_PATH}", file=sys.stderr)
    else:
        print(f"Using cache: {len(rows)} posts, {age:.1f} days old "
              f"(--refresh to refetch).", file=sys.stderr)

    own_signal, own_status = {}, "skipped (--no-first-party)"
    if not args.no_first_party:
        print("Reading first-party signals ...", file=sys.stderr)
        usage, events = fetch_first_party()
        if usage is None and events is None:
            own_status = ("UNAVAILABLE -- no Supabase connection. The "
                          "own_demand column is absent, not zero.")
        else:
            cip_titles = {}
            try:
                import analyze_traffic
                cip_titles = analyze_traffic.load_app_namespace().get(
                    "CIP_FAMILY_TITLES", {})
            except Exception:
                pass
            own_signal = own_demand_signal(usage, events, cip_titles)
            own_status = (f"{len(own_signal)} topic(s) with a first-party "
                          f"signal, from {0 if usage is None else len(usage):,} "
                          f"usage_logs and "
                          f"{0 if events is None else len(events):,} "
                          f"scenario_events rows")
        print(f"  {own_status}", file=sys.stderr)

    result = build_clusters(rows, asof_ts, own_signal=own_signal,
                            sim_threshold=args.sim_threshold,
                            min_threads=args.min_threads,
                            halflife=args.halflife_days)
    result["own_status"] = own_status

    if args.explain is not None:
        print(explain(result, args.explain))
        return 0

    header = dict(meta or {})
    header.update({
        "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "asof": args.asof or header.get("run_at", "today"),
        "stale": age > CACHE_MAX_AGE_DAYS,
        "cache_age_days": age,
    })
    digest = render_digest(result, header, limit=args.limit)
    print(digest)
    OUTPUT_DIR.mkdir(exist_ok=True)
    DIGEST_PATH.write_text(digest + "\n")
    print(f"\nWrote {DIGEST_PATH}", file=sys.stderr)

    if args.output:
        write_csv(result, args.output)
        print(f"Wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

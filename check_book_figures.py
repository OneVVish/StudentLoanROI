#!/usr/bin/env python3
"""Every fixture-bearing figure in the book reproduces from the tool.

WHY THIS EXISTS. `marketing/book/families.md` is the fixture file: the
chapters quote from it and never restate a figure, and every number in it
was produced by a tool run on stated inputs. Nothing checked that the tool
still produces them. Two review rounds in September 2026 found three
classes of drift, all of them by a reader:

  - The Parent PLUS roads were priced at 8.5%, the calculator's working
    assumption, after the Department published 9.07%. Every road in
    chapter 7 and one line of chapter 1 ran about $9,500 light.
  - Chapter 3 carried Dartmouth's free-tuition line at $125,000 after the
    school moved it to $175,000.
  - The aid ladder's cost lines took a median over every institution in the
    file, so a chapter about paying for a bachelor's degree quoted a figure
    that folded in community colleges.

Only the first class is mechanically checkable, and it is the one this
guard covers: a figure the tool computes, quoted in the fixture file and
in the chapters. The other two are facts about the world and about a
dataset's basis, and they belong to `sources.md` and to a reader.

WHAT IT ASSERTS, per figure:

  1. The tool still produces it. The value is recomputed from app.py's own
     functions on the inputs families.md states.
  2. The fixture file still records the exact figure, so the chapters have
     something to quote from.
  3. Every chapter listed still quotes the same rounding, literally. This
     is what catches a chapter left behind when a fixture moves, which is
     exactly what happened to chapter 1 on 2026-09-08.

COVERAGE IS DERIVED, not declared. families.md marks an exact tool figure
as `(tool: $N)`. Every one of those markers must be claimed by a figure
below or exempted with a reason, so ADDING A TOOL FIGURE TO THE FIXTURE
FILE FAILS THIS GUARD until someone teaches it to reproduce. That is the
`check_repayment_surfaces` discipline: a guard that quietly narrows as the
thing it guards grows is worse than no guard, because the green tick means
less than it did and nothing says so.

THE ROI HALF GOES THROUGH analyze_model.py, NOT THROUGH A THIRD COPY OF
THE KWARGS. A break-even or a premium is only the app's number if it is
computed with the app's baseline: `pre_earnings_years` for the enrollment
years and `baseline_start_age_for` behind them, both of which
analyze_model already assembles and documents, having got them wrong once
and understated every break-even by 15% to 26%. Writing that assembly out
again here would be the second implementation this repo warns about
everywhere else, so the guard imports it. It costs about a second to
build the 836-entry MAJOR_DATA and almost nothing per figure.

Two dimensions still are not covered. Crossover ages are integers rather
than money and this checks money. And the community-college and Compare
Mode fixtures in families.md need a scenario the guard does not
construct. Both are exempted by name.

Rounding rule, two halves. A cited figure like `~$114,400` must sit within
half its own last significant place of the computed value, so the citation
itself says how much slack it claims and a writer who wants a tighter check
quotes more digits. And that place must be no coarser than a tenth of the
value, so `~$1,000` cannot stand in for $1,400: it claims one significant
digit, and one is not a citation. The two rules are separate because a
correct rounding of a small figure is a large fraction of it. $1,164.93 to
the nearest hundred really is $1,200, 3% away, and an error ceiling would
reject the right answer.

Negative controls, all five run on every invocation:
  1. A chapter's citation edited to a stale value must fail, naming the
     chapter. This is the 2026-09-08 chapter 1 defect.
  2. A tool that returns a different number must fail, naming the figure.
  3. A new `(tool: $N)` marker in the fixture file must fail as uncovered.
  4. A citation whose rounding is wrong at its own precision must fail.
  5. A premium the book calls "behind" that turns positive must fail on the
     word, not only the number.
  6. A figure whose exact value is missing from families.md must fail.
"""

import ast
import io
import os
import re
import sys
from contextlib import redirect_stderr
from pathlib import Path

REPO = Path(__file__).resolve().parent
BOOK = REPO / "marketing" / "book"
FIXTURES = BOOK / "families.md"

MIN_SIGNIFICANT_DIGITS = 2


def load():
    """app.py's section 1-2 prefix, plus the section-4 "2m" functions.

    The repayment tool's own functions sit physically inside section 4
    (CLAUDE.md records that numbering wart), so the prefix alone does not
    define them and every repayment guard carries this same second AST
    pass. Defining a function never resolves its globals, so the ones that
    read section-4 names are safe to define here and are simply not called.
    """
    src = (REPO / "app.py").read_text()
    cut = src.index("# 3. PAGE CONFIG")
    ns = {"__name__": "app_book_figures"}
    buf = io.StringIO()
    with redirect_stderr(buf):
        exec(compile(src[:cut], "app.py", "exec"), ns)
        for node in ast.parse(src).body:
            if isinstance(node, ast.FunctionDef) and node.name not in ns:
                exec(compile(ast.Module(body=[node], type_ignores=[]),
                             "app.py", "exec"), ns)
    return ns


# --------------------------------------------------------------------------
# Reading a money figure out of prose.

MONEY = re.compile(r"~?\$([\d,]+(?:\.\d+)?)")


def money_value(text):
    """The number in a citation like `~$114,400`."""
    m = MONEY.fullmatch(text.strip())
    if not m:
        raise ValueError(f"not a money citation: {text!r}")
    return float(m.group(1).replace(",", ""))


def money_step(text):
    """The size of the citation's last significant place.

    `~$114,400` is written to the nearest hundred, so its step is 100 and
    it may sit up to $50 from the truth. `$4,044` is written exactly and
    its step is 1. Derived from the trailing zeros, so the citation itself
    says how much slack it is claiming, and a writer who wants a tighter
    check simply quotes more digits.
    """
    digits = MONEY.fullmatch(text.strip()).group(1).replace(",", "")
    if "." in digits:
        return 10 ** -len(digits.split(".")[1])
    zeros = len(digits) - len(digits.rstrip("0"))
    return 10 ** zeros


# --------------------------------------------------------------------------
# The figures. Each names how it is computed, the exact value families.md
# records, and every file that quotes it.

class Fig:
    """One figure: how to compute it, how the book writes it, and where.

    `behind` is for a premium the model returns NEGATIVE and the prose
    writes as a magnitude, "~$70,300 behind". The check then asserts the
    sign as well as the size, so a path that flips from behind to ahead
    fails here rather than leaving the word "behind" over a positive
    number, which is the failure the wording invites.
    """

    def __init__(self, key, compute, cite, files, exact=None, note="",
                 behind=False):
        self.key = key
        self.compute = compute
        self.cite = cite
        self.files = files
        self.exact = exact
        self.note = note
        self.behind = behind


# Inputs, quoted from families.md so a reader can see the guard and the
# fixture file agree about what was run.
REYES_LOANS_SLOW = [(0, 54), (12, 54), (24, 54), (36, 54)]
REYES_LOANS_AYG = [(0, 2), (12, 14), (24, 26), (36, 38)]
REYES_RATE = 9.07          # published Direct PLUS, July 1 2026 to June 30 2027
REYES_PER_LOAN = 16_250.0

HALL_PRIVATE = [{"balance": 59_000.0, "rate": 11.0, "term_years": 10}]

DANA_FED, DANA_FED_RATE = 13_000.0, 5.5
DANA_PRIV, DANA_PRIV_RATE, DANA_PRIV_TERM = 102_000.0, 11.0, 10
DANA_INCOME = 70_000.0

# The basis families.md states for every ROI figure: national wages, 6.5
# percent, Standard, school years counted.
ROI_RATE = 6.5
ROI_STRATEGY = "Standard 10-Year"
KINDERGARTEN = "Kindergarten Teachers, Except Special Education"


def _reyes(ns, spec):
    loans = [{"balance": REYES_PER_LOAN, "rate": REYES_RATE,
              "disbursed": d, "entry": e, "subsidized": False}
             for d, e in spec]
    rows = ns["compare_existing_loan_plans"](
        0, 0, DANA_INCOME, 0, False, 0.0, False, 0, federal_loans=loans)
    for label, result, *_ in rows:
        if label.startswith("2026 Tiered Standard"):
            return result
    raise AssertionError("no Tiered Standard row in the Reyes comparison")


def _first_year_interest(result):
    """Interest paid in the first twelve months of a fixed schedule."""
    s = result["schedule"]
    return (12 * result["monthly_payment"]
            - (float(s["balance"].iloc[0]) - float(s["balance"].iloc[12])))


def _dana_fed(ns, income):
    rows = ns["compare_existing_loan_plans"](
        DANA_FED, DANA_FED_RATE, income, 0, True, 0.0, False, 0)
    return {label: result for label, result, *_ in rows}


def _restructure(ns, **kw):
    return ns["private_restructure"](HALL_PRIVATE, **kw)


def _offer(ns, rate, term, who="alone", **kw):
    for o in _restructure(ns, offer_rate=rate, offer_term=term, **kw)["offers"]:
        if o["who"] == who:
            return o
    raise AssertionError(f"no {who} offer at {rate}% over {term} years")


_ROI = {}


def roi_layer():
    """app.py's model with MAJOR_DATA built, via analyze_model.

    Memoized: the build reads three CSVs and merges the curated entries and
    the training overlay, which is a second or so, and every ROI figure
    below wants the same namespace.
    """
    if "ns" not in _ROI:
        import analyze_model
        buf = io.StringIO()
        with redirect_stderr(buf):
            _ROI["ns"] = analyze_model.load_model_layer()
            _ROI["mod"] = analyze_model
    return _ROI["ns"], _ROI["mod"]


def breakeven(title):
    """(break-even loan, premium at zero undergraduate loan) for one path."""
    ns, am = roi_layer()
    if title not in ns["MAJOR_DATA"]:
        raise AssertionError(f"{title!r} is not in MAJOR_DATA; the fixture "
                             f"names an occupation the file does not have")
    buf = io.StringIO()
    with redirect_stderr(buf):
        return am.find_breakeven_loan(ns, title, ROI_RATE, ROI_STRATEGY)


def premium(title, loan, years=10):
    """The ten-year premium over a debt-free high school graduate.

    Same baseline assembly as the break-even, which is the point of routing
    through analyze_model rather than calling compute_scenario_results with
    hand-written kwargs.
    """
    ns, am = roi_layer()
    md = ns["MAJOR_DATA"][title]
    py = ns["program_years_for_education"](md.get("typical_education"), title)
    ey = ns["pre_earnings_years"](title, py)
    ba = am.resolve_baseline_start_age(ns, title, py, ey, False)
    kw = {"enrollment_years": ey, "baseline_start_age": ba}
    if years != 10:
        kw["roi_window_years"] = years
    buf = io.StringIO()
    with redirect_stderr(buf):
        return ns["compute_scenario_results"](
            title, float(loan), ROI_RATE, ROI_STRATEGY,
            **kw)["roi_result"]["earnings_premium"]


def sai_at(income):
    """The Student Aid Index for chapter 3's household, at one income.

    Formula A, two parents, four in the household, one in college, nothing
    else supplied. That is the household chapter 3's table is computed for
    and the one families.md records, so the guard states it here rather
    than reading it back off the chapter.

    These are cheap, unlike the ROI figures: the worksheet is a lookup.
    """
    return load()["compute_student_aid_index"](float(income), 4)["sai"]


_LEVELS = {}


def level_medians(level):
    """Median break-even and premium across every occupation at one level.

    Chapter 14 sets the two-year credential against the four-year one, and
    its whole argument is four medians, so these are checked rather than
    exempted. The pass is 225 bisections and about 1.3 seconds, which is
    worth it: a distribution median is exactly the kind of figure that
    moves silently under a dataset refresh and that no reader can spot.

    The break-even median is taken over the paths that HAVE one. A path
    that never breaks even returns None and has no figure to median, which
    is why families.md states the two counts beside the two medians.
    """
    if level not in _LEVELS:
        ns, am = roi_layer()
        buf = io.StringIO()
        with redirect_stderr(buf):
            rows = [am.find_breakeven_loan(ns, t, ROI_RATE, ROI_STRATEGY)
                    for t, v in ns["MAJOR_DATA"].items()
                    if v.get("typical_education") == level]
        be = sorted(r["breakeven_loan"] for r in rows
                    if r["breakeven_loan"] is not None)
        pr = sorted(r["premium_at_zero_debt"] for r in rows)
        _LEVELS[level] = {
            "n": len(rows),
            "breakeven": _median(be),
            "premium": _median(pr),
        }
    return _LEVELS[level]


def _assoc_wage_median(level):
    """Median wage across one education level, from MAJOR_DATA."""
    ns, _ = roi_layer()
    return _median(sorted(v["median_salary"]
                          for v in ns["MAJOR_DATA"].values()
                          if v.get("typical_education") == level))


def _median(xs):
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _wage(title, path="cleaned_careers.csv", col="a_median"):
    """Read a wage straight out of the committed dataset.

    Deliberately NOT through build_major_data: the question here is whether
    the chapter's table matches the file the app ships, and reading the
    file is the independent witness. Going through the app's own loader
    would mostly assert that the loader loads.
    """
    import pandas as pd
    df = pd.read_csv(REPO / path)
    row = df.loc[df["occ_title"] == title]
    if row.empty:
        raise AssertionError(f"{title!r} is not in {path}")
    return float(row.iloc[0][col])


def _metro_wage(title, city):
    import pandas as pd
    df = pd.read_csv(REPO / "data" / "metro_careers_clean.csv")
    row = df.loc[(df["occ_title"] == title) & (df["city"] == city)]
    if row.empty:
        raise AssertionError(f"{title!r} in {city!r} is not in the metro file")
    return float(row.iloc[0]["a_median"])


CH07 = "ch07-the-parents-loan.md"
CH01 = "ch01-two-questions.md"
CH08 = "ch08-private-money.md"
CH12 = "ch12-the-wage-you-will-see.md"
HR_ASSISTANT = "Human Resources Assistants, Except Payroll and Timekeeping"
VET_TECH = "Veterinary Technologists and Technicians"
PRESCHOOL = "Preschool Teachers, Except Special Education"
POLICE = "Police and Sheriff's Patrol Officers"
ASSOC, BACH = "Associate's degree", "Bachelor's degree"
CH03 = "ch03-what-the-formula-expects.md"
CH04 = "ch04-the-price-you-would-pay.md"
CH13 = "ch13-long-roads.md"
CH11 = "ch11-major-or-career.md"
CH14 = "ch14-short-roads.md"
CH17 = "ch17-ride-or-pay.md"
CH18 = "ch18-slashing-interest-priced.md"
CH00 = "ch00-introduction.md"
FIX = "families.md"

# ---- The Reyes family against the Forbes twenty, chapter 4. The prices are
# Scorecard's NPT4x by INCOME BAND, not the NPT4 average the chart draws, and
# band 5 is $110,001 and up. Read from the raw institution file because the
# committed CSV keeps the average only; the file is the same release, which is
# how the debt column was added.
REYES_AGI, REYES_SIZE = 150_000, 4
# THE TWENTY ARE DERIVED, NOT LISTED, and the guard derives them the same way
# the chart does rather than holding a copy. A second hand-typed list is how the
# two come to disagree about which twenty, silently, with both looking right.
#
# They used to be Forbes' twenty. The selection is the book's own now, by a
# published rule: under Feist a factual compilation protects its SELECTION and
# not its facts, and borrowing the selection was the one part that was theirs.
MIN_FAMILIES, TOP_N = 5, 20
_BAND5 = {}


def _top20(d):
    """Bachelor's-granting, 5+ CIP families, the twenty lowest admit rates."""
    # programs_bachl IS A PIPE-DELIMITED STRING, never a count: to_numeric on it
    # is NaN for almost every row and drops 1,870 of the 2,235.
    b = d[d.programs_bachl.notna() & (d.programs_bachl.astype(str).str.strip() != "")].copy()
    b["families"] = (b.programs_bachl.astype(str).str.split("|")
                     .apply(lambda x: len([i for i in x if i.strip()])))
    return (b.dropna(subset=["ADM_RATE"])
             .query("families >= @MIN_FAMILIES")
             .nsmallest(TOP_N, "ADM_RATE"))


def _band5(name=None):
    """Four years at band 5 for one school, or the median across the twenty."""
    if not _BAND5:
        # The COMMITTED file, not the 100MB raw download, which is gitignored
        # and would have failed this guard on CI and on any clone. The bands
        # are in the cleaned dataset for exactly that reason.
        import pandas as pd
        d = pd.read_csv(REPO / "data/college_coa_clean.csv",
                        usecols=["INSTNM", "net_price_110_plus", "ADM_RATE",
                                 "programs_bachl"], low_memory=False)
        d = _top20(d)
        _BAND5.update(dict(zip(d.INSTNM, d.net_price_110_plus * 4)))
        _BAND5["__median__"] = float(d.net_price_110_plus.median() * 4)
    return _BAND5["__median__"] if name is None else _BAND5[name]


_CA = {}


def _ca(name=None):
    """Four years at band 5 for a California public, or the median of them.

    A SECOND READER AND NOT _band5, because that one is scoped to the twenty
    and every school here is deliberately outside it. Same breadth floor as the
    twenty so the two tables in chapter 4 compare like with like, and the
    community colleges that award one bachelor's cannot pass as universities.
    """
    if not _CA:
        import pandas as pd
        d = pd.read_csv(REPO / "data/college_coa_clean.csv",
                        usecols=["INSTNM", "STABBR", "control_type",
                                 "net_price_110_plus", "programs_bachl"],
                        low_memory=False)
        b = d[d.programs_bachl.notna() & (d.programs_bachl.astype(str).str.strip() != "")].copy()
        b["families"] = (b.programs_bachl.astype(str).str.split("|")
                         .apply(lambda x: len([i for i in x if i.strip()])))
        ca = b[(b.STABBR == "CA") & (b.control_type == "Public")
               & (b.families >= MIN_FAMILIES)].dropna(subset=["net_price_110_plus"])
        _CA.update(dict(zip(ca.INSTNM, ca.net_price_110_plus * 4)))
        _CA["__median__"] = float(ca.net_price_110_plus.median() * 4)
    return _CA["__median__"] if name is None else _CA[name]


def _reyes_sai(ns):
    r = ns["compute_student_aid_index"](REYES_AGI, REYES_SIZE, two_parents=True)
    return r["sai"] if isinstance(r, dict) else r


SOCIAL_WORKER_WAGE = 48270.0     # Child, Family, and School Social Workers
HS_AT_22 = 36182.0               # HS_GRAD_SALARY * hs_age_factor(22)
TAKE_HOME_STATE = "OH"           # ch11 already uses Columbus as the ordinary market


def _monthly_net(ns, salary):
    return ns["calculate_take_home_pay"](salary, TAKE_HOME_STATE)["net_take_home"] / 12


FIGURES = [
    # ---- Chapter 0's school social worker, run monthly before it is run over
    # the decade. The point of the passage is that these five all say the
    # degree is ahead while the ten-year premium says it finishes ~$30,400
    # behind, so they have to be right or the contrast is not a contrast.
    Fig("ch00-sw-take-home", lambda ns: _monthly_net(ns, SOCIAL_WORKER_WAGE),
        "~$3,350", [FIX, CH00], exact="$3,346"),
    Fig("ch00-hs-take-home", lambda ns: _monthly_net(ns, HS_AT_22),
        "~$2,560", [FIX, CH00], exact="$2,564.80"),
    # THE TERM COMES FROM THE LAW, NOT FROM A CHOICE, and reading it from
    # calculate_tiered_standard_term rather than typing 15 is what stops this
    # silently reverting to the ten-year Standard plan that 34 CFR 685.208(b)
    # closed to any loan made on or after July 1, 2026. The first draft of
    # chapter 0 priced that closed plan at $305 and contradicted chapter 6.
    Fig("ch00-student-cap-payment",
        lambda ns: ns["calculate_standard_repayment"](
            27000, 6.5, ns["calculate_tiered_standard_term"](27000))["monthly_payment"],
        "$235", [FIX, CH00], exact="$235.20"),
    # ---- Chapter 4's Reyes case against the twenty.
    Fig("reyes-sai-year", lambda ns: _reyes_sai(ns), "~$25,200", [FIX, CH04], exact="$25,231"),
    Fig("reyes-sai-four", lambda ns: _reyes_sai(ns) * 4, "~$100,900", [FIX, CH04], exact="$100,924"),
    Fig("reyes-ucla", lambda ns: _ca("University of California-Los Angeles"),
        "~$118,700", [FIX, CH04], exact="$118,728"),
    Fig("reyes-berkeley", lambda ns: _ca("University of California-Berkeley"),
        "~$138,100", [FIX, CH04], exact="$138,116"),
    Fig("reyes-princeton", lambda ns: _band5("Princeton University"),
        "~$144,400", [FIX, CH04], exact="$144,376"),
    Fig("reyes-bowdoin", lambda ns: _band5("Bowdoin College"),
        "~$140,800", [FIX, CH04], exact="$140,784"),
    Fig("reyes-penn", lambda ns: _band5("University of Pennsylvania"),
        "~$223,900", [FIX, CH04], exact="$223,888"),
    # ---- Her own state, which is a different table and a different question.
    Fig("reyes-ca-long-beach", lambda ns: _ca("California State University-Long Beach"),
        "~$79,000", [FIX, CH04], exact="$79,000"),
    Fig("reyes-ca-median", lambda ns: _ca(),
        "~$90,100", [FIX, CH04], exact="$90,092"),
    Fig("reyes-ca-sdsu", lambda ns: _ca("San Diego State University"),
        "~$95,300", [FIX, CH04], exact="$95,284"),
    Fig("reyes-ca-calpoly",
        lambda ns: _ca("California Polytechnic State University-San Luis Obispo"),
        "~$111,100", [FIX, CH04], exact="$111,088"),
    Fig("reyes-top20-median", lambda ns: _band5(),
        "~$192,400", [FIX, CH04], exact="$192,350"),
    Fig("reyes-duke", lambda ns: _band5("Duke University"),
        "~$216,900", [FIX, CH04], exact="$216,920"),
    # ---- The Reyes family's three Parent PLUS roads. Every one of these
    # moved on 2026-09-08 when the published rate replaced the assumption,
    # and chapter 1 was left behind by the first pass.
    Fig("reyes-slow-monthly",
        lambda ns: _reyes(ns, REYES_LOANS_SLOW)["monthly_payment"],
        "~$750", [FIX, CH07]),
    Fig("reyes-slow-interest",
        lambda ns: _reyes(ns, REYES_LOANS_SLOW)["total_interest"],
        "~$114,400", [FIX, CH07, CH01], exact="$114,443"),
    Fig("reyes-ayg-interest",
        lambda ns: _reyes(ns, REYES_LOANS_AYG)["total_interest"],
        "~$56,400", [FIX, CH07], exact="$56,427"),
    Fig("reyes-deferment-accrual",
        lambda ns: sum(REYES_PER_LOAN * (REYES_RATE / 100) * m / 12
                       for m in (54, 42, 30, 18)),
        "~$17,700", [FIX, CH07],
        note="simple interest from each fall's disbursement to entry"),
    Fig("reyes-fast-per-loan-monthly",
        lambda ns: ns["calculate_standard_repayment"](
            REYES_PER_LOAN, REYES_RATE, 10)["monthly_payment"],
        "~$206", [FIX, CH07], exact="$206.46"),
    Fig("reyes-fast-peak-monthly",
        lambda ns: 4 * ns["calculate_standard_repayment"](
            REYES_PER_LOAN, REYES_RATE, 10)["monthly_payment"],
        "~$826", [FIX, CH07],
        note="all four loans paying at once in the senior year"),
    Fig("reyes-fast-interest",
        lambda ns: 4 * ns["calculate_standard_repayment"](
            REYES_PER_LOAN, REYES_RATE, 10)["total_interest"],
        "~$34,100", [FIX, CH07, CH01], exact="$8,526",
        note="four loans each paid at the ten-year pace from its own "
             "disbursement; the exact figure recorded is the per-loan one"),

    # ---- The Hall family's private loan and the restructure offers.
    Fig("hall-monthly",
        lambda ns: ns["calculate_standard_repayment"](
            59_000.0, 11.0, 10)["monthly_payment"],
        "~$810", [FIX, CH08], exact="$813"),
    Fig("hall-first-payment-interest",
        lambda ns: 59_000.0 * 0.11 / 12,
        "~$540", [FIX, CH08], exact="$541"),
    Fig("hall-total-interest",
        lambda ns: ns["calculate_standard_repayment"](
            59_000.0, 11.0, 10)["total_interest"],
        "~$38,500", [FIX, CH08], exact="$38,527"),
    Fig("hall-refi-7-over-15-interest",
        lambda ns: _offer(ns, 7.0, 15)["total_interest"],
        "~$36,500", [FIX, CH08], exact="$36,456"),
    Fig("hall-refi-5-cosigner-interest",
        lambda ns: _offer(ns, 7.0, 15, who="with a cosigner",
                          cosigner_rate=5.0)["total_interest"],
        "~$25,000", [FIX, CH08], exact="$24,982"),
    Fig("hall-cosigner-worth",
        lambda ns: (_offer(ns, 7.0, 15)["total_interest"]
                    - _offer(ns, 7.0, 15, who="with a cosigner",
                             cosigner_rate=5.0)["total_interest"]),
        "~$11,500", [FIX, CH08], exact="$11,473"),
    Fig("hall-interest-only-adds",
        lambda ns: _restructure(ns, io_months=12)["interest_only"]["interest_change"],
        "~$2,200", [FIX, CH08], exact="$2,159"),
    Fig("hall-refi-7-over-10-interest",
        lambda ns: _offer(ns, 7.0, 10)["total_interest"],
        "~$23,200", [FIX], exact="$23,205"),

    # ---- Dana Whitfield, where the federal side is the small one.
    Fig("dana-standard-interest",
        lambda ns: _dana_fed(ns, DANA_INCOME)["Standard (10-year)"]["total_interest"],
        "~$3,900", [FIX], exact="$3,930"),
    Fig("dana-standard-monthly",
        lambda ns: _dana_fed(ns, DANA_INCOME)["Standard (10-year)"]["monthly_payment"],
        "~$141", [FIX, CH17, CH18]),
    Fig("dana-rap-70k-interest",
        lambda ns: _dana_fed(ns, DANA_INCOME)[
            ns["RAP_STRATEGY_LABEL"]]["total_interest"],
        "~$1,200", [FIX], exact="$1,165"),
    Fig("dana-rap-70k-monthly",
        lambda ns: float(_dana_fed(ns, DANA_INCOME)[
            ns["RAP_STRATEGY_LABEL"]]["schedule"]["payment"].iloc[0]),
        "~$350", [FIX, CH17, CH18]),
    Fig("dana-private-required",
        lambda ns: ns["calculate_standard_repayment"](
            DANA_PRIV, DANA_PRIV_RATE, DANA_PRIV_TERM)["monthly_payment"],
        "~$1,405", [FIX], exact="$1,405"),
    Fig("dana-year-one-federal-interest",
        lambda ns: _first_year_interest(
            _dana_fed(ns, DANA_INCOME)["Standard (10-year)"]),
        "$685", [FIX],
        note="the schedule carries balances rather than interest, so this "
             "is twelve payments less the year's principal reduction; the "
             "opening balance times the rate is $715 and is the wrong "
             "figure, because the balance falls all year"),
    Fig("dana-year-one-private-interest",
        lambda ns: _first_year_interest(ns["calculate_standard_repayment"](
            DANA_PRIV, DANA_PRIV_RATE, DANA_PRIV_TERM)),
        "$10,872", [FIX]),

    # ---- Chapter 12's wages and take-home, read against the shipped data.
    Fig("nurse-national-wage",
        lambda ns: _wage("Registered Nurses"),
        "$97,550", [FIX]),
    Fig("nurse-sf-wage",
        lambda ns: _metro_wage("Registered Nurses", "San Francisco, CA"),
        "$186,610", [FIX]),
    Fig("nurse-columbus-wage",
        lambda ns: _metro_wage("Registered Nurses", "Columbus, OH"),
        "$83,900", [FIX]),
    Fig("nurse-sf-take-home",
        lambda ns: ns["calculate_take_home_pay"](186_610.0, "CA")["net_take_home"],
        "$125,744", [FIX]),
    Fig("nurse-columbus-take-home",
        lambda ns: ns["calculate_take_home_pay"](83_900.0, "OH")["net_take_home"],
        "$65,592", [FIX]),
    Fig("dev-seattle-take-home",
        lambda ns: ns["calculate_take_home_pay"](167_280.0, "WA")["net_take_home"],
        "$124,797", [FIX]),
    Fig("dev-cleveland-take-home",
        lambda ns: ns["calculate_take_home_pay"](111_310.0, "OH")["net_take_home"],
        "$84,036", [FIX],
        note="chapter 12 rounds this to ~$84,100; it read ~$84,000 until "
             "2026-09-08"),

    # ---- The ROI half: Sofia's path and the three professional cases.
    Fig("kindergarten-starting-wage",
        lambda ns: roi_layer()[0]["MAJOR_DATA"][KINDERGARTEN]["starting_salary"],
        "$52,180", [FIX], exact="$52,180"),
    Fig("kindergarten-breakeven",
        lambda ns: breakeven(KINDERGARTEN)["breakeven_loan"],
        "$4,044", [FIX], exact="$4,044",
        note="the number chapter 11 turns on"),
    Fig("kindergarten-premium-at-zero",
        lambda ns: breakeven(KINDERGARTEN)["premium_at_zero_debt"],
        "~$5,500", [FIX], exact="$5,511"),
    Fig("medicine-premium-at-zero",
        lambda ns: breakeven("Family Medicine Physicians")["premium_at_zero_debt"],
        "~$70,300", [FIX], exact="$70,306", behind=True),
    Fig("dentistry-premium-at-zero",
        lambda ns: breakeven("Dentists, General")["premium_at_zero_debt"],
        "~$35,000", [FIX], exact="$34,647", behind=True),
    Fig("law-premium-at-zero",
        lambda ns: breakeven("Lawyers")["premium_at_zero_debt"],
        "~$115,300", [FIX], exact="$115,324"),
    Fig("law-clerk-premium-at-zero",
        lambda ns: breakeven("Judicial Law Clerks")["premium_at_zero_debt"],
        "~$316,000", [FIX], exact="$316,191", behind=True,
        note="the counterweight case: the same degree into a $52,870 job"),
    # Chapter 13's first table, at the $13,000 undergraduate loan it states.
    Fig("ch13-medicine-premium",
        lambda ns: premium("Family Medicine Physicians", 13_000),
        "~$83,700", [CH13], exact="$83,697", behind=True),
    Fig("ch13-dentistry-premium",
        lambda ns: premium("Dentists, General", 13_000),
        "~$48,000", [CH13], exact="$48,039", behind=True),
    Fig("ch13-law-premium",
        lambda ns: premium("Lawyers", 13_000),
        "~$100,500", [CH13], exact="$100,506"),

    # ---- Chapter 14: the two-year credential against the four-year one.
    # The four medians are the chapter's argument, so they are computed
    # rather than exempted; the pass costs about 1.3 seconds.
    Fig("ch14-associate-median-breakeven",
        lambda ns: level_medians(ASSOC)["breakeven"],
        "~$102,200", [FIX, CH14, CH11], exact="$102,172.86",
        note="median over the 44 of 48 that have a break-even at all. CH11 was "
             "added 2026-09-10 when chapter 11 began setting this figure beside "
             "Theo's three majors: a figure quoted in a second chapter is a "
             "second place for it to go stale."),
    Fig("ch14-bachelor-median-breakeven",
        lambda ns: level_medians(BACH)["breakeven"],
        "~$190,700", [FIX, CH14], exact="$190,658.57",
        note="median over the 147 of 177 that have one"),
    Fig("ch14-associate-median-premium",
        lambda ns: level_medians(ASSOC)["premium"],
        "~$126,300", [FIX, CH14], exact="$126,335.68"),
    Fig("ch14-bachelor-median-premium",
        lambda ns: level_medians(BACH)["premium"],
        "~$177,800", [FIX, CH14, CH00], exact="$177,760.64"),
    Fig("ch14-associate-median-wage",
        lambda ns: _assoc_wage_median(ASSOC),
        "~$67,000", [FIX, CH14], exact="$66,985"),
    Fig("ch14-bachelor-median-wage",
        lambda ns: _assoc_wage_median(BACH),
        "~$83,700", [FIX, CH14], exact="$83,680"),

    # The named rows. Wages come out of MAJOR_DATA rather than the CSV so
    # the curated entries and the overlay are applied, which is the wage
    # the chapter's own break-even was computed from.
    Fig("ch14-atc-breakeven",
        lambda ns: breakeven("Air Traffic Controllers")["breakeven_loan"],
        "~$557,600", [FIX, CH14], exact="$557,632.45"),
    Fig("ch14-atc-wage",
        lambda ns: roi_layer()[0]["MAJOR_DATA"]["Air Traffic Controllers"]["median_salary"],
        "$148,080", [FIX, CH14], exact="$148,080"),
    Fig("ch14-hygienist-breakeven",
        lambda ns: breakeven("Dental Hygienists")["breakeven_loan"],
        "~$315,800", [FIX, CH14], exact="$315,750.12"),
    Fig("ch14-hygienist-wage",
        lambda ns: roi_layer()[0]["MAJOR_DATA"]["Dental Hygienists"]["median_salary"],
        "$98,100", [FIX, CH14], exact="$98,100"),
    Fig("ch14-respiratory-breakeven",
        lambda ns: breakeven("Respiratory Therapists")["breakeven_loan"],
        "~$227,900", [FIX, CH14], exact="$227,920.53"),
    Fig("ch14-respiratory-wage",
        lambda ns: roi_layer()[0]["MAJOR_DATA"]["Respiratory Therapists"]["median_salary"],
        "$82,280", [FIX, CH14], exact="$82,280"),
    Fig("ch14-paralegal-breakeven",
        lambda ns: breakeven("Paralegals and Legal Assistants")["breakeven_loan"],
        "~$65,100", [FIX, CH14], exact="$65,078.74"),
    Fig("ch14-paralegal-wage",
        lambda ns: roi_layer()[0]["MAJOR_DATA"]["Paralegals and Legal Assistants"]["median_salary"],
        "$62,890", [FIX, CH14], exact="$62,890"),
    Fig("ch14-hr-assistant-breakeven",
        lambda ns: breakeven(HR_ASSISTANT)["breakeven_loan"],
        "$168", [FIX, CH14], exact="$167.85",
        note="the tie: over ten years $168 is a rounding, and the chapter "
             "says so rather than counting it as a win"),
    Fig("ch14-hr-assistant-wage",
        lambda ns: roi_layer()[0]["MAJOR_DATA"][HR_ASSISTANT]["median_salary"],
        "$50,610", [FIX, CH14], exact="$50,610"),
    Fig("ch14-agtech-wage",
        lambda ns: roi_layer()[0]["MAJOR_DATA"]["Agricultural Technicians"]["median_salary"],
        "$49,630", [FIX, CH14], exact="$49,630"),
    Fig("ch14-vettech-wage",
        lambda ns: roi_layer()[0]["MAJOR_DATA"][VET_TECH]["median_salary"],
        "$47,380", [FIX, CH14], exact="$47,380"),
    Fig("ch14-preschool-wage",
        lambda ns: roi_layer()[0]["MAJOR_DATA"][PRESCHOOL]["median_salary"],
        "$38,140", [FIX, CH14], exact="$38,140"),
    Fig("ch14-dietetic-wage",
        lambda ns: roi_layer()[0]["MAJOR_DATA"]["Dietetic Technicians"]["median_salary"],
        "$37,640", [FIX, CH14], exact="$37,640"),

    # The counterweight, national on purpose: every other figure in the
    # chapter is national, and a one-row basis switch to California is the
    # error check_chart_basis exists to catch.
    Fig("ch14-police-wage",
        lambda ns: roi_layer()[0]["MAJOR_DATA"][POLICE]["median_salary"],
        "$76,210", [FIX, CH14], exact="$76,210"),
    Fig("ch14-firefighter-wage",
        lambda ns: roi_layer()[0]["MAJOR_DATA"]["Firefighters"]["median_salary"],
        "$59,280", [FIX, CH14], exact="$59,280"),

    # ---- Chapter 3: the aid ladder, and the band where need-based aid ends.
    # Rounded to the nearest hundred, which is what the published guide
    # already used and what the minimum-significant-digits rule requires:
    # ~$8,000 for $8,396 is a one-digit citation and is refused.
    Fig("sai-75k", lambda ns: sai_at(75_000), "~$3,300", [FIX, CH03], exact="$3,284"),
    Fig("sai-100k", lambda ns: sai_at(100_000), "~$8,400", [FIX, CH03], exact="$8,396",
        note="the book said ~$8,000 and the refusing-to-pay guide said ~$8,400 "
             "until 2026-09-09; one figure, two surfaces, two roundings"),
    Fig("sai-150k", lambda ns: sai_at(150_000), "~$25,200", [FIX, CH03], exact="$25,231",
        note="the Reyes household's own number, and the edge of the band"),
    Fig("sai-200k", lambda ns: sai_at(200_000), "~$41,800", [FIX, CH03], exact="$41,764"),
    Fig("sai-250k", lambda ns: sai_at(250_000), "~$58,200", [FIX, CH03], exact="$58,185"),
    Fig("sai-250k-with-assets",
        lambda ns: load()["compute_student_aid_index"](
            250_000.0, 4, parent_assets=150_000.0)["sai"],
        "~$66,600", [FIX, CH03], exact="$66,645",
        note="the $0 asset protection allowance, which is the most common "
             "reason a household lands above an income-only estimate"),
    Fig("sai-150k-two-children", lambda ns: 2 * sai_at(150_000),
        "~$50,500", [FIX, CH03], exact="$50,462",
        note="the 2024-25 FAFSA stopped dividing the parent contribution "
             "between siblings, so it is the figure twice, not split"),
    Fig("sai-200k-two-children", lambda ns: 2 * sai_at(200_000),
        "~$83,500", [FIX, CH03], exact="$83,528"),

    # ---- The baseline every case is measured against.
    Fig("hs-grad-salary",
        lambda ns: ns["HS_GRAD_SALARY"],
        "$51,688", [FIX]),
]

# A `(tool: $N)` marker in families.md that no figure above reproduces.
# Each needs a reason, and the reason is the record of what is not checked.
TOOL_MARKER_EXEMPT = {
    "$3,814, 77 against 79": "avalanche against snowball, which families.md "
                             "itself records as an offline simulation on the "
                             "tool's payments rather than a tool call",
    "$3,048, 58 against 59": "offline simulation, as above",
    "$2,788, 77 against 79": "offline simulation, as above",
    "$1,747, 58 against 58": "offline simulation, as above",
    "$38,527 against $23,205": "both halves are checked individually above",
    "federal $685 + private $10,872 = $11,558": "both halves are checked "
                                                "individually above",
}

TOOL_MARKER = re.compile(r"\(tool: ([^)]*)\)")


# --------------------------------------------------------------------------
# The checks.

def check_figures(ns, chapters) -> list:
    """Each figure reproduces, is recorded exactly, and is quoted alike."""
    problems = []
    for fig in FIGURES:
        try:
            got = float(fig.compute(ns))
        except Exception as exc:                      # noqa: BLE001
            problems.append(f"  {fig.key}: could not be computed ({exc})")
            continue
        if fig.behind:
            if got >= 0:
                problems.append(
                    f"  {fig.key}: the book calls this figure \"behind\" and "
                    f"the model now returns ${got:,.2f}, which is ahead. The "
                    f"word is wrong, not only the number.")
                continue
            got = -got
        want = money_value(fig.cite)
        step = money_step(fig.cite)
        if abs(got - want) > step / 2:
            problems.append(
                f"  {fig.key}: the tool says ${got:,.2f} and the book says "
                f"{fig.cite}, which is more than half of its own last place "
                f"(${step / 2:,.2f}) away. Re-run the fixture and move every "
                f"file that quotes it: {', '.join(fig.files)}")
            continue
        if got and step > abs(got) / 10 ** (MIN_SIGNIFICANT_DIGITS - 1):
            problems.append(
                f"  {fig.key}: {fig.cite} is written to the nearest "
                f"${step:,.0f} on a figure of ${got:,.2f}, which is fewer "
                f"than {MIN_SIGNIFICANT_DIGITS} significant digits. Quote "
                f"another digit.")
        for name in fig.files:
            if fig.cite not in chapters[name]:
                problems.append(
                    f"  {fig.key}: {name} does not contain {fig.cite}. Either "
                    f"it quotes a stale value, or it stopped quoting this "
                    f"figure and should come off the list.")
        if fig.exact and fig.exact not in chapters[FIX]:
            problems.append(
                f"  {fig.key}: families.md no longer records the exact "
                f"{fig.exact}, so the chapters have nothing to round from.")
    return problems


def check_marker_coverage(chapters) -> list:
    """Every exact tool figure in the fixture file is checked or excused."""
    problems = []
    claimed = {f.exact for f in FIGURES if f.exact}
    for marker in TOOL_MARKER.findall(chapters[FIX]):
        marker = marker.strip()
        if marker in claimed or marker in TOOL_MARKER_EXEMPT:
            continue
        problems.append(
            f"  families.md records (tool: {marker}) and nothing reproduces "
            f"it. Add a figure that computes it, or an entry in "
            f"TOOL_MARKER_EXEMPT saying why it cannot be.")
    live = {m.strip() for m in TOOL_MARKER.findall(chapters[FIX])}
    for stale in sorted(set(TOOL_MARKER_EXEMPT) - live - claimed):
        problems.append(
            f"  TOOL_MARKER_EXEMPT excuses (tool: {stale}), which families.md "
            f"no longer contains. Drop the exemption; a stale one hides the "
            f"next figure that needs it.")
    return problems


def read_chapters() -> dict:
    return {p.name: p.read_text() for p in BOOK.glob("*.md")}


# --------------------------------------------------------------------------
# Negative controls. All five run on every invocation, and each asserts the
# edit it makes actually landed, because a control that silently did not
# apply reads exactly like a control that passed.

def negative_controls(ns) -> list:
    problems = []
    base = read_chapters()

    def must_fail(label, chapters, extra_ns=None, marker_only=False):
        found = (check_marker_coverage(chapters) if marker_only
                 else check_figures(extra_ns or ns, chapters))
        if not found:
            problems.append(f"  negative control did not fire: {label}")
        return found

    # 1. A chapter left behind on a superseded value.
    stale = dict(base)
    before = stale[CH01]
    stale[CH01] = before.replace("~$114,400", "~$104,900")
    if stale[CH01] == before:
        problems.append("  control 1 did not apply: chapter 1 no longer "
                        "carries ~$114,400, so this control proves nothing")
    else:
        found = must_fail("a chapter quoting a superseded value", stale)
        if found and not any(CH01 in f for f in found):
            problems.append("  control 1 fired without naming chapter 1")

    # 2. The tool itself returning something else.
    moved = dict(ns)
    real = ns["calculate_standard_repayment"]
    moved["calculate_standard_repayment"] = (
        lambda *a, **k: {**real(*a, **k), "total_interest": 1.0,
                         "monthly_payment": 1.0})
    found = must_fail("the tool returning a different number", base, moved)
    if found and not any("hall-total-interest" in f for f in found):
        problems.append("  control 2 fired without naming the Hall figure")

    # 3. An uncovered tool marker.
    added = dict(base)
    added[FIX] = base[FIX] + "\n\nA new figure (tool: $1,234,567).\n"
    must_fail("a tool marker nothing reproduces", added, marker_only=True)

    # 4. A citation rounded wrongly at its own precision.
    class Wrong:
        pass
    wrong = Fig("control-rounding", lambda ns: 1000.0, "$900", [FIX])
    FIGURES.append(wrong)
    try:
        found = check_figures(ns, base)
        if not any("control-rounding" in f for f in found):
            problems.append("  control 4 did not fire: a citation $100 off a "
                            "$1,000 figure was accepted")
    finally:
        FIGURES.remove(wrong)

    # 5. A "behind" figure that has turned positive. The prose writes these
    # as magnitudes, so without the sign check the word "behind" would sit
    # over an ahead number and every size test would still pass.
    flipped = Fig("control-behind", lambda ns: 70_306.0, "~$70,300", [FIX],
                  behind=True)
    FIGURES.append(flipped)
    try:
        found = check_figures(ns, base)
        if not any("control-behind" in f and "ahead" in f for f in found):
            problems.append("  control 6 did not fire: a premium that turned "
                            "positive was accepted under a 'behind' citation")
    finally:
        FIGURES.remove(flipped)

    # 6. An exact value missing from the fixture file.
    gone = dict(base)
    before = gone[FIX]
    gone[FIX] = before.replace("$38,527", "$00,000")
    if gone[FIX] == before:
        problems.append("  control 5 did not apply: families.md no longer "
                        "records $38,527")
    else:
        found = must_fail("an exact figure missing from families.md", gone)
        if found and not any("families.md no longer records" in f
                             for f in found):
            problems.append("  control 5 fired for the wrong reason")

    return problems


def main() -> int:
    # RUN FROM THE REPO, WHATEVER DIRECTORY THIS WAS INVOKED IN. app.py
    # resolves its datasets through relative paths (CAREERS_CSV_PATH_NATIONAL
    # is "cleaned_careers.csv", and the professional debt and tuition files
    # are the same shape), so from anywhere else MAJOR_DATA builds without
    # its occupations and the professional debt resolves to something else
    # entirely. Run from marketing/, chapter 13's lawyer read -$61,174
    # against the +$100,506 it reads here, with no error anywhere. That
    # matters because marketing/ is the workspace a co-editor opens: the
    # parent repository gitignores it, so Cursor will not index the
    # manuscript from the repo root.
    os.chdir(REPO)
    if not BOOK.exists():
        print("SKIP check_book_figures: marketing/book is not in this "
              "checkout (marketing/ is gitignored and mirrored privately). "
              "Nothing was checked.")
        return 0

    ns = load()
    chapters = read_chapters()

    problems = []
    problems += check_figures(ns, chapters)
    problems += check_marker_coverage(chapters)
    problems += negative_controls(ns)

    checked = len(FIGURES) + len(TOOL_MARKER.findall(chapters[FIX])) + 6
    if problems:
        print("check_book_figures FAILED\n")
        print("\n".join(problems))
        return 1
    print(f"check_book_figures OK: {len(FIGURES)} figures reproduce from the "
          f"tool and are quoted alike across {len(chapters)} files; "
          f"{len(TOOL_MARKER.findall(chapters[FIX]))} tool markers covered or "
          f"excused; 6 negative controls fired. {checked} checks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

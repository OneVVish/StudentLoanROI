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

WHAT IT DOES NOT COVER, and this is the honest limit. The ROI half of the
book, break-even loans and ten-year premiums and crossover ages, needs a
full `build_major_data` and the chapter's own baseline kwargs, which is a
setup this guard does not do yet. Those figures are exempted BY NAME in
TOOL_MARKER_EXEMPT so the gap is visible rather than assumed away, and
extending the guard to them is the next piece of work. `analyze_model.py`
and `check_timeline_alignment.py` are what stand behind them today.

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
  5. A figure whose exact value is missing from families.md must fail.
"""

import ast
import io
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
    def __init__(self, key, compute, cite, files, exact=None, note=""):
        self.key = key
        self.compute = compute
        self.cite = cite
        self.files = files
        self.exact = exact
        self.note = note


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
CH16 = "ch16-ride-or-pay.md"
FIX = "families.md"

FIGURES = [
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
        "~$141", [FIX, CH16]),
    Fig("dana-rap-70k-interest",
        lambda ns: _dana_fed(ns, DANA_INCOME)[
            ns["RAP_STRATEGY_LABEL"]]["total_interest"],
        "~$1,200", [FIX], exact="$1,165"),
    Fig("dana-rap-70k-monthly",
        lambda ns: float(_dana_fed(ns, DANA_INCOME)[
            ns["RAP_STRATEGY_LABEL"]]["schedule"]["payment"].iloc[0]),
        "~$350", [FIX, CH16]),
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

    # ---- The baseline every case is measured against.
    Fig("hs-grad-salary",
        lambda ns: ns["HS_GRAD_SALARY"],
        "$51,688", [FIX]),
]

# A `(tool: $N)` marker in families.md that no figure above reproduces.
# Each needs a reason, and the reason is the record of what is not checked.
TOOL_MARKER_EXEMPT = {
    "$52,180": "the kindergarten teacher's wage as MAJOR_DATA resolves it, "
               "which needs a full build_major_data; the raw dataset wage is "
               "checked above for the chapter 12 occupations instead",
    "$4,044": "break-even loan: needs build_major_data and the chapter's own "
              "baseline kwargs. See the docstring's limits.",
    "$138,462": "ten-year premium, same limit as the break-even",
    "$34,647": "ten-year premium, same limit as the break-even",
    "$316,191": "ten-year premium, same limit as the break-even",
    "$86,362": "ten-year premium, same limit as the break-even",
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

    # 5. An exact value missing from the fixture file.
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

    checked = len(FIGURES) + len(TOOL_MARKER.findall(chapters[FIX])) + 5
    if problems:
        print("check_book_figures FAILED\n")
        print("\n".join(problems))
        return 1
    print(f"check_book_figures OK: {len(FIGURES)} figures reproduce from the "
          f"tool and are quoted alike across {len(chapters)} files; "
          f"{len(TOOL_MARKER.findall(chapters[FIX]))} tool markers covered or "
          f"excused; 5 negative controls fired. {checked} checks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""The book's diagrams: SVG drawn from app.py's own functions.

    python3 brand/book_figures.py --list
    python3 brand/book_figures.py                 # all of them
    python3 brand/book_figures.py --only three-roads

WHY ONE FILE AND NOT TWELVE. Every one of these needs the same four things:
the section 1-2 exec prefix, a 900-unit canvas, the type ramp, and the parse
that refuses to write invalid XML. Twelve scripts is eleven copies of that,
and the copies are what drift. `brand/build_guide_plan_chart.py` stays as it
is: it is published, it is cited by a live guide, and moving it would be a
refactor wearing a chore's clothes.

THE CANVAS IS 900 BECAUSE OF THE PAGE, not because of the screen. A book
figure is printed at about 5 inches wide, and what decides legibility is the
ratio of type to canvas width, exactly as it is for a guide figure on a
phone. NOTHING BELOW 26 UNITS. Cut the copy to fit rather than shrinking the
type, and put explanatory sentences in the caption where they are real text
rather than inside the picture where they are 7px.

THE GROUND IS WHITE. The book's interior was decided on 2026-09-10, so these
are drawn light from the start and there is no dark twin to keep in step.

AN INVALID SVG RENDERS AS NOTHING, SILENTLY. No console error, no failing
guard, just blank space. A duplicate attribute and a `--` inside a comment
are both XML errors and both easy to write, so every figure parses before it
is written.
"""
import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "static"

W = 900
INK, MUTED, GRID, RULE = "#14161a", "#5c636d", "#eceff2", "#dfe3e8"
# The brand's own light values, the same ones marketing/chart_palette.py maps
# the dark charts onto, so a diagram and a chart on facing pages agree.
COST, GAIN, DEEP = "#eb6834", "#2a78d6", "#12335c"
OLIVE, WARN = "#5c6b1f", "#b0532a"

FONT = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, "
        "sans-serif")
CSS = f"""
  text {{ font-family: {FONT}; fill: {INK}; }}
  .h  {{ font-size: 34px; font-weight: 700; }}
  .sub{{ font-size: 26px; fill: {MUTED}; }}
  .lab{{ font-size: 27px; }}
  .num{{ font-size: 29px; font-weight: 700; }}
  .ax {{ font-size: 26px; fill: {MUTED}; }}
  .key{{ font-size: 26px; }}
"""


def load_app():
    """app.py's sections 1 and 2, the way analyze_model.py reads them."""
    src = (REPO / "app.py").read_text()
    m = re.search(r"^# =+\n# 3\. PAGE CONFIG & SESSION STATE", src, re.M)
    if not m:
        sys.exit("app.py's section 3 banner moved; this reads the prefix above it.")
    ns = {"__name__": "app_prefix"}
    exec(compile(src[:m.start()], str(REPO / "app.py"), "exec"), ns)
    # Section 2m, the existing-balance repayment comparison, physically sits
    # BELOW the section 3 banner even though its banner says 2m, so the prefix
    # cannot reach it. Every repayment guard carries this same AST pass.
    import ast
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name not in ns:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), ns)
    return ns


_MODEL = {}


def load_model():
    """MAJOR_DATA and the rest, through the layer analyze_model already builds.

    Section 4 is where MAJOR_DATA is assembled from load_bls_careers and the
    curated entries, and section 4 needs a sidebar. analyze_model.py exists to
    rebuild it without one, and check_book_figures.py already reads the book's
    money through it, so a figure and a fixture cannot disagree about which
    model they are drawn from.
    """
    if not _MODEL:
        sys.path.insert(0, str(REPO))
        import analyze_model
        _MODEL["ns"] = analyze_model.load_model_layer()
    return _MODEL["ns"]


def find_title(ns, needle):
    hits = [t for t in ns["MAJOR_DATA"] if needle.lower() in t.lower()]
    if not hits:
        sys.exit(f"no occupation matching {needle!r}; the careers file moved.")
    return sorted(hits, key=len)[0]


# Average advance per character at each class size, measured off the rendered
# frames rather than assumed: a bold face is wider, and a figure that fits at
# one string length does not fit at another. This is the SVG stand-in for the
# measured-text rule the matplotlib charts follow, and it is deliberately
# PESSIMISTIC, because a refusal costs a rewrite and an overflow costs a
# reprint.
CHAR_W = {"h": 20.0, "sub": 13.5, "lab": 14.0, "num": 17.0, "ax": 13.5, "key": 13.5}


def text(cls, x, y, s, anchor="start", fill=None, budget=None):
    """One text element, refusing to emit a line that cannot fit the canvas."""
    width = len(str(s)) * CHAR_W[cls]
    canvas = (x - 20 if anchor == "end" else W - x - 20 if anchor == "start"
              else 2 * min(x - 20, W - x - 20))
    # AN EXPLICIT BUDGET NARROWS, IT NEVER WIDENS. A caller passing a budget
    # bigger than the room actually on the canvas turns this check off, which
    # is how "$456,558" ran off the right edge and how two legend items
    # overlapped: both passed, both were wrong. min() makes that unreachable.
    room = min(budget, canvas) if budget is not None else canvas
    if width > room:
        sys.exit(f"a {cls} line needs about {width:.0f} units and has {room:.0f}: "
                 f"{s!r}. Cut the copy rather than shrinking the type; an "
                 f"explanatory sentence belongs in the caption.")
    a = f' text-anchor="{anchor}"' if anchor != "start" else ""
    f = f' fill="{fill}"' if fill else ""
    return f'<text class="{cls}" x="{x:.1f}" y="{y:.1f}"{a}{f}>{esc(s)}</text>'


# What each figure asserts its chapter says, filled in as a figure is built.
# check_book_diagrams.py reads it: a figure and the chapter that cites it come
# from the same model and so cannot drift from IT, but they can drift from
# EACH OTHER, and until this existed nothing compared them.
CLAIMS = {}

# Figures that make no claim on a chapter, each with the reason. A figure in
# neither dict fails the guard: coverage is derived, not listed.
NO_CLAIM = {
    "extra-dollar": "a regulation's ordering, no computed figure on it",
    "households": "the four households as the book introduces them, no arithmetic",
    "the-year": "the calendar in order, no arithmetic",
    "the-order": "the four questions in order, no arithmetic",
    "interest-only": "the balance is the Hall loan but the stretch is the "
                     "figure's own example; Ch. 8 prices no stretch",
    "roll-down": "its four-loan portfolio is invented for the figure; Ch. 18 "
                 "prices a different one",
}


def claim(name, chapter, exact=(), **cites):
    """Record what this figure says its chapter says.

    Keys are the string as the chapter writes it, values are what the model
    produced. The guard checks both halves: that the string is still in the
    chapter, and that the number behind it is within half of its own last
    place of what the figure drew.

    `exact` NAMES THE STATUTORY ONES, and it is not a nicety. "$5,500" reads
    as a figure quoted to the nearest hundred, so the half-place rule accepts
    $5,506 against it, which is fine for an estimate and wrong for a ceiling
    written into law. The book's own convention is that rounded money carries
    a tilde and statutory money does not; naming them here says which is which
    without asking the guard to infer it from punctuation.
    """
    CLAIMS[name] = {"chapter": chapter, "cites": dict(cites),
                    "exact": set(exact)}


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def money(v, k=False):
    return f"${v / 1000:,.0f}k" if k else f"${v:,.0f}"


def write(name, height, body, note=None):
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" '
           f'viewBox="0 0 {W} {height}" role="img">',
           f'<style>{CSS}</style>',
           f'<rect width="{W}" height="{height}" fill="#ffffff"/>']
    svg += body
    svg.append("</svg>")
    out = "\n".join(svg) + "\n"
    try:
        ET.fromstring(out)
    except ET.ParseError as exc:
        sys.exit(f"{name}: refusing to write invalid SVG: {exc}")
    # THE HEIGHT IS IN THE FILENAME, so changing a figure's height leaves the
    # old file beside the new one. That is not merely untidy: it is how a
    # screenshot of the PREVIOUS version gets checked and passed, which
    # happened once during this work. Sweep the figure's own stale siblings.
    path = OUT_DIR / f"book-{name}-{W}x{height}.svg"
    for stale in OUT_DIR.glob(f"book-{name}-{W}x*.svg"):
        if stale != path:
            stale.unlink()
            print(f"  swept {stale.name}")
    path.write_text(out)
    print(f"  wrote {path.name}  ({path.stat().st_size:,} bytes)")
    if note:
        print(f"    {note}")
    return path


# ---------------------------------------------------------------- Ch. 7

PLUS_RATE = 9.07          # first disbursed July 1 2026 to June 30 2027
PLUS_YEAR = 16_250.0      # the capped $65,000 spread evenly over four years


def three_roads(ns):
    """The capped Parent PLUS loan on each of the three roads, as balances.

    THE THREE CONVENTIONS ARE THE CHAPTER'S AND EACH ONE MATTERS.

      slow        every loan deferred to 54 months, which is the four years
                  plus the six month post-enrollment period 685.204(b)(2)
                  grants on request, so all four enter together and all four
                  land in the 20-year band.
      as you go    each loan enters 60 days after its own disbursement, so the
                  terms differ: 10, 15, 15, 20.
      fast        the same entries as the disbursements, because the road is
                  "pay from the first disbursement", and paid at the 10-year
                  pace whatever term each loan was assigned.

    The third convention is not cosmetic. Paying from ENTRY rather than from
    DISBURSEMENT gives $35,601 and $838, against the chapter's $34,103 and
    $826. Building this figure is what settled which one the chapter meant.
    """
    def road(entries, disbursed, pace=None):
        loans = [{"balance": PLUS_YEAR, "rate": PLUS_RATE, "disbursed": d,
                  "entry": e, "subsidized": False}
                 for e, d in zip(entries, disbursed)]
        terms = ns["tiered_terms_at_entry"](loans)
        per, interest = [], 0.0
        for loan, term in zip(loans, terms):
            d = ns["entry_deferment"](loan)
            r = ns["calculate_standard_repayment"](d["principal"], PLUS_RATE,
                                                   term_years=(pace or term))
            r = ns["apply_in_school_deferment"](r, d, d["entry"])
            per.append(r)
            interest += r["total_interest"]
        horizon = int(max(r["schedule"].month.max() for r in per))
        total = sum(r["schedule"].set_index("month").balance
                    .reindex(range(1, horizon + 1)).ffill().fillna(0.0) for r in per)
        return {"terms": terms, "interest": interest, "last": horizon / 12,
                "monthly": sum(r["monthly_payment"] for r in per),
                "points": [(mth / 12, bal) for mth, bal in total.items()]}

    D = [0, 12, 24, 36]
    return [
        ("Defer, and take the schedule", COST,  road([54, 54, 54, 54], D)),
        ("Pay as you go",                GAIN,  road([2, 14, 26, 38], D)),
        ("Pay at the ten-year pace",     OLIVE, road(D, D, pace=10)),
    ]


def fig_three_roads(ns):
    roads = three_roads(ns)
    PAD_L, PAD_R, TOP, PLOT_H = 132, 40, 150, 430
    xmax = max(r["last"] for _, _, r in roads)
    peak = max(b for _, _, r in roads for _, b in r["points"])
    # Round the top to the gridline above the peak. Stepping the grid past a
    # raw ymax draws a label ABOVE the plot, where it lands in the subtitle:
    # the first render put "$100k" through the deck.
    step = 20_000
    ymax = step * (int(peak // step) + 1)
    rows_h = 46
    height = TOP + PLOT_H + 96 + rows_h * len(roads) + 40

    def X(yr):
        return PAD_L + (W - PAD_L - PAD_R) * yr / xmax

    def Y(bal):
        return TOP + PLOT_H - PLOT_H * bal / ymax

    b = [f'<text class="h" x="40" y="52">The same $65,000, on three roads</text>',
         text("sub", 40, 96,
              f"Four loans of {money(PLUS_YEAR)} at {PLUS_RATE}%, what is still owed")]
    for gy in range(0, int(ymax) + 20_000, 20_000):
        b.append(f'<line x1="{PAD_L}" y1="{Y(gy):.1f}" x2="{W - PAD_R}" y2="{Y(gy):.1f}" '
                 f'stroke="{GRID}" stroke-width="2"/>')
        b.append(f'<text class="ax" x="{PAD_L - 14}" y="{Y(gy) + 9:.1f}" '
                 f'text-anchor="end">{money(gy, k=True)}</text>')
    for yr in range(0, int(xmax) + 5, 5):
        b.append(f'<text class="ax" x="{X(yr):.1f}" y="{TOP + PLOT_H + 42}" '
                 f'text-anchor="middle">{yr}</text>')
    b.append(f'<text class="ax" x="{W - PAD_R}" y="{TOP + PLOT_H + 80}" '
             f'text-anchor="end">Years after the freshman fall</text>')
    for label, colour, r in roads:
        pts = " ".join(f"{X(y):.1f},{Y(v):.1f}" for y, v in r["points"])
        b.append(f'<polyline points="{pts}" fill="none" stroke="{colour}" '
                 f'stroke-width="4.5" stroke-linejoin="round"/>')
    y = TOP + PLOT_H + 130
    for label, colour, r in roads:
        b.append(f'<rect x="40" y="{y - 20}" width="26" height="26" fill="{colour}"/>')
        b.append(f'<text class="key" x="80" y="{y}">{esc(label)}</text>')
        b.append(f'<text class="key" x="{W - 40}" y="{y}" text-anchor="end" '
                 f'fill="{MUTED}">{money(r["interest"])} of interest, '
                 f'{r["last"]:.0f} years</text>')
        y += rows_h
    slow, asgo, fast = (r for _, _, r in roads)
    claim("three-roads", "ch07",
          **{"~$114,400": slow["interest"], "~$56,400": asgo["interest"],
             "~$34,100": fast["interest"], "~$82,700": max(b for _, b in slow["points"]),
             "~$750": slow["monthly"], "~$695": asgo["monthly"],
             "~$826": fast["monthly"]})
    note = (" | ".join(f"{l}: terms {r['terms']}, {money(r['interest'])}, "
                       f"{money(r['monthly'])}/mo peak, {r['last']:.1f}y"
                       for l, _, r in roads))
    return write("three-roads", height, b, note)


# ---------------------------------------------------------------- Ch. 6

def fig_cap_ladder(ns):
    """What a dependent undergraduate may borrow, year by year.

    STATUTORY FIGURES, SO NO TILDE AND NO ROUNDING. These are ceilings rather
    than estimates, which is the book's own rule for $5,500 and $65,000, and
    they are read out of app.py's constants rather than typed here.
    """
    annual = ns["FEDERAL_DIRECT_ANNUAL_LIMITS"]["dependent"]
    subsid = ns["FEDERAL_SUBSIDIZED_ANNUAL_LIMITS"]
    plus = ns["PARENT_PLUS_AGGREGATE_LIMIT"]
    years = sorted(annual)
    total = sum(annual[y] for y in years)

    PAD_L, TOP, PLOT_H, BAR_W = 132, 168, 300, 108
    height = TOP + PLOT_H + 300
    ymax = 8000
    gap = (W - PAD_L - 60 - BAR_W * len(years)) / (len(years) - 1)

    def bx(i):
        return PAD_L + i * (BAR_W + gap)

    def h(v):
        return PLOT_H * v / ymax

    b = [text("h", 40, 52, "The student's four-year ladder"),
         text("sub", 40, 96, f"What a dependent undergraduate may borrow: "
                             f"{money(total)} in all")]
    for i, y in enumerate(years):
        sub_v, un_v = subsid[y], annual[y] - subsid[y]
        base = TOP + PLOT_H
        b.append(f'<rect x="{bx(i):.1f}" y="{base - h(sub_v):.1f}" width="{BAR_W}" '
                 f'height="{h(sub_v):.1f}" fill="{DEEP}"/>')
        b.append(f'<rect x="{bx(i):.1f}" y="{base - h(annual[y]):.1f}" width="{BAR_W}" '
                 f'height="{h(un_v):.1f}" fill="{GAIN}"/>')
        b.append(text("num", bx(i) + BAR_W / 2, base - h(annual[y]) - 16,
                      money(annual[y]), anchor="middle", budget=BAR_W + gap))
        b.append(text("ax", bx(i) + BAR_W / 2, base + 42, f"Year {y}",
                      anchor="middle", budget=BAR_W + gap))
    y = TOP + PLOT_H + 108
    for colour, label in ((DEEP, "Subsidized: no interest while enrolled"),
                          (GAIN, "Unsubsidized: interest runs from disbursement")):
        b.append(f'<rect x="40" y="{y - 20}" width="26" height="26" fill="{colour}"/>')
        if "loan" in label.lower():
            b.append(f'<rect x="40" y="{y - 20}" width="26" height="26" '
                     f'fill="url(#loanhatch)"/>')
        b.append(text("key", 80, y, label))
        y += 44
    b.append(f'<line x1="40" y1="{y + 6}" x2="{W - 40}" y2="{y + 6}" '
             f'stroke="{RULE}" stroke-width="2"/>')
    b.append(text("lab", 40, y + 54, f"A parent may borrow {money(plus)} more, "
                                     f"at a higher rate."))
    claim("cap-ladder", "ch06",
          exact=("$5,500", "$6,500", "$7,500", "$27,000", "$65,000"),
          **{"$5,500": annual[1], "$6,500": annual[2], "$7,500": annual[3],
             "$27,000": total, "$65,000": plus})
    return write("cap-ladder", height, b,
                 f"{money(total)} student ({money(sum(subsid.values()))} of it "
                 f"subsidized) against {money(plus)} parent")


# ---------------------------------------------------------------- Ch. 18

def fig_extra_dollar(ns):
    """Where a prepayment goes, and the instruction that changes it.

    34 CFR 685.211(a)(3). The order is the regulation's; the last box is what
    happens BY DEFAULT, which is the part borrowers do not expect, and the
    footer is the instruction that redirects it. This figure asserts nothing
    about what any servicer does, which is the line app.py's own
    EXTRA_PAYMENT_DIRECTION_NOTE holds.
    """
    steps = [("Late charges and collection costs", "first, if any are owed"),
             ("Accrued interest", "everything outstanding, before principal"),
             ("Principal", "what is left of the extra"),
             ("Your next due date moves out", "unless you say otherwise")]
    BOX_H, GAP, TOP = 96, 30, 168
    height = TOP + len(steps) * (BOX_H + GAP) + 190
    b = [text("h", 40, 52, "Where an extra dollar goes"),
         text("sub", 40, 96, "The order set by 34 CFR 685.211(a)(3)")]
    for i, (head, why) in enumerate(steps):
        y = TOP + i * (BOX_H + GAP)
        last = i == len(steps) - 1
        fill, edge = ("#fdf1ec", COST) if last else ("#eef4fb", GAIN)
        b.append(f'<rect x="40" y="{y}" width="{W - 80}" height="{BOX_H}" rx="10" '
                 f'fill="{fill}" stroke="{edge}" stroke-width="3"/>')
        b.append(text("lab", 68, y + 42, head))
        b.append(text("ax", 68, y + 76, why))
        b.append(text("num", W - 68, y + 58, str(i + 1), anchor="end",
                      fill=edge, budget=60))
        if not last:
            mid = W / 2
            b.append(f'<path d="M{mid} {y + BOX_H} L{mid} {y + BOX_H + GAP - 6} '
                     f'M{mid - 9} {y + BOX_H + GAP - 15} L{mid} {y + BOX_H + GAP - 6} '
                     f'L{mid + 9} {y + BOX_H + GAP - 15}" stroke="{MUTED}" '
                     f'stroke-width="3" fill="none"/>')
    y = TOP + len(steps) * (BOX_H + GAP) + 54
    b.append(text("lab", 40, y, "The fourth box is the default, not the rule."))
    b.append(text("ax", 40, y + 40, "Ask in writing for it to go to principal on a"))
    b.append(text("ax", 40, y + 76, "loan you name, and for the due date to stay put."))
    return write("extra-dollar", height, b, "four boxes, 685.211(a)(3)")


# ---------------------------------------------------------------- Ch. 4

def fig_in_state(ns):
    """The same seat at two prices, and how many schools charge them.

    The gap is what makes a search a per-row question rather than one flag:
    a result set spans nine states and a family is resident in one. Both
    figures are the same schools' own cost of attendance, so the difference
    is residency and nothing else.
    """
    import pandas as pd
    d = pd.read_csv(REPO / "data" / "college_coa_clean.csv")
    pub = d[(d.control_type == "Public")
            & d.in_state_coa.notna() & d.out_of_state_coa.notna()
            & (d.out_of_state_coa > d.in_state_coa)]
    ins, out = pub.in_state_coa.median(), pub.out_of_state_coa.median()
    gap = out - ins
    priv = d[(d.control_type == "Private Non-Profit") & d.in_state_coa.notna()]
    pm = priv.in_state_coa.median()

    PAD_L, TOP, BAR_H, GAP = 348, 168, 74, 34
    rows = [("Public, in state", ins, GAIN),
            ("Public, out of state", out, COST),
            ("Private nonprofit", pm, MUTED)]
    ymax = max(v for _, v, _ in rows) * 1.18
    height = TOP + len(rows) * (BAR_H + GAP) + 210
    b = [text("h", 40, 52, "The same seat, at two prices"),
         text("sub", 40, 96, f"Median cost of attendance for a year, "
                             f"{len(pub):,} public colleges")]
    for i, (label, v, colour) in enumerate(rows):
        y = TOP + i * (BAR_H + GAP)
        w = (W - PAD_L - 190) * v / ymax
        b.append(f'<rect x="{PAD_L}" y="{y}" width="{w:.1f}" height="{BAR_H}" '
                 f'fill="{colour}"/>')
        b.append(text("lab", PAD_L - 24, y + BAR_H / 2 + 10, label, anchor="end",
                      budget=PAD_L - 60))
        b.append(text("num", PAD_L + w + 20, y + BAR_H / 2 + 10, money(v),
                      budget=170))
    y = TOP + len(rows) * (BAR_H + GAP) + 46
    b.append(f'<line x1="40" y1="{y}" x2="{W - 40}" y2="{y}" stroke="{RULE}" '
             f'stroke-width="2"/>')
    b.append(text("lab", 40, y + 54,
                  f"Crossing a state line costs {money(gap)} a year at the median."))
    b.append(text("ax", 40, y + 96, "The search prices every school at the rate you"))
    b.append(text("ax", 40, y + 132, "would pay, which is why it asks where you live."))
    claim("in-state", "ch04", **{"~$7,000": gap})
    return write("in-state", height, b,
                 f"in {money(ins)} out {money(out)} gap {money(gap)} "
                 f"private {money(pm)} over {len(pub):,} publics")


# ---------------------------------------------------------------- Ch. 10

ROI_RATE, ROI_STRATEGY = 6.5, "Standard 10-Year"   # check_book_figures.py's own
BREAKEVEN_CAREERS = ["Dentists, General", "Software Developers", "Lawyers",
                     "Registered Nurses", "Elementary School Teachers",
                     "Kindergarten Teachers"]


def fig_break_even(ns):
    """How much debt each career can carry, against what the law will lend.

    The break-even is the loan at which ten years of this career equals ten
    years of a debt-free high school graduate. Below the line the degree
    pays for itself inside the window; above it, it does not.

    THE TWO RULES ARE THE POINT. $27,000 is everything a dependent
    undergraduate may borrow in their own name and $92,000 is that plus the
    parents' $65,000, so a career whose bar stops short of the first rule
    cannot carry even the student's own federal loan.
    """
    mns = load_model()
    import analyze_model as am
    rows = []
    for needle in BREAKEVEN_CAREERS:
        title = find_title(mns, needle)
        # THROUGH analyze_model's WRAPPER, NOT app.py's OWN FUNCTION. The
        # wrapper assembles hs_wage_index, enrollment_years, working_years
        # and baseline_start_age together, which is the whole reason it
        # exists: CLAUDE.md records that omitting any one of them silently
        # compares against a different baseline. Called directly with a
        # hand-written argument list, every one of these six came back
        # "beyond_search_max", still ahead on a $1,000,000 loan, which is
        # what that failure looks like. check_book_figures.py reproduces the
        # book's own break-evens through this same call.
        r = am.find_breakeven_loan(mns, title, ROI_RATE, ROI_STRATEGY)
        rows.append((title, r["breakeven_loan"], r["premium_at_zero_debt"]))
    # A career that never breaks even has no bar and must not be dropped:
    # "no loan is small enough" is the chapter's sharpest row, and leaving it
    # out would make the picture a survey of the ones that work.
    rows.sort(key=lambda x: (x[1] is None, -(x[1] or 0)))
    student = mns["FEDERAL_DIRECT_AGGREGATE_CAP"]["dependent"] - 4000  # see note
    student = sum(mns["FEDERAL_DIRECT_ANNUAL_LIMITS"]["dependent"].values())
    family = student + mns["PARENT_PLUS_AGGREGATE_LIMIT"]

    PAD_L, TOP, BAR_H, GAP = 424, 244, 56, 26
    # THE AXIS IS THE LAW, NOT THE LARGEST BAR. Break-evens here run $4,044 to
    # $456,558, a range of 113 times, and drawn to the largest the two federal
    # rules land almost on the origin: the question this figure exists to ask,
    # can this career carry what the government will lend, becomes unreadable.
    # So the axis stops just past the family maximum and a bar that runs past
    # it is cut with an arrow and its own figure. The alternative, a log axis,
    # asks a reader to do arithmetic in their head to compare two bars.
    xmax = family * 1.30
    height = TOP + len(rows) * (BAR_H + GAP) + 190

    def X(v):
        return PAD_L + (W - PAD_L - 150) * min(v, xmax) / xmax

    b = [text("h", 40, 52, "What each career can carry"),
         text("sub", 40, 96, "The loan at which ten years of the job equals not going"),
         text("sub", 40, 132, "at all. National wages, 6.5%, the ten-year plan.")]
    bottom = TOP + len(rows) * (BAR_H + GAP) - GAP + 8
    # Staggered, because at this scale the two rules are 65 units apart and
    # their labels are wider than that. The first draft printed them on one
    # line and they overlapped each other AND the deck.
    for v, label, colour, lift in ((student, f"student {money(student)}", MUTED, 62),
                                   (family, f"family {money(family)}", COST, 26)):
        b.append(f'<line x1="{X(v):.1f}" y1="{TOP - lift + 6}" x2="{X(v):.1f}" '
                 f'y2="{bottom}" stroke="{colour}" stroke-width="3" '
                 f'stroke-dasharray="8 7"/>')
        b.append(text("ax", X(v) - 12, TOP - lift, label, anchor="end",
                      budget=X(v) - 60))
    for i, (title, v, prem) in enumerate(rows):
        y = TOP + i * (BAR_H + GAP)
        short = title.split(",")[0]
        b.append(text("lab", PAD_L - 22, y + BAR_H / 2 + 9, short, anchor="end",
                      budget=PAD_L - 56))
        if v is None:
            b.append(text("lab", PAD_L + 8, y + BAR_H / 2 + 10,
                          "never, at any loan", fill=COST, budget=W - PAD_L - 60))
            continue
        w = max(X(v) - PAD_L, 3)
        b.append(f'<rect x="{PAD_L}" y="{y}" width="{w:.1f}" height="{BAR_H}" '
                 f'fill="{GAIN if v >= family else DEEP}"/>')
        if v > xmax:
            # THE LABEL GOES INSIDE. A clipped bar already reaches the right
            # margin, so its figure has nowhere to sit outside it: the first
            # render ran "$456,558" off the canvas, and it passed the width
            # check only because an over-generous explicit budget had been
            # handed to it. An explicit budget must never be wider than the
            # room actually there.
            tip = PAD_L + w
            b.append(f'<path d="M{tip} {y} L{tip + 22} {y + BAR_H / 2} '
                     f'L{tip} {y + BAR_H} Z" fill="{GAIN}"/>')
            b.append(f'<text class="num" x="{tip - 18:.1f}" '
                     f'y="{y + BAR_H / 2 + 10:.1f}" text-anchor="end" '
                     f'fill="#ffffff">{money(v)}</text>')
        else:
            b.append(text("num", PAD_L + w + 18, y + BAR_H / 2 + 10, money(v)))
    y = TOP + len(rows) * (BAR_H + GAP) + 40
    b.append(f'<line x1="40" y1="{y}" x2="{W - 40}" y2="{y}" stroke="{RULE}" stroke-width="2"/>')
    b.append(text("lab", 40, y + 52, "A bar short of the student line cannot carry"))
    b.append(text("lab", 40, y + 90, "even the loan in the student's own name."))
    kinder = next(v for t, v, _ in rows if "Kindergarten" in t)
    claim("break-even", "ch10", **{"$4,000": kinder})
    return write("break-even", height, b,
                 " | ".join(f"{t.split(chr(44))[0]} "
                            f"{money(v) if v else 'never'}" for t, v, _ in rows))


# ---------------------------------------------------------------- Ch. 12

def fig_take_home(ns):
    """One salary, from gross to what is left after the loan.

    ONE BAR, NOT TWO PIES. app.py made the same move on 2026-08-10 and for
    the same reason: the loan payment never appeared beside the tax bites it
    competes with, and a length is comparable where two nested pies are not.
    The segments come from salary_flow_segments, so this figure and the
    calculator cannot disagree about what is taken.
    """
    # A ROW FROM THE CHAPTER'S OWN TABLE, not a fifth example. The figure
    # first drew a generic $95,000 California salary and was placed directly
    # under Ch. 12's table of four named jobs, where it read as a fifth row
    # nobody could find. Drawing one of the four instead lets the picture be
    # CHECKED against the prose rather than merely captioned.
    #
    # The payment is the one on the whole federal student loan, so it is the
    # book's own $27,000 rather than a number picked to look reasonable.
    gross = 186_600.0
    payment = ns["calculate_standard_repayment"](
        sum(ns["FEDERAL_DIRECT_ANNUAL_LIMITS"]["dependent"].values()),
        6.5)["monthly_payment"]
    th = ns["calculate_take_home_pay"](gross, "CA")
    # salary_flow_segments ALREADY ENDS WITH WHAT IS LEFT. Appending a
    # remainder row put a duplicate "What is left, $0, 0%" under the real one:
    # the segments cover the whole salary, which is the invariant the app's
    # own bar relies on. Read what a function returns before extending it.
    segs = list(ns["salary_flow_segments"](th, payment))
    covered = sum(v for _, v, _, _ in segs)
    if abs(covered - gross) > 1.0:
        sys.exit(f"the segments cover {money(covered)} of a {money(gross)} "
                 f"salary; salary_flow_segments changed shape.")

    PAD_L, TOP, BAR_H = 40, 200, 118
    height = TOP + BAR_H + 96 + len(segs) * 46 + 60
    # A HATCH ON THE LOAN SEGMENT, because this bar does not survive grayscale
    # without it. Measured 2026-09-11: the loan's orange and What is left's blue
    # convert to the same dark grey, in the bar AND in the legend swatch, so the
    # two segments app.py's own note calls "the two carrying the decision" become
    # one block. The hatch is a SECOND channel, so the distinction no longer
    # rests on hue alone and the deliberate three-step grey tax ramp is left
    # exactly as it was. Interior print may be monochrome; this figure no longer
    # depends on the answer.
    b = [('<defs><pattern id="loanhatch" width="10" height="10" '
          'patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
          '<rect width="10" height="10" fill="none"/>'
          f'<line x1="0" y1="0" x2="0" y2="10" stroke="{INK}" stroke-width="4" '
          'stroke-opacity="0.55"/></pattern></defs>'),
         text("h", 40, 52, "Where a nurse's pay goes"),
         text("sub", 40, 96, f"San Francisco, {money(gross)} gross, and the "
                             f"payment on the"),
         text("sub", 40, 132, "whole $27,000 federal student loan")]
    x = PAD_L
    for label, v, colour, _ in segs:
        w = (W - 80) * v / gross
        b.append(f'<rect x="{x:.1f}" y="{TOP}" width="{w:.1f}" height="{BAR_H}" '
                 f'fill="{colour}"/>')
        if "loan" in label.lower():
            b.append(f'<rect x="{x:.1f}" y="{TOP}" width="{w:.1f}" '
                     f'height="{BAR_H}" fill="url(#loanhatch)"/>')
        x += w
    y = TOP + BAR_H + 76
    for label, v, colour, _ in segs:
        b.append(f'<rect x="40" y="{y - 20}" width="26" height="26" fill="{colour}"/>')
        if "loan" in label.lower():
            b.append(f'<rect x="40" y="{y - 20}" width="26" height="26" '
                     f'fill="url(#loanhatch)"/>')
        b.append(text("key", 80, y, label))
        b.append(text("key", W - 40, y, f"{100 * v / gross:.0f}%", anchor="end",
                      fill=MUTED, budget=90))
        b.append(text("key", W - 132, y, money(v), anchor="end", fill=MUTED,
                      budget=220))
        y += 46
    # The three taxes are the chapter's own row and are unaffected by the loan
    # payment, so they are what this figure claims. What is left is NOT
    # claimed: the chapter's take-home column is before the loan and this
    # bar's last segment is after it.
    claim("take-home", "ch12",
          **{"~$34,300": th["federal_tax"], "~$13,400": th["state_tax"],
             "~$13,200": th["fica_tax"], "~$186,600": gross})
    return write("take-home", height, b,
                 " | ".join(f"{l} {money(v)}" for l, v, _, _ in segs))


# ---------------------------------------------------------------- Ch. 8

def fig_interest_only(ns):
    """A private balance with an interest-only stretch, and without.

    BOTH DIRECTIONS OR IT IS ADVERTISING, which is the rule
    private_structure_disclosure already holds in the app: the stretch lowers
    the payment AND raises the total, and a figure showing only the first
    half is a lender's brochure.
    """
    bal, rate, term, stretch = 59_000.0, 11.0, 10, 24
    plain = ns["calculate_standard_repayment"](bal, rate, term_years=term)
    # During the stretch only the interest is paid, so the balance is flat and
    # the amortising term starts later on the same principal.
    monthly_interest = bal * rate / 100 / 12
    after = ns["calculate_standard_repayment"](bal, rate, term_years=term)
    total_plain = plain["monthly_payment"] * term * 12
    total_stretch = monthly_interest * stretch + after["monthly_payment"] * term * 12

    PAD_L, TOP, BAR_H, GAP = 386, 190, 70, 30
    rows = [("Pay it as written", plain["monthly_payment"], total_plain, GAIN),
            (f"{stretch} months interest only", monthly_interest, total_stretch, COST)]
    height = TOP + len(rows) * (BAR_H + GAP) + 230
    xmax = max(t for _, _, t, _ in rows) * 1.16
    b = [text("h", 40, 52, "What an interest-only stretch buys"),
         text("sub", 40, 96, f"{money(bal)} at {rate}% over {term} years"),
         text("sub", 40, 132, "The monthly falls and the total rises.")]
    for i, (label, m, total, colour) in enumerate(rows):
        y = TOP + i * (BAR_H + GAP)
        w = (W - PAD_L - 200) * total / xmax
        b.append(f'<rect x="{PAD_L}" y="{y}" width="{w:.1f}" height="{BAR_H}" fill="{colour}"/>')
        b.append(text("lab", PAD_L - 22, y + BAR_H / 2 + 9, label, anchor="end",
                      budget=PAD_L - 56))
        b.append(text("num", PAD_L + w + 18, y + BAR_H / 2 + 10, money(total), budget=190))
    y = TOP + len(rows) * (BAR_H + GAP) + 42
    b.append(f'<line x1="40" y1="{y}" x2="{W - 40}" y2="{y}" stroke="{RULE}" stroke-width="2"/>')
    b.append(text("lab", 40, y + 52,
                  f"{money(rows[0][1])} a month becomes {money(rows[1][1])},"))
    b.append(text("lab", 40, y + 90,
                  f"and {money(total_stretch - total_plain)} is added to the total."))
    b.append(text("ax", 40, y + 134, "Bars are what is handed over in all."))
    return write("interest-only", height, b,
                 f"plain {money(total_plain)} vs stretch {money(total_stretch)}")


# ---------------------------------------------------------------- Ch. 15

def fig_exposure(ns):
    """Where the exposed work is, by the education its entry needs.

    THE FINDING IS THAT EXPOSURE IS NOT ON THE DEGREE'S SIDE. This book's
    counterfactual is a debt-free high school graduate, so if the most
    exposed work sits below a bachelor's then any damage falls on BOTH sides
    of the comparison, and on the baseline first.

    Exposure here is app.py's own band by SOC major group, which moves no
    salary anywhere in the model, and the weighting is employment, so the
    picture is about jobs rather than about occupation titles.
    """
    mns = load_model()
    bands = mns["AI_EXPOSURE_BY_SOC_GROUP"]
    groups = {"High": COST, "Medium": "#c9b18c", "Low": GAIN}
    tiers = {"High school diploma or equivalent": "Entry needs no degree",
             "Bachelor's degree": "Entry needs a bachelor's"}
    tally = {t: {k: 0.0 for k in groups} for t in tiers.values()}
    for title, d in mns["MAJOR_DATA"].items():
        edu = d.get("typical_education")
        soc = str(d.get("soc_major_group") or "")
        emp = d.get("national_employment") or 0
        if edu in tiers and soc in bands and emp:
            lvl = bands[soc]["risk_level"]
            if lvl in tally[tiers[edu]]:
                tally[tiers[edu]][lvl] += float(emp)
    PAD_L, TOP, BAR_H, GAP = 40, 210, 96, 74
    height = TOP + len(tally) * (BAR_H + GAP) + 210
    b = [text("h", 40, 52, "Where the exposed work is"),
         text("sub", 40, 96, "Employment by the education an entrant needs,"),
         text("sub", 40, 132, "split by this book's own AI exposure band")]
    y = TOP
    for tier, counts in tally.items():
        total = sum(counts.values()) or 1.0
        b.append(text("lab", 40, y - 16, f"{tier}, {total / 1e6:,.1f} million jobs"))
        x = 40
        for level, colour in groups.items():
            w = (W - 80) * counts[level] / total
            b.append(f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{BAR_H}" '
                     f'fill="{colour}"/>')
            if w > 96:
                b.append(f'<text class="key" x="{x + w / 2:.1f}" y="{y + BAR_H / 2 + 9:.1f}" '
                         f'text-anchor="middle" fill="#ffffff">{100 * counts[level] / total:.0f}%'
                         f'</text>')
            x += w
        y += BAR_H + GAP
    # Laid out from the labels rather than on a guessed pitch: "Medium
    # exposure" is wider than the 224-unit step the first version used, so it
    # ran into the swatch beside it.
    lx = 42
    for level, colour in groups.items():
        label = f"{level} exposure"
        b.append(f'<rect x="{lx}" y="{y - 34}" width="26" height="26" fill="{colour}"/>')
        b.append(text("key", lx + 40, y - 14, label))
        lx += 40 + len(label) * CHAR_W["key"] + 46
    b.append(text("lab", 40, y + 52, "The exposure is on both sides of the comparison,"))
    b.append(text("lab", 40, y + 90, "and the baseline is not the sheltered one."))
    no_deg = tally["Entry needs no degree"]
    bach = tally["Entry needs a bachelor's"]
    claim("exposure", "ch15",
          **{"30%": 100 * no_deg["High"] / sum(no_deg.values()),
             "26%": 100 * bach["High"] / sum(bach.values()),
             "54.7 million": sum(no_deg.values()) / 1e6,
             "39.0 million": sum(bach.values()) / 1e6})
    return write("exposure", height, b,
                 " | ".join(f"{t}: " + " ".join(f"{k} {v/1e6:.1f}M" for k, v in c.items())
                            for t, c in tally.items()))


# ---------------------------------------------------------------- Ch. 1

def fig_households(ns):
    """The four households, as the book introduces them."""
    # CUT TO FIT, which is the rule: an explanatory sentence belongs in the
    # caption, where it is real text. The first draft ran 918 units into a
    # 740-unit row and text() refused to write it.
    cards = [("The Reyes family", "Senior spring. A $31,400 gap, a PLUS letter.",
              "Ch. 3, 7, 10, 11"),
             ("The Hall family", "Sophomore year. $59,000 private, at 11%.",
              "Ch. 8, 15"),
             ("The Nakamura family", "Junior year. No school, three majors.",
              "Ch. 4, 11, 14"),
             ("Dana Whitfield", "Thirty-one. The federal minimum is back.",
              "Ch. 17, 18")]
    BOX_H, GAP, TOP = 132, 26, 168
    height = TOP + len(cards) * (BOX_H + GAP) + 120
    b = [text("h", 40, 52, "Four households"),
         text("sub", 40, 96, "Invented families, real numbers, every one from the tool")]
    for i, (name, line, where) in enumerate(cards):
        y = TOP + i * (BOX_H + GAP)
        b.append(f'<rect x="40" y="{y}" width="{W - 80}" height="{BOX_H}" rx="10" '
                 f'fill="#f7f9fb" stroke="{RULE}" stroke-width="2"/>')
        b.append(f'<rect x="40" y="{y}" width="8" height="{BOX_H}" rx="4" fill="{DEEP}"/>')
        b.append(text("lab", 72, y + 46, name))
        b.append(text("ax", 72, y + 84, line, budget=W - 160))
        b.append(text("ax", 72, y + 116, where, fill=MUTED, budget=W - 160))
    b.append(text("ax", 40, height - 48,
                  "Where a chapter quotes one, the input is on the page."))
    return write("households", height, b, f"{len(cards)} cards")


# ---------------------------------------------------------------- Ch. 9

def fig_the_year(ns):
    """The college money year, in the order the dates arrive."""
    stops = [("October 1", "The form opens", "Ch. 3"),
             ("November", "Binding deadlines land first", "Ch. 4"),
             ("January", "The numbers underneath reset", "Ch. 3"),
             ("March, April", "The letter", "Ch. 2"),
             ("May 1", "The reply date", "Ch. 4"),
             ("July 1", "The day the rules change", "Ch. 6"),
             ("September 30", "One form, one point", "Ch. 18"),
             ("Six months out", "The first payment", "Ch. 17")]
    TOP, STEP, LX = 176, 78, 300
    height = TOP + len(stops) * STEP + 130
    b = [text("h", 40, 52, "The year, in order"),
         text("sub", 40, 96, "Each date belongs to a chapter that has priced it")]
    b.append(f'<line x1="{LX}" y1="{TOP - 24}" x2="{LX}" '
             f'y2="{TOP + (len(stops) - 1) * STEP + 24}" stroke="{RULE}" stroke-width="4"/>')
    for i, (when, what, ch) in enumerate(stops):
        y = TOP + i * STEP
        b.append(f'<circle cx="{LX}" cy="{y}" r="11" fill="{DEEP}"/>')
        b.append(text("lab", LX - 34, y + 10, when, anchor="end", budget=LX - 70))
        b.append(text("lab", LX + 34, y + 10, what, budget=W - LX - 160))
        b.append(text("ax", W - 40, y + 10, ch, anchor="end", fill=MUTED, budget=110))
    b.append(text("ax", 40, height - 52,
                  "The order matters more than any single date."))
    return write("the-year", height, b, f"{len(stops)} stops")


# ---------------------------------------------------------------- Ch. 16

def fig_the_order(ns):
    """The four questions, in the order that makes each one answerable."""
    steps = [("What will this actually cost us?",
              "Not the sticker. This family, this school.", "Ch. 3, 4, 5"),
             ("What is left to borrow, and in whose name?",
              "$27,000, then $65,000, then a bank.", "Ch. 6, 7, 8"),
             ("What does the payment look like?",
              "A monthly figure against a paycheck.", "Ch. 12"),
             ("Is the whole thing worth it?",
              "Against not going, over your own horizon.", "Ch. 10, 11")]
    BOX_H, GAP, TOP = 128, 28, 168
    height = TOP + len(steps) * (BOX_H + GAP) + 116
    b = [text("h", 40, 52, "The order to work in"),
         text("sub", 40, 96, "Each step changes what the next one means")]
    for i, (q, why, ch) in enumerate(steps):
        y = TOP + i * (BOX_H + GAP)
        b.append(f'<rect x="40" y="{y}" width="{W - 80}" height="{BOX_H}" rx="10" '
                 f'fill="#f7f9fb" stroke="{RULE}" stroke-width="2"/>')
        b.append(f'<circle cx="86" cy="{y + BOX_H / 2}" r="26" fill="{DEEP}"/>')
        b.append(f'<text class="num" x="86" y="{y + BOX_H / 2 + 10}" '
                 f'text-anchor="middle" fill="#ffffff">{i + 1}</text>')
        b.append(text("lab", 132, y + 50, q, budget=W - 240))
        b.append(text("ax", 132, y + 88, why, budget=W - 240))
        b.append(text("ax", W - 68, y + 50, ch, anchor="end", fill=MUTED, budget=150))
    b.append(text("ax", 40, height - 48,
                  "Answer them out of order and the answers do not mean much."))
    return write("the-order", height, b, f"{len(steps)} steps")


# ---------------------------------------------------------------- Ch. 18

ROLL_LOANS = [{"balance": 12_000.0, "rate": 12.5},
              {"balance": 18_000.0, "rate": 9.5},
              {"balance": 9_000.0,  "rate": 7.0},
              {"balance": 20_000.0, "rate": 5.5}]


def fig_roll_down(ns):
    """The same budget, with and without rolling a cleared note forward.

    THE BUDGET NEVER SHRINKS, which is the whole mechanism: when a note is
    paid off its required payment does not go back to the borrower, it goes
    to the highest-rate note still alive. simulate_fixed_avalanche is what
    app.py already does this with, so the figure and the repayment tool
    cannot disagree about the order of attack.

    Drawn as total interest and payoff, because the per-note bands are the
    chart the app already renders and a book figure has one page.
    """
    term, extra = 10, 250.0
    plain = ns["simulate_fixed_avalanche"](ROLL_LOANS, term)
    rolled = ns["simulate_fixed_avalanche"](ROLL_LOANS, term,
                                            extra_payments=((1, extra),))
    principal = sum(l["balance"] for l in ROLL_LOANS)
    rows = [("Required payments only", plain, MUTED),
            (f"Plus {money(extra)}, rolled down", rolled, GAIN)]

    PAD_L, TOP, BAR_H, GAP = 470, 208, 72, 46
    xmax = max(r["total_interest"] for _, r, _ in rows) * 1.30
    height = TOP + len(rows) * (BAR_H + GAP) + 250
    b = [text("h", 40, 52, "What rolling the money down buys"),
         text("sub", 40, 96, f"Four loans, {money(principal)} in all, "
                             f"5.5% to 12.5%"),
         text("sub", 40, 132, "Bars are the interest handed over.")]
    for i, (label, r, colour) in enumerate(rows):
        y = TOP + i * (BAR_H + GAP)
        w = (W - PAD_L - 200) * r["total_interest"] / xmax
        b.append(f'<rect x="{PAD_L}" y="{y}" width="{max(w, 3):.1f}" '
                 f'height="{BAR_H}" fill="{colour}"/>')
        b.append(text("lab", PAD_L - 22, y + BAR_H / 2 + 9, label, anchor="end",
                      budget=PAD_L - 56))
        b.append(text("num", PAD_L + w + 18, y + BAR_H / 2 + 10,
                      money(r["total_interest"])))
        b.append(text("ax", PAD_L - 22, y + BAR_H / 2 + 42,
                      f"clear in {r['payoff_years']:.1f} years", anchor="end",
                      fill=MUTED, budget=PAD_L - 56))
    y = TOP + len(rows) * (BAR_H + GAP) + 44
    saved = plain["total_interest"] - rolled["total_interest"]
    sooner = plain["payoff_years"] - rolled["payoff_years"]
    b.append(f'<line x1="40" y1="{y}" x2="{W - 40}" y2="{y}" stroke="{RULE}" stroke-width="2"/>')
    b.append(text("lab", 40, y + 52, f"{money(extra)} a month buys {money(saved)}"))
    b.append(text("lab", 40, y + 90, f"and {sooner:.1f} years."))
    b.append(text("ax", 40, y + 136, "The highest rate is attacked first, and a"))
    b.append(text("ax", 40, y + 172, "cleared note's payment rolls onto the next."))
    return write("roll-down", height, b,
                 f"plain {money(plain['total_interest'])} / "
                 f"{plain['payoff_years']:.1f}y vs rolled "
                 f"{money(rolled['total_interest'])} / {rolled['payoff_years']:.1f}y")



# ------------------------------------------------- Federal Student Aid, stock

# THE DEPARTMENT'S OWN BANDS, transcribed from Direct Loan Portfolio by Borrower
# Debt Size, FY2026 Q2 (the quarter ending 3/31/2026), read 2026-09-11. The
# source is a quarterly .xls download that is NOT in git, so the table lives
# here as literals for the same reason check_sai_worksheet.py holds the
# published SAI tables as literals: a figure a clone cannot draw is a figure
# nobody can check. `marketing/book/read_fsa_xls.py --report` re-derives them
# from the download when it is present.
#
# Dollars in billions, borrowers in millions, both as the Department rounds
# them. They are NOT re-rounded here: the shares below are computed from these
# figures exactly as published, so the curve is theirs and not ours.
DEBT_BANDS = (("$5k", 16.5, 5.9), ("$10k", 47.7, 6.6), ("$20k", 121.9, 8.4),
              ("$40k", 252.0, 8.9), ("$60k", 190.7, 3.9), ("$80k", 158.4, 2.3),
              ("$100k", 108.0, 1.2), ("$200k", 317.1, 2.3),
              ("$200k+", 318.6, 1.0))

# A DIFFERENT POPULATION AND A DIFFERENT DATE, which is the whole caveat on the
# lower strip. IDR Portfolio by Academic Level, FY2025 Q1 (3/31/2025), read the
# same day: the 12.6 million borrowers on an income-driven plan, not the 40.5
# million above. The Department publishes NO split of the debt bands by level,
# so these two halves cannot be drawn as one picture and are not.
LEVEL_SPLIT = (("Undergraduate only", 270.7, 8.7),
               ("Graduate only", 120.2, 1.1),
               ("Both", 327.6, 2.6))


def fig_who_owes_what(ns):
    """Balance owed, by borrower percentile. The quantile function, not the CDF.

    WHY THIS WAY ROUND, having drawn it the other way first. A CDF puts the
    dollar bands on the x axis, and the Department's bands are NOT evenly
    spaced: $5k, $10k, $20k, $40k, $60k, $80k, $100k, $200k. Drawn as nine
    equal slots the axis silently rescales the money, and drawn to scale the
    nine points bunch into the left tenth. Turning it over fixes both, because
    the axis that is genuinely continuous and evenly spaced is the POPULATION:
    every percentile is the same width by construction.

    It also states the finding in the shape rather than in a gap between two
    lines. Flat across two thirds of borrowers, then vertical. The reader does
    not have to subtract anything to see it.

    THE POINTS ARE BAND EDGES AND ONLY BAND EDGES. Nothing published says how
    balances are shaped inside a band, so the nine points are drawn and joined
    and the caption says they are the Department's own cuts. Reading a value
    between two of them is interpolation, which is why no gridline invites it.

    THE TOP IS OPEN, and that is the tail rather than a drawing problem.
    "$200k+" has no upper edge, so the curve leaves the frame at 97.5% instead
    of landing on a number, and the label carries what those borrowers hold.
    """
    dollars = [d for _, d, _ in DEBT_BANDS]
    people = [n for _, _, n in DEBT_BANDS]
    td, tp = sum(dollars), sum(people)
    edges = (5, 10, 20, 40, 60, 80, 100, 200)          # thousands, band upper edges

    x0, x1, top, bot = 118, 828, 186, 470
    ymax = 200.0
    xof = lambda p: x0 + p * (x1 - x0)
    yof = lambda k: bot - min(k, ymax) / ymax * (bot - top)

    b = [text("h", 40, 52, "Who owes what"),
         text("sub", 40, 84, "Every Direct Loan borrower, smallest balance to largest"),
         text("sub", 40, 112,
              f"{tp:.1f} million borrowers. ${td:,.0f} billion. March 2026.")]

    for k in (0, 50, 100, 150, 200):
        y = yof(k)
        b.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" '
                 f'stroke="{GRID}" stroke-width="2"/>')
        b.append(text("ax", x0 - 12, y + 9, f"${k}k" if k else "$0", anchor="end"))
    for p in (0, 0.25, 0.5, 0.75, 1.0):
        b.append(text("ax", xof(p), bot + 34, f"{p:.0%}", anchor="middle"))
    b.append(text("ax", (x0 + x1) / 2, bot + 68,
                  "Share of borrowers, ordered by what they owe", anchor="middle"))

    cum, pts = 0.0, [(xof(0), yof(0))]
    for i, k in enumerate(edges):
        cum += people[i] / tp
        pts.append((xof(cum), yof(k)))
    tail_x = pts[-1][0]
    b.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="5" '
             'stroke-linejoin="round"/>'
             % (" ".join(f"{x:.1f},{y:.1f}" for x, y in pts), GAIN))
    for x, y in pts[1:]:
        b.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="{GAIN}"/>')
    # The open top band: up and out of the frame rather than to a value.
    b.append(f'<line x1="{tail_x:.1f}" y1="{yof(200):.1f}" x2="{tail_x:.1f}" '
             f'y2="{top - 44}" stroke="{COST}" stroke-width="5" '
             f'stroke-dasharray="11 9"/>')
    b.append(f'<path d="M {tail_x - 11:.1f} {top - 40} L {tail_x:.1f} {top - 58} '
             f'L {tail_x + 11:.1f} {top - 40} Z" fill="{COST}"/>')

    half = sum(people[:3]) / tp                        # through the $20k band
    b.append(f'<line x1="{x0}" y1="{yof(20):.1f}" x2="{xof(half):.1f}" '
             f'y2="{yof(20):.1f}" stroke="{RULE}" stroke-width="3"/>')
    b.append(text("key", x0 + 14, yof(20) - 16,
                  f"{half:.0%} owe $20,000 or less", fill=INK))

    tail_p = people[-1] / tp
    b.append(text("key", tail_x - 16, top - 34,
                  f"the last {tail_p:.1%} owe more than $200,000",
                  anchor="end", fill=COST))
    b.append(text("key", tail_x - 16, top - 6,
                  f"and hold {dollars[-1] / td:.0%} of all the money",
                  anchor="end", fill=COST))

    # ---- The level split, deliberately a strip and not a second axis.
    sy = bot + 118
    b.append(text("lab", 40, sy, "By level, income-driven plans only"))
    shown = sum(n for _, _, n in LEVEL_SPLIT)
    b.append(text("ax", 40, sy + 30,
                  f"{shown:.1f} million of the 12.6 million on those plans, March 2025"))
    # THE COUNT SITS UNDER THE NAME, NOT IN A THIRD COLUMN. Drawn as columns,
    # the money label and the borrower count each fitted its own budget and
    # landed on top of each other, which is the overlap `text` cannot catch:
    # it measures one string against the canvas, never two against each other.
    widest = max(d * 1e9 / (n * 1e6) for _, d, n in LEVEL_SPLIT)
    bar_x, bar_max = 330, 380
    for j, (lab, d, n) in enumerate(LEVEL_SPLIT):
        y = sy + 66 + j * 76
        mean = d * 1e9 / (n * 1e6)
        w = (mean / widest) * bar_max
        b.append(text("lab", 40, y + 18, lab))
        b.append(text("ax", 40, y + 46, f"{n:.1f} million borrowers"))
        # Dark wherever GRADUATE borrowing is in the bar, so the two carrying
        # it read as one group against the one that does not.
        b.append(f'<rect x="{bar_x}" y="{y}" width="{w:.1f}" height="32" '
                 f'fill="{GAIN if lab.startswith("Undergraduate") else DEEP}"/>')
        b.append(text("num", bar_x + w + 16, y + 26, money(round(mean, -2)),
                      budget=W - (bar_x + bar_max + 16) - 20))
    # THE CHAPTER AND THE PICTURE MUST QUOTE THE SAME BANDS. Both figures the
    # introduction states are read straight off the Department's cuts here, so
    # a refreshed quarter that moves either one fails the guard rather than
    # leaving the prose describing last quarter's distribution.
    #
    # Both are STATUTORY-STYLE exact rather than rounded: they are the band
    # EDGES the Department publishes, not estimates of anything, so they carry
    # no tilde and the half-place rule must not be applied to them.
    claim("who-owes-what", "ch00", exact=("$20,000", "$200,000"),
          **{"$20,000": 20_000, "$200,000": 200_000})
    return write("who-owes-what", 940, b,
                 note=f"median band $20k; top {tail_p:.1%} hold "
                      f"{dollars[-1] / td:.1%} of the balance")


FIGURES = {"three-roads": fig_three_roads,
           "cap-ladder": fig_cap_ladder,
           "extra-dollar": fig_extra_dollar,
           "in-state": fig_in_state,
           "break-even": fig_break_even,
           "take-home": fig_take_home,
           "interest-only": fig_interest_only,
           "exposure": fig_exposure,
           "households": fig_households,
           "the-year": fig_the_year,
           "the-order": fig_the_order,
           "roll-down": fig_roll_down,
           "who-owes-what": fig_who_owes_what}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", action="append", choices=sorted(FIGURES))
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    if args.list:
        for name in sorted(FIGURES):
            print(f"  {name}")
        return
    ns = load_app()
    for name in (args.only or sorted(FIGURES)):
        print(name)
        FIGURES[name](ns)


if __name__ == "__main__":
    main()

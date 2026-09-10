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
    return ns


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
    room = budget if budget is not None else (
        x - 20 if anchor == "end" else W - x - 20 if anchor == "start"
        else 2 * min(x - 20, W - x - 20))
    if width > room:
        sys.exit(f"a {cls} line needs about {width:.0f} units and has {room:.0f}: "
                 f"{s!r}. Cut the copy rather than shrinking the type; an "
                 f"explanatory sentence belongs in the caption.")
    a = f' text-anchor="{anchor}"' if anchor != "start" else ""
    f = f' fill="{fill}"' if fill else ""
    return f'<text class="{cls}" x="{x:.1f}" y="{y:.1f}"{a}{f}>{esc(s)}</text>'


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
    path = OUT_DIR / f"book-{name}-{W}x{height}.svg"
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
        b.append(text("key", 80, y, label))
        y += 44
    b.append(f'<line x1="40" y1="{y + 6}" x2="{W - 40}" y2="{y + 6}" '
             f'stroke="{RULE}" stroke-width="2"/>')
    b.append(text("lab", 40, y + 54, f"A parent may borrow {money(plus)} more, "
                                     f"at a higher rate."))
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
    return write("in-state", height, b,
                 f"in {money(ins)} out {money(out)} gap {money(gap)} "
                 f"private {money(pm)} over {len(pub):,} publics")


FIGURES = {"three-roads": fig_three_roads,
           "cap-ladder": fig_cap_ladder,
           "extra-dollar": fig_extra_dollar,
           "in-state": fig_in_state}


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

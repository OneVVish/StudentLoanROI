#!/usr/bin/env python3
"""The two-panel refinance figure for the refinancing guide.

    python3 brand/build_guide_refi_chart.py

WHAT IT DRAWS. Two stacked panels against the same income axis, for
content/posts/refinancing-federal-student-loans.md. The top panel is the
monthly payment on each path; the bottom is that payment as a share of gross
income. Both carry one federal line and one refinanced line.

THE MECHANISM IT EXISTS TO SHOW. The federal payment is a FUNCTION OF INCOME
and the refinanced payment is a CONSTANT. Every other claim in the guide falls
out of that one difference, and a table of three incomes cannot show a function.
The guide's table gives three points; this gives the shape between and beyond
them.

IT ADDS A FACT THE TABLE DOES NOT CARRY. The two lines cross. Below the
crossing the refinanced payment is the larger one, and above it the refinanced
payment is smaller as well as cheaper overall. The table stops at $80,000 and
so stops just short of the crossing, which would let a reader infer the
refinanced payment is always higher. It is not, and the figure says so.

EVERY NUMBER COMES FROM app.py, through the section 1-2 exec prefix that
analyze_model.py and build_guide_plan_chart.py both use:

    federal      calculate_rap_payment(agi, dependents=0)
    refinanced   calculate_standard_repayment(BALANCE, OFFER_PCT, 10)

THE BALANCE AND THE OFFER ARE THE GUIDE'S OWN, so the figure and the prose
describe one borrower. The federal rate does not appear on the figure at all:
RAP's payment is set by income rather than by the rate, so drawing an 8.5
percent label beside the federal line would imply the line moves with it.

THE OFFER IS AN ASSUMPTION AND THE FIGURE SAYS SO. There is no private
refinance rate dataset anywhere in this repo, and there is not going to be one.
The subtitle names the offer as an offer for that reason, and no lender is
named here or in the guide: this project has no source for what any lender
charges and no way to keep one current, which is the line
private_structure_disclosure already holds in the app.

RAP IS PIECEWISE LINEAR WITH A JUMP AT EACH BAND EDGE, not a smooth curve and
not a staircase of flat treads. Within a band the percentage is fixed, so the
payment is that percentage OF INCOME and therefore rises across the band; at
the edge the percentage changes and the payment jumps. Drawn with flat treads
it would understate the payment everywhere except the band edges.

NOTHING BELOW 26 UNITS, and the canvas is 900 wide, for the reason
build_guide_plan_chart.py records: a guide body figure is displayed at roughly
610px on a desktop and 342px on a phone, so what decides legibility is the
ratio of type to canvas width. The panels stack rather than sitting side by
side so each gets the full column.

THE PANELS DO NOT SHARE A Y-SCALE, and that is deliberate rather than an
oversight. They are in different units, dollars and percent, so a shared scale
would be meaningless. What they DO share is the x-axis, which is what lets a
reader carry the crossing from one panel to the other.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "app.py"

# The guide's borrower. Change these and the guide's table must change with them.
BALANCE = 50_000
OFFER_PCT = 6.0
OFFER_YEARS = 10

AGI_MIN, AGI_MAX = 20_000, 100_000

W = 900
PAD_L, PAD_R = 132, 62
HEAD = 208
PANEL_TITLE = 46
PLOT_H = 248
PANEL_GAP = 84
AXIS_LABEL_DY = 76     # "Your income" baseline, below a panel's own base rule
AXIS_LABEL_DESC = 10   # how far that 27px label descends below its baseline
NOTE_LEAD = 38         # line height for the two footer notes

INK, MUTED, GRID, RULE = "#14161a", "#5c636d", "#eceff2", "#dfe3e8"
FED_C, REFI_C = "#2a78d6", "#eb6834"


# TEXT WIDTH IS MEASURED, NOT COUNTED, and this file exists because it was
# counted first. The subtitle ran off the right edge of the canvas in the very
# first render: an SVG <text> does not wrap, does not shrink and does not warn,
# it simply draws past the viewBox and the browser clips it. Nothing in the
# build could see it and check_content cannot either, because the file is valid
# XML and the image is present.
#
# These advances are approximate, which is all that is needed: the check is
# "does this line fit inside the canvas", not "where exactly does it end", and
# every string here is checked with a real margin left over.
_ADV = {"narrow": 0.28, "digit": 0.56, "upper": 0.66, "lower": 0.50, "space": 0.26}


def text_width(s: str, px: float) -> float:
    """Approximate rendered width of a string at a given font size."""
    total = 0.0
    for ch in s:
        if ch == " ":
            total += _ADV["space"]
        elif ch.isdigit():
            total += _ADV["digit"]
        elif ch.isupper():
            total += _ADV["upper"]
        elif ch.islower():
            total += _ADV["lower"]
        else:
            total += _ADV["narrow"]
    return total * px


def fits(s: str, px: float, x: float, right_margin: float = 40.0) -> str:
    """Return s, or exit naming the overflow. Called on every free text line."""
    end = x + text_width(s, px)
    if end > W - right_margin:
        sys.exit(f"text overflows the {W}-unit canvas by {end - (W - right_margin):.0f} "
                 f"units at {px}px: {s!r}\n"
                 f"  shorten it or split it across two lines; an SVG will clip it "
                 f"silently rather than wrap")
    return s


def load_app():
    src = APP.read_text()
    m = re.search(r"^# =+\n# 3\. PAGE CONFIG & SESSION STATE", src, re.M)
    if not m:
        sys.exit("app.py's section 3 banner moved; this reads the prefix above it.")
    ns = {"__name__": "app_prefix"}
    exec(compile(src[:m.start()], str(APP), "exec"), ns)
    return ns


def rap_points(ns):
    """RAP monthly payment across the income range, as (agi, payment) vertices.

    One pair per band: the payment just inside the band's lower edge and at its
    upper edge. The jump between bands is drawn as a real discontinuity rather
    than smoothed over, because that is what the schedule does.
    """
    pay = ns["calculate_rap_payment"]
    segments, lo = [], AGI_MIN
    while lo < AGI_MAX:
        hi = min((lo // 10_000 + 1) * 10_000, AGI_MAX)
        segments.append([(lo, pay(lo)["monthly_payment"]),
                         (hi, pay(hi)["monthly_payment"])])
        lo = hi
    return segments


def crossing(ns, refi_monthly):
    """The income where RAP first asks at least what the refinanced loan does."""
    pay = ns["calculate_rap_payment"]
    for agi in range(AGI_MIN, AGI_MAX + 1, 50):
        if pay(agi)["monthly_payment"] >= refi_monthly:
            return agi
    return None


def main():
    ns = load_app()
    # The two guideline bands are app.py's OWN constants, already cited there
    # and already shipped in the calculator: 10 percent of gross from student
    # loan budgeting guidance, and 36 percent from the back-end debt-to-income
    # ceiling mortgage lenders apply to ALL debts combined. Read them rather
    # than typing them, so the figure cannot drift from the app.
    band_ok = float(ns["LOAN_TO_INCOME_GROSS_MANAGEABLE_PCT"])
    band_max = float(ns["LOAN_TO_INCOME_GROSS_CAUTION_PCT"])
    refi_monthly = float(ns["calculate_standard_repayment"](
        BALANCE, OFFER_PCT, term_years=OFFER_YEARS)["monthly_payment"])
    segments = rap_points(ns)
    cross = crossing(ns, refi_monthly)

    # THE BOTTOM BLOCKS STACK, SO THEIR POSITIONS ARE ARITHMETIC, NOT TASTE.
    # The first version guessed a FOOT constant and got a height formula that
    # budgeted one PANEL_GAP while the loop below consumed one after EVERY
    # panel, so the footer note was drawn straight through the last panel's
    # "Your income" label. Derive every y from the one before it instead, and
    # assert the clearance rather than eyeballing it.
    n_panels = 2
    panel_h = PANEL_TITLE + PLOT_H
    last_bottom = HEAD + n_panels * panel_h + (n_panels - 1) * PANEL_GAP
    axis_label_y = last_bottom + AXIS_LABEL_DY          # its baseline
    note_y1 = axis_label_y + AXIS_LABEL_DESC + NOTE_LEAD
    note_y2 = note_y1 + NOTE_LEAD
    height = int(note_y2 + NOTE_LEAD)
    if note_y1 <= axis_label_y + AXIS_LABEL_DESC:
        sys.exit("the footer note would overlap the last panel's axis label")
    plot_w = W - PAD_L - PAD_R

    def sx(agi):
        return PAD_L + (agi - AGI_MIN) / (AGI_MAX - AGI_MIN) * plot_w

    out = []
    a = out.append
    a('<?xml version="1.0" encoding="UTF-8"?>')
    a(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" '
      f'width="{W}" height="{height}" role="img" aria-labelledby="rf-t rf-d">')
    a('  <title id="rf-t">The federal payment moves with income and the refinanced '
      'payment does not</title>')
    a('  <desc id="rf-d">Two stacked panels sharing an income axis from twenty thousand '
      'to one hundred thousand dollars. The upper panel plots the monthly payment on a '
      'fifty thousand dollar balance: the federal line rises in steps with income, while '
      'the refinanced line is flat because a fixed payment does not know what the '
      f'borrower earns. The two cross near {cross:,} dollars of income. The lower panel '
      'plots the same payments as a share of gross income, where the refinanced line '
      'falls steeply as income rises and the federal line climbs gently, so at the '
      'lowest incomes the refinanced payment takes several times the share the federal '
      'one does.</desc>')
    a('  <style>')
    a('    .f { font-family: "Avenir Next", -apple-system, "Segoe UI", Roboto, '
      '"Helvetica Neue", sans-serif; }')
    a(f'    .deck {{ font-size: 30px; font-weight: 600; fill: {INK}; }}')
    a(f'    .key {{ font-size: 27px; font-weight: 500; fill: {INK}; }}')
    a(f'    .ptitle {{ font-size: 28px; font-weight: 700; fill: {INK}; }}')
    a(f'    .tick {{ font-size: 26px; font-weight: 500; fill: {MUTED}; }}')
    a(f'    .axis {{ font-size: 27px; font-weight: 500; fill: {MUTED}; }}')
    a(f'    .note {{ font-size: 26px; font-weight: 500; fill: {MUTED}; }}')
    a(f'    .mark-word {{ font-size: 19px; font-weight: 700; fill: {INK}; }}')
    a(f'    .mark-tld {{ font-size: 19px; font-weight: 500; fill: {MUTED}; }}')
    a('  </style>')
    a(f'  <rect width="{W}" height="{height}" fill="#ffffff"/>')

    a('  <g transform="translate(40 20) scale(0.3)">')
    a(f'    <path d="M 8 38 L 24 54 L 35.826 38" fill="none" stroke="{REFI_C}" '
      'stroke-width="9.5" stroke-linecap="round" stroke-linejoin="round"/>')
    a(f'    <path d="M 35.826 38 L 58 8" fill="none" stroke="{FED_C}" '
      'stroke-width="9.5" stroke-linecap="round"/>')
    a('  </g>')
    a('  <text class="f" x="66" y="37"><tspan class="mark-word">worthmydegree</tspan>'
      '<tspan class="mark-tld">.com</tspan></text>')
    deck = fits("One payment follows your income. One does not.", 30, 40)
    a(f'  <text class="f deck" x="40" y="88">{deck}</text>')
    sub = fits(f"A ${BALANCE:,} balance, against an offer of {OFFER_PCT:g} percent",
               27, 40)
    a(f'  <text class="f axis" x="40" y="130">{sub}</text>')

    for colour, label, x in ((FED_C, "Federal (RAP)", 40), (REFI_C, "Refinanced", 420)):
        a(f'  <line x1="{x}" y1="172" x2="{x + 36}" y2="172" stroke="{colour}" '
          'stroke-width="5" stroke-linecap="round"/>')
        a(f'  <text class="f key" x="{x + 46}" y="181">{label}</text>')

    panels = [
        {"title": "What you pay each month",
         "fed": [[(g, p) for g, p in seg] for seg in segments],
         "refi": [[(AGI_MIN, refi_monthly), (AGI_MAX, refi_monthly)]],
         "fmt": lambda v: f"${v:,.0f}",
         "ticks": [0, 200, 400, 600, 800]},
        {"title": "What that is, out of every dollar earned",
         "fed": [[(g, p * 12 / g * 100) for g, p in seg] for seg in segments],
         "refi": [[(g, refi_monthly * 12 / g * 100)
                   for g in range(AGI_MIN, AGI_MAX + 1, 1000)]],
         "fmt": lambda v: f"{v:g}%",
         "ticks": [0, 10, 20, 30, 40],
         # Labels sit at the LEFT, where neither curve is near either band:
         # at $20,000 the federal line is at about 1 percent and the
         # refinanced one at about 33, so the gap around 10 and just above 36
         # is empty. At the right they would land on top of both curves.
         "bands": [(band_ok, "Budgeting guideline, 10%"),
                   (band_max, "All-debt ceiling, 36%")]},
    ]

    y = HEAD
    for panel in panels:
        top = y + PANEL_TITLE
        bottom = top + PLOT_H
        hi = max(panel["ticks"])

        def sy(v, top=top, hi=hi):
            return bottom - min(v, hi) / hi * PLOT_H

        a(f'  <text class="f ptitle" x="40" y="{y + 30}">'
          f'{fits(panel["title"], 28, 40)}</text>')
        for t in panel["ticks"]:
            gy = sy(t)
            a(f'  <line x1="{PAD_L}" y1="{gy:.1f}" x2="{W - PAD_R}" y2="{gy:.1f}" '
              f'stroke="{GRID}" stroke-width="2"/>')
            a(f'  <text class="f tick" x="{PAD_L - 16}" y="{gy + 9:.1f}" '
              f'text-anchor="end">{panel["fmt"](t)}</text>')
        a(f'  <line x1="{PAD_L}" y1="{bottom}" x2="{W - PAD_R}" y2="{bottom}" '
          f'stroke="{RULE}" stroke-width="3"/>')

        for value, label in panel.get("bands", ()):
            by = sy(value)
            a(f'  <line x1="{PAD_L}" y1="{by:.1f}" x2="{W - PAD_R}" y2="{by:.1f}" '
              f'stroke="{MUTED}" stroke-width="3" stroke-dasharray="10 8"/>')
            a(f'  <text class="f note" x="{PAD_L + 10}" y="{by - 12:.1f}">'
              f'{fits(label, 26, PAD_L + 10)}</text>')

        for colour, key in ((FED_C, "fed"), (REFI_C, "refi")):
            for seg in panel[key]:
                pts = " ".join(f"{sx(g):.1f},{sy(v):.1f}" for g, v in seg)
                a(f'  <polyline points="{pts}" fill="none" stroke="{colour}" '
                  'stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>')

        for agi in range(AGI_MIN, AGI_MAX + 1, 20_000):
            a(f'  <text class="f tick" x="{sx(agi):.1f}" y="{bottom + 36}" '
              f'text-anchor="middle">${agi // 1000}k</text>')
        a(f'  <text class="f axis" x="{PAD_L + plot_w / 2:.0f}" '
          f'y="{bottom + AXIS_LABEL_DY}" text-anchor="middle">Your income</text>')
        y = bottom + PANEL_GAP    # only between panels; see the stack above

    note_a = fits(f"The monthly payments cross near ${cross:,} of income.", 26, 40)
    note_b = fits("Below that, the refinanced payment is the larger one.", 26, 40)
    a(f'  <text class="f note" x="40" y="{note_y1:.0f}">{note_a}</text>')
    a(f'  <text class="f note" x="40" y="{note_y2:.0f}">{note_b}</text>')
    a('</svg>')

    svg = "\n".join(out) + "\n"
    # An <img> renders an invalid SVG as nothing at all: no console error, no failing
    # guard, just blank space. A duplicate attribute and a double hyphen inside a
    # comment are both XML errors and both easy to write, so this parses before it
    # writes.
    import xml.etree.ElementTree as ET
    try:
        ET.fromstring(svg)
    except ET.ParseError as exc:
        sys.exit(f"refusing to write invalid SVG: {exc}")

    out_path = REPO / "static" / f"guide-refi-vs-federal-{W}x{height}.svg"
    out_path.write_text(svg)
    print(f"wrote {out_path}  ({out_path.stat().st_size:,} bytes, {W}x{height})")
    print(f"  refinanced: ${refi_monthly:,.2f}/mo on ${BALANCE:,} at {OFFER_PCT:g}% "
          f"over {OFFER_YEARS}y")
    print(f"  crossing:   ~${cross:,} of income")
    for agi in (30_000, 50_000, 80_000, 100_000):
        r = ns["calculate_rap_payment"](agi)["monthly_payment"]
        print(f"  at ${agi:>7,}:  federal ${r:>7.2f}  ({r*12/agi*100:>4.1f}% of gross)"
              f"   refinanced ${refi_monthly:>7.2f}  ({refi_monthly*12/agi*100:>4.1f}%)")


if __name__ == "__main__":
    main()

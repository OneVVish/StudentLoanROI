#!/usr/bin/env python3
"""The stacked plan-comparison figure for the 2026 repayment guide.

    python3 brand/build_guide_plan_chart.py

WHAT IT DRAWS. Monthly payment against adjusted gross income, one panel per
household size, three lines each: IBR at 10 percent, IBR at 15 percent, and
RAP. Written as SVG rather than a raster because a guide body figure is real
text in the page, and because these lines are exact geometry rather than a
sampled curve (see below).

EVERY NUMBER COMES FROM app.py, through the section 1-2 exec prefix that
analyze_model.py uses. Nothing here reimplements a payment:

    RAP      calculate_rap_payment(agi, dependents)
    IBR      max(agi - idr_income_allowance(size), 0) / 12 * rate

THE TWO PLANS COUNT HOUSEHOLD DIFFERENTLY AND THAT IS THE TRAP. RAP takes
DEPENDENTS and subtracts a flat $50 each. IBR takes FAMILY SIZE and shelters
150% of that household's poverty guideline. A panel headed "household of N"
therefore asks IBR for N and RAP for N-1, so all three lines describe the same
family. MIN_FAMILY_SIZE is 1, so there is no household of zero.

THE LINES ARE DRAWN EXACTLY, NOT SAMPLED. Both IBR variants are two straight
segments: flat at zero up to the allowance, then linear. RAP is piecewise
linear with a jump at each $10,000 band edge, because within a band the
percentage is fixed and the payment is that percentage of AGI. So the whole
figure is about sixty line segments and needs no sampling resolution.

NOTHING BELOW 26 UNITS. The canvas is 900 wide because a guide body figure is
displayed at roughly 610px on a desktop and 342px on a phone, and what decides
legibility is the ratio of type to canvas width. Stacking the panels rather
than gridding them is what makes this readable at 342px: each panel gets the
full column instead of a third of it.

NO CLIPPED LINE. The panels share one y-scale set by the largest payment any
line reaches, so a reader comparing two panels is comparing equal heights, and
the 15 percent line is never cut off at the top of its own panel.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "app.py"
OUT = REPO / "static" / "guide-plan-by-agi-900x1902.svg"

AGI_MAX = 150_000
SIZES = [1, 2, 3, 4, 5]          # people in the household, borrower included

W = 900
PAD_L, PAD_R = 128, 62           # left pad carries the money axis labels,
                                 # right pad keeps the $150k label off the edge
HEAD = 196                       # wordmark, deck and key
PANEL_TITLE = 44
PLOT_H = 208
PANEL_GAP = 70                   # clears this panel's x labels AND the next title
FOOT = 96

INK, MUTED, GRID, RULE = "#14161a", "#5c636d", "#eceff2", "#dfe3e8"
NEW_C, OLD_C, RAP_C = "#12335c", "#eb6834", "#2a78d6"


def load_app():
    src = APP.read_text()
    m = re.search(r"^# =+\n# 3\. PAGE CONFIG & SESSION STATE", src, re.M)
    if not m:
        sys.exit("app.py's section 3 banner moved; this reads the prefix above it.")
    ns = {"__name__": "app_prefix"}
    exec(compile(src[:m.start()], str(APP), "exec"), ns)
    return ns


def rap_points(ns, dependents):
    """RAP as exact vertices. Flat at the floor to $10,000, then one straight
    run per $10,000 band, with a jump at each edge because the percentage
    steps."""
    pts = [(0, ns["calculate_rap_payment"](0, dependents)["monthly_payment"])]
    pts.append((10_000, ns["calculate_rap_payment"](10_000, dependents)["monthly_payment"]))
    edge = 10_000
    while edge < AGI_MAX:
        lo, hi = edge + 1, min(edge + 10_000, AGI_MAX)
        pts.append((lo, ns["calculate_rap_payment"](lo, dependents)["monthly_payment"]))
        pts.append((hi, ns["calculate_rap_payment"](hi, dependents)["monthly_payment"]))
        edge += 10_000
    return pts


def ibr_points(allowance, rate):
    return [(0, 0.0), (allowance, 0.0),
            (AGI_MAX, max(AGI_MAX - allowance, 0) / 12.0 * rate)]


def main():
    ns = load_app()
    panels = []
    for size in SIZES:
        allowance = ns["idr_income_allowance"](size)
        panels.append({
            "size": size,
            "dependents": size - 1,
            "allowance": allowance,
            "new": ibr_points(allowance, ns["IDR_PAYMENT_RATE"]),
            "old": ibr_points(allowance, ns["OLD_IBR_PAYMENT_RATE"]),
            "rap": rap_points(ns, size - 1),
        })

    ymax = max(y for p in panels for series in ("new", "old", "rap")
               for _, y in p[series])
    ystep = 400
    ytop = (int(ymax // ystep) + 1) * ystep          # a round top, nothing clipped
    height = HEAD + len(panels) * (PANEL_TITLE + PLOT_H + PANEL_GAP) + FOOT

    def sx(agi):
        return PAD_L + (W - PAD_L - PAD_R) * agi / AGI_MAX

    out = []
    a = out.append
    a(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" '
      f'width="{W}" height="{height}" role="img" aria-labelledby="pa-t pa-d">')
    a('  <title id="pa-t">Monthly payment against income, by household size, on three '
      'income-driven plans</title>')
    a('  <desc id="pa-d">Five stacked panels, one per household size from one to five. Each '
      'plots the monthly payment against adjusted gross income for IBR at 10 percent, IBR at 15 '
      'percent and RAP. RAP rises in steps because it charges a fixed percentage within each '
      'ten thousand dollar band. Both IBR lines stay at zero until income passes the amount the '
      'household shelters, which grows with each person, while RAP subtracts a flat fifty '
      'dollars per dependent and never falls below ten dollars.</desc>')
    a('  <style>')
    a('    .f { font-family: "Avenir Next", -apple-system, "Segoe UI", Roboto, '
      '"Helvetica Neue", sans-serif; }')
    a(f'    .deck {{ font-size: 30px; font-weight: 600; fill: {INK}; }}')
    a(f'    .key {{ font-size: 27px; font-weight: 500; fill: {INK}; }}')
    a(f'    .ptitle {{ font-size: 28px; font-weight: 700; fill: {INK}; }}')
    a(f'    .tick {{ font-size: 26px; font-weight: 500; fill: {MUTED}; }}')
    a(f'    .axis {{ font-size: 27px; font-weight: 500; fill: {MUTED}; }}')
    a(f'    .mark-word {{ font-size: 19px; font-weight: 700; fill: {INK}; }}')
    a(f'    .mark-tld {{ font-size: 19px; font-weight: 500; fill: {MUTED}; }}')
    a('  </style>')
    a(f'  <rect width="{W}" height="{height}" fill="#ffffff"/>')

    a('  <g transform="translate(40 20) scale(0.3)">')
    a(f'    <path d="M 8 38 L 24 54 L 35.826 38" fill="none" stroke="{OLD_C}" '
      'stroke-width="9.5" stroke-linecap="round" stroke-linejoin="round"/>')
    a(f'    <path d="M 35.826 38 L 58 8" fill="none" stroke="{RAP_C}" '
      'stroke-width="9.5" stroke-linecap="round"/>')
    a('  </g>')
    a('  <text class="f" x="66" y="37"><tspan class="mark-word">worthmydegree</tspan>'
      '<tspan class="mark-tld">.com</tspan></text>')
    a(f'  <text class="f deck" x="40" y="84">What each plan asks for, by household size</text>')

    a('  <text class="f axis" x="40" y="124">Monthly payment against adjusted gross '
      'income</text>')
    key = [(OLD_C, "IBR, older loans", 40), (NEW_C, "IBR, newer loans", 340), (RAP_C, "RAP", 640)]
    for colour, label, x in key:
        a(f'  <line x1="{x}" y1="166" x2="{x + 36}" y2="166" stroke="{colour}" '
          'stroke-width="5" stroke-linecap="round"/>')
        a(f'  <text class="f key" x="{x + 46}" y="175">{label}</text>')

    for idx, p in enumerate(panels):
        top = HEAD + idx * (PANEL_TITLE + PLOT_H + PANEL_GAP)
        base = top + PANEL_TITLE + PLOT_H

        def sy(v):
            return base - PLOT_H * v / ytop

        dep = p["dependents"]
        noun = "borrower alone" if dep == 0 else \
               f"borrower plus {dep} dependent{'' if dep == 1 else 's'}"
        a(f'  <text class="f ptitle" x="40" y="{top + 30}">'
          f'Household of {p["size"]}: {noun}</text>')

        for v in range(0, ytop + 1, ystep):
            a(f'  <line x1="{PAD_L}" y1="{sy(v):.1f}" x2="{W - PAD_R}" y2="{sy(v):.1f}" '
              f'stroke="{GRID}" stroke-width="1.5"/>')
            a(f'  <text class="f tick" x="{PAD_L - 12}" y="{sy(v) + 9:.1f}" '
              f'text-anchor="end">${v:,}</text>')
        ax = sx(p["allowance"])
        a(f'  <line x1="{ax:.1f}" y1="{sy(0):.1f}" x2="{ax:.1f}" y2="{sy(ytop):.1f}" '
          f'stroke="{RULE}" stroke-width="1.5" stroke-dasharray="4 6"/>')

        for series, colour, width in (("old", OLD_C, 4.5), ("new", NEW_C, 4.5),
                                      ("rap", RAP_C, 5)):
            d = " ".join(f"{'M' if i == 0 else 'L'} {sx(x):.1f} {sy(y):.1f}"
                         for i, (x, y) in enumerate(p[series]))
            a(f'  <path d="{d}" fill="none" stroke="{colour}" stroke-width="{width}" '
              'stroke-linecap="round" stroke-linejoin="round"/>')

        for agi in (0, 50_000, 100_000, 150_000):
            label = "$0" if agi == 0 else f"${agi // 1000}k"
            a(f'  <text class="f tick" x="{sx(agi):.1f}" y="{base + 34}" '
              f'text-anchor="middle">{label}</text>')

    # Shortened after the first render ran it off the right edge. Text width
    # depends on the string, so a line that fits is not a rule that fits.
    a(f'  <text class="f axis" x="40" y="{height - 44}">Adjusted gross income. The dotted '
      'line is the sheltered amount</text>')
    a('</svg>')

    svg = "\n".join(out) + "\n"
    # An <img> renders an invalid SVG as nothing at all: no console error, no failing
    # guard, just blank space. A duplicate attribute and a "--" inside a comment are
    # both XML errors and both easy to write, so this parses before it writes.
    import xml.etree.ElementTree as ET
    try:
        ET.fromstring(svg)
    except ET.ParseError as exc:
        sys.exit(f"refusing to write invalid SVG: {exc}")
    OUT.write_text(svg)
    print(f"wrote {OUT}  ({OUT.stat().st_size:,} bytes, {W}x{height})")
    for p in panels:
        r = dict(p["rap"])[AGI_MAX]
        print(f"  household {p['size']}: shelters ${p['allowance']:>8,.0f} | "
              f"at $150k  RAP ${r:>7.2f}  new ${p['new'][-1][1]:>7.2f}  "
              f"old ${p['old'][-1][1]:>7.2f}")


if __name__ == "__main__":
    main()

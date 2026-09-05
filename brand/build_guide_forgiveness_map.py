#!/usr/bin/env python3
"""Guide body figure: where riding forgiveness out costs less than paying it
off, under IBR and under RAP, as two STACKED panels sized for a phone.

    python3 brand/build_guide_forgiveness_map.py

WHY A SECOND DRAWING OF A PUBLISHED CHART. The gallery infographic
(forgiveness-map-pair) puts the two plans side by side, which is right on a
desktop and wrong in a guide: an article figure renders at about 342px on a
phone, so two panels across would give each 170px. CLAUDE.md's rule for guide
bodies is stack, never grid, with nothing below 26 units of type on a 900-unit
canvas. So this draws the same two regions one above the other.

SAME ARITHMETIC AS THE GALLERY CHART, DELIBERATELY. The boundaries are solved
the same way from the same app.py functions: for each balance, scan a grid of
incomes for every sign change in (riding all-in minus Standard), then refine
each bracket, never bisect from the ends, because the cost curve is not
monotonic in income and RAP crosses twice. Two drawings of one solver cannot
disagree about where a line sits.

THE PICTURE CARRIES NO EXPLANATORY SENTENCES. In a guide those are real text
in the caption paragraph below the image, where they scale, select and
translate; inside the SVG they were 7px on a phone.

Parses its own output before writing: an <img> renders an invalid SVG as
nothing at all, with no console error and check_content still green.
"""
import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "app.py"

RATE, GROWTH = 7.0, 0.03
LO_BAL, HI_BAL, BAL_STEP = 20_000, 400_000, 20_000
INC_LO, INC_HI, INC_STEP = 15_000, 420_000, 15_000
PANEL_TOP = 400_000

W = 900
PAD_L, PAD_R = 128, 62
HEAD = 200
PANEL_TITLE = 62   # the title sat on the $400k tick at 48
PLOT_H = 340
PANEL_GAP = 78
FOOT = 96
HEIGHT = HEAD + 2 * (PANEL_TITLE + PLOT_H + PANEL_GAP) + FOOT
OUT = REPO / "static" / f"guide-forgiveness-map-900x{HEIGHT}.svg"

INK, MUTED, GRID = "#14161a", "#5c636d", "#ffffff"
CHEAP, DEAR = "#bcd3ee", "#f5c4ac"        # riding costs less / costs more, on white
EDGE = "#14161a"


def load_app():
    """app.py's pure prefix plus its later pure functions: the repayment
    simulators are in section 2 but discharge_tax_estimate sits in 2m, below
    the section 3 banner, which is why the second pass exists."""
    src = APP.read_text()
    m = re.search(r"^# =+\n# 3\. PAGE CONFIG & SESSION STATE", src, re.M)
    if not m:
        sys.exit("app.py's section 3 banner moved; this reads the prefix above it.")
    ns = {"__name__": "app_prefix"}
    exec(compile(src[:m.start()], str(APP), "exec"), ns)
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name not in ns:
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(APP), "exec"), ns)
    return ns


def payments_made(result):
    s = result["schedule"]
    return float(s["payment"].sum()) if "payment" in s.columns else \
        float(result.get("monthly_payment", 0.0)) * len(s)


def solve(ns):
    std, idr, rap = ns["calculate_standard_repayment"], ns["calculate_idr_repayment"], ns["simulate_rap_schedule"]
    allow, tax = ns["idr_income_allowance"], ns["discharge_tax_estimate"]

    def total(plan, balance, income):
        if plan == "rap":
            r = rap(balance, RATE, "x", annual_income=income, income_growth=GROWTH)
        else:
            r = idr(balance, RATE, "x", annual_income=income, income_growth=GROWTH,
                    living_adjustment=allow(1), payment_rate=0.10, max_term_years=20)
        forgiven = r.get("forgiven_amount", 0.0) or 0.0
        t = tax(forgiven, income, r["payoff_years"], income_growth=GROWTH)["tax"] if forgiven > 0 else 0.0
        return payments_made(r) + t

    def boundaries(plan, balance):
        standard = payments_made(std(balance, RATE, term_years=10))
        grid = list(range(INC_LO, INC_HI + 1, INC_STEP))
        cheap = [total(plan, balance, i) < standard for i in grid]
        edges = []
        for a in range(1, len(grid)):
            if cheap[a] == cheap[a - 1]:
                continue
            lo, hi = grid[a - 1], grid[a]
            for _ in range(16):
                mid = (lo + hi) / 2
                if (total(plan, balance, mid) < standard) == cheap[a - 1]:
                    lo = mid
                else:
                    hi = mid
            edges.append((lo + hi) / 2)
        return cheap[0], edges

    out = {}
    for plan in ("idr", "rap"):
        out[plan] = [dict(balance=b, **dict(zip(("starts_cheap", "edges"), boundaries(plan, b))))
                     for b in range(LO_BAL, HI_BAL + 1, BAL_STEP)]
    return out


def cheap_spans(row):
    """(lo, hi) income spans where riding is cheaper, at one balance."""
    marks = [0.0] + row["edges"] + [float(PANEL_TOP)]
    cheap, spans = row["starts_cheap"], []
    for lo, hi in zip(marks, marks[1:]):
        if cheap and hi > lo:
            spans.append((lo, min(hi, PANEL_TOP)))
        cheap = not cheap
    return spans


def main():
    ns = load_app()
    data = solve(ns)
    if max(len(r["edges"]) for r in data["idr"]) != 1:
        sys.exit("IBR no longer crosses exactly once at every balance; the caption "
                 "in the guide describes a single line and is no longer true")

    def sx(bal):
        return PAD_L + (W - PAD_L - PAD_R) * (bal - LO_BAL) / (HI_BAL - LO_BAL)

    out = []
    a = out.append
    a(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {HEIGHT}" '
      f'width="{W}" height="{HEIGHT}" role="img" aria-labelledby="fm-t fm-d">')
    a('  <title id="fm-t">Where riding forgiveness out costs less than paying the loan off, under IBR and under RAP</title>')
    a('  <desc id="fm-d">Two stacked panels with balance along the bottom and adjusted gross income up the '
      'side, shaded blue where riding an income-driven plan to a taxed discharge costs less than the ten-year '
      'Standard plan and orange where it costs more. The IBR panel has one boundary rising with balance: blue '
      'below, orange above. The RAP panel has two: a blue wedge at the bottom, an orange band in the middle, and '
      'blue again above, because RAP has no payment ceiling and a high earner overpays and beats the Standard '
      'plan outright.</desc>')
    a('  <style>')
    a('    .f { font-family: "Avenir Next", -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif; }')
    a(f'    .deck {{ font-size: 30px; font-weight: 600; fill: {INK}; }}')
    a(f'    .key {{ font-size: 27px; font-weight: 500; fill: {INK}; }}')
    a(f'    .ptitle {{ font-size: 28px; font-weight: 700; fill: {INK}; }}')
    a(f'    .tick {{ font-size: 26px; font-weight: 500; fill: {MUTED}; }}')
    a(f'    .axis {{ font-size: 27px; font-weight: 500; fill: {MUTED}; }}')
    a(f'    .mark-word {{ font-size: 19px; font-weight: 700; fill: {INK}; }}')
    a(f'    .mark-tld {{ font-size: 19px; font-weight: 500; fill: {MUTED}; }}')
    a('  </style>')
    a(f'  <rect width="{W}" height="{HEIGHT}" fill="#ffffff"/>')
    a('  <g transform="translate(40 20) scale(0.3)">')
    a('    <path d="M 8 38 L 24 54 L 35.826 38" fill="none" stroke="#eb6834" stroke-width="9.5" stroke-linecap="round" stroke-linejoin="round"/>')
    a('    <path d="M 35.826 38 L 58 8" fill="none" stroke="#2a78d6" stroke-width="9.5" stroke-linecap="round"/>')
    a('  </g>')
    a('  <text class="f" x="66" y="37"><tspan class="mark-word">worthmydegree</tspan><tspan class="mark-tld">.com</tspan></text>')
    a('  <text class="f deck" x="40" y="84">Ride it out, or pay it off?</text>')
    a('  <text class="f axis" x="40" y="124">At 7 percent, by what you owe and what you earn</text>')
    for colour, label, x in ((CHEAP, "Riding costs less", 40), (DEAR, "Riding costs more", 420)):
        a(f'  <rect x="{x}" y="150" width="36" height="26" fill="{colour}" stroke="{EDGE}" stroke-width="1.5"/>')
        a(f'  <text class="f key" x="{x + 48}" y="172">{label}</text>')

    titles = {"idr": "IBR: loans made before July 1, 2026", "rap": "RAP: every loan made since"}
    for idx, plan in enumerate(("idr", "rap")):
        rows = data[plan]
        top = HEAD + idx * (PANEL_TITLE + PLOT_H + PANEL_GAP)
        base = top + PANEL_TITLE + PLOT_H

        def sy(v):
            return base - PLOT_H * v / PANEL_TOP

        a(f'  <text class="f ptitle" x="40" y="{top + 32}">{titles[plan]}</text>')
        a(f'  <rect x="{PAD_L}" y="{sy(PANEL_TOP):.1f}" width="{W - PAD_L - PAD_R}" height="{PLOT_H}" fill="{DEAR}"/>')
        # Paint the cheap spans over a dear background, one polygon per band
        # index, so a balance with one cheap span and one with two both draw.
        for k in range(3):
            pts = [(r["balance"], cheap_spans(r)[k]) for r in rows if len(cheap_spans(r)) > k]
            if len(pts) < 2:
                continue
            fwd = " ".join(f"{sx(b):.1f},{sy(lo):.1f}" for b, (lo, hi) in pts)
            back = " ".join(f"{sx(b):.1f},{sy(hi):.1f}" for b, (lo, hi) in reversed(pts))
            a(f'  <polygon points="{fwd} {back}" fill="{CHEAP}"/>')
        for r in rows:
            pass
        # boundary lines: one polyline per edge index
        for k in range(2):
            pts = [(r["balance"], r["edges"][k]) for r in rows if len(r["edges"]) > k]
            if len(pts) < 2:
                continue
            d = " ".join(f"{sx(b):.1f},{sy(min(y, PANEL_TOP)):.1f}" for b, y in pts)
            a(f'  <polyline points="{d}" fill="none" stroke="{EDGE}" stroke-width="3" stroke-linejoin="round"/>')
        for v in range(0, PANEL_TOP + 1, 100_000):
            a(f'  <line x1="{PAD_L}" y1="{sy(v):.1f}" x2="{W - PAD_R}" y2="{sy(v):.1f}" stroke="{GRID}" stroke-width="1.5" opacity="0.7"/>')
            a(f'  <text class="f tick" x="{PAD_L - 12}" y="{sy(v) + 9:.1f}" text-anchor="end">${v // 1000}k</text>')
        for bal in (100_000, 200_000, 300_000, 400_000):
            a(f'  <line x1="{sx(bal):.1f}" y1="{sy(0):.1f}" x2="{sx(bal):.1f}" y2="{sy(PANEL_TOP):.1f}" stroke="{GRID}" stroke-width="1.5" opacity="0.7"/>')
            a(f'  <text class="f tick" x="{sx(bal):.1f}" y="{base + 34}" text-anchor="middle">${bal // 1000}k</text>')

    a(f'  <text class="f axis" x="40" y="{HEIGHT - 44}">Balance along the bottom, adjusted gross income up the side</text>')
    a('</svg>')
    svg = "\n".join(out) + "\n"
    import xml.etree.ElementTree as ET
    try:
        ET.fromstring(svg)
    except ET.ParseError as exc:
        sys.exit(f"refusing to write invalid SVG: {exc}")
    OUT.write_text(svg)
    print(f"wrote {OUT.relative_to(REPO)}  ({OUT.stat().st_size:,} bytes, {W}x{HEIGHT})")
    for plan in ("idr", "rap"):
        r = next(x for x in data[plan] if x["balance"] == 180_000)
        print(f"  {plan} at $180,000: edges {[f'${e:,.0f}' for e in r['edges']]}")


if __name__ == "__main__":
    main()

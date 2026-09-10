#!/usr/bin/env python3
"""Cost of attendance, the Student Aid Index and what is left, as bars.

    python3 brand/build_sai_need_chart.py

WHAT IT DRAWS. One bar per family income, at two schools: the median public
university in state and the median private nonprofit. Each bar is the whole
cost of attendance, cut where the Student Aid Index falls. The left piece is
what the formula says the family can pay. The right piece is need, which is
the definition and nothing more: cost of attendance minus the index.

THE RIGHT PIECE IS DRAWN HOLLOW ON PURPOSE, and it is the only real design
decision here. Need is an arithmetic gap, not an award: a school meeting it
in full is a promise a few hundred colleges make and most do not. A solid
block of colour next to a solid block of colour reads as two kinds of money,
which is exactly the misreading this figure exists to prevent, so need is an
outline with nothing inside it.

EVERY FIGURE COMES FROM THE APP. The index is compute_student_aid_index on
Formula A, the same function the site's estimator runs, through the section
1-2 exec prefix analyze_model.py uses. The two costs are medians over
data/college_coa_clean.csv restricted to bachelor's-granting institutions,
which is the same restriction chapter 3 and the aid-formula chart already
carry: the median over EVERY public institution folds in community colleges
and reads about $5,000 lower.

ONE X-SCALE ACROSS BOTH PANELS. The private bars are more than twice the
public ones and that is the point of putting them together, so drawing each
panel to its own width would silently rescale the comparison away. Same rule
the salary-flow stages carry in app.py.

NOTHING BELOW 26 UNITS on a 900-wide canvas, and the panels are stacked
rather than gridded, both for the same reason the plan-by-AGI figure records:
a guide body figure is about 610px on a desktop and 342px on a phone, and
what decides legibility is the ratio of type to canvas width.
"""
import io
import sys
from contextlib import redirect_stderr
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "static"

W = 900
GUTTER = 168          # income labels
RIGHT = 122           # room for a figure that will not fit inside its piece
BAR_H = 38
ROW = 62
PANEL_TITLE = 46
PANEL_GAP = 34
TOP = 24

INK = "#1a1c1f"
MUTED = "#6b6f77"
RULE = "#c9ced6"
SAI_FILL = "#eb6834"      # brand cost orange: money the family is asked for
NEED_EDGE = "#2a78d6"     # brand gain blue, as an outline only

INCOMES = (75_000, 100_000, 125_000, 150_000, 200_000, 250_000)
FAMILY_SIZE = 4           # chapter 3's household: two parents, one in college


def model():
    src = (REPO / "app.py").read_text()
    ns = {"__name__": "sai_need_chart"}
    with redirect_stderr(io.StringIO()):
        exec(compile(src[:src.index("# 3. PAGE CONFIG")], "app.py", "exec"), ns)
    return ns


def costs():
    import pandas as pd
    df = pd.read_csv(REPO / "data" / "college_coa_clean.csv")
    df = df[df.programs_bachl.notna()]
    pub = df[df.control_type == "Public"].in_state_coa.dropna()
    prv = df[df.control_type.str.contains("Non", na=False)].in_state_coa.dropna()
    if len(pub) < 500 or len(prv) < 500:
        sys.exit(f"only {len(pub)} publics and {len(prv)} private nonprofits with a cost; "
                 f"the bachelor's filter is wrong and a short median is not a small year.")
    return (("the median public university, in state", float(pub.median())),
            ("the median private nonprofit", float(prv.median())))


def esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def money(value):
    return f"${value:,.0f}"


def main() -> int:
    ns = model()
    sai_of = ns["compute_student_aid_index"]
    panels = costs()
    top_cost = max(cost for _, cost in panels)
    span = W - GUTTER - RIGHT

    def x_of(dollars):
        return GUTTER + span * dollars / top_cost

    parts, y = [], TOP
    parts.append(f'<text x="{GUTTER}" y="{y + 30}" font-size="30" fill="{MUTED}" '
                 f'font-family="Georgia,serif">One year, and what the formula does with it</text>')
    y += 58

    for title, cost in panels:
        parts.append(f'<text x="4" y="{y + 30}" font-size="31" fill="{INK}" '
                     f'font-family="Georgia,serif">{esc(title)}, {money(cost)} a year</text>')
        y += PANEL_TITLE
        for income in INCOMES:
            sai = sai_of(float(income), FAMILY_SIZE)["sai"]
            paid = min(sai, cost)
            need = max(cost - sai, 0.0)
            parts.append(f'<text x="{GUTTER - 14}" y="{y + BAR_H / 2 + 9}" font-size="27" '
                         f'fill="{INK}" text-anchor="end" '
                         f'font-family="Georgia,serif">{money(income)}</text>')
            parts.append(f'<rect x="{x_of(0):.1f}" y="{y}" width="{x_of(paid) - x_of(0):.1f}" '
                         f'height="{BAR_H}" fill="{SAI_FILL}"/>')
            if need > 0:
                # Hollow, because need is a gap rather than an award.
                parts.append(f'<rect x="{x_of(paid):.1f}" y="{y + 1}" '
                             f'width="{x_of(cost) - x_of(paid) - 1:.1f}" height="{BAR_H - 2}" '
                             f'fill="none" stroke="{NEED_EDGE}" stroke-width="2.5" '
                             f'stroke-dasharray="7 5"/>')
            # THE INDEX PIECE LABELS ITSELF OR NOT AT ALL. Pushing it outside
            # put it straight through the need figure at the incomes where the
            # piece is thin, which is every row the figure is really about.
            # Chapter 3's table above already lists the index by income, so a
            # dropped label here costs nothing; a collision costs the row.
            baseline = y + BAR_H / 2 + 9
            if paid > 0 and (x_of(paid) - x_of(0)) > len(money(paid)) * 15.5:
                parts.append(f'<text x="{(x_of(0) + x_of(paid)) / 2:.1f}" y="{baseline}" '
                             f'font-size="26" fill="#ffffff" text-anchor="middle" '
                             f'font-family="Georgia,serif">{esc(money(paid))}</text>')
            # Need labels inside its box, or just past the bar. RIGHT reserves
            # the room, so "just past" can never leave the canvas.
            if need > 0:
                label = money(need)
                inside = (x_of(cost) - x_of(paid)) > len(label) * 15.5
                parts.append(
                    f'<text x="{(x_of(paid) + x_of(cost)) / 2 if inside else x_of(cost) + 10:.1f}" '
                    f'y="{baseline}" font-size="26" '
                    f'fill="{NEED_EDGE if inside else MUTED}" '
                    f'text-anchor="{"middle" if inside else "start"}" '
                    f'font-family="Georgia,serif">{esc(label)}</text>')
            y += ROW
        y += PANEL_GAP - (ROW - BAR_H)

    # Key, in the picture because the two pieces are unlabeled otherwise.
    y += 8
    parts.append(f'<rect x="4" y="{y}" width="30" height="24" fill="{SAI_FILL}"/>')
    parts.append(f'<text x="44" y="{y + 20}" font-size="27" fill="{INK}" '
                 f'font-family="Georgia,serif">what the formula says the family pays</text>')
    parts.append(f'<rect x="470" y="{y + 1}" width="29" height="22" fill="none" '
                 f'stroke="{NEED_EDGE}" stroke-width="2.5" stroke-dasharray="7 5"/>')
    parts.append(f'<text x="510" y="{y + 20}" font-size="27" fill="{INK}" '
                 f'font-family="Georgia,serif">need, which is not an award</text>')
    height = y + 46

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" '
           f'width="{W}" height="{height}" role="img">'
           f'<rect width="{W}" height="{height}" fill="#ffffff"/>'
           + "".join(parts) + "</svg>")

    # AN INVALID SVG RENDERS AS NOTHING, SILENTLY. No console error, no failing
    # guard, just blank space. CLAUDE.md records both easy ways in: a "--" inside
    # an XML comment and a duplicate attribute. Parse before writing.
    import xml.etree.ElementTree as ET
    try:
        ET.fromstring(svg)
    except ET.ParseError as exc:
        sys.exit(f"refusing to write: the SVG does not parse ({exc})")

    out = OUT_DIR / f"guide-sai-need-{W}x{height}.svg"
    out.write_text(svg)
    print(f"wrote {out.relative_to(REPO)}  ({out.stat().st_size:,} bytes, {W}x{height})")
    for title, cost in panels:
        line = [f"  {title}, {money(cost)}:"]
        for income in INCOMES:
            s = sai_of(float(income), FAMILY_SIZE)["sai"]
            line.append(f"{money(income)} need {money(max(cost - s, 0))}")
        print("\n     ".join(line))
    return 0


if __name__ == "__main__":
    sys.exit(main())

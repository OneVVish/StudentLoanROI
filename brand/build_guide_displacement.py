#!/usr/bin/env python3
"""What an outside scholarship does to an aid package, in three panels.

    python3 brand/build_guide_displacement.py

WHAT IT DRAWS. One family, one year, three endings. Every bar is the same cost
of attendance on the same x scale, cut where the Student Aid Index falls, with
need drawn as the hollow outline that brand/build_sai_need_chart.py established
in the figure above it in the guide. Panel 1 fills need with a grant and a
subsidized loan. Panel 2 drops a scholarship on top and marks the part that
pushes past need. Panel 3 shows the three things a college may do next, which
is the whole point: the federal rule decides THAT something gives, and the
college decides WHICH line gives.

THE THIRD PANEL IS WHY THIS FIGURE EXISTS. Reduce the loan and the family is
better off by the loan. Reduce the college's own grant and the family is better
off by nothing at all. Both are legal, the reader cannot tell from the letter
which one they are looking at, and outside Connecticut no college has to say.

EVERY FIGURE COMES FROM THE APP OR THE STATUTE. The cost of attendance is the
median in-state cost at bachelor's-granting publics in data/college_coa_clean.csv,
the same restriction the need figure and chapter 3 carry. The index is
compute_student_aid_index on Formula A, through the section 1-2 exec prefix
analyze_model.py uses. The subsidized loan is the first-year statutory cap read
off app.py, not typed. Only the scholarship is chosen, because a scholarship has
no typical size, and the guide says so in the caption.

WHY THE UNSUBSIDIZED LOAN SITS AGAINST THE INDEX AND NOT AGAINST NEED. 34 CFR
673.5(c)(2)(iii) excludes from estimated financial assistance "those amounts
used to replace EFC, including ... unsubsidized Federal Stafford or Direct
Loans, Federal PLUS or Federal Direct PLUS Loans". So it is not part of the sum
that can overshoot need, and drawing it inside need would assert the opposite of
the rule the figure is about.

NOTHING BELOW 26 UNITS on a 900-wide canvas, panels stacked and never gridded.
A guide body figure is about 610px on a desktop and 342px on a phone, so what
decides legibility is the ratio of type to canvas width. Every free line is
measured and the script refuses to write rather than let an SVG clip it in
silence.
"""
import io
import sys
from contextlib import redirect_stderr
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "static"

W = 900
LEFT = 40
RIGHT = 40
BAR_H = 46
TOP = 130          # clears the 26px deck at y=78
PANEL_TITLE = 44
PANEL_GAP = 46
NOTE_H = 36

INK = "#1a1c1f"
MUTED = "#6b6f77"
RULE = "#c9ced6"
# Chosen for grayscale separation as well as hue, because the book may take
# this figure into a black and white interior later: the adjacent pairs all
# clear the 1.30 step marketing/book/gray_sweep.py enforces. A first set put
# the loan at #7f96b3, which converted to within 1.12 of the orange.
FAMILY = "#eb6834"     # brand cost orange: what the formula says the family pays
GRANT = "#12335c"      # the college's own money
LOAN = "#b8c4d4"       # borrowed money
SCHOL = "#5c6b1f"      # the outside award
NEED_EDGE = "#2a78d6"  # brand gain blue, as an outline only

# TWO FIGURES FROM ONE SCRIPT, because the guide and the book are answering
# this for different people.
#
# The guide's is a median family at the median in-state public, whose package
# fills need exactly. That is the case displacement needs in order to happen at
# all, so it is the right one for explaining the mechanism.
#
# The book's is Marisol Reyes, whom chapter 4 has already priced, and the
# arithmetic refuses to give her three endings: at every school in that chapter
# she has UNMET need, so an award falls into the gap and nothing is displaced.
# What her figure draws instead is the school-level difference that decides it,
# at two real California publics the chapter already tables.
#
# IT IS NOT A CLAIM ABOUT EITHER COLLEGE'S DISPLACEMENT POLICY. No source
# publishes that, which is the whole point of the section, and content/README.md
# forbids institution-level outcome claims anyway. What differs here is only
# where each school's package leaves her against the need ceiling, and that is
# arithmetic out of college_coa_clean.csv and the app's own index.
GUIDE_INCOME, GUIDE_AWARD = 75_000, 4_000
FAMILY_SIZE = 4
BOOK_INCOME, BOOK_AWARD = 150_000, 4_000
BOOK_SCHOOLS = ("San Diego State University", "University of California-Berkeley")

_ADV = {"narrow": 0.28, "digit": 0.56, "upper": 0.66, "lower": 0.50, "space": 0.26}


def text_width(s, px):
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


def fits(s, px, x, right_margin=20.0):
    """Return s, or exit naming the overflow. Called on every free line."""
    end = x + text_width(s, px)
    if end > W - right_margin:
        sys.exit(f"a {px}px line overflows the {W}-unit canvas by "
                 f"{end - (W - right_margin):.0f} units: {s!r}\n"
                 f"  cut the copy rather than shrinking the type; an SVG clips "
                 f"silently rather than wrapping.")
    return s


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def money(v):
    return f"${v:,.0f}"


def model():
    src = (REPO / "app.py").read_text()
    ns = {"__name__": "displacement_chart"}
    with redirect_stderr(io.StringIO()):
        exec(compile(src[:src.index("# 3. PAGE CONFIG")], "app.py", "exec"), ns)
    return ns


def school_cost(name):
    """One named school's in-state cost and the grant its net price implies."""
    import pandas as pd
    df = pd.read_csv(REPO / "data" / "college_coa_clean.csv")
    r = df[df.INSTNM == name]
    if r.empty:
        sys.exit(f"no row for {name!r} in college_coa_clean.csv")
    r = r.iloc[0]
    return float(r.in_state_coa), float(r.net_price_110_plus)


def public_coa():
    """The median in-state cost at a bachelor's-granting public.

    programs_bachl IS A PIPE-DELIMITED STRING, never a count, so the test is
    non-empty. to_numeric on it returns NaN for almost every row and would drop
    most of the file, which CLAUDE.md records as having reversed an answer once.
    """
    import pandas as pd
    df = pd.read_csv(REPO / "data" / "college_coa_clean.csv")
    df = df[df.programs_bachl.notna()]
    pub = df[df.control_type == "Public"].in_state_coa.dropna()
    if len(pub) < 500:
        sys.exit(f"only {len(pub)} publics carry a cost; the bachelor's filter "
                 f"is wrong and a short median is not a small year.")
    return float(pub.median())


def figures():
    ns = model()
    coa = public_coa()
    sai = float(ns["compute_student_aid_index"](GUIDE_INCOME, FAMILY_SIZE)["sai"])
    sai = max(sai, 0.0)
    need = max(coa - sai, 0.0)
    # The FIRST YEAR subsidized limit, read off the app's own table rather than
    # typed. federal_subsidized_cap takes a whole schedule and returns the
    # four-year total, which is not what one year of a package is made of.
    sub = min(float(ns["FEDERAL_SUBSIDIZED_ANNUAL_LIMITS"][1]), need)
    grant = max(need - sub, 0.0)
    return {"coa": coa, "sai": sai, "need": need, "sub": sub, "grant": grant}


def bar(x0, y, scale, blocks, need_w):
    """One cost-of-attendance bar: solid blocks, then the hollow need outline."""
    out, x = [], x0
    for value, fill, _ in blocks:
        if value <= 0:
            continue
        w = value * scale
        out.append(f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{BAR_H}" '
                   f'fill="{fill}" stroke="#ffffff" stroke-width="1"/>')
        x += w
    out.append(f'<rect x="{x0:.1f}" y="{y - 5}" width="{need_w:.1f}" '
               f'height="{BAR_H + 10}" fill="none" stroke="{NEED_EDGE}" '
               f'stroke-width="2.5" stroke-dasharray="7 5"/>')
    return out, x


def book_figure():
    """Two real schools, one family, one award: where the ceiling sits.

    THE DIFFERENCE DRAWN HERE IS ARITHMETIC AND NOT POLICY. Nobody publishes
    what a college reduces first, and this figure asserts nothing about it.
    What it draws is where each school's package leaves Marisol Reyes against
    her need, because that is what decides whether an award has anywhere to go.
    """
    ns = model()
    sai = float(ns["compute_student_aid_index"](BOOK_INCOME, FAMILY_SIZE)["sai"])
    rows = []
    for name in BOOK_SCHOOLS:
        coa, net = school_cost(name)
        need, grant = max(coa - sai, 0.0), max(coa - net, 0.0)
        rows.append(dict(name=name, coa=coa, need=need, grant=grant,
                         unmet=max(need - grant, 0.0)))
    widest = max(r["coa"] for r in rows)
    scale = (W - LEFT - RIGHT) / widest
    parts, y = [], 150

    parts.append(f'<text x="{LEFT}" y="52" font-size="34" font-weight="700" '
                 f'fill="{INK}" font-family="Georgia,serif">'
                 f'{esc(fits("The same award, at two of her schools", 34, LEFT))}</text>')
    deck = (f"Marisol Reyes, index {money(sai)} a year, and a {money(BOOK_AWARD)} "
            f"scholarship")
    parts.append(f'<text x="{LEFT}" y="88" font-size="26" fill="{MUTED}" '
                 f'font-family="Georgia,serif">{esc(fits(deck, 26, LEFT))}</text>')
    short = {"San Diego State University": "San Diego State",
             "University of California-Berkeley": "UC Berkeley"}
    for r in rows:
        title = f"{short.get(r['name'], r['name'])}, {money(r['coa'])} a year"
        parts.append(f'<text x="{LEFT}" y="{y}" font-size="28" font-weight="700" '
                     f'fill="{INK}" font-family="Georgia,serif">'
                     f'{esc(fits(title, 28, LEFT))}</text>')
        bar_top = y + 16
        x = LEFT
        for value, fill in ((r["grant"], GRANT), (BOOK_AWARD, SCHOL)):
            w = value * scale
            parts.append(f'<rect x="{x:.1f}" y="{bar_top}" width="{w:.1f}" '
                         f'height="{BAR_H}" fill="{fill}" stroke="#ffffff" '
                         f'stroke-width="1"/>')
            x += w
        parts.append(f'<rect x="{LEFT}" y="{bar_top - 5}" '
                     f'width="{r["need"] * scale:.1f}" height="{BAR_H + 10}" '
                     f'fill="none" stroke="{NEED_EDGE}" stroke-width="2.5" '
                     f'stroke-dasharray="7 5"/>')
        # SHORT, because the first pass overran the canvas by 198 units and the
        # house rule sends an explanatory sentence to the caption rather than
        # into the picture. The bar says where the ceiling is; the caption says
        # what that means.
        note = (f"Need {money(r['need'])}, grant {money(r['grant'])}, "
                + (f"{money(r['unmet'])} still open."
                   if r["unmet"] >= BOOK_AWARD else "need already covered."))
        base = bar_top + BAR_H + 35
        parts.append(f'<text x="{LEFT}" y="{base}" font-size="26" fill="{MUTED}" '
                     f'font-family="Georgia,serif">{esc(fits(note, 26, LEFT))}</text>')
        y = base + 52

    y += 6
    for fill, label in ((GRANT, "grant, from the school's net price at her band"),
                        (SCHOL, "the outside scholarship"),
                        (None, "need, which is cost of attendance less her index")):
        if fill:
            parts.append(f'<rect x="{LEFT}" y="{y}" width="30" height="24" fill="{fill}"/>')
        else:
            parts.append(f'<rect x="{LEFT}" y="{y + 1}" width="29" height="22" '
                         f'fill="none" stroke="{NEED_EDGE}" stroke-width="2.5" '
                         f'stroke-dasharray="7 5"/>')
        parts.append(f'<text x="{LEFT + 40}" y="{y + 20}" font-size="27" fill="{INK}" '
                     f'font-family="Georgia,serif">{esc(fits(label, 27, LEFT + 40))}</text>')
        y += NOTE_H
    height = y + 20

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" '
           f'width="{W}" height="{height}" role="img" aria-labelledby="bd-t bd-d">'
           f'<title id="bd-t">The same scholarship at two schools</title>'
           f'<desc id="bd-d">Two bars, one per school, on a shared scale. At San '
           f'Diego State the grant already covers need, so a scholarship has '
           f'nowhere to go inside it. At Berkeley a large part of need is unmet, '
           f'so the same scholarship lands in the gap.</desc>'
           f'<rect width="{W}" height="{height}" fill="#ffffff"/>'
           + "".join(parts) + "</svg>")
    import xml.etree.ElementTree as ET
    try:
        ET.fromstring(svg)
    except ET.ParseError as exc:
        sys.exit(f"refusing to write: the SVG does not parse ({exc})")
    path = OUT_DIR / f"book-displacement-{W}x{height}.svg"
    for stale in OUT_DIR.glob(f"book-displacement-{W}x*.svg"):
        if stale != path:
            stale.unlink(); print(f"  swept {stale.name}")
    path.write_text(svg)
    print(f"wrote {path.relative_to(REPO)}  ({path.stat().st_size:,} bytes, {W}x{height})")
    for r in rows:
        print(f"  {r['name'][:38]:40} need {money(r['need']):>9}  "
              f"grant {money(r['grant']):>9}  unmet {money(r['unmet']):>9}")
    return path


def main():
    f = figures()
    # THE SCALE COMES FROM THE WIDEST BAR, NOT FROM THE COST OF ATTENDANCE.
    # Panel 2 is the whole cost PLUS the scholarship, because that is what
    # "past need" means, so scaling on the cost alone ran that bar off the
    # right edge of the canvas and clipped the family's own block in half.
    widest = f["coa"] + GUIDE_AWARD
    scale = (W - LEFT - RIGHT) / widest
    need_w = f["need"] * scale
    parts, y = [], TOP

    parts.append(f'<text x="{LEFT}" y="44" font-size="34" font-weight="700" '
                 f'fill="{INK}" font-family="Georgia,serif">'
                 f'{esc(fits("One scholarship, three endings", 34, LEFT))}</text>')
    sub_line = (f"A year at {money(f['coa'])}, the median in-state public, on a "
                f"{money(GUIDE_INCOME)} income")
    parts.append(f'<text x="{LEFT}" y="78" font-size="26" fill="{MUTED}" '
                 f'font-family="Georgia,serif">{esc(fits(sub_line, 26, LEFT))}</text>')

    def panel(title, blocks, note):
        """One titled bar with a note under it.

        THE SPACING IS ARITHMETIC, NOT TASTE, and guessing it cost two
        collisions in the first render: the note was struck through by the
        need outline, which extends 5 units past the bar on each side, and
        the next panel's title landed on top of that note. Each offset below
        is measured from the thing above it.
        """
        nonlocal y
        parts.append(f'<text x="{LEFT}" y="{y}" font-size="28" font-weight="700" '
                     f'fill="{INK}" font-family="Georgia,serif">'
                     f'{esc(fits(title, 28, LEFT))}</text>')
        bar_top = y + 16
        rects, _ = bar(LEFT, bar_top, scale, blocks, need_w)
        parts.extend(rects)
        outline_bottom = bar_top + BAR_H + 5
        note_base = outline_bottom + 30
        parts.append(f'<text x="{LEFT}" y="{note_base}" font-size="26" '
                     f'fill="{MUTED}" font-family="Georgia,serif">'
                     f'{esc(fits(note, 26, LEFT))}</text>')
        y = note_base + PANEL_GAP

    panel("As offered",
          [(f["grant"], GRANT, "grant"), (f["sub"], LOAN, "subsidized loan"),
           (f["sai"], FAMILY, "the family")],
          f"Need is {money(f['need'])} and the package fills it exactly.")

    over = min(GUIDE_AWARD, f["need"])
    panel(f"A {money(GUIDE_AWARD)} scholarship arrives",
          [(f["grant"], GRANT, "grant"), (f["sub"], LOAN, "subsidized loan"),
           (GUIDE_AWARD, SCHOL, "scholarship"), (f["sai"], FAMILY, "the family")],
          f"Now {money(over)} of assistance sits past need. Something gives.")

    cut_loan = min(GUIDE_AWARD, f["sub"])
    cut_grant_after_loan = GUIDE_AWARD - cut_loan
    panel("It comes off the loan",
          [(f["grant"] - cut_grant_after_loan, GRANT, "grant"),
           (f["sub"] - cut_loan, LOAN, "subsidized loan"),
           (GUIDE_AWARD, SCHOL, "scholarship"), (f["sai"], FAMILY, "the family")],
          f"{money(cut_loan)} less borrowed. The family is better off by that.")

    panel("It comes off the college's grant",
          [(f["grant"] - GUIDE_AWARD, GRANT, "grant"),
           (f["sub"], LOAN, "subsidized loan"),
           (GUIDE_AWARD, SCHOL, "scholarship"), (f["sai"], FAMILY, "the family")],
          "Same bill, same loan. The family is better off by nothing.")

    keys = [(GRANT, "grant, the college's own money"),
            (LOAN, "subsidized loan"),
            (SCHOL, "the outside scholarship"),
            (FAMILY, "what the formula says the family pays"),
            (None, "need, which is not an award")]
    y += 6
    for fill, label in keys:
        if fill:
            parts.append(f'<rect x="{LEFT}" y="{y}" width="30" height="24" '
                         f'fill="{fill}"/>')
        else:
            parts.append(f'<rect x="{LEFT}" y="{y + 1}" width="29" height="22" '
                         f'fill="none" stroke="{NEED_EDGE}" stroke-width="2.5" '
                         f'stroke-dasharray="7 5"/>')
        parts.append(f'<text x="{LEFT + 40}" y="{y + 20}" font-size="27" '
                     f'fill="{INK}" font-family="Georgia,serif">'
                     f'{esc(fits(label, 27, LEFT + 40))}</text>')
        y += NOTE_H
    height = y + 20

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" '
           f'width="{W}" height="{height}" role="img" aria-labelledby="dp-t dp-d">'
           f'<title id="dp-t">What an outside scholarship does to an aid package'
           f'</title>'
           f'<desc id="dp-d">Four bars of one cost of attendance on a shared '
           f'scale. The package as offered fills need with a grant and a '
           f'subsidized loan. A scholarship pushes assistance past need. The '
           f'college then reduces either the loan, which leaves the family '
           f'better off, or its own grant, which leaves the family no better '
           f'off.</desc>'
           f'<rect width="{W}" height="{height}" fill="#ffffff"/>'
           + "".join(parts) + "</svg>")

    # AN INVALID SVG RENDERS AS NOTHING, SILENTLY: no console error, no failing
    # guard, just blank space where the figure should be. Parse before writing.
    import xml.etree.ElementTree as ET
    try:
        ET.fromstring(svg)
    except ET.ParseError as exc:
        sys.exit(f"refusing to write: the SVG does not parse ({exc})")

    path = OUT_DIR / f"guide-displacement-{W}x{height}.svg"
    for stale in OUT_DIR.glob(f"guide-displacement-{W}x*.svg"):
        if stale != path:
            stale.unlink()
            print(f"  swept {stale.name}")
    path.write_text(svg)
    print(f"wrote {path.relative_to(REPO)}  ({path.stat().st_size:,} bytes, {W}x{height})")
    print(f"  cost of attendance {money(f['coa'])}, index {money(f['sai'])}, "
          f"need {money(f['need'])}")
    print(f"  package: grant {money(f['grant'])} + subsidized {money(f['sub'])}")
    print(f"  a {money(GUIDE_AWARD)} award off the loan saves {money(cut_loan)}, "
          f"off the grant saves $0")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--book", action="store_true",
                    help="the two-school figure chapter 4 places")
    a = ap.parse_args()
    book_figure() if a.book else main()

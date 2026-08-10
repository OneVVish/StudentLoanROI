#!/usr/bin/env python3
"""The federal borrowing-limits poster, in the shape of the reference graphic.

    python3 brand/build_borrowing_graphic.py

WHAT THIS IS. A counselor-style infographic: condensed caps headline, a callout,
a TABLE as the centrepiece, a footnote, three points, a closing line. The
reference that prompted it used navy and gold; this uses a deep step of the
app's own blue with the app's orange as the accent, because a second palette
would be a second brand -- the logo next door would stop matching the poster
inside a week.

EVERY FIGURE IN THE TABLE IS COMPUTED BY THE APP, not typed off the reference.
`borrowing_table()` calls federal_direct_cap and parent_plus_cap on a real
four-year schedule, one year at a time, so the poster and the calculator cannot
disagree. That matters more here than on the feature grid: this graphic's whole
claim is that the numbers are right, and it will be screenshotted and forwarded
without the site attached.

ONE THING THE REFERENCE GETS WRONG AND THIS DOES NOT. It labels the table
"Class of 2028 -- under current federal law". The new Parent PLUS ceiling binds
on loans first disbursed on or after July 1 2026 (PARENT_PLUS_LIMIT_EFFECTIVE_YEAR),
so a 2028 graduate borrowed their first two years under the old rule, where
Parent PLUS was cost-of-attendance-minus-aid and had no practical ceiling at
all. The table is true for a student STARTING in 2026 or later, and that is
what this says.
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyBboxPatch, Circle, Rectangle

sys.path.insert(0, str(Path(__file__).parent))
from build_feature_graphic import (          # noqa: E402  -- path set above
    BRAND, draw_mark, px_to_pt, wrap, fit_line, ROOT)

OUT = Path(__file__).parent

DEEP = "#12335c"      # white sits on this at 12.7:1
ORANGE = BRAND["light"]["cost"]
BLUE = BRAND["light"]["gain"]
INK = "#14161a"
MUTED = "#5c636d"
TINT = "#fdf2ec"      # the reference's cream, warmed toward our orange
RULE = "#dfe3e8"
SURFACE = "#ffffff"

CAPS = FontProperties(family="Avenir Next Condensed", weight="heavy")
BOLD = FontProperties(family="Avenir Next", weight="demibold")
HEAVY = FontProperties(family="Avenir Next", weight="bold")
BOOK = FontProperties(family="Avenir Next", weight="regular")


def borrowing_table():
    """(rows, total_row, caps) straight out of app.py's own cap functions.

    Built one year at a time so each row is that year's marginal capacity --
    which is what a family experiences -- while the total is the app's own
    four-year answer, aggregate ceilings included. Typing the rows and summing
    them would give the same numbers today and silently diverge the moment a
    ceiling moves, which is the whole reason this reads the code.
    """
    src = (ROOT / "app.py").read_text()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    ns = {"__name__": "postercheck"}
    exec(compile(src[:src.rindex("# " + "=" * 60, 0, cut)], "app.py", "exec"), ns)

    schedule = [{"year": y, "financed": True, "phase": "university"}
                for y in range(1, 5)]
    start = ns["PARENT_PLUS_LIMIT_EFFECTIVE_YEAR"]

    rows, direct_run, plus_run = [], 0.0, 0.0
    for i, label in enumerate(("Freshman", "Sophomore", "Junior", "Senior"), 1):
        upto = schedule[:i]
        direct_total = ns["federal_direct_cap"](upto, "dependent")
        plus_total = ns["parent_plus_cap"](upto, "dependent", start_year=start)
        direct, plus = direct_total - direct_run, plus_total - plus_run
        direct_run, plus_run = direct_total, plus_total
        rows.append((label, direct, plus, direct + plus))

    total = ("4-year total", direct_run, plus_run, direct_run + plus_run)
    caps = {
        "plus_annual": ns["PARENT_PLUS_ANNUAL_LIMIT"],
        "plus_aggregate": ns["PARENT_PLUS_AGGREGATE_LIMIT"],
        "direct_aggregate": ns["FEDERAL_DIRECT_AGGREGATE_CAP"]["dependent"],
        "effective_year": start,
    }
    return rows, total, caps


def money(v):
    return f"${v:,.0f}"


def esc(text: str) -> str:
    """The same escape T() applies, for the two places that MEASURE a string
    before drawing it. Measuring the raw text and drawing the escaped one is
    how a fitted line ends up a hair wider than the box it was fitted to."""
    return text.replace("$", "\\$")


POINTS = [
    ("See what this table does not cover.",
     "The gap above these ceilings is private money, at a higher rate."),
    ("Compare 5,035 schools on published cost.",
     "Priced in-state or out, for the state you actually live in."),
    ("Then model the payment, not just the debt.",
     "RAP, Tiered Standard, and your position ten years out."),
]

LAYOUTS = {
    "borrowing-1080x1350": (1080, 1350),
    "borrowing-1080x1920": (1080, 1920),
    "borrowing-letter":    (1275, 1650),
}


def build_one(name, w, h, rows, total, caps, slack=0.0):
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi, facecolor=SURFACE)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, w); ax.set_ylim(0, h); ax.axis("off")
    fig.canvas.draw()

    # A TALLER CANVAS GETS BIGGER TYPE, not bigger gaps. The sections have
    # fixed heights, so on the 1080x1920 story the slack pass had ~570px to
    # spread over four gaps and the poster came apart into four islands. A
    # story is also read at arm's length on a phone, where larger type is the
    # right answer anyway. Capped at 1.35 so the headline cannot outgrow its
    # own measure.
    u = (w / 1080.0) * min(1.35, max(1.0, h / (w * 1.25)))
    pad = 56 * u
    cw = w - pad * 2
    cx = w / 2

    def T(x, y, s, px, font, color, **kw):
        # ESCAPED HERE, at the one place text reaches the canvas, because
        # matplotlib reads paired dollar signs as mathtext. The footnote --
        # "capped at $65,000 ... so $20,000 in each of the first three years"
        # -- came out as an italic run with both signs eaten. It is the same
        # trap fmt_money_md guards on the Streamlit side and _pdf_escape_money
        # guards in the PDF, met a third time, which is why it is applied to
        # EVERY string rather than to the ones that look like money today.
        return ax.text(x, y, s.replace("$", r"\$"), fontproperties=font,
                       fontsize=px_to_pt(px, dpi), color=color, **kw)

    y = h - pad

    # ---- headline ----
    head = "KNOW BEFORE YOU BORROW"
    hp = fit_line(fig, head, 92 * u, CAPS, dpi, cw)
    T(cx, y, head, hp, CAPS, DEEP, va="top", ha="center")
    y -= hp * 1.02

    # ---- accent rule with a centred mark ----
    y -= 22 * u
    for x0, x1 in ((pad, cx - 26 * u), (cx + 26 * u, w - pad)):
        ax.plot([x0, x1], [y, y], color=ORANGE, lw=2.6 * u, zorder=3)
    ax.add_patch(plt.Polygon(
        [(cx, y + 13 * u), (cx + 11 * u, y), (cx, y - 13 * u), (cx - 11 * u, y)],
        closed=True, facecolor=ORANGE, edgecolor="none", zorder=4))
    y -= 34 * u + slack

    sub = "What federal loans actually cover — before you fall in love with a school."
    sp = fit_line(fig, sub, 30 * u, BOLD, dpi, cw)
    T(cx, y, sub, sp, BOLD, INK, va="top", ha="center")
    y -= sp * 1.5

    # ---- callout ----
    y -= 18 * u
    box_pad = 22 * u
    icon_r = 34 * u
    body = (f"Starting college in {caps['effective_year']} or later: the new "
            f"federal caps apply to every year you borrow.")
    body_px = 26 * u
    inner = cw - box_pad * 3 - icon_r * 2
    lines = wrap(fig, body, body_px, BOOK, dpi, inner)
    box_h = max(icon_r * 2 + box_pad * 1.2, len(lines) * body_px * 1.45 + box_pad * 1.6)
    ax.add_patch(FancyBboxPatch(
        (pad, y - box_h), cw, box_h,
        boxstyle="round,pad=0,rounding_size=%f" % (14 * u),
        facecolor=TINT, edgecolor=ORANGE, lw=2.2 * u, zorder=1))
    icx, icy = pad + box_pad + icon_r, y - box_h / 2
    ax.add_patch(Circle((icx, icy), icon_r, facecolor=DEEP, edgecolor="none",
                        zorder=2))
    # A board over a cap body, not a lone lozenge -- drawn as a single
    # diamond it read as an eye on a navy disc.
    ax.add_patch(Rectangle((icx - icon_r * 0.26, icy - icon_r * 0.42),
                           icon_r * 0.52, icon_r * 0.44,
                           facecolor="#ffffff", edgecolor="none", zorder=3))
    ax.add_patch(plt.Polygon(
        [(icx - icon_r * 0.60, icy + icon_r * 0.02),
         (icx, icy - icon_r * 0.26), (icx + icon_r * 0.60, icy + icon_r * 0.02),
         (icx, icy + icon_r * 0.30)],
        closed=True, facecolor="#ffffff", edgecolor="none", zorder=4))
    ax.plot([icx + icon_r * 0.52, icx + icon_r * 0.52],
            [icy + icon_r * 0.04, icy - icon_r * 0.34],
            color="#ffffff", lw=2.0 * u, solid_capstyle="round", zorder=4)
    ty = icy + (len(lines) - 1) * body_px * 1.45 / 2
    for line in lines:
        T(icx + icon_r + box_pad, ty, line, body_px, BOOK, INK,
          va="center", ha="left")
        ty -= body_px * 1.45
    y -= box_h + 30 * u + slack

    # ---- table ----
    headers = ("College Year", "Student Direct Loan",
               "Parent PLUS Max", "Maximum Combined")
    col_w = (cw * 0.28, cw * 0.24, cw * 0.24, cw * 0.24)
    xs = [pad]
    for cwid in col_w[:-1]:
        xs.append(xs[-1] + cwid)

    hpx = 22 * u
    row_px = 27 * u
    row_h = 52 * u
    head_h = 50 * u

    ax.add_patch(Rectangle((pad, y - head_h), cw, head_h, facecolor=DEEP,
                           edgecolor="none", zorder=2))
    # ONE size for every header, found by fitting them all and taking the
    # smallest. Fitted independently they came out at different sizes in the
    # same row -- a large "College Year" beside a small "Student Direct Loan"
    # -- which reads as a mistake rather than as emphasis.
    hpx = min(fit_line(fig, h_, hpx, BOLD, dpi, w_ - 16 * u)
              for h_, w_ in zip(headers, col_w))
    for hx, hw, htext in zip(xs, col_w, headers):
        T(hx + hw / 2, y - head_h / 2, htext, hpx, BOLD, "#ffffff",
          va="center", ha="center", zorder=3)
    y -= head_h

    for r, (label, direct, plus, comb) in enumerate(rows):
        ax.add_patch(Rectangle((pad, y - row_h), cw, row_h,
                               facecolor=SURFACE if r % 2 == 0 else "#f6f8fa",
                               edgecolor="none", zorder=1))
        ax.plot([pad, pad + cw], [y - row_h] * 2, color=RULE, lw=1.2, zorder=2)
        cells = (label, money(direct), money(plus) + ("*" if r == 3 else ""),
                 money(comb))
        for hx, hw, cell in zip(xs, col_w, cells):
            T(hx + hw / 2, y - row_h / 2, cell, row_px, BOOK, INK,
              va="center", ha="center", zorder=3)
        y -= row_h

    ax.add_patch(Rectangle((pad, y - row_h), cw, row_h, facecolor=TINT,
                           edgecolor="none", zorder=1))
    for hx, hw, cell in zip(xs, col_w,
                            (total[0], money(total[1]), money(total[2]),
                             money(total[3]))):
        T(hx + hw / 2, y - row_h / 2, cell, row_px, BOLD, DEEP,
          va="center", ha="center", zorder=3)
    y -= row_h + 22 * u + slack

    # ---- footnote ----
    note = (f"*Parent PLUS is capped at {money(caps['plus_aggregate'])} in total "
            f"per dependent student, so {money(caps['plus_annual'])} in each of "
            f"the first three years leaves only "
            f"{money(caps['plus_aggregate'] - caps['plus_annual'] * 3)} for year "
            f"four. These are ceilings, not offers.")
    npx = 21 * u
    for line in wrap(fig, note, npx, BOOK, dpi, cw - 40 * u):
        T(cx, y, line, npx, BOOK, MUTED, va="top", ha="center")
        y -= npx * 1.45
    y -= 26 * u + slack

    # ---- three points ----
    dot_r = 20 * u
    lead_px = 27 * u
    tail_px = 24 * u
    for i, (lead, tail) in enumerate(POINTS):
        ax.add_patch(Circle((pad + dot_r, y - dot_r * 0.9), dot_r,
                            facecolor=DEEP, edgecolor="none", zorder=3))
        T(pad + dot_r, y - dot_r * 0.9, str(i + 1), dot_r * 1.05, BOLD,
          "#ffffff", va="center", ha="center", zorder=4)
        tx = pad + dot_r * 2 + 20 * u
        tw = cw - (dot_r * 2 + 20 * u)
        lp = fit_line(fig, lead, lead_px, BOLD, dpi, tw)
        T(tx, y, lead, lp, BOLD, INK, va="top", ha="left")
        cur = y - lp * 1.34
        for line in wrap(fig, tail, tail_px, BOOK, dpi, tw):
            T(tx, cur, line, tail_px, BOOK, MUTED, va="top", ha="left")
            cur -= tail_px * 1.40
        y = cur - 16 * u

    # ---- closing ----
    close_y = pad * 0.92
    mark = 46 * u
    tag = "Run your own numbers, free"
    tag_px = 27 * u
    url = "worthmydegree.com"
    url_px = 30 * u
    tag_w = fig.text(0, 0, tag, fontproperties=BOLD,
                     fontsize=px_to_pt(tag_px, dpi))
    tw_ = tag_w.get_window_extent(fig.canvas.get_renderer()).width
    tag_w.remove()
    url_t = fig.text(0, 0, url, fontproperties=HEAVY,
                     fontsize=px_to_pt(url_px, dpi))
    uw = url_t.get_window_extent(fig.canvas.get_renderer()).width
    url_t.remove()
    block = mark * 1.25 + tw_ + 14 * u + uw
    bx = cx - block / 2
    # The flanking rules are dropped when the block leaves them too short to
    # read as rules -- on the story format they came out as two stubs either
    # side of the tagline, which looks like a rendering fault.
    rule_y = close_y + mark * 0.42
    if (bx - 18 * u) - pad > 40 * u:
        ax.plot([pad, bx - 18 * u], [rule_y] * 2, color=ORANGE,
                lw=2.2 * u, zorder=2)
        ax.plot([bx + block + 18 * u, w - pad], [rule_y] * 2, color=ORANGE,
                lw=2.2 * u, zorder=2)
    draw_mark(ax, bx, close_y, mark)
    T(bx + mark * 1.25, close_y + mark * 0.42, tag, tag_px, BOLD, MUTED,
      va="center", ha="left")
    T(bx + mark * 1.25 + tw_ + 14 * u, close_y + mark * 0.42, url, url_px,
      HEAVY, DEEP, va="center", ha="left")

    floor = close_y + mark + 30 * u
    leftover = y - floor
    # A pixel of tolerance: after the slack pass leftover lands on zero and
    # floating point puts it a hair either side, which would fire this warning
    # on a layout that fits exactly.
    if leftover < -1.0:
        print(f"    ! {name}: content runs {-leftover:.0f}px into the closing "
              f"block -- shorten a section")

    path = OUT / f"{name}.png"
    fig.savefig(path, dpi=dpi, facecolor=SURFACE)
    plt.close(fig)
    return path, leftover


def build():
    rows, total, caps = borrowing_table()
    print("table computed from app.py's own cap functions:")
    for r in rows:
        print(f"    {r[0]:<12} direct {money(r[1]):>8}  plus {money(r[2]):>8} "
              f" combined {money(r[3]):>8}")
    print(f"    {total[0]:<12} direct {money(total[1]):>8}  "
          f"plus {money(total[2]):>8}  combined {money(total[3]):>8}")
    for name, (w, h) in LAYOUTS.items():
        # TWO PASSES. The sections have fixed heights and the canvases do not,
        # so a 1350-tall poster left ~200px pooled in one gap above the closing
        # line -- which reads as a layout that ran out rather than as breathing
        # room. The first pass measures that leftover; the second spreads it
        # across the four section gaps. SLACK_GAPS must match the number of
        # `+ slack` terms in build_one.
        SLACK_GAPS = 4
        _p, leftover = build_one(name, w, h, rows, total, caps)
        if leftover > 0:
            # Capped per gap: whatever the type scale leaves over should read
            # as breathing room, never as four separated islands. Any excess
            # beyond the cap stays as bottom margin, which is where a story's
            # own UI chrome sits anyway.
            per_gap = min(leftover / SLACK_GAPS, 60 * (w / 1080.0))
            _p, leftover = build_one(name, w, h, rows, total, caps,
                                     slack=per_gap)
        print(f"  wrote brand/{_p.name}  ({w}x{h}, "
              f"{leftover:.0f}px trailing)")


if __name__ == "__main__":
    build()

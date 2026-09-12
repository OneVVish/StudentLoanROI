#!/usr/bin/env python3
"""Four cover concepts for the book, drawn rather than illustrated.

WHY NONE OF THESE IS AN ILLUSTRATION. The nineteen chapter openers are
klein4b renders, and the author's decision on 2026-09-12 was that cover art
is not. So every concept here is type, the drawn logo mark, and real data
out of data/college_coa_clean.csv. That also sidesteps the open question in
the book plan about disclosing AI art on the copyright page, which a cover
would have made the sharpest possible version of.

THE MARK IS ALREADY THE BOOK'S ARGUMENT, which is why three of the four use
it. brand/palette.json's geometry is an orange stroke that starts on a zero
rule, drops into a trough, climbs back to the rule at x=35.826, and a blue
stroke that carries on up to (58,8) with a dot at the crossing. That is the
net-position curve: what the degree costs, when it gets ahead, and by how
much. It has been the favicon all along.

THE FOUR, each against a real archetype on the Amazon shelf:
  mark       white, glyph-led      the editorial trade book (Lieber)
  ceiling    cream, chart-led      the number-led self-publisher
  annual     dark, year-badged     Princeton Review, head on
  questions  cream, typographic    the clean self-publisher

THE ONLY TEST THAT MATTERS IS THE THUMBNAIL. A Kindle browser sees about
100px of width. --contact-sheet renders all four at that size side by side,
and a cover that fails there fails, whatever it looks like at 1600.

THE DISPLAY FACE IS RE-CUT IN INTER, and the reason is a licence rather than
a preference. brand/README.md records that the wordmark is set in Avenir
Next, "which is licensed, not open", and that "converting outlines does not
convert the licence". A book sold for money is the sharpest form of that
question, so the covers use Inter Display under the SIL Open Font License,
bundled in brand/fonts/ with its licence file so the cover reproduces on a
clone and on CI rather than depending on one Mac's font book. Georgia stays
on the serif lines: a raster cover embeds no font, and the contrast between
a geometric display face and a serif sub-line is doing real work.

Writes brand/cover-{concept}.png (master, gitignored like the other brand
masters) and static/cover-{concept}.jpg.
"""
import argparse
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.lines as mlines
from matplotlib import font_manager as fm
import matplotlib.pyplot as plt
import pandas as pd

REPO = Path(__file__).resolve().parents[1]

# KDP's stated ideal, read 2026-09-12 at kdp.amazon.com/en_US/help/topic/
# G200645690: "Ideal dimensions for cover files are 2,560 pixels in height x
# 1,600 pixels in width", ratio at least 1.6:1, JPEG or TIFF, under 50MB.
W, H, DPI = 1600, 2560, 200

PAL = json.loads((REPO / "brand" / "palette.json").read_text())
COST, GAIN = PAL["light"]["cost"], PAL["light"]["gain"]
INK, MUTED, RULE = PAL["light"]["ink"], PAL["light"]["muted"], PAL["light"]["rule"]
PAPER = "#f7f4ee"        # the interior's cream, decided 2026-09-10
WHITE = "#ffffff"        # the brand's own light surface, what mark-light.svg assumes
NIGHT = "#15171b"        # a shade under palette ink, so ink type can sit on it
ON_NIGHT = PAL["dark"]["ink"]

# Registered from brand/fonts/ so this never depends on what is installed.
FONT_DIR = REPO / "brand" / "fonts"
for _f in sorted(FONT_DIR.glob("*.ttf")):
    fm.fontManager.addfont(str(_f))
TITLE_F = "Inter Display"          # Black for the question, the cover's voice
BODY_F = "Georgia"                 # the serif sub-line, unchanged
DISPLAY_BLACK = fm.FontProperties(fname=str(FONT_DIR / "InterDisplay-Black.ttf"))
DISPLAY_SEMI = fm.FontProperties(fname=str(FONT_DIR / "InterDisplay-SemiBold.ttf"))

# Cover copy. Held here so check_chart_prose can reach it and so the house
# rules in marketing/book/AGENTS.md can be checked against one place: no dash
# punctuation, no contractions, the % sign never the word, a tilde on rounded
# money and none on statutory, and NO VERDICT AND NO PROMISE OF A SAVING.
# That last rule is what rules out this category's entire register.
# One text with real leading, not two hand-placed lines: at 130pt the
# two lines overlap in bbox terms and the collision guard was right to
# say so. matplotlib sets the leading better than arithmetic does.
TITLE = "IS IT\nWORTH IT?"
SUBTITLE = "Paying for College in 2027 by the Numbers"
AUTHOR = "Veer Vishwakarma"
SITE = "worthmydegree.com"
YEAR = "2027"
METHOD_LINE = "Every figure computed from federal data"
# $92,000 is statutory, so it carries no tilde. AGENTS.md: "Money that is
# statutory does not: $5,500, $65,000, $92,000. Those are ceilings."
CEILING = 92_000
TWO_QUESTIONS = ("Paying for college is two questions.\n"
                 "The second is the one almost nobody asks.")


def frame(repo: Path):
    """The 2,235 bachelor's-granting colleges, by what four years costs.

    programs_bachl is a PIPE-DELIMITED STRING of CIP families, not a count.
    to_numeric on it returns NaN for almost every row and silently drops
    1,870 colleges, which is the trap CLAUDE.md records as having once
    reversed an answer. The correct test is non-empty, and chapter 2's own
    count of 2,235 is what proves the filter is right.
    """
    d = pd.read_csv(repo / "data/college_coa_clean.csv")
    is_bach = d.programs_bachl.notna() & (d.programs_bachl.astype(str).str.strip() != "")
    b = d[is_bach & d.in_state_coa.notna()].copy()
    b["four"] = b.in_state_coa * 4
    if len(b) != 2235:
        raise SystemExit(f"refusing to write: {len(b)} colleges, chapter 2 says 2,235")
    over = int((b.four > CEILING).sum())
    if over != 1644:
        raise SystemExit(f"refusing to write: {over} over the line, chapter 0 says 1,644")
    return b.sort_values("four").reset_index(drop=True), over


def mark(fig, x, y, size_frac, cost=COST, gain=GAIN, rule=RULE, ring=None,
         rule_span=None, dot=None):
    """The logo mark in FIGURE coordinates, the geometry app.py section 2k
    draws for the share card and the PDF header. Drawn, never embedded: the
    brand READMEs require it and an SVG would need a font this has none of.

    THE FULL VARIANT, NOT THE FAVICON, AND THE REASON IS THE BOOK'S OWN RULE.
    draw_logo_mark_mpl draws strokes only, no zero rule and no crossing dot,
    which is right at 16px in a PDF header and wrong at half a cover wide: with
    no baseline to read the trough against, the shape stops being a curve and
    becomes a TICK. A giant green-light tick on the front of a book whose
    AGENTS.md says "No verdicts and no advice" argues the opposite of the text
    inside it. The rule and the dot put the chart back."""
    g = PAL["geometry"]
    start, trough, end = tuple(g["start"]), tuple(g["trough"]), tuple(g["end"])
    zero_y, cross_x = g["zero_y"], g["crossing_x"]
    aspect = fig.get_figwidth() / fig.get_figheight()

    def pt(px, py):
        return (x + (px / 64.0) * size_frac,
                y + ((64 - py) / 64.0) * size_frac * aspect)

    lw = size_frac * fig.get_figwidth() * 72 * 0.148
    # AT COVER SCALE THE RULE HAS TO OUTRUN THE STROKES OR IT IS A
    # STRIKETHROUGH. Inside a 64px favicon a rule from x=4 to x=60 reads as a
    # baseline because nothing else is on the canvas. Blown up to half a cover,
    # a rule barely wider than the tick reads as a line drawn THROUGH it.
    # rule_span carries it edge to edge, and then the shape is a curve crossing
    # an axis, which is what it has always actually been.
    rx0, ry = pt(4.0, zero_y)
    rx1, _ = pt(60.0, zero_y)
    if rule_span:
        rx0, rx1 = rule_span
    fig.add_artist(mlines.Line2D([rx0, rx1], [ry, ry], transform=fig.transFigure,
                                 color=rule, linewidth=lw * 0.306,
                                 solid_capstyle="round", zorder=1))
    for pts, colour in (((start, trough, (cross_x, zero_y)), cost),
                        (((cross_x, zero_y), end), gain)):
        xs, ys = zip(*(pt(*p) for p in pts))
        fig.add_artist(mlines.Line2D(xs, ys, transform=fig.transFigure, color=colour,
                                     linewidth=lw, solid_capstyle="round",
                                     solid_joinstyle="round", zorder=2))
    dx, dy = pt(cross_x, zero_y)
    fig.add_artist(mlines.Line2D([dx], [dy], transform=fig.transFigure,
                                 marker="o", markersize=lw * (11.2 / 8.5),
                                 markerfacecolor=dot or gain,
                                 markeredgecolor=ring or fig.get_facecolor(),
                                 markeredgewidth=lw * (2.4 / 8.5),
                                 linestyle="none", zorder=3))
    return lw


def fit(fig, t, max_frac):
    """Shrink a text until it fits max_frac of the canvas width.

    A hand-picked point size cannot survive a change of face, and this cover
    changed face mid-build: Inter Display is far wider than the condensed face
    the layout was first set in, so every title overflowed at once. Measure,
    never count, which is the rule the salary-flow labels and the share card's
    deck line already follow.
    """
    fig.canvas.draw()
    rend = fig.canvas.get_renderer()
    px = fig.get_size_inches()[0] * fig.dpi
    for _ in range(60):
        if t.get_window_extent(renderer=rend).width <= max_frac * px:
            break
        t.set_fontsize(t.get_fontsize() * 0.97)
    return t


def under(fig, artist, gap):
    """Figure-y just below a drawn artist. Once a title auto-fits, its height
    is not known until it is drawn, so anything placed beneath it by a typed
    constant breaks the moment the face or the measure changes. This reads the
    bbox back instead."""
    fig.canvas.draw()
    bb = artist.get_window_extent(renderer=fig.canvas.get_renderer())
    return bb.y0 / (fig.get_size_inches()[1] * fig.dpi) - gap


def canvas(bg):
    fig = plt.figure(figsize=(W / DPI, H / DPI), facecolor=bg)
    return fig


def byline(fig, colour, muted):
    fig.text(0.5, 0.075, AUTHOR, fontproperties=DISPLAY_SEMI, fontsize=28, color=colour,
             ha="center", va="bottom", parse_math=False)
    fig.text(0.5, 0.046, SITE, fontname=BODY_F, fontsize=17, color=muted,
             ha="center", va="bottom", parse_math=False)


def draw_mark_cover(args):
    """White, glyph-led. The question, then the argument as a shape.

    WHITE GROUND AND THE MARK IN ITS OWN COLOURS. This went dark first, then
    the mark went white on it, and the white version had a real cost: with no
    orange for the cost leg and no blue for the return, the whole reading of
    the shape rested on the rule, and a mark cropped or printed at low
    contrast became a tick, which is the verdict AGENTS.md refuses to give.
    On white the mark can simply be itself, which is what brand/mark-light.svg
    already is. Nothing carries meaning on trust here: the orange dips below
    the rule, the blue climbs above it, the dot marks the crossing.
    """
    fig = canvas(WHITE)
    t = fig.text(0.5, 0.955, TITLE, fontproperties=DISPLAY_BLACK, fontsize=118, color=INK,
                 ha="center", va="top", linespacing=0.92, parse_math=False)
    fit(fig, t, 0.82)
    mark(fig, 0.245, 0.395, 0.51, ring=WHITE, rule_span=(0.065, 0.935))
    sub = fig.text(0.5, 0.315, SUBTITLE, fontname=BODY_F, fontsize=29,
                   color=MUTED, ha="center", va="top", parse_math=False)
    fit(fig, sub, 0.84)
    byline(fig, INK, MUTED)
    return fig, [t, sub]


def draw_ceiling_cover(args):
    """Cream, chart-led. The cover is a figure, and every mark is a college."""
    b, over = frame(REPO)
    fig = canvas(PAPER)
    ax = fig.add_axes([0.10, 0.145, 0.80, 0.40], facecolor=PAPER)
    y = b.four.to_numpy() / 1000.0
    x = range(len(y))
    above = b.four.to_numpy() > CEILING
    ax.scatter([i for i, a in zip(x, above) if not a], y[~above], s=7.5,
               color=GAIN, linewidths=0, zorder=2)
    ax.scatter([i for i, a in zip(x, above) if a], y[above], s=7.5,
               color=COST, linewidths=0, zorder=2)
    ax.axhline(CEILING / 1000.0, color=INK, lw=2.2, zorder=3)
    ax.set_xlim(-40, len(y) + 40)
    ax.set_ylim(0, 340)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xticks([]); ax.set_yticks([])

    lab = ax.text(len(y) * 0.012, CEILING / 1000.0 + 9, "$92,000",
                  fontproperties=DISPLAY_SEMI, fontsize=32, color=INK, va="bottom", parse_math=False)
    t = fig.text(0.5, 0.968, TITLE, fontproperties=DISPLAY_BLACK, fontsize=98, color=INK,
                 ha="center", va="top", linespacing=0.92, parse_math=False)
    fit(fig, t, 0.76)
    sub = fig.text(0.5, under(fig, t, 0.022), SUBTITLE, fontname=BODY_F, fontsize=27,
                   color=MUTED, ha="center", va="top", parse_math=False)
    note = fig.text(0.5, under(fig, sub, 0.030),
                    f"${CEILING:,} is the most the federal government\n"
                    f"will lend one family for one bachelor's degree.\n"
                    f"At {over:,} of {len(b):,} colleges, four years costs more.",
                    fontname=BODY_F, fontsize=22, color=INK, ha="center", va="top",
                    linespacing=1.6, parse_math=False)
    fit(fig, sub, 0.84)
    fit(fig, note, 0.86)
    byline(fig, INK, MUTED)
    return fig, [t, sub, note, lab]


def draw_annual_cover(args):
    """Dark, year-badged. Built to sit beside Paying for College 2027."""
    fig = canvas(NIGHT)
    # The badge sits clear ABOVE the title, the way the annual it is aimed at
    # does it. Placed level with the title it collided, because the title auto
    # fits to 84% of the width and so runs under the badge's column.
    yr = fig.text(0.915, 0.968, YEAR, fontproperties=DISPLAY_BLACK, fontsize=60,
                  color=COST, ha="right", va="top", parse_math=False)
    t = fig.text(0.075, 0.872, TITLE, fontproperties=DISPLAY_BLACK, fontsize=120,
                 color=ON_NIGHT, ha="left", va="top", linespacing=0.92,
                 parse_math=False)
    fit(fig, t, 0.84)
    sub = fig.text(0.075, under(fig, t, 0.020), SUBTITLE, fontname=BODY_F,
                   fontsize=30, color=PAL["dark"]["muted"], ha="left", va="top",
                   parse_math=False)
    fit(fig, sub, 0.80)
    fig.patches.append(plt.Rectangle((0.075, under(fig, sub, 0.030)), 0.85, 0.004,
                                     transform=fig.transFigure, facecolor=COST,
                                     edgecolor="none"))

    # Where Chany puts a starburst promising FAFSA help, this puts the method.
    b, over = frame(REPO)
    ax = fig.add_axes([0.075, 0.235, 0.85, 0.33], facecolor=NIGHT)
    y = b.four.to_numpy() / 1000.0
    above = b.four.to_numpy() > CEILING
    idx = range(len(y))
    ax.scatter([i for i, a in zip(idx, above) if not a], y[~above], s=5.0,
               color=GAIN, linewidths=0)
    ax.scatter([i for i, a in zip(idx, above) if a], y[above], s=5.0,
               color=COST, linewidths=0)
    ax.axhline(CEILING / 1000.0, color=PAL["dark"]["muted"], lw=1.6)
    ax.text(len(y) * 0.012, CEILING / 1000.0 + 11, f"${CEILING:,}",
            fontproperties=DISPLAY_SEMI, fontsize=26, color=PAL["dark"]["ink"],
            va="bottom", parse_math=False)
    ax.set_xlim(-40, len(y) + 40); ax.set_ylim(0, 340)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xticks([]); ax.set_yticks([])

    fit(fig, sub, 0.80)
    meth = fig.text(0.075, 0.175, METHOD_LINE, fontproperties=DISPLAY_SEMI, fontsize=32,
                    color=COST, ha="left", va="top", parse_math=False)
    fit(fig, meth, 0.80)
    a = fig.text(0.075, 0.075, AUTHOR, fontproperties=DISPLAY_SEMI, fontsize=28, color=ON_NIGHT,
                 ha="left", va="bottom", parse_math=False)
    fig.text(0.075, 0.046, SITE, fontname=BODY_F, fontsize=17,
             color=PAL["dark"]["muted"], ha="left", va="bottom", parse_math=False)
    return fig, [yr, t, sub, meth, a]


def draw_questions_cover(args):
    """Cream, typographic. No chart, no illustration, the mark kept small."""
    fig = canvas(PAPER)
    t = fig.text(0.5, 0.915, TITLE, fontproperties=DISPLAY_BLACK, fontsize=124, color=INK,
                 ha="center", va="top", linespacing=0.92, parse_math=False)
    fit(fig, t, 0.80)
    fig.patches.append(plt.Rectangle((0.30, 0.655), 0.40, 0.004,
                                     transform=fig.transFigure,
                                     facecolor=RULE, edgecolor="none"))
    sub = fig.text(0.5, 0.615, SUBTITLE, fontname=BODY_F, fontsize=30, color=INK,
                   ha="center", va="top", parse_math=False)
    q = fig.text(0.5, 0.500, TWO_QUESTIONS, fontname=BODY_F, fontsize=27, color=MUTED,
                 ha="center", va="top", linespacing=1.75, parse_math=False)
    fit(fig, sub, 0.84)
    fit(fig, q, 0.84)
    mark(fig, 0.335, 0.215, 0.33, rule_span=(0.215, 0.785))
    byline(fig, INK, MUTED)
    return fig, [t, sub, q]


CONCEPTS = {"mark": draw_mark_cover, "ceiling": draw_ceiling_cover,
            "annual": draw_annual_cover, "questions": draw_questions_cover}


def guard(fig, texts):
    """Cover type is enormous, so running off the edge is the likeliest
    defect and the one a full-size render hides from a tired eye."""
    fig.canvas.draw()
    rend = fig.canvas.get_renderer()
    px, py = fig.get_size_inches() * fig.dpi
    for t in texts:
        bb = t.get_window_extent(renderer=rend)
        if bb.x0 < 6 or bb.x1 > px - 6:
            raise SystemExit(f"refusing to write: {t.get_text()[:28]!r} leaves the canvas")
        if bb.y0 < 6 or bb.y1 > py - 6:
            raise SystemExit(f"refusing to write: {t.get_text()[:28]!r} runs off top or bottom")
    for a, b in zip(texts, texts[1:]):
        ba, bb = a.get_window_extent(renderer=rend), b.get_window_extent(renderer=rend)
        if ba.y0 < bb.y1 - 2 and ba.x1 > bb.x0 and bb.x1 > ba.x0:
            raise SystemExit(f"refusing to write: {a.get_text()[:20]!r} collides with "
                             f"{b.get_text()[:20]!r}")


def render(name, args):
    global TITLE_F
    if args.face:
        TITLE_F = args.face
    fig, texts = CONCEPTS[name](args)
    guard(fig, texts)
    out = REPO / "brand" / f"cover-{name}.png"
    fig.savefig(out, dpi=DPI, facecolor=fig.get_facecolor())
    jpg = REPO / "static" / f"cover-{name}.jpg"
    fig.savefig(jpg, dpi=DPI, facecolor=fig.get_facecolor(),
                pil_kwargs={"quality": 92, "progressive": True, "optimize": True})
    plt.close(fig)
    kb = jpg.stat().st_size / 1024
    print(f"  wrote {out.name} and {jpg.name}  ({W}x{H}, {kb:,.0f} KB)")
    return out


def contact_sheet():
    """Kindle browsing is the real viewing condition: about 100px of width.
    A cover that fails here fails, whatever it looks like at full size."""
    from PIL import Image
    tw = 100
    ims = []
    for n in CONCEPTS:
        p = REPO / "brand" / f"cover-{n}.png"
        if not p.exists():
            raise SystemExit(f"refusing: render {n} first")
        im = Image.open(p).convert("RGB")
        ims.append(im.resize((tw, round(tw * H / W)), Image.LANCZOS))
    pad, th = 18, ims[0].height
    sheet = Image.new("RGB", (len(ims) * (tw + pad) + pad, th + 2 * pad), "#dedede")
    for i, im in enumerate(ims):
        sheet.paste(im, (pad + i * (tw + pad), pad))
    out = REPO / "brand" / "cover-contact-sheet.png"
    sheet.save(out)
    print(f"  wrote {out.name}  ({', '.join(CONCEPTS)} at {tw}px wide)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--concept", choices=list(CONCEPTS) + ["all"], default="all")
    ap.add_argument("--face", help="re-cut the display type, for the licence question")
    ap.add_argument("--contact-sheet", action="store_true")
    args = ap.parse_args()
    if args.contact_sheet:
        return contact_sheet()
    names = list(CONCEPTS) if args.concept == "all" else [args.concept]
    for n in names:
        render(n, args)


if __name__ == "__main__":
    main()

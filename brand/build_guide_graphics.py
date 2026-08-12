#!/usr/bin/env python3
"""The two in-article graphics for the parent walkthrough guide.

    python3 brand/build_guide_graphics.py

WHAT THESE ARE. Two body illustrations for
content/posts/for-parents-run-the-numbers.md: one for "Why the letters do not
compare" (what a sticker price leaves out) and one for "What you are holding at
the end" (the four outputs). The article was drafted on a Situation/Task/Action/
Result spine and those two sections are where a picture earned its place; the
spine itself is no longer named in the headings, so do not go looking for it.

WHY THEY CARRY NO FIGURES. Every other graphic in this folder computes its
numbers from the app or the committed datasets, and says so loudly, because its
whole claim is that the numbers are right. These two make a STRUCTURAL point
instead: that a cost of attendance omits the four things that decide the
answer, and that the tool returns four specific outputs. Neither argument needs
a dollar amount, and putting illustrative ones on a diagram would invent
figures a reader could mistake for findings. The consequence worth having is
that these cannot go stale: no rate, cap, wage or dataset vintage appears on
them, so a data refresh never silently makes them wrong. Contrast the deck
screenshots in presentation/assets, which show grey action buttons and
pre-2026-08-12 copy and are already out of date.

WHY THEY ARE NOT SCREENSHOTS. A screenshot of a real verdict would be more
persuasive in the Result section, and it would need recapturing on every copy
or layout change. The guide is meant to outlive that.

The palette, the fonts and the logo mark all come from build_feature_graphic,
so these sit beside the feature grid and the borrowing poster without
introducing a second brand. Nothing here imports app.py.

OUTPUT. Writes into brand/, then the caller copies into static/ so the guide
renderer can reach them at /app/static/<name> (see brand/README.md; static/ is
COPIES of brand/ output).

ONE MATPLOTLIB TRAP AVOIDED BY CONSTRUCTION. Paired dollar signs in a string
are mathtext, which is why build_borrowing_graphic carries an esc() helper.
No string in this file contains a dollar sign, so the trap cannot fire. Keep it
that way, or borrow that helper.
"""
import shutil
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch

sys.path.insert(0, str(Path(__file__).parent))
from build_feature_graphic import (          # noqa: E402  -- path set above
    BLUE, BOOK, BOLD, BRAND, HEAVY, INK, MUTED, ORANGE, ROOT, RULE, SURFACE,
    TILE, draw_mark, fit_line, text_width, wrap)

OUT = Path(__file__).parent
STATIC = ROOT / "static"
DPI = 200


# --- shared furniture ------------------------------------------------------
def canvas(w_px, h_px):
    """A blank landscape figure in pixel coordinates, so every position below
    reads as pixels rather than as a fraction of something."""
    fig = plt.figure(figsize=(w_px / DPI, h_px / DPI), dpi=DPI)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, w_px)
    ax.set_ylim(0, h_px)
    ax.axis("off")
    ax.add_patch(plt.Rectangle((0, 0), w_px, h_px, color=SURFACE, zorder=0))
    return fig, ax


def text(ax, x, y, s, px, font, color=INK, ha="left", va="baseline", **kw):
    ax.text(x, y, s, fontproperties=font, fontsize=px * 72.0 / DPI,
            color=color, ha=ha, va=va, zorder=6, **kw)


def tile(ax, x, y, w, h, fill=TILE, edge=None, lw=0.0, radius=14):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle=f"round,pad=0,rounding_size={radius}",
        facecolor=fill, edgecolor=edge or fill, linewidth=lw, zorder=2))


def wordmark(fig, ax, x, y, size=30, ink=INK, muted=MUTED):
    """Mark plus the domain, so a forwarded screenshot still says where it
    came from.

    The ".com" is positioned from the MEASURED width of "worthmydegree", not
    from a multiple of the mark size. The first version guessed at size * 4.30
    and the two words overlapped: a glyph advance is not a fixed ratio of an
    unrelated dimension, and the error is invisible until something renders.

    `ink`/`muted` exist for the social card, which sets the two words on a navy
    bar. The defaults are the light-background pair, so every existing caller
    is unchanged.
    """
    draw_mark(ax, x, y, size)
    px = size * 0.62
    left = x + size * 1.35
    base = y + size * 0.30
    text(ax, left, base, "worthmydegree", px, HEAVY, ink)
    text(ax, left + text_width(fig, "worthmydegree", px, HEAVY, DPI), base,
         ".com", px, BOOK, muted)


def footer(ax, w, line):
    ax.plot([70, w - 70], [110, 110], color=RULE, lw=1.4, zorder=3)
    text(ax, 70, 66, line, 21, BOOK, MUTED)


def tile_text(fig, ax, x, y, w, h, head, sub, accent):
    """One tile's contents, laid out DOWNWARD from its own top edge.

    Everything here is measured or derived from h. The first version pinned the
    heading near the top and the body near the BOTTOM, which left a hole in the
    middle of a tall tile and, on a short one, put the body under the heading's
    descenders.
    """
    pad = 30
    ax.plot([x + pad, x + pad + 52], [y + h - 26, y + h - 26], color=accent,
            lw=5, solid_capstyle="round", zorder=5)
    head_px = fit_line(fig, head, 31, HEAVY, DPI, w - 2 * pad)
    text(ax, x + pad, y + h - 26 - head_px * 1.55, head, head_px, HEAVY, INK)
    lines = wrap(fig, sub, 20, BOOK, DPI, w - 2 * pad)
    top = y + h - 26 - head_px * 1.55 - 34
    for i, line in enumerate(lines):
        text(ax, x + pad, top - i * 27, line, 20, BOOK, MUTED)


# --- 1. What the sticker price leaves out ----------------------------------
OMISSIONS = [
    # Each body is short enough to set on ONE measured line at 20px inside a
    # 470px tile. Two of these originally wrapped, and the second line sat
    # 15px off the tile floor.
    ("The aid you actually receive",
     "Only a net price calculator knows it"),
    ("Interest over the whole term",
     "The balance is not the cost of the balance"),
    ("The salary at the end",
     "It decides whether the debt is affordable"),
    ("Where they will live and work",
     "The same pay is a different life by city"),
]


def build_situation(name="guide-situation-1600x900.png", w=1600, h=900):
    fig, ax = canvas(w, h)
    wordmark(fig, ax, 70, h - 100)

    # Explicit vertical bands, measured from the top and never overlapping.
    text(ax, 70, 700, "A cost of attendance is not", 58, HEAVY, INK)
    text(ax, 70, 634, "what you pay", 58, HEAVY, INK)
    ax.plot([70, 148], [598, 598], color=ORANGE, lw=6,
            solid_capstyle="round", zorder=5)

    grid_top, row_h, gap = 500, 150, 20
    col_w = 470

    # The sticker sits alone on the left, the biggest thing on the page and
    # deliberately blank: the point is that it is not an answer.
    card_h = row_h * 2 + gap
    cx, cy = 70, grid_top - card_h
    tile(ax, cx, cy, 430, card_h, fill=SURFACE, edge=ORANGE, lw=3)
    text(ax, cx + 34, cy + card_h - 52, "What the letter says", 24, BOLD, ORANGE)
    text(ax, cx + 34, cy + card_h - 122, "Cost of", 44, HEAVY, INK)
    text(ax, cx + 34, cy + card_h - 178, "attendance", 44, HEAVY, INK)
    # Under the heading, not pinned to the card floor: the same hole that
    # tile_text was rewritten to avoid.
    text(ax, cx + 34, cy + card_h - 222, "One number, before anything",
         20, BOOK, MUTED)

    # Heading sits ABOVE the grid, in its own band.
    text(ax, 580, grid_top + 34, "What it leaves out", 28, BOLD, INK)

    for i, (head, sub) in enumerate(OMISSIONS):
        col, row = i % 2, i // 2
        x = 580 + col * (col_w + gap)
        y = grid_top - (row + 1) * row_h - row * gap
        tile(ax, x, y, col_w, row_h)
        tile_text(fig, ax, x, y, col_w, row_h, head, sub, BLUE)

    footer(ax, w, "None of the four is on the letter. All four decide the answer.")
    path = OUT / name
    fig.savefig(path, dpi=DPI, facecolor=SURFACE)
    plt.close(fig)
    return path


# --- 2. What a parent is holding at the end --------------------------------
OUTPUTS = [
    ("The monthly payment",
     "After tax, in the city where they will actually work"),
    ("The break-even ceiling",
     "The loan amount at which the degree stops paying for itself"),
    ("The year it pulls ahead",
     "For a path that trains before it earns, often well past year ten"),
    ("The alternative, beside it",
     "Two full scenarios, the same arithmetic run twice"),
]


def build_result(name="guide-result-1600x900.png", w=1600, h=900):
    fig, ax = canvas(w, h)
    wordmark(fig, ax, 70, h - 100)

    text(ax, 70, 706, "What you walk away with", 58, HEAVY, INK)
    ax.plot([70, 148], [670, 670], color=ORANGE, lw=6,
            solid_capstyle="round", zorder=5)
    text(ax, 70, 622, "Four things you did not have when you sat down.",
         24, BOOK, MUTED)

    # 150 rather than 190: with a one-line body the taller tile left a band of
    # dead space along its floor, which reads as a layout fault.
    grid_top, row_h, gap = 520, 150, 24
    col_w = 712
    for i, (head, sub) in enumerate(OUTPUTS):
        col, row = i % 2, i // 2
        x = 70 + col * (col_w + gap)
        y = grid_top - (row + 1) * row_h - row * gap
        tile(ax, x, y, col_w, row_h)
        # A short accent rule over each figure, the same device the counselor
        # decks use for their stat grid. Alternating the two brand colours by
        # COLUMN, not by index, so the pairing reads down the page.
        tile_text(fig, ax, x, y, col_w, row_h, head, sub,
                  ORANGE if col == 0 else BLUE)

    footer(ax, w, "Not a recommendation. The arithmetic, with real numbers.")
    path = OUT / name
    fig.savefig(path, dpi=DPI, facecolor=SURFACE)
    plt.close(fig)
    return path


# --- 2c. The counselor guide's closing graphic ------------------------------
# The three students from "The three conversations it makes shorter", each
# ending somewhere good. It closes the article on outcomes rather than on
# caveats, which is what the section above it earns.
#
# NO FIGURES, for the reason the other two carry none: a salary or a cap on a
# diagram goes stale on the next data refresh and nothing would flag it. There
# is a second reason here. A "happy outcomes" panel with dollar amounts on it
# would be a claim about what this tool DELIVERS, and the tool does not deliver
# a salary -- it delivers an answer. Each line below is therefore about the
# decision being made with the number in hand, never about the number being
# large. The article spends 1,400 words refusing to flatter anyone; the picture
# at the end must not undo that.
OUTCOMES = [
    ("The one who had already decided",
     "Priced it before signing, not after. Same school, eyes open.", ORANGE),
    ("The one who ruled it out on price",
     "Found a school that teaches their field inside the budget.", BLUE),
    ("The one not going to a four-year school",
     "Saw a no-degree path measured on exactly the same arithmetic.", ORANGE),
]


def build_outcomes(name="guide-outcomes-1600x900.png", w=1600, h=900):
    fig, ax = canvas(w, h)
    wordmark(fig, ax, 70, h - 100)

    text(ax, 70, 706, "Three students, three real answers", 58, HEAVY, INK)
    ax.plot([70, 148], [670, 670], color=ORANGE, lw=6,
            solid_capstyle="round", zorder=5)
    text(ax, 70, 622,
         "None of them was told what to do. All three left knowing the number.",
         24, BOOK, MUTED)

    # Three full-width rows rather than build_result's 2x2: an odd count in a
    # two-column grid leaves a hole, and these read as a sequence.
    #
    # 124 not 150. At 150 the three rows plus their gaps ran to y=22 and the
    # last tile sat UNDER the footer rule at y=110, printing the caption
    # through the tile's own body text. matplotlib draws it happily -- nothing
    # overflows a canvas -- so this was invisible until the PNG was opened.
    # The assertion below is what makes it impossible to reintroduce by editing
    # a constant. 124 is the floor tile_text can hold: its body baseline lands
    # 16px off the tile floor, which clears the descenders.
    row_h, gap, col_w = 124, 22, 1460
    top = 548
    floor = top - len(OUTCOMES) * row_h - (len(OUTCOMES) - 1) * gap
    assert floor > 110 + 20, f"tiles reach y={floor}, into the footer at 110"
    for i, (head, sub, accent) in enumerate(OUTCOMES):
        y = top - (i + 1) * row_h - i * gap
        tile(ax, 70, y, col_w, row_h)
        tile_text(fig, ax, 70, y, col_w, row_h, head, sub, accent)

    footer(ax, w, "Same arithmetic every time. Different right answers.")
    path = OUT / name
    fig.savefig(path, dpi=DPI, facecolor=SURFACE)
    plt.close(fig)
    return path


# --- 3. The title-section hero banner --------------------------------------
DEEP = "#12335c"          # the landing page's --deep; darker than palette ink


def _curve_motif(ax, x, y, size, alpha):
    """The break-even curve from the logo, drawn large as background texture.

    Stands in for the stock photograph the reference format puts here. A photo
    would have to be licensed and would date; this is the mark the brand already
    owns, and it happens to BE the thing the article is about -- a line that
    goes down before it comes up.
    """
    g = BRAND["geometry"]
    s = size / 64.0

    def P(px, py):
        return x + px * s, y + (64 - py) * s

    zero = g["zero_y"]
    ax.plot(*zip(P(4, zero), P(60, zero)), color="#ffffff", alpha=alpha * 0.30,
            lw=size * 0.010, solid_capstyle="round", zorder=2)
    # The two strokes need DIFFERENT alphas on a dark band. At a shared value
    # the orange descent turned to mud against the navy while the blue ascent
    # stayed legible, so the mark read as a plain V rather than as a line that
    # goes down before it comes up -- which is the entire point of the shape.
    ax.plot(*zip(P(*g["start"]), P(*g["trough"]), P(g["crossing_x"], zero)),
            color=ORANGE, alpha=min(alpha * 2.6, 1.0), lw=size * 0.085,
            solid_capstyle="round", solid_joinstyle="round", zorder=2)
    ax.plot(*zip(P(g["crossing_x"], zero), P(*g["end"])),
            color=BLUE, alpha=min(alpha * 1.9, 1.0), lw=size * 0.085,
            solid_capstyle="round", zorder=2)


def compose_hero(name="guide-hero-1600x460.png", w=1600, h=460,
                 background=None, overlay=0.0):
    """Full-bleed title banner: a dark band with a curved bottom, and NO TEXT.

    THE HEADLINE IS NOT DRAWN HERE, deliberately. The first version baked it in
    and it does not survive a phone: `article img { width: 100% }` scales the
    image, so text in the pixels shrinks with the container instead of
    reflowing. Measured on 2026-08-12 -- 13.6px on a 390pt phone against a live
    `h1` of 30px, and already smaller than the heading on desktop at 26px
    against 44px. The template puts the real `<h1>` over this instead, which is
    what the reference format does and what makes the words selectable,
    translatable and reachable by a screen reader.

    It is also the only arrangement that suits a generated background: a
    diffusion model renders text unreliably, so the words must not be its job.

    `background` is an optional RGB array (see build_ai_hero.py). Without one
    this degrades to flat navy plus the brand curve, so the repo still builds
    with no Cloudflare credentials present.

    The curved bottom edge is the detail that makes this read as a hero rather
    than a coloured rectangle, and it is filled in SURFACE so the band appears
    cut away rather than overlaid.
    """
    fig, ax = canvas(w, h)

    if background is None:
        ax.add_patch(plt.Rectangle((0, 0), w, h, color=DEEP, zorder=1))
        # The curve is the only thing on the band, so it can be large. With a
        # photograph behind it, it would fight the image instead.
        _curve_motif(ax, w * 0.665, h * 0.24, h * 0.98, alpha=0.16)
    else:
        ax.imshow(background, extent=(0, w, 0, h), aspect="auto", zorder=1)
        # NO TINT BY DEFAULT, on purpose. The template already lays a
        # rgba(18,51,92,0.72) scrim under the headline, sized so that white text
        # clears 4.5:1 even over a pure white patch of photo. Baking a second
        # one here stacked the two: at 0.62 plus 0.72 only 11% of the image
        # survived, so the photograph we paid to generate was invisible.
        # Contrast is guaranteed in ONE place, and it is the CSS, because that
        # can be retuned without regenerating anything.
        if overlay:
            ax.add_patch(plt.Rectangle((0, 0), w, h, color=DEEP, alpha=overlay,
                                       zorder=2, linewidth=0))

    # NO CUT-AWAY ARC. The first version drew a white curve across the foot,
    # which is the detail that makes the reference format read as a hero. It
    # cannot live in the IMAGE once the template lays a scrim over the whole
    # header: the white arc sits under the overlay and renders as a grey band,
    # which looks like a rendering fault rather than a shape. A curve would have
    # to be drawn in CSS, above the scrim, to work. Rounded corners on the
    # header carry the same job for far less machinery.

    path = OUT / name
    fig.savefig(path, dpi=DPI, facecolor=SURFACE)
    plt.close(fig)
    return path


def build_hero(name="guide-hero-1600x460.png", w=1600, h=460):
    """The credential-free fallback, kept so `build()` needs no network."""
    return compose_hero(name, w, h, background=None)


# --- 4. The per-article social card ----------------------------------------
OG_W, OG_H = 1200, 630
OG_BAR = 96              # the solid footer the wordmark sits on
# NO TINT OVER THE PHOTO. It was 0.30 navy, and unlike the hero's scrim that
# alpha was doing no work: nothing is set over the photograph here, because the
# wordmark sits on the solid bar below it. So the tint was pure colour cast --
# it made every card blue, and a link preview is the one place the picture has
# to do all the persuading on its own. The bar keeps the card branded.
OG_TINT = 0.0


def compose_og_card(name, hero_png, w=OG_W, h=OG_H):
    """The 1200x630 card a link preview shows, built FROM that guide's hero.

    DERIVED, NOT GENERATED. A second trip to the image model would cost another
    seed to record and could return a different scene, so the card and the page
    a reader lands on would show different photographs -- which is the thing a
    preview is supposed to promise. Cropping the band we already have cannot
    drift from it, needs no credentials, and makes this reproducible from the
    repo alone.

    NO HEADLINE IS DRAWN. Every platform renders og:title beside the image, so
    baking the words in prints them twice, and each platform crops a card
    differently -- text near an edge is the first thing lost. Baked text is
    fine HERE in the sense the hero's docstring rules out (a card is a fixed
    1200x630 raster and never scales responsively), so this is a cropping
    decision rather than the scaling one.

    The wordmark is the exception, and it sits on a solid bar rather than on the
    photo: contrast against an arbitrary photograph cannot be guaranteed, and
    the bar also keeps the mark clear of the safe-area crop.
    """
    from PIL import Image

    src = Image.open(hero_png).convert("RGB")
    # Centre-crop the 3.45:1 band to the card's aspect before scaling, so the
    # photo is never stretched. The band is wider than it is tall relative to
    # the card, so height is the binding dimension and width gets trimmed.
    photo_h = h - OG_BAR
    cw = min(src.width, int(round(src.height * (w / photo_h))))
    left = (src.width - cw) // 2
    src = src.crop((left, 0, left + cw, src.height)).resize(
        (w, photo_h), Image.LANCZOS)

    fig, ax = canvas(w, h)
    ax.imshow(np.asarray(src), extent=(0, w, OG_BAR, h), aspect="auto", zorder=1)
    if OG_TINT:
        ax.add_patch(plt.Rectangle((0, OG_BAR), w, photo_h, color=DEEP,
                                   alpha=OG_TINT, zorder=2, linewidth=0))
    ax.add_patch(plt.Rectangle((0, 0), w, OG_BAR, color=DEEP, zorder=3,
                               linewidth=0))
    wordmark(fig, ax, 46, OG_BAR * 0.30, size=34, ink="#ffffff",
             muted="#b9c6d8")

    path = OUT / name
    fig.savefig(path, dpi=DPI, facecolor=SURFACE)
    plt.close(fig)
    return path


def build():
    STATIC.mkdir(exist_ok=True)
    for fn in (build_situation, build_result, build_outcomes, build_hero):
        src = fn()
        dst = STATIC / src.name
        shutil.copyfile(src, dst)
        print(f"  wrote {src.relative_to(ROOT)} "
              f"({src.stat().st_size:,} bytes) -> {dst.relative_to(ROOT)}")


if __name__ == "__main__":
    build()

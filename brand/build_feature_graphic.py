#!/usr/bin/env python3
"""Feature graphic for worthmydegree.com, at every size it gets posted at.

    python3 brand/build_feature_graphic.py

WHY MATPLOTLIB AND NOT SVG. The logo set next door is SVG because a logo is
master artwork that gets scaled forever. This is the opposite: it is consumed
as a raster at four fixed sizes by platforms that will not take an SVG at all.
No SVG rasteriser is installed on this machine, so an SVG here would be a file
nobody could post and nobody could open to check -- and a graphic that has not
been looked at is not finished.

EVERY NUMBER ON IT IS READ FROM THE DATASETS, not typed. A marketing asset that
drifts from the product is the same failure as a chart twin that drifts from
its original, except it is the one artifact that leaves the building. If the
Scorecard file is rebuilt with a different school count, this graphic is one
command behind rather than quietly wrong.

THE ICONS ARE DRAWN, NOT EMOJI. Emoji render in whatever the platform feels
like -- a different glyph per OS, and matplotlib has no colour-emoji font at
all, so they would come out as tofu. Each tile gets a small diagram in its own
accent instead, which also lets the icon say something: the ROI tile carries
the actual break-even curve.
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyBboxPatch, Circle

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).parent
BRAND = json.loads((OUT / "palette.json").read_text())

INK = "#14161a"
MUTED = "#6b7280"
RULE = "#e4e7eb"
SURFACE = "#ffffff"
TILE = "#f7f8fa"
BLUE = BRAND["light"]["gain"]
ORANGE = BRAND["light"]["cost"]

BOLD = FontProperties(family="Avenir Next", weight="demibold")
HEAVY = FontProperties(family="Avenir Next", weight="bold")
BOOK = FontProperties(family="Avenir Next", weight="regular")


def facts() -> dict:
    """The numbers, from the committed datasets rather than from memory."""
    coa = pd.read_csv(ROOT / "data/college_coa_clean.csv")
    careers = pd.read_csv(ROOT / "cleaned_careers.csv")
    prof = pd.read_csv(ROOT / "data/professional_tuition_clean.csv")
    grad = pd.read_csv(ROOT / "data/graduate_tuition_clean.csv")
    return {
        "schools": len(coa),
        "careers": len(careers) + 11,        # + the curated entries in the dropdown
        "programmes": prof["program_key"].nunique(),
        "grad_schools": len(grad),
    }


def features(f: dict) -> list:
    """(headline, supporting line, icon key).

    Six, because six is what a feed thumbnail can carry -- past that the type
    has to shrink below what a phone resolves and the graphic becomes a
    picture of some text. The bodies are deliberately SHORT: the first draft
    ran two full sentences per tile, and at the size a 1200x630 tile actually
    is, that text ran out of its own box and across the row beneath it.
    """
    return [
        ("10-year outcome",
         "Your net position against a debt-free high-school grad", "curve"),
        (f"{f['schools']:,} real schools",
         "Published cost of attendance, priced where you live", "campus"),
        ("The 2026 federal rules",
         "RAP, Tiered Standard, the new Parent PLUS caps", "rules"),
        ("Compare two paths",
         "Two majors, two schools, side by side", "compare"),
        ("Cost of living by city",
         "Metro wages and local prices, not a national average", "city"),
        ("Graduate & professional",
         f"Medicine, dentistry, law, the MBA and {f['programmes'] - 4} more",
         "grad"),
    ]


# --- the logo mark, from the same solved geometry as the SVG ---------------
def draw_mark(ax, x, y, size):
    """The break-even tick. Coordinates come from palette.json, which
    build_logo.py writes -- so moving the logo moves this, and the graphic
    cannot ship last month's mark."""
    g = BRAND["geometry"]
    s = size / 64.0

    def P(px, py):
        # The SVG grid runs y-down; axes run y-up.
        return x + px * s, y + (64 - py) * s

    zero = g["zero_y"]
    ax.plot(*zip(P(4, zero), P(60, zero)), color="#b0b5bd",
            lw=size * 0.040, solid_capstyle="round", zorder=3)
    ax.plot(*zip(P(*g["start"]), P(*g["trough"]), P(g["crossing_x"], zero)),
            color=ORANGE, lw=size * 0.135, solid_capstyle="round",
            solid_joinstyle="round", zorder=4)
    ax.plot(*zip(P(g["crossing_x"], zero), P(*g["end"])),
            color=BLUE, lw=size * 0.135, solid_capstyle="round", zorder=4)
    cx, cy = P(g["crossing_x"], zero)
    ax.add_patch(Circle((cx, cy), size * 0.088, facecolor=BLUE,
                        edgecolor=SURFACE, lw=size * 0.038, zorder=5))


# --- tile icons -----------------------------------------------------------
def draw_icon(ax, key, x, y, s, accent):
    """A small diagram in a 1x1 cell at (x, y), scaled by s."""
    def L(pts, color=accent, lw=2.6):
        ax.plot([x + px * s for px, _ in pts], [y + py * s for _, py in pts],
                color=color, lw=lw * s / 34, solid_capstyle="round",
                solid_joinstyle="round", zorder=4)

    def BAR(bx, by, bw, bh, color=accent):
        ax.add_patch(FancyBboxPatch(
            (x + bx * s, y + by * s), bw * s, bh * s,
            boxstyle="round,pad=0,rounding_size=%f" % (0.06 * s),
            facecolor=color, edgecolor="none", zorder=4))

    if key == "curve":                       # the product's own chart
        L([(0.05, 0.40), (0.95, 0.40)], color="#c3c7cd", lw=1.8)
        L([(0.08, 0.40), (0.30, 0.10), (0.50, 0.40)], color=ORANGE, lw=4.2)
        L([(0.50, 0.40), (0.93, 0.90)], color=BLUE, lw=4.2)
        ax.add_patch(Circle((x + 0.50 * s, y + 0.40 * s), 0.075 * s,
                            facecolor=BLUE, edgecolor=SURFACE,
                            lw=2.0 * s / 34, zorder=5))
    elif key == "campus":
        # A pediment and columns, NOT a bar cluster. In the first set three of
        # the six icons were groups of rounded bars -- campus, compare and
        # city -- so at thumbnail size half the grid looked like the same
        # picture repeated and none of the three named its own feature.
        ax.add_patch(plt.Polygon(
            [(x + 0.50 * s, y + 0.92 * s), (x + 0.04 * s, y + 0.64 * s),
             (x + 0.96 * s, y + 0.64 * s)],
            closed=True, facecolor=accent, edgecolor="none", zorder=4))
        for bx in (0.14, 0.42, 0.70):
            BAR(bx, 0.18, 0.16, 0.42, color="#c3c7cd")
        BAR(0.02, 0.06, 0.96, 0.10, color=accent)
    elif key == "rules":
        BAR(0.18, 0.08, 0.64, 0.84, color="#e2e5ea")
        for i, w in enumerate((0.40, 0.46, 0.30)):
            BAR(0.28, 0.66 - i * 0.19, w, 0.08,
                color=accent if i == 0 else "#b7bcc4")
    elif key == "compare":
        # Two panels of EQUAL size, split by a gutter: the shape of an A/B
        # comparison. Two bars of unequal height is a bar chart, which is what
        # the tile beside it already is.
        BAR(0.04, 0.14, 0.42, 0.72, color="#c3c7cd")
        BAR(0.54, 0.14, 0.42, 0.72, color=accent)
    elif key == "city":
        for bx, bh, c in ((0.04, 0.30, "#c3c7cd"), (0.26, 0.62, accent),
                          (0.48, 0.42, "#c3c7cd"), (0.70, 0.76, "#c3c7cd")):
            BAR(bx, 0.12, 0.18, bh, color=c)
        L([(0.02, 0.12), (0.98, 0.12)], color="#9aa0a8", lw=2.2)
    elif key == "grad":
        # Cap body FIRST and narrow, board over it, tassel off the right
        # corner. Drawn the other way round -- a filled diamond with a U
        # beneath -- the two shapes merged into a house with a chimney.
        BAR(0.30, 0.20, 0.40, 0.34, color="#c3c7cd")
        ax.add_patch(plt.Polygon(
            [(x + 0.04 * s, y + 0.62 * s), (x + 0.50 * s, y + 0.42 * s),
             (x + 0.96 * s, y + 0.62 * s), (x + 0.50 * s, y + 0.82 * s)],
            closed=True, facecolor=accent, edgecolor="none", zorder=5))
        L([(0.88, 0.655), (0.88, 0.26)], color=accent, lw=2.6)
        ax.add_patch(Circle((x + 0.88 * s, y + 0.22 * s), 0.055 * s,
                            facecolor=accent, edgecolor="none", zorder=5))


# --- text that fits, because it was measured ------------------------------
def px_to_pt(px, dpi):
    return px * 72.0 / dpi


def text_width(fig, s, px, font, dpi):
    """Rendered width of one line, in pixels."""
    t = fig.text(0, 0, s, fontproperties=font, fontsize=px_to_pt(px, dpi))
    w = t.get_window_extent(fig.canvas.get_renderer()).width
    t.remove()
    return w


def wrap(fig, s, px, font, dpi, max_w):
    """Greedy wrap on measured width, not on a character count.

    A character count is what the first version effectively used, and it does
    not survive the difference between "5,035 real schools" and "Graduate &
    professional" in the same box.
    """
    words, lines, cur = s.split(), [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if cur and text_width(fig, trial, px, font, dpi) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def fit_line(fig, s, px, font, dpi, max_w, floor=0.6):
    """Shrink a single line until it fits, down to a floor. Used for the
    headline and the tile titles, which must not wrap."""
    size = px
    while size > px * floor and text_width(fig, s, size, font, dpi) > max_w:
        size -= 1
    return size


# --- layout ---------------------------------------------------------------
LAYOUTS = {
    "og-1200x630":     (1200, 630, 3, 2),
    "square-1080":     (1080, 1080, 2, 3),
    "story-1080x1920": (1080, 1920, 2, 3),
    "slide-1920x1080": (1920, 1080, 3, 2),
}


def build_one(name, w, h, cols, rows, f):
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi, facecolor=SURFACE)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, w); ax.set_ylim(0, h); ax.axis("off")
    ax.set_facecolor(SURFACE)
    fig.canvas.draw()

    def T(x, y, s, px, font, color, **kw):
        ax.text(x, y, s, fontproperties=font, fontsize=px_to_pt(px, dpi),
                color=color, **kw)

    pad = min(w, h) * 0.076
    content_w = w - pad * 2
    gap = min(w, h) * 0.025
    gw = (content_w - gap * (cols - 1)) / cols

    HEAD = "Is the degree worth the loan?"
    SUB = "Free · anonymous · no sign-up — real federal data, not estimates"
    SRC = ("Bureau of Labor Statistics · New York Fed · College Scorecard · "
           "IPEDS · CPS ASEC")

    # TWO SCALES, and the split is the point.
    #
    # One scale for everything was the first attempt and it broke both ways.
    # Sized off the canvas, the header was 92px over 20px tile titles on the
    # square -- a ratio of 4.6 where the format that looked right was 2.35.
    # Solved jointly instead, the header then ate a SHORT canvas: on the
    # 1200x630 the block took so much of 630px that the tiles collapsed to
    # unreadable slivers.
    #
    # So the header is capped at a share of the canvas HEIGHT, and the tiles
    # take what is left. The header cannot bully a short format, and the two
    # still move together because the cap only ever binds on the wide ones.
    HEADER_SHARE = 0.30          # of canvas height, header block included
    HEADER_RATIO = 1.484         # header_height / header_scale, from below

    def header_height(sc):
        return (sc * 0.34            # mark
                + sc * 0.30          # gap to headline
                + sc * 0.372         # headline
                + sc * 0.372 * 0.30  # gap to sub
                + sc * 0.140         # sub
                + sc * 0.26)         # gap to grid

    def footer_height(sc):
        return sc * 0.105 * 2.6 + pad * 0.5

    head_scale = min(min(w, h) * 0.22, h * HEADER_SHARE / HEADER_RATIO)
    grid_top_est = h - pad - header_height(head_scale)
    grid_bottom_est = footer_height(head_scale)
    row_h = ((grid_top_est - grid_bottom_est) - gap * (rows - 1)) / rows
    scale = max(min(row_h, gw * 0.62), min(w, h) * 0.06)

    head_px = head_scale * 0.372
    sub_px = head_scale * 0.140
    mark_px = head_scale * 0.34
    word_px = head_scale * 0.185
    src_px = head_scale * 0.105

    # ---- header ----
    top = h - pad
    draw_mark(ax, pad, top - mark_px, mark_px)
    T(pad + mark_px * 1.30, top - mark_px * 0.52, "worthmydegree.com",
      word_px, HEAVY, INK, va="center", ha="left")

    head_px = fit_line(fig, HEAD, head_px, HEAVY, dpi, content_w)
    head_top = top - mark_px - head_scale * 0.30
    T(pad, head_top, HEAD, head_px, HEAVY, INK, va="top", ha="left")

    sub_px = fit_line(fig, SUB, sub_px, BOOK, dpi, content_w)
    sub_top = head_top - head_px * 1.30
    T(pad, sub_top, SUB, sub_px, BOOK, MUTED, va="top", ha="left")

    # ---- footer ----
    foot_y = footer_height(head_scale) * 0.42
    src_px = fit_line(fig, SRC, src_px, BOOK, dpi, content_w)
    ax.plot([pad, w - pad], [foot_y + src_px * 1.9] * 2, color=RULE,
            lw=1.4, zorder=2)
    T(pad, foot_y, SRC, src_px, BOOK, MUTED, va="center", ha="left")

    # ---- tile grid ----
    grid_top = sub_top - sub_px * 1.1 - head_scale * 0.26
    grid_bottom = foot_y + src_px * 3.4
    avail_h = grid_top - grid_bottom

    # THE TILE FITS ITS CONTENT; it does not stretch to fill the canvas.
    # Stretched, the story format gave every tile 410px of height for 250px of
    # text and the grid read as six mostly-empty boxes. So the height is
    # MEASURED -- wrap the bodies, add up what they need, take the tallest --
    # and the finished block is centred in the space available.
    def measure(sc):
        ipad = sc * 0.115
        isize = sc * 0.26
        title_px = sc * 0.155
        body_px = sc * 0.115
        line_h = body_px * 1.38
        inner_w = gw - ipad * 2
        wrapped = [wrap(fig, body, body_px, BOOK, dpi, inner_w)
                   for _t, body, _i in features(f)]
        needed = max(ipad * 2 + isize + sc * 0.075 + title_px * 1.30
                     + len(lines) * line_h for lines in wrapped)
        return dict(ipad=ipad, isize=isize, title_px=title_px,
                    body_px=body_px, line_h=line_h, inner_w=inner_w,
                    wrapped=wrapped, tile_h=needed)

    m = measure(scale)
    total = m["tile_h"] * rows + gap * (rows - 1)
    if total > avail_h:
        # One correction pass, scaled by exactly the overshoot: a smaller body
        # can also re-wrap to fewer lines, so this is re-measured, not scaled.
        m = measure(scale * (avail_h / total) * 0.98)
        total = m["tile_h"] * rows + gap * (rows - 1)
    gh = m["tile_h"]
    block_top = grid_top - max(0.0, (avail_h - total) / 2)

    for i, (title, body, icon) in enumerate(features(f)):
        r, c = divmod(i, cols)
        tx = pad + c * (gw + gap)
        ty = block_top - (r + 1) * gh - r * gap
        ax.add_patch(FancyBboxPatch(
            (tx, ty), gw, gh,
            boxstyle="round,pad=0,rounding_size=%f" % (scale * 0.09),
            facecolor=TILE, edgecolor="none", zorder=1))

        accent = BLUE if i % 2 == 0 else ORANGE
        cursor = ty + gh - m["ipad"]
        draw_icon(ax, icon, tx + m["ipad"], cursor - m["isize"], m["isize"],
                  accent)
        cursor -= m["isize"] + m["ipad"] * 0.65

        tpx = fit_line(fig, title, m["title_px"], BOLD, dpi, m["inner_w"])
        T(tx + m["ipad"], cursor, title, tpx, BOLD, INK, va="top", ha="left")
        cursor -= m["title_px"] * 1.30

        for line in m["wrapped"][i]:
            T(tx + m["ipad"], cursor, line, m["body_px"], BOOK, MUTED,
              va="top", ha="left")
            cursor -= m["line_h"]
        if cursor < ty - 1:
            print(f"    ! tile {i+1} ({name}) overflows by "
                  f"{ty - cursor:.0f}px -- shorten its body")

    path = OUT / f"feature-{name}.png"
    fig.savefig(path, dpi=dpi, facecolor=SURFACE)
    plt.close(fig)
    return path


def build():
    f = facts()
    print("facts read from the datasets:", f)
    for name, (w, h, cols, rows) in LAYOUTS.items():
        p = build_one(name, w, h, cols, rows, f)
        print(f"  wrote brand/{p.name}  ({w}x{h})")


if __name__ == "__main__":
    build()

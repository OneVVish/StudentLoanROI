#!/usr/bin/env python3
"""Generate the worthmydegree.com logo set as self-contained SVG.

    python3 brand/build_logo.py

WHY A GENERATOR AND NOT A DRAWING. Two reasons, both about the wordmark.

An SVG that names a font renders differently on every machine that lacks it,
which for a logo is not a style difference -- it is a different logo. So the
type is converted to OUTLINES here, and the emitted files reference no font at
all. And because the outlining is a script rather than a one-time export, the
whole set regenerates from one place when the wordmark, the palette or the
geometry changes: five files that must agree cannot be kept in agreement by
hand.

THE MARK IS THE PRODUCT'S OWN CHART. Net position over time starts at zero,
falls while tuition and foregone earnings accumulate, and crosses back above
zero at break-even. That shape is a checkmark. It reads as "worth it" at a
glance and as the actual curve the calculator draws when you look twice --
the cost stroke in orange, the return stroke in blue, and a dot on the
zero line at the moment the loan has paid for itself.

The two hues are the app's own SERIES_ORANGE and SERIES_BLUE, which pass the
dataviz validator's six checks on both the white and #0E1117 surfaces
(CVD dE 24.7 light / 26.8 dark). A logo does not strictly owe anyone
colourblind separation, but these two carry meaning here -- cost against
return -- and the app already has to pass, so reusing the validated steps
costs nothing and keeps one palette across the product and its brand.
"""
import json
from pathlib import Path

from matplotlib import font_manager as fm
from matplotlib.textpath import TextPath
from matplotlib.font_manager import FontProperties

OUT = Path(__file__).parent

# --- palette -------------------------------------------------------------
# The app's own steps, so the brand and the product cannot drift apart.
LIGHT = {"cost": "#eb6834", "gain": "#2a78d6",
         "ink": "#1a1c1f", "muted": "#8a8f98", "rule": "#b0b5bd"}
DARK = {"cost": "#d95926", "gain": "#3987e5",
        "ink": "#f2f4f7", "muted": "#9aa1ab", "rule": "#484e57"}

WORDMARK = "worthmydegree"
SUFFIX = ".com"
FONT = FontProperties(family="Avenir Next", weight="demibold")


# --- the mark ------------------------------------------------------------
# One 64x64 grid, so every variant is the same drawing at a different size
# rather than a redraw that has to be kept in step.
#
# The numbers are the curve's own story: start ON the zero line at (10,40),
# fall to a trough at (24,52) -- four years of tuition and no salary -- then
# climb to (54,12). The rise crosses y=40 at x=33, which is where break-even
# is marked. Moving any point means recomputing that crossing; CROSS_X is
# derived below rather than typed, because a dot that sits near the line
# instead of on it is the one flaw this mark cannot survive.
START = (8.0, 38.0)
TROUGH = (24.0, 54.0)
END = (58.0, 8.0)
ZERO_Y = 38.0
CROSS_X = TROUGH[0] + (END[0] - TROUGH[0]) * (
    (TROUGH[1] - ZERO_Y) / (TROUGH[1] - END[1]))

# Sized against the WORDMARK, not against the box. At the first weights the
# mark was drawn to fit its 64px grid politely and the lockup read as a word
# with a small ornament in front of it -- the curve spanned 40px of thin
# 7-unit stroke beside a cap height of 31. It now spans 46 at 8.5, which is
# the balance the lockup needs; the box is the same, only the drawing grew
# into it.
STROKE = 8.5
RULE_W = 2.6        # the zero line: recessive, but it must not read as a hairline
                    # artefact, which at 2.2 against #c9ccd1 is what it did
DOT_R = 5.6


def mark_svg(c, *, rule=True, dot=True, mono=None, stroke=STROKE, ring=True):
    """The mark's inner SVG, on the 64x64 grid.

    mono collapses both strokes to one colour for single-colour printing and
    for the smallest favicon, where two hues meeting at a 1px junction turn to
    mud. rule/dot drop the zero line and the break-even dot at sizes where
    they stop being legible and start being noise -- below about 24px the dot
    is under a pixel across, and a sub-pixel dot does not read as smaller, it
    reads as a smudge.
    """
    cost = mono or c["cost"]
    gain = mono or c["gain"]
    parts = []
    if rule:
        parts.append(
            f'<line x1="4" y1="{ZERO_Y}" x2="60" y2="{ZERO_Y}" '
            f'stroke="{c["rule"]}" stroke-width="{RULE_W}" stroke-linecap="round"/>')
    parts.append(
        f'<path d="M {START[0]} {START[1]} L {TROUGH[0]} {TROUGH[1]} '
        f'L {CROSS_X:.3f} {ZERO_Y}" fill="none" stroke="{cost}" '
        f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round"/>')
    parts.append(
        f'<path d="M {CROSS_X:.3f} {ZERO_Y} L {END[0]} {END[1]}" fill="none" '
        f'stroke="{gain}" stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-linejoin="round"/>')
    if dot:
        # A ring in the surface colour, not a gap: the dot sits ON the stroke,
        # and without the ring the join between the two hues reads as a kink.
        #
        # ring=False for the one-colour file. It hardcoded --surface to white,
        # which is correct on paper and paints a WHITE DISC on a dark
        # background -- the single-colour variant exists precisely to be
        # dropped on any background, so it cannot carry a colour of its own.
        # Without the ring the dot merges into the stroke, which in one
        # colour is the honest rendering anyway.
        stroke_attr = (' stroke="var(--surface)" stroke-width="2.4"'
                       if ring else '')
        parts.append(
            f'<circle cx="{CROSS_X:.3f}" cy="{ZERO_Y}" r="{DOT_R}" '
            f'fill="{gain}"{stroke_attr}/>')
    return "\n    ".join(parts)


# --- the wordmark, as outlines ------------------------------------------
def text_path(text, size, font=FONT):
    """(svg path data, advance width, cap height) for text, y-DOWN.

    matplotlib's y runs up and SVG's runs down, so every vertex is flipped
    here rather than by a transform on the emitted path -- a flip in the file
    would have to be re-applied by anyone who copies one glyph out of it.
    """
    tp = TextPath((0, 0), text, size=size, prop=font)
    verts, codes = tp.vertices, tp.codes
    out, i = [], 0
    while i < len(codes):
        code = codes[i]
        if code == 1:                                    # MOVETO
            x, y = verts[i]; out.append(f"M {x:.2f} {-y:.2f}"); i += 1
        elif code == 2:                                  # LINETO
            x, y = verts[i]; out.append(f"L {x:.2f} {-y:.2f}"); i += 1
        elif code == 3:                                  # CURVE3 (quadratic)
            (x1, y1), (x, y) = verts[i], verts[i + 1]
            out.append(f"Q {x1:.2f} {-y1:.2f} {x:.2f} {-y:.2f}"); i += 2
        elif code == 4:                                  # CURVE4 (cubic)
            (x1, y1), (x2, y2), (x, y) = verts[i], verts[i + 1], verts[i + 2]
            out.append(f"C {x1:.2f} {-y1:.2f} {x2:.2f} {-y2:.2f} "
                       f"{x:.2f} {-y:.2f}"); i += 3
        elif code == 79:                                 # CLOSEPOLY
            out.append("Z"); i += 1
        else:
            i += 1
    bb = tp.get_extents()
    return " ".join(out), bb.x1, bb.y1


def svg_document(width, height, body, *, surface=None, title=""):
    """A standalone SVG. --surface is a real custom property so the dot's ring
    matches whatever the file is placed on; it is given a concrete fallback
    because a logo is routinely dropped into renderers that resolve no
    variables at all."""
    surface = surface or "#ffffff"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-label="{title}">\n'
        f'  <title>{title}</title>\n'
        f'  <g style="--surface: {surface}">\n    {body}\n  </g>\n</svg>\n')


def build():
    written = []
    for theme, c in (("light", LIGHT), ("dark", DARK)):
        surface = "#ffffff" if theme == "light" else "#0E1117"

        # ---- horizontal lockup ----
        size = 40
        word_d, word_w, _ = text_path(WORDMARK, size)
        suf_d, suf_w, _ = text_path(SUFFIX, size * 0.80)
        gap = 18                      # mark to wordmark
        mark_box = 64
        # Optical centring: the wordmark's cap height, not its bounding box.
        # Descenders in "y" would otherwise push the whole word up.
        baseline_y = 42
        text_x = mark_box + gap
        total_w = text_x + word_w + 6 + suf_w + 8
        body = (
            f'<g>{mark_svg(c)}</g>\n'
            f'    <g transform="translate({text_x}, {baseline_y})">\n'
            f'      <path d="{word_d}" fill="{c["ink"]}"/>\n'
            f'    </g>\n'
            f'    <g transform="translate({text_x + word_w + 6}, {baseline_y})">\n'
            f'      <path d="{suf_d}" fill="{c["muted"]}"/>\n'
            f'    </g>')
        name = f"logo-horizontal-{theme}.svg"
        (OUT / name).write_text(svg_document(
            round(total_w), 64, body, surface=surface,
            title="worthmydegree.com"))
        written.append(name)

        # ---- stacked ----
        s_word_d, s_word_w, _ = text_path(WORDMARK, 34)
        s_suf_d, s_suf_w, _ = text_path(SUFFIX, 34 * 0.78)
        block_w = s_word_w + 5 + s_suf_w
        stack_w = max(block_w + 16, 96)
        mark_x = (stack_w - 64) / 2
        text_x = (stack_w - block_w) / 2
        body = (
            f'<g transform="translate({mark_x:.2f}, 0)">{mark_svg(c)}</g>\n'
            f'    <g transform="translate({text_x:.2f}, 100)">\n'
            f'      <path d="{s_word_d}" fill="{c["ink"]}"/>\n'
            f'    </g>\n'
            f'    <g transform="translate({text_x + s_word_w + 5:.2f}, 100)">\n'
            f'      <path d="{s_suf_d}" fill="{c["muted"]}"/>\n'
            f'    </g>')
        name = f"logo-stacked-{theme}.svg"
        (OUT / name).write_text(svg_document(
            round(stack_w), 112, body, surface=surface, title="worthmydegree.com"))
        written.append(name)

        # ---- mark alone ----
        name = f"mark-{theme}.svg"
        (OUT / name).write_text(svg_document(
            64, 64, mark_svg(c), surface=surface,
            title="worthmydegree.com mark"))
        written.append(name)

        # ---- favicon: no rule, no dot, heavier stroke ----
        # At 16px the zero line is a grey hair and the dot is under a pixel.
        # What survives is the two-tone tick, which is the identity anyway.
        name = f"favicon-{theme}.svg"
        (OUT / name).write_text(svg_document(
            64, 64, mark_svg(c, rule=False, dot=False, stroke=9.5),
            surface=surface, title="worthmydegree.com"))
        written.append(name)

    # ---- one-colour, for a stamp, an embroidery file or a fax ----
    word_d, word_w, _ = text_path(WORDMARK, 40)
    suf_d, suf_w, _ = text_path(SUFFIX, 40 * 0.80)
    body = (
        f'<g>{mark_svg(LIGHT, mono="currentColor", ring=False)}</g>\n'
        f'    <g transform="translate({64 + 18}, 42)">'
        f'<path d="{word_d}" fill="currentColor"/></g>\n'
        f'    <g transform="translate({64 + 18 + word_w + 6}, 42)" opacity="0.55">'
        f'<path d="{suf_d}" fill="currentColor"/></g>')
    mono = svg_document(round(64 + 18 + word_w + 6 + suf_w + 8), 64, body,
                        title="worthmydegree.com")
    # currentColor everywhere means one file serves black-on-white and
    # white-on-black; the rule inherits it too, at reduced opacity.
    mono = mono.replace(f'stroke="{LIGHT["rule"]}"',
                        'stroke="currentColor" opacity="0.3"')
    (OUT / "logo-mono.svg").write_text(mono)
    written.append("logo-mono.svg")

    # ---- theme-AUTO horizontal lockup, for st.logo ----
    # Streamlit's st.logo renders ONE image on both the light and dark theme
    # and its docs say to pick one that works on both. The mark already does
    # (its two hues pass the validator on both surfaces); only the wordmark
    # ink cannot -- near-black on a dark sidebar disappears. An SVG loaded
    # through <img> still applies its own internal media queries, so the ink
    # switches with prefers-color-scheme while the file stays one file.
    # Streamlit's theme toggle follows the OS by default, which is exactly
    # the signal this reads; a visitor who forces the app theme against
    # their OS gets the OS's ink, the one case this cannot see.
    word_d, word_w, _ = text_path(WORDMARK, 40)
    suf_d, suf_w, _ = text_path(SUFFIX, 40 * 0.80)
    auto_body = (
        f'<style>\n'
        f'      .ink {{ fill: {LIGHT["ink"]}; }} .muted {{ fill: {LIGHT["muted"]}; }}\n'
        f'      @media (prefers-color-scheme: dark) {{\n'
        f'        .ink {{ fill: {DARK["ink"]}; }} .muted {{ fill: {DARK["muted"]}; }}\n'
        f'      }}\n'
        f'    </style>\n'
        f'    <g>{mark_svg(LIGHT, ring=False)}</g>\n'
        f'    <g transform="translate({64 + 18}, 42)">'
        f'<path class="ink" d="{word_d}"/></g>\n'
        f'    <g transform="translate({64 + 18 + word_w + 6}, 42)">'
        f'<path class="muted" d="{suf_d}"/></g>')
    (OUT / "logo-horizontal-auto.svg").write_text(svg_document(
        round(64 + 18 + word_w + 6 + suf_w + 8), 64, auto_body,
        title="worthmydegree.com"))
    written.append("logo-horizontal-auto.svg")

    # ---- favicon PNGs, for st.set_page_config ----
    # page_icon takes a raster; no SVG rasteriser is installed, but matplotlib
    # is -- and the favicon variant is three strokes, which matplotlib draws
    # exactly. Same geometry constants, so the tab icon cannot drift from the
    # SVG favicon.
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    for px in (32, 64):
        fig = Figure(figsize=(1, 1), dpi=px)
        FigureCanvasAgg(fig)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, 64); ax.set_ylim(64, 0); ax.axis("off")
        lw = 9.5 * px / 64
        ax.plot([START[0], TROUGH[0], CROSS_X], [START[1], TROUGH[1], ZERO_Y],
                color=LIGHT["cost"], lw=lw, solid_capstyle="round",
                solid_joinstyle="round")
        ax.plot([CROSS_X, END[0]], [ZERO_Y, END[1]],
                color=LIGHT["gain"], lw=lw, solid_capstyle="round")
        fig.savefig(OUT / f"favicon-{px}.png", transparent=True)
        written.append(f"favicon-{px}.png")

    (OUT / "palette.json").write_text(json.dumps(
        {"light": LIGHT, "dark": DARK,
         "geometry": {"start": START, "trough": TROUGH, "end": END,
                      "zero_y": ZERO_Y, "crossing_x": round(CROSS_X, 3)}},
        indent=2) + "\n")
    written.append("palette.json")
    for n in written:
        print("  wrote brand/" + n)
    print(f"\ncrossing point solved at x={CROSS_X:.3f} on the zero line y={ZERO_Y}")


if __name__ == "__main__":
    build()

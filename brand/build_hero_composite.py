#!/usr/bin/env python3
"""Paste a REAL screenshot onto the blank monitor in a generated hero.

    python3 brand/build_hero_composite.py --slug <slug> --shot <screenshot.png>
    python3 brand/build_hero_composite.py --slug <slug> --detect-only

WHY THIS EXISTS AND WHY IT IS NOT A PROMPT. A guide hero that shows this
site's own tool on a screen has to show the REAL tool. A diffusion model asked
for "our repayment calculator on a monitor" invents the labels, the numbers and
the charts, and a fabricated picture of our own product is the one thing a site
built on traceable figures cannot publish: a reader takes it for a screenshot,
clicks through, and finds something else.

So the hero is generated with the screen deliberately BLANK, and a real
screenshot is perspective-mapped onto it here. Everything on the screen in the
finished image came out of the running app.

THE SCREENSHOT IS NOT PRODUCED HERE, AND CANNOT BE. Headless Chrome hangs on
this machine, verified twice including on a trivial local file with a fresh
profile, and the browser extension returns images into the conversation rather
than writing them to disk. Capture it by hand, and capture it with `?test=1`
or the session writes rows to the production research dataset:

    python3 -m streamlit run app.py --server.port 8502
    localhost:8502/?tool=repayment&test=1&rb=50000&rr=8.5&ri=50000

THE CORNERS ARE DETECTED, NOT TYPED, because a reseeded hero moves the monitor
and hand-typed corners would silently paste the screenshot into the wall. The
detector looks for the largest dark near-rectangular region and prints what it
found; --detect-only draws the outline so it can be checked before compositing.
If detection ever picks the wrong thing, pass --corners explicitly rather than
loosening the threshold.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
STATIC = REPO / "static"
BRAND = Path(__file__).resolve().parent
DARK_MAX = 90          # a switched-off screen; the desk and wall are far lighter
MIN_PANEL_WIDTH_FRAC = 0.15   # a monitor panel spans a good part of the frame
MIN_PANEL_ROWS = 60           # and is tall; a dark pen or cable is not


def detect_screen(img: Image.Image):
    """Corners of the monitor's dark panel, as (tl, tr, br, bl).

    ROW RUNS, NOT CONNECTED COMPONENTS. The first version took the largest dark
    blob and got the monitor, its stand AND the keyboard as one region, because
    all three are dark and all three touch. The outline it drew spanned half
    the desk.

    A screen is distinguishable by SHAPE rather than by darkness: its rows
    carry one long unbroken run of dark pixels, where the stand's rows carry a
    narrow one and the desk's carry none. So take the topmost band of
    consecutive rows whose longest dark run is wide, and that is the panel.
    """
    g = np.asarray(img.convert("L"), dtype=np.int16)
    dark = g <= DARK_MAX
    h, w = dark.shape

    def longest_run(row):
        best = cur = start = best_start = 0
        for x, v in enumerate(row):
            if v:
                if cur == 0:
                    start = x
                cur += 1
                if cur > best:
                    best, best_start = cur, start
            else:
                cur = 0
        return best, best_start

    runs = [longest_run(dark[y]) for y in range(h)]
    wide = [y for y, (n, _) in enumerate(runs) if n >= MIN_PANEL_WIDTH_FRAC * w]
    if not wide:
        sys.exit(f"no row carries a dark run of {MIN_PANEL_WIDTH_FRAC:.0%} of the "
                 f"frame width; is the screen blank and facing the camera?")
    # The topmost consecutive band of those rows. Anything lower is the
    # keyboard, which is also wide and also dark.
    top = wide[0]
    bottom = top
    for y in wide:
        if y - bottom > 2:
            break
        bottom = y
    if bottom - top < MIN_PANEL_ROWS:
        sys.exit(f"the widest dark band is only {bottom - top} rows tall; "
                 f"that is not a monitor panel")
    # FIT BOTH EDGES ACROSS EVERY ROW IN THE BAND, rather than trusting the
    # single top row and the single bottom row. A highlight on the bezel breaks
    # one row's run and moves that corner by a hundred pixels: the first version
    # read the panel's top-left at x=781 where it belongs near x=660. A least
    # squares line through every row is immune to one bad row and handles the
    # slight trapezoid of a monitor that is not perfectly square to the camera.
    # THE SIDE EDGES COME FROM THE FIRST AND LAST DARK PIXEL IN EACH ROW, not
    # from the bounds of that row's LONGEST run. The longest run jumps sideways
    # wherever something interrupts it, and the badge on a monitor's chin does
    # exactly that: the fitted left edge came out sloping ninety pixels across
    # the screen and the outline cut the panel in half diagonally.
    band = list(range(top, bottom + 1))
    ys, lefts, rights = [], [], []
    for y in band:
        idx = np.nonzero(dark[y])[0]
        if idx.size < MIN_PANEL_WIDTH_FRAC * w:
            continue
        ys.append(y); lefts.append(idx[0]); rights.append(idx[-1])
    if len(ys) < 20:
        sys.exit("could not trace the panel's side edges")
    ys = np.array(ys, float)
    lefts = np.array(lefts, float)
    rights = np.array(rights, float)

    def robust_fit(x, y):
        """Least squares, then refit without the worst tenth of residuals.

        One stray row (a reflection, a cable crossing the bezel) drags a plain
        fit visibly on an edge only a few hundred pixels long.
        """
        m, c = np.polyfit(x, y, 1)
        keep = np.abs(y - (m * x + c)) <= np.quantile(np.abs(y - (m * x + c)), 0.9)
        return np.polyfit(x[keep], y[keep], 1) if keep.sum() > 10 else (m, c)

    lm, lc = robust_fit(ys, lefts)
    rm, rc = robust_fit(ys, rights)

    # FIT THE TOP AND BOTTOM AS LINES TOO, rather than taking the band's first
    # and last row as horizontals. A monitor that is a degree or two off square
    # has a SLOPED top edge, and a horizontal top edge drawn across it sits
    # above the bezel on the high side: the paste then overhangs the monitor
    # onto the wall, which is exactly what shipped once. Scan each column of
    # the panel for its topmost and bottommost dark pixel and fit those.
    inner = range(int(max(lefts.min(), 0)) + 2, int(min(rights.max(), w)) - 2)
    cols, tops, bots = [], [], []
    for x in inner:
        col = dark[top:bottom + 1, x]
        idx = np.nonzero(col)[0]
        if idx.size < 0.5 * (bottom - top):      # skip columns the panel misses
            continue
        cols.append(x); tops.append(top + idx[0]); bots.append(top + idx[-1])
    if len(cols) < 20:
        sys.exit("could not trace the panel's top and bottom edges")
    tm, tc = robust_fit(np.array(cols, float), np.array(tops, float))
    bm, bc = robust_fit(np.array(cols, float), np.array(bots, float))

    def corner(edge_m, edge_c, side_m, side_c):
        """Intersect a sloped horizontal edge with a sloped vertical edge."""
        # y = edge_m*x + edge_c   and   x = side_m*y + side_c
        y = (edge_m * side_c + edge_c) / (1.0 - edge_m * side_m)
        return (side_m * y + side_c, y)

    return [corner(tm, tc, lm, lc), corner(tm, tc, rm, rc),
            corner(bm, bc, rm, rc), corner(bm, bc, lm, lc)]


def perspective_coeffs(dst, src):
    """PIL's PERSPECTIVE wants the inverse map, dst -> src."""
    m = []
    for (dx, dy), (sx, sy) in zip(dst, src):
        m.append([dx, dy, 1, 0, 0, 0, -sx * dx, -sx * dy])
        m.append([0, 0, 0, dx, dy, 1, -sy * dx, -sy * dy])
    A = np.array(m, dtype=float)
    b = np.array(src, dtype=float).reshape(8)
    return np.linalg.solve(A, b)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--shot", help="PNG of the real app screen")
    ap.add_argument("--detect-only", action="store_true")
    ap.add_argument("--corners", help="x1,y1,x2,y2,x3,y3,x4,y4 (tl,tr,br,bl)")
    # PER EDGE, NOT ONE NUMBER. The detector finds the dark PANEL, which is
    # glass plus bezel, and a monitor's bezel is not the same width all the way
    # round: the chin under the screen is two or three times the side bezels
    # and usually carries the maker's badge. A single uniform inset that clears
    # the chin eats into the picture at the top and sides, and one that fits
    # the sides leaves the paste sitting over the badge.
    ap.add_argument("--inset-x", type=float, default=0.018)
    ap.add_argument("--inset-top", type=float, default=0.022)
    ap.add_argument("--inset-bottom", type=float, default=0.085)
    ap.add_argument("--stretch", action="store_true",
                    help="force the shot onto the quad, distorting it; padding "
                         "to the screen's aspect is the default and is correct")
    ap.add_argument("--dim", type=float, default=0.93,
                    help="slight darkening so the paste reads as a lit screen")
    a = ap.parse_args()

    blank = BRAND / f"guide-hero-{a.slug}-klein4b.png"
    hero = STATIC / f"guide-hero-{a.slug}-klein4b.png"
    if not blank.exists():
        sys.exit(f"no blank hero at {blank}; generate it with build_ai_hero.py")
    # ALWAYS composite from the pristine brand/ copy, never from static/, which
    # may already carry a paste. This makes the script idempotent: run it twice
    # and you get the same picture, not a chart on top of a chart.
    base = Image.open(blank).convert("RGB")

    if a.corners:
        v = [float(x) for x in a.corners.split(",")]
        quad = [(v[0], v[1]), (v[2], v[3]), (v[4], v[5]), (v[6], v[7])]
    else:
        quad = detect_screen(base)
    # Pull each edge in by its own fraction of the panel, so the paste lands on
    # the glass. A screenshot sitting over the bezel reads as a sticker stuck
    # to the monitor rather than as something the monitor is displaying.
    (tlx, tly), (trx, try_), (brx, bry), (blx, bly) = quad
    pw = ((trx - tlx) + (brx - blx)) / 2.0
    ph = ((bly - tly) + (bry - try_)) / 2.0
    dx, dt, db = a.inset_x * pw, a.inset_top * ph, a.inset_bottom * ph
    quad = [(tlx + dx, tly + dt), (trx - dx, try_ + dt),
            (brx - dx, bry - db), (blx + dx, bly - db)]
    print(f"  hero   {hero.name}  ({base.width}x{base.height})")
    print(f"  screen tl={quad[0]} tr={quad[1]} br={quad[2]} bl={quad[3]}")

    if a.detect_only or not a.shot:
        out = base.copy()
        from PIL import ImageDraw
        ImageDraw.Draw(out).polygon(quad, outline=(255, 80, 0))
        p = STATIC / f"_detect-{a.slug}.png"
        out.save(p)
        print(f"  wrote {p}  (outline only; check it, then rerun with --shot)")
        return 0

    shot = Image.open(a.shot).convert("RGB")
    if not a.stretch:
        # PAD TO THE SCREEN'S ASPECT RATHER THAN STRETCH TO IT. The quad is
        # whatever shape the monitor in the photograph is, and the chart is
        # whatever shape matplotlib drew; forcing one onto the other visibly
        # squashes the type and the bars, and a distorted chart of ours is a
        # misrepresentation of ours. Padding reads as the chart displayed in a
        # window that is wider than it needs, which is what it would be.
        qw = ((quad[1][0] - quad[0][0]) + (quad[2][0] - quad[3][0])) / 2.0
        qh = ((quad[3][1] - quad[0][1]) + (quad[2][1] - quad[1][1])) / 2.0
        want = qw / qh
        have = shot.width / shot.height
        if abs(want - have) > 0.01:
            if have < want:
                w2, h2 = int(round(shot.height * want)), shot.height
            else:
                w2, h2 = shot.width, int(round(shot.width / want))
            bg = shot.getpixel((0, 0))          # the chart's own paper colour
            padded = Image.new("RGB", (w2, h2), bg)
            padded.paste(shot, ((w2 - shot.width) // 2, (h2 - shot.height) // 2))
            print(f"  padded the shot {shot.width}x{shot.height} -> {w2}x{h2} "
                  f"to match the screen's {want:.2f}:1 without stretching")
            shot = padded
    coeffs = perspective_coeffs(quad, [(0, 0), (shot.width, 0),
                                       (shot.width, shot.height), (0, shot.height)])
    warped = shot.transform(base.size, Image.PERSPECTIVE, coeffs, Image.BICUBIC)
    mask = Image.new("L", base.size, 0)
    from PIL import ImageDraw
    ImageDraw.Draw(mask).polygon(quad, fill=255)
    if a.dim != 1.0:
        warped = Image.eval(warped, lambda v: int(v * a.dim))
    base.paste(warped, (0, 0), mask)
    base.save(hero)
    print(f"  composited the real screenshot onto {hero.name}")
    print("  now rerun: python3 infra/build_site.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())

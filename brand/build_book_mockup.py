#!/usr/bin/env python3
"""The book as an object: the front cover in perspective, with a spine and a
shadow, for the landing page's book band.

    python3 brand/build_book_mockup.py            # -> static/book-3d.png

WHY DRAWN AND NOT DOWNLOADED. Every "free 3D book mockup" generator either
watermarks the output or grants a licence nobody can produce on request, and
this image goes on a page that says every asset here is the project's own.
The cover is already ours (brand/build_book_cover.py writes static/cover-mark.jpg
at 1600x2560), so the render is a perspective transform of a file we made,
which needs Pillow and nothing else. Pillow ships already: matplotlib depends
on it and app.py imports PIL directly.

WHAT MAKES IT READ AS A BOOK rather than a tilted picture, in order of how
much each contributes:

  1. The SPINE. A cover alone at an angle is a photograph of a poster. The
     sliver of spine down the left is the whole illusion, and it is drawn in
     the cover's own edge colour darkened, so it cannot clash with art it was
     sampled from.
  2. The VERTICAL SHEAR. The front panel's far edge is shorter than its near
     edge, which is what a camera does to a rectangle turned a few degrees.
     Pillow's QUAD transform does it exactly: give it the four corners the
     panel should land on and it solves the mapping.
  3. The SHADOW, offset down and right, blurred. Without it the object floats.
     Drawn on its own layer and composited, never painted over the cover.
  4. The PAGE EDGE, a two-pixel cream line inside the right edge. Small, and
     it is the difference between a solid slab and a stack of paper.

TRANSPARENT PNG, NOT JPEG. The render has a soft shadow and rounded corners,
so it needs an alpha channel; a JPEG would put a grey rectangle behind it on
the band's tinted card. That costs bytes, which is why the output is sized for
the 180px the band draws it at (2x for retina) rather than for the archive.

THE ONLY FIGURE HERE IS AN ANGLE. Nothing in this file is a claim about the
book, so it needs no source and carries no text.
"""
import argparse
import sys
from pathlib import Path

from PIL import Image, ImageFilter

REPO = Path(__file__).resolve().parents[1]
COVER = REPO / "static" / "cover-mark.jpg"
OUT = REPO / "static" / "book-3d.png"

# 2x the ~180px the landing band draws it at. A retina panel asks for two
# device pixels per CSS pixel and nothing here asks for more.
WIDTH = 460
# How much shorter the far (left) edge is than the near one, as a share of the
# cover's height. 0.055 is a few degrees: enough to read as an angle, little
# enough that the title stays square enough to read.
TILT = 0.055
# The spine's width as a share of the panel's. A 219-page book on cream stock
# is 0.5475in against a 6in front, which is 9%; drawn at that it looks like a
# ream, because perspective foreshortens the spine and not the front. 0.055 is
# what that 9% looks like turned about fifteen degrees.
SPINE_W = 0.055
SHADOW_BLUR = 9
SHADOW_ALPHA = 78


def edge_colour(im):
    """The median of the cover's own border pixels, the rule the wrap's back
    panel and the landing cards both follow: a corner pixel would let one
    stray mark choose the colour for the whole spine."""
    w, h = im.size
    px = ([im.getpixel((x, 0)) for x in range(0, w, 7)]
          + [im.getpixel((x, h - 1)) for x in range(0, w, 7)]
          + [im.getpixel((0, y)) for y in range(0, h, 7)]
          + [im.getpixel((w - 1, y)) for y in range(0, h, 7)])
    return tuple(sorted(c[i] for c in px)[len(px) // 2] for i in range(3))


def darken(rgb, f):
    return tuple(max(0, min(255, round(c * f))) for c in rgb)


def build(width: int = WIDTH) -> Path:
    if not COVER.exists():
        sys.exit(f"  {COVER.name} is missing; run brand/build_book_cover.py first.")
    cover = Image.open(COVER).convert("RGB")
    ratio = cover.height / cover.width

    spine_w = round(width * SPINE_W)
    face_w = width - spine_w
    face_h = round(face_w * ratio)
    drop = round(face_h * TILT)          # how far the near edge hangs lower
    pad = SHADOW_BLUR * 3                # room for the blur to fall into
    canvas_w, canvas_h = width + pad * 2, face_h + drop + pad * 2

    # THE FRONT PANEL, ONE TRANSFORM AND NOT TWO. The first version ran a
    # QUAD perspective AND a column shear over its result, then cropped the
    # shear's source to face_h, which threw away the bottom `drop` rows: the
    # author's name and the URL came off the bottom of the cover. A column
    # shear alone is the whole effect, so that is all it does now.
    #
    # Every column x slides down by drop * x / face_w, so the far (left) edge
    # sits high and the near (right) edge low, which is a rectangle turned a
    # few degrees toward the viewer.
    flat = cover.convert("RGBA").resize((face_w, face_h), Image.LANCZOS)
    face = Image.new("RGBA", (face_w, face_h + drop), (0, 0, 0, 0))
    for x in range(face_w):
        face.paste(flat.crop((x, 0, x + 1, face_h)),
                   (x, round(drop * x / face_w)))

    ink = edge_colour(cover)
    # THE SPINE, a parallelogram: its top rises as it goes left, mirroring the
    # front's far edge, so the two share one silhouette.
    spine = Image.new("RGBA", (spine_w, face_h + drop), (0, 0, 0, 0))
    for x in range(spine_w):
        top = round(drop * x / (spine_w + face_w))
        for y in range(top, top + face_h):
            # A gradient across the spine, darkest at the fold, which is what
            # a curved paper spine does to light.
            f = 0.55 + 0.25 * (x / max(1, spine_w - 1))
            spine.putpixel((x, y), (*darken(ink, f), 255))

    book = Image.new("RGBA", (width, face_h + drop), (0, 0, 0, 0))
    book.alpha_composite(spine, (0, 0))
    book.alpha_composite(face, (spine_w, 0))

    # THE PAGE EDGE: two columns of near-white just inside the right edge,
    # following the same slope as the cover beside them.
    for x in range(width - 3, width - 1):
        top = round(drop * x / width)
        for y in range(top, top + face_h):
            book.putpixel((x, y), (246, 243, 237, 255))

    # THE SHADOW, from the book's own alpha rather than a rectangle, so a
    # rounded or irregular silhouette would still cast the right shape.
    shadow = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    mask = book.split()[3].point(lambda a: SHADOW_ALPHA if a else 0)
    block = Image.new("RGBA", book.size, (20, 22, 26, 255))
    shadow.paste(block, (pad + 7, pad + 9), mask)
    shadow = shadow.filter(ImageFilter.GaussianBlur(SHADOW_BLUR))

    out = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    out.alpha_composite(shadow)
    out.alpha_composite(book, (pad, pad))
    out.save(OUT, "PNG", optimize=True)
    print(f"  wrote {OUT.name}  ({out.size[0]}x{out.size[1]}, "
          f"{OUT.stat().st_size:,} bytes)")
    return OUT


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--width", type=int, default=WIDTH,
                    help=f"output width in pixels (default {WIDTH}, which is "
                         f"2x the size the landing band draws it at)")
    build(ap.parse_args().width)


if __name__ == "__main__":
    main()

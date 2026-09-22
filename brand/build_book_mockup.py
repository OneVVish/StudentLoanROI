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
  2. The VERTICAL SHEAR, sloping UP from the spine to the outer edge. A
     column shear is the whole effect; the direction is what decides whether
     the object reads as standing (up) or lying on a desk (down).
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

from PIL import Image, ImageDraw, ImageFilter, ImageFont

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
# ream, because perspective foreshortens the spine and not the front. 0.072 is
# what that 9% looks like turned about fifteen degrees, and it was 0.055 until
# the spine had to carry the title: a strip too narrow to set type on is a
# strip the eye reads as an edge rather than as a spine.
SPINE_W = 0.072
SHADOW_BLUR = 9
SHADOW_ALPHA = 78

# THE SPINE CARRIES THE TITLE, and it will not be legible. At the ~200px the
# band draws this at, the spine is about 20 CSS pixels, so the type lands
# near 7px: it reads as "there is writing here", which is what makes a
# mockup look like a book and is all a mockup's spine ever does. The real
# spine (brand/build_book_cover.py) also carries the site and the author;
# three items at this size would be a smear rather than a suggestion.
SPINE_TEXT = "IS IT WORTH IT?"
SPINE_FONT = "InterDisplay-SemiBold.ttf"
FONTS = REPO / "brand" / "fonts"


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
    drop = round(face_h * TILT)          # how far the outer edge rides higher
    pad = SHADOW_BLUR * 3                # room for the blur to fall into
    canvas_w, canvas_h = width + pad * 2, face_h + drop + pad * 2

    # THE FRONT PANEL, ONE TRANSFORM AND NOT TWO. The first version ran a
    # QUAD perspective AND a column shear over its result, then cropped the
    # shear's source to face_h, which threw away the bottom `drop` rows: the
    # author's name and the URL came off the bottom of the cover. A column
    # shear alone is the whole effect, so that is all it does now.
    #
    # SLOPING UP, the author's call 2026-09-22: column x rises by
    # drop * x / face_w, so the spine edge sits LOW and the outer edge high.
    # The first version sloped the other way, which reads as a book lying
    # face up on a desk; this one reads as a book standing and turned toward
    # the viewer, which is what a shelf or a shop listing shows.
    flat = cover.convert("RGBA").resize((face_w, face_h), Image.LANCZOS)
    face = Image.new("RGBA", (face_w, face_h + drop), (0, 0, 0, 0))
    for x in range(face_w):
        face.paste(flat.crop((x, 0, x + 1, face_h)),
                   (x, drop - round(drop * x / face_w)))

    ink = edge_colour(cover)
    # THE SPINE IS BUILT FLAT AND THEN SHEARED, the way the face is. Drawing
    # it column by column at its final slope left nowhere to put the title:
    # text has to be composited onto a rectangle before the rectangle is bent.
    flat_spine = Image.new("RGBA", (spine_w, face_h), (0, 0, 0, 0))
    for x in range(spine_w):
        # A gradient across the spine, darkest at the fold, which is what a
        # curved paper spine does to light.
        f = 0.55 + 0.25 * (x / max(1, spine_w - 1))
        for y in range(face_h):
            flat_spine.putpixel((x, y), (*darken(ink, f), 255))

    # THE TITLE, drawn horizontally and turned, because Pillow cannot set
    # type down a column. Its ink is chosen from the spine's own mid tone
    # rather than fixed to white: this cover is near white, so its darkened
    # spine is a mid grey where white type would measure about 2:1.
    mid = darken(ink, 0.675)
    lum = (0.2126 * mid[0] + 0.7152 * mid[1] + 0.0722 * mid[2]) / 255
    text_ink = (255, 255, 255, 235) if lum < 0.5 else (26, 28, 31, 235)
    size = max(6, round(spine_w * 0.46))
    try:
        font = ImageFont.truetype(str(FONTS / SPINE_FONT), size)
    except OSError:
        sys.exit(f"  {SPINE_FONT} is missing from brand/fonts")
    strip = Image.new("RGBA", (face_h, spine_w), (0, 0, 0, 0))
    sd = ImageDraw.Draw(strip)
    bbox = sd.textbbox((0, 0), SPINE_TEXT, font=font)
    # Centred down the spine's length, and across its width off the TEXT's own
    # bbox rather than the font's line box, which carries ascender space this
    # narrow a strip cannot spare.
    sd.text(((face_h - (bbox[2] - bbox[0])) / 2 - bbox[0],
             (spine_w - (bbox[3] - bbox[1])) / 2 - bbox[1]),
            SPINE_TEXT, font=font, fill=text_ink)
    # TOP TO BOTTOM, the US shelf convention the printed spine already
    # follows: rotate(90) is counter-clockwise and would set it running up.
    flat_spine.alpha_composite(strip.rotate(-90, expand=True))

    spine = Image.new("RGBA", (spine_w, face_h + drop), (0, 0, 0, 0))
    for x in range(spine_w):
        top = drop - round(drop * x / (spine_w + face_w))
        spine.paste(flat_spine.crop((x, 0, x + 1, face_h)), (x, top))

    book = Image.new("RGBA", (width, face_h + drop), (0, 0, 0, 0))
    book.alpha_composite(spine, (0, 0))
    book.alpha_composite(face, (spine_w, 0))

    # THE PAGE EDGE: two columns of near-white just inside the right edge,
    # following the same slope as the cover beside them.
    for x in range(width - 3, width - 1):
        top = drop - round(drop * x / width)
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

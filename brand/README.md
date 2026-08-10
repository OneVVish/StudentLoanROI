# worthmydegree.com — logo

Everything here is generated. Edit `build_logo.py` and re-run it; do not hand-edit
the SVGs, because five files have to agree and nothing checks that they do.

```bash
python3 brand/build_logo.py       # rewrites every SVG + palette.json
python3 -m http.server 8777       # then open brand/preview.html to look at them
```

## The mark is the product's own chart

Net position over time starts at zero, falls while tuition and foregone earnings
accumulate, and crosses back above zero at break-even. That shape is a
checkmark. It reads as *"worth it"* at a glance and as the actual curve the
calculator draws when you look twice — the cost stroke in orange, the return
stroke in blue, and a dot on the zero line at the moment the degree has paid for
itself.

The crossing point is **solved, not typed**. Move any of `START` / `TROUGH` /
`END` and `CROSS_X` recomputes, because a dot sitting *near* the zero line
instead of *on* it is the one flaw this mark cannot survive.

## Which file to use

| File | Use it for |
|---|---|
| `logo-horizontal-{light,dark}.svg` | the default. Site header, email signature, slides |
| `logo-stacked-{light,dark}.svg` | square-ish spaces — social avatars, a card |
| `mark-{light,dark}.svg` | wherever the name is already on screen |
| `favicon-{light,dark}.svg` | browser tab, app icon, anything under ~24px |
| `logo-mono.svg` | one-colour reproduction: stamps, embroidery, a fax |

**The favicon is not the mark shrunk.** It drops the zero line and the crossing
dot and thickens the stroke, because below about 24px the dot is under a pixel
across and a sub-pixel dot does not read as smaller — it reads as a smudge. What
survives is the two-tone tick, which is the identity anyway. Verified legible at
16px.

## Colour

The app's own `SERIES_ORANGE` / `SERIES_BLUE` and their dark-surface steps, so
the brand and the product cannot drift apart. Both pairs pass the dataviz
validator's six checks on white and on `#0E1117` (CVD ΔE 24.7 light, 26.8 dark).

A logo does not strictly owe anyone colourblind separation. These two do, because
here they carry meaning — cost against return — and the app already has to pass,
so reusing the validated steps costs nothing.

Values are in `palette.json` alongside the solved geometry.

## Two things to know before this goes anywhere public

- **The wordmark is set in Avenir Next**, which is licensed, not open. The type is
  converted to outlines so the files reference no font and render identically
  everywhere — but converting outlines does not convert the licence. Check what
  yours permits for a commercial mark, or re-cut it in an open face (Inter,
  Source Sans, Work Sans); that is a one-line change to `FONT` and a rebuild.
- **`logo-mono.svg` uses `currentColor`, which `<img src="...">` does not
  inherit.** An SVG loaded as an image is an isolated document, so `currentColor`
  resolves to its own default black and the file appears black on any
  background. Inline it in the HTML, or use it as a CSS `mask-image`. The
  light/dark files have concrete colours and work as `<img>` anywhere.

## What is not here

No favicon `.ico` and no raster PNGs. Both are one command away
(`rsvg-convert`, `cairosvg` or a browser export) and neither is installed on
this machine, so shipping them would mean shipping something I could not open
and look at.

---

# Feature graphic

```bash
python3 brand/build_feature_graphic.py    # rewrites all four PNGs
```

| File | Size | For |
|---|---|---|
| `feature-og-1200x630.png` | 1200×630 | Facebook / LinkedIn feed, and the site's OG image |
| `feature-square-1080.png` | 1080×1080 | Instagram, group posts, anywhere square |
| `feature-story-1080x1920.png` | 1080×1920 | Instagram / Facebook story |
| `feature-slide-1920x1080.png` | 1920×1080 | a deck |

PNG rather than SVG, and that is the opposite choice from the logo on purpose:
a logo is master artwork that gets scaled forever, while this is consumed as a
raster at fixed sizes by platforms that will not accept an SVG at all. No SVG
rasteriser is installed here, so an SVG would be a file nobody could post *and*
nobody could open to check.

## Every number is read from the datasets

`facts()` counts the rows — 5,035 schools from `college_coa_clean.csv`, nine
programmes from `professional_tuition_clean.csv`. Nothing on the graphic is
typed. A marketing asset that drifts from the product is the same failure as a
chart twin drifting from its original, except it is the one artifact that
leaves the building.

Rebuild the pipelines and this graphic is one command behind, rather than
quietly wrong.

## Layout notes, all of them bought by rendering it and looking

- **The tile fits its content; it does not stretch.** Stretched, the story
  format gave each tile 410px of height for 250px of text and read as six
  mostly-empty boxes. Heights are measured — wrap the bodies, take the tallest
  — and the finished block is centred in what was available.
- **Type is sized from the tile's SHORTER edge.** Height alone let a tall
  canvas set 47px body text inside a 445px column; the wrap then needed more
  lines than the height it was derived from, and all six tiles overflowed.
- **The header has its own scale, capped at 30% of canvas height.** Sized off
  the canvas it was a 92px headline over 20px tile titles on the square.
  Solved jointly with the tiles it ate the short 1200×630 canvas and collapsed
  the tiles to slivers. Two scales, one cap.
- **An overflow check prints a warning at build time.** It has already earned
  its place twice.
- **The icons are drawn, not emoji.** matplotlib has no colour-emoji font, so
  emoji come out as tofu — and three of the first six glyphs were bar clusters,
  so half the grid looked like the same picture repeated. Each one now names its
  own feature, and the ROI tile carries the actual break-even curve.

## Changing the copy

Edit `features()`. Keep the bodies short — the first draft ran two sentences a
tile and at the size a 1200×630 tile actually is, the text left its own box and
crossed the row beneath it. Six tiles is the ceiling: past that the type drops
below what a phone resolves and the graphic becomes a picture of some text.

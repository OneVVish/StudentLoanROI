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

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

**The app itself consumes three of these at runtime** — `logo-horizontal-auto.svg`
(st.logo, all pages), `mark-light.svg` (the collapsed-sidebar icon) and
`favicon-32.png` (the browser tab). Renaming or deleting them breaks the
deployed app, not just this folder. The PDFs and the share card do NOT read
any file here: app.py draws the mark from geometry constants copied out of
`palette.json` — if `START`/`TROUGH`/`END` move in `build_logo.py`, move
`LOGO_*` in app.py §2k in the same commit.

`logo-horizontal-auto.svg` switches its wordmark ink with
`prefers-color-scheme` via an internal `<style>` block — an SVG loaded through
`<img>` still applies its own media queries, which is how one file serves
st.logo's both-themes requirement. The favicon PNGs are rasterised by
matplotlib from the same geometry, since no SVG rasteriser is installed here.



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

---

# Borrowing-limits poster

```bash
python3 brand/build_borrowing_graphic.py
```

| File | Size | For |
|---|---|---|
| `borrowing-1080x1350.png` | 1080×1350 | Instagram / Facebook portrait — the default |
| `borrowing-1080x1920.png` | 1080×1920 | story |
| `borrowing-letter.png` | 1275×1650 | a counselor handout, letter portrait at 150dpi |

Built after a reference graphic in the counselor-infographic idiom: condensed
caps headline, callout, a **table** as the centrepiece, footnote, three points,
closing line. The reference used navy and gold; this uses a deep step of the
app's own blue (`#12335c`, white on it at 12.7:1) with the app's orange as the
accent — a second palette would be a second brand, and the logo would stop
matching the poster inside a week.

## The table is computed, not transcribed

`borrowing_table()` execs app.py's section 1–2 prefix and calls
`federal_direct_cap` and `parent_plus_cap` on a real four-year schedule, one
year at a time, so each row is that year's marginal capacity and the total is
the app's own answer with aggregate ceilings applied.

It matters more here than on the feature grid: this graphic's whole claim is
that the numbers are right, and it gets screenshotted and forwarded without the
site attached.

## One thing the reference got wrong, and this does not

The reference labels its table *"Class of 2028 — under current federal law."*
The new Parent PLUS ceiling binds on loans **first disbursed on or after
July 1 2026** (`PARENT_PLUS_LIMIT_EFFECTIVE_YEAR`), so a 2028 graduate borrowed
their first two years under the old rule, where Parent PLUS was
cost-of-attendance-minus-aid with no practical ceiling. The table is true for a
student **starting** in 2026 or later, and that is what the callout says.

## Layout notes

- **Every string is `$`-escaped at the one place text reaches the canvas.**
  matplotlib reads paired dollar signs as mathtext, so the footnote came out as
  an italic run with both signs eaten. Same trap as `fmt_money_md` on the
  Streamlit side and `_pdf_escape_money` in the PDF — met a third time, hence
  escaping everything rather than only what looks like money today.
- **A taller canvas gets bigger type, not bigger gaps.** The sections have
  fixed heights, so on the story the slack pass had ~570px to spread over four
  gaps and the poster came apart into four islands. Type now scales with the
  aspect (capped at 1.35×) and per-gap slack is capped.
- **Table headers share one size**, the smallest any of them needs. Fitted
  independently they came out at different sizes in the same row.
- The build warns if content runs into the closing block.

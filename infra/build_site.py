#!/usr/bin/env python3
"""Build the whole static edge site -- landing page and guides -- and inject
it into the Worker.

    python3 infra/build_site.py          # rewrites infra/landing.html, the
                                         # guide pages, and the LANDING /
                                         # GUIDES constants in infra/worker.js

Renamed from build_landing.py when guides arrived: one script, one read of
content/posts, so the landing page's guide list and the guide pages
themselves cannot disagree about what exists.

WHY GENERATED. The page quotes numbers -- the school count, the 2026 federal
borrowing table, the Berkeley example -- and a marketing page that drifts from
the product is the one artifact that lies to strangers. Every figure here is
read from the app's own constants and datasets at build time, the same
discipline as brand/build_borrowing_graphic.py. If the datasets move, this
page is one command behind rather than quietly wrong.

WHY INJECTED INTO worker.js RATHER THAN IMPORTED. wrangler could import an
.html file as a text module, but the documented fallback deploy is pasting
worker.js into the Cloudflare dashboard (infra/SEO_DEPLOY.md), and an import
would silently break that path. So the page is a constant between markers,
exactly how ROBOTS/SITEMAP/LLMS already live, and infra/landing.html is the
reference copy -- same convention, same "change both halves in one PR" rule,
except here a script enforces the sync instead of a comment asking for it.

The logo is INLINED as SVG markup (read from brand/logo-horizontal-light.svg)
and the favicon rides as a data: URI -- the page makes zero requests beyond
itself, so it renders complete even if the origin is down. That is the point:
this page is served from the edge and survives anything.
"""
import base64
import datetime
import hashlib
import json
import re
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "infra" / "landing.html"
WORKER = ROOT / "infra" / "worker.js"

START_MARK = "// {{LANDING_HTML_START}}"
END_MARK = "// {{LANDING_HTML_END}}"

# The organizational status line in every page footer. Kept identical to
# app.py's ORG_STATUS_LINE, which puts the same sentence on every generated
# PDF; the two are separate constants because this file never imports app.py,
# so changing one means changing both in the same commit.
#
# THE WORDING IS THE CLAIM AND IT IS DELIBERATELY NARROW. Incorporation with
# the California Secretary of State is an entity, not a tax status. "Nonprofit"
# on its own is read by most people as "donations are deductible", which is
# 501(c)(3) and a separate IRS determination; soliciting donations in
# California would additionally need registration with the Attorney General's
# Registry of Charities and Fundraisers. So no "charity", no "tax-exempt", no
# EIN, until the filing that licenses each one exists.
ORG_STATUS_LINE = "Worth My Degree Inc. is a California nonprofit corporation."


# --- content ---------------------------------------------------------------
#
# A DELIBERATELY SMALL MARKDOWN SUBSET, rendered here rather than by a library.
# No markdown package is in requirements.txt, and the build scripts in this
# repo use production dependencies only (brand/build_*.py use matplotlib and
# pandas because the app already ships them). Adding a dev-only dependency
# would make the site un-buildable on a machine that lacks it -- a
# works-on-my-machine hazard for the one artifact strangers read.
#
# The subset is enforced, not merely documented: check_content.py fails on any
# construct this cannot render, so an unsupported line is a build error rather
# than a paragraph that silently comes out as literal asterisks.
SUPPORTED_MARKDOWN = """
  # ## ###        headings
  paragraphs      blank-line separated
  **bold**  *italic*  `code`
  [text](url)     links
  ![alt](file)    image, resolved against /app/static/
  - item          unordered list
  > quote         blockquote
  | a | b |       simple pipe table with a --- separator row
  ---             horizontal rule (on its own line, outside a table)
"""

# Shared by the landing page, every guide and the guides index -- one copy,
# because two copies is how the guides came to lack it. The landing had this
# from the start; the guide templates did not, so a counselor arriving on
# /guides/<slug>?src=ccounselors read the article tagged and then landed in the
# calculator as untagged organic traffic. The read was counted and the thing
# worth counting was not.
#
# It carries the WHOLE query string onto internal links, not a chosen subset:
# ?src= is attribution and ?test=1 keeps a developer's click-through out of
# production Supabase, and both fail silently rather than loudly when dropped.
#
# Every app-bound link already carries ?go=1 (the worker serves the landing on
# a parameter-less "/", so a bare href="/" would loop a clean visitor back
# instead of opening the calculator) and ?from=welcome or ?from=guide, which
# the app validates against NAV_ORIGINS and logs as `nav:from=X:to=Y`. Drop
# either and the link silently stops being counted --
# check_internal_links.py asserts app-bound hrefs still carry them.
CARRY_QS_JS = """<script>
(function () {
  var qs = location.search.replace(/^\\?/, "");
  if (!qs) return;
  document.querySelectorAll('a[href^="/"]').forEach(function (a) {
    var href = a.getAttribute("href");
    if (href.indexOf("llms.txt") !== -1) return;
    // The query goes BEFORE the fragment. This used to append blindly, which
    // turned /charts#some-slug into /charts#some-slug?v=1: the anchor then
    // matches no element, so the link stops jumping to the chart it names, AND
    // the carried value ends up inside the fragment where nothing reads it --
    // so ?src= is silently lost on exactly the links this script exists to
    // protect. Harmless until the first fragment link, which the landing's
    // infographic cards are.
    var hash = "";
    var cut = href.indexOf("#");
    if (cut !== -1) { hash = href.slice(cut); href = href.slice(0, cut); }
    a.setAttribute("href",
      href + (href.indexOf("?") === -1 ? "?" : "&") + qs + hash);
  });
})();
</script>"""

SITE_CSS = """  :root {
    --deep: #12335c; --blue: #2a78d6; --orange: #eb6834;
    --ink: #14161a; --muted: #5c636d; --rule: #dfe3e8;
    --tint: #fdf2ec; --tile: #f7f8fa; --surface: #ffffff;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font: 17px/1.6 "Avenir Next", -apple-system, "Segoe UI", Roboto, sans-serif;
    color: var(--ink); background: var(--surface);
  }
  .wrap { max-width: 980px; margin: 0 auto; padding: 0 24px; }
  header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 18px 0; border-bottom: 1px solid var(--rule);
  }
  .logo svg { height: 36px; width: auto; display: block; }
  .btn {
    display: inline-block; background: var(--orange); color: #fff;
    font-weight: 700; text-decoration: none; border-radius: 10px;
    padding: 12px 22px; font-size: 16px;
  }
  .btn.big { padding: 16px 34px; font-size: 19px; }
  .btn.ghost { background: transparent; color: var(--deep);
    border: 2px solid var(--deep); }
  .hero { text-align: center; padding: 64px 0 40px; }
  .hero h1 {
    font-size: clamp(34px, 6vw, 58px); line-height: 1.08; color: var(--deep);
    font-weight: 800; letter-spacing: -0.01em; text-transform: uppercase;
  }
  .hero .accent {
    width: 130px; height: 3px; background: var(--orange);
    margin: 22px auto; position: relative;
  }
  .hero p.sub {
    font-size: 20px; color: var(--muted); max-width: 620px; margin: 0 auto 30px;
  }
  .trust { margin-top: 14px; color: var(--muted); font-size: 15px; }
  .stats {
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px;
    padding: 26px 0 8px;
  }
  .stat { background: var(--tile); border-radius: 12px; padding: 18px 10px;
    text-align: center; }
  .stat b { display: block; font-size: 26px; color: var(--deep); }
  .stat span { font-size: 14px; color: var(--muted); }
  section { padding: 44px 0 8px; }
  h2 { font-size: 28px; color: var(--deep); margin-bottom: 6px; }
  .deck { color: var(--muted); margin-bottom: 24px; max-width: 640px; }
  .grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
  .tile { background: var(--tile); border-radius: 12px; padding: 20px; }
  .tile b { display: block; font-size: 17px; margin-bottom: 6px; }
  .tile p { font-size: 15px; color: var(--muted); }
  table { width: 100%; border-collapse: collapse; font-size: 16px;
    margin-top: 18px; }
  thead th { background: var(--deep); color: #fff; font-weight: 600;
    padding: 10px 8px; }
  tbody td, tfoot td { text-align: center; padding: 10px 8px;
    border-bottom: 1px solid var(--rule); }
  tbody tr:nth-child(even) { background: #f6f8fa; }
  tfoot td { background: var(--tint); color: var(--deep); font-weight: 700; }
  .note { font-size: 14px; color: var(--muted); margin-top: 12px;
    max-width: 640px; }
  .callout {
    background: var(--tint); border: 2px solid var(--orange);
    border-radius: 12px; padding: 18px 22px; margin-top: 20px; font-size: 16px;
  }
  .tools .tile a { color: var(--blue); font-weight: 600;
    text-decoration: none; }
  /* TWO columns for the tool cards, not the three the feature grid uses.
     There are four tools, and four cards in a 3-up grid leave a lone card
     stranded on row two -- the same reason the guides index is 2-up. This
     needs the extra specificity of `.tools .grid`, so the 720px breakpoint
     below has to restate it or the cards stay 2-up on a phone. */
  .tools .grid { grid-template-columns: repeat(2, 1fr); }
  /* The pathways band has four cards too, and the same stranded-card problem
     the comment above describes. Same fix, same specificity requirement, so
     the 720px breakpoint restates this one as well. */
  .paths .grid { grid-template-columns: repeat(2, 1fr); }
  /* The landing's infographic cards. Two up, matching .guides, and a picture
     on top because a chart IS the picture. Deliberately NOT .chart-card: that
     one carries the gallery's Helpful and Share buttons and its own reaction
     JS, and neither belongs on a doorway card. */
  .infos { display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; }
  .infos .info-card { display: block; text-decoration: none; overflow: hidden;
    background: var(--tile); border-radius: 12px; }
  /* Square, and deliberately NOT object-fit: cover -- _chart_jpeg's pad branch
     has already padded the file to this ratio, so the whole infographic is on
     screen. A cover rule here would crop the padded file a second time. */
  .infos .info-card img { display: block; width: 100%; height: auto;
    aspect-ratio: 1 / 1; background: var(--surface); }
  .infos .info-card b { display: block; padding: 16px 18px 0; font-size: 17px;
    color: var(--deep); }
  .infos .info-card span { display: block; padding: 8px 18px 18px;
    color: var(--muted); font-size: 15px; }
  /* ===== The tools band =====
     The tools are the product, so they sit above the guides and carry a
     background of their own: edge to edge, the section reads as its own zone
     instead of as one more stretch of the same white page.
     The colour is --tile, the neutral grey the stat tiles and feature cards
     already use. NOT the brand orange: that is this page's single accent, and
     four cards painted in it would be a far louder change than a zone marker
     needs to be. No new token and no new hue, so nothing here has to be
     re-decided if the palette moves.
     The full bleed comes from the MARKUP -- the section is a sibling of the
     page's .wrap containers with its own .wrap inside -- and deliberately not
     from `margin-left: calc(50% - 50vw)`. 100vw counts the scrollbar, so the
     vw trick adds a horizontal scrollbar of its own at exactly the widths
     where the page is otherwise fine.
     The cards flip to white with a rule because `.tile`'s own background IS
     --tile: left alone they would dissolve into the band they sit on. Their
     fill and their accent are set with the guide cards, in the one-card-two-
     colours rule below, so the two kinds cannot drift apart in shape. */
  .tools { background: var(--tile); margin-top: 40px; padding: 48px 0; }
  .cta { text-align: center; padding: 56px 0; }
  footer {
    border-top: 1px solid var(--rule); margin-top: 40px; padding: 26px 0 40px;
    color: var(--muted); font-size: 14px;
  }
  /* Guide cards render on BOTH the landing page and the guides index, so these
     live in SITE_CSS rather than ARTICLE_CSS. They were in ARTICLE_CSS, which
     only the guide pages include -- so the landing page emitted the markup with
     no rules at all and the card collapsed into one run-on underlined link:
     title, summary and date with nothing separating them. The display:block on
     b/time is what does the separating, so a card without this CSS is not
     merely unstyled, it is unreadable. */
  /* ===== The guides index =====
     Structure follows a conventional blog index: a titled band, then a card
     grid, each card a photograph with a date and a reading time under it. It
     is picture-led as of 2026-08-14, because every guide now has a hero --
     see card_image_for and build_guides_index_html, where that is ALL OR
     NOTHING and always has been.
     TWO columns, not three. The pictures are the point once they are there,
     and a third column bought density at the cost of drawing every photograph
     at ~290px, where a wide scene reads as a texture rather than an image. At
     two the card is ~457px, which is inside the 720px thumbnail's resolution
     and close to the single-column list the reference site uses. Four guides
     also fill a 2-up grid exactly, where 3-up leaves a lone card on row two.
     NO TINT on these either -- see the article hero note in ARTICLE_CSS. The
     text sits below the frame, so nothing needs to be legible on top of a
     photograph and nothing is painted over one. */
  .guides-band { background: var(--deep); color: #fff; border-radius: 16px;
    padding: 44px 40px 40px; margin: 26px 0 34px; }
  .guides-band h1 { font-size: clamp(30px, 5vw, 46px); line-height: 1.1;
    font-weight: 800; letter-spacing: -0.01em; }
  .guides-band .accent { width: 96px; height: 4px; background: var(--orange);
    border-radius: 2px; margin: 18px 0; }
  .guides-band p { color: rgba(255, 255, 255, 0.86); max-width: 60ch;
    font-size: 17px; }
  .post-head { display: flex; align-items: baseline; justify-content: space-between;
    gap: 16px; margin-bottom: 16px; }
  .post-head h2 { font-size: 24px; color: var(--deep); }
  .post-head span { color: var(--muted); font-size: 15px; }
  .post-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 22px; }
  .post-card { display: flex; flex-direction: column; background: var(--surface);
    border: 1px solid var(--rule); border-radius: 14px; overflow: hidden;
    text-decoration: none; color: inherit;
    transition: border-color .15s ease, transform .15s ease; }
  .post-card:hover { border-color: var(--blue); transform: translateY(-2px); }
  .post-card:focus-visible { outline: 3px solid var(--blue); outline-offset: 2px; }
  .post-card figure { margin: 0; aspect-ratio: 16 / 9; background: var(--tile); }
  .post-card figure img { width: 100%; height: 100%; object-fit: cover;
    display: block; }
  .post-card .meta { padding: 16px 18px 0; color: var(--muted); font-size: 13px;
    letter-spacing: 0.02em; }
  .post-card b { display: block; padding: 8px 18px 0; color: var(--deep);
    font-size: 19px; line-height: 1.28; }
  .post-card span.sum { display: block; padding: 10px 18px 0; color: var(--muted);
    font-size: 15px; }
  /* margin-top:auto pins the rule to the bottom whatever the title wraps to,
     so a row of cards lines up along it rather than along the text. */
  .post-card .more { margin-top: auto; padding: 16px 18px; color: var(--blue);
    font-weight: 700; font-size: 15px; }
  @media (max-width: 640px) { .post-grid { grid-template-columns: 1fr; }
    .guides-band { padding: 32px 24px 28px; } }
  @media (prefers-reduced-motion: reduce) {
    .post-card { transition: none; }
    .post-card:hover { transform: none; }
  }
  /* ===== THE CHART GALLERY =====
     Borrows .post-grid for layout and .post-card's frame, then diverges in one
     structural way: a guide card IS a link, and a chart card CANNOT be, because
     it carries its own Helpful and Share buttons. An <a> wrapping a <button> is
     invalid and, worse, swallows the button's click on some browsers. So the
     picture is the link and the reactions sit outside it as siblings.

     These rules live in SITE_CSS rather than ARTICLE_CSS deliberately. The
     .guide-card rules once sat in ARTICLE_CSS while the LANDING rendered the
     markup, so the homepage served cards with no rules at all and the title,
     summary and date collapsed into one run-on underlined link. Anything a
     second page might ever render belongs here. */
  .chart-card { display: flex; flex-direction: column; background: var(--surface);
    border: 1px solid var(--rule); border-radius: 14px; overflow: hidden; }
  .chart-card .shot { display: block; aspect-ratio: 16 / 9; background: var(--tile);
    text-decoration: none; }
  .chart-card .shot img { width: 100%; height: 100%; object-fit: cover;
    object-position: top; display: block; }
  .chart-card b { display: block; padding: 16px 18px 0; color: var(--deep);
    font-size: 19px; line-height: 1.28; }
  .chart-card span.sum { display: block; padding: 10px 18px 0; color: var(--muted);
    font-size: 15px; }
  .chart-card .src { padding: 10px 18px 0; margin: 0; color: var(--muted);
    font-size: 12.5px; }
  /* margin-top:auto pins the reaction bar to the bottom of the card whatever
     the summary wraps to, so a row of cards lines its buttons up. */
  .chart-card .reactions { margin-top: auto; display: flex; align-items: center;
    gap: 10px; flex-wrap: wrap; padding: 16px 18px; }
  .chart-card .reactions button { font: inherit; font-size: 14px; cursor: pointer;
    background: var(--surface); color: var(--deep); border: 1px solid var(--rule);
    border-radius: 999px; padding: 7px 14px; }
  .chart-card .reactions button:hover:not(:disabled) { border-color: var(--blue);
    color: var(--blue); }
  .chart-card .reactions button:disabled { cursor: default; color: var(--muted); }
  .chart-card .reactions .count { color: var(--muted); font-size: 13px; }
  .chart-card .reactions .sharelink { font-size: 12.5px; color: var(--muted);
    word-break: break-all; }
  .guides { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }
  /* ===== ONE CARD, TWO COLOURS =====
     The landing page holds two kinds of card: a tool is something you use, a
     guide is something you read. Before this they were two neutral greys
     pointed at each other -- white cards on the --tile band above, --tile
     cards on white here -- which reads as one list interrupted by a band
     rather than as two categories.
     They are now ONE shape written once, and colour is the only difference:
     tools cool (blue rule on white), guides warm (orange rule on --tint, the
     cream the callout and the table footer already use). Both accents are
     existing tokens; no new hue enters the palette. Writing the shape twice is
     how the two would drift into looking like unrelated components, which is
     the opposite of what a reader is meant to take from them: same kind of
     object, different category.
     Blue against orange is also the pairing that survives the common forms of
     colour blindness, which matters because the accent is the part carrying
     the distinction at a glance.
     COLOUR IS NOT THE ONLY SIGNAL, deliberately. The fills are nearly
     identical in greyscale (--tint against --surface is 1.1:1), so position
     and the section heading do the real work, and the accent rule is
     reinforcement rather than the sole carrier: --blue on the white tool card
     is 4.42:1, --orange between the cream fill (2.91:1) and the white page
     (3.20:1). Text is untouched and clears AA on both fills -- --deep 11.54:1
     and --muted 5.52:1 on --tint, --ink 18.11:1 and --muted 6.07:1 on white.
     `.guide-card` is the LANDING page's card. The guides index draws
     `.post-card` -- different rule, different page; editing the wrong one
     restyles a page nobody asked about. */
  .tools .tile, .guide-card { border-radius: 12px; padding: 20px;
    border: 1px solid var(--rule); border-left-width: 3px; }
  .tools .tile { background: var(--surface); border-left-color: var(--blue); }
  .guide-card { background: var(--tint); border-left-color: var(--orange);
    display: block; text-decoration: none; color: inherit; }
  .guide-card b { display: block; color: var(--deep); font-size: 18px;
    margin-bottom: 6px; }
  .guide-card span { color: var(--muted); font-size: 15px; }
  .guide-card time { display: block; color: var(--muted); font-size: 13px;
    margin-top: 10px; }
  @media (max-width: 720px) {
    .stats { grid-template-columns: repeat(2, 1fr); }
    .grid { grid-template-columns: 1fr; }
    .tools .grid { grid-template-columns: 1fr; }
    .paths .grid { grid-template-columns: 1fr; }
    .infos { grid-template-columns: 1fr; }
    .guides { grid-template-columns: 1fr; }
    .hide-m { display: none; }
    .table-scroll { overflow-x: auto; }
    /* ===== THE GALLERY IS A FEED ON A PHONE =====
       Twenty-five dense pictures in a one-column grid is a long scroll of
       cropped thumbnails. On a phone each card becomes one screen and the
       page snaps card to card, the shape people already know from short
       video: the whole picture uncropped (the <picture> source swaps the
       16:9 crop for the square-padded landing image), the title, a clamped
       summary, and the Helpful and Share bar pinned at the bottom of the
       screen. A tap on the picture still opens it full size. Pure CSS: no
       script, no new page, no new logging name, and the desktop grid is
       untouched. The intro block is a snap point too, or a mandatory snap
       would jump straight past it on load. */
    html:has(.post-grid .chart-card) { scroll-snap-type: y mandatory; }
    .guides-band, .post-head { scroll-snap-align: start; }
    .post-grid:has(.chart-card) { gap: 0; }
    .chart-card { scroll-snap-align: start; scroll-snap-stop: always;
      min-height: 100vh; min-height: 100dvh; box-sizing: border-box;
      border-radius: 0; border-left: 0; border-right: 0; }
    .chart-card .shot { aspect-ratio: auto; height: 62vh; height: 62dvh;
      display: flex; align-items: center; justify-content: center; }
    .chart-card .shot img { object-fit: contain; object-position: center;
      max-height: 100%; }
    .chart-card span.sum { display: -webkit-box; -webkit-line-clamp: 3;
      -webkit-box-orient: vertical; overflow: hidden; }
    .chart-card .src { display: -webkit-box; -webkit-line-clamp: 2;
      -webkit-box-orient: vertical; overflow: hidden; }
    /* A card with a phone render IS the picture: the headline, the caveat
       and the source are in the frame, so the card's own text is hidden and
       the picture fills the screen with the two icons floating at the foot. */
    .chart-card.tall { position: relative; background: #0b0b0b; }
    .chart-card.tall .shot { height: 100vh; height: 100dvh; background: #0b0b0b; }
    .chart-card.tall b, .chart-card.tall span.sum, .chart-card.tall .src,
    .chart-card.tall .reactions .count { display: none; }
    .chart-card.tall .reactions { position: absolute; left: 0; right: 0; bottom: 0;
      margin: 0; justify-content: space-between; pointer-events: none;
      padding: 14px 16px; padding-bottom: calc(14px + env(safe-area-inset-bottom)); }
    /* font-size 0 hides the button's own words; the glyph rides ::before as
       the literal character, because inside this Python literal a CSS escape
       like \\2665 is read as an OCTAL escape and comes out as garbage.
       !important because the shared disc rule below the media block sets
       22px at the same specificity and would win on order. */
    .chart-card.tall .reactions button { font-size: 0 !important;
      pointer-events: auto; line-height: 1; cursor: pointer; border: 0;
      border-radius: 999px; width: 48px; height: 48px; padding: 0;
      background: rgba(255,255,255,.92); color: #0b0b0b;
      box-shadow: 0 2px 10px rgba(0,0,0,.35); }
    .chart-card.tall .reactions .like { color: #e0245e; }
    .chart-card.tall .reactions .like.on { background: #e0245e; color: #fff; }
    .chart-card.tall .reactions .like::before { content: "♥"; font-size: 22px; }
    .chart-card.tall .reactions .share::before { content: "🔗"; font-size: 22px; }
    .chart-card.tall .reactions .sharelink { pointer-events: none; order: 1;
      align-self: center; color: #fff; background: rgba(0,0,0,.6);
      border-radius: 999px; padding: 6px 10px; word-break: normal; }
  }
  /* The on-page picture viewer the feed opens on a phone. Twice the screen's
     width by default so a chart's text is legible, scrollable both ways,
     pinchable because the viewport allows scaling; .fit shows it whole. */
  .viewer { position: fixed; inset: 0; z-index: 60; background: #0b0b0b;
    overflow: auto; -webkit-overflow-scrolling: touch; }
  @media (prefers-color-scheme: light) {
    .viewer { background: #ffffff; }
    .viewer .close, .viewer .vbar button {
      background: #111210; color: #f2f2f0; box-shadow: 0 2px 10px rgba(0,0,0,.25); }
    .viewer .vlike { color: #e0245e; }
  }
  @media (max-width: 720px) and (prefers-color-scheme: light) {
    .chart-card.tall, .chart-card.tall .shot { background: #ffffff; }
    .chart-card.tall .reactions button {
      background: #111210; color: #f2f2f0; box-shadow: 0 2px 10px rgba(0,0,0,.25); }
    .chart-card.tall .reactions .like { color: #e0245e; }
  }
  .viewer img { display: block; width: 200vw; max-width: none; }
  .viewer.fit img { width: 100vw; }
  /* Helpful lower left, Share lower right, floating over the picture. The
     bar itself passes touches through so the picture stays scrollable. */
  .viewer .vbar { position: fixed; left: 0; right: 0; bottom: 0; z-index: 61;
    display: flex; justify-content: space-between; pointer-events: none;
    padding: 14px 16px; padding-bottom: calc(14px + env(safe-area-inset-bottom)); }
  /* Icon only: a red heart and the chain, in white discs. The viewer only
     opens on a phone, so these can be global; the TALL CARD'S bar gets the
     same discs inside the phone media query below and nowhere else. The
     first cut applied them at every width and squeezed "Helpful" into a
     48px disc on the desktop grid. */
  .viewer .vbar button { pointer-events: auto;
    font: inherit; font-size: 22px; line-height: 1; cursor: pointer; border: 0;
    border-radius: 999px; width: 48px; height: 48px; padding: 0;
    background: rgba(255,255,255,.92); color: #0b0b0b;
    box-shadow: 0 2px 10px rgba(0,0,0,.35); }
  .viewer .vlike { color: #e0245e; }
  .viewer .vlike.on { background: #e0245e; color: #fff; }
  .viewer .vbar button:disabled { opacity: 1; }
  .viewer .close { position: fixed; top: 12px; right: 12px; z-index: 61;
    width: 40px; height: 40px; border-radius: 999px; border: 0;
    background: rgba(255,255,255,.92); color: #0b0b0b; font-size: 18px;
    cursor: pointer; }
  [hidden] { display: none !important; }"""


POST_DIR = ROOT / "content" / "posts"
REQUIRED_FRONT_MATTER = ("title", "description", "summary", "date")


def parse_post(path: Path) -> dict:
    """Front matter + body from one .md file.

    Front matter is `key: value` lines between --- fences. Not YAML: a real
    YAML parser is another dependency, and the four fields here are strings.
    """
    raw = path.read_text()
    if not raw.startswith("---\n"):
        raise ValueError(f"{path.name}: missing --- front matter fence")
    _, fm, body = raw.split("---\n", 2)
    meta = {}
    for line in fm.strip().splitlines():
        if ":" not in line:
            raise ValueError(f"{path.name}: front-matter line without a colon: {line!r}")
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip()
    missing = [k for k in REQUIRED_FRONT_MATTER if not meta.get(k)]
    if missing:
        raise ValueError(f"{path.name}: front matter missing {', '.join(missing)}")
    meta["slug"] = path.stem
    meta["body"] = body.strip()
    return meta


def _attr(value) -> str:
    """A front-matter value on its way into an attribute or a text node.

    The four characters that end an attribute or open a tag. The apostrophe is
    deliberately left alone: every attribute this generator writes is
    double-quoted, and escaping it would rewrite every title with one in it
    for no gain. A title, description or summary is ours, but it reaches
    <title>, content=, alt=, aria-label= and data-title=, and a renderer that
    trusts its input is one copy-paste away from being a hole.
    """
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# What a Markdown link or image may point at. Anything else -- javascript:,
# data:, vbscript:, a bare word -- is left as the literal text it was typed as,
# which is visible on the page and therefore gets fixed, where a rendered
# javascript: link is invisible until clicked.
SAFE_LINK_TARGET = re.compile(r"^(https?://|/|#|mailto:)", re.I)


def _inline(text: str) -> str:
    """Inline markdown -> HTML. Escapes first, so post text can never inject
    markup -- these files are ours, but a renderer that trusts its input is one
    copy-paste away from being a hole. The escape covers the double quote as
    well as & < >, because two of the rules below put captured text inside a
    double-quoted attribute; and a link target outside SAFE_LINK_TARGET is
    rendered as text rather than as a link."""
    out = _attr(text)

    def image(m):
        alt, src = m.group(1), m.group(2)
        # Image sources are static/ filenames, so a scheme is never right.
        if ":" in src:
            return m.group(0)
        return f'<img src="/app/static/{src}" alt="{alt}" loading="lazy">'

    def link(m):
        label, href = m.group(1), m.group(2)
        if not SAFE_LINK_TARGET.match(href):
            return m.group(0)
        return f'<a href="{href}">{label}</a>'

    out = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", image, out)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", out)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    return out


def js_string(value: str) -> str:
    """A Python string as a JS string literal, safe inside a <script> block.

    json.dumps does the escaping, for the reason article_jsonld already gives:
    a title carrying a quote or an apostrophe would otherwise break out of the
    literal, and broken JS fails silently in exactly the way this site cannot
    afford. It handles one thing JSON escaping does not -- "</" closes the
    script element wherever it appears, string or not.
    """
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def render_markdown(body: str) -> str:
    """The supported subset, block by block. Anything unrecognised raises --
    silence is the failure mode this exists to prevent."""
    html, lines, i = [], body.splitlines(), 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line:
            i += 1
        elif line.startswith("|"):
            rows, i = [], i
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            head, body_rows = rows[0], [r for r in rows[1:]
                                        if not all(set(c) <= set("-: ") for c in r)]
            html.append("<div class='table-scroll'><table><thead><tr>"
                        + "".join(f"<th>{_inline(c)}</th>" for c in head)
                        + "</tr></thead><tbody>"
                        + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r)
                                  + "</tr>" for r in body_rows)
                        + "</tbody></table></div>")
        elif line.startswith("### "):
            html.append(f"<h3>{_inline(line[4:])}</h3>"); i += 1
        elif line.startswith("## "):
            html.append(f"<h2>{_inline(line[3:])}</h2>"); i += 1
        elif line.startswith("# "):
            html.append(f"<h1>{_inline(line[2:])}</h1>"); i += 1
        elif line.startswith(">"):
            # ">" WITHOUT A SPACE IS THE PARAGRAPH SEPARATOR inside a quote, and
            # matching only "> " hung the build outright: a bare ">" matched no
            # branch, fell to the paragraph `else`, and that loop rejects a line
            # starting with ">" before it ever increments i. No output, no error,
            # no exit -- just a build that never returns. Found 2026-08-15 by
            # adding an ordinary two-paragraph footnote to the repayment guide.
            quote, i = [], i
            while i < len(lines) and lines[i].startswith(">"):
                quote.append(lines[i][1:].strip()); i += 1
            paras, cur = [], []
            for q in quote:
                if q:
                    cur.append(q)
                elif cur:
                    paras.append(" ".join(cur)); cur = []
            if cur:
                paras.append(" ".join(cur))
            html.append("<blockquote>"
                        + "".join(f"<p>{_inline(p)}</p>" for p in paras)
                        + "</blockquote>")
        elif line.startswith("- "):
            items, i = [], i
            while i < len(lines) and lines[i].startswith("- "):
                item = lines[i][2:]
                i += 1
                # A WRAPPED bullet. Without this the loop stopped at the
                # continuation line, closed the <ul>, rendered the remainder as
                # its own paragraph, and opened a SECOND <ul> for the next
                # bullet -- one list became two with a stray sentence between
                # them. It fails silently: valid HTML, plausible-looking page,
                # and only obvious beside the source. Found in preview on
                # 2026-08-12 in the parent guide's Result list.
                while (i < len(lines) and lines[i].strip()
                       and lines[i][:1] in " \t"
                       and not lines[i].lstrip().startswith("- ")):
                    item += " " + lines[i].strip()
                    i += 1
                items.append(f"<li>{_inline(item)}</li>")
            html.append("<ul>" + "".join(items) + "</ul>")
        elif line.strip() == "---":
            html.append("<hr>"); i += 1
        else:
            para, i = [], i
            while i < len(lines) and lines[i].strip() and not lines[i][0] in "#>-|":
                para.append(lines[i].strip()); i += 1
            if not para:
                # PROGRESS GUARANTEE. This branch is the fallthrough, so a line
                # matching no branch above AND rejected by the loop condition
                # consumes nothing and spins forever -- which is what the bare
                # ">" did. Emit it and advance. A slightly wrong render is
                # recoverable and visible; a hung build is neither.
                para.append(line); i += 1
            html.append(f"<p>{_inline(' '.join(para))}</p>")
    return "\n".join(html)


def load_posts() -> list:
    """Every post, newest first. Sorted on the date field, which the guard
    requires and checks is ISO -- a post with a malformed date would otherwise
    sort silently into the wrong place."""
    if not POST_DIR.exists():
        return []
    return sorted((parse_post(p) for p in POST_DIR.glob("*.md")),
                  key=lambda m: m["date"], reverse=True)


def app_facts() -> dict:
    """Counts and caps from the app's own code and datasets, never typed."""
    import pandas as pd
    src = (ROOT / "app.py").read_text()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    ns = {"__name__": "landingbuild"}
    exec(compile(src[:src.rindex("# " + "=" * 60, 0, cut)], "app.py", "exec"), ns)

    schedule = [{"year": y, "financed": True} for y in range(1, 5)]
    start = ns["PARENT_PLUS_LIMIT_EFFECTIVE_YEAR"]
    rows, d_run, p_run = [], 0.0, 0.0
    for i, label in enumerate(("Freshman", "Sophomore", "Junior", "Senior"), 1):
        upto = schedule[:i]
        d = ns["federal_direct_cap"](upto, "dependent")
        p = ns["parent_plus_cap"](upto, "dependent", start_year=start)
        rows.append((label, d - d_run, p - p_run, (d - d_run) + (p - p_run)))
        d_run, p_run = d, p

    coa = pd.read_csv(ROOT / "data/college_coa_clean.csv")
    careers = pd.read_csv(ROOT / "cleaned_careers.csv")
    berkeley = coa[coa["INSTNM"] == "University of California-Berkeley"].iloc[0]
    return {
        "schools": len(coa),
        "careers": len(careers) + len(ns["CURATED_MAJOR_DATA"]),
        "majors": len(ns["MAJOR_TO_CIP_FAMILY"]),
        "cities": len(ns["CITY_DATA"]),
        "plus_annual": ns["PARENT_PLUS_ANNUAL_LIMIT"],
        "plus_aggregate": ns["PARENT_PLUS_AGGREGATE_LIMIT"],
        "effective_year": start,
        "cap_rows": rows,
        "cap_total": (d_run, p_run, d_run + p_run),
        "berkeley_coa": float(berkeley["in_state_coa"]),
    }


def money(v):
    return f"${v:,.0f}"


def build_html(f: dict, posts: list = (), charts: list = ()) -> str:
    logo_svg = (ROOT / "brand/logo-horizontal-light.svg").read_text()
    # The lockup ships its own width/height; the page sizes it with CSS.
    logo_svg = re.sub(r'width="\d+" height="\d+"', "", logo_svg, count=1)
    favicon = base64.b64encode(
        (ROOT / "brand/favicon-light.svg").read_bytes()).decode()

    # The guide list, or nothing at all when there are no posts -- an empty
    # "Guides" heading advertises a section that does not exist.
    guides_section = ""
    if posts:
        cards = "\n".join(
            f'''    <a class="guide-card" href="/guides/{p["slug"]}">
      <b>{_attr(p["title"])}</b><span>{_attr(p["summary"])}</span>
      <time datetime="{p["date"]}">{p["date"]}</time></a>''' for p in posts[:4])
        guides_section = f'''<section>
  <h2>Guides</h2>
  <div class="guides">
{cards}
  </div>
  <p class="deck" style="margin-top:14px"><a href="/guides"
    style="color:var(--blue);font-weight:600;text-decoration:none">All
    guides&nbsp;→</a></p>
  <p class="deck" style="margin-top:6px">Still deciding what you want to be?
  Start with the <a href="https://onetinterestprofiler.org/"
    style="color:var(--blue)">O*NET Interest Profiler</a>, the federal
  government's free interest assessment. It tells you which careers fit you.
  This site tells you what each one costs to reach, and when it pays off.</p>
</section>'''

    # Two infographics, with their pictures, below the guides band. The guides
    # band is text because a post has no picture on the landing; a chart IS a
    # picture, so a text card would be advertising the one thing it cannot show.
    #
    # TWO, not the twelve on /charts: this is a doorway, not the gallery. The
    # cards link to /charts#slug, which opens the Infographics page scrolled to
    # that chart -- the same fragment addressing llms.txt already uses, and the
    # only way to point at one picture on a one-page gallery.
    #
    # Ordered by charts_by_rank: most liked when a ranking has been committed,
    # newest first otherwise. Only DRAWABLE charts are eligible, matching the
    # /charts page, since a card whose thumbnail is missing is a broken card
    # rather than a card without a picture.
    charts_section = ""
    eligible = [c for c in charts_by_rank(list(charts)) if c.get("drawable")]
    if eligible:
        chart_cards = "\n".join(
            f'''    <a class="info-card" href="/charts#{c["slug"]}">
      <img src="/app/static/{c["land_url"]}" alt="{_attr(c["description"])}"
           loading="lazy" width="640" height="640">
      <b>{_attr(c["title"])}</b><span>{_attr(c["summary"])}</span></a>'''
            for c in eligible[:2])
        charts_section = f'''<section>
  <h2>Infographics</h2>
  <p class="deck">One picture, one finding, sourced. {len(charts)} of them, free
  to share.</p>
  <div class="infos">
{chart_cards}
  </div>
  <p class="deck" style="margin-top:14px"><a href="/charts"
    style="color:var(--blue);font-weight:600;text-decoration:none">All
    infographics&nbsp;→</a></p>
</section>'''

    cap_body = "\n".join(
        f"        <tr><td>{label}</td><td>{money(d)}</td>"
        f"<td>{money(p)}{'*' if label == 'Senior' else ''}</td>"
        f"<td>{money(c)}</td></tr>"
        for label, d, p, c in f["cap_rows"])
    d_tot, p_tot, c_tot = f["cap_total"]

    leftover = f["plus_aggregate"] - f["plus_annual"] * 3

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Is the degree worth the loan? — worthmydegree.com</title>
<meta name="description" content="Free, anonymous calculator: pick a major, school, and loan; see the 10-year outcome under the 2026 federal repayment rules. {f['schools']:,} real schools, no sign-up.">
<link rel="canonical" href="https://worthmydegree.com/">
<meta property="og:type" content="website">
<meta property="og:title" content="Is the degree worth the loan? — worthmydegree.com">
<meta property="og:description" content="Free, anonymous calculator: pick a major, school, and loan; see the 10-year outcome under the 2026 federal repayment rules. {f['schools']:,} real schools, no sign-up.">
<meta property="og:url" content="https://worthmydegree.com/">
<meta property="og:image" content="https://worthmydegree.com/app/static/feature-og-calculator-1200x630.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="https://worthmydegree.com/app/static/feature-og-calculator-1200x630.png">
<link rel="icon" href="data:image/svg+xml;base64,{favicon}">
<script type="application/ld+json">{{
  "@context": "https://schema.org",
  "@graph": [
    {{"@type": "Organization", "@id": "https://worthmydegree.com/#org",
      "name": "WorthMyDegree", "url": "https://worthmydegree.com/"}},
    {{"@type": "WebApplication", "@id": "https://worthmydegree.com/#app",
      "name": "WorthMyDegree — Student Loan Payoff & Major ROI Calculator",
      "url": "https://worthmydegree.com/",
      "applicationCategory": "FinanceApplication", "operatingSystem": "Web",
      "offers": {{"@type": "Offer", "price": "0", "priceCurrency": "USD"}},
      "publisher": {{"@id": "https://worthmydegree.com/#org"}}}}
  ]
}}</script>
<style>
{SITE_CSS}
</style>
</head>
<body>
<div class="wrap">

<header>
  <a class="logo" href="/welcome" aria-label="worthmydegree.com">{logo_svg}</a>
  <a class="btn hide-m" href="/?go=1&amp;from=welcome">Open the calculator</a>
</header>

<div class="hero">
  <h1>Is the degree<br>worth the loan?</h1>
  <div class="accent"></div>
  <p class="sub">Pick a school and a major. See the real loan, the monthly
  payment under the 2026 federal rules, and where you stand ten years out,
  before anyone signs on the dotted line.</p>
  <a class="btn big" href="/?go=1&amp;from=welcome">Run your numbers, free</a>
  <div class="trust">Free · anonymous · no sign-up · no ads</div>
</div>

<div class="stats">
  <div class="stat"><b>{f['schools']:,}</b><span>US Schools</span></div>
  <div class="stat"><b>{f['careers']:,}</b><span>Careers</span></div>
  <div class="stat"><b>{f['majors']}</b><span>College Majors</span></div>
  <div class="stat"><b>{f['cities']}</b><span>US Metro Areas</span></div>
</div>

{charts_section}

<section>
  <h2>The numbers colleges don't put on the brochure</h2>
  <p class="deck">Sticker price predicts almost nothing. These are the ones
  that decide how the next decade feels.</p>
  <div class="grid">
    <div class="tile"><b>📊 The loan you'd actually sign</b>
      <p>Median borrowing at your school, or build it from cost, aid and the
      federal caps. Berkeley's sticker is {money(f['berkeley_coa'])}/yr; its
      median borrower leaves with $13,000.</p></div>
    <div class="tile"><b>💸 The payment, not just the debt</b>
      <p>The 2026 RAP income-driven plan against the fixed plans, month by
      month, including what gets waived and what gets forgiven.</p></div>
    <div class="tile"><b>🏙️ Ten years out, where you'll live</b>
      <p>Metro wages and cost of living, not a national average. The same
      salary is a different life in San Francisco and in Columbus.</p></div>
    <div class="tile"><b>🔀 Two paths, side by side</b>
      <p>UC vs CSU, nursing vs CS, or the same degree with two community-college
      years first. One page, same math on both sides.</p></div>
    <div class="tile"><b>🎓 Graduate &amp; professional</b>
      <p>Medicine, dentistry, law, the MBA and five more, priced per school
      from federal data, residency years modeled.</p></div>
    <div class="tile"><b>📄 Take it with you</b>
      <p>Every result exports as a PDF report or a shareable image, sourced
      down to the footnote.</p></div>
  </div>
</section>

<!-- Pathways. Placed BEFORE the tools band on purpose: the tools band says
     what the site does, and this says who it is for. A visitor who assumes
     this is a four-year-college calculator stops reading before the tools.
     Plain .tile inside .wrap, matching the feature section above, rather than
     a second full-bleed .tools band -- two tinted bands in a row merge into
     one and neither heading lands.
     No links. Three of these four are sidebar selections rather than pages,
     so there is nothing to link to, and one linked tile among four reads as
     the only one that works. The tools band directly below carries the CTAs. -->
<section class="paths">
  <h2>Not every path runs through a four-year college</h2>
  <p class="deck">The comparison changes with the life. Each of these is
  modeled in full, priced from the same federal data as everything else.</p>
  <div class="grid">
    <div class="tile"><b>🔧 Straight to work</b>
      <p>487 of the 836 careers here are entered with no degree at all. They
      are priced as a real option: no tuition, no loan, and none of the years
      of pay a degree gives up.</p></div>
    <div class="tile"><b>🏫 Community college first</b>
      <p>Four ways through: two years then transfer, part time while working,
      an associate's on its own, or a bachelor's awarded by the community
      college itself.</p></div>
    <div class="tile"><b>📚 Graduate &amp; professional school</b>
      <p>Master's and doctoral, plus medicine, dentistry, law, the MBA and five
      more, with the unpaid school years and residency stipends inside the
      arithmetic.</p></div>
    <div class="tile"><b>🔁 Returning student</b>
      <p>Going back at 35 or 49 is measured against the salary you already
      earn, not against a debt-free eighteen-year-old. Every sentence on the
      page changes with it.</p></div>
  </div>
</section>

</div>

<!-- The tools band breaks out of .wrap so its background can run edge to
     edge; it carries its own .wrap so the content stays on the same grid as
     every section above and below it. See the tools-band note in SITE_CSS. -->
<section class="tools">
  <div class="wrap">
  <h2>Five tools, one dataset</h2>
  <div class="grid">
    <div class="tile"><b>🎓 The calculator</b>
      <p>School + major + loan → the 10-year verdict.
      <a href="/?go=1&amp;from=welcome">Open&nbsp;→</a></p></div>
    <div class="tile"><b>🔎 Schools that fit a budget</b>
      <p>Every school teaching your field, cheapest first, priced for your
      state. <a href="/?tool=schools&amp;from=welcome">Search&nbsp;→</a></p></div>
    <div class="tile"><b>🏛️ Graduate schools that fit a budget</b>
      <p>Master's, doctoral, medicine, dentistry, law and the MBA, at each
      school's published tuition and fees beside what its graduates in that
      field borrowed.
      <a href="/?tool=gradschools&amp;from=welcome">Find&nbsp;→</a></p></div>
    <div class="tile"><b>🧮 What will colleges expect you to pay?</b>
      <p>The 2027-28 federal aid formula, worked line by line, plus which
      colleges also want the CSS Profile.
      <a href="/?tool=sai&amp;from=welcome">Estimate&nbsp;→</a></p></div>
    <div class="tile"><b>💸 Already have loans?</b>
      <p>Compare the 2026 repayment plans on the balance you already owe.
      <a href="/?tool=repayment&amp;from=welcome">Compare&nbsp;→</a></p></div>
  </div>
  </div>
</section>

<div class="wrap">

{guides_section}

<div class="cta">
  <h2>Two minutes. Zero forms.</h2>
  <p class="deck" style="margin:8px auto 22px">That is all it takes to see where
  you would stand ten years out on the most expensive decision most families
  ever finance.</p>
  <a class="btn big" href="/?go=1&amp;from=welcome">Open the calculator</a>
</div>

<footer>
  Data Source: Bureau of Labor Statistics (May 2025), New York Fed
  (February 2026), College Scorecard (2024 data,
  released June 2026), IPEDS (2023) and CPS ASEC (2025).
  Educational estimate, not financial advice.<br>
  {ORG_STATUS_LINE}<br>
  <a href="/llms.txt" style="color:inherit">Plain-text summary</a> ·
  <a href="/" style="color:inherit">worthmydegree.com</a>
</footer>

</div>
{CARRY_QS_JS}
</body>
</html>
"""


ARTICLE_CSS = """
  article { max-width: 68ch; margin: 0 auto; }
  article h1 { font-size: clamp(30px, 5vw, 44px); line-height: 1.12;
    color: var(--deep); font-weight: 800; margin: 8px 0 6px;
    text-wrap: balance; }
  article .meta { color: var(--muted); font-size: 15px; margin-bottom: 26px; }
  article h2 { font-size: 25px; color: var(--deep); margin: 34px 0 8px; }
  article p { margin-bottom: 16px; }
  article ul { margin: 0 0 16px 22px; }
  article li { margin-bottom: 6px; }
  article img { width: 100%; border-radius: 12px; margin: 20px 0; }
  /* ===== Hero: the photograph, shown as itself =====
     NO TINT. There is deliberately no scrim, no gradient, no filter and no
     blend mode over this image, and none may be added back. The band that
     preceded it painted rgba(0,0,0,0.55) across the whole photograph because
     the headline sat ON the picture in white and had to clear AA against
     whatever a diffusion model happened to return. That was a real constraint
     while the type was on top of the image, and it cost 45% of every hero's
     luminance: a warm kitchen interior and an overcast campus morning arrived
     at the same flat grey.
     Moving the type BELOW the image retires the constraint rather than tuning
     it. Contrast is now a question about dark ink on the page background, so
     the picture is free to be a picture. Anything that puts text back over
     this image brings the scrim back with it.
     background: var(--tile) holds the shape while the bytes arrive and if the
     image 404s -- a neutral placeholder, not a brand colour, for the same
     reason. */
  article > figure.guide-hero {
    margin: 12px 0 22px; border-radius: 14px; overflow: hidden;
    background: var(--tile);
  }
  /* At full desktop width the picture breaks out of the 68ch reading measure
     to the width of the page container. At reading width a 3.5:1 banner is a
     456px strip; at container width it is an opening image, which is the whole
     job of the format.
     Fixed pixels behind a media query, NOT the usual 100vw breakout: 100vw
     counts the scrollbar, so that trick puts a horizontal scrollbar on the
     page it is meant to improve, and this page must never scroll sideways.
     The arithmetic: .wrap is 980px with 24px of padding a side, so its content
     box is 932px and its half-width is 466px. The article is centred inside
     it, so shifting the figure left by (50% of the article - 466px) puts the
     figure's centre on the article's centre whatever the reading measure
     resolves to in the actual font. */
  @media (min-width: 1000px) {
    article > figure.guide-hero { width: 932px; margin-left: calc(50% - 466px); }
  }
  /* Overrides the generic article img above: no second radius inside the
     clipped figure, and no vertical margin, which would show as a strip of
     placeholder at the top and bottom of the frame. */
  article > figure.guide-hero img {
    display: block; width: 100%; height: auto; margin: 0; border-radius: 0;
  }
  /* The line between the picture and the headline: date, reading time, source.
     Reading time is derived (read_minutes), so it matches the figure the
     guides index puts on the same guide's card by construction. */
  article .eyebrow { color: var(--muted); font-size: 14px;
    letter-spacing: 0.02em; margin: 0 0 6px; }
  article blockquote { border-left: 4px solid var(--orange);
    background: var(--tint); border-radius: 0 10px 10px 0;
    padding: 14px 18px; margin: 20px 0; font-size: 18px; }
  /* The renderer wraps quote paragraphs in <p> so a multi-paragraph footnote
     is possible. Zeroing the outer margins keeps every EXISTING single
     paragraph quote pixel-identical to before that change. */
  article blockquote p { margin: 0 0 12px; }
  article blockquote p:last-child { margin-bottom: 0; }
  article hr { border: 0; border-top: 1px solid var(--rule); margin: 30px 0; }
  article table { width: 100%; border-collapse: collapse; margin: 18px 0; }
  article thead th { background: var(--deep); color: #fff; padding: 9px 10px;
    text-align: left; font-size: 15px; }
  article tbody td { padding: 9px 10px; border-bottom: 1px solid var(--rule);
    font-variant-numeric: tabular-nums; }
  /* The reactions bar. Named for what it holds rather than for the like
     button alone: it carries Helpful AND Share, and a rule set called .likes
     styling a share control is the kind of small lie the next reader has to
     work around. flex-wrap because two pills plus the count overflow a 320px
     phone in one line. */
  .reactions { display: flex; align-items: center; flex-wrap: wrap; gap: 12px;
    margin: 34px 0 8px; padding: 16px 0; border-top: 1px solid var(--rule);
    border-bottom: 1px solid var(--rule); }
  .reactions button { font: inherit; font-weight: 700; cursor: pointer;
    background: var(--surface); color: var(--deep);
    border: 2px solid var(--deep); border-radius: 99px; padding: 8px 20px; }
  .reactions button[disabled] { background: var(--tint); border-color: var(--orange);
    color: var(--orange); cursor: default; }
  .reactions button:focus-visible { outline: 3px solid var(--blue);
    outline-offset: 2px; }
  .reactions .count { color: var(--muted); font-size: 15px; }
  /* Share sits at the right edge, away from Helpful. The count is the middle
     element in source order for that reason -- pushing the button with
     margin-left:auto needs everything it is being pushed past to come BEFORE
     it, and putting the button last in the DOM instead would leave the
     revealed fallback link stranded to its right. */
  .reactions #share { margin-left: auto; }
  /* The last-resort share fallback: the link itself, shown only when both copy
     mechanisms failed. `user-select: all` makes one click take the whole URL,
     so the keyboard copy the button suggests has something to act on -- and it
     stays put rather than flashing, because a reader who got this far is
     copying by hand. */
  .reactions .sharelink { flex-basis: 100%; color: var(--muted);
    font-size: 15px; word-break: break-all;
    user-select: all; -webkit-user-select: all; }
  .reactions .sharelink[hidden] { display: none; }
"""


DEFAULT_OG_IMAGE = "feature-og-1200x630.png"


def og_image_for(post: dict) -> str:
    """The social card for one guide.

    Every guide used to share `feature-og-1200x630.png`, so a link to the
    counselor piece previewed identically to the parent piece -- in a feed or a
    group chat, which is where a guide aimed at a named audience actually gets
    passed around, the card is most of what a reader sees before deciding
    whether to click. `card:` in front matter overrides; otherwise a post with a
    hero gets the card built from that same photograph, so the preview and the
    page a reader lands on are recognisably the same thing.

    The name is derived rather than declared: the card is generated FROM the
    hero (brand/build_ai_hero.py) and a second front-matter field would be one
    more thing to keep in step. If the file is absent the build fails through
    missing_static() rather than serving a broken card.
    """
    if post.get("card"):
        return post["card"]
    if post.get("hero"):
        return f"guide-og-{post['slug']}.png"
    return DEFAULT_OG_IMAGE


def article_jsonld(post: dict, canonical: str, image: str, lastmod: str) -> str:
    """schema.org Article for one guide.

    The Worker injects Organization/WebApplication into the APP shell, but a
    guide is returned verbatim from the GUIDES constant and never passed
    through that path, so these pages carried no structured data at all. That
    is the half a search engine reads for a headline, a publish date and a
    modified date -- everything a guide has and the calculator does not.

    Built as a dict and serialised with json.dumps, never an f-string: a title
    holding an apostrophe or a quote would otherwise break out of the literal
    and produce invalid JSON-LD, which is ignored silently rather than
    reported.
    """
    data = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": post["title"],
        "description": post["description"],
        "datePublished": post["date"],
        "dateModified": lastmod,
        "url": canonical,
        "mainEntityOfPage": {"@type": "WebPage", "@id": canonical},
        "image": f"https://worthmydegree.com/app/static/{image}",
        "inLanguage": "en-US",
        "isAccessibleForFree": True,
        "author": {"@type": "Organization", "name": "worthmydegree.com",
                   "url": "https://worthmydegree.com/"},
        "publisher": {"@type": "Organization", "name": "worthmydegree.com",
                      "url": "https://worthmydegree.com/"},
    }
    # A guide built around one piece of published research says so in its
    # structured data, not only in its prose. Optional, and both halves are
    # required together: a citation with a name and no URL is unresolvable,
    # and one with a URL and no name is a bare link a reader cannot identify.
    # Authors are semicolon separated because a name may contain a comma.
    if post.get("citation_name") and post.get("citation_url"):
        citation = {"@type": "ScholarlyArticle",
                    "name": post["citation_name"],
                    "url": post["citation_url"]}
        authors = [a.strip() for a in post.get("citation_authors", "").split(";") if a.strip()]
        if authors:
            citation["author"] = [{"@type": "Person", "name": a} for a in authors]
        data["citation"] = citation
    return ('<script type="application/ld+json">'
            + json.dumps(data, ensure_ascii=False) + "</script>")


def _page_head(title, description, canonical, image, favicon, jsonld=""):
    """One <head> for every page on the edge site, so a guide and the landing
    carry the same card, the same icon and the same theme handling."""
    return f'''<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_attr(title)}</title>
<meta name="description" content="{_attr(description)}">
<link rel="canonical" href="{_attr(canonical)}">
<meta property="og:type" content="article">
<meta property="og:title" content="{_attr(title)}">
<meta property="og:description" content="{_attr(description)}">
<meta property="og:url" content="{_attr(canonical)}">
<meta property="og:image" content="https://worthmydegree.com/app/static/{_attr(image)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="https://worthmydegree.com/app/static/{_attr(image)}">
<link rel="icon" href="data:image/svg+xml;base64,{favicon}">
{jsonld}
<style>
{SITE_CSS}
{ARTICLE_CSS}
</style>'''


def build_guide_html(post, logo_svg, favicon, lastmod: str = None) -> str:
    """One article page. Same shell as the landing, plus the like control.

    The like button is progressive: it is a plain button that works the moment
    the page paints, and the count fills in afterwards from our OWN origin. If
    the count request fails the article is unaffected -- which is the property
    the whole edge site is built around, so a reaction counter must not be the
    thing that breaks it.
    """
    body = render_markdown(post["body"])
    canonical = f"https://worthmydegree.com/guides/{post['slug']}"

    # ===== The hero is a PHOTOGRAPH, above the headline =====
    #
    # It used to be a background image with the live <h1> painted on top, which
    # forced a scrim over the whole band -- rgba(0,0,0,0.55), the only thing
    # guaranteeing the white heading stayed legible over a picture nobody had
    # seen yet. That scrim threw away 45% of every photograph's luminance and
    # was the loudest thing on the page: a warm afternoon kitchen and a grey
    # campus morning came out looking like the same washed-out image.
    #
    # Putting the picture ABOVE the type removes the reason for it. Dark text
    # on white needs no scrim, so the photograph renders as itself, and the
    # headline is an ordinary <h1> in the article's own type scale rather than
    # white text sized to survive a background. It is also what a blog index
    # like collegewise.com/blog does: picture, then date and reading time, then
    # the title.
    #
    # The rule the old approach was protecting still holds and still applies:
    # a hero is a picture with NO WORDS IN IT. Baking the headline into the
    # image was measured on 2026-08-12 at 13.6px on a 390pt phone, because the
    # picture scales with the column while a live heading reflows. Live text
    # also stays selectable, translatable and reachable by a screen reader, and
    # a generated image never has to render a word, which diffusion models do
    # badly.
    #
    # `hero:` in the front matter opts a post in; without it the header is
    # exactly what it was. alt="" because these are decorative openers -- the
    # <h1> immediately below says what the page is, and a screen reader should
    # not have to hear a description of a stock photograph first.
    hero = post.get("hero")
    meta_line = (f'<time datetime="{post["date"]}">{card_date(post["date"])}'
                 f'</time> · {read_minutes(post)} min read · worthmydegree.com')
    back = ('<p class="meta"><a href="/guides" '
            'style="color:var(--blue);text-decoration:none">← All guides</a></p>')
    if hero:
        rendered = article_hero(hero)
        w, h = image_ratio(rendered)
        head = (f'{back}\n'
                f'  <figure class="guide-hero"><img src="/app/static/{rendered}"'
                f' alt="" width="{w}" height="{h}" fetchpriority="high"></figure>\n'
                f'  <p class="eyebrow">{meta_line}</p>\n'
                f'  <h1>{_inline(post["title"])}</h1>')
    else:
        head = (f'{back}\n'
                f'  <p class="eyebrow">{meta_line}</p>\n'
                f'  <h1>{_inline(post["title"])}</h1>')
    return f'''<!doctype html>
<html lang="en">
<head>
{_page_head(post["title"] + " — worthmydegree.com", post["description"],
            canonical, og_image_for(post), favicon,
            article_jsonld(post, canonical, og_image_for(post), lastmod or post["date"]))}
</head>
<body>
<div class="wrap">
<header>
  <a class="logo" href="/welcome" aria-label="worthmydegree.com">{logo_svg}</a>
  <a class="btn hide-m" href="/?go=1&amp;from=guide">Open the calculator</a>
</header>

<article>
  {head}
{body}

  <div class="reactions">
    <button id="like" data-slug="{post["slug"]}">♥ Helpful</button>
    <span class="count" id="likecount">&nbsp;</span>
    <button id="share" type="button">🔗 Share</button>
    <span class="sharelink" id="sharelink" hidden></span>
  </div>

  <div class="cta" style="padding:34px 0">
    <a class="btn big" href="/?go=1&amp;from=guide">Run your own numbers, free</a>
    <div class="trust">Free · anonymous · no sign-up</div>
  </div>
</article>

<footer>
  Data Source: Bureau of Labor Statistics (May 2025), New York Fed
  (February 2026), College Scorecard (2024 data,
  released June 2026), IPEDS (2023) and CPS ASEC (2025).
  Educational estimate, not financial advice.<br>
  {ORG_STATUS_LINE}<br>
  <a href="/guides" style="color:inherit">All guides</a> ·
  <a href="/" style="color:inherit">worthmydegree.com</a>
</footer>
</div>
<script>
(function () {{
  var btn = document.getElementById("like");
  var out = document.getElementById("likecount");
  var slug = btn.dataset.slug;
  var key = "liked:" + slug;
  function render(n) {{
    out.textContent = n === null ? "" :
      (n === 0 ? "" :
       n === 1 ? "1 person found this helpful" : n + " people found this helpful");
  }}
  // The count is a nicety, not the point: if it never arrives the button still
  // works and the article is unaffected.
  fetch("/api/likes?slug=" + encodeURIComponent(slug))
    .then(function (r) {{ return r.json(); }})
    .then(function (d) {{ render(typeof d.count === "number" ? d.count : null); }})
    .catch(function () {{}});
  if (localStorage.getItem(key)) {{ btn.disabled = true; btn.textContent = "♥ Thanks"; }}
  btn.addEventListener("click", function () {{
    if (btn.disabled) return;
    btn.disabled = true; btn.textContent = "♥ Thanks";
    try {{ localStorage.setItem(key, "1"); }} catch (e) {{}}
    // location.search rides along, and both halves of it matter: ?src= is the
    // recruitment tag (read straight off the page URL, exactly as the Worker
    // reads it for a guide_view -- without this the column was NULL for every
    // reaction ever recorded), and ?test=1 / ?src=selftest are what let the
    // Worker refuse to log the author's own verification taps. CARRY_QS_JS
    // rewrites <a href> only; a fetch has to do it itself.
    fetch("/api/like" + location.search, {{
      method: "POST", headers: {{"content-type": "application/json"}},
      body: JSON.stringify({{slug: slug}}),
    }}).then(function (r) {{ return r.json(); }})
      .then(function (d) {{ if (typeof d.count === "number") render(d.count); }})
      .catch(function () {{}});
  }});
}})();
</script>
<script>
(function () {{
  // Share. The native sheet where the browser has one -- which is the phone,
  // and a guide written for parents and counselors is forwarded far more often
  // than it is posted -- and a clipboard copy everywhere else.
  //
  // The URL is the CANONICAL one baked in at build time, never location.href.
  // A reader who arrived on ?src=<channel> would otherwise hand the recipient
  // the sharer's own recruitment tag, and that recipient was never recruited
  // through it: the same fabricated attribution that keeps src out of the
  // app's share links. It also drops ?test=1 and any junk in the address bar.
  var btn = document.getElementById("share");
  if (!btn) return;
  var url = {js_string(canonical)};
  var title = {js_string(post["title"])};
  var label = btn.textContent;
  var timer = null;
  var slug = document.getElementById("like").dataset.slug;
  function record() {{
    // Called ONLY where the link actually left the page: the share sheet
    // resolved, or the clipboard took it. Not on the cancel (the reader
    // changed their mind) and not on the reveal fallback (we showed them a
    // link and cannot know whether they took it) -- a counter that fires on
    // the click alone counts curiosity as sharing.
    //
    // keepalive because the native sheet backgrounds the page on a phone, and
    // an ordinary fetch is cancelled when it does. Failure is silent all the
    // way down: this is a statistic and the reader is mid-article.
    try {{
      // location.search carries the ?src= tag and the ?test=1 / ?src=selftest
      // exclusions -- see the like POST above for why a fetch must do this
      // itself.
      fetch("/api/share" + location.search, {{
        method: "POST",
        headers: {{"content-type": "application/json"}},
        body: JSON.stringify({{slug: slug}}),
        keepalive: true,
      }}).catch(function () {{}});
    }} catch (e) {{}}
  }}
  function flash(text) {{
    btn.textContent = text;
    clearTimeout(timer);
    // Back to the label rather than staying on the confirmation: the button
    // has to be usable a second time, on a different device or a second chat.
    timer = setTimeout(function () {{ btn.textContent = label; }}, 2400);
  }}
  function revealLink() {{
    // Everything automatic failed. Put the link on the page, selected, and say
    // so -- naming a keyboard shortcut with nothing selected (which is what
    // this did first) asks the reader to copy air, and it names the wrong key
    // on two platforms out of three.
    var out = document.getElementById("sharelink");
    out.textContent = url;
    out.hidden = false;
    try {{
      var range = document.createRange();
      range.selectNodeContents(out);
      var sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    }} catch (e) {{}}
    flash("Copy this link:");
  }}
  function legacyCopy() {{
    // execCommand is deprecated and is the only thing that works when
    // navigator.clipboard is absent or refused (it needs a secure context and
    // can still be denied by permission). Failure is SAID, not swallowed: a
    // button that looks like it copied and did not is worse than one that
    // admits it, because the reader pastes nothing and blames themselves.
    try {{
      var ta = document.createElement("textarea");
      ta.value = url;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.top = "-1000px";
      document.body.appendChild(ta);
      ta.select();
      var ok = document.execCommand("copy");
      document.body.removeChild(ta);
      if (ok) {{ record(); flash("✓ Link copied"); }} else {{ revealLink(); }}
    }} catch (e) {{
      revealLink();
    }}
  }}
  function copy() {{
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      navigator.clipboard.writeText(url).then(
        function () {{ record(); flash("✓ Link copied"); }}, legacyCopy);
    }} else {{
      legacyCopy();
    }}
  }}
  btn.addEventListener("click", function () {{
    if (navigator.share) {{
      // AbortError is the reader closing the sheet -- a decision, not a
      // failure, and falling back to a copy there would put a link on their
      // clipboard that they just declined to send. Anything else IS a
      // failure, and the copy is the useful answer to it.
      navigator.share({{title: title, url: url}}).then(function () {{
        // A SUCCESSFUL SHARE HAS TO SAY SO. This branch recorded the row
        // and changed nothing on screen, so on every browser that HAS
        // navigator.share -- all mobile, and desktop Chrome -- the button
        // looked dead: the sheet opens over the page, the reader completes
        // or dismisses it, and the page underneath is identical. Reported as
        // "the share button is not working" while the rows were landing
        // correctly. The copy path had always confirmed itself.
        record(); flash("\\u2713 Shared");
      }}, function (err) {{
        if (err && err.name === "AbortError") return;
        copy();
      }});
      return;
    }}
    copy();
  }});
}})();
</script>
{CARRY_QS_JS}
</body>
</html>
'''


def read_minutes(post: dict) -> int:
    """Reading time in whole minutes, from the post's own word count.

    200 words a minute is the usual convention for adult non-fiction and it is
    close enough for a label whose job is "is this a two-minute thing or a
    ten-minute thing". Derived rather than declared, so it cannot go stale
    against the text the way a front-matter field would. Tables are counted as
    the words they contain, which slightly over-reads a figure-heavy guide;
    that errs toward telling someone it is longer than it is, which is the
    forgiving direction.
    """
    return max(1, round(len(post.get("body", "").split()) / 200))


def card_date(iso: str) -> str:
    """2026-08-13 -> Aug 13, 2026. The ISO string stays in the datetime
    attribute for machines; this is the half a person reads."""
    try:
        return datetime.date.fromisoformat(iso).strftime("%b %-d, %Y")
    except ValueError:
        return iso


CARD_THUMB_WIDTH = 720          # 2x the ~360px a card is ever drawn at
ARTICLE_HERO_WIDTH = 1360       # 2x the ~680px an article column is ever drawn at
JPEG_QUALITY = 82


def _resized_jpeg(source: str, width: int, prefix: str) -> str:
    """A width-limited JPEG of a source image, generated once and committed
    beside it. Returns the new filename, or the source unchanged when the
    source is not in static/ (the caller's page still renders).

    The heroes are 1600x459 PNGs of about a megabyte each, which is right for
    an archival original and absurd for anything a browser draws. Serving them
    directly put 4.3 MB of images on the guides index, roughly thirty times the
    bytes the page can use, and a 1.27 MB background on a single article page.
    The visitor most likely to open a shared guide link is on a phone.

    JPEG rather than PNG because these are photographs, where PNG's lossless
    encoding buys nothing and costs an order of magnitude. Pillow is already
    installed (matplotlib depends on it and app.py imports PIL directly), so
    this adds no dependency, in keeping with this file's rule about using only
    what production already ships.

    Two sizes exist rather than one because a card is drawn at ~360px and an
    article hero at ~680px, and a single file cannot serve both without being
    wrong for one of them: the card size is blurry as a hero, and the hero size
    is four cards' worth of bytes on an index that shows every guide at once.

    Regenerated only when the source is newer, so a normal build does no image
    work at all.
    """
    from PIL import Image

    out = f"{prefix}-{Path(source).stem}.jpg"
    src, dst = ROOT / "static" / source, ROOT / "static" / out
    if not src.exists():
        return source
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        return out
    with Image.open(src) as im:
        im = im.convert("RGB")
        height = round(im.height * width / im.width)
        im = im.resize((width, height), Image.LANCZOS)
        im.save(dst, "JPEG", quality=JPEG_QUALITY, optimize=True,
                progressive=True)
    print(f"  {prefix} image {out}  ({dst.stat().st_size:,} bytes"
          f" from {src.stat().st_size:,})")
    return out


def card_thumb(source: str) -> str:
    """The guides-index card picture for a hero. See _resized_jpeg."""
    return _resized_jpeg(source, CARD_THUMB_WIDTH, "card")


# The /charts gallery. Sources live OUTSIDE static/ and outside git.
CHART_DIR = ROOT / "marketing" / "infographics"
CHART_FULL_WIDTH = 1400        # the picture on the gallery page itself
# 810px is 390 CSS px at a phone's 2x, so a tall render stays sharp at about
# 200 KB instead of the 360 KB the full 1080 costs. The feed lazy-loads them,
# but a swipe should not wait on the next one.
CHART_PHONE_WIDTH = 810
CHART_CARD_W, CHART_CARD_H = 720, 405     # 16:9, matching the guide cards
# The LANDING band shows the whole picture instead, padded to a square rather
# than cropped to 16:9. Two different jobs, so two different files: the gallery
# is a grid of twelve doorways where uniformity is what makes it scannable, and
# the landing band is two cards carrying the finding itself, where a headline
# sliced off at both margins is the thing a reader notices first.
#
# Square because these run 0.77 to 1.37 and a square is the shape that pads all
# of them least -- 11% and 15% on the two showing today, against the 40%+ a
# 16:9 box would put on the portrait ones. The padding takes each chart's OWN
# border colour, so on the dark charts the letterbox is invisible and the card
# reads as one picture rather than a picture in a frame.
CHART_LAND_PX = 640


def cache_bust(name: str) -> str:
    """`?v=<content hash>` for a stable-named asset, or "" when it is absent.

    The query is enough: Cloudflare's Cache API and every browser key on the
    full URL, so changed bytes get a new key without renaming a committed file
    and without touching the several places that reference these names.
    """
    path = ROOT / "static" / name
    if not path.exists():
        return ""
    return "?v=" + hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def _chart_jpeg(source: str, out: str, width: int, box=None, pad=None) -> bool:
    """One web-sized JPEG in static/ from a full-resolution chart PNG.

    A MISSING SOURCE IS NOT AN ERROR when the output already exists, and that
    is the whole reason this is not _resized_jpeg. Those sources read from
    static/, which is committed; these read from marketing/infographics/, which
    is NOT -- so on a fresh clone the PNGs are simply absent while the JPEGs
    this produced are sitting in git right where the page expects them.
    Treating that as a failure would make the repo unbuildable by anyone but
    the machine that first ran it.

    Returns True when the output exists afterwards, so the caller can decide
    what to do about a chart it genuinely cannot draw.
    """
    from PIL import Image

    src, dst = CHART_DIR / source, ROOT / "static" / out
    if not src.exists():
        return dst.exists()
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        return True
    with Image.open(src) as im:
        im = im.convert("RGB")
        if box:
            # Uniform cards from images that are not a uniform shape: these run
            # from 0.77 (tall) to 1.37 (wide), and a grid of ragged tiles reads
            # as broken rather than as varied. Cropped from the TOP, not the
            # centre: every one of these charts puts its headline at the top,
            # so a centred crop would show the middle of a bar chart and no
            # indication of what it is about. The card is a doorway; the whole
            # image is one click away.
            cw, ch = box
            scale = max(cw / im.width, ch / im.height)
            im = im.resize((max(cw, round(im.width * scale)),
                            max(ch, round(im.height * scale))), Image.LANCZOS)
            left = (im.width - cw) // 2
            im = im.crop((left, 0, left + cw, ch))
        elif pad:
            # Contain, not cover: the whole picture, centred on a canvas of the
            # image's own border colour. Sampled as the median of the four
            # edges rather than one corner pixel, so a stray bright pixel or a
            # logo sitting in a corner cannot pick the colour for the whole
            # card.
            pw, ph = pad
            scale = min(pw / im.width, ph / im.height)
            im = im.resize((max(1, round(im.width * scale)),
                            max(1, round(im.height * scale))), Image.LANCZOS)
            edge = []
            for x in range(0, im.width, max(1, im.width // 64)):
                edge += [im.getpixel((x, 0)), im.getpixel((x, im.height - 1))]
            for y in range(0, im.height, max(1, im.height // 64)):
                edge += [im.getpixel((0, y)), im.getpixel((im.width - 1, y))]
            bg = tuple(sorted(c[i] for c in edge)[len(edge) // 2]
                       for i in range(3))
            canvas = Image.new("RGB", (pw, ph), bg)
            canvas.paste(im, ((pw - im.width) // 2, (ph - im.height) // 2))
            im = canvas
        else:
            height = round(im.height * width / im.width)
            im = im.resize((width, height), Image.LANCZOS)
        im.save(dst, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    print(f"  chart image {out}  ({dst.stat().st_size:,} bytes"
          f" from {src.stat().st_size:,})")
    return True


def load_charts() -> list:
    """Every chart in content/charts/, newest first.

    Same front-matter parser as the guides, because they are the same kind of
    file and a second one would drift. `image` is required here where it is
    optional for a post: a chart with no picture is not a chart.
    """
    d = ROOT / "content" / "charts"
    if not d.exists():
        return []
    charts = []
    for path in sorted(d.glob("*.md")):
        meta = parse_post(path)
        if not meta.get("image"):
            raise ValueError(f"{path.name}: a chart must name an image")
        meta["full"] = f"info-{meta['slug']}.jpg"
        meta["card"] = f"card-info-{meta['slug']}.jpg"
        meta["land"] = f"land-info-{meta['slug']}.jpg"
        meta["drawable"] = (
            _chart_jpeg(meta["image"], meta["full"], CHART_FULL_WIDTH)
            and _chart_jpeg(meta["image"], meta["card"], 0,
                            box=(CHART_CARD_W, CHART_CARD_H))
            and _chart_jpeg(meta["image"], meta["land"], 0,
                            pad=(CHART_LAND_PX, CHART_LAND_PX)))
        # A CORRECTED CHART IS INVISIBLE FOR AN HOUR WITHOUT THIS. These
        # filenames are STABLE and the file is regenerated in place, so a
        # redeploy mints no new URL and the edge keeps serving the old bytes
        # for the whole /app/static TTL. That is the opposite of the assumption
        # EDGE_CACHED rests on -- Streamlit's own bundles are content-hashed, so
        # a redeploy renames them and the TTL can never serve a stale byte.
        # Reported as "the images in the gallery are the older versions".
        # A SEPARATE KEY, not the filename with a query glued on. Three
        # consumers mean "a file on disk" (the two _chart_jpeg calls above and
        # check_content's served-image sweep) and three mean "a URL a browser
        # fetches". Overwriting the one key made the guard report every chart in
        # the gallery as a missing image, which is the guard working correctly
        # against a name that had quietly changed meaning.
        for key in ("full", "card", "land"):
            meta[f"{key}_url"] = meta[key] + cache_bust(meta[key])
        # THE PHONE VARIANT IS OPTIONAL. A chart script's --phone mode writes
        # phone-<image>.png beside the gallery source, a 1080x1920 frame laid
        # out for a tall screen. Where it exists the feed shows it full-screen
        # instead of the desktop picture; where it does not, the feed shows
        # the desktop picture at width, as before. Same missing-source rule
        # as the three above: absent source with a committed output is fine.
        meta["phone"] = f"phone-info-{meta['slug']}.jpg"
        meta["has_phone"] = _chart_jpeg("phone-" + meta["image"], meta["phone"], CHART_PHONE_WIDTH)
        meta["phone_url"] = (meta["phone"] + cache_bust(meta["phone"])
                             if meta["has_phone"] else None)
        # THE DAY VERSION, equally optional: a script's --light writes
        # light-<image>.png (and light-phone-<image>.png). Served through the
        # <picture> element on prefers-color-scheme: light, for the full
        # picture, the card crop and the phone frame. The landing square stays
        # the dark one: it sits on the landing's own light band by design.
        meta["light"] = f"light-info-{meta['slug']}.jpg"
        meta["light_card"] = f"light-card-info-{meta['slug']}.jpg"
        meta["light_phone"] = f"light-phone-info-{meta['slug']}.jpg"
        has_light = (_chart_jpeg("light-" + meta["image"], meta["light"], CHART_FULL_WIDTH)
                     and _chart_jpeg("light-" + meta["image"], meta["light_card"], 0,
                                     box=(CHART_CARD_W, CHART_CARD_H)))
        has_light_phone = _chart_jpeg("light-phone-" + meta["image"], meta["light_phone"],
                                      CHART_PHONE_WIDTH)
        meta["light_url"] = meta["light"] + cache_bust(meta["light"]) if has_light else None
        meta["light_card_url"] = (meta["light_card"] + cache_bust(meta["light_card"])
                                  if has_light else None)
        meta["light_phone_url"] = (meta["light_phone"] + cache_bust(meta["light_phone"])
                                   if has_light_phone else None)
        charts.append(meta)
    return sorted(charts, key=lambda m: m["date"], reverse=True)


CHART_RANKING_FILE = ROOT / "content" / "charts" / "_ranking.json"


def charts_by_rank(charts: list) -> list:
    """Charts ordered most-liked first, or newest first when no ranking exists.

    THE RANKING IS A COMMITTED FILE, not a query. Likes live in Supabase, and
    this landing page is baked into infra/worker.js and served from Cloudflare
    precisely so it needs nothing at view time -- the note on landing_view in
    CLAUDE.md records that an external reference on this page would cost it the
    one property that makes it worth having, since it renders when the origin is
    down. Reading Supabase at BUILD time instead would only move the dependency:
    the build would then fail whenever the database is unreachable, which it was
    on the day this was written.

    So infra/rank_charts.py writes the order into _ranking.json when the network
    allows, that file is committed and reviewable in a diff, and this reads it.
    Missing or unreadable, the order falls back to newest first, which is what
    load_charts already returns. A stale ranking is a wrong ORDER; a failed
    build is no page.

    Slugs in the file that no longer exist are ignored, and charts missing from
    the file keep their newest-first order after the ranked ones, so adding a
    chart never silently drops it from the running.
    """
    try:
        ranked = json.loads(CHART_RANKING_FILE.read_text()).get("by_likes") or []
    except (FileNotFoundError, ValueError, AttributeError):
        return charts
    position = {slug: i for i, slug in enumerate(ranked)}
    return sorted(charts, key=lambda c: position.get(c["slug"], len(position)))


def article_hero(source: str) -> str:
    """The in-article photograph for a hero. See _resized_jpeg."""
    return _resized_jpeg(source, ARTICLE_HERO_WIDTH, "hero")


def image_ratio(name: str, fallback=(1600, 459)) -> tuple:
    """(width, height) of a static image, for the img attributes.

    Present so the browser can reserve the right box before the bytes arrive.
    Guessing here is not harmless: a wrong ratio on a full-width photograph
    reserves the wrong height and the article text jumps when the image lands,
    which is the one layout fault a reader always notices.
    """
    path = ROOT / "static" / name
    if not path.exists():
        return fallback
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:
        return fallback


def card_image_for(post: dict):
    """The card's picture, or None when the post has no hero of its own.

    Deliberately NOT falling back to DEFAULT_OG_IMAGE the way og_image_for
    does. That default is right for a social card, where every link needs some
    picture, and wrong in a grid, where it would put the identical image on
    every guide that lacks a hero and make the page look broken rather than
    plain. None means the card renders type-only, which is a design that works.
    """
    source = post.get("card") or post.get("hero")
    return card_thumb(source) if source else None


CHART_SHARE_BASE = "https://worthmydegree.com/charts"


def build_charts_index_html(charts, logo_svg, favicon) -> str:
    """The /charts gallery: every infographic, each with its own reactions.

    ONE PAGE, NOT ONE PAGE PER CHART. A guide has a body worth its own URL; a
    chart is a single picture, and six pages each holding one image would be
    six thin pages competing with each other for the same search intent. The
    trade is that a share link cannot address a chart on its own, which is what
    the #slug fragment is for.

    THE REACTION JS IS A LOOP, and that is the whole difference from
    build_guide_html's version. That one resolves `document.getElementById
    ("like")` because an article page has exactly one. Six on a page means
    per-card lookups, so everything keys off the card element and its data-slug.
    Copying the article version and changing the selector by hand is how the two
    drift; this reimplements the same three rules deliberately and repeats them
    in comments where they are easy to lose.
    """
    cards = []
    for c in charts:
        if not c.get("drawable"):
            # A chart whose picture cannot be produced is skipped rather than
            # rendered as a broken image. This happens on a clone that has the
            # committed JPEGs but not the gitignored PNGs -- in which case the
            # JPEGs ARE there and this never fires -- or when a manifest names
            # an image nobody has. missing_static() would fail the build on the
            # second case anyway; this keeps the page honest in the meantime.
            continue
        tall = " tall" if c.get("has_phone") else ""
        # Sources in order of specificity: the browser takes the FIRST whose
        # media query matches, so day+phone comes before phone, and day
        # before the default. A chart with no day version simply has no
        # light sources and every viewer gets the dark picture.
        light_srcs = ""
        if c.get("light_phone_url"):
            light_srcs += (f'\n        <source media="(max-width: 720px) and (prefers-color-scheme: light)"'
                           f' srcset="/app/static/{c["light_phone_url"]}">')
        phone_src = (f'\n        <source media="(max-width: 720px)"'
                     f' srcset="/app/static/{c["phone_url"] or c["full_url"]}">')
        if c.get("light_card_url"):
            light_srcs_card = (f'\n        <source media="(prefers-color-scheme: light)"'
                               f' srcset="/app/static/{c["light_card_url"]}">')
        else:
            light_srcs_card = ""
        light_full = f' data-light="/app/static/{c["light_url"]}"' if c.get("light_url") else ""
        cards.append(f'''  <div class="chart-card{tall}" id="{c["slug"]}">
    <a class="shot" href="/app/static/{c["full_url"]}" target="_blank" rel="noopener"{light_full}
       aria-label="Open the full-size infographic: {_attr(c["title"])}">
      <picture>{light_srcs}{phone_src}{light_srcs_card}
        <img src="/app/static/{c["card_url"]}" alt="{_attr(c["description"])}"
             loading="lazy" width="720" height="405"></picture></a>
    <b>{_attr(c["title"])}</b>
    <span class="sum">{_attr(c["summary"])}</span>
    <p class="src">{_attr(c.get("source", ""))}</p>
    <div class="reactions" data-slug="{c["slug"]}" data-title="{_attr(c["title"])}">
      <button class="like" type="button">&#9829; Helpful</button>
      <span class="count">&nbsp;</span>
      <button class="share" type="button">&#128279; Share</button>
      <span class="sharelink" hidden></span>
    </div>
  </div>''')
    cards = "\n".join(cards)
    return f'''<!doctype html>
<html lang="en">
<head>
{_page_head("Infographics — worthmydegree.com",
            "Free infographics on what college costs, what majors pay, and "
            "what the 2026 student loan rules changed. Built from federal data.",
            CHART_SHARE_BASE, "feature-og-calculator-1200x630.png", favicon)}
</head>
<body>
<div class="wrap">
<header>
  <a class="logo" href="/welcome" aria-label="worthmydegree.com">{logo_svg}</a>
  <a class="btn hide-m" href="/?go=1&amp;from=charts">Open the calculator</a>
</header>
<section class="guides-band">
  <h1>Infographics: the college money picture, one page at a time</h1>
  <div class="accent"></div>
  <p>Every infographic is built from federal data: Bureau of Labor Statistics
  wages, College Scorecard costs and borrowing, New York Fed outcomes. Free to
  use anywhere, no permission needed. Click any one for the full-size version.</p>
</section>

<div class="post-head">
  <h2>All infographics</h2>
  <span>{len(charts)} infographic{"" if len(charts) == 1 else "s"}</span>
</div>
<div class="post-grid">
{cards}
</div>

<div id="viewer" class="viewer" hidden>
  <button class="close" type="button" aria-label="Close">&#10005;</button>
  <img alt="">
  <div class="vbar">
    <button class="vlike" type="button" aria-label="Helpful">&#9829;</button>
    <button class="vshare" type="button" aria-label="Share">&#128279;</button>
  </div>
</div>
<div class="cta" style="padding:34px 0">
  <a class="btn big" href="/?go=1&amp;from=charts">Run your own numbers, free</a>
  <div class="trust">Free · anonymous · no sign-up</div>
</div>

<footer>
  <a href="/guides" style="color:inherit">Guides</a> ·
  <a href="/" style="color:inherit">worthmydegree.com</a> · Educational
  estimate, not financial advice.<br>
  {ORG_STATUS_LINE}
</footer>
</div>
{CARRY_QS_JS}
<script>
(function () {{
  var base = {js_string(CHART_SHARE_BASE)};
  document.querySelectorAll(".chart-card .reactions").forEach(function (bar) {{
    var slug  = bar.dataset.slug;
    var title = bar.dataset.title;
    var url   = base + "#" + slug;
    var like  = bar.querySelector(".like");
    var share = bar.querySelector(".share");
    var out   = bar.querySelector(".count");
    var link  = bar.querySelector(".sharelink");
    var key   = "liked:chart:" + slug;

    if (like.disabled) like.classList.add("on");
    function render(n) {{
      out.textContent = n === null ? "" :
        (n === 0 ? "" :
         n === 1 ? "1 person found this helpful" : n + " people found this helpful");
    }}
    // The count is a nicety. If it never arrives the buttons still work.
    fetch("/api/likes?slug=" + encodeURIComponent(slug) + "&kind=chart")
      .then(function (r) {{ return r.json(); }})
      .then(function (d) {{ render(typeof d.count === "number" ? d.count : null); }})
      .catch(function () {{}});
    if (localStorage.getItem(key)) {{ like.disabled = true; like.textContent = "\\u2665 Thanks"; }}

    like.addEventListener("click", function () {{
      if (like.disabled) return;
      like.disabled = true; like.textContent = "\\u2665 Thanks"; like.classList.add("on");
      try {{ localStorage.setItem(key, "1"); }} catch (e) {{}}
      // location.search rides along for the reason the guide version gives:
      // ?src= is the recruitment tag and ?test=1 / ?src=selftest are what let
      // the Worker refuse to log the author's own taps. CARRY_QS_JS rewrites
      // <a href> only, so a fetch has to carry it itself.
      fetch("/api/like" + location.search, {{
        method: "POST", headers: {{"content-type": "application/json"}},
        body: JSON.stringify({{slug: slug, kind: "chart"}}),
      }}).then(function (r) {{ return r.json(); }})
        .then(function (d) {{ if (typeof d.count === "number") render(d.count); }})
        .catch(function () {{}});
    }});

    function record() {{
      // keepalive, because the native share sheet backgrounds the page on a
      // phone and an ordinary fetch is cancelled with it.
      fetch("/api/share" + location.search, {{
        method: "POST", keepalive: true,
        headers: {{"content-type": "application/json"}},
        body: JSON.stringify({{slug: slug, kind: "chart"}}),
      }}).catch(function () {{}});
    }}
    function flash(msg) {{
      link.hidden = false; link.textContent = msg;
      setTimeout(function () {{ link.hidden = true; }}, 4000);
    }}
    function copy() {{
      if (navigator.clipboard && navigator.clipboard.writeText) {{
        navigator.clipboard.writeText(url).then(
          function () {{ record(); flash("\\u2713 Link copied"); }},
          function () {{ flash(url); }});
      }} else {{
        flash(url);
      }}
    }}
    share.addEventListener("click", function () {{
      if (navigator.share) {{
        // AbortError is the reader closing the sheet: a decision, not a
        // failure. Falling back to a copy would put a link on their clipboard
        // that they just declined to send, and recording it would count a
        // cancellation as a share.
        navigator.share({{title: title, url: url}}).then(function () {{
          // A SUCCESSFUL SHARE HAS TO SAY SO. This branch recorded the row
          // and changed nothing on screen, so on every browser that HAS
          // navigator.share -- all mobile, and desktop Chrome -- the button
          // looked dead: the sheet opens over the page, the reader completes
          // or dismisses it, and the page underneath is identical. Reported as
          // "the share button is not working" while the rows were landing
          // correctly. The copy path had always confirmed itself.
          record(); flash("\\u2713 Shared");
        }}, function (err) {{
          if (err && err.name === "AbortError") return;
          copy();
        }});
        return;
      }}
      copy();
    }});
  }});

  // THE VIEWER. On a phone a tap on a picture opens it here, on the page,
  // at twice the screen's width so the chart's text is readable, scrollable
  // in both directions and pinchable. It used to open the JPEG in a new tab,
  // which a phone treats as a file: Android downloads it, iOS shows it with
  // no way back but the browser's own. Desktop keeps the new tab. A second
  // tap on the picture toggles between fit-to-width and readable width.
  var viewer = document.getElementById("viewer");
  var viewerImg = viewer && viewer.querySelector("img");
  var phone = window.matchMedia("(max-width: 720px)");
  // THE DESKTOP LINK FOLLOWS THE SCHEME TOO. On a desktop the picture is a
  // plain link that opens the full-size file in a new tab, and the file it
  // named was always the dark one; a light-mode reader clicked a white card
  // and got a black page. Where a day version exists, the link is retargeted
  // at load time to match the scheme, so the tab shows what the card showed.
  if (window.matchMedia("(prefers-color-scheme: light)").matches) {{
    document.querySelectorAll(".chart-card .shot[data-light]").forEach(function (shot) {{
      shot.setAttribute("href", shot.getAttribute("data-light"));
    }});
  }}
  var current = null;   // the card whose picture the viewer is showing
  function closeViewer() {{
    viewer.hidden = true; viewerImg.src = ""; current = null;
    document.body.style.overflow = "";
  }}
  // The viewer's own Helpful and Share are PROXIES for the card's buttons:
  // they click them and mirror their state, so there is one like path, one
  // share path, one localStorage key and one row shape, not two of each.
  function mirror() {{
    if (!current) return;
    var like = current.querySelector(".reactions .like");
    var vlike = viewer.querySelector(".vlike");
    vlike.classList.toggle("on", like.disabled); vlike.disabled = like.disabled;
  }}
  if (viewer) {{
    document.querySelectorAll(".chart-card .shot").forEach(function (shot) {{
      shot.addEventListener("click", function (e) {{
        if (!phone.matches) return;
        e.preventDefault();
        current = shot.closest(".chart-card");
        var light = shot.getAttribute("data-light");
        viewerImg.src = (light && window.matchMedia("(prefers-color-scheme: light)").matches)
          ? light : shot.getAttribute("href");
        viewerImg.alt = shot.getAttribute("aria-label") || "";
        // OPENS FITTED TO THE SCREEN. It opened at twice the width, and a
        // phone reader had no visible way back out: pinching zooms the page
        // under a fixed overlay. Fit first; a tap on the picture toggles.
        viewer.classList.add("fit");
        viewer.hidden = false; viewer.scrollTop = 0; viewer.scrollLeft = 0;
        document.body.style.overflow = "hidden";
        mirror();
      }});
    }});
    viewer.querySelector(".vlike").addEventListener("click", function () {{
      if (!current) return;
      current.querySelector(".reactions .like").click();
      setTimeout(mirror, 0);
    }});
    viewer.querySelector(".vshare").addEventListener("click", function () {{
      if (current) current.querySelector(".reactions .share").click();
    }});
    viewer.querySelector(".close").addEventListener("click", closeViewer);
    // A tap on the picture toggles fit and double width; no button for it.
    viewerImg.addEventListener("click", function () {{
      viewer.classList.toggle("fit"); viewer.scrollLeft = 0;
    }});
    document.addEventListener("keydown", function (e) {{
      if (e.key === "Escape" && !viewer.hidden) closeViewer();
    }});
  }}
}})();
</script>
</body>
</html>
'''


def build_guides_index_html(posts, logo_svg, favicon) -> str:
    # ALL OR NOTHING. A grid where some cards carry a photograph and others do
    # not is not a grid with a few pictures missing, it is a broken-looking
    # page: the picture cards stand taller and the plain ones read as failed
    # images. Rendered once with two of four heroes present, and it looked
    # exactly like that. So the pictures appear only when every guide has one,
    # which also means this becomes the picture-led version by itself the
    # moment the last hero is generated, with no code change.
    use_images = all(card_image_for(p) for p in posts)
    cards = []
    for p in posts:
        image = card_image_for(p)
        figure = (f'    <figure><img src="/app/static/{image}" alt="" '
                  f'loading="lazy" width="1600" height="900"></figure>\n'
                  if use_images and image else "")
        cards.append(
            f'''  <a class="post-card" href="/guides/{p["slug"]}">
{figure}    <p class="meta"><time datetime="{p["date"]}">{card_date(p["date"])}</time>
      · {read_minutes(p)} min read</p>
    <b>{_attr(p["title"])}</b>
    <span class="sum">{_attr(p["summary"])}</span>
    <p class="more">Read the guide</p></a>''')
    cards = "\n".join(cards)
    return f'''<!doctype html>
<html lang="en">
<head>
{_page_head("Guides — worthmydegree.com",
            "Plain-English guides to the 2026 federal student loan rules, "
            "college costs, and whether a degree pays for itself.",
            "https://worthmydegree.com/guides", "feature-og-1200x630.png",
            favicon)}
</head>
<body>
<div class="wrap">
<header>
  <a class="logo" href="/welcome" aria-label="worthmydegree.com">{logo_svg}</a>
  <a class="btn hide-m" href="/?go=1&amp;from=guide">Open the calculator</a>
</header>
<section class="guides-band">
  <h1>Guides: the money side of the college decision</h1>
  <div class="accent"></div>
  <p>What a degree costs, what you can borrow, and what you pay back. Including
  the One Big Beautiful Bill Act (OBBBA), which changed federal borrowing in
  2026.</p>
</section>
<section>
  <div class="post-head">
    <h2>Latest guides</h2>
    <span>{len(posts)} guide{"" if len(posts) == 1 else "s"}</span>
  </div>
  <div class="post-grid">
{cards}
  </div>
</section>
<footer>
  <a href="/" style="color:inherit">worthmydegree.com</a> · Educational
  estimate, not financial advice.<br>
  {ORG_STATUS_LINE}
</footer>
</div>
{CARRY_QS_JS}
</body>
</html>
'''


def inject(worker_src: str, html: str) -> str:
    """Replace the LANDING constant between the markers."""
    if START_MARK not in worker_src:
        sys.exit(f"worker.js is missing {START_MARK} -- add the marker block first")
    block = (f"{START_MARK}\n"
             f"// GENERATED by infra/build_site.py -- edit that, not this.\n"
             f"const LANDING = {json.dumps(html)};\n"
             f"{END_MARK}")
    return re.sub(
        re.escape(START_MARK) + r".*?" + re.escape(END_MARK),
        lambda _match: block, worker_src, count=1, flags=re.S)


GUIDES_START = "// {{GUIDES_HTML_START}}"
GUIDES_END = "// {{GUIDES_HTML_END}}"


SCRIPT_RE = re.compile(r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>", re.S)


def script_hashes(html: str) -> list:
    """The CSP hash of every EXECUTABLE inline script on a built page.

    The edge pages carry no external script and no inline handler, so a
    Content-Security-Policy can name each script by hash and refuse anything
    else -- which is what makes the reaction endpoints hard to drive from an
    injected tag. The hashes have to be computed HERE, at build time, because
    the Worker serves the page as a constant and cannot hash it per request
    without paying for it on every hit. JSON-LD blocks are data, not scripts;
    browsers neither execute them nor check them against script-src, so they
    are left out rather than hashed.

    The hash is of the element's text content exactly as served, byte for
    byte, which is why check_content.py recomputes it from the built pages and
    compares: a script edited without a rebuild would be blocked by the
    browser with no failing guard, and the Helpful button would just stop.
    """
    import base64
    import hashlib
    out = []
    for m in SCRIPT_RE.finditer(html):
        if "ld+json" in m.group("attrs"):
            continue
        digest = hashlib.sha256(m.group("body").encode("utf-8")).digest()
        out.append("sha256-" + base64.b64encode(digest).decode("ascii"))
    return out


def inject_guides(worker_src: str, pages: dict, chart_slugs=(), script_hashes=None) -> str:
    """The guide pages as one path -> HTML map, injected like LANDING.

    A map rather than a page each: the Worker looks the request up, so adding
    a post never touches routing code. Same reason LANDING is a constant and
    not an import -- the documented fallback deploy is pasting worker.js into
    the Cloudflare dashboard, which an import would break.

    CHART_SLUGS rides in the same block rather than earning its own marker
    pair. The charts are ONE page, so unlike a guide their slugs cannot be
    recovered from the keys of this map -- and knownSlug has to be able to
    reject an arbitrary slug, because /api/like is public and its argument
    lands in usage_logs.action. Same generated block, same regeneration, one
    fewer marker to keep in step.

    CSP_SCRIPT_HASHES rides here too: path -> the hashes of that page's inline
    scripts, which the Worker puts in the page's Content-Security-Policy. The
    landing is keyed "/" whichever of its two paths served it.
    """
    if GUIDES_START not in worker_src:
        sys.exit(f"worker.js is missing {GUIDES_START} -- add the marker block first")
    block = (f"{GUIDES_START}\n"
             f"// GENERATED by infra/build_site.py -- edit that, not this.\n"
             f"const GUIDES = {json.dumps(pages)};\n"
             f"const CHART_SLUGS = {json.dumps(sorted(chart_slugs))};\n"
             f"const CSP_SCRIPT_HASHES = {json.dumps(script_hashes or {}, sort_keys=True)};\n"
             f"{GUIDES_END}")
    return re.sub(re.escape(GUIDES_START) + r".*?" + re.escape(GUIDES_END),
                  lambda _m: block, worker_src, count=1, flags=re.S)


LLMS_PATH = ROOT / "infra" / "llms.txt"
LLMS_START = "// {{LLMS_START}}"
LLMS_END = "// {{LLMS_END}}"
LLMS_GUIDES_HEADING = "## Guides"


def llms_guides_section(posts: list) -> str:
    """The `## Guides` section of llms.txt, one entry per post.

    It used to name the INDEX and nothing else, so an agent asked what this
    site says about Parent PLUS had one URL to fetch and a list of card titles
    to guess from. llms.txt exists to save exactly that hop: the whole point is
    that the useful URLs are enumerated, with enough description to choose
    between them without fetching each one.

    The description is the post's own `description` -- the same string the
    <title>'s meta description and the JSON-LD carry, so an agent and a search
    engine are told the same thing about the same page.
    """
    lines = [LLMS_GUIDES_HEADING, "",
             "Plain-English explainers on the 2026 federal loan rules and what a",
             "degree costs, indexed at https://worthmydegree.com/guides. Each one",
             "ends at the calculator.", ""]
    for post in posts:
        url = f"https://worthmydegree.com/guides/{post['slug']}"
        # The link stays on ONE line however long it runs. Wrapping a URL
        # across a newline breaks it for anything reading this as text, which
        # is every consumer of this file.
        lines.append(f"- [{post['title']}]({url}):")
        lines += textwrap.wrap(post["description"], width=70,
                               initial_indent="  ", subsequent_indent="  ")
    return "\n".join(lines) + "\n"


LLMS_GUIDES_HEADING = "## Guides"
LLMS_CHARTS_HEADING = "## Infographics"


def llms_charts_section(charts: list) -> str:
    """The `## Infographics` section, one entry per chart.

    Enumerated rather than pointed at, for the reason llms_guides_section
    records: an index URL plus a list of card titles makes an agent fetch the
    page to find out what is on it, which is the hop this file exists to save.

    The URL carries the #slug fragment because the gallery is ONE page. That
    fragment is the only way to address a single infographic, and it is the
    same id build_charts_index_html puts on the card, so a link here scrolls to
    the picture it names.
    """
    lines = [LLMS_CHARTS_HEADING, "",
             "Free single-picture charts built from the same federal data as the",
             "calculator, indexed at https://worthmydegree.com/charts. Reusable",
             "anywhere with attribution. One page, so each is addressed by fragment.",
             ""]
    for c in charts:
        url = f"https://worthmydegree.com/charts#{c['slug']}"
        # One line however long, the same rule the guide links follow: a URL
        # wrapped across a newline is broken for anything reading this as text.
        lines.append(f"- [{c['title']}]({url}):")
        lines += textwrap.wrap(c["description"], width=70,
                               initial_indent="  ", subsequent_indent="  ")
    return "\n".join(lines) + "\n"


def render_llms(posts: list, charts: list) -> str:
    """infra/llms.txt with its guide list regenerated. The file is the source;
    the Worker's LLMS constant is built from this by inject_llms().

    The section is bounded by its own heading and the NEXT `## ` heading (or
    end of file), which is a closing boundary in the sense inject_sitemap's
    docstring demands: it cannot run past the following section the way a
    single-marker DOTALL sweep once ate three static URLs. A rename of the
    heading makes the substitution match nothing, so the count is checked --
    silently leaving a stale list is the failure this whole change is fixing.
    """
    text = LLMS_PATH.read_text()
    for heading, body in ((LLMS_GUIDES_HEADING, llms_guides_section(posts)),
                          (LLMS_CHARTS_HEADING, llms_charts_section(charts))):
        # The trailing newline is load-bearing. The match ends immediately
        # before the "\n## " of the NEXT heading, so a replacement that ends
        # flush leaves the following heading glued to this section's last line.
        # It went unnoticed while Guides was the final section and matched \Z;
        # the tail is re-stripped on return, so this cannot leave a blank end.
        text, n = re.subn(r"^" + re.escape(heading) + r"\n.*?(?=\n## |\Z)",
                          lambda _m, b=body: b.rstrip("\n") + "\n",
                          text, count=1, flags=re.S | re.M)
        if n != 1:
            sys.exit(f"infra/llms.txt: no {heading!r} section to regenerate "
                     f"-- restore the heading or update this builder")
    return text.rstrip("\n") + "\n"


def inject_llms(worker_src: str, text: str) -> str:
    """The llms.txt body into the Worker, as a JSON string rather than the
    backtick template literal it used to be.

    That swap is the point. A template literal makes every backtick and every
    `${` in the prose live syntax, so a future guide title containing either
    would break the Worker at parse time -- the whole site down, from a
    punctuation mark in a headline. json.dumps has no such characters, which is
    why LANDING and GUIDES are already written this way.

    It also retires the "change both halves in the same PR" instruction on this
    file: infra/llms.txt is now the only copy anyone edits.
    """
    if LLMS_START not in worker_src:
        sys.exit(f"worker.js is missing {LLMS_START} -- add the marker block first")
    block = (f"{LLMS_START}\n"
             f"// GENERATED by infra/build_site.py from infra/llms.txt.\n"
             f"const LLMS = {json.dumps(text)};\n"
             f"{LLMS_END}")
    return re.sub(re.escape(LLMS_START) + r".*?" + re.escape(LLMS_END),
                  lambda _m: block, worker_src, count=1, flags=re.S)


LASTMOD_PATH = ROOT / "content" / "lastmod.json"


def resolve_lastmod(posts: list, manifest: dict, today: str):
    """`{slug: lastmod}` for the sitemap and the JSON-LD, plus the manifest to
    persist. Pure: the caller does the reading and the writing.

    WHY THIS IS NOT THE FRONT-MATTER DATE. It was, and that field is the
    PUBLISH date -- an author who fixes a wrong figure in a published guide has
    no reason to touch it, so `lastmod` went on claiming the original day and
    crawlers had no signal to come back for the correction. The failure is
    silent in the direction that matters: the sitemap stays valid, the page
    stays served, and only the re-crawl never happens.

    WHY A HASH AND NOT THE FILE MTIME. git does not preserve mtimes, so a fresh
    clone stamps every post with the checkout time and the next build would
    announce that all of them changed today. The hash is of the MARKDOWN BODY
    only, so a CSS edit, a template change or a re-run does not move a date --
    which is what keeps the build byte-identical across runs, the property that
    makes a size change meaningful rather than noise.

    An unknown slug takes its publish date rather than today, so importing an
    older post does not backdate-then-bump it on the following build.
    """
    out, man, changed = {}, dict(manifest), []
    for post in posts:
        sha = hashlib.sha1(post["body"].encode("utf-8")).hexdigest()
        rec = manifest.get(post["slug"])
        if rec is None:
            lastmod = post["date"]          # first time we have seen it
        elif rec.get("sha") == sha:
            lastmod = rec.get("lastmod", post["date"])   # body unchanged
        else:
            lastmod = today                # body changed, and we know when
            changed.append(post["slug"])
        out[post["slug"]] = lastmod
        man[post["slug"]] = {"sha": sha, "lastmod": lastmod}
    # A deleted post leaves the manifest, deliberately: restoring it should
    # restore its history rather than read as brand new.
    #
    # `changed` is returned rather than derived by the caller from the dates.
    # Deriving it was the first version and it lied on the day a post was
    # published: lastmod equals today because the PUBLISH date is today, and
    # the build announced "body changed" for a post it had never seen before.
    return out, man, changed


SITEMAP_START = "<!--GUIDES-->"
SITEMAP_END = "<!--/GUIDES-->"


def inject_sitemap(text: str, posts: list, lastmod: dict = None,
                   charts: list = ()) -> str:
    """Guide URLs into both sitemap halves, replacing whatever was there.

    Regenerated from the posts every build, so deleting a post removes its URL
    -- a sitemap that lists a 301 is worse than one that lists nothing.

    PAIRED markers, at the END of the urlset. The first version used one
    marker and a `(<url>...</url>)*` sweep after it, which with DOTALL ate the
    three static tool URLs that happened to follow it -- a silent deletion
    that left a valid, shorter sitemap. An explicit closing marker cannot
    over-reach, and nothing static sits between the two.
    """
    entries = ["  <url>\n    <loc>https://worthmydegree.com/guides</loc>\n"
               "    <changefreq>weekly</changefreq>\n"
               "    <priority>0.7</priority>\n  </url>"]
    # The gallery sits inside the generated block rather than beside the static
    # tool URLs, because like the guides it exists only when its content does:
    # no charts, no page, and a sitemap listing a 301 is worse than one listing
    # nothing. Weekly like the guides index, since both change when their
    # contents do.
    if charts:
        entries.append("  <url>\n    <loc>https://worthmydegree.com/charts</loc>\n"
                       "    <changefreq>weekly</changefreq>\n"
                       "    <priority>0.6</priority>\n  </url>")
    lastmod = lastmod or {}
    entries += [f"  <url>\n    <loc>https://worthmydegree.com/guides/{p['slug']}</loc>\n"
                f"    <lastmod>{lastmod.get(p['slug'], p['date'])}</lastmod>\n"
                f"    <changefreq>monthly</changefreq>\n"
                f"    <priority>0.6</priority>\n  </url>" for p in posts]
    inner = ("\n" + "\n".join(entries)) if (posts or charts) else ""
    block = f"{SITEMAP_START}{inner}\n  {SITEMAP_END}"
    return re.sub(re.escape(SITEMAP_START) + r".*?" + re.escape(SITEMAP_END),
                  lambda _m: block, text, count=1, flags=re.S)


def render_all():
    """Every page, built in memory and returned. No writes.

    main() and preview() both go through here, so a preview cannot drift from
    what deploys. A second renderer for previewing would be the chart-twin trap
    from CLAUDE.md wearing different clothes: two implementations of one output,
    diverging quietly, with the preview being the one nobody checks against
    production.
    """
    facts = app_facts()
    print("facts:", {k: v for k, v in facts.items() if k not in ("cap_rows", "cap_total")})
    posts = load_posts()
    print(f"posts: {len(posts)} -> {[p['slug'] for p in posts]}")

    logo_svg = (ROOT / "brand/logo-horizontal-light.svg").read_text()
    logo_svg = re.sub(r'width="\d+" height="\d+"', "", logo_svg, count=1)
    favicon = base64.b64encode(
        (ROOT / "brand/favicon-light.svg").read_bytes()).decode()

    manifest = (json.loads(LASTMOD_PATH.read_text())
                if LASTMOD_PATH.exists() else {})
    today = datetime.date.today().isoformat()
    lastmod, manifest, changed = resolve_lastmod(posts, manifest, today)
    if changed:
        print(f"  lastmod -> {today} (body changed): {', '.join(changed)}")

    # Loaded BEFORE build_html, which now needs it for the landing's
    # infographic cards. Still exactly one load_charts() call in this function,
    # which the note below the pages map asks for -- it is not a pure read, it
    # writes the JPEGs, so extra calls were real work rather than just clutter.
    charts = load_charts()
    html = build_html(facts, posts, charts)
    pages = {}
    if posts:
        pages["/guides"] = build_guides_index_html(posts, logo_svg, favicon)
        for post in posts:
            pages[f"/guides/{post['slug']}"] = build_guide_html(
                post, logo_svg, favicon, lastmod.get(post["slug"]))
    # The chart gallery rides the same pages map, which is what routes it: the
    # Worker serves anything in that map and 301s everything else to "/", so a
    # page absent from here is not merely unlinked, it is unreachable.
    if charts:
        pages["/charts"] = build_charts_index_html(charts, logo_svg, favicon)
    return html, pages, posts, lastmod, manifest


def check_worker_syntax(worker_src: str, allow_no_node: bool = False) -> None:
    """Refuse to write a worker.js that is not JavaScript.

    Four constants are injected by regex into a file that also CONTAINS the
    XML, markdown and HTML it serves, so a marker string can appear somewhere
    it was never meant to be a marker. That is not hypothetical: a comment in
    worker.js that mentioned `<!--` + `GUIDES` + `-->` in prose became the
    first match for inject_sitemap, which then replaced everything from the
    comment to the real closing marker with sitemap XML. The file was no longer
    parseable, and every check in this repo still passed -- they read the
    generated PAGES, and the pages were fine. Only `node --check` saw it.

    A missing node used to skip this with a warning, on the reasoning that a
    missing toolchain must cost a check and not the build. But the check is
    the only thing standing between a marker collision and a Worker that does
    not parse, and a warning on a build that then succeeds is read as success.
    So it is a failure now, with --allow-no-node for a machine that genuinely
    has no node and is not the one deploying: `npx wrangler deploy` needs node
    anyway, so anyone in a position to ship this has it.
    """
    import shutil as _shutil
    import subprocess
    import tempfile

    if not _shutil.which("node"):
        if allow_no_node:
            print("  WARNING: node not found, worker.js syntax NOT verified "
                  "(--allow-no-node)")
            return
        raise SystemExit("  FAILED: node is not installed, so the generated "
                         "worker.js cannot be syntax-checked and was NOT "
                         "written. Install node, or pass --allow-no-node on a "
                         "machine that is not deploying.")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(worker_src)
        tmp = fh.name
    try:
        proc = subprocess.run(["node", "--check", tmp],
                              capture_output=True, text=True)
    finally:
        Path(tmp).unlink(missing_ok=True)
    if proc.returncode != 0:
        raise SystemExit("  FAILED: the generated worker.js is not valid "
                         "JavaScript, so it was NOT written:\n"
                         + proc.stderr.strip())


def missing_static(html: str, pages: dict) -> list:
    """Every /app/static/<file> a built page references that is not on disk.

    Cheap, and it catches the one failure a preview would otherwise still let
    through to production: an image whose path is right in the markdown and
    whose file was never copied into static/. The page renders, the alt text
    shows, and nothing errors.
    """
    refs = set()
    for doc in (html, *pages.values()):
        refs |= set(re.findall(r'/app/static/([^"\')\s?]+)', doc))
    return sorted(r for r in refs if not (ROOT / "static" / r).exists())


def main(allow_no_node: bool = False):
    html, pages, posts, lastmod, manifest = render_all()

    gone = missing_static(html, pages)
    if gone:
        raise SystemExit("  FAILED: referenced but absent from static/: "
                         + ", ".join(gone))

    OUT.write_text(html)
    print(f"  wrote infra/landing.html  ({len(html):,} bytes)")

    guide_dir = ROOT / "infra" / "guides"
    guide_dir.mkdir(exist_ok=True)
    for path, page in pages.items():
        name = (path.rsplit("/", 1)[-1] or "index") + ".html"
        (guide_dir / name).write_text(page)
        print(f"  wrote infra/guides/{name}  ({len(page):,} bytes)")

    # Read ONCE and reused. render_all() builds its own list and does not
    # return it, and this ran load_charts() twice more below -- three reads of
    # the same directory, any of which a future edit could let drift apart.
    charts = load_charts()

    llms = render_llms(posts, charts)
    LLMS_PATH.write_text(llms)

    worker = inject(WORKER.read_text(), html)
    hashes = {"/": script_hashes(html),
              **{path: script_hashes(page) for path, page in pages.items()}}
    worker = inject_guides(worker, pages, [c["slug"] for c in charts], hashes)
    worker = inject_sitemap(worker, posts, lastmod, charts)
    worker = inject_llms(worker, llms)
    check_worker_syntax(worker, allow_no_node)
    WORKER.write_text(worker)
    print("  injected LANDING, GUIDES and LLMS into infra/worker.js")
    print(f"  llms.txt: {len(posts)} guide and {len(charts)} infographic "
          f"URL(s), {len(llms):,} bytes")

    # Written HERE and not in render_all(), so --preview stays a read. A
    # preview that recorded a hash would mark the post as seen without ever
    # publishing it, and the build that followed would find nothing changed.
    LASTMOD_PATH.parent.mkdir(exist_ok=True)
    LASTMOD_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    # The reference copy beside the Worker, kept in step by the same call --
    # the two halves used to be a "change both in one PR" comment, and a
    # generated section is one less thing asking a human to remember.
    ref = ROOT / "infra" / "sitemap.xml"
    ref.write_text(inject_sitemap(ref.read_text(), posts, lastmod, charts))
    print(f"  sitemap: {len(posts) + 1} guide URL(s) in both halves")


def preview(port: int = 8787):
    """Serve the site from memory, writing NOTHING.

    Exists because building was the only way to see a guide, and building also
    injects LANDING and GUIDES into infra/worker.js. That made "let me look at
    it" and "arm the next deploy" the same command: anyone who ran
    `npx wrangler deploy` afterwards shipped whatever the last person had been
    previewing. This path touches no file, so it cannot do that.

    Routing mirrors worker.js for the paths a preview needs, which is the point.
    In particular /app/static/* is served from the real static/ directory,
    because guide images resolve there in production; without it every image in
    a guide is broken here and the preview teaches you nothing about the one
    thing you most wanted to look at.

    NOT a substitute for the real thing. It does not run the edge logic, the
    canonical headers, the 503 page, the redirects or the landing logger, and
    localhost is not the Fireglass-proxied network the top of CLAUDE.md warns
    about. It shows you the PAGES.
    """
    import http.server
    import mimetypes
    import urllib.parse

    html, pages, posts, _lastmod, _manifest = render_all()
    gone = missing_static(html, pages)

    routes = {"/": html}
    for path, page in pages.items():
        routes[path] = page
        routes[path + "/"] = page

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):            # quiet; the summary below is enough
            pass

        def _send(self, body: bytes, ctype: str):
            self.send_response(200)
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(body)))
            self.send_header("cache-control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path in ("/welcome", "/welcome/"):
                path = "/"                     # the alias worker.js serves
            if path in routes:
                return self._send(routes[path].encode(), "text/html; charset=utf-8")
            if path.startswith("/app/static/"):
                target = ROOT / "static" / path[len("/app/static/"):]
                if target.is_file():
                    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                    return self._send(target.read_bytes(), ctype)
            self.send_error(404, f"not in the preview: {path}")

    # flush=True on every line: stdout block-buffers when redirected, so a
    # backgrounded preview printed its URL into a buffer that never drained
    # while serve_forever held the process. The one thing this command exists
    # to tell you was the one thing you could not see.
    say = lambda s="": print(s, flush=True)
    say()
    if gone:
        say("  WARNING: referenced but absent from static/ "
            f"({len(gone)}): {', '.join(gone)}")
    say(f"  preview on http://localhost:{port}  (nothing written to disk)")
    say("    /            the landing page")
    for path in pages:
        say(f"    {path:<13}{'the guides index' if path == '/guides' else ''}")
    say("  Ctrl-C to stop. Re-run to pick up edits.")
    try:
        http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")


if __name__ == "__main__":
    args = sys.argv[1:]
    allow_no_node = "--allow-no-node" in args
    args = [a for a in args if a != "--allow-no-node"]
    if "--preview" in args:
        rest = [a for a in args if a != "--preview"]
        preview(int(rest[0]) if rest else 8787)
    else:
        main(allow_no_node)

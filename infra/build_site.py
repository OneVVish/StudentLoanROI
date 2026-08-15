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
    if (a.getAttribute("href").indexOf("llms.txt") !== -1) return;
    a.href += (a.href.indexOf("?") === -1 ? "?" : "&") + qs;
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
     --tile: left alone they would dissolve into the band they sit on. */
  .tools { background: var(--tile); margin-top: 40px; padding: 48px 0; }
  .tools .tile { background: var(--surface); border: 1px solid var(--rule); }
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
  .guides { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }
  .guide-card { background: var(--tile); border-radius: 12px; padding: 20px;
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
    .guides { grid-template-columns: 1fr; }
    .hide-m { display: none; }
    .table-scroll { overflow-x: auto; }
  }"""


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


def _inline(text: str) -> str:
    """Inline markdown -> HTML. Escapes first, so post text can never inject
    markup -- these files are ours, but a renderer that trusts its input is one
    copy-paste away from being a hole."""
    out = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    out = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)",
                 lambda m: f'<img src="/app/static/{m.group(2)}" alt="{m.group(1)}" loading="lazy">',
                 out)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', out)
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
        elif line.startswith("> "):
            quote, i = [], i
            while i < len(lines) and lines[i].startswith("> "):
                quote.append(lines[i][2:]); i += 1
            html.append(f"<blockquote>{_inline(' '.join(quote))}</blockquote>")
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


def build_html(f: dict, posts: list = ()) -> str:
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
      <b>{p["title"]}</b><span>{p["summary"]}</span>
      <time datetime="{p["date"]}">{p["date"]}</time></a>''' for p in posts[:4])
        guides_section = f'''<section>
  <h2>Guides</h2>
  <div class="guides">
{cards}
  </div>
  <p class="deck" style="margin-top:14px"><a href="/guides"
    style="color:var(--blue);font-weight:600;text-decoration:none">All
    guides&nbsp;→</a></p>
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
<meta property="og:image" content="https://worthmydegree.com/app/static/feature-og-1200x630.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="https://worthmydegree.com/app/static/feature-og-1200x630.png">
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
  <a class="logo" href="/" aria-label="worthmydegree.com">{logo_svg}</a>
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
  <div class="stat"><b>{f['schools']:,}</b><span>real schools, published costs</span></div>
  <div class="stat"><b>{f['careers']:,}</b><span>careers with federal wage data</span></div>
  <div class="stat"><b>{f['majors']}</b><span>majors, NY Fed outcomes</span></div>
  <div class="stat"><b>{f['cities']}</b><span>metro areas, local pay &amp; prices</span></div>
</div>

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
      from federal data, residency years modelled.</p></div>
    <div class="tile"><b>📄 Take it with you</b>
      <p>Every result exports as a PDF report or a shareable image, sourced
      down to the footnote.</p></div>
  </div>
</section>

</div>

<!-- The tools band breaks out of .wrap so its background can run edge to
     edge; it carries its own .wrap so the content stays on the same grid as
     every section above and below it. See the tools-band note in SITE_CSS. -->
<section class="tools">
  <div class="wrap">
  <h2>Four tools, one dataset</h2>
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
    return ('<script type="application/ld+json">'
            + json.dumps(data, ensure_ascii=False) + "</script>")


def _page_head(title, description, canonical, image, favicon, jsonld=""):
    """One <head> for every page on the edge site, so a guide and the landing
    carry the same card, the same icon and the same theme handling."""
    return f'''<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{canonical}">
<meta property="og:type" content="article">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="https://worthmydegree.com/app/static/{image}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="https://worthmydegree.com/app/static/{image}">
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
  <a class="logo" href="/" aria-label="worthmydegree.com">{logo_svg}</a>
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
      (n === 1 ? "1 person found this helpful" : n + " people found this helpful");
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
      navigator.share({{title: title, url: url}}).then(record, function (err) {{
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
    <b>{p["title"]}</b>
    <span class="sum">{p["summary"]}</span>
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
  <a class="logo" href="/" aria-label="worthmydegree.com">{logo_svg}</a>
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
  estimate, not financial advice.
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


def inject_guides(worker_src: str, pages: dict) -> str:
    """The guide pages as one path -> HTML map, injected like LANDING.

    A map rather than a page each: the Worker looks the request up, so adding
    a post never touches routing code. Same reason LANDING is a constant and
    not an import -- the documented fallback deploy is pasting worker.js into
    the Cloudflare dashboard, which an import would break.
    """
    if GUIDES_START not in worker_src:
        sys.exit(f"worker.js is missing {GUIDES_START} -- add the marker block first")
    block = (f"{GUIDES_START}\n"
             f"// GENERATED by infra/build_site.py -- edit that, not this.\n"
             f"const GUIDES = {json.dumps(pages)};\n"
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


def render_llms(posts: list) -> str:
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
    new, n = re.subn(r"^" + re.escape(LLMS_GUIDES_HEADING) + r"\n.*?(?=\n## |\Z)",
                     lambda _m: llms_guides_section(posts).rstrip("\n"),
                     text, count=1, flags=re.S | re.M)
    if n != 1:
        sys.exit(f"infra/llms.txt: no {LLMS_GUIDES_HEADING!r} section to "
                 f"regenerate -- restore the heading or update this builder")
    return new.rstrip("\n") + "\n"


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


def inject_sitemap(text: str, posts: list, lastmod: dict = None) -> str:
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
    lastmod = lastmod or {}
    entries += [f"  <url>\n    <loc>https://worthmydegree.com/guides/{p['slug']}</loc>\n"
                f"    <lastmod>{lastmod.get(p['slug'], p['date'])}</lastmod>\n"
                f"    <changefreq>monthly</changefreq>\n"
                f"    <priority>0.6</priority>\n  </url>" for p in posts]
    inner = ("\n" + "\n".join(entries)) if posts else ""
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

    html = build_html(facts, posts)
    pages = {}
    if posts:
        pages["/guides"] = build_guides_index_html(posts, logo_svg, favicon)
        for post in posts:
            pages[f"/guides/{post['slug']}"] = build_guide_html(
                post, logo_svg, favicon, lastmod.get(post["slug"]))
    return html, pages, posts, lastmod, manifest


def check_worker_syntax(worker_src: str) -> None:
    """Refuse to write a worker.js that is not JavaScript.

    Four constants are injected by regex into a file that also CONTAINS the
    XML, markdown and HTML it serves, so a marker string can appear somewhere
    it was never meant to be a marker. That is not hypothetical: a comment in
    worker.js that mentioned `<!--` + `GUIDES` + `-->` in prose became the
    first match for inject_sitemap, which then replaced everything from the
    comment to the real closing marker with sitemap XML. The file was no longer
    parseable, and every check in this repo still passed -- they read the
    generated PAGES, and the pages were fine. Only `node --check` saw it.

    Skipped, with a warning, when node is absent: a missing toolchain must cost
    a check and not the build. Deploying is `npx wrangler deploy`, so anyone in
    a position to ship this has node.
    """
    import shutil as _shutil
    import subprocess
    import tempfile

    if not _shutil.which("node"):
        print("  WARNING: node not found, worker.js syntax NOT verified")
        return
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
        refs |= set(re.findall(r'/app/static/([^"\')\s]+)', doc))
    return sorted(r for r in refs if not (ROOT / "static" / r).exists())


def main():
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

    llms = render_llms(posts)
    LLMS_PATH.write_text(llms)

    worker = inject(WORKER.read_text(), html)
    worker = inject_guides(worker, pages)
    worker = inject_sitemap(worker, posts, lastmod)
    worker = inject_llms(worker, llms)
    check_worker_syntax(worker)
    WORKER.write_text(worker)
    print("  injected LANDING, GUIDES and LLMS into infra/worker.js")
    print(f"  llms.txt: {len(posts)} guide URL(s), {len(llms):,} bytes")

    # Written HERE and not in render_all(), so --preview stays a read. A
    # preview that recorded a hash would mark the post as seen without ever
    # publishing it, and the build that followed would find nothing changed.
    LASTMOD_PATH.parent.mkdir(exist_ok=True)
    LASTMOD_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    # The reference copy beside the Worker, kept in step by the same call --
    # the two halves used to be a "change both in one PR" comment, and a
    # generated section is one less thing asking a human to remember.
    ref = ROOT / "infra" / "sitemap.xml"
    ref.write_text(inject_sitemap(ref.read_text(), posts, lastmod))
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
    if "--preview" in args:
        rest = [a for a in args if a != "--preview"]
        preview(int(rest[0]) if rest else 8787)
    else:
        main()

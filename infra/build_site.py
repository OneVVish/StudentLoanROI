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
import json
import re
import sys
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
                items.append(f"<li>{_inline(lines[i][2:])}</li>"); i += 1
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

<section>
  <h2>Know before you borrow</h2>
  <p class="deck">Starting college in {f['effective_year']} or later, federal
  borrowing for a dependent undergraduate has hard ceilings. This is the whole
  table:</p>
  <div class="table-scroll">
  <table>
    <thead><tr><th>College year</th><th>Student Direct loan</th>
      <th>Parent PLUS max</th><th>Maximum combined</th></tr></thead>
    <tbody>
{cap_body}
    </tbody>
    <tfoot><tr><td>4-year total</td><td>{money(d_tot)}</td>
      <td>{money(p_tot)}</td><td>{money(c_tot)}</td></tr></tfoot>
  </table>
  </div>
  <p class="note">*Parent PLUS is capped at {money(f['plus_aggregate'])} in
  total per dependent student: borrow {money(f['plus_annual'])} in each of the
  first three years and only {money(leftover)} remains for senior year. These
  are ceilings, not offers; anything a school costs beyond them is private
  borrowing.</p>
  <div class="callout">Planning around PLUS? The steady number is
  {money(f['plus_aggregate'] / 4)} a year, not {money(f['plus_annual'])}.
  The calculator applies these caps automatically to any school you pick.</div>
</section>

{guides_section}

<section class="tools">
  <h2>Three tools, one dataset</h2>
  <div class="grid">
    <div class="tile"><b>🎓 The calculator</b>
      <p>School + major + loan → the 10-year verdict.
      <a href="/?go=1&amp;from=welcome">Open&nbsp;→</a></p></div>
    <div class="tile"><b>🔎 Schools that fit a budget</b>
      <p>Every school teaching your field, cheapest first, priced for your
      state. <a href="/?tool=schools&amp;from=welcome">Search&nbsp;→</a></p></div>
    <div class="tile"><b>💸 Already have loans?</b>
      <p>Compare the 2026 repayment plans on the balance you already owe.
      <a href="/?tool=repayment&amp;from=welcome">Compare&nbsp;→</a></p></div>
  </div>
</section>

<div class="cta">
  <h2>Two minutes. Zero forms.</h2>
  <p class="deck" style="margin:8px auto 22px">That is all it takes to see where
  you would stand ten years out on the most expensive decision most families
  ever finance.</p>
  <a class="btn big" href="/?go=1&amp;from=welcome">Open the calculator</a>
</div>

<footer>
  Built from Bureau of Labor Statistics, New York Fed, College Scorecard,
  IPEDS and CPS ASEC data, every figure traceable to its source in the app's
  Methodology section. A student research project. Educational estimate, not
  financial advice.<br>
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
  article blockquote { border-left: 4px solid var(--orange);
    background: var(--tint); border-radius: 0 10px 10px 0;
    padding: 14px 18px; margin: 20px 0; font-size: 18px; }
  article hr { border: 0; border-top: 1px solid var(--rule); margin: 30px 0; }
  article table { width: 100%; border-collapse: collapse; margin: 18px 0; }
  article thead th { background: var(--deep); color: #fff; padding: 9px 10px;
    text-align: left; font-size: 15px; }
  article tbody td { padding: 9px 10px; border-bottom: 1px solid var(--rule);
    font-variant-numeric: tabular-nums; }
  .likes { display: flex; align-items: center; gap: 12px; margin: 34px 0 8px;
    padding: 16px 0; border-top: 1px solid var(--rule);
    border-bottom: 1px solid var(--rule); }
  .likes button { font: inherit; font-weight: 700; cursor: pointer;
    background: var(--surface); color: var(--deep);
    border: 2px solid var(--deep); border-radius: 99px; padding: 8px 20px; }
  .likes button[disabled] { background: var(--tint); border-color: var(--orange);
    color: var(--orange); cursor: default; }
  .likes button:focus-visible { outline: 3px solid var(--blue);
    outline-offset: 2px; }
  .likes .count { color: var(--muted); font-size: 15px; }
"""


def _page_head(title, description, canonical, image, favicon):
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
<style>
{SITE_CSS}
{ARTICLE_CSS}
</style>'''


def build_guide_html(post, logo_svg, favicon) -> str:
    """One article page. Same shell as the landing, plus the like control.

    The like button is progressive: it is a plain button that works the moment
    the page paints, and the count fills in afterwards from our OWN origin. If
    the count request fails the article is unaffected -- which is the property
    the whole edge site is built around, so a reaction counter must not be the
    thing that breaks it.
    """
    body = render_markdown(post["body"])
    canonical = f"https://worthmydegree.com/guides/{post['slug']}"
    return f'''<!doctype html>
<html lang="en">
<head>
{_page_head(post["title"] + " — worthmydegree.com", post["description"],
            canonical, post.get("image", "feature-og-1200x630.png"), favicon)}
</head>
<body>
<div class="wrap">
<header>
  <a class="logo" href="/" aria-label="worthmydegree.com">{logo_svg}</a>
  <a class="btn hide-m" href="/?go=1&amp;from=guide">Open the calculator</a>
</header>

<article>
  <p class="meta"><a href="/guides" style="color:var(--blue);text-decoration:none">
    ← All guides</a></p>
  <h1>{post["title"]}</h1>
  <p class="meta"><time datetime="{post["date"]}">{post["date"]}</time>
    · worthmydegree.com</p>
{body}

  <div class="likes">
    <button id="like" data-slug="{post["slug"]}">♥ Helpful</button>
    <span class="count" id="likecount">&nbsp;</span>
  </div>

  <div class="cta" style="padding:34px 0">
    <a class="btn big" href="/?go=1&amp;from=guide">Run your own numbers, free</a>
    <div class="trust">Free · anonymous · no sign-up</div>
  </div>
</article>

<footer>
  Built from Bureau of Labor Statistics, New York Fed, College Scorecard,
  IPEDS and CPS ASEC data. A student research project. Educational estimate,
  not financial advice.<br>
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
    fetch("/api/like", {{
      method: "POST", headers: {{"content-type": "application/json"}},
      body: JSON.stringify({{slug: slug}}),
    }}).then(function (r) {{ return r.json(); }})
      .then(function (d) {{ if (typeof d.count === "number") render(d.count); }})
      .catch(function () {{}});
  }});
}})();
</script>
{CARRY_QS_JS}
</body>
</html>
'''


def build_guides_index_html(posts, logo_svg, favicon) -> str:
    cards = "\n".join(
        f'''  <a class="guide-card" href="/guides/{p["slug"]}">
    <b>{p["title"]}</b><span>{p["summary"]}</span>
    <time datetime="{p["date"]}">{p["date"]}</time></a>''' for p in posts)
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
<section>
  <h2 style="font-size:32px">Guides</h2>
  <p class="deck">The rules that decide what a degree costs, written out in
  plain English. Every figure comes from the same federal data the calculator
  runs on.</p>
  <div class="guides">
{cards}
  </div>
</section>
<footer>
  <a href="/" style="color:inherit">worthmydegree.com</a> · A student research
  project. Educational estimate, not financial advice.
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


SITEMAP_START = "<!--GUIDES-->"
SITEMAP_END = "<!--/GUIDES-->"


def inject_sitemap(text: str, posts: list) -> str:
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
    entries += [f"  <url>\n    <loc>https://worthmydegree.com/guides/{p['slug']}</loc>\n"
                f"    <lastmod>{p['date']}</lastmod>\n"
                f"    <changefreq>monthly</changefreq>\n"
                f"    <priority>0.6</priority>\n  </url>" for p in posts]
    inner = ("\n" + "\n".join(entries)) if posts else ""
    block = f"{SITEMAP_START}{inner}\n  {SITEMAP_END}"
    return re.sub(re.escape(SITEMAP_START) + r".*?" + re.escape(SITEMAP_END),
                  lambda _m: block, text, count=1, flags=re.S)


def main():
    facts = app_facts()
    print("facts:", {k: v for k, v in facts.items() if k not in ("cap_rows", "cap_total")})
    posts = load_posts()
    print(f"posts: {len(posts)} -> {[p['slug'] for p in posts]}")

    logo_svg = (ROOT / "brand/logo-horizontal-light.svg").read_text()
    logo_svg = re.sub(r'width="\d+" height="\d+"', "", logo_svg, count=1)
    favicon = base64.b64encode(
        (ROOT / "brand/favicon-light.svg").read_bytes()).decode()

    html = build_html(facts, posts)
    OUT.write_text(html)
    print(f"  wrote infra/landing.html  ({len(html):,} bytes)")

    pages = {}
    guide_dir = ROOT / "infra" / "guides"
    guide_dir.mkdir(exist_ok=True)
    if posts:
        pages["/guides"] = build_guides_index_html(posts, logo_svg, favicon)
        for post in posts:
            pages[f"/guides/{post['slug']}"] = build_guide_html(
                post, logo_svg, favicon)
        for path, page in pages.items():
            name = (path.rsplit("/", 1)[-1] or "index") + ".html"
            (guide_dir / name).write_text(page)
            print(f"  wrote infra/guides/{name}  ({len(page):,} bytes)")

    worker = inject(WORKER.read_text(), html)
    worker = inject_guides(worker, pages)
    worker = inject_sitemap(worker, posts)
    WORKER.write_text(worker)
    print("  injected LANDING and GUIDES into infra/worker.js")

    # The reference copy beside the Worker, kept in step by the same call --
    # the two halves used to be a "change both in one PR" comment, and a
    # generated section is one less thing asking a human to remember.
    ref = ROOT / "infra" / "sitemap.xml"
    ref.write_text(inject_sitemap(ref.read_text(), posts))
    print(f"  sitemap: {len(posts) + 1} guide URL(s) in both halves")


if __name__ == "__main__":
    main()

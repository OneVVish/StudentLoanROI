#!/usr/bin/env python3
"""Generate the /welcome landing page and inject it into the edge Worker.

    python3 infra/build_landing.py       # rewrites infra/landing.html AND the
                                         # LANDING constant in infra/worker.js

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


def build_html(f: dict) -> str:
    logo_svg = (ROOT / "brand/logo-horizontal-light.svg").read_text()
    # The lockup ships its own width/height; the page sizes it with CSS.
    logo_svg = re.sub(r'width="\d+" height="\d+"', "", logo_svg, count=1)
    favicon = base64.b64encode(
        (ROOT / "brand/favicon-light.svg").read_bytes()).decode()

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
  :root {{
    --deep: #12335c; --blue: #2a78d6; --orange: #eb6834;
    --ink: #14161a; --muted: #5c636d; --rule: #dfe3e8;
    --tint: #fdf2ec; --tile: #f7f8fa; --surface: #ffffff;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font: 17px/1.6 "Avenir Next", -apple-system, "Segoe UI", Roboto, sans-serif;
    color: var(--ink); background: var(--surface);
  }}
  .wrap {{ max-width: 980px; margin: 0 auto; padding: 0 24px; }}
  header {{
    display: flex; align-items: center; justify-content: space-between;
    padding: 18px 0; border-bottom: 1px solid var(--rule);
  }}
  .logo svg {{ height: 36px; width: auto; display: block; }}
  .btn {{
    display: inline-block; background: var(--orange); color: #fff;
    font-weight: 700; text-decoration: none; border-radius: 10px;
    padding: 12px 22px; font-size: 16px;
  }}
  .btn.big {{ padding: 16px 34px; font-size: 19px; }}
  .btn.ghost {{ background: transparent; color: var(--deep);
    border: 2px solid var(--deep); }}
  .hero {{ text-align: center; padding: 64px 0 40px; }}
  .hero h1 {{
    font-size: clamp(34px, 6vw, 58px); line-height: 1.08; color: var(--deep);
    font-weight: 800; letter-spacing: -0.01em; text-transform: uppercase;
  }}
  .hero .accent {{
    width: 130px; height: 3px; background: var(--orange);
    margin: 22px auto; position: relative;
  }}
  .hero p.sub {{
    font-size: 20px; color: var(--muted); max-width: 620px; margin: 0 auto 30px;
  }}
  .trust {{ margin-top: 14px; color: var(--muted); font-size: 15px; }}
  .stats {{
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px;
    padding: 26px 0 8px;
  }}
  .stat {{ background: var(--tile); border-radius: 12px; padding: 18px 10px;
    text-align: center; }}
  .stat b {{ display: block; font-size: 26px; color: var(--deep); }}
  .stat span {{ font-size: 14px; color: var(--muted); }}
  section {{ padding: 44px 0 8px; }}
  h2 {{ font-size: 28px; color: var(--deep); margin-bottom: 6px; }}
  .deck {{ color: var(--muted); margin-bottom: 24px; max-width: 640px; }}
  .grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }}
  .tile {{ background: var(--tile); border-radius: 12px; padding: 20px; }}
  .tile b {{ display: block; font-size: 17px; margin-bottom: 6px; }}
  .tile p {{ font-size: 15px; color: var(--muted); }}
  table {{ width: 100%; border-collapse: collapse; font-size: 16px;
    margin-top: 18px; }}
  thead th {{ background: var(--deep); color: #fff; font-weight: 600;
    padding: 10px 8px; }}
  tbody td, tfoot td {{ text-align: center; padding: 10px 8px;
    border-bottom: 1px solid var(--rule); }}
  tbody tr:nth-child(even) {{ background: #f6f8fa; }}
  tfoot td {{ background: var(--tint); color: var(--deep); font-weight: 700; }}
  .note {{ font-size: 14px; color: var(--muted); margin-top: 12px;
    max-width: 640px; }}
  .callout {{
    background: var(--tint); border: 2px solid var(--orange);
    border-radius: 12px; padding: 18px 22px; margin-top: 20px; font-size: 16px;
  }}
  .tools .tile a {{ color: var(--blue); font-weight: 600;
    text-decoration: none; }}
  .cta {{ text-align: center; padding: 56px 0; }}
  footer {{
    border-top: 1px solid var(--rule); margin-top: 40px; padding: 26px 0 40px;
    color: var(--muted); font-size: 14px;
  }}
  @media (max-width: 720px) {{
    .stats {{ grid-template-columns: repeat(2, 1fr); }}
    .grid {{ grid-template-columns: 1fr; }}
    .hide-m {{ display: none; }}
    .table-scroll {{ overflow-x: auto; }}
  }}
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
  payment under the 2026 federal rules, and where you stand ten years out —
  before anyone signs anything.</p>
  <a class="btn big" href="/?go=1&amp;from=welcome">Run your numbers — free</a>
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
      <p>Median borrowing at your school — or build it from cost, aid and the
      federal caps. Berkeley's sticker is {money(f['berkeley_coa'])}/yr; its
      median borrower leaves with $13,000.</p></div>
    <div class="tile"><b>💸 The payment, not just the debt</b>
      <p>The 2026 RAP income-driven plan against the fixed plans, month by
      month, including what gets waived and what gets forgiven.</p></div>
    <div class="tile"><b>🏙️ Ten years out, where you'll live</b>
      <p>Metro wages and cost of living, not a national average — the same
      salary is a different life in San Francisco and in Columbus.</p></div>
    <div class="tile"><b>🔀 Two paths, side by side</b>
      <p>UC vs CSU, nursing vs CS, or the same degree with two community-college
      years first. One page, same math on both sides.</p></div>
    <div class="tile"><b>🎓 Graduate &amp; professional</b>
      <p>Medicine, dentistry, law, the MBA and five more — priced per school
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
  total per dependent student — borrow {money(f['plus_annual'])} in each of the
  first three years and only {money(leftover)} remains for senior year. These
  are ceilings, not offers; anything a school costs beyond them is private
  borrowing.</p>
  <div class="callout">Planning around PLUS? The steady number is
  {money(f['plus_aggregate'] / 4)} a year — not {money(f['plus_annual'])}.
  The calculator applies these caps automatically to any school you pick.</div>
</section>

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
  <p class="deck" style="margin:8px auto 22px">The most expensive decision most
  families ever finance deserves ten real minutes of arithmetic.</p>
  <a class="btn big" href="/?go=1&amp;from=welcome">Open the calculator</a>
</div>

<footer>
  Built from Bureau of Labor Statistics, New York Fed, College Scorecard,
  IPEDS and CPS ASEC data — every figure traceable to its source in the app's
  Methodology section. A student research project. Educational estimate, not
  financial advice.<br>
  <a href="/llms.txt" style="color:inherit">Plain-text summary</a> ·
  <a href="/" style="color:inherit">worthmydegree.com</a>
</footer>

</div>
<script>
/* Carry the visitor's query string (?src= attribution, ?test=) onto every
   internal link, so a tagged arrival stays tagged when they click through --
   the same rule the app's own internal_tool_url enforces.

   Every app-bound link already carries ?go=1: the worker serves THIS page on
   a parameter-less "/", so a bare href="/" would loop a clean visitor back
   here instead of opening the calculator. go=1 means nothing to the app --
   it exists purely to make the click-through distinguishable from a fresh
   arrival at the edge.

   They also carry from=welcome: the app validates that against NAV_ORIGINS
   and logs `nav:from=welcome:to=<destination>`, which is what turns "someone
   landed" into "someone landed and went to the schools search". Drop the
   param from a link and that link silently stops being counted --
   check_internal_links.py asserts every app-bound href still carries it. */
(function () {{
  var qs = location.search.replace(/^\\?/, "");
  if (!qs) return;
  document.querySelectorAll('a[href^="/"]').forEach(function (a) {{
    if (a.getAttribute("href").indexOf("llms.txt") !== -1) return;
    a.href += (a.href.indexOf("?") === -1 ? "?" : "&") + qs;
  }});
}})();
</script>
</body>
</html>
"""


def inject(worker_src: str, html: str) -> str:
    """Replace the LANDING constant between the markers."""
    if START_MARK not in worker_src:
        sys.exit(f"worker.js is missing {START_MARK} -- add the marker block first")
    block = (f"{START_MARK}\n"
             f"// GENERATED by infra/build_landing.py -- edit that, not this.\n"
             f"const LANDING = {json.dumps(html)};\n"
             f"{END_MARK}")
    return re.sub(
        re.escape(START_MARK) + r".*?" + re.escape(END_MARK),
        lambda _match: block, worker_src, count=1, flags=re.S)


def main():
    facts = app_facts()
    print("facts:", {k: v for k, v in facts.items() if k not in ("cap_rows", "cap_total")})
    html = build_html(facts)
    OUT.write_text(html)
    print(f"  wrote infra/landing.html  ({len(html):,} bytes)")
    WORKER.write_text(inject(WORKER.read_text(), html))
    print("  injected into infra/worker.js between the markers")


if __name__ == "__main__":
    main()

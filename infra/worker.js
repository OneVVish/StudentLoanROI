// Edge layer for worthmydegree.com — deployed as Cloudflare Worker
// "wmd-edge" on routes worthmydegree.com/* and www.worthmydegree.com/*.
//
// Jobs, in order:
//   1. Canonicalize host + scheme (www and http 301 to the https apex).
//   2. Serve /robots.txt, /sitemap.xml, /llms.txt (Streamlit cannot serve
//      root-path files, so the edge does).
//   3. Pass Streamlit internals (/_stcore websocket + health, static
//      assets) through UNTOUCHED — breaking these hangs the app.
//   4. 301 stray paths to / (the app has exactly one route).
//   5. Inject <link rel=canonical>, meta description, and Organization/
//      WebApplication JSON-LD into the HTML shell; add a Link: canonical
//      response header. Tracking params (src/test/admin/research/from)
//      are stripped from the CANONICAL VALUE ONLY — the request itself is
//      never rewritten, so share links and attribution are unaffected.
//
// Reference copies of the served text files live beside this file
// (infra/robots.txt, infra/sitemap.xml, infra/llms.txt). Change both
// halves in the same PR.

const CANON = "https://worthmydegree.com";

const META_DESC =
  "Free, anonymous calculator: pick a major, school, and loan; see the " +
  "10-year financial outcome under the 2026 federal repayment rules. " +
  "Real BLS, NY Fed, and College Scorecard data. No login.";

const ROBOTS = `# worthmydegree.com — free college-ROI calculator (student project)
# Canonical host. AI crawlers welcome; see /llms.txt for a dense summary.

User-agent: *
Allow: /
Disallow: /*?*admin=
Disallow: /*?*test=
Disallow: /*?*research=

# --- AI / LLM crawlers: explicitly welcome ---
User-agent: GPTBot
Allow: /

User-agent: OAI-SearchBot
Allow: /

User-agent: ChatGPT-User
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: Claude-User
Allow: /

User-agent: Claude-SearchBot
Allow: /

User-agent: PerplexityBot
Allow: /

User-agent: Perplexity-User
Allow: /

User-agent: Google-Extended
Allow: /

User-agent: Amazonbot
Allow: /

User-agent: CCBot
Allow: /

User-agent: meta-externalagent
Allow: /

Sitemap: https://worthmydegree.com/sitemap.xml
`;

const SITEMAP = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://worthmydegree.com/</loc>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://worthmydegree.com/?tool=repayment</loc>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>https://worthmydegree.com/?tool=schools</loc>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>https://worthmydegree.com/?tool=gradschools</loc>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>
</urlset>
`;

const LLMS = `# WorthMyDegree

> Free, anonymous college-ROI calculator. Enter a major, a school, and a
> loan; see the 10-year financial outcome versus a debt-free high-school
> graduate, under the 2026 US federal repayment rules. No login, no cost,
> nothing sold. Built by a high-school student; every figure on screen
> traces to a cited public source (the in-app Methodology section lists
> every citation).

## Pages

- [Calculator](https://worthmydegree.com/): major + school + loan →
  10-year net position, break-even debt, monthly payment and take-home
  pay by career stage, side-by-side comparison of two paths, the
  community-college transfer path (2+2 or full associate's), and a
  downloadable PDF report.
- [Repayment plan comparison](https://worthmydegree.com/?tool=repayment):
  for balances already owed — every current federal plan (Standard,
  Extended, Tiered Standard, IBR, RAP) plus private loans, on the user's
  actual balance and income, including forgiveness clocks and the
  one-way-door asymmetry of switching into RAP.
- [School search](https://worthmydegree.com/?tool=schools): field of
  study + monthly budget + home state → every US college that fits the
  budget, each priced at the rate that student would actually pay
  (in-state vs out-of-state resolved per school).
- [Graduate school search](https://worthmydegree.com/?tool=gradschools):
  field of study + master's or doctorate + budget → graduate schools
  priced from IPEDS published tuition, each shown beside what graduates
  in that field actually borrowed. Tuition and fees only — no federal
  source publishes graduate living costs.

## Data sources (all public, all cited in-app)

- BLS OEWS wages, layered national → state → metro
- BLS "typical education needed for entry" by occupation
- NY Fed labor-market outcomes by major (unemployment, underemployment,
  early/mid-career wages)
- College Scorecard: cost of attendance and median debt for 5,000+
  institutions; per-school medical/dental/law debt from the
  field-of-study release
- studentaid.gov published 2026 repayment rules — the RAP payment
  function is verified row-by-row against the published AGI chart by an
  automated check
- CPS ASEC microdata for the age-earnings profile behind the
  high-school-graduate baseline

## Facts an agent should get right

- Everything shown is an estimate for education, not financial advice.
- Outcome data reflects who attends a school, not the school's causal
  effect (selection vs. treatment); the tool discloses this.
- The 2026 federal overhaul changed borrowing materially: Grad PLUS is
  abolished, Parent PLUS is newly capped, and RAP is the default
  income-driven plan for new loans. Advice describing SAVE/PAYE-era
  rules is out of date.
- Canonical host: worthmydegree.com. studentloanroi.com (redirects) and
  studentloanroi.streamlit.app (legacy) are the same application.

## Contact

research@worthmydegree.com — student research project on whether outcome
transparency changes college decisions.
`;

const JSONLD = JSON.stringify({
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "@id": "https://worthmydegree.com/#org",
      "name": "WorthMyDegree",
      "url": "https://worthmydegree.com/",
      "founder": { "@type": "Person", "name": "Veer Vishwakarma" },
      "contactPoint": {
        "@type": "ContactPoint",
        "email": "research@worthmydegree.com",
        "contactType": "research inquiries",
      },
      // sameAs deliberately includes the legacy host: it binds the
      // duplicate content to this entity instead of competing with it.
      "sameAs": [
        "https://www.linkedin.com/in/veer-vishwakarma-b5a47a2b1/",
        "https://github.com/OneVVish/StudentLoanROI",
        "https://studentloanroi.streamlit.app",
      ],
    },
    {
      "@type": "WebApplication",
      "@id": "https://worthmydegree.com/#app",
      "name": "WorthMyDegree — Student Loan Payoff & Major ROI Calculator",
      "url": "https://worthmydegree.com/",
      "applicationCategory": "FinanceApplication",
      "operatingSystem": "Web",
      "offers": { "@type": "Offer", "price": "0", "priceCurrency": "USD" },
      "publisher": { "@id": "https://worthmydegree.com/#org" },
      "description":
        "Free, anonymous calculator showing the 10-year financial outcome " +
        "of a college + major + loan decision, using BLS, NY Fed, and " +
        "College Scorecard data and the 2026 federal repayment rules.",
    },
  ],
});

// The canonical value keeps ONLY the tool param — repayment and schools
// are real pages; everything else (src, test, admin, research, from,
// share-scenario params) identifies a session or campaign, not a page.
function canonicalFor(url) {
  const tool = url.searchParams.get("tool");
  return CANON + "/" + (tool ? `?tool=${encodeURIComponent(tool)}` : "");
}

// {{LANDING_HTML_START}}
// GENERATED by infra/build_landing.py -- edit that, not this.
const LANDING = "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n<title>Is the degree worth the loan? \u2014 worthmydegree.com</title>\n<meta name=\"description\" content=\"Free, anonymous calculator: pick a major, school, and loan; see the 10-year outcome under the 2026 federal repayment rules. 5,035 real schools, no sign-up.\">\n<link rel=\"canonical\" href=\"https://worthmydegree.com/\">\n<meta property=\"og:type\" content=\"website\">\n<meta property=\"og:title\" content=\"Is the degree worth the loan? \u2014 worthmydegree.com\">\n<meta property=\"og:description\" content=\"Free, anonymous calculator: pick a major, school, and loan; see the 10-year outcome under the 2026 federal repayment rules. 5,035 real schools, no sign-up.\">\n<meta property=\"og:url\" content=\"https://worthmydegree.com/\">\n<meta property=\"og:image\" content=\"https://worthmydegree.com/app/static/feature-og-1200x630.png\">\n<meta property=\"og:image:width\" content=\"1200\">\n<meta property=\"og:image:height\" content=\"630\">\n<meta name=\"twitter:card\" content=\"summary_large_image\">\n<meta name=\"twitter:image\" content=\"https://worthmydegree.com/app/static/feature-og-1200x630.png\">\n<link rel=\"icon\" href=\"data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA2NCA2NCIgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiByb2xlPSJpbWciIGFyaWEtbGFiZWw9IndvcnRobXlkZWdyZWUuY29tIj4KICA8dGl0bGU+d29ydGhteWRlZ3JlZS5jb208L3RpdGxlPgogIDxnIHN0eWxlPSItLXN1cmZhY2U6ICNmZmZmZmYiPgogICAgPHBhdGggZD0iTSA4LjAgMzguMCBMIDI0LjAgNTQuMCBMIDM1LjgyNiAzOC4wIiBmaWxsPSJub25lIiBzdHJva2U9IiNlYjY4MzQiIHN0cm9rZS13aWR0aD0iOS41IiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiLz4KICAgIDxwYXRoIGQ9Ik0gMzUuODI2IDM4LjAgTCA1OC4wIDguMCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjMmE3OGQ2IiBzdHJva2Utd2lkdGg9IjkuNSIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8+CiAgPC9nPgo8L3N2Zz4K\">\n<script type=\"application/ld+json\">{\n  \"@context\": \"https://schema.org\",\n  \"@graph\": [\n    {\"@type\": \"Organization\", \"@id\": \"https://worthmydegree.com/#org\",\n      \"name\": \"WorthMyDegree\", \"url\": \"https://worthmydegree.com/\"},\n    {\"@type\": \"WebApplication\", \"@id\": \"https://worthmydegree.com/#app\",\n      \"name\": \"WorthMyDegree \u2014 Student Loan Payoff & Major ROI Calculator\",\n      \"url\": \"https://worthmydegree.com/\",\n      \"applicationCategory\": \"FinanceApplication\", \"operatingSystem\": \"Web\",\n      \"offers\": {\"@type\": \"Offer\", \"price\": \"0\", \"priceCurrency\": \"USD\"},\n      \"publisher\": {\"@id\": \"https://worthmydegree.com/#org\"}}\n  ]\n}</script>\n<style>\n  :root {\n    --deep: #12335c; --blue: #2a78d6; --orange: #eb6834;\n    --ink: #14161a; --muted: #5c636d; --rule: #dfe3e8;\n    --tint: #fdf2ec; --tile: #f7f8fa; --surface: #ffffff;\n  }\n  * { margin: 0; padding: 0; box-sizing: border-box; }\n  body {\n    font: 17px/1.6 \"Avenir Next\", -apple-system, \"Segoe UI\", Roboto, sans-serif;\n    color: var(--ink); background: var(--surface);\n  }\n  .wrap { max-width: 980px; margin: 0 auto; padding: 0 24px; }\n  header {\n    display: flex; align-items: center; justify-content: space-between;\n    padding: 18px 0; border-bottom: 1px solid var(--rule);\n  }\n  .logo svg { height: 36px; width: auto; display: block; }\n  .btn {\n    display: inline-block; background: var(--orange); color: #fff;\n    font-weight: 700; text-decoration: none; border-radius: 10px;\n    padding: 12px 22px; font-size: 16px;\n  }\n  .btn.big { padding: 16px 34px; font-size: 19px; }\n  .btn.ghost { background: transparent; color: var(--deep);\n    border: 2px solid var(--deep); }\n  .hero { text-align: center; padding: 64px 0 40px; }\n  .hero h1 {\n    font-size: clamp(34px, 6vw, 58px); line-height: 1.08; color: var(--deep);\n    font-weight: 800; letter-spacing: -0.01em; text-transform: uppercase;\n  }\n  .hero .accent {\n    width: 130px; height: 3px; background: var(--orange);\n    margin: 22px auto; position: relative;\n  }\n  .hero p.sub {\n    font-size: 20px; color: var(--muted); max-width: 620px; margin: 0 auto 30px;\n  }\n  .trust { margin-top: 14px; color: var(--muted); font-size: 15px; }\n  .stats {\n    display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px;\n    padding: 26px 0 8px;\n  }\n  .stat { background: var(--tile); border-radius: 12px; padding: 18px 10px;\n    text-align: center; }\n  .stat b { display: block; font-size: 26px; color: var(--deep); }\n  .stat span { font-size: 14px; color: var(--muted); }\n  section { padding: 44px 0 8px; }\n  h2 { font-size: 28px; color: var(--deep); margin-bottom: 6px; }\n  .deck { color: var(--muted); margin-bottom: 24px; max-width: 640px; }\n  .grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }\n  .tile { background: var(--tile); border-radius: 12px; padding: 20px; }\n  .tile b { display: block; font-size: 17px; margin-bottom: 6px; }\n  .tile p { font-size: 15px; color: var(--muted); }\n  table { width: 100%; border-collapse: collapse; font-size: 16px;\n    margin-top: 18px; }\n  thead th { background: var(--deep); color: #fff; font-weight: 600;\n    padding: 10px 8px; }\n  tbody td, tfoot td { text-align: center; padding: 10px 8px;\n    border-bottom: 1px solid var(--rule); }\n  tbody tr:nth-child(even) { background: #f6f8fa; }\n  tfoot td { background: var(--tint); color: var(--deep); font-weight: 700; }\n  .note { font-size: 14px; color: var(--muted); margin-top: 12px;\n    max-width: 640px; }\n  .callout {\n    background: var(--tint); border: 2px solid var(--orange);\n    border-radius: 12px; padding: 18px 22px; margin-top: 20px; font-size: 16px;\n  }\n  .tools .tile a { color: var(--blue); font-weight: 600;\n    text-decoration: none; }\n  .cta { text-align: center; padding: 56px 0; }\n  footer {\n    border-top: 1px solid var(--rule); margin-top: 40px; padding: 26px 0 40px;\n    color: var(--muted); font-size: 14px;\n  }\n  @media (max-width: 720px) {\n    .stats { grid-template-columns: repeat(2, 1fr); }\n    .grid { grid-template-columns: 1fr; }\n    .hide-m { display: none; }\n    .table-scroll { overflow-x: auto; }\n  }\n</style>\n</head>\n<body>\n<div class=\"wrap\">\n\n<header>\n  <a class=\"logo\" href=\"/\" aria-label=\"worthmydegree.com\"><svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 461 64\"  role=\"img\" aria-label=\"worthmydegree.com\">\n  <title>worthmydegree.com</title>\n  <g style=\"--surface: #ffffff\">\n    <g><line x1=\"4\" y1=\"38.0\" x2=\"60\" y2=\"38.0\" stroke=\"#b0b5bd\" stroke-width=\"2.6\" stroke-linecap=\"round\"/>\n    <path d=\"M 8.0 38.0 L 24.0 54.0 L 35.826 38.0\" fill=\"none\" stroke=\"#eb6834\" stroke-width=\"8.5\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n    <path d=\"M 35.826 38.0 L 58.0 8.0\" fill=\"none\" stroke=\"#2a78d6\" stroke-width=\"8.5\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n    <circle cx=\"35.826\" cy=\"38.0\" r=\"5.6\" fill=\"#2a78d6\" stroke=\"var(--surface)\" stroke-width=\"2.4\"/></g>\n    <g transform=\"translate(82, 42)\">\n      <path d=\"M 0.16 -19.20 L 5.36 -19.20 L 9.36 -5.44 L 9.44 -5.44 L 13.56 -19.20 L 18.68 -19.20 L 22.84 -5.44 L 22.92 -5.44 L 27.00 -19.20 L 32.00 -19.20 L 25.36 -0.00 L 20.48 -0.00 L 16.12 -13.44 L 16.04 -13.44 L 11.72 -0.00 L 6.84 -0.00 L 0.16 -19.20 Z M 33.93 -9.68 Q 33.93 -11.96 34.74 -13.82 Q 35.56 -15.68 36.96 -17.00 Q 38.36 -18.32 40.28 -19.04 Q 42.20 -19.76 44.36 -19.76 Q 46.53 -19.76 48.44 -19.04 Q 50.36 -18.32 51.76 -17.00 Q 53.16 -15.68 53.98 -13.82 Q 54.80 -11.96 54.80 -9.68 Q 54.80 -7.40 53.98 -5.52 Q 53.16 -3.64 51.76 -2.30 Q 50.36 -0.96 48.44 -0.20 Q 46.53 0.56 44.36 0.56 Q 42.20 0.56 40.28 -0.20 Q 38.36 -0.96 36.96 -2.30 Q 35.56 -3.64 34.74 -5.52 Q 33.93 -7.40 33.93 -9.68 Z M 38.80 -9.68 Q 38.80 -8.56 39.14 -7.44 Q 39.48 -6.32 40.16 -5.44 Q 40.84 -4.56 41.88 -4.00 Q 42.93 -3.44 44.36 -3.44 Q 45.80 -3.44 46.84 -4.00 Q 47.88 -4.56 48.56 -5.44 Q 49.24 -6.32 49.58 -7.44 Q 49.93 -8.56 49.93 -9.68 Q 49.93 -10.80 49.58 -11.90 Q 49.24 -13.00 48.56 -13.88 Q 47.88 -14.76 46.84 -15.30 Q 45.80 -15.84 44.36 -15.84 Q 42.93 -15.84 41.88 -15.30 Q 40.84 -14.76 40.16 -13.88 Q 39.48 -13.00 39.14 -11.90 Q 38.80 -10.80 38.80 -9.68 Z M 59.44 -19.20 L 64.04 -19.20 L 64.04 -16.00 L 64.12 -16.00 Q 64.92 -17.68 66.36 -18.72 Q 67.80 -19.76 69.73 -19.76 Q 70.00 -19.76 70.32 -19.74 Q 70.64 -19.72 70.88 -19.64 L 70.88 -15.24 Q 70.40 -15.36 70.06 -15.40 Q 69.73 -15.44 69.40 -15.44 Q 67.76 -15.44 66.76 -14.84 Q 65.76 -14.24 65.20 -13.40 Q 64.64 -12.56 64.44 -11.68 Q 64.24 -10.80 64.24 -10.28 L 64.24 -0.00 L 59.44 -0.00 L 59.44 -19.20 Z M 70.76 -15.36 L 70.76 -19.20 L 74.12 -19.20 L 74.12 -24.76 L 78.84 -24.76 L 78.84 -19.20 L 83.64 -19.20 L 83.64 -15.36 L 78.84 -15.36 L 78.84 -6.44 Q 78.84 -5.16 79.30 -4.32 Q 79.76 -3.48 81.36 -3.48 Q 81.84 -3.48 82.40 -3.58 Q 82.96 -3.68 83.41 -3.88 L 83.56 -0.12 Q 82.93 0.12 82.04 0.26 Q 81.16 0.40 80.36 0.40 Q 78.44 0.40 77.24 -0.14 Q 76.04 -0.68 75.34 -1.62 Q 74.64 -2.56 74.38 -3.78 Q 74.12 -5.00 74.12 -6.40 L 74.12 -15.36 L 70.76 -15.36 Z M 97.81 -19.76 Q 99.68 -19.76 101.02 -19.10 Q 102.36 -18.44 103.22 -17.36 Q 104.08 -16.28 104.48 -14.88 Q 104.88 -13.48 104.88 -12.00 L 104.88 -0.00 L 100.08 -0.00 L 100.08 -10.56 Q 100.08 -11.40 99.96 -12.30 Q 99.84 -13.20 99.46 -13.94 Q 99.08 -14.68 98.38 -15.16 Q 97.68 -15.64 96.53 -15.64 Q 95.36 -15.64 94.52 -15.20 Q 93.68 -14.76 93.12 -14.02 Q 92.56 -13.28 92.28 -12.36 Q 92.01 -11.44 92.01 -10.48 L 92.01 -0.00 L 87.21 -0.00 L 87.21 -30.24 L 92.01 -30.24 L 92.01 -16.52 L 92.08 -16.52 Q 92.36 -17.12 92.90 -17.70 Q 93.44 -18.28 94.16 -18.74 Q 94.88 -19.20 95.80 -19.48 Q 96.73 -19.76 97.81 -19.76 Z M 133.41 -19.76 Q 135.29 -19.76 136.62 -19.10 Q 137.97 -18.44 138.83 -17.36 Q 139.69 -16.28 140.09 -14.88 Q 140.49 -13.48 140.49 -12.00 L 140.49 -0.00 L 135.69 -0.00 L 135.69 -10.64 Q 135.69 -11.48 135.56 -12.40 Q 135.44 -13.32 135.04 -14.06 Q 134.64 -14.80 133.94 -15.28 Q 133.24 -15.76 132.09 -15.76 Q 130.97 -15.76 130.17 -15.28 Q 129.37 -14.80 128.84 -14.04 Q 128.33 -13.28 128.08 -12.34 Q 127.84 -11.40 127.84 -10.48 L 127.84 -0.00 L 123.04 -0.00 L 123.04 -11.60 Q 123.04 -13.40 122.18 -14.58 Q 121.33 -15.76 119.49 -15.76 Q 118.41 -15.76 117.61 -15.30 Q 116.81 -14.84 116.31 -14.12 Q 115.81 -13.40 115.54 -12.46 Q 115.29 -11.52 115.29 -10.56 L 115.29 -0.00 L 110.49 -0.00 L 110.49 -19.20 L 115.04 -19.20 L 115.04 -16.12 L 115.12 -16.12 Q 115.44 -16.84 115.98 -17.48 Q 116.53 -18.12 117.26 -18.64 Q 118.01 -19.16 118.96 -19.46 Q 119.93 -19.76 121.09 -19.76 Q 123.33 -19.76 124.81 -18.76 Q 126.29 -17.76 127.09 -16.12 Q 128.04 -17.88 129.64 -18.82 Q 131.24 -19.76 133.41 -19.76 Z M 143.21 -19.20 L 148.53 -19.20 L 153.89 -5.24 L 153.97 -5.24 L 158.73 -19.20 L 163.73 -19.20 L 154.64 4.12 Q 154.12 5.44 153.53 6.46 Q 152.93 7.48 152.08 8.18 Q 151.24 8.88 150.11 9.24 Q 148.97 9.60 147.37 9.60 Q 146.77 9.60 146.14 9.54 Q 145.53 9.48 144.89 9.32 L 145.29 5.16 Q 145.77 5.32 146.22 5.38 Q 146.69 5.44 147.09 5.44 Q 147.84 5.44 148.36 5.26 Q 148.89 5.08 149.24 4.70 Q 149.61 4.32 149.89 3.76 Q 150.17 3.20 150.49 2.44 L 151.44 -0.00 L 143.21 -19.20 Z M 186.12 -0.00 L 181.56 -0.00 L 181.56 -2.88 L 181.49 -2.88 Q 180.49 -1.20 178.71 -0.32 Q 176.93 0.56 174.89 0.56 Q 172.69 0.56 170.94 -0.26 Q 169.21 -1.08 167.98 -2.46 Q 166.76 -3.84 166.10 -5.70 Q 165.44 -7.56 165.44 -9.68 Q 165.44 -11.80 166.12 -13.64 Q 166.81 -15.48 168.04 -16.84 Q 169.29 -18.20 171.01 -18.98 Q 172.73 -19.76 174.76 -19.76 Q 176.09 -19.76 177.12 -19.46 Q 178.16 -19.16 178.94 -18.72 Q 179.73 -18.28 180.28 -17.76 Q 180.84 -17.24 181.21 -16.76 L 181.33 -16.76 L 181.33 -30.24 L 186.12 -30.24 L 186.12 -0.00 Z M 170.33 -9.68 Q 170.33 -8.56 170.66 -7.44 Q 171.01 -6.32 171.68 -5.44 Q 172.36 -4.56 173.40 -4.00 Q 174.44 -3.44 175.84 -3.44 Q 177.16 -3.44 178.20 -3.98 Q 179.24 -4.52 179.98 -5.40 Q 180.73 -6.28 181.11 -7.38 Q 181.49 -8.48 181.49 -9.60 Q 181.49 -10.72 181.11 -11.84 Q 180.73 -12.96 179.98 -13.84 Q 179.24 -14.72 178.20 -15.28 Q 177.16 -15.84 175.84 -15.84 Q 174.44 -15.84 173.40 -15.30 Q 172.36 -14.76 171.68 -13.88 Q 171.01 -13.00 170.66 -11.90 Q 170.33 -10.80 170.33 -9.68 Z M 205.49 -11.52 Q 205.49 -12.44 205.23 -13.28 Q 204.97 -14.12 204.41 -14.76 Q 203.85 -15.40 202.99 -15.78 Q 202.12 -16.16 200.97 -16.16 Q 198.81 -16.16 197.31 -14.86 Q 195.81 -13.56 195.65 -11.52 L 205.49 -11.52 Z M 210.29 -9.36 Q 210.29 -9.04 210.29 -8.72 Q 210.29 -8.40 210.25 -8.08 L 195.65 -8.08 Q 195.73 -7.04 196.19 -6.18 Q 196.65 -5.32 197.41 -4.70 Q 198.17 -4.08 199.12 -3.72 Q 200.09 -3.36 201.12 -3.36 Q 202.93 -3.36 204.16 -4.02 Q 205.41 -4.68 206.21 -5.84 L 209.41 -3.28 Q 206.57 0.56 201.17 0.56 Q 198.93 0.56 197.04 -0.14 Q 195.17 -0.84 193.79 -2.12 Q 192.41 -3.40 191.62 -5.26 Q 190.85 -7.12 190.85 -9.48 Q 190.85 -11.80 191.62 -13.70 Q 192.41 -15.60 193.76 -16.94 Q 195.12 -18.28 196.99 -19.02 Q 198.85 -19.76 201.01 -19.76 Q 203.01 -19.76 204.71 -19.10 Q 206.41 -18.44 207.64 -17.14 Q 208.89 -15.84 209.59 -13.90 Q 210.29 -11.96 210.29 -9.36 Z M 234.45 -19.20 L 234.45 -1.72 Q 234.45 0.88 233.81 2.98 Q 233.17 5.08 231.83 6.54 Q 230.49 8.00 228.45 8.80 Q 226.41 9.60 223.61 9.60 Q 222.45 9.60 221.15 9.40 Q 219.85 9.20 218.59 8.80 Q 217.33 8.40 216.17 7.80 Q 215.01 7.20 214.09 6.44 L 216.85 2.72 Q 218.25 4.04 220.01 4.74 Q 221.77 5.44 223.57 5.44 Q 225.29 5.44 226.47 4.94 Q 227.65 4.44 228.35 3.56 Q 229.05 2.68 229.35 1.48 Q 229.65 0.28 229.65 -1.16 L 229.65 -2.56 L 229.57 -2.56 Q 228.53 -1.16 226.91 -0.42 Q 225.29 0.32 223.29 0.32 Q 221.13 0.32 219.37 -0.48 Q 217.61 -1.28 216.39 -2.64 Q 215.17 -4.00 214.49 -5.82 Q 213.81 -7.64 213.81 -9.68 Q 213.81 -11.76 214.47 -13.60 Q 215.13 -15.44 216.35 -16.80 Q 217.57 -18.16 219.31 -18.96 Q 221.05 -19.76 223.21 -19.76 Q 225.25 -19.76 227.01 -18.92 Q 228.77 -18.08 229.81 -16.32 L 229.89 -16.32 L 229.89 -19.20 L 234.45 -19.20 Z M 224.21 -15.84 Q 222.89 -15.84 221.87 -15.36 Q 220.85 -14.88 220.15 -14.06 Q 219.45 -13.24 219.07 -12.12 Q 218.69 -11.00 218.69 -9.72 Q 218.69 -8.56 219.07 -7.48 Q 219.45 -6.40 220.15 -5.54 Q 220.85 -4.68 221.87 -4.16 Q 222.89 -3.64 224.17 -3.64 Q 225.49 -3.64 226.55 -4.14 Q 227.61 -4.64 228.35 -5.48 Q 229.09 -6.32 229.49 -7.42 Q 229.89 -8.52 229.89 -9.72 Q 229.89 -10.96 229.49 -12.08 Q 229.09 -13.20 228.35 -14.04 Q 227.61 -14.88 226.57 -15.36 Q 225.53 -15.84 224.21 -15.84 Z M 240.21 -19.20 L 244.81 -19.20 L 244.81 -16.00 L 244.89 -16.00 Q 245.69 -17.68 247.13 -18.72 Q 248.57 -19.76 250.49 -19.76 Q 250.77 -19.76 251.09 -19.74 Q 251.41 -19.72 251.65 -19.64 L 251.65 -15.24 Q 251.17 -15.36 250.83 -15.40 Q 250.49 -15.44 250.17 -15.44 Q 248.53 -15.44 247.53 -14.84 Q 246.53 -14.24 245.97 -13.40 Q 245.41 -12.56 245.21 -11.68 Q 245.01 -10.80 245.01 -10.28 L 245.01 -0.00 L 240.21 -0.00 L 240.21 -19.20 Z M 267.57 -11.52 Q 267.57 -12.44 267.31 -13.28 Q 267.06 -14.12 266.49 -14.76 Q 265.94 -15.40 265.07 -15.78 Q 264.21 -16.16 263.06 -16.16 Q 260.89 -16.16 259.39 -14.86 Q 257.89 -13.56 257.74 -11.52 L 267.57 -11.52 Z M 272.38 -9.36 Q 272.38 -9.04 272.38 -8.72 Q 272.38 -8.40 272.34 -8.08 L 257.74 -8.08 Q 257.81 -7.04 258.28 -6.18 Q 258.74 -5.32 259.49 -4.70 Q 260.26 -4.08 261.21 -3.72 Q 262.18 -3.36 263.21 -3.36 Q 265.01 -3.36 266.25 -4.02 Q 267.49 -4.68 268.29 -5.84 L 271.49 -3.28 Q 268.66 0.56 263.26 0.56 Q 261.01 0.56 259.13 -0.14 Q 257.26 -0.84 255.88 -2.12 Q 254.49 -3.40 253.71 -5.26 Q 252.94 -7.12 252.94 -9.48 Q 252.94 -11.80 253.71 -13.70 Q 254.49 -15.60 255.85 -16.94 Q 257.21 -18.28 259.07 -19.02 Q 260.94 -19.76 263.09 -19.76 Q 265.09 -19.76 266.79 -19.10 Q 268.49 -18.44 269.73 -17.14 Q 270.98 -15.84 271.68 -13.90 Q 272.38 -11.96 272.38 -9.36 Z M 290.54 -11.52 Q 290.54 -12.44 290.28 -13.28 Q 290.02 -14.12 289.46 -14.76 Q 288.90 -15.40 288.04 -15.78 Q 287.18 -16.16 286.02 -16.16 Q 283.86 -16.16 282.36 -14.86 Q 280.86 -13.56 280.70 -11.52 L 290.54 -11.52 Z M 295.34 -9.36 Q 295.34 -9.04 295.34 -8.72 Q 295.34 -8.40 295.30 -8.08 L 280.70 -8.08 Q 280.78 -7.04 281.24 -6.18 Q 281.70 -5.32 282.46 -4.70 Q 283.22 -4.08 284.18 -3.72 Q 285.14 -3.36 286.18 -3.36 Q 287.98 -3.36 289.21 -4.02 Q 290.46 -4.68 291.26 -5.84 L 294.46 -3.28 Q 291.62 0.56 286.22 0.56 Q 283.98 0.56 282.09 -0.14 Q 280.22 -0.84 278.84 -2.12 Q 277.46 -3.40 276.68 -5.26 Q 275.90 -7.12 275.90 -9.48 Q 275.90 -11.80 276.68 -13.70 Q 277.46 -15.60 278.81 -16.94 Q 280.18 -18.28 282.04 -19.02 Q 283.90 -19.76 286.06 -19.76 Q 288.06 -19.76 289.76 -19.10 Q 291.46 -18.44 292.69 -17.14 Q 293.94 -15.84 294.64 -13.90 Q 295.34 -11.96 295.34 -9.36 Z\" fill=\"#1a1c1f\"/>\n    </g>\n    <g transform=\"translate(383.33750000000003, 42)\">\n      <path d=\"M 1.60 -2.24 Q 1.60 -3.27 2.35 -4.00 Q 3.10 -4.74 4.16 -4.74 Q 5.19 -4.74 5.95 -4.03 Q 6.72 -3.33 6.72 -2.31 Q 6.72 -1.28 5.96 -0.55 Q 5.21 0.19 4.16 0.19 Q 3.65 0.19 3.19 -0.00 Q 2.72 -0.19 2.37 -0.51 Q 2.02 -0.83 1.80 -1.28 Q 1.60 -1.73 1.60 -2.24 Z M 21.12 -11.11 Q 20.67 -11.75 19.78 -12.18 Q 18.88 -12.61 17.95 -12.61 Q 16.89 -12.61 16.09 -12.18 Q 15.29 -11.75 14.77 -11.04 Q 14.24 -10.34 13.98 -9.46 Q 13.73 -8.58 13.73 -7.68 Q 13.73 -6.79 14.00 -5.91 Q 14.27 -5.03 14.81 -4.32 Q 15.36 -3.62 16.18 -3.18 Q 16.99 -2.75 18.08 -2.75 Q 18.98 -2.75 19.87 -3.10 Q 20.77 -3.46 21.31 -4.13 L 23.71 -1.70 Q 22.75 -0.67 21.26 -0.11 Q 19.78 0.45 18.05 0.45 Q 16.35 0.45 14.83 -0.10 Q 13.31 -0.64 12.18 -1.70 Q 11.04 -2.75 10.38 -4.25 Q 9.73 -5.76 9.73 -7.68 Q 9.73 -9.54 10.38 -11.04 Q 11.04 -12.54 12.16 -13.60 Q 13.28 -14.66 14.77 -15.23 Q 16.25 -15.81 17.95 -15.81 Q 19.65 -15.81 21.23 -15.17 Q 22.82 -14.53 23.75 -13.41 L 21.12 -11.11 Z M 25.34 -7.75 Q 25.34 -9.57 26.00 -11.05 Q 26.66 -12.54 27.78 -13.60 Q 28.89 -14.66 30.43 -15.23 Q 31.96 -15.81 33.70 -15.81 Q 35.43 -15.81 36.96 -15.23 Q 38.49 -14.66 39.62 -13.60 Q 40.73 -12.54 41.39 -11.05 Q 42.05 -9.57 42.05 -7.75 Q 42.05 -5.92 41.39 -4.42 Q 40.73 -2.91 39.62 -1.84 Q 38.49 -0.77 36.96 -0.16 Q 35.43 0.45 33.70 0.45 Q 31.96 0.45 30.43 -0.16 Q 28.89 -0.77 27.78 -1.84 Q 26.66 -2.91 26.00 -4.42 Q 25.34 -5.92 25.34 -7.75 Z M 29.25 -7.75 Q 29.25 -6.85 29.52 -5.95 Q 29.79 -5.05 30.34 -4.35 Q 30.88 -3.65 31.71 -3.20 Q 32.55 -2.75 33.70 -2.75 Q 34.84 -2.75 35.68 -3.20 Q 36.51 -3.65 37.05 -4.35 Q 37.60 -5.05 37.87 -5.95 Q 38.15 -6.85 38.15 -7.75 Q 38.15 -8.64 37.87 -9.52 Q 37.60 -10.40 37.05 -11.11 Q 36.51 -11.81 35.68 -12.24 Q 34.84 -12.67 33.70 -12.67 Q 32.55 -12.67 31.71 -12.24 Q 30.88 -11.81 30.34 -11.11 Q 29.79 -10.40 29.52 -9.52 Q 29.25 -8.64 29.25 -7.75 Z M 64.09 -15.81 Q 65.60 -15.81 66.67 -15.28 Q 67.75 -14.75 68.43 -13.88 Q 69.12 -13.03 69.44 -11.90 Q 69.76 -10.79 69.76 -9.60 L 69.76 -0.00 L 65.92 -0.00 L 65.92 -8.51 Q 65.92 -9.19 65.82 -9.92 Q 65.72 -10.65 65.41 -11.25 Q 65.09 -11.84 64.53 -12.22 Q 63.97 -12.61 63.04 -12.61 Q 62.15 -12.61 61.51 -12.22 Q 60.87 -11.84 60.45 -11.23 Q 60.03 -10.62 59.84 -9.87 Q 59.65 -9.12 59.65 -8.38 L 59.65 -0.00 L 55.80 -0.00 L 55.80 -9.28 Q 55.80 -10.72 55.12 -11.67 Q 54.43 -12.61 52.96 -12.61 Q 52.09 -12.61 51.45 -12.24 Q 50.81 -11.87 50.41 -11.29 Q 50.02 -10.72 49.80 -9.96 Q 49.60 -9.21 49.60 -8.45 L 49.60 -0.00 L 45.76 -0.00 L 45.76 -15.36 L 49.41 -15.36 L 49.41 -12.89 L 49.47 -12.89 Q 49.73 -13.47 50.16 -13.98 Q 50.59 -14.50 51.18 -14.91 Q 51.77 -15.33 52.54 -15.57 Q 53.31 -15.81 54.24 -15.81 Q 56.03 -15.81 57.22 -15.01 Q 58.40 -14.21 59.04 -12.89 Q 59.80 -14.30 61.09 -15.05 Q 62.37 -15.81 64.09 -15.81 Z\" fill=\"#8a8f98\"/>\n    </g>\n  </g>\n</svg>\n</a>\n  <a class=\"btn hide-m\" href=\"/?go=1&amp;from=welcome\">Open the calculator</a>\n</header>\n\n<div class=\"hero\">\n  <h1>Is the degree<br>worth the loan?</h1>\n  <div class=\"accent\"></div>\n  <p class=\"sub\">Pick a school and a major. See the real loan, the monthly\n  payment under the 2026 federal rules, and where you stand ten years out \u2014\n  before anyone signs anything.</p>\n  <a class=\"btn big\" href=\"/?go=1&amp;from=welcome\">Run your numbers \u2014 free</a>\n  <div class=\"trust\">Free \u00b7 anonymous \u00b7 no sign-up \u00b7 no ads</div>\n</div>\n\n<div class=\"stats\">\n  <div class=\"stat\"><b>5,035</b><span>real schools, published costs</span></div>\n  <div class=\"stat\"><b>836</b><span>careers with federal wage data</span></div>\n  <div class=\"stat\"><b>73</b><span>majors, NY Fed outcomes</span></div>\n  <div class=\"stat\"><b>23</b><span>metro areas, local pay &amp; prices</span></div>\n</div>\n\n<section>\n  <h2>The numbers colleges don't put on the brochure</h2>\n  <p class=\"deck\">Sticker price predicts almost nothing. These are the ones\n  that decide how the next decade feels.</p>\n  <div class=\"grid\">\n    <div class=\"tile\"><b>\ud83d\udcca The loan you'd actually sign</b>\n      <p>Median borrowing at your school \u2014 or build it from cost, aid and the\n      federal caps. Berkeley's sticker is $45,619/yr; its\n      median borrower leaves with $13,000.</p></div>\n    <div class=\"tile\"><b>\ud83d\udcb8 The payment, not just the debt</b>\n      <p>The 2026 RAP income-driven plan against the fixed plans, month by\n      month, including what gets waived and what gets forgiven.</p></div>\n    <div class=\"tile\"><b>\ud83c\udfd9\ufe0f Ten years out, where you'll live</b>\n      <p>Metro wages and cost of living, not a national average \u2014 the same\n      salary is a different life in San Francisco and in Columbus.</p></div>\n    <div class=\"tile\"><b>\ud83d\udd00 Two paths, side by side</b>\n      <p>UC vs CSU, nursing vs CS, or the same degree with two community-college\n      years first. One page, same math on both sides.</p></div>\n    <div class=\"tile\"><b>\ud83c\udf93 Graduate &amp; professional</b>\n      <p>Medicine, dentistry, law, the MBA and five more \u2014 priced per school\n      from federal data, residency years modelled.</p></div>\n    <div class=\"tile\"><b>\ud83d\udcc4 Take it with you</b>\n      <p>Every result exports as a PDF report or a shareable image, sourced\n      down to the footnote.</p></div>\n  </div>\n</section>\n\n<section>\n  <h2>Know before you borrow</h2>\n  <p class=\"deck\">Starting college in 2026 or later, federal\n  borrowing for a dependent undergraduate has hard ceilings. This is the whole\n  table:</p>\n  <div class=\"table-scroll\">\n  <table>\n    <thead><tr><th>College year</th><th>Student Direct loan</th>\n      <th>Parent PLUS max</th><th>Maximum combined</th></tr></thead>\n    <tbody>\n        <tr><td>Freshman</td><td>$5,500</td><td>$20,000</td><td>$25,500</td></tr>\n        <tr><td>Sophomore</td><td>$6,500</td><td>$20,000</td><td>$26,500</td></tr>\n        <tr><td>Junior</td><td>$7,500</td><td>$20,000</td><td>$27,500</td></tr>\n        <tr><td>Senior</td><td>$7,500</td><td>$5,000*</td><td>$12,500</td></tr>\n    </tbody>\n    <tfoot><tr><td>4-year total</td><td>$27,000</td>\n      <td>$65,000</td><td>$92,000</td></tr></tfoot>\n  </table>\n  </div>\n  <p class=\"note\">*Parent PLUS is capped at $65,000 in\n  total per dependent student \u2014 borrow $20,000 in each of the\n  first three years and only $5,000 remains for senior year. These\n  are ceilings, not offers; anything a school costs beyond them is private\n  borrowing.</p>\n  <div class=\"callout\">Planning around PLUS? The steady number is\n  $16,250 a year \u2014 not $20,000.\n  The calculator applies these caps automatically to any school you pick.</div>\n</section>\n\n<section class=\"tools\">\n  <h2>Three tools, one dataset</h2>\n  <div class=\"grid\">\n    <div class=\"tile\"><b>\ud83c\udf93 The calculator</b>\n      <p>School + major + loan \u2192 the 10-year verdict.\n      <a href=\"/?go=1&amp;from=welcome\">Open&nbsp;\u2192</a></p></div>\n    <div class=\"tile\"><b>\ud83d\udd0e Schools that fit a budget</b>\n      <p>Every school teaching your field, cheapest first, priced for your\n      state. <a href=\"/?tool=schools&amp;from=welcome\">Search&nbsp;\u2192</a></p></div>\n    <div class=\"tile\"><b>\ud83d\udcb8 Already have loans?</b>\n      <p>Compare the 2026 repayment plans on the balance you already owe.\n      <a href=\"/?tool=repayment&amp;from=welcome\">Compare&nbsp;\u2192</a></p></div>\n  </div>\n</section>\n\n<div class=\"cta\">\n  <h2>Two minutes. Zero forms.</h2>\n  <p class=\"deck\" style=\"margin:8px auto 22px\">The most expensive decision most\n  families ever finance deserves ten real minutes of arithmetic.</p>\n  <a class=\"btn big\" href=\"/?go=1&amp;from=welcome\">Open the calculator</a>\n</div>\n\n<footer>\n  Built from Bureau of Labor Statistics, New York Fed, College Scorecard,\n  IPEDS and CPS ASEC data \u2014 every figure traceable to its source in the app's\n  Methodology section. A student research project. Educational estimate, not\n  financial advice.<br>\n  <a href=\"/llms.txt\" style=\"color:inherit\">Plain-text summary</a> \u00b7\n  <a href=\"/\" style=\"color:inherit\">worthmydegree.com</a>\n</footer>\n\n</div>\n<script>\n/* Carry the visitor's query string (?src= attribution, ?test=) onto every\n   internal link, so a tagged arrival stays tagged when they click through --\n   the same rule the app's own internal_tool_url enforces.\n\n   Every app-bound link already carries ?go=1: the worker serves THIS page on\n   a parameter-less \"/\", so a bare href=\"/\" would loop a clean visitor back\n   here instead of opening the calculator. go=1 means nothing to the app --\n   it exists purely to make the click-through distinguishable from a fresh\n   arrival at the edge.\n\n   They also carry from=welcome: the app validates that against NAV_ORIGINS\n   and logs `nav:from=welcome:to=<destination>`, which is what turns \"someone\n   landed\" into \"someone landed and went to the schools search\". Drop the\n   param from a link and that link silently stops being counted --\n   check_internal_links.py asserts every app-bound href still carries it. */\n(function () {\n  var qs = location.search.replace(/^\\?/, \"\");\n  if (!qs) return;\n  document.querySelectorAll('a[href^=\"/\"]').forEach(function (a) {\n    if (a.getAttribute(\"href\").indexOf(\"llms.txt\") !== -1) return;\n    a.href += (a.href.indexOf(\"?\") === -1 ? \"?\" : \"&\") + qs;\n  });\n})();\n</script>\n</body>\n</html>\n";
// {{LANDING_HTML_END}}

const TEXT_FILES = {
  "/robots.txt": ["text/plain", ROBOTS],
  "/sitemap.xml": ["application/xml", SITEMAP],
  "/llms.txt": ["text/markdown", LLMS],
};

// Served when the origin cannot answer: a 5xx, or a fetch that throws
// (connection refused, TLS failure, or the subrequest timing out).
//
// WHY THIS EXISTS. The app is one Streamlit process on Community Cloud. Under
// a spike it can stop answering, and what a visitor sees then is Streamlit's
// own connecting spinner, forever, with no explanation -- and a crawler sees
// an error where it previously saw a calculator. A static page that says what
// happened costs nothing to serve, is indexable, and is honest.
//
// Deliberately plain HTML with inline CSS: it must render with no origin, no
// assets and no JavaScript. Anything it references could be the thing that is
// down.
//
// It carries a 503 with Retry-After so crawlers treat it as temporary and come
// back, rather than de-indexing the page.
const BUSY_PAGE = `<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Busy right now — Worth My Degree</title>
<meta name="robots" content="noindex">
<style>
 body{margin:0;background:#0e1117;color:#fafafa;font:16px/1.6 -apple-system,
   BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;}
 main{max-width:38rem;margin:0 auto;padding:3rem 1.25rem;}
 h1{font-size:1.6rem;line-height:1.25;margin:0 0 .5rem;}
 p{color:#c9ccd4;} a{color:#ff4b4b;}
 .card{border:1px solid #2b3038;border-radius:.6rem;padding:1rem 1.25rem;
   margin:1.5rem 0;background:#161a22;}
 ul{padding-left:1.1rem;color:#c9ccd4;} li{margin:.35rem 0;}
</style></head><body><main>
<h1>🎓 More people than usual are using this right now</h1>
<p>The calculator is temporarily unavailable. Nothing is broken and nothing you
entered was saved — it is one small server, and it is busy.
<a href="/">Try again</a> in a minute.</p>
<div class="card">
<p><strong>What this tool does, while you wait.</strong> It models the real
ten-year financial outcome of a major, school and loan: repayment under the
2026 federal rules, federal and state tax, and cost of living by city, compared
against a debt-free high school graduate.</p>
<ul>
<li>Wages: BLS Occupational Employment and Wage Statistics.</li>
<li>Major outcomes: New York Fed's labor-market survey.</li>
<li>School cost and borrowing: College Scorecard and IPEDS.</li>
</ul>
<p>Free, anonymous, no sign-up. Every figure is computed from published federal
data — an educational estimate, not financial advice.</p>
</div>
<p><a href="/llms.txt">A dense text summary</a> of the whole tool is served
from the edge and is always available.</p>
</main></body></html>`;

// Streamlit-owned path prefixes that must reach the origin unmodified.
// /_stcore carries the websocket (the app's entire runtime) and the
// health endpoint Railway checks.
// /app is Streamlit's static-file mount (enableStaticServing serves repo
// ./static at /app/static) -- without this entry the stray-path 301 folded
// every image URL back to /.
const PASSTHROUGH = ["/_stcore", "/static", "/app/static", "/component", "/media", "/vendor", "/favicon"];

// Of those, the prefixes safe to CACHE at the edge. /static is Streamlit's
// bundle directory and its filenames are content-hashed, so a day of TTL can
// never serve a stale byte -- a redeploy mints new names and the uncached
// HTML shell points at them. This is the single biggest origin-load cut a
// spike can get: without it every new visitor pulled every JS/CSS bundle
// from the origin, which is exactly the multiplication a traffic spike is.
//
// /_stcore must NEVER join this list (websocket + health check), and the
// shell stays uncached for the same version-skew reason the hashes make
// /static safe. /component, /media, /vendor and /favicon are left alone
// until someone verifies their names are immutable too -- caching them on
// an assumption is how a stale asset would outlive a deploy.
// Per-prefix, because the two cacheable families age differently. /static
// bundles are content-hashed -- a day plus `immutable` can never serve a
// stale byte. /app/static is Streamlit's static-file feature carrying the
// marketing images (repo ./static, see static/README.md), whose NAMES DO NOT
// CHANGE when the content does -- an hour, no immutable, so a regenerated
// poster propagates within the hour instead of within a day.
const EDGE_CACHED = [
  { prefix: "/static", ttl: 86400, immutable: true },
  { prefix: "/app/static", ttl: 3600, immutable: false },
];

function busyResponse() {
  return new Response(BUSY_PAGE, {
    status: 503,
    headers: {
      "content-type": "text/html; charset=utf-8",
      // Temporary, and say so: a crawler that reads 503 + Retry-After comes
      // back rather than dropping the page from its index.
      "retry-after": "60",
      "cache-control": "no-store",
    },
  });
}

// Landing-page counting, written to the SAME Supabase table the app logs to,
// so the admin page reads it with the machinery it already has.
//
// SERVER-SIDE ON PURPOSE. The obvious alternative -- a beacon script in the
// page -- would put an external reference on a page whose entire value is
// that it references nothing and therefore renders when the origin is down.
// This runs in the Worker AFTER the response is already on its way
// (ctx.waitUntil), so the visitor's browser makes zero extra requests and
// waits for nothing.
//
// Needs two secrets, set once per deploy:
//     npx wrangler secret put SUPABASE_URL
//     npx wrangler secret put SUPABASE_ANON_KEY
// Absent either, logging is skipped silently -- a missing secret must cost a
// statistic, never a page.
const LANDING_ACTION = "landing_view";
const LANDING_LOG_TIMEOUT_MS = 2500;

// Crude on purpose, and stated as such: a user-agent substring match catches
// the declared crawlers (which are most of the volume) and nothing else. It
// exists so the landing count is not mostly Googlebot; it is not a bot
// defence and must not be read as one.
const BOT_UA = /bot|crawl|spider|slurp|bingpreview|facebookexternalhit|headless|lighthouse|pingdom|uptime/i;

async function logLanding(request, env, url) {
  if (!env || !env.SUPABASE_URL || !env.SUPABASE_ANON_KEY) return;

  // Same exclusions the app applies to itself. ?test=1 is the developer flag;
  // src=selftest is the production-verification tag. A bare "/" cannot carry
  // either (a query string routes to the app), so this only ever fires for
  // /welcome -- but the check stays, because the routing rule is one edit away
  // from changing and this is the row nobody would notice was wrong.
  const src = url.searchParams.get("src");
  if (url.searchParams.get("test") === "1" || src === "selftest") return;
  if (BOT_UA.test(request.headers.get("user-agent") || "")) return;

  // The event:k=v shape analyze_survey.py already parses. path= separates a
  // typed bare domain from a clicked marketing link, which the src tag alone
  // cannot: a /welcome hit whose tag went missing is a different story from
  // someone who typed the name.
  const action = `${LANDING_ACTION}:path=${url.pathname === "/" ? "root" : "welcome"}`;

  // UTC, not visitor-local. isoformat() in the app emits an offset and this
  // emits Z; both are correct absolute instants, which is all daily bucketing
  // needs (traffic_report_dates converts before it buckets). The edge knows
  // the visitor's timezone but constructing a local ISO string from an IANA
  // name here would be guesswork for no gain.
  //
  // session_id is NULL because an edge landing HAS no session -- no Streamlit
  // run, no browser state. CLAUDE.md's rule for NULL session_id is "exclude
  // from joins rather than treat as one shared session", which is exactly the
  // right treatment here.
  const row = {
    timestamp: new Date().toISOString(),
    session_id: null,
    traffic_source: src || null,
    action,
  };

  try {
    await fetch(`${env.SUPABASE_URL}/rest/v1/usage_logs`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        apikey: env.SUPABASE_ANON_KEY,
        authorization: `Bearer ${env.SUPABASE_ANON_KEY}`,
        prefer: "return=minimal",
      },
      body: JSON.stringify(row),
      signal: AbortSignal.timeout(LANDING_LOG_TIMEOUT_MS),
    });
  } catch (err) {
    // Swallowed deliberately: the response has already been returned, and a
    // database that is slow or down must not turn into a Worker exception.
  }
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // 1. Host + scheme canonicalization, path and query preserved.
    if (url.hostname !== "worthmydegree.com" || url.protocol === "http:") {
      url.hostname = "worthmydegree.com";
      url.protocol = "https:";
      return Response.redirect(url.toString(), 301);
    }

    // 2. Edge-served text artifacts.
    const text = TEXT_FILES[url.pathname];
    if (text) {
      return new Response(text[1], {
        headers: {
          "content-type": `${text[0]}; charset=utf-8`,
          "cache-control": "public, max-age=3600",
        },
      });
    }

    // 2b. The static landing page -- THE FRONT DOOR since 2026-08-11.
    //
    // A bare, parameter-less "/" serves it: someone typing the domain gets
    // the instant edge page, and "Open the calculator" is one click. Any
    // query string at all goes straight to the app instead, which is what
    // keeps every existing link working unchanged: share links, ?src= tags,
    // ?tool= pages and ?admin= all carry params by construction. The
    // landing's own CTAs point at "/?go=1" -- a marker with no meaning to
    // the app -- precisely so a click-through has a param and cannot loop
    // back here.
    //
    // /welcome stays as an alias (marketing links already point there, WITH
    // src params, and must keep landing here rather than falling through to
    // the app). Both spellings canonicalize to "/", so search engines see
    // one page.
    //
    // CONSEQUENCE FOR THE RESEARCH DATA, recorded in migrations.sql: from
    // this date the app's `pageview` rows are clicked-through visitors, not
    // raw arrivals -- organic traffic gained one click of funnel.
    const isLanding =
      (url.pathname === "/" && url.search === "") ||
      url.pathname === "/welcome" || url.pathname === "/welcome/";
    if (isLanding) {
      const page = new Response(LANDING, {
        headers: {
          "content-type": "text/html; charset=utf-8",
          "cache-control": "public, max-age=3600",
          "link": `<${CANON}/>; rel="canonical"`,
        },
      });
      // AFTER the response. waitUntil keeps the invocation alive for the
      // insert without delaying a single byte to the visitor.
      if (ctx && typeof ctx.waitUntil === "function") {
        ctx.waitUntil(logLanding(request, env, url));
      }
      return page;
    }

    // 3. Streamlit internals: passthrough, cached at the edge where safe.
    if (PASSTHROUGH.some((p) => url.pathname.startsWith(p))) {
      // The websocket must stay a bare fetch: wrapping it or buffering its
      // response is how you hang every session at once, per the note on
      // step 5. Everything else gets a guard so a dead origin during a spike
      // degrades to a clean 503 instead of Cloudflare's own 1101 error page.
      // A bare 503, not BUSY_PAGE -- these are asset requests, and an HTML
      // apology where a script was expected is a second error, not a message.
      if (url.pathname.startsWith("/_stcore")) {
        return fetch(request);
      }
      const cached = EDGE_CACHED.find((p) => url.pathname.startsWith(p.prefix));
      try {
        const resp = await fetch(request, cached ? {
          cf: { cacheEverything: true, cacheTtl: cached.ttl },
        } : undefined);
        if (cached && resp.ok) {
          // Assert the policy on the response too, so the browser holds the
          // asset as long as the edge does. Only on 2xx: caching an origin
          // error would outlive the outage that caused it.
          const out = new Response(resp.body, resp);
          out.headers.set("cache-control",
            `public, max-age=${cached.ttl}` +
            (cached.immutable ? ", immutable" : ""));
          return out;
        }
        return resp;
      } catch (err) {
        return new Response(null, {
          status: 503,
          headers: { "retry-after": "60", "cache-control": "no-store" },
        });
      }
    }

    // 4. The app has exactly one route; fold stray paths into it.
    if (url.pathname !== "/") {
      return Response.redirect(CANON + "/" + url.search, 301);
    }

    // 5. Serve the shell with canonical + JSON-LD + description injected.
    //
    // The origin call is guarded from here down. A spike shows up as a 5xx or
    // a throw, and either one used to become Streamlit's bare spinner; now it
    // becomes a page that says what happened. Only the HTML DOCUMENT is
    // substituted -- /_stcore returned above, untouched, because breaking the
    // websocket is how you hang a session that would otherwise have recovered.
    let resp;
    try {
      resp = await fetch(request);
    } catch (err) {
      return busyResponse();
    }
    if (resp.status >= 500) return busyResponse();
    const ctype = resp.headers.get("content-type") || "";
    if (!ctype.includes("text/html")) return resp;

    const canonical = canonicalFor(url);
    const rewritten = new HTMLRewriter()
      .on("head", {
        element(head) {
          head.append(`<link rel="canonical" href="${canonical}">`, { html: true });
          head.append(`<meta name="description" content="${META_DESC}">`, { html: true });
          // The social preview card. og:image points at the feature graphic
          // served from /app/static (see static/README.md -- renaming that
          // file breaks this tag). Every link posted to FB/LinkedIn/Slack
          // renders this card, whether or not the poster attached anything.
          head.append(`<meta property="og:type" content="website">`, { html: true });
          head.append(`<meta property="og:title" content="Is the degree worth the loan? — worthmydegree.com">`, { html: true });
          head.append(`<meta property="og:description" content="${META_DESC}">`, { html: true });
          head.append(`<meta property="og:url" content="${canonical}">`, { html: true });
          head.append(`<meta property="og:image" content="${CANON}/app/static/feature-og-1200x630.png">`, { html: true });
          head.append(`<meta property="og:image:width" content="1200">`, { html: true });
          head.append(`<meta property="og:image:height" content="630">`, { html: true });
          head.append(`<meta name="twitter:card" content="summary_large_image">`, { html: true });
          head.append(`<meta name="twitter:image" content="${CANON}/app/static/feature-og-1200x630.png">`, { html: true });
          head.append(`<script type="application/ld+json">${JSONLD}</script>`, { html: true });
        },
      })
      .transform(resp);

    const headers = new Headers(rewritten.headers);
    headers.append("Link", `<${canonical}>; rel="canonical"`);
    return new Response(rewritten.body, { status: rewritten.status, headers });
  },
};

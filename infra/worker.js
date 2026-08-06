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

const TEXT_FILES = {
  "/robots.txt": ["text/plain", ROBOTS],
  "/sitemap.xml": ["application/xml", SITEMAP],
  "/llms.txt": ["text/markdown", LLMS],
};

// Streamlit-owned path prefixes that must reach the origin unmodified.
// /_stcore carries the websocket (the app's entire runtime) and the
// health endpoint Railway checks.
const PASSTHROUGH = ["/_stcore", "/static", "/component", "/media", "/vendor", "/favicon"];

export default {
  async fetch(request) {
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

    // 3. Streamlit internals: untouched passthrough.
    if (PASSTHROUGH.some((p) => url.pathname.startsWith(p))) {
      return fetch(request);
    }

    // 4. The app has exactly one route; fold stray paths into it.
    if (url.pathname !== "/") {
      return Response.redirect(CANON + "/" + url.search, 301);
    }

    // 5. Serve the shell with canonical + JSON-LD + description injected.
    const resp = await fetch(request);
    const ctype = resp.headers.get("content-type") || "";
    if (!ctype.includes("text/html")) return resp;

    const canonical = canonicalFor(url);
    const rewritten = new HTMLRewriter()
      .on("head", {
        element(head) {
          head.append(`<link rel="canonical" href="${canonical}">`, { html: true });
          head.append(`<meta name="description" content="${META_DESC}">`, { html: true });
          head.append(`<script type="application/ld+json">${JSONLD}</script>`, { html: true });
        },
      })
      .transform(resp);

    const headers = new Headers(rewritten.headers);
    headers.append("Link", `<${canonical}>; rel="canonical"`);
    return new Response(rewritten.body, { status: rewritten.status, headers });
  },
};

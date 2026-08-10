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
const PASSTHROUGH = ["/_stcore", "/static", "/component", "/media", "/vendor", "/favicon"];

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
          head.append(`<script type="application/ld+json">${JSONLD}</script>`, { html: true });
        },
      })
      .transform(resp);

    const headers = new Headers(rewritten.headers);
    headers.append("Link", `<${canonical}>; rel="canonical"`);
    return new Response(rewritten.body, { status: rewritten.status, headers });
  },
};

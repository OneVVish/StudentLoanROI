# SEO / AI-optimization edge layer — deploy & verify runbook

worthmydegree.com is a Streamlit SPA on Railway. Streamlit cannot serve
root-path files or inject `<head>` tags, so canonical signals, robots.txt,
sitemap.xml, and llms.txt are all delivered by ONE Cloudflare Worker
(`infra/worker.js`) running in front of the Railway origin. This file is
the runbook for deploying and verifying it.

## Phase A — Cloudflare zone prep (dashboard, do in a quiet hour)

1. DNS → set `@` and `www` to **Proxied** (orange cloud). Targets stay the
   Railway CNAMEs. Email records untouched. Expect a ~15–30 min edge-cert
   window during which HTTPS may fail for some visitors (measured twice on
   this account).
2. SSL/TLS → overview → **Full (strict)**. Edge Certificates → **Always
   Use HTTPS** on. (HSTS only AFTER Phase E passes: max-age 6 months, no
   includeSubDomains, no preload.)
3. **AI Crawl Control → allow AI crawlers.** Cloudflare's new-zone default
   BLOCKS them (observed on this account: automated fetchers got 403).
   Leaving it on defeats the whole project. Also Security → Bots →
   **Bot Fight Mode OFF**.
4. Network → WebSockets ON (default).

Rollback: grey-cloud both records — instant return to direct-to-Railway.

## Phase C — Deploy the Worker

1. Workers & Pages → Create → name `wmd-edge` → paste `infra/worker.js`
   → Deploy.
2. Worker → Settings → Domains & Routes → add routes
   `worthmydegree.com/*` and `www.worthmydegree.com/*` (zone
   worthmydegree.com).
3. Run the verification set below immediately. Rollback: delete the two
   routes (Worker stays deployed, stops receiving traffic).

## Phase D — Index registration

1. Google Search Console → add **domain property** `worthmydegree.com` →
   verify via DNS TXT. Done 2026-08-06 using the **manual "Any DNS
   provider" TXT path**, deliberately NOT the one-click Cloudflare
   integration (that flow grants Google OAuth access to the Cloudflare
   account). The record is a root TXT
   `google-site-verification=...` on worthmydegree.com — **do not delete
   it**; GSC re-checks it periodically and ownership lapses without it.
2. Sitemaps → submit `https://worthmydegree.com/sitemap.xml`. A fresh
   property shows **"Couldn't fetch" with an empty Last-read — that is a
   placeholder, not a failure** (observed here: URL Inspection already
   listed the sitemap as the discovery source while the Sitemaps page
   still said Couldn't fetch). Confirm the sitemap serves in a browser
   and re-check GSC in a day before debugging anything.
3. URL-inspect `/` and all three `?tool=` URLs → Request indexing (all three
   queued 2026-08-06). The inspect search box sometimes keeps the prior
   inspection — check the breadcrumb names the URL you typed before
   trusting the panel.
4. Bing Webmaster Tools → sign in → Import from Search Console (an OAuth
   grant against the Google account — user-performed).

## Phase E — Verification

Note: run from a network that can reach worthmydegree.com — some
security appliances block newly registered domains; a neutral vantage
(phone hotspot, or a fetch proxy) works.

```bash
# Host + scheme canonicalization
curl -sI "https://www.worthmydegree.com/?src=x" | grep -iE "^(HTTP|location)"  # 301 → apex, ?src=x preserved
curl -sI "http://worthmydegree.com/" | grep -iE "^(HTTP|location)"             # 301 → https

# Edge-served text artifacts
curl -s https://worthmydegree.com/robots.txt | head -5
curl -s https://worthmydegree.com/sitemap.xml | head -3
curl -s https://worthmydegree.com/llms.txt | head -5

# Canonical signals: header AND tag; tracking params stripped from the value
curl -sI "https://worthmydegree.com/?src=img" | grep -i "^link:"
curl -s  "https://worthmydegree.com/?tool=repayment&src=re" | grep -o '<link rel="canonical"[^>]*>'
curl -s https://worthmydegree.com/ | grep -c "application/ld+json"             # exactly 1

# AI crawlers NOT blocked (regression test for Phase A step 3)
curl -s -o /dev/null -w "%{http_code}\n" -A "GPTBot/1.0" https://worthmydegree.com/robots.txt      # 200
curl -s -o /dev/null -w "%{http_code}\n" -A "ClaudeBot/1.0" https://worthmydegree.com/             # 200
curl -s -o /dev/null -w "%{http_code}\n" -A "PerplexityBot/1.0" https://worthmydegree.com/llms.txt # 200

# Stray path folds into the single route
curl -sI "https://worthmydegree.com/anything?src=x" | grep -i location         # → /?src=x
```

Browser checks (the part curl can't prove):
- `https://worthmydegree.com/?test=1` renders and widgets respond — this
  is the websocket surviving the Worker, the single most important check.
- `?tool=repayment&test=1` renders; a share link round-trips.
- Paste `https://worthmydegree.com/` into validator.schema.org — both
  Organization and WebApplication parse clean.

After a few days: GSC URL Inspection shows the user-declared canonical;
asking Perplexity/ChatGPT about worthmydegree.com returns fetched content.

## Invariants (do not break)

- The Worker NEVER rewrites request URLs or bodies — only response heads.
  `src`/`test`/share params must reach Streamlit exactly as sent.
- `/_stcore` passthrough is load-bearing: it carries the websocket (the
  entire app runtime) and Railway's health check path.
- robots.txt Disallow lines cover `admin=`, `test=`, `research=` — the
  three params that must never be crawled into an index.
- The served strings in worker.js and the reference copies
  (infra/robots.txt, sitemap.xml, llms.txt) change together, same PR.

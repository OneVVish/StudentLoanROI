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

**Updating an already-deployed Worker (the normal case):**

```bash
npx wrangler login     # once per machine; opens a browser, riteshvk@gmail.com
npx wrangler deploy    # from the repo root, reads ./wrangler.toml
```

`wrangler.toml` declares the script and nothing else — **routes are not
managed from the repo**, deliberately, so a bad deploy can only serve wrong
code on the right routes. Rollback is Worker → Deployments → revert, one
click, no route surgery.

Two things to check before the FIRST wrangler deploy, because they are the
only ways this path differs from the dashboard one:

- Worker → Settings → **Runtime**: if the deployed compatibility date is not
  the `compatibility_date` in `wrangler.toml`, put the deployed one in the
  file first. Changing runtime semantics in the same deploy as a code change
  makes any regression impossible to attribute.
- The deploy output must name **`wmd-edge`**. A typo in `name` creates a NEW
  Worker with no routes, which looks like a successful deploy and changes
  nothing — the live site keeps serving the old code.

**First-time setup, or a rebuild from scratch (dashboard):**


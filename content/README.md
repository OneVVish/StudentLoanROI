# Guides — how to publish one

> Working with Claude Code? Just say **`/publish-guide`** (or "write a guide
> about X") — `.claude/skills/publish-guide/` carries this whole workflow,
> including the rule that every number must come from the datasets rather
> than from memory. This file is the human version of the same thing.

```bash
$EDITOR content/posts/my-new-guide.md       # write it
python3 infra/build_site.py --preview       # LOOK at it -- writes nothing
python3 infra/build_site.py                 # build pages + sitemap + worker
python3 check_content.py                    # will refuse anything broken
git add -A && git commit && git push        # merge, then: npx wrangler deploy
```

**Preview before you build.** `--preview` renders through the same code the real
build uses and serves it on `localhost:8787`, but writes nothing at all: no
`landing.html`, no `worker.js` injection, no sitemap. Building is what arms the
next `wrangler deploy`, so previewing first keeps "let me look at it" and "ship
it" as separate acts. It maps `/app/static/*` to the real `static/` directory,
so inline images resolve exactly as they will in production.

The filename is the URL: `my-new-guide.md` → `worthmydegree.com/guides/my-new-guide`.

## Front matter (all four required)

```markdown
---
title: The headline, also the tab title and the shared card
description: One sentence for search results and the preview card (<200 chars)
summary: One line for the guide cards on the landing page and the index
date: 2026-08-11
image: borrowing-1080x1350.png     # optional; must exist in static/
---
```

## The Markdown subset

There is **no Markdown library** in this project — `requirements.txt` pins what
production runs, and the build scripts use only those. `infra/build_site.py`
renders a deliberate subset instead:

```
# ## ###      headings          - item        bullet list
**bold**  *italic*  `code`      > quote       blockquote
[text](url)                     | a | b |     table (with a --- row)
![alt](file.png)                ---           horizontal rule
```

Anything else — ordered lists, nested lists, fenced code, raw HTML — is a
**build error**, not a silently mis-rendered paragraph. `check_content.py`
names the file, the line and the construct.

A bullet **may** wrap across lines; indented continuation lines fold into the
item above. That was not always true. Before 2026-08-12 the list loop stopped at
the continuation, closed the `<ul>`, rendered the rest as its own paragraph and
opened a second list for the next bullet — one list became two with a stray
sentence between them. It produced valid HTML and a plausible page, `check_content.py`
passed it (verified), and only the preview showed it. If you add a construct to
the subset, check that the guard rejects the old form, or the next one fails the
same silent way.

## Rules worth knowing

- **Images live in `static/`**, referenced by bare filename. The guard fails if
  the file is not there, because a missing preview image is a broken thumbnail
  on someone else's timeline and you would never see it.
- **Every guide ends at the calculator.** The CTA carries `from=guide`, so the
  click is attributed. The guard fails a page without one — an article that
  cannot send anyone anywhere is a dead end.
- **Reads and likes are counted at the edge**, by the Worker, and shown on the
  admin page. Neither is a measurement of people: an edge read has no session,
  and a like has no identity at all. They are warm signals for what to write
  next.
- **The sitemap regenerates from the posts.** Deleting a post removes its URL,
  which is right — a sitemap listing a 301 is worse than one listing nothing.

## Where each piece ends up

| Thing | Built into |
|---|---|
| Article page | `GUIDES` map in `infra/worker.js` (+ `infra/guides/*.html` reference copies) |
| Guide cards on the landing page | `LANDING` constant, newest 4 |
| `/guides` index | same map |
| Sitemap URLs | both halves, between the `<!--GUIDES-->` markers |

Nothing is live until `npx wrangler deploy`.

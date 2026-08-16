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
hero: guide-hero-<slug>-klein4b.png   # optional; the article's opening photo
card: some-other.png               # optional; overrides hero for card + og
---
```

### The `hero:` picture

`hero:` is the photograph at the top of the article and the picture on the
guide's card in the index. Generate one with `python3 brand/build_ai_hero.py
--slug <slug>`; the build resizes it into the two sizes a browser actually
draws (a 1360px article JPEG and a 720px card JPEG) and commits them beside
the original, so nothing serves the ~1 MB PNG.

Three things to know before writing one:

- **The index is all or nothing.** The cards show pictures only when *every*
  published guide has a hero. A grid where some cards carry a photograph and
  some do not does not read as a few missing pictures, it reads as a broken
  page. So a new guide without a hero silently turns the pictures off for all
  of them.
- **No words in the picture.** The headline is live text below it. Baked-in
  text scales with the column instead of reflowing, and diffusion models
  render letterforms badly.
- **No tint, ever.** The photograph is shown as itself: no scrim, no gradient,
  no filter. Contrast is a question about the type below the picture, not
  about the picture. See the note in `infra/build_site.py`'s `ARTICLE_CSS`.

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
- **Reads, likes and shares are counted at the edge**, by the Worker, and shown
  on the admin page. None is a measurement of people: an edge read has no
  session, and a like has no identity at all. They are warm signals for what to
  write next.
- **Every guide carries a Share button**, right-justified in the reactions bar
  at the foot of the article. It offers the phone's own share sheet where one
  exists and copies the link everywhere else. It shares the **canonical** URL,
  never the address bar: a reader who arrived on `?src=<channel>` would
  otherwise stamp the recipient with the sharer's recruitment tag, which is the
  fabricated attribution that keeps `src` out of the app's share links too.
- **A share is the softest number on that page**, softer than a like. It is
  written when the share sheet resolves or the link reaches the clipboard, and
  neither proves anyone received it; a canceled sheet writes nothing; there is
  no dedupe, so one enthusiastic reader is several rows. Upper bound on intent,
  never a rate. `migrations.sql` carries the full list of caveats.
- **The sitemap regenerates from the posts.** Deleting a post removes its URL,
  which is right — a sitemap listing a 301 is worse than one listing nothing.

## How the prose reads

House style, arrived at by writing four of these. None of it is enforced by a
guard, so it is written down instead.

- **American English.** math, modeling, license, canceled, toward. The audience
  is American families; a British spelling reads as a page written by somebody
  who is not from here, which is the last impression a money article wants.
- **No dashes as punctuation.** Not the em dash, not the en dash, not a spaced
  hyphen. Use a period, a comma or a colon. Hyphens inside words are fine and
  necessary: four-year, in-state, ten-year. This is the visitor-facing half of
  the same rule the app follows, and the reason is the audience: a sentence
  holding two nested dash asides makes a seventeen-year-old parse an
  interruption while keeping a clause open.
- **Almost no contractions.** The published guides run about one per 1,400
  words. It is not a ban, it is a register. Reaching for contractions to sound
  friendlier makes a page sound like a different writer.
- **Vary how paragraphs open.** Five paragraphs in a row each leading with a
  bolded thesis is a template, not a voice, and it reads as machine-written
  even when every sentence in it is true.
- **Name the parties, not the pronouns.** The community college guide ended a
  paragraph with "They can answer it and this cannot", meaning the colleges and
  the calculator, and saying neither. A compressed closing line sounds decisive
  and communicates nothing; if a sentence carries `this`, `they` or `it` across
  a paragraph break, spell out what each one is.
- **When you add a paragraph, reread its neighbours.** Adding the "community
  college gets talked about as a fallback" paragraph left two consecutive
  paragraphs both declining the same argument. Insertions are where redundancy
  gets in, because the new text is the part you are reading closely.

## What the guides will not claim

The temptation is always to answer the question the reader is actually asking.
Some of those questions this app cannot answer, and a guide that answers them
anyway spends the credibility the rest of the page depends on.

- **Nothing about outcomes by institution.** `SCOPE.md` names this a real gap:
  modelled salary does not vary by school except through a thin prestige
  multiplier. So a guide can compare schools on COST and must not imply the
  degree is worth more or less for having come from one of them.
- **No verdict on a perception, however obvious the verdict feels.** The
  community college guide nearly opened by calling the "that is for people who
  did not make it" idea wrong. That is a claim about outcomes, the repo cannot
  support it, and the one piece of outcome evidence it does hold (NBER on
  community college bachelor's graduates) points mildly the other way. The
  version that shipped names the perception and what believing it costs, and
  leaves the judgement to the reader.
- **No advice.** `SCOPE.md` puts "should I do this" out of scope, and the
  counselor guide ends "None of those three was told what to do." Price the
  trade, then stop.
- **Say what is missing rather than letting silence imply zero.** The community
  college guide cannot price credits that fail to transfer or students who
  never transfer at all, so it says so and sends the reader to the two
  admissions offices that can answer it.

## Numbers in the prose

- **Every figure comes from a dataset in this repo, never from memory.** This
  is the rule the whole content pipeline exists to protect. Compute it, do not
  recall it, and re-derive it from the datasets before publishing rather than
  trusting the draft.
- **Round the money, and hedge every rounded figure**: *a bit under $18,000*,
  *roughly $10,000*, *nearly $38,000 ahead*. An unhedged $17,725 claims a
  precision the median of 1,797 colleges does not have, and it reads like a
  machine that has not thought about what the number is for.
- **In a table, mark the rounding once or mark it per cell, and pick one per
  guide.** Both are in use and both are right in their place:

  - **Per cell, `~$71,000`.** The default for a short table, and REQUIRED
    whenever rounded and exact figures share one. The tilde is then the only
    thing separating them, and a reader can see which is which without leaving
    the row. The community college guide's three-row cost table works this way.
    The renderer passes `~` through untouched, including inside `**bold**`.
  - **Once, in a sentence near the top.** Better when a guide runs several
    money tables, where a tilde on every cell stops being a signal and becomes
    texture: the repayment guide carries thirteen money cells across three
    tables. If you take this route the sentence MUST carve out the exact
    figures, because a blanket "amounts are rounded" makes a statutory number
    in the same table look like an estimate. That guide's note names the $50
    dependent reduction and the $10 minimum for exactly this reason, and
    without the carve-out the $10 in its last row would read as approximate.

  Do not mix the two inside one guide. A table with tildes beside a table
  without them reads as a mistake in the second one.
- **Statutory figures stay exact.** $5,500, $7,500, the $20,000 and $65,000
  PLUS caps, the $27,000 aggregate. Those are legal ceilings rather than
  estimates, and "about $65,000" is simply false: it is $65,000, or you are
  over the limit. The contrast is useful in itself, because it lets a reader
  feel which numbers are hard and which are typical.
- **Rounded figures must still add up on the page.** Round each one
  independently and the differences a reader can check stop working, which
  looks like carelessness rather than approximation. In the community college
  guide $71,000 minus $43,000 is the $28,000 the text claims, and $44,000 minus
  $16,000 is the same $28,000 arriving from the other direction. Check the
  arithmetic on the ROUNDED numbers before publishing, not just the exact ones.

## Where each piece ends up

| Thing | Built into |
|---|---|
| Article page | `GUIDES` map in `infra/worker.js` (+ `infra/guides/*.html` reference copies) |
| Guide cards on the landing page | `LANDING` constant, newest 4 |
| `/guides` index | same map |
| Sitemap URLs | both halves, between the `<!--GUIDES-->` markers |

Nothing is live until `npx wrangler deploy`.

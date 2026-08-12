---
name: publish-guide
description: Write and publish a guide article to worthmydegree.com/guides. Use when asked to write, draft, or publish an article, blog post, guide, or explainer for the site. Handles the Markdown subset, verified numbers, the build, the guard, the visual check, and the deploy reminder.
---

# Publish a guide

Guides are static HTML served from the Cloudflare Worker at
`worthmydegree.com/guides/<slug>`. They are the only indexable content this
project has — the Streamlit app is one URL to a search engine — so each one
exists to answer a question someone types, and to end at the calculator.

## The pipeline

```bash
$EDITOR content/posts/<slug>.md     # 1. write
python3 infra/build_site.py         # 2. build pages + index + sitemap + worker
python3 check_content.py            # 3. guard (refuses anything broken)
                                    # 4. LOOK at it (see below)
                                    # 5. commit, PR, then: npx wrangler deploy
```

Filename is the URL. `parent-plus-caps.md` → `/guides/parent-plus-caps`.

## 1. Every number comes from the data, never from memory

This is the rule the whole project is built on and it is not negotiable in an
article, which is the artifact strangers read without the app around it.

Before writing a figure, get it from the source:

```python
# app constants (caps, limits, program lengths): exec the section 1-2 prefix
src = open("app.py").read()
cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
ns = {"__name__": "guide"}
exec(compile(src[:src.rindex("# " + "=" * 60, 0, cut)], "app.py", "exec"), ns)
ns["PARENT_PLUS_AGGREGATE_LIMIT"]        # 65000
ns["federal_direct_cap"](schedule, "dependent")

# school / wage / debt figures: read the committed CSVs
pd.read_csv("data/college_coa_clean.csv")     # 5,035 schools, in_state_coa
pd.read_csv("cleaned_careers.csv")            # 825 occupations, wages
pd.read_csv("data/graduate_debt_clean.csv")   # per-school graduate debt
```

Put the date you checked into the draft's own notes. If a figure cannot be
traced to a dataset or a cited federal source, cut the sentence.

## 2. Front matter — all four required

```markdown
---
title: The headline, also the tab title and the shared card
description: One sentence for search results and the preview card (under 200 chars)
summary: One line for the guide cards on the landing page and the index
date: 2026-08-11
image: borrowing-1080x1350.png     # optional; MUST already exist in static/
---
```

`check_content.py` fails a missing or stubby field. It is checking the things
an author never sees while writing: the browser tab, the search listing, the
card someone else's timeline renders.

## 3. The Markdown subset is small and enforced

There is no Markdown library in this project — `requirements.txt` pins what
production runs, and build scripts use only those. `infra/build_site.py`
renders a deliberate subset:

```
# ## ###      headings          - item        bullet list
**bold**  *italic*  `code`      > quote       blockquote
[text](url)                     | a | b |     table (needs a --- separator row)
![alt](file.png)                ---           horizontal rule
```

**Not supported, and a build error rather than a silent mis-render:** ordered
lists, nested/indented lists, fenced code blocks, raw HTML, `*` bullets,
setext headings. The guard names the file, the line and the construct.

Images are referenced by bare filename and must exist in `static/`. If you
generate a new one in `brand/`, copy it to `static/` in the same commit —
they do not sync (see `static/README.md`).

## 4. Structural rules

- **Every guide ends at the calculator.** The template already emits a CTA
  carrying `from=guide`; the guard fails a page without one. An article that
  sends nobody anywhere is a dead end and cannot be credited with anything.
- **The sitemap regenerates from the posts** — deleting a post removes its
  URL. Never hand-edit sitemap entries.
- **Reads and likes are counted at the edge** and appear on the admin page.
  Neither is a measurement of people: an edge read has no session, and a like
  has no identity at all. Never write copy, or an analysis, that treats them
  as one.

## 5. Look at it before shipping

The guard checks structure, not rendering. Serve and open the built page:

```bash
python3 -m http.server 8777
# then open infra/guides/<slug>.html in a browser and read it
```

Inline images will show as broken locally — `/app/static/...` only resolves in
production. That is expected, not a fault.

## 6. Voice

Match the existing guide (`content/posts/parent-plus-senior-year.md`):

- Lead with the reader's problem, not the product.
- One idea per section, short sections, concrete numbers.
- Name the thing that is *not* obvious — the senior-year cliff, the gap the
  sticker price hides. If the article contains nothing a careful parent could
  not have worked out, it is not worth publishing.
- No hype, no urgency, no "unlock". The tone is a well-informed friend who
  did the arithmetic.
- Mention the calculator once, at the end, as the way to run their own
  numbers.

## 7. Ship it

```bash
git checkout -b guide/<slug>
git add content/ infra/ static/
git commit          # say what the article claims and where the numbers came from
gh pr create
```

After merge the user must run `npx wrangler deploy` — guides live in the
Worker, so nothing is live until then. Say so explicitly in the handoff; it is
the step that is easy to forget and invisible when missed.

If the article introduces a new kind of logged event or changes what an
existing number means, add a dated note to `migrations.sql`.

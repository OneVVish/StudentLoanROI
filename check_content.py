#!/usr/bin/env python3
"""Guard: every published guide is complete, renderable, and reachable.

    python3 check_content.py     (exit 1 on a violation)

WHY THIS EXISTS. Guides are the one artifact strangers read without the app
around them, and every way they break is quiet:

  * A post missing front matter renders a page with an empty <title>, no
    description and no preview card. It looks fine to the author, who never
    sees the tab or the shared link.
  * The Markdown renderer in infra/build_site.py supports a deliberate SUBSET
    (no library is available -- see its docstring). An unsupported construct
    does not raise; it comes out as literal asterisks or a dangling pipe in
    the middle of a published article.
  * A slug that is not URL-safe produces a page the Worker can serve and
    nothing can link to.
  * A post whose URL is missing from the sitemap is invisible to search, which
    is the entire reason these pages exist rather than being app screens.
  * The like endpoint validates against the GUIDES map. A slug mismatch
    between the built page and that map makes the button 404 forever, with a
    working-looking button on a working-looking page.

The sitemap regeneration is checked too, because it has already gone wrong
once: a single-marker regex with DOTALL swept up the three static tool URLs
that followed it and deleted them, leaving a shorter, perfectly valid sitemap.
Paired markers fixed it; this asserts the result rather than the mechanism.
"""
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Constructs the renderer cannot handle. Each would render as literal text in
# a published article rather than raising, which is why they are named here.
UNSUPPORTED = (
    (re.compile(r"^\s{2,}[-*+] "), "indented/nested list"),
    (re.compile(r"^\d+\. "), "ordered list"),
    (re.compile(r"^```"), "fenced code block"),
    (re.compile(r"^\s*<[a-zA-Z/]"), "raw HTML"),
    (re.compile(r"^\* "), "* bullet (use - )"),
    (re.compile(r"^={3,}$|^-{3,}$(?<!^---$)"), "setext heading"),
)

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# A line from CARRY_QS_JS, quoted here rather than imported from the builder --
# reading the constant under test would only assert that it equals itself, the
# same flaw recorded against the first versions of check_chart_axes and the
# residency guard. Deleting the script from a template must fail this.
#
# It exists because the landing page carried this from the start and the guide
# templates shipped without it (fixed 2026-08-11). Nothing looked wrong: the
# guide read was logged WITH its src, and only the click into the app lost the
# tag -- so the funnel showed reads and no conversions, which reads as a
# content problem rather than a measurement one.
# Updated 2026-08-18 when the script learned to put the query BEFORE the
# fragment. The marker deliberately spans the "+ hash" tail as well as the
# append, so reverting to the old blind `a.href += ... + qs` fails this too --
# that version silently ate the carried value into the fragment on any
# fragment-bearing link, which the landing's infographic cards are the first of.
CARRY_MARKER = 'href + (href.indexOf("?") === -1 ? "?" : "&") + qs + hash);'


def load_builder():
    spec = importlib.util.spec_from_file_location(
        "build_site", ROOT / "infra" / "build_site.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    builder = load_builder()
    problems = []
    fail = problems.append

    posts = builder.load_posts()
    if not posts:
        print("content OK -- no guides published yet (nothing to check).")
        return 0

    worker = (ROOT / "infra" / "worker.js").read_text()
    sitemap = (ROOT / "infra" / "sitemap.xml").read_text()
    static_dir = ROOT / "static"

    seen_slugs = set()
    for post in posts:
        slug = post["slug"]
        where = f"content/posts/{slug}.md"

        if not SLUG_RE.match(slug):
            fail(f"{where}: slug {slug!r} is not URL-safe -- use "
                 f"lowercase-words-with-hyphens")
        if slug in seen_slugs:
            fail(f"{where}: duplicate slug {slug!r} -- one would silently "
                 f"overwrite the other in the GUIDES map")
        seen_slugs.add(slug)

        if not DATE_RE.match(post["date"]):
            fail(f"{where}: date {post['date']!r} is not YYYY-MM-DD -- posts "
                 f"sort on this string, so a malformed one sorts wrong "
                 f"without erroring")

        for field in ("title", "description", "summary"):
            value = post.get(field, "")
            if len(value) < 10:
                fail(f"{where}: {field} is missing or too short "
                     f"({value!r}) -- it reaches the tab, the search result "
                     f"and the shared card")
        if len(post.get("description", "")) > 200:
            fail(f"{where}: description is {len(post['description'])} chars; "
                 f"search results truncate around 160")

        # The preview image must actually be servable, or the shared card is
        # a broken image on someone else's timeline.
        image = post.get("image", "feature-og-1200x630.png")
        if not (static_dir / image).exists():
            fail(f"{where}: image {image!r} is not in static/ -- the preview "
                 f"card would 404 (copy it there; see static/README.md)")

        # Inline images resolve against /app/static/ too.
        for ref in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", post["body"]):
            if not (static_dir / ref).exists():
                fail(f"{where}: inline image {ref!r} is not in static/")

        for i, line in enumerate(post["body"].splitlines(), 1):
            for pattern, name in UNSUPPORTED:
                if pattern.match(line):
                    fail(f"{where}:{i}: {name} is not in the supported "
                         f"Markdown subset -- it would render as literal "
                         f"text. Supported:{builder.SUPPORTED_MARKDOWN}")
                    break

        # Reachability: built into the Worker, listed in the sitemap.
        if f'"/guides/{slug}"' not in worker:
            fail(f"{where}: /guides/{slug} is not in the Worker's GUIDES map "
                 f"-- re-run python3 infra/build_site.py")
        if f"/guides/{slug}</loc>" not in sitemap:
            fail(f"{where}: /guides/{slug} is missing from infra/sitemap.xml "
                 f"-- search engines would never find it")

        # Every guide must send readers somewhere, and tagged, or the article
        # is a dead end that cannot be credited with anything.
        page = builder.build_guide_html(post, "<svg></svg>", "")
        if "from=guide" not in page:
            fail(f"{where}: the built page has no from=guide link -- clicks "
                 f"into the app would not be attributed to the guide")
        if CARRY_MARKER not in page:
            fail(f"{where}: the built page does not carry the query string "
                 f"onto its internal links -- a visitor arriving on "
                 f"?src=<channel> would read the guide tagged and then land "
                 f"in the calculator as untagged organic traffic. Include "
                 f"CARRY_QS_JS in the template.")

    # ---- The /charts gallery -------------------------------------------
    #
    # Held to the same rules the guides are, because it fails the same quiet
    # way: it renders, it serves, and only a picture, a route or a reaction is
    # missing. The one rule that is STRICTER is the image, which is optional
    # for a post and mandatory here -- a chart with no picture is not a chart.
    charts = builder.load_charts()
    chart_slugs = set()
    for c in charts:
        where = f"content/charts/{c['slug']}.md"
        if not SLUG_RE.match(c["slug"]):
            fail(f"{where}: slug {c['slug']!r} is not URL-safe")
        if c["slug"] in chart_slugs:
            fail(f"{where}: duplicate slug {c['slug']!r}")
        chart_slugs.add(c["slug"])
        if not c.get("source"):
            fail(f"{where}: a chart must name a source -- these are published "
                 f"as standalone pictures and travel without the page")
        # The committed JPEGs, not the gitignored PNG. A clone has the first
        # and not the second, and it is the first the page actually serves.
        for served in (c["full"], c["card"]):
            if not (ROOT / "static" / served).exists():
                fail(f"{where}: static/{served} is missing -- the gallery "
                     f"would render a broken image. Run infra/build_site.py "
                     f"with marketing/infographics/ present to regenerate it.")
    if charts:
        if '"/charts"' not in worker:
            fail("infra/worker.js has no /charts route -- the Worker 301s "
                 "anything not in its page map to /, so the gallery would "
                 "silently redirect to the homepage")
        # Public endpoint: knownSlug rejects anything not in this list, so a
        # slug missing here is a like button that 404s.
        for c in charts:
            if f'"{c["slug"]}"' not in worker:
                fail(f"content/charts/{c['slug']}.md: the slug is not in "
                     f"CHART_SLUGS, so /api/like and /api/share will reject "
                     f"it and the buttons will fail silently")
        if "/charts</loc>" not in sitemap:
            fail("infra/sitemap.xml has no /charts entry")

    # The sitemap regeneration must not eat the static entries (it did once).
    for required in ("https://worthmydegree.com/</loc>",
                     "?tool=repayment</loc>", "?tool=schools</loc>",
                     "?tool=gradschools</loc>"):
        if required not in sitemap:
            fail(f"infra/sitemap.xml lost {required!r} -- the guide "
                 f"regeneration has over-reached and deleted a static URL")

    if problems:
        print(f"content: {len(problems)} problem(s) across {len(posts)} "
              f"guide(s)\n")
        print("\n\n".join(f"  {p}" for p in problems))
        print("\n  A guide fails quietly: it renders, it serves, and only the "
              "tab title,\n  the preview card or the search listing is wrong "
              "-- none of which the\n  author sees while writing it.")
        return 1
    print(f"content OK -- {len(posts)} guide(s) and {len(charts)} chart(s): "
          f"front matter complete,\n  Markdown inside the supported subset, "
          f"images present, slugs URL-safe,\n  every page in the Worker map "
          f"and the sitemap, every chart slug reachable\n  by the reaction endpoints, and the static URLs survived regeneration.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

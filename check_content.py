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
    print(f"content OK -- {len(posts)} guide(s): front matter complete, "
          f"Markdown inside the\n  supported subset, images present, slugs "
          f"URL-safe, every page in the Worker\n  map and the sitemap, and "
          f"the static URLs survived regeneration.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

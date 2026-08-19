#!/usr/bin/env python3
"""Write content/charts/_ranking.json: the infographics, most liked first.

    python3 infra/rank_charts.py            # refresh from Supabase
    python3 infra/rank_charts.py --show     # print counts, write nothing

WHY THIS IS A FILE AND NOT A QUERY. The landing page is baked into
infra/worker.js and served from Cloudflare, and CLAUDE.md records why it holds
no external reference: that is the property which lets it render when the
origin is down, verified at 24,859 bytes in 1 ms with Supabase unreachable.
Reading likes when a visitor loads the page would spend exactly that.

Reading them at BUILD time only moves the dependency. The build would then fail
whenever the database is unreachable, which it was on the day this was written
-- the proxy on this network answers Supabase with CERTIFICATE_VERIFY_FAILED.
A stale ranking is a wrong ORDER; a failed build is no page.

So the order is committed. This script refreshes it deliberately, the diff
shows what moved, and infra/build_site.py falls back to newest-first when the
file is missing or unreadable. See charts_by_rank.

WHAT A LIKE IS, AND IS NOT. usage_logs carries one row per tap of Helpful on
/charts. There is NO per-chart view count, so this is a raw tally with no
denominator: a chart published earlier, or sitting higher in a one-page
gallery, accumulates more. Read the order as "what people tapped", never as
"what is best", and do not put a number on the landing page beside it.
"""
import json
import sys
import tomllib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "content" / "charts" / "_ranking.json"
LIKE_PREFIX = "chart_like"


def like_counts() -> Counter:
    """One tally per slug, from usage_logs. Paged, because PostgREST caps a
    plain select at 1,000 rows and says so only by returning exactly 1,000."""
    from supabase import create_client
    secrets = tomllib.load(open(ROOT / ".streamlit/secrets.toml", "rb"))
    cfg = secrets["connections"]["supabase_connection"]
    client = create_client(cfg["SUPABASE_URL"], cfg["SUPABASE_KEY"])
    rows, start = [], 0
    while True:
        page = (client.table("usage_logs").select("action")
                .like("action", f"{LIKE_PREFIX}%")
                .range(start, start + 999).execute())
        rows += page.data or []
        if not page.data or len(page.data) < 1000:
            break
        start += 1000
    return Counter(r["action"].split("slug=")[-1]
                   for r in rows if "slug=" in r.get("action", ""))


def main() -> int:
    try:
        counts = like_counts()
    except Exception as exc:                       # network, credentials, schema
        print(f"Could not read likes: {type(exc).__name__}: {str(exc)[:120]}")
        print("Nothing written. The build falls back to newest-first, so this "
              "is a stale ranking rather than a broken site.")
        return 1

    if not counts:
        print("No chart likes recorded yet. Nothing written: an empty ranking "
              "and a missing one mean the same thing to charts_by_rank, and "
              "writing one would imply an order the data does not support.")
        return 0

    ordered = [slug for slug, _ in counts.most_common()]
    for slug, n in counts.most_common():
        print(f"  {n:>4}  {slug}")
    if "--show" in sys.argv:
        print("\n--show: nothing written.")
        return 0

    OUT.write_text(json.dumps(
        {"_comment": "Written by infra/rank_charts.py. Raw like tallies with "
                     "no denominator; see that file before reading meaning "
                     "into the order.",
         "by_likes": ordered}, indent=2) + "\n")
    print(f"\nWrote {OUT.relative_to(ROOT)} ({len(ordered)} slugs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

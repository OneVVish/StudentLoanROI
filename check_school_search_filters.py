#!/usr/bin/env python3
"""Guard: the school search's filters narrow the list without changing what it
means, and a wide-open control means no filter at all.

    python3 check_school_search_filters.py      (exit 1 on a violation)

The admit-rate band carries a semantic that reads as an implementation detail
and is not one. `adm_rate_range=None` and an explicit `(0.0, 1.0)` band look
interchangeable -- both "allow every rate" -- and they are not: the explicit
band silently drops the 3,204 of 5,035 rows that report NO admit rate, because
an unknown rate cannot be shown to fall inside a band. Only the None sentinel
keeps them. So the widget sitting at both ends must pass None rather than the
band it is displaying, and anyone "simplifying" that away deletes two thirds of
the dataset from every search that never touched the slider.

That failure is invisible from inside the app. The list still renders, still
sorts by cost, still says a truthful number of schools -- there is simply no
sign of the ones that stopped being eligible, and "no school teaches your field
at that price" is exactly the answer this feature exists to produce. Nothing
raises, so only an assertion about the row COUNT can catch it.

Six properties, each aimed at a distinct way this can regress:

1. **The sentinel.** None is bit-identical to no filter and keeps unrated
   schools; an explicit full band is NOT, and drops them. Asserting both
   directions is the point -- a check that only tested None would pass on an
   implementation that had quietly made them the same thing.
2. **Blanks.** Any narrowing excludes every unrated school. This is the honest
   behaviour, not an oversight, so it is pinned rather than left to drift.
3. **Edges.** The band is inclusive at both ends. An exclusive edge silently
   drops schools sitting exactly on a round number a visitor just dragged to.
4. **Sectors partition.** The three control types sum to the unfiltered set and
   never overlap -- which is also what proves the filter is matching the
   dataset's own spelling. A renamed category matches nothing and reports it as
   an empty result: a wrong ANSWER, not an error.
5. **Order and subset.** Every filter combination leaves the list sorted by
   cost and a subset of the unfiltered one. Filters must narrow, never reorder
   -- the sort is the one claim this tool makes about its own list.
6. **Filter before cap.** Results are the cheapest `limit` MATCHES, not the
   matches among the cheapest `limit`. Filtering after the cap turns a 25-row
   answer into a 2-row one while looking entirely plausible.

Plus the picker's identity rule (`reconcile_search_pick`), which is why the
options are UNITIDs: the result frame is reset_index'd, so row positions are
0..N-1 on every search and a stored position stays "valid" against a completely
different result set. The reconcile is tested through the real function rather
than a copy, and the UNITID uniqueness it depends on is asserted separately --
a duplicate key would make "is my school still here" ambiguous.

NEGATIVE CONTROL. Five deliberate breakages were run against a copy of app.py,
and each was caught by the property aimed at it -- not merely by something:

    None treated as a full band          -> [sentinel]
    `>=` becomes `>` on the low edge     -> [inclusive edges]
    admit filter moved below head(limit) -> [filter before cap]
    reconcile always returns row 0       -> [picker identity]
    "Private Non-Profit" renamed         -> [sectors partition]

The third is worth the reminder of what it costs: with the filter applied
after the cap instead of before it, a search that should return 25 schools
returned 1, and every other property still passed.

Run this after touching search_schools_by_budget, reconcile_search_pick, or
the filter controls in render_school_search.
"""
import sys

import pandas as pd

# Chosen for size and spread rather than realism: CIP 11 (Computer & Information
# Sciences) at bachelor's is the largest family in the dataset, spans all three
# sectors, and has both rated and unrated schools -- so every property below has
# something to bite on. The budget is above the most expensive match so the cost
# filter is never what is doing the narrowing.
CIP = "11"
CREDENTIAL = "Bachelor's degree"
BUDGET = 200_000
HOME_STATE = "CA"
BIG = 10_000          # effectively "no cap", so `limit` is never the constraint


def load_app_namespace():
    """app.py's sections 1-2, without the UI. Same exec-prefix trick
    analyze_model.py uses -- see CLAUDE.md on why the section banners are
    load-bearing."""
    src = open("app.py").read()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    prefix = src[:src.rindex("# " + "=" * 60, 0, cut)]
    ns = {"__name__": "searchfilterscheck"}
    exec(compile(prefix, "app.py", "exec"), ns)
    return ns


def search(ns, **kwargs):
    return ns["search_schools_by_budget"](
        CIP, CREDENTIAL, BUDGET, HOME_STATE, limit=BIG, **kwargs)


def ids(frame) -> list:
    return [int(u) for u in frame["UNITID"]]


def check_sentinel(ns, base) -> list:
    """Property 1: None is no filter; an explicit full band is a filter."""
    problems = []
    unrated = int(base["ADM_RATE"].isna().sum())
    rated = len(base) - unrated

    if unrated == 0:
        problems.append(
            "  the fixture has no unrated schools, so every admit-rate check "
            "below discriminates nothing -- pick a CIP family that has some")
        return problems

    if ids(search(ns, adm_rate_range=None)) != ids(base):
        problems.append(
            "  adm_rate_range=None is not a no-op\n"
            "    the sentinel must leave the result set exactly as it found it")

    full = search(ns, adm_rate_range=(0.0, 1.0))
    if len(full) != rated:
        problems.append(
            f"  an explicit (0.0, 1.0) band returned {len(full)} rows, expected "
            f"{rated} (the schools reporting a rate)\n"
            f"    a band cannot contain an unknown rate, so a full band is NOT "
            f"the same as no band")
    if len(full) == len(base):
        problems.append(
            f"  an explicit (0.0, 1.0) band returned the same {len(base)} rows as "
            f"no filter at all\n"
            f"    then the None sentinel is doing nothing and the widget sitting "
            f"wide open is silently dropping {unrated} unrated schools")
    return problems


def check_blanks_excluded(ns) -> list:
    """Property 2: any narrowing requires a reported rate."""
    problems = []
    for low, high in [(0.0, 0.5), (0.6, 1.0), (0.2, 0.6)]:
        got = search(ns, adm_rate_range=(low, high))
        if got["ADM_RATE"].isna().any():
            problems.append(
                f"  band {low:.2f}-{high:.2f} returned a school with no admit rate\n"
                f"    an unknown rate cannot be placed inside a band")
        outside = got[(got["ADM_RATE"] < low) | (got["ADM_RATE"] > high)]
        if not outside.empty:
            problems.append(
                f"  band {low:.2f}-{high:.2f} returned {len(outside)} school(s) "
                f"outside it")
    return problems


def check_edges(ns, base) -> list:
    """Property 3: both edges inclusive."""
    problems = []
    rates = sorted(base["ADM_RATE"].dropna().unique())
    if not rates:
        return ["  no reported admit rates in the fixture; edge check is vacuous"]
    for edge in (rates[0], rates[len(rates) // 2], rates[-1]):
        point = search(ns, adm_rate_range=(edge, edge))
        if point.empty:
            problems.append(
                f"  a degenerate band at exactly {edge:.4f} returned nothing, but "
                f"a school reports that rate\n"
                f"    an exclusive edge drops schools sitting on the round number "
                f"a visitor just dragged to")
    return problems


def check_sectors_partition(ns, base) -> list:
    """Property 4: the sectors partition the unfiltered set."""
    problems = []
    seen, total = set(), 0
    for sector in ns["CONTROL_TYPE_ORDER"]:
        part = search(ns, control_types=(sector,))
        total += len(part)
        got = set(part["control_type"].unique())
        if got - {sector}:
            problems.append(f"  control_types=({sector!r},) also returned {got - {sector}}")
        overlap = seen & set(ids(part))
        if overlap:
            problems.append(f"  {sector} shares {len(overlap)} school(s) with another sector")
        seen |= set(ids(part))
    if total != len(base):
        problems.append(
            f"  the three sectors sum to {total} but the unfiltered set is {len(base)}\n"
            f"    a category the filter cannot spell matches nothing and reports "
            f"it as an empty result rather than an error")
    if ids(search(ns, control_types=None)) != ids(base):
        problems.append("  control_types=None is not a no-op")
    return problems


def check_order_and_subset(ns, base) -> list:
    """Property 5: filters narrow, never reorder."""
    problems = []
    baseline = set(ids(base))
    combos = [
        {"control_types": ("Public",)},
        {"adm_rate_range": (0.0, 0.5)},
        {"control_types": ("Public",), "adm_rate_range": (0.0, 0.5)},
        {"states": ("CA",)},
        {"states": ("CA",), "control_types": ("Public",), "adm_rate_range": (0.1, 0.9)},
        {"min_coa_per_year": 20_000},
    ]
    for kwargs in combos:
        got = search(ns, **kwargs)
        if not got["coa_per_year"].is_monotonic_increasing:
            problems.append(f"  {kwargs} broke the cost ordering")
        stray = set(ids(got)) - baseline
        if stray:
            problems.append(f"  {kwargs} returned {len(stray)} school(s) not in the "
                            f"unfiltered set -- a filter must only ever remove rows")
    return problems


def check_filter_before_cap(ns, base) -> list:
    """Property 6: the cheapest `limit` MATCHES, not the matches among the
    cheapest `limit`."""
    problems = []
    limit = 25
    band = (0.0, 0.5)
    capped = ns["search_schools_by_budget"](
        CIP, CREDENTIAL, BUDGET, HOME_STATE, limit=limit, adm_rate_range=band)
    eligible = len(search(ns, adm_rate_range=band))
    if eligible < limit:
        problems.append("  fixture has too few matches for the cap to bind; "
                        "this check discriminates nothing")
        return problems
    if len(capped) != limit:
        problems.append(
            f"  a capped filtered search returned {len(capped)} of a possible "
            f"{limit}\n"
            f"    filtering AFTER .head(limit) turns a full answer into a "
            f"remnant while looking entirely plausible")
    # And it must be the cheapest ones, not an arbitrary slice.
    want = ids(search(ns, adm_rate_range=band))[:limit]
    if ids(capped) != want:
        problems.append("  the capped result is not the cheapest `limit` matches")
    return problems


def check_picker_identity(ns, base) -> list:
    """The picker keys on UNITID, so UNITID must be a real key -- and the
    reconcile must keep a survivor while replacing an evicted selection."""
    problems = []
    if base["UNITID"].isna().any():
        problems.append("  a result row has no UNITID; the picker cannot identify it")
    if base["UNITID"].duplicated().any():
        problems.append(
            "  UNITID is not unique within a result set\n"
            "    'is my school still in the list' stops having one answer")

    reconcile = ns["reconcile_search_pick"]
    wide = ids(base)
    narrow = ids(search(ns, adm_rate_range=(0.0, 0.5)))

    survivor = narrow[len(narrow) // 2]
    if reconcile(survivor, narrow) != survivor:
        problems.append("  a school still in the list lost the selection")

    evicted = next((u for u in wide if u not in narrow), None)
    if evicted is None:
        problems.append("  fixture: narrowing evicted nothing, so the reconcile "
                        "check discriminates nothing")
    elif reconcile(evicted, narrow) != narrow[0]:
        problems.append(
            f"  an evicted school did not fall back to the cheapest row\n"
            f"    Streamlit raises when a keyed widget's stored value is absent "
            f"from its options")

    if reconcile(None, narrow) != narrow[0]:
        problems.append("  a first render (stored=None) did not land on the cheapest row")
    if reconcile(survivor, []) is not None:
        problems.append("  an empty result set must reconcile to None, not raise")
    return problems


def main() -> int:
    ns = load_app_namespace()
    if ns["load_coa_dataset"]().empty:
        print("school search filters: COA dataset missing; nothing to check")
        return 1

    base = search(ns)
    if base.empty:
        print("school search filters: the fixture returned no schools at all")
        return 1

    problems, checks = [], []
    for name, fn in [
        ("sentinel", lambda: check_sentinel(ns, base)),
        ("blanks excluded", lambda: check_blanks_excluded(ns)),
        ("inclusive edges", lambda: check_edges(ns, base)),
        ("sectors partition", lambda: check_sectors_partition(ns, base)),
        ("order and subset", lambda: check_order_and_subset(ns, base)),
        ("filter before cap", lambda: check_filter_before_cap(ns, base)),
        ("picker identity", lambda: check_picker_identity(ns, base)),
    ]:
        found = fn()
        checks.append(name)
        problems += [f"[{name}]\n{p}" for p in found]

    if problems:
        print(f"school search filters: {len(problems)} violation(s)\n")
        print("\n\n".join(problems))
        return 1
    unrated = int(base["ADM_RATE"].isna().sum())
    print(f"school search filters OK -- {len(checks)} properties over "
          f"{len(base)} schools ({unrated} unrated): wide-open means no filter, "
          f"narrowing excludes unrated, edges inclusive, sectors partition, "
          f"order preserved, cap applied last, picker keyed on UNITID.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

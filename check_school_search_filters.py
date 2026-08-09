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

Seven properties, each aimed at a distinct way this can regress:

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
7. **The credential gate.** The admit-rate filter is bachelor's-only, because
   ADM_RATE is an UNDERGRADUATE rate whose coverage collapses below one: 74% of
   bachelor's institutions publish it against 35% at associate's and 14-24% for
   certificates. Below a bachelor's the filter measures who files the field
   rather than who is selective. The check asserts the gate AND the coverage
   gap it rests on, so a Scorecard release that closed that gap surfaces as a
   decision to make rather than a stale constant nobody rereads.

Plus the picker's identity rule (`reconcile_search_pick`), which is why the
options are UNITIDs: the result frame is reset_index'd, so row positions are
0..N-1 on every search and a stored position stays "valid" against a completely
different result set. The reconcile is tested through the real function rather
than a copy, and the UNITID uniqueness it depends on is asserted separately --
a duplicate key would make "is my school still here" ambiguous.

NEGATIVE CONTROL. Six deliberate breakages were run against a copy of app.py,
and each was caught by the property aimed at it -- not merely by something:

    None treated as a full band          -> [sentinel]
    `>=` becomes `>` on the low edge     -> [inclusive edges]
    admit filter moved below head(limit) -> [filter before cap]
    reconcile always returns row 0       -> [picker identity]
    filter offered at every credential   -> [credential gate]
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


def check_credential_gate(ns) -> list:
    """The admit-rate filter is offered only where the field has the coverage
    to mean something, and the fact that gate rests on is still true."""
    problems = []
    applies = ns["adm_filter_applies"]
    allowed = set(ns["ADM_RATE_CREDENTIALS"])

    for credential in ns["CREDENTIAL_LEVELS"]:
        want = credential in allowed
        if applies(credential) is not want:
            problems.append(f"  adm_filter_applies({credential!r}) != {want}")
    if applies("Some credential that does not exist"):
        problems.append(
            "  an unrecognised credential enabled the filter\n"
            "    this gates something that can only REMOVE schools, so the safe "
            "answer to 'we don't know this level' is to leave the list alone")

    if not allowed:
        problems.append(
            "  ADM_RATE_CREDENTIALS is empty, so the admit-rate filter is now "
            "unreachable at every level -- a feature removed by a constant")

    # The gate is a claim about Scorecard's coverage, so assert the CLAIM.
    #
    # This is deliberately an absolute floor per allowed credential, not a
    # comparison between allowed and gated ones. The relative version was the
    # first thing written here and it was worthless: it skipped every
    # credential inside the allowed set, so widening the tuple to all five
    # levels made the whole check vacuous and the guard passed on exactly the
    # change it exists to stop. The negative control is what found that.
    #
    # 60% sits well below bachelor's 74% -- room for ordinary release drift --
    # and well above associate's 35%, so nothing currently gated could slip in
    # without a real change in what Scorecard publishes.
    floor = 0.60
    coa = ns["load_coa_dataset"]()
    for credential in sorted(allowed):
        entry = ns["CREDENTIAL_LEVELS"].get(credential)
        if entry is None:
            problems.append(
                f"  ADM_RATE_CREDENTIALS names {credential!r}, which is not a "
                f"credential the search offers")
            continue
        column = f"programs_{entry[0]}"
        offers = coa[coa[column].notna() & (coa[column].astype(str).str.len() > 0)]
        if not len(offers):
            problems.append(f"  no schools award {credential}; coverage is undefined")
            continue
        share = offers["ADM_RATE"].notna().mean()
        if share < floor:
            problems.append(
                f"  the filter is offered for {credential}, where only "
                f"{share:.0%} of schools report an admit rate (floor {floor:.0%})\n"
                f"    below that the filter stops measuring selectivity and "
                f"starts measuring who files the field -- switching it on would "
                f"drop {1 - share:.0%} of the list for having nothing on file")
    return problems


def check_graduate_search(ns) -> list:
    """The graduate half: its own registry, its own price source, and a picker
    name that the sidebar can actually accept."""
    problems = []
    grad_levels = ns["GRADUATE_CREDENTIAL_LEVELS"]
    undergrad = set(ns["CREDENTIAL_LEVELS"])

    # The two registries must not overlap. A label in both would dispatch on
    # whichever check ran first, and the two searches read different files.
    both = undergrad & set(grad_levels)
    if both:
        problems.append(
            f"  {sorted(both)} appear in BOTH credential registries\n"
            "    the search dispatches on which one a label came from, so a "
            "label in both resolves to whichever test runs first")
    for label in grad_levels:
        if not ns["is_graduate_credential"](label):
            problems.append(f"  is_graduate_credential({label!r}) is False")
        if label not in ns["GRADUATE_SEARCH_TO_CREDENTIAL"]:
            problems.append(
                f"  {label!r} has no sidebar credential to hand off to\n"
                "    applying a result would set a credential the radio has no "
                "option for, and Streamlit raises on that")
    for label in undergrad:
        if ns["is_graduate_credential"](label):
            problems.append(f"  is_graduate_credential({label!r}) is True")

    # The years must be the ADDITIONAL graduate ones. PROGRAM_YEARS_BY_EDUCATION
    # holds the totals including the bachelor's -- 6 and 9 -- and pricing a
    # master's over six years of graduate tuition treble-counts it.
    additional = ns["GRADUATE_ADDITIONAL_YEARS"]
    for label, (_, years) in grad_levels.items():
        if years >= ns["UNDERGRAD_YEARS"] + 1 and years not in additional.values():
            problems.append(
                f"  {label!r} prices {years} years, which looks like a TOTAL "
                f"rather than the graduate years alone ({sorted(set(additional.values()))})")

    # And the handoff name must be one the sidebar picker will accept.
    for family, credential in [("52", "master"), ("11", "master")]:
        results = ns["search_graduate_schools_by_budget"](
            family, credential, 500_000, "CA", limit=10_000)
        if results.empty:
            problems.append(f"  no graduate results at all for CIP {family}")
            continue
        if "picker_name" not in results.columns:
            problems.append("  results carry no picker_name for the handoff")
            continue
        options = set(ns["graduate_schools_for"](family, credential))
        stray = set(results["picker_name"]) - options
        if stray:
            problems.append(
                f"  {len(stray)} result(s) carry a picker_name the sidebar has "
                f"no option for (e.g. {sorted(stray)[0]!r})\n"
                "    the picker resets to the national default on an unknown "
                "name, silently discarding the school just applied")
        if not results["price_per_year"].is_monotonic_increasing:
            problems.append(f"  CIP {family} graduate results are not price-sorted")
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
        ("credential gate", lambda: check_credential_gate(ns)),
        ("graduate search", lambda: check_graduate_search(ns)),
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
          f"order preserved, cap applied last, graduate levels dispatch "
          f"separately, admit rate gated to "
          f"{'/'.join(ns['ADM_RATE_CREDENTIALS'])}, picker keyed on UNITID.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

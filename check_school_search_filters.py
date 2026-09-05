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
import ast
import sys

import pandas as pd

# Every column render_graduate_results reads. ONE renderer serves both the
# graduate and the professional search, so a column present on only one frame
# is a KeyError at render time -- nothing types it, nothing imports it, and the
# page simply breaks for one level while working for the other.
# total_program_cost went missing from the professional frame exactly this way.
SHARED_RESULT_COLUMNS = [
    "INSTNM", "CITY", "STABBR", "control_type", "UNITID", "picker_name",
    "is_home_state", "total_program_cost", "debt_median",
    "grad_tuition_fees_in", "grad_tuition_fees_out", "price_per_year",
]

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
    # MAJOR_DATA is a section-4 name, and the professional checks need the REAL
    # one: the curated AAMC/ABA/ADEA constants live in it, and against an empty
    # dict every path falls through to the Scorecard-derived figure -- so the
    # check would pass on code that had lost the curated ones entirely. Built
    # the way analyze_model.py builds it, from app.py's own builder.
    ns["MAJOR_DATA"] = ns["build_major_data"](ns["CAREERS_CSV_PATH_NATIONAL"])
    # Plus the pure functions defined BELOW the section-3 banner. The display
    # helpers live in section 5 beside what they render, so the exec prefix
    # cannot see them -- and the metric they build is exactly where the last
    # two reported bugs surfaced. Same extra pass check_internal_links.py runs.
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name not in ns:
            try:
                exec(compile(ast.Module(body=[node], type_ignores=[]),
                             "app.py", "exec"), ns)
            except Exception:
                pass          # a def whose decorators need the UI; not ours
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


def check_match_count_reported(ns, base) -> list:
    """Property: the frame carries the PRE-cap match count.

    The caption above the results used to read "{len(results)} schools" over a
    frame already through .head(limit), so it said "25 schools" whether 25
    matched or 633. A visitor could not tell "these are all of them" from
    "these are the cheapest 25 of many", which are different findings in the
    same way "no school teaches this" and "none is this cheap" are.

    The expectation is an INDEPENDENT unbounded search, never the capped
    frame's own length -- deriving it from the thing under test would assert
    only that the code equals itself.
    """
    problems = []
    limit = 25
    capped = ns["search_schools_by_budget"](
        CIP, CREDENTIAL, BUDGET, HOME_STATE, limit=limit)
    expected = len(search(ns))
    if expected <= limit:
        problems.append("  fixture has too few matches for the cap to bind; "
                        "this check discriminates nothing")
        return problems
    got = capped.attrs.get("total_matches")
    if got is None:
        problems.append(
            "  the capped frame carries no total_matches attribute\n"
            "    pandas DROPS DataFrame.attrs across .merge -- if this was set "
            "before the field-debt merge it survives graduate searches and "
            "vanishes on bachelor's ones, which is the default path")
    elif got != expected:
        problems.append(f"  total_matches is {got}, expected {expected}")
    caption = ns["search_result_caption"](limit, expected, "Cost")
    if str(expected) not in caption.replace(",", ""):
        problems.append(f"  the caption does not name the total: {caption!r}")
    if str(limit) not in caption:
        problems.append(f"  the caption does not name the shown count: {caption!r}")
    # And it must read differently when nothing was cut, or the fix is cosmetic.
    if ns["search_result_caption"](7, 7, "Cost") == ns["search_result_caption"](7, 70, "Cost"):
        problems.append("  a capped and an uncapped result caption identically")
    return problems


def check_sort_before_cap(ns) -> list:
    """Property: the sort runs BEFORE the cap, in every offered mode.

    Sorting after .head() would return the best of the cheapest 25 rather than
    the best 25 matches -- the same defect check_filter_before_cap measures for
    filters, and equally invisible: the list still renders and still sorts.

    Also pins missing values to the TAIL. ADM_RATE is blank for a quarter of
    bachelor's institutions, so NaN-first would fill the whole window with
    schools that report nothing.
    """
    problems = []
    limit = 25
    modes = ns["search_sort_modes"](CREDENTIAL)
    if len(modes) < 2:
        problems.append("  the fixture level offers no sort choice; "
                        "this check discriminates nothing")
        return problems
    for mode in modes:
        column, ascending = ns["SEARCH_SORT_MODES"][mode]
        full = search(ns, sort_mode=mode)
        capped = ns["search_schools_by_budget"](
            CIP, CREDENTIAL, BUDGET, HOME_STATE, limit=limit, sort_mode=mode)
        if ids(capped) != ids(full)[:limit]:
            problems.append(
                f"  [{mode}] the capped result is not the first {limit} of the "
                f"sorted matches\n"
                f"    sorting AFTER .head(limit) reorders a cost-selected "
                f"remnant while looking entirely plausible")
        values = full[column].tolist()
        reported = [v for v in values if v == v]
        blanks = [i for i, v in enumerate(values) if v != v]
        if blanks and reported and min(blanks) < len(reported):
            problems.append(
                f"  [{mode}] rows with no {column} are not last "
                f"(first blank at {min(blanks)} of {len(reported)} reported)")
        ordered = sorted(reported, reverse=not ascending)
        if reported != ordered:
            problems.append(f"  [{mode}] the reported values are not monotonic")
    return problems


def check_sort_excludes_judgement_columns(ns) -> list:
    """Property: no sort mode orders on net price or on either debt column.

    Net price is an average over AIDED students only, so ordering on it ranks
    schools by who received aid. The two borrowing columns would rank schools
    by what other families were willing to owe, which is not a fact about the
    school. All three are display-only and the app says so in three places.
    """
    problems = []
    forbidden = {"net_price", "NPT4_PUB", "NPT4_PRIV",
                 "PLUS_DEBT_INST_COMP_MD", "field_debt_median"}
    used = {column for column, _asc in ns["SEARCH_SORT_MODES"].values()}
    overlap = used & forbidden
    if overlap:
        problems.append(
            f"  a sort mode orders on {sorted(overlap)}, which the table shows "
            f"but must never rank on")
    if ns["SEARCH_SORT_DEFAULT"] not in ns["SEARCH_SORT_MODES"]:
        problems.append("  the default sort mode is not one of the modes")
    # A level that cannot support a mode must not offer it, and a stored mode
    # from a level that could must not survive onto one that cannot -- the
    # widget raises on a value absent from its options.
    if "Admit rate" in ns["search_sort_modes"]("Associate's degree"):
        problems.append("  admit-rate sorting is offered below bachelor's, "
                        "where three quarters of schools report no rate")
    if ns["resolve_search_sort"]("Admit rate", "Associate's degree") != ns["SEARCH_SORT_DEFAULT"]:
        problems.append("  a stale sort mode is not reconciled to the default")
    # A mode the FRAME cannot support is a control that lies. The graduate and
    # professional searches build their frames from the tuition and debt files,
    # which carry neither completion_rate nor ADM_RATE, so cost is the only
    # orderable column -- offering more renders a selectbox whose choice the
    # results silently ignore. Found in a browser, not by a type error.
    import inspect
    for credential in list(ns["GRADUATE_CREDENTIAL_LEVELS"]) + list(ns["PROFESSIONAL_SEARCH_LEVELS"]):
        offered = ns["search_sort_modes"](credential)
        if offered != [ns["SEARCH_SORT_DEFAULT"]]:
            problems.append(
                f"  {credential!r} offers {offered}, but the graduate frames "
                f"carry no column to order on but price")
    # And the name filter must actually reach those searches, for the same
    # reason: the shared control renders on both tools.
    for fn in ("search_graduate_schools_by_budget",
               "search_professional_schools_by_budget"):
        if "name_query" not in inspect.signature(ns[fn]).parameters:
            problems.append(
                f"  {fn} takes no name_query, but render_search_controls "
                f"shows the name filter on the graduate tool")
    return problems


def check_name_filter(ns, base) -> list:
    """Property: the name filter is a filter -- before the cap, case-blind."""
    problems = []
    limit = 25
    query = "University"
    filtered = search(ns, name_query=query)
    if filtered.empty:
        problems.append("  the fixture matches no school by name; "
                        "this check discriminates nothing")
        return problems
    if not set(ids(filtered)) <= set(ids(base)):
        problems.append("  the name filter returned schools the base search did not")
    names = filtered["INSTNM"].fillna("")
    if not names.str.contains(query, case=False, regex=False).all():
        problems.append("  a returned school's name does not contain the query")
    if len(filtered) != len(search(ns, name_query=query.upper())):
        problems.append("  the name filter is case-SENSITIVE")
    capped = ns["search_schools_by_budget"](
        CIP, CREDENTIAL, BUDGET, HOME_STATE, limit=limit, name_query=query)
    if ids(capped) != ids(filtered)[:limit]:
        problems.append(
            "  the capped name search is not the cheapest matches\n"
            "    filtering by name AFTER .head(limit) searches only the 25 "
            "already on screen, which is a different question")
    if capped.attrs.get("total_matches") != len(filtered):
        problems.append("  the reported total ignores the name filter")
    return problems


def check_regions_partition(ns) -> list:
    """Property: the region buttons cover every state exactly once.

    They POPULATE the state filter rather than filtering, so a gap here cannot
    silently narrow a search -- but a state in no region is unreachable by
    button, and a state in two is a chip that appears to do nothing when the
    other is already selected.
    """
    problems = []
    regions = ns["SEARCH_REGIONS"]
    seen, dupes = set(), set()
    for name, codes in regions.items():
        codes = set(codes)
        dupes |= seen & codes
        seen |= codes
    if dupes:
        problems.append(f"  states in more than one region: {sorted(dupes)}")
    missing = set(ns["US_STATES"]) - seen
    if missing:
        problems.append(f"  states in no region: {sorted(missing)}")
    # DC is deliberately included and is not in US_STATES; anything ELSE extra
    # is a typo, and a code that matches no school is a chip that does nothing.
    extra = seen - set(ns["US_STATES"]) - {"DC"}
    if extra:
        problems.append(f"  region codes that are not states: {sorted(extra)}")
    known = set(ns["load_coa_dataset"]()["STABBR"].dropna().unique())
    unknown = seen - known
    if unknown:
        problems.append(f"  region codes matching no school in the data: "
                        f"{sorted(unknown)}")
    return problems


def check_plus_debt_sample_size(ns) -> list:
    """Property: the count behind the Parent PLUS median is available to show.

    The pipeline keeps PLUS_DEBT_INST_COMP_N explicitly so the median is not
    misread, and a median over 5 families renders identically to one over 500
    without it.
    """
    problems = []
    frame = ns["load_coa_dataset"]()
    if "PLUS_DEBT_INST_COMP_N" not in frame.columns:
        problems.append("  PLUS_DEBT_INST_COMP_N is absent from the dataset")
        return problems
    import pandas as _pd
    n = _pd.to_numeric(frame["PLUS_DEBT_INST_COMP_N"], errors="coerce")
    md = _pd.to_numeric(frame["PLUS_DEBT_INST_COMP_MD"], errors="coerce")
    shown = md.notna() & (md > 0)
    if not shown.any():
        problems.append("  no school publishes a Parent PLUS median")
        return problems
    if (n[shown].dropna() < 0).any():
        problems.append("  a negative borrowing-family count")
    thin = float((n[shown] < ns["PLUS_DEBT_THIN_N"]).mean())
    # A threshold marking nothing, or marking everything, is not a threshold.
    # Measured at 4% of publishing schools.
    if not 0.001 < thin < 0.40:
        problems.append(
            f"  PLUS_DEBT_THIN_N={ns['PLUS_DEBT_THIN_N']} marks {thin:.1%} of "
            f"publishing schools, which is not a useful cut")
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
        missing = [c for c in SHARED_RESULT_COLUMNS if c not in results.columns]
        if missing:
            problems.append(
                f"  graduate results for CIP {family} are missing {missing}, "
                f"which the shared results table reads")
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
    # The professional programmes are a THIRD shape: keyed by programme rather
    # than by field-plus-credential, and priced from the per-programme file.
    prof_levels = ns["PROFESSIONAL_SEARCH_LEVELS"]
    if set(prof_levels) & set(grad_levels):
        problems.append("  a label is in both the graduate and professional registries")
    for label in prof_levels:
        if not ns["is_professional_credential"](label):
            problems.append(f"  is_professional_credential({label!r}) is False")
        if ns["is_graduate_credential"](label):
            problems.append(
                f"  {label!r} answers True to BOTH predicates\n"
                "    the controls hide the field selector on one and require it "
                "on the other, so a level in both renders an impossible page")

    # Every column the shared results table reads must exist on the
    # professional frame too. The table is one renderer for two searches, so a
    # column present on only one is a KeyError at render time -- which is
    # exactly how total_program_cost was found missing.
    for label, (program_key, years) in prof_levels.items():
        priced = ns["search_professional_schools_by_budget"](
            program_key, 500_000, "CA", limit=10_000)
        if priced.empty:
            problems.append(f"  no {label} schools priced at all")
            continue
        missing = [c for c in SHARED_RESULT_COLUMNS if c not in priced.columns]
        if missing:
            problems.append(
                f"  {label} results are missing {missing}, which the shared "
                f"results table reads")
        if not priced["price_per_year"].is_monotonic_increasing:
            problems.append(f"  {label} results are not price-sorted")
        # The price file is the universe here, and it must stay the larger one
        # -- that is the reason this search reads it rather than the debt file.
        debt = ns["load_professional_debt"]()
        known = debt[(debt["credential"] == "professional")
                     & (debt["program_key"] == program_key)]
        if len(priced) < len(known):
            problems.append(
                f"  {label} prices {len(priced)} schools but {len(known)} publish "
                f"debt\n    the price file is supposed to be the wider universe; "
                f"if it is not, the search is reading the wrong source")
    return problems


def check_fixed_field_levels(ns) -> list:
    """The MBA is a third shape, and every place that assumed two must know.

    Its controls behave like a professional programme (the level IS the field,
    so no field selector) while its search behaves like a master's (the debt
    file is the universe, the price is the school's institution-wide graduate
    average, because IPEDS publishes no MBA price). Every predicate below
    exists because conflating the two halves breaks something silently: a field
    selector whose value the search ignores, an empty frame read as "no school
    teaches this", or a KeyError on the apply button of a level that searched
    and rendered perfectly.
    """
    problems = []
    fixed = ns["FIXED_FIELD_GRADUATE_LEVELS"]
    prof = ns["PROFESSIONAL_SEARCH_LEVELS"]
    grad = ns["GRADUATE_CREDENTIAL_LEVELS"]

    for label in fixed:
        if label in prof or label in grad:
            problems.append(f"  {label!r} is in two level registries; the "
                            f"dispatch would pick whichever it tests first")
        if ns["is_professional_credential"](label):
            problems.append(
                f"  is_professional_credential({label!r}) is True, so it would "
                f"be priced from the per-programme file -- which has no MBA")
        if not ns["level_supplies_its_own_field"](label):
            problems.append(
                f"  {label!r} would render a field selector whose value its "
                f"search then ignores")
        if label not in ns["GRADUATE_SEARCH_TO_CREDENTIAL"]:
            problems.append(
                f"  {label!r} has no GRADUATE_SEARCH_TO_CREDENTIAL row, so "
                f"applying a school raises a KeyError -- after the level has "
                f"searched and rendered perfectly")

    # The divergence itself: own-field is the union, and a plain degree level
    # is in neither half of it.
    for label in prof:
        if not ns["level_supplies_its_own_field"](label):
            problems.append(f"  {label!r} lost its own-field status")
    for label in grad:
        if ns["level_supplies_its_own_field"](label):
            problems.append(
                f"  {label!r} is a degree level and must ASK for a field")

    for label, (program_key, credential, years, picker_family) in fixed.items():
        results = ns["search_graduate_schools_by_budget"](
            program_key, credential, 500_000, "CA", limit=10_000)
        if results.empty:
            problems.append(f"  {label} returns no schools at any price")
            continue
        missing = [c for c in SHARED_RESULT_COLUMNS if c not in results.columns]
        if missing:
            problems.append(f"  {label} results are missing {missing}")
        if not results["price_per_year"].is_monotonic_increasing:
            problems.append(f"  {label} results are not price-sorted")
        # The hand-off aims at the FAMILY picker, since no per-programme option
        # list exists. Every result must be in it or the apply silently resets.
        options = set(ns["graduate_schools_for"](picker_family, credential))
        stray = set(results["picker_name"]) - options
        if stray:
            problems.append(
                f"  {len(stray)} {label} school(s) are absent from the family "
                f"{picker_family} picker (e.g. {sorted(stray)[0]!r})\n"
                f"    the picker resets to the national default on a name it "
                f"does not have, discarding the school just applied")
        # The programme median and the family rollup describe the same students
        # and must stay separately readable -- they disagree by a lot.
        rollup = ns["graduate_schools_for"](picker_family, credential)
        if not rollup:
            problems.append(f"  family {picker_family} has no rollup rows, so "
                            f"{label} has nothing to be distinguished FROM")
    return problems


def check_programmes_without_debt(ns) -> list:
    """PROGRAMMES_WITHOUT_OWN_DEBT must name exactly the programmes with none.

    The caption on those levels tells the visitor the borrowing column is not
    programme-specific. If a future release gave one of them its own CIP the
    caption would become a lie in the safe direction; if a programme fell OUT
    of the debt file without being listed here, the table would present a
    medicine median as that programme's own. Both are silent, so both are
    checked -- against the data, not against the tuple.
    """
    problems = []
    debt = ns["load_professional_debt"]()
    if debt.empty or "credential" not in debt.columns:
        return ["  no professional debt data to check against"]
    prof = debt[debt["credential"] == "professional"]
    listed = set(ns["PROGRAMMES_WITHOUT_OWN_DEBT"])
    for label, (program_key, _years) in ns["PROFESSIONAL_SEARCH_LEVELS"].items():
        rows = len(prof[prof["program_key"] == program_key])
        if program_key in listed and rows:
            problems.append(
                f"  {program_key!r} is listed as having no debt of its own but "
                f"has {rows} rows -- the caption disclaiming it is now false")
        if program_key not in listed and not rows:
            problems.append(
                f"  {program_key!r} has no debt rows and is not listed, so its "
                f"results show a borrowing column built from another programme "
                f"with nothing saying so")
    return problems


def check_professional_paths(ns) -> list:
    """Every search level must be able to reach the calculator, or say so.

    A level whose apply can never succeed is worse than one that does not
    exist: the button is there, the warning tells the visitor to set a major
    the app does not have, and nothing on screen admits the path is unmodelled.
    So every professional level must map to an occupation, and every mapped
    occupation must resolve a non-zero national debt.

    Zero is the specific failure. professional_debt_cap reads a falsy debt as a
    real cap of zero rather than "unset", which pushes the entire tranche into
    private borrowing while the principal simultaneously loses the debt -- two
    wrong answers from one absent row, and both flatter or punish silently.
    """
    problems = []
    occupations = ns["PROFESSIONAL_PROGRAM_BY_OCCUPATION"]
    # Every mapped title must be a real occupation. A near-miss ("Optometrist"
    # for "Optometrists") is inert in the worst way: the map looks complete,
    # every lookup against it succeeds in isolation, and the actual occupation
    # in MAJOR_DATA silently keeps costing nothing. Only the curated pseudo-
    # occupations are exempt, since those are entries this app invents.
    known = set(ns["MAJOR_DATA"])
    for title, programme in sorted(occupations.items()):
        if title not in known:
            problems.append(
                f"  {title!r} -> {programme!r} names no occupation in "
                f"MAJOR_DATA\n    the mapping is inert and that path is priced "
                f"with no professional debt at all")
    for label, (level_key, _years) in ns["PROFESSIONAL_SEARCH_LEVELS"].items():
        programme = ns["calculator_programme_for_level"](level_key)
        reachable = [occ for occ, key in occupations.items() if key == programme]
        if not reachable:
            problems.append(
                f"  {label!r} applies to programme {programme!r}, which no "
                f"occupation maps to\n    its apply button can only ever warn, "
                f"and the warning names a path the app does not offer")
            continue
        occ = reachable[0]
        national = ns["national_professional_debt"](occ)
        if not national:
            problems.append(
                f"  {occ!r} ({programme}) resolves a national debt of 0\n"
                f"    professional_debt_cap reads that as a cap, not as unset: "
                f"the whole tranche goes private and the debt vanishes")
        # The federal professional cap keys on unpaid_training_years, so a
        # path with no training structure gets a cap of ZERO -- and
        # split_loan_financing reads that as a real cap, pricing an ordinary
        # federal professional loan entirely as private money at the higher
        # rate. It is invisible: the page renders, the total is right, and only
        # the tranche split is wrong. All five occupations shipped that way.
        cap = ns["professional_debt_cap"](occ, national)
        if not cap:
            problems.append(
                f"  {occ!r} has a federal professional cap of 0\n"
                f"    every dollar of its {ns['fmt_money'](national)} debt is "
                f"priced as private borrowing; check unpaid_training_years")
        # And a path that attends school must not earn a full salary while in
        # it. Year 0 is the first year after a bachelor's, which for every
        # programme here is the first year OF the professional degree.
        first_year = ns["get_annual_salary_for_year"](occ, 0)
        if first_year:
            problems.append(
                f"  {occ!r} earns {ns['fmt_money'](first_year)} in its first "
                f"year of professional school\n    it is being charged the "
                f"tuition and paid the salary at the same time")
        listed = ns["professional_schools_for"](programme)
        if not listed and programme not in ns["PROGRAMMES_WITHOUT_OWN_DEBT"]:
            problems.append(
                f"  {programme!r} has no schools to pick and is not declared as "
                f"a programme without its own debt")
        # The caption promises a school figure differs from the national one.
        if listed:
            school_debt = ns["resolve_professional_debt"](occ, listed[0])
            if not school_debt:
                problems.append(
                    f"  naming {listed[0]!r} for {occ!r} resolves no debt at all")
    return problems


def check_professional_cost_years(ns) -> list:
    """A degree priced by debt must not ALSO be charged the school's tuition.

    additional_training_debt is added on top of the undergraduate loan, so the
    cost model must stop at the undergraduate years on those paths. Until
    2026-08-14 it did not: a dermatologist at a $45,619 school was charged
    eight years of that COA and then had $205,000 of medical school debt added,
    for a $569,952 principal where the twelve modelled physician titles read
    $387,476. Both halves looked right on their own, which is why this asserts
    the RELATIONSHIP rather than either number.

    Both directions matter. Trimming the graduate years off a master's would
    price the degree at zero, because nothing else is paying for it there.
    """
    problems = []
    major_data = ns["MAJOR_DATA"]
    COA = 40_000

    def principal(title):
        py = ns["program_years_for_major"](title)
        gy = ns["graduate_years_for_major"](title)
        cy = ns["school_cost_years"](py, gy, title)
        rows = ns["compute_loan_schedule_by_year"](COA, 0.0, 0.0, 0.0, years=cy)
        return py, gy, cy, sum(r["loan_amount"] for r in rows)

    professional = sorted(set(ns["PROFESSIONAL_PROGRAM_BY_OCCUPATION"]) & set(major_data))
    for title in professional:
        debt = major_data[title].get("additional_training_debt", 0)
        if not debt:
            continue
        py, gy, cy, charged = principal(title)
        if cy != max(py - gy, 0):
            problems.append(
                f"  {title!r} is charged {cy} years of the school's COA but its "
                f"{gy} graduate years\n"
                f"    are already priced by ${debt:,.0f} of professional debt. "
                f"Expected {max(py - gy, 0)}.")
        if gy and charged >= COA * py:
            problems.append(
                f"  {title!r} is charged ${charged:,.0f} of COA over a {py}-year "
                f"programme AND\n    ${debt:,.0f} of professional debt: the "
                f"degree is being paid for twice")

    # The LABEL has to name the years the figure actually covers. Trimming the
    # cost model without following it here printed "Total Loan Amount (all 8
    # years)" over four years of a dentist's tuition -- reported from the
    # dashboard, not caught by anything, because both the number and the label
    # were individually defensible.
    for title in professional:
        if not major_data[title].get("additional_training_debt"):
            continue
        py, gy, cy, _ = principal(title)
        label = ns["loan_amount_label"]("detailed", py, cy)
        if str(py) in label:
            problems.append(
                f"  the Total Loan Amount label for {title!r} names {py} years "
                f"({label!r})\n    over a figure covering {cy}")
        if str(cy) not in label:
            problems.append(
                f"  the Total Loan Amount label for {title!r} does not name the "
                f"{cy} years it covers ({label!r})")

    # And the headline metric must be the principal the other three metrics
    # beside it are computed from. It was the undergraduate part under the word
    # "Total": "Total Loan Amount (school-reported) $13,000" next to "Total
    # Interest Paid $417,825" on the same row. Reported from the dashboard,
    # twice, because each number was individually correct.
    for title in professional:
        debt = major_data[title].get("additional_training_debt", 0)
        if not debt:
            continue
        scenario = ns["compute_scenario_results"](
            title, 13_000, 6.5, "Standard 10-Year", col_index=100.0,
            hs_wage_index=1.0, enrollment_years=0, working_years=0,
            baseline_start_age=ns["baseline_start_age_for"](8, 0, title))
        label, value = ns["total_loan_metric"](scenario, 13_000, "reported", 8, 4)
        if abs(value - scenario["effective_principal"]) > 1:
            problems.append(
                f"  the Total Loan Amount metric for {title!r} shows "
                f"${value:,.0f} while the\n    principal every other metric is "
                f"computed from is ${scenario['effective_principal']:,.0f}")
        if "Total" in label and value < scenario["effective_principal"] - 1:
            problems.append(
                f"  {title!r} calls ${value:,.0f} a Total when the total is "
                f"${scenario['effective_principal']:,.0f}")

    # The other direction: a path whose graduate years nothing else pays for
    # must keep every year, or that degree becomes free. "Nothing else" is now
    # two things -- a debt figure, or a funded doctorate where tuition is
    # waived and a stipend is paid -- and the check has to know about both or
    # it fires on the funded paths, which is exactly what it did when they
    # landed.
    for title in sorted(major_data):
        info = major_data[title]
        if info.get("additional_training_debt") or info.get("graduate_years_funded"):
            continue
        py = ns["program_years_for_major"](title)
        gy = ns["graduate_years_for_major"](title)
        if not gy:
            continue
        cy = ns["school_cost_years"](py, gy, title)
        if cy != py:
            problems.append(
                f"  {title!r} has no professional debt figure, so nothing else "
                f"pays for its\n    {gy} graduate years, but the cost model "
                f"charges only {cy} of {py} years: that degree is free")
            break
    return problems


def check_funded_doctorates(ns) -> list:
    """A funded doctorate pays no tuition, is paid a stipend, and borrows
    nothing extra for the degree.

    All three or none. A path marked funded that still charges tuition has
    changed nothing; one that charges no tuition and pays no stipend has
    replaced an overstated cost with an overstated income gap; and one that
    carries professional debt as well is claiming the degree is both free and
    borrowed for.

    The clinical doctorates are asserted from the other side, because the
    tempting mistake is to sweep them in: an AuD, PsyD or DPT is a professional
    practice degree students generally pay for, and marking those funded would
    swap the old error for its mirror image.
    """
    problems = []
    major_data = ns["MAJOR_DATA"]
    funded = [t for t in ns["RESEARCH_DOCTORATE_TITLES"] if t in major_data]

    if len(funded) < 30:
        problems.append(
            f"  only {len(funded)} of the research-doctorate titles exist in "
            f"MAJOR_DATA -- a title that does not match an OEWS string is inert, "
            f"and the path silently keeps paying nine years of tuition")

    for title in funded:
        info = major_data[title]
        py = ns["program_years_for_major"](title)
        gy = ns["graduate_years_for_major"](title)
        if ns["school_cost_years"](py, gy, title) != max(py - gy, 0):
            problems.append(
                f"  {title!r} is funded but still charged the school's COA for "
                f"its {gy} graduate years")
        if not info.get("stipend_salary"):
            problems.append(
                f"  {title!r} pays no tuition and no stipend either: the years "
                f"are free AND earn nothing, which is not what funded means")
        if info.get("additional_training_debt"):
            problems.append(
                f"  {title!r} is marked funded and ALSO carries "
                f"${info['additional_training_debt']:,.0f} of degree debt")
        # Year 0 is the first year after the bachelor's, i.e. the first year of
        # the doctorate. It must pay the stipend, not the professor's salary.
        first = ns["get_annual_salary_for_year"](title, 0)
        if first != info.get("stipend_salary"):
            problems.append(
                f"  {title!r} earns ${first:,.0f} in its first doctoral year, "
                f"not the ${info.get('stipend_salary', 0):,.0f} stipend")

    for title in ("Audiologists", "Clinical and Counseling Psychologists",
                  "Physical Therapists"):
        if title not in major_data:
            continue
        if major_data[title].get("graduate_years_funded"):
            problems.append(
                f"  {title!r} is marked funded. It is a clinical practice "
                f"doctorate students generally pay for, and treating it like a\n"
                f"    research PhD understates its cost by the whole degree.")
    return problems


def check_doctoral_coverage(ns) -> list:
    """Every doctoral occupation is either modelled or knowingly listed.

    The other professional checks in this file all start from
    PROFESSIONAL_PROGRAM_BY_OCCUPATION and verify that what is IN it is
    coherent. None of them can see an occupation that should be in it and is
    not, because an absent title is absent from the map too, so there is
    nothing to iterate over. That blind spot has now shipped three times:
    Dentists, General; Lawyers; and on 2026-08-14 five physicians plus
    Orthodontists, including the two largest physician occupations in OEWS.
    Each was charged nine years of school, given no professional debt, and
    paid a full specialist salary from the year after a bachelor's, while the
    modelled titles beside it looked perfectly correct.

    So this checks the set rather than its members: the doctoral occupations
    in MAJOR_DATA must be exactly the mapped ones plus UNMODELLED_DOCTORAL_TITLES.
    An unrecognised title fails LOUDLY, which is the point, because the
    alternative is a path that silently costs nothing. A retired title fails
    too, since a stale entry in the list is how the list stops describing the
    dataset.
    """
    problems = []
    major_data = ns["MAJOR_DATA"]
    doctoral = {t for t, info in major_data.items()
                if info.get("typical_education") == "Doctoral or professional degree"}
    mapped = set(ns["PROFESSIONAL_PROGRAM_BY_OCCUPATION"]) | set(ns["ADVANCED_TRAINING_OVERLAY"])
    known = set(ns["UNMODELLED_DOCTORAL_TITLES"])

    for title in sorted(doctoral - mapped - known):
        problems.append(
            f"  {title!r} is a doctoral occupation in neither\n"
            f"    PROFESSIONAL_PROGRAM_BY_OCCUPATION nor UNMODELLED_DOCTORAL_TITLES.\n"
            f"    If it needs a professional degree, add it to PHYSICIAN_TITLES /\n"
            f"    DENTIST_TITLES / LAW_TITLES or the programme map, or it is\n"
            f"    charged nine years of school, borrows nothing for the degree\n"
            f"    and earns a full salary from year one. If it is a research or\n"
            f"    clinical doctorate, add it to UNMODELLED_DOCTORAL_TITLES.")

    for title in sorted(known - doctoral):
        problems.append(
            f"  UNMODELLED_DOCTORAL_TITLES lists {title!r}, which is not a\n"
            f"    doctoral occupation in MAJOR_DATA. A title that does not exist\n"
            f"    is inert: it silently stops excusing anything and the list\n"
            f"    stops describing the dataset.")

    overlap = sorted(known & mapped)
    if overlap:
        problems.append(
            f"  {overlap[0]!r} is BOTH modelled and listed as unmodelled -- the\n"
            f"    two registries disagree about whether it has a degree to pay for")
    return problems


def check_residency_modelling(ns) -> list:
    """Charged residencies and disclosed ones must be different sets.

    Two registries make one claim between them: ADVANCED_TRAINING_OVERLAY's
    stipend years say "every graduate of this path serves this", and
    OPTIONAL_RESIDENCY says "some do, and we are not charging for it". A path
    in both charges everyone for something the caption calls optional; a path
    in neither, that should be in one, is silent either way -- deleting
    podiatry's residency moves its earnings four years earlier and raises no
    error anywhere.

    Podiatry is named here rather than derived, deliberately, and the same way
    check_rap_payment_table names the published chart: CPME standardised
    podiatric postgraduate training as a single 36-month residency in 2011 and
    the ABPM certifies only its completers, so 3 years is an external fact this
    file can hold the code to. Deriving it from the overlay would only assert
    that the overlay equals itself.
    """
    problems = []
    overlay = ns["ADVANCED_TRAINING_OVERLAY"]
    optional = ns["OPTIONAL_RESIDENCY"]

    REQUIRED_YEARS = {"Podiatrists": 3}
    for occ, years in REQUIRED_YEARS.items():
        entry = overlay.get(occ, {})
        if entry.get("stipend_training_years") != years:
            problems.append(
                f"  {occ!r} must serve a required {years}-year residency "
                f"(CPME 36-month PMSR); the overlay says "
                f"{entry.get('stipend_training_years', 0)}")
        if not entry.get("stipend_salary"):
            problems.append(
                f"  {occ!r} serves a residency at a stipend of 0 -- residents "
                f"are salaried house staff, not unpaid")

    both = set(optional) & {o for o, e in overlay.items()
                            if e.get("stipend_training_years")}
    for occ in sorted(both):
        problems.append(
            f"  {occ!r} is charged a residency AND disclosed as not charged "
            f"for one\n    the sidebar caption and the earnings curve now "
            f"contradict each other")

    # A disclosed residency must be genuinely absent from the arithmetic, and
    # the sentence must actually name a figure -- an empty disclosure is worse
    # than none, since the path then looks like it has no residency at all.
    for occ in optional:
        if overlay.get(occ, {}).get("stipend_training_years"):
            continue                       # already reported above
        text = ns["optional_residency_disclosure"](occ)
        if not text or "$" not in text:
            problems.append(
                f"  {occ!r} discloses no stipend figure: {text!r}")
    return problems


def check_program_lengths(ns) -> list:
    """One length per path, and the two modes must price the same life alike.

    GRADUATE_ADDITIONAL_YEARS' 5 is a FALLBACK for paths whose length this app
    does not know, and reading it where a real length exists is how a single
    scenario came to carry two: a physician's cost and graduate cap sized on 5
    years while the debt cap and the earnings delay used 4. Nothing on screen
    showed the disagreement -- both halves looked right on their own.
    """
    problems = []
    MD = ns["MAJOR_DATA"]
    undergrad = ns["UNDERGRAD_YEARS"]

    # 1. Cost and earnings read one number. `unpaid_training_years` is the
    #    length of post-bachelor's school for every path that has one, so the
    #    graduate half of the cost must equal it.
    for major, entry in sorted(MD.items()):
        school = entry.get("unpaid_training_years", 0)
        if not school:
            continue
        grad = ns["graduate_years_for_major"](major)
        if grad != school:
            problems.append(
                f"  {major!r} is charged {grad} graduate year(s) of cost but "
                f"attends {school}\n    the loan, the graduate cap and the "
                f"foregone earnings all use the first; the debt cap and the "
                f"earnings delay use the second")

    # 2. The total is the undergraduate years plus that half, always. Anything
    #    else means a call site added its own arithmetic.
    for major, entry in sorted(MD.items()):
        grad = ns["graduate_years_for_major"](major)
        if not grad:
            continue
        total = ns["program_years_for_major"](major)
        if total != undergrad + grad:
            problems.append(
                f"  {major!r}: total {total} != {undergrad} + {grad} graduate")

    # 3. The same life must cost the same in both modes. These pairs are a
    #    curated MAJOR and the OCCUPATION it leads to; "Medicine" resolving to
    #    four undergraduate years while Family Medicine Physicians resolved to
    #    nine is the contradiction ADVANCED_TRAINING_OVERLAY exists to prevent,
    #    and it survived on the major side for as long as that overlay has.
    TWINS = [("Medicine", "Family Medicine Physicians"),
             ("Law", "Lawyers"),
             ("Athletic Training", "Athletic Trainers")]
    for major, occupation in TWINS:
        if major not in MD or occupation not in MD:
            problems.append(f"  fixture: {major!r}/{occupation!r} not in MAJOR_DATA")
            continue
        for label, fn in (("total years", ns["program_years_for_major"]),
                          ("graduate years", ns["graduate_years_for_major"])):
            if fn(major) != fn(occupation):
                problems.append(
                    f"  {major!r} and {occupation!r} disagree about {label}: "
                    f"{fn(major)} vs {fn(occupation)}\n    one life, two "
                    f"prices, decided by which dropdown the visitor used")
        if (MD[major].get("unpaid_training_years", 0)
                != MD[occupation].get("unpaid_training_years", 0)):
            problems.append(
                f"  {major!r} and {occupation!r} disagree about when earnings "
                f"start")

    # 4. Every occupation that attends a professional school has a curated
    #    length. Without one it silently falls back to 5 -- and the fallback is
    #    wrong for all nine programmes.
    for occupation, programme in sorted(
            ns["PROFESSIONAL_PROGRAM_BY_OCCUPATION"].items()):
        if occupation not in MD:
            continue                      # reported by check_professional_paths
        if not ns["curated_school_years"](occupation):
            problems.append(
                f"  {occupation!r} attends {programme} school with no curated "
                f"length, so it falls back to "
                f"{ns['GRADUATE_ADDITIONAL_YEARS']['Doctoral or professional degree']} "
                f"years -- which is no programme's real length")
    return problems


def check_shared_controls_have_per_tool_keys(_ns) -> list:
    """render_search_controls runs TWICE in one script run, so no key it
    creates may be a bare string.

    The calculator's "More tools" renders the undergraduate search and the
    graduate one back to back. Streamlit raises StreamlitDuplicateElementKey on
    the second widget with a repeated key and the script dies there -- and
    because the results above had already drawn, the page looks half-built
    rather than broken. That shipped: `key="search_cip_family"` took down the
    calculator for every visitor who reached that section.

    It is invisible to every other guard in this repo, which exec sections 1-2
    and never render anything. Checked statically instead: read the keyword
    arguments inside the function and require each `key=` to be an expression,
    not a constant. A per-tool name has to be computed, so "computed" is a
    sound proxy for "distinct per tool".

    The reverse -- that the values still travel between the two tools -- is not
    checked here, because it is a seeding behaviour rather than a shape. It is
    verified in a browser: the graduate keys setdefault from the undergraduate
    ones on first render.
    """
    problems = []
    tree = ast.parse(open("app.py").read())
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef)
               and n.name == "render_search_controls"), None)
    if fn is None:
        return ["  render_search_controls is gone; this check is inert"]

    literal_keys = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "key" and isinstance(kw.value, ast.Constant):
                literal_keys.append((kw.value.value, getattr(node, "lineno", "?")))
    for key, line in literal_keys:
        problems.append(
            f"  app.py:{line} creates key={key!r} as a literal\n"
            f"    this function renders twice per run on the calculator page, "
            f"so the second one raises StreamlitDuplicateElementKey and the "
            f"page dies mid-render")

    # A BUTTON's key must not go through tool_key. That helper seeds the
    # graduate copy by ASSIGNING to it, and Streamlit forbids assigning to a
    # button's key -- StreamlitValueAssignmentNotAllowedError the moment "More
    # tools" renders the undergraduate search (leaving search_region_* in
    # session_state) and then the graduate one. The page half-drew and died.
    # tool_button_key applies the same prefix and does no seeding.
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "button"):
            continue
        for kw in node.keywords:
            if kw.arg != "key":
                continue
            called = (kw.value.func.id
                      if isinstance(kw.value, ast.Call)
                      and isinstance(kw.value.func, ast.Name) else None)
            if called == "tool_key":
                problems.append(
                    f"  app.py:{getattr(node, 'lineno', '?')} keys a button "
                    f"with tool_key()\n"
                    f"    tool_key SEEDS by assignment and Streamlit forbids "
                    f"that on a button key; use tool_button_key")
            elif called != "tool_button_key":
                problems.append(
                    f"  app.py:{getattr(node, 'lineno', '?')} keys a button "
                    f"with {called or 'something unrecognised'}\n"
                    f"    both searches render in one run, so a button key "
                    f"must be per-tool via tool_button_key")
    return problems


def check_discipline_map_keys(ns):
    """Every MAJOR_TO_CIP_DISCIPLINE key is a real major, every value a real
    discipline.

    A key that matches no major is INERT, not broken: the lookup succeeds, the
    prefill never happens, and nothing anywhere raises. An entry for
    "Biomedical Engineering" was written and caught by this check before it
    shipped -- there is no such NY Fed major.
    """
    problems = []
    majors = set(ns["MAJOR_TO_CIP_FAMILY"])
    for major in ns["MAJOR_TO_CIP_DISCIPLINE"]:
        if major not in majors:
            problems.append(
                f"  MAJOR_TO_CIP_DISCIPLINE key {major!r} is not a major\n"
                "    a key nothing matches is inert: the prefill silently never "
                "happens and nothing raises")
    outcomes = ns["load_discipline_outcomes"]()
    if not outcomes.empty:
        shipped = set(outcomes["discipline_key"])
        for major, key in ns["MAJOR_TO_CIP_DISCIPLINE"].items():
            if key not in shipped:
                problems.append(
                    f"  {major!r} prefills discipline {key!r}, which the dataset "
                    f"does not ship\n"
                    "    the reconcile clears it, so the prefill is dead code "
                    "rather than a wrong answer -- but it should be removed or "
                    "the discipline shipped")
    return problems


def check_field_debt_column(ns, base) -> list:
    """The per-field borrowing figure must ADD a column and change nothing else.

    It is attached by a merge, and a merge is the easiest way to accidentally
    build a filter: an inner join would silently drop every school that
    publishes no figure for the field -- half of them in computing and
    engineering -- turning a display column into the narrowest filter in the
    search, with the result count still looking plausible. The whole search
    would then answer a different question than the one the visitor asked.

    So the properties are about what did NOT change: same schools, same order,
    same count. Plus the level gate, because the debt file has no associate's
    or certificate rows at all and a column of dashes reads as "these graduates
    borrow nothing".
    """
    problems = []
    search_fn = ns["search_schools_by_budget"]
    family, credential = "11", "Bachelor's degree"

    with_debt = search_fn(family, credential, 60_000, home_state="CA", limit=25)
    if "field_debt_median" not in with_debt.columns:
        return ["  a bachelor's search carries no field_debt_median column"]

    # 1. Membership and order are untouched. Compared against the SAME search
    #    with the join disabled -- monkeypatching the gate rather than the
    #    merge, so this measures the merge itself.
    real_gate = ns["field_debt_applies"]
    ns["field_debt_applies"] = lambda credential: False
    try:
        without = search_fn.__wrapped__(family, credential, 60_000,
                                        home_state="CA", limit=25) \
            if hasattr(search_fn, "__wrapped__") else search_fn(
                family, credential, 60_000, home_state="CA", limit=25)
    finally:
        ns["field_debt_applies"] = real_gate
    if list(without["UNITID"]) != list(with_debt["UNITID"]):
        problems.append(
            f"  the join changed the result set: {len(without)} schools without "
            f"it, {len(with_debt)} with\n    it is a display column and must "
            f"not filter -- an inner join drops every school with no figure")
    if not with_debt["coa_per_year"].is_monotonic_increasing:
        problems.append("  the join broke the cost ordering")

    # 2. It must actually populate, or the join key is wrong and every cell
    #    reads "—" while looking like honest missing data.
    if with_debt["field_debt_median"].notna().sum() == 0:
        problems.append(
            "  no row carries a figure; a wrong join key looks exactly like a "
            "field nobody publishes")

    # 3. The level gate. These rows exist only at CREDLEV 3.
    for credential, want in [("Bachelor's degree", True),
                             ("Associate's degree", False)]:
        if ns["field_debt_applies"](credential) != want:
            problems.append(f"  field_debt_applies({credential!r}) != {want}")
    lower = search_fn(family, "Associate's degree", 60_000, home_state="CA", limit=25)
    if not lower.empty and "field_debt_median" in lower.columns:
        problems.append(
            "  an associate's search carries the column, which can only render "
            "as a full column of dashes")

    # 4. Values must be per-FIELD, not the institution-wide figure repeated.
    #    Two different fields at the same school should generally differ; if
    #    they never do, the join has collapsed to something school-level.
    other = search_fn("52", "Bachelor's degree", 60_000, home_state="CA", limit=25)
    shared = set(with_debt["UNITID"]) & set(other["UNITID"])
    if shared:
        a = with_debt.set_index("UNITID")["field_debt_median"]
        b = other.set_index("UNITID")["field_debt_median"]
        both = [u for u in shared if pd.notna(a.get(u)) and pd.notna(b.get(u))]
        if both and all(a[u] == b[u] for u in both):
            problems.append(
                f"  computing and business report identical debt at all "
                f"{len(both)} shared school(s) -- the figure is not per-field")
    return problems


def check_search_level_catalog(ns) -> list:
    """Every level a search can log must have an admin label, and the action
    must still carry the two fields the admin parser reads.

    Both tools emit one school_search_run with `level` as the only
    discriminator, so that field is what separates an undergraduate search from
    a graduate or professional one after the fact. Two ways that breaks
    silently:

      * A level added to a registry and not to search_level_catalog renders as
        "Unknown" in the admin table -- indistinguishable from a parsing bug,
        and it lands in the one place nobody double-checks.
      * The action string is built in one f-string and read by a regex ~1,500
        lines away. Drop `:level=` or `:n=` from the format and the table goes
        quietly empty, which reads as "nobody searched".
    """
    problems = []
    catalog = ns["search_level_catalog"]()

    registries = [
        ("CREDENTIAL_LEVELS", ns["CREDENTIAL_LEVELS"], 0),
        ("GRADUATE_CREDENTIAL_LEVELS", ns["GRADUATE_CREDENTIAL_LEVELS"], 0),
        ("FIXED_FIELD_GRADUATE_LEVELS", ns["FIXED_FIELD_GRADUATE_LEVELS"], 0),
        ("PROFESSIONAL_SEARCH_LEVELS", ns["PROFESSIONAL_SEARCH_LEVELS"], 0),
    ]
    for name, registry, key_index in registries:
        for label, value in registry.items():
            key = value[key_index]
            if key not in catalog:
                problems.append(
                    f"  {name}[{label!r}] logs level={key!r}, which has no "
                    f"admin label\n    it renders as \"Unknown\" beside real "
                    f"levels and looks like a parsing bug")
                continue
            tool, _shown = ns["search_level_label"](key)
            if tool == "Unknown":
                problems.append(
                    f"  level={key!r} resolves to the Unknown bucket")

    # An unrecognised level must be REPORTED, not folded into a neighbour --
    # rows predating a rename carry levels that no longer exist, and absorbing
    # them overstates whichever bucket takes them.
    tool, shown = ns["search_level_label"]("level_that_never_existed")
    if tool != "Unknown" or "level_that_never_existed" not in shown:
        problems.append(
            f"  a retired level was bucketed as {tool!r} instead of surfaced")

    # The two fields the admin regex depends on must still be in the action.
    src = open("app.py").read()
    start = src.index("def _log_school_search")
    body = src[start:src.index("\ndef ", start + 1)]
    for fragment, why in ((":level=", "separates the two search tools"),
                          (":n=", "carries the result count")):
        if fragment not in body:
            problems.append(
                f"  the school_search_run action no longer contains "
                f"{fragment!r}, which {why}\n    the admin table parses it "
                f"with a regex and would go quietly empty")
    return problems


def check_net_price_and_completion(ns, base) -> list:
    """The two display-only columns, and the sector split that hides in them.

    Scorecard reports net price as NPT4_PUB or NPT4_PRIV and completion as
    C150_4 or C150_L4, never both sides of either pair. A consumer reading only
    one loses half the dataset in a way that looks like missing data rather
    than a bug: every private school reports no NPT4_PUB, every two-year school
    no C150_4. The pipeline coalesces them; this asserts the coalescing
    actually happened on BOTH sides.

    They are DISPLAY ONLY. Net price is an average over aided students, so
    filtering or sorting on it would promise a discount not everyone gets --
    the sticker stays the affordability test, and the ordering must not move.
    """
    problems = []
    coa = ns["load_coa_dataset"]()
    for column in ("net_price", "completion_rate"):
        if column not in coa.columns:
            problems.append(
                f"  {column} is missing from the dataset -- rebuild it with "
                f"clean_college_scorecard.py")
    if problems:
        return problems

    # Coverage, per sector and per length. A one-sided coalesce shows up here
    # as a whole category reporting nothing.
    for label, mask in (("public", coa["control_type"] == "Public"),
                        ("private", coa["control_type"].str.startswith("Private"))):
        share = coa.loc[mask, "net_price"].notna().mean()
        if share < 0.5:
            problems.append(
                f"  only {share:.0%} of {label} schools have a net price\n"
                f"    Scorecard splits this column by sector; a coalesce that "
                f"missed one side looks exactly like missing data")
    if coa["completion_rate"].notna().mean() < 0.8:
        problems.append(
            f"  only {coa['completion_rate'].notna().mean():.0%} of schools "
            f"have a completion rate; both C150 columns should be coalesced")

    # Rates are rates, not percentages. A 58 where 0.58 belongs renders as
    # "5800%" and nothing else would catch it.
    rates = coa["completion_rate"].dropna()
    if len(rates) and (rates.max() > 1.0 or rates.min() < 0.0):
        problems.append(
            f"  completion_rate ranges {rates.min():.2f}-{rates.max():.2f}; "
            f"it must be a 0-1 fraction, which the display formats as a %")

    # Display-only: the ordering is cost and nothing else.
    if not base["coa_per_year"].is_monotonic_increasing:
        problems.append("  the result set is no longer cost-ordered")
    # And net price must NOT have become the filter: a search whose ceiling is
    # below a school's sticker but above its net price must still exclude it.
    ceiling = 20_000
    rows = ns["search_schools_by_budget"]("11", "Bachelor's degree", ceiling,
                                          home_state="CA", limit=200)
    if not rows.empty and (rows["coa_per_year"] > ceiling).any():
        problems.append(
            "  a school priced above the ceiling came back -- the budget "
            "filter is reading something other than the sticker")
    return problems


def check_pdf_columns_fit_their_content(ns):
    """No shortlist column may be narrower than its widest unbreakable word.

    _pdf_table scales a wide table down to the page, so it can never overflow
    horizontally -- which is why this failed silently. What it CAN do is squeeze
    a column below the width of "$45,619", and money does not wrap: it prints
    the mid-number split ("$54,30 0") that sending this report landscape was
    supposed to end. Adding the Outcomes column took the shortlist from ten
    columns to eleven and put NINE of them under their own text.

    Asserted against the widest whitespace-delimited word rather than the widest
    cell, because that is the real constraint: a school name wraps and loses
    nothing, a money figure cannot break at all.
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth
    problems = []
    width = ns["PDF_CONTENT_WIDTH_LANDSCAPE"]
    size = ns["PDF_CELL_FONT_SIZE"]
    pad = ns["PDF_CELL_H_PADDING"]
    results = ns["search_schools_by_budget"](
        "52", "Bachelor's degree", max_coa_per_year=50_000, limit=25,
        home_state="CA", states=("CA",), discipline_key="business",
        sort_mode="Outcomes")
    if results.empty:
        return ["  the fixture search returned nothing, so nothing was checked"]
    money = ns["fmt_money"]
    rows = [["School", "Where", "Type", "Per year", "Avg net price",
             "Whole program", "Admits", "Finish", "Parents borrowed",
             "Grads borrowed", "Outcomes"]]
    for row in results.itertuples():
        rows.append([
            row.INSTNM, f"{row.CITY}, {row.STABBR}", row.control_type,
            money(row.coa_per_year), money(row.net_price),
            money(row.total_program_cost), "50%", "50%",
            money(row.PLUS_DEBT_INST_COMP_MD), money(row.field_debt_median),
            "100"])
    table = ns["_pdf_table"](rows, full_width=True, content_width=width)
    widths = table._colWidths
    for index, header in enumerate(rows[0]):
        need = max(
            stringWidth(word, "Helvetica-Bold" if r == 0 else "Helvetica", size)
            for r, row in enumerate(rows)
            for word in (str(row[index]).split() or [""])) + pad
        if widths[index] + 0.5 < need:
            problems.append(
                f"  PDF column {header!r} is {widths[index]:.0f}pt but its "
                f"widest unbreakable word needs {need:.0f}pt\n"
                "    money does not wrap, so it prints the mid-number split "
                "this report went landscape to avoid")
    return problems


def check_pdf_table_repeats_header(ns) -> list:
    """A table that spills onto a second page must reprint its header there.

    The shortlist PDF is the report this matters for: 25 schools across three
    landscape pages, and pages 2 and 3 without a header are a grid of bare
    figures -- three money columns, two percentages, two more money columns --
    in an order the reader cannot recover. Nothing on page 1 looks wrong, which
    is why this needs a check rather than an eye.

    The negative half matters as much: header=False means column 0 is the bold
    key of a key/value table, and repeating row 0 there would restate one
    arbitrary pair at the top of every page as though it were a heading.
    """
    problems = []
    build = ns["_pdf_table"]

    with_header = build([["School", "Per year"], ["A", "$1"], ["B", "$2"]],
                        header=True)
    if getattr(with_header, "repeatRows", 0) != 1:
        problems.append(
            "  a header table does not set repeatRows=1, so a shortlist "
            "spanning pages loses its column names after page 1")

    key_value = build([["Field", "Computer Science"], ["Level", "Bachelor's"]],
                      header=False)
    if getattr(key_value, "repeatRows", 0):
        problems.append(
            "  a key/value table repeats its first ROW, which is a data pair "
            "rather than a heading")
    return problems


def check_apply_target(ns) -> list:
    """Where an applied school lands, and when it must refuse instead.

    This is the property the user reported broken: "when i click use graduate
    school, only the school is populated". Both sidebar pickers RESET a value
    they do not recognise back to their default, so aiming at the wrong one
    fails silently -- no exception, no message, a sidebar that looks like it
    ignored the click. Nothing here can be caught by reading a stack trace.

    The whole reason graduate_apply_target is a pure section-2 function is so
    this can run at all: it used to be ~30 lines inline in a section-5
    renderer, unreachable by any guard.
    """
    problems = []
    target = ns["graduate_apply_target"]
    med = next(k for k, v in ns["PROFESSIONAL_PROGRAM_BY_OCCUPATION"].items()
               if v == "medicine")
    priced = ns["search_professional_schools_by_budget"]("medicine", 1_000_000,
                                                         limit=400)
    listed = set(ns["professional_schools_for"]("medicine"))
    named = next((n for n in priced["picker_name"] if n in listed), None)
    unlisted = next((n for n in priced["picker_name"] if n not in listed), None)
    if named is None or unlisted is None:
        problems.append(
            "  fixture: medicine has no school of one of the two kinds, so the "
            "listed/unlisted split discriminates nothing")
        return problems

    # 1. A professional school the picker CAN name goes to prof_school_a.
    got, blocked = target(True, "medicine", None, None, named, named,
                          300_000, med)
    if blocked or got is None or got[0] != "prof_school_a":
        problems.append(
            f"  a medical school went to {got and got[0]!r}, not prof_school_a\n"
            f"    the graduate picker stocks CIP families and would drop it")

    # 2. One it cannot name is carried by price instead, never dropped and
    #    never aimed at a picker that has no such option.
    got, blocked = target(True, "medicine", None, None, unlisted, unlisted,
                          300_000, med)
    if blocked or got is None or got[0] is not None or got[3] != 300_000:
        problems.append(
            f"  {unlisted!r} has no debt row, so its PRICE must be carried; "
            f"got {got!r} / {blocked!r}")

    # 3. A subject the sidebar has no field for must SAY so, not apply.
    got, blocked = target(True, "medicine", None, None, named, named,
                          300_000, "Accounting")
    if got is not None or not blocked:
        problems.append(
            "  applying a medical school to an accounting scenario was not "
            "refused -- prof_school_a would reset and nothing would say why")

    # 4. Graduate: right field applies, wrong field refuses. Both from the
    #    real crosswalk, so a rewrite of it re-tests this.
    major, family = next(iter(sorted(ns["MAJOR_TO_CIP_FAMILY"].items())))
    other = next(m for m, f in ns["MAJOR_TO_CIP_FAMILY"].items() if f != family)
    got, blocked = target(False, None, family, "Master's degree",
                          "Some University", "Some University", 60_000, major)
    if blocked or got is None or got[0] != "grad_school_a":
        problems.append(f"  a same-field master's did not reach grad_school_a: "
                        f"{got!r} / {blocked!r}")
    elif got[2] != ns["GRADUATE_SEARCH_TO_CREDENTIAL"]["Master's degree"]:
        problems.append(f"  the applied credential is {got[2]!r}, which is not "
                        f"what the Level control searched")
    got, blocked = target(False, None, family, "Master's degree",
                          "Some University", "Some University", 60_000, other)
    if got is not None or not blocked:
        problems.append(
            f"  a {ns['MAJOR_TO_CIP_FAMILY'][other]} scenario accepted a CIP "
            f"{family} school; that picker does not stock it")

    # 5. Exactly one of the two is ever set. A renderer reading the pair would
    #    otherwise both apply and complain, or do neither.
    for case in [(True, "medicine", None, None, named, named, 1, med),
                 (True, "medicine", None, None, unlisted, unlisted, 1, med),
                 (True, "medicine", None, None, named, named, 1, "Accounting"),
                 (False, None, family, "Master's degree", "S", "S", 1, major),
                 (False, None, family, "Master's degree", "S", "S", 1, other)]:
        got, blocked = target(*case)
        if bool(got) == bool(blocked):
            problems.append(f"  {case[7]!r}: target and blocked are both "
                            f"{'set' if got else 'empty'}")
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



# ---------------------------------------------------------------------------
# PPD:2026 on the search and the sidebar. Every row of that file is a claim
# about a real school's federal loan eligibility, ED calls it preliminary, and
# absence in it is common and means nothing. So the checks are about what the
# surfaces may NOT say: "clear" for a school that is merely absent, a verdict
# for a flag, or a count over anything but the rows on screen.
# ---------------------------------------------------------------------------
def _ppd_fixture_ids():
    import pandas as pd
    f = pd.read_csv("data/ppd_program_flags.csv", dtype={"OPEID6": "str"})
    coa = pd.read_csv("data/college_coa_clean.csv", usecols=["UNITID"])
    beauty = f[(f.master_fail == 1) & (f.CREDLEV == 1) & (f.CIPCODE // 100 == 12)
               & f.UNITID.isin(coa.UNITID)]
    single = int(beauty[beauty.campuses == 1].UNITID.iloc[0])
    inherited = int(beauty[beauty.campuses > 1].UNITID.iloc[0])
    return f, single, inherited


def check_ppd_absence_is_unknown(ns) -> list:
    """{} for a school not in the data, never a zero-filled dict, and both
    captions silent for it. A zero-filled dict is how "not flagged" gets
    rendered out of a lookup that found nothing."""
    problems = []
    status = ns["ppd_program_status"]
    if status(999_999, 1, "12") != {}:
        problems.append("  ppd_program_status returns something for a school not "
                        "in the data; absence must be {} (unknown), never 'clear'")
    if ns["ppd_school_caption"](999_999, "Postsecondary nondegree award", "x") != "":
        problems.append("  the sidebar note speaks about a school PPD has never seen")
    _, single, _ = _ppd_fixture_ids()
    st = status(single, 1, "12")
    if not st or st.get("flagged", 0) < 1:
        problems.append("  a known flagged beauty school reports no flag")
    if ns["ppd_school_caption"](single, "High school diploma or equivalent", "x") != "":
        problems.append("  the sidebar note speaks at a level PPD does not assess")
    return problems


def check_ppd_caption_counts_rows_on_screen(ns) -> list:
    """The count is over the rows shown, recomputed here from the file, and
    the caption is silent when no listed school is flagged."""
    import pandas as pd
    problems = []
    f, _, _ = _ppd_fixture_ids()
    res = ns["search_schools_by_budget"]("12", "Certificate (under 1 year)", 60_000, limit=25)
    cap = ns["ppd_search_caption"](res, "Certificate (under 1 year)", "12")
    hit = f[(f.master_fail == 1) & (f.CREDLEV == 1) & (f.CIPCODE // 100 == 12)
            & f.UNITID.isin(res.UNITID)].UNITID.nunique()
    if hit == 0:
        problems.append("  the cosmetology fixture has no flagged school on screen; "
                        "the fixture drifted")
    elif f"**{hit} of the {len(res)} shown**" not in cap:
        problems.append(f"  caption count does not match the rows on screen: "
                        f"expected {hit} of {len(res)}, caption reads {cap[:60]!r}")
    quiet = ns["search_schools_by_budget"]("11", "Bachelor's degree", 60_000, limit=25)
    if ns["ppd_search_caption"](quiet, "Bachelor's degree", "11") != "":
        problems.append("  the caption speaks on a search with no flagged school shown")
    return problems


def check_ppd_wording(ns) -> list:
    """Preliminary, dated, and never a determination, on both surfaces."""
    problems = []
    _, single, inherited = _ppd_fixture_ids()
    res = ns["search_schools_by_budget"]("12", "Certificate (under 1 year)", 60_000, limit=25)
    texts = {
        "search caption": ns["ppd_search_caption"](res, "Certificate (under 1 year)", "12"),
        "sidebar note": ns["ppd_school_caption"](single, "Postsecondary nondegree award", "x"),
    }
    # The caveat is ONE shared sentence, and each surface must carry it
    # verbatim. The first version of this check looked for the word
    # "preliminary" anywhere in the text, and a control that stripped it from
    # the shared caveat passed, because both surfaces also use the word in
    # their own lead sentence. An inconclusive control is worse than none.
    caveat = ns["ppd_caveat"]()
    if "preliminary" not in caveat.lower() or "2027" not in caveat:
        problems.append("  the shared caveat no longer says preliminary and dated")
    for where, t in texts.items():
        low = t.lower()
        if caveat not in t:
            problems.append(f"  the {where} does not carry the shared caveat verbatim")
        for banned in ("will lose", "loses eligibility", "has lost", "is ineligible"):
            if banned in low:
                problems.append(f"  the {where} states a determination: {banned!r}")
    inh = ns["ppd_school_caption"](inherited, "Postsecondary nondegree award", "x")
    if "campuses" not in inh:
        problems.append("  a flag inherited across a Title IV certification is not "
                        "worded as one; it reads as a campus fact")
    return problems


def check_ppd_never_a_sort_key(ns) -> list:
    """Display only. Ranking on it would rank schools by other people's
    earnings against a metric ED calls provisional."""
    import inspect
    problems = []
    if any("fail" in str(v).lower() or "ppd" in str(v).lower()
           for v in ns["SEARCH_SORT_MODES"]):
        problems.append("  a PPD flag is offered as a sort mode")
    src = inspect.getsource(ns["search_schools_by_budget"])
    if "master_fail" in src or "obbb_fail" in src or "load_ppd_flags" in src:
        problems.append("  search_schools_by_budget reads the PPD flags; they must "
                        "stay out of the search, filter and sort entirely")
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
        ("match count reported", lambda: check_match_count_reported(ns, base)),
        ("sort before cap", lambda: check_sort_before_cap(ns)),
        ("sort excludes judgement columns",
            lambda: check_sort_excludes_judgement_columns(ns)),
        ("name filter", lambda: check_name_filter(ns, base)),
        ("regions partition", lambda: check_regions_partition(ns)),
        ("plus debt sample size", lambda: check_plus_debt_sample_size(ns)),
        ("credential gate", lambda: check_credential_gate(ns)),
        ("graduate search", lambda: check_graduate_search(ns)),
        ("picker identity", lambda: check_picker_identity(ns, base)),
        ("apply target", lambda: check_apply_target(ns)),
        ("fixed-field levels", lambda: check_fixed_field_levels(ns)),
        ("professional paths", lambda: check_professional_paths(ns)),
        ("professional cost years", lambda: check_professional_cost_years(ns)),
        ("funded doctorates", lambda: check_funded_doctorates(ns)),
        ("doctoral coverage", lambda: check_doctoral_coverage(ns)),
        ("residency modelling", lambda: check_residency_modelling(ns)),
        ("program lengths", lambda: check_program_lengths(ns)),
        ("field debt column", lambda: check_field_debt_column(ns, base)),
        ("discipline map keys", lambda: check_discipline_map_keys(ns)),
        ("pdf columns fit", lambda: check_pdf_columns_fit_their_content(ns)),
        ("pdf header repeats", lambda: check_pdf_table_repeats_header(ns)),
        ("ppd absence is unknown", lambda: check_ppd_absence_is_unknown(ns)),
        ("ppd counts rows on screen", lambda: check_ppd_caption_counts_rows_on_screen(ns)),
        ("ppd wording", lambda: check_ppd_wording(ns)),
        ("ppd never a sort key", lambda: check_ppd_never_a_sort_key(ns)),
        ("net price and completion",
         lambda: check_net_price_and_completion(ns, base)),
        ("search level catalog", lambda: check_search_level_catalog(ns)),
        ("per-tool widget keys",
         lambda: check_shared_controls_have_per_tool_keys(ns)),
        ("programmes without debt", lambda: check_programmes_without_debt(ns)),
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
          f"separately, applies reach the picker that stocks them, "
          f"admit rate gated to "
          f"{'/'.join(ns['ADM_RATE_CREDENTIALS'])}, picker keyed on UNITID.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

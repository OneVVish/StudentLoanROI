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
        ("apply target", lambda: check_apply_target(ns)),
        ("fixed-field levels", lambda: check_fixed_field_levels(ns)),
        ("professional paths", lambda: check_professional_paths(ns)),
        ("doctoral coverage", lambda: check_doctoral_coverage(ns)),
        ("residency modelling", lambda: check_residency_modelling(ns)),
        ("program lengths", lambda: check_program_lengths(ns)),
        ("field debt column", lambda: check_field_debt_column(ns, base)),
        ("pdf header repeats", lambda: check_pdf_table_repeats_header(ns)),
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

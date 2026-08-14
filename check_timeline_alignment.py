#!/usr/bin/env python3
"""Guard: both sides of the ROI comparison must agree what timeline year 0 is.

    python3 check_timeline_alignment.py     (exit 1 on any mismatch)

calculate_roi sums the graduate's earnings and the high-school baseline's
earnings over the SAME year index. Nothing makes the two agree about what that
index means, and for twelve days they did not.

    hs_cumulative_earnings = sum(baseline_wage(y) for y in range(...))
    major_cumulative_earnings = sum(get_annual_salary_for_year(m, y) for y in ...)

get_annual_salary_for_year's year 0 is the first year after the BACHELOR'S for
any occupation carrying an ADVANCED_TRAINING_OVERLAY entry: the overlay picks
the timeline up there and spends the graduate years at $0 (unpaid school) or on
a stipend (a funded doctorate, or a residency). baseline_start_age_for's year 0
was `18 + program_years`, the age at the END of the whole programme.

Those were the same number until 2026-08-02. baseline_start_age_for shipped on
2026-07-30, when PROGRAM_YEARS_BY_EDUCATION had no graduate lengths and every
professional path was four undergraduate years, so `18 + 4` landed exactly where
the earnings curve started. "Model graduate program length" then gave doctoral
and professional paths 9 and 6 years. The baseline moved with them. The earnings
curve did not.

The result, for a dentist on the default 10-year window:

    yr  dentist earns  real age    HS baseline age
     0$             0        22                 26
     3$             0        25                 29
     4$       125,710        26                 30
     9$       146,595        31                 35

The dental-school years were charged twice -- once by aging the baseline past
them, once by zeroing the salary inside the window -- so only 6 of the 10 window
years were practicing years while the baseline drew a full 10 at ages 26-35. It
understated 74 of 836 occupations (2-year gap on 2, 3-year on 5, 4-year on 28,
5-year on 39), and with foregone earnings counted it flipped the SIGN of the
earnings premium on four of the six paths measured: a dentist read -$180,222
where the aligned model reads +$85,541.

Nothing caught it, and nothing could have. Each half is correct read alone: the
baseline really is the graduate's age at the end of the programme, and the
earnings curve really does start at the bachelor's. py_compile, the share guard,
check_repayment_invariants and all four PDFs pass either way, because no money
is lost -- the two series are simply describing different calendar years. Only a
check that holds them against each other can see it, which is this file.

WHY THE EXPECTATIONS ARE BUILT FROM THE RAW CONSTANTS. Every figure below comes
from ADVANCED_TRAINING_OVERLAY / CURATED_MAJOR_DATA read directly, never from
overlay_school_years or pre_earnings_years -- the functions under test. Deriving
the expectation from the thing being checked is the flaw already recorded
against the first versions of check_chart_axes and the residency check in
check_school_search_filters: it asserts only that the code equals itself, and a
sign flip passes.
"""
import ast
import sys

UNDERGRAD = 4          # UNDERGRAD_YEARS, restated so a change to it is visible here
HS_START = 18          # HS_GRAD_START_AGE, same reason
WINDOW = 10            # ROI_WINDOW_YEARS


def load_app_namespace():
    """app.py's sections 1-2, without the UI. Same exec-prefix trick
    analyze_model.py uses -- see CLAUDE.md on why the section banners are
    load-bearing."""
    src = open("app.py").read()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    prefix = src[:src.rindex("# " + "=" * 60, 0, cut)]
    ns = {"__name__": "timelinealigncheck"}
    exec(compile(prefix, "app.py", "exec"), ns)
    # The REAL MAJOR_DATA, not an empty dict: the curated overlay entries are
    # merged in by build_major_data, and against {} every occupation would look
    # like a plain bachelor's path and this whole check would pass vacuously.
    ns["MAJOR_DATA"] = ns["build_major_data"](ns["CAREERS_CSV_PATH_NATIONAL"])
    return ns


def raw_overlay(ns, title):
    """The overlay's own numbers for this title, read straight from the
    section-1 constants. (unpaid_school, stipend_years, funded)."""
    for source in (ns["ADVANCED_TRAINING_OVERLAY"], ns["CURATED_MAJOR_DATA"]):
        entry = source.get(title, {})
        if entry.get("unpaid_training_years") or entry.get("stipend_training_years"):
            return (int(entry.get("unpaid_training_years") or 0),
                    int(entry.get("stipend_training_years") or 0),
                    bool(entry.get("graduate_years_funded")))
    return (0, 0, False)


def expected_year_zero_age(ns, title, program_years):
    """The age the graduate REALLY is in timeline year 0, derived independently.

    An overlay picks the timeline up immediately after the bachelor's -- that is
    what "post-bachelor's" means in get_annual_salary_for_year's docstring and
    in every curated entry -- so year 0 is age 18+4. With no overlay the earnings
    curve is a plain salary from the first working year, which is the end of the
    whole programme.
    """
    unpaid, stipend, funded = raw_overlay(ns, title)
    school_in_overlay = unpaid + (stipend if funded else 0)
    return HS_START + (UNDERGRAD if school_in_overlay else program_years)


def check_alignment(ns, start_age_fn):
    """Every occupation: the baseline's age in year 0 == the graduate's age in
    year 0. One row per disagreement."""
    problems = []
    for title in sorted(ns["MAJOR_DATA"]):
        py = ns["program_years_for_major"](title)
        if py == 0:
            continue          # no college, no timeline offset to get wrong
        got = start_age_fn(py, 0, title)
        want = expected_year_zero_age(ns, title, py)
        if got != want:
            problems.append(
                f"  {title!r}: baseline starts at age {got} but the graduate is "
                f"{want} in timeline year 0 (programme {py}y)")
    return problems


def check_composition(ns, start_age_fn):
    """The stronger identity, and the one a wrong subtraction cannot survive.

    Walk to the first year the earnings curve pays the FULL starting salary.
    Whatever age the baseline is by then must be the age this person truly
    starts practising, which is independently:

        18 + program_years + residency_years

    -- the whole programme, plus any stipend years that are WORK rather than
    school. A funded doctorate's stipend years are inside program_years already
    and must not be added twice; a medical residency is not and must be.
    """
    problems = []
    for title in sorted(ns["MAJOR_DATA"]):
        py = ns["program_years_for_major"](title)
        if py == 0:
            continue
        unpaid, stipend, funded = raw_overlay(ns, title)
        residency = 0 if funded else stipend
        full = ns["MAJOR_DATA"][title]["starting_salary"]
        first_full = next(
            (y for y in range(40)
             if ns["get_annual_salary_for_year"](title, y) >= full - 0.01), None)
        if first_full is None:
            problems.append(f"  {title!r}: never reaches its starting salary")
            continue
        got = start_age_fn(py, 0, title) + first_full
        want = HS_START + py + residency
        if got != want:
            problems.append(
                f"  {title!r}: full salary lands at age {got} on the comparison "
                f"timeline, but this path starts practising at {want} "
                f"({py}y programme + {residency}y residency)")
    return problems


def check_head_start(ns):
    """With foregone earnings ON the baseline starts at 18 and is credited with
    a head start instead. That head start must END where the earnings curve
    BEGINS, or the graduate years are charged twice again -- the same bug
    wearing the other toggle.

    pre_earnings_years is what both the head start and the start age are built
    from in app.py, so the identity to check is that it equals the years of
    school this occupation does before its earnings curve starts.
    """
    problems = []
    for title in sorted(ns["MAJOR_DATA"]):
        py = ns["program_years_for_major"](title)
        if py == 0:
            continue
        got = ns["pre_earnings_years"](title, py)
        want = expected_year_zero_age(ns, title, py) - HS_START
        if got != want:
            problems.append(
                f"  {title!r}: foregone-earnings head start is {got}y but the "
                f"earnings curve starts after {want}y of school")
    return problems


def check_baseline_sum(ns):
    """End to end, on the figure a visitor actually reads: a dentist's baseline
    over the default window must be the high-school wage at ages 22-31, summed
    from an age this file states literally rather than from the app's offset."""
    title = "Dentists, General"
    if title not in ns["MAJOR_DATA"]:
        return [f"  {title!r} is missing from MAJOR_DATA"]
    py = ns["program_years_for_major"](title)
    scenario = ns["compute_scenario_results"](
        title, 100_000, 6.5, "Standard 10-Year", col_index=100.0,
        hs_wage_index=1.0, enrollment_years=0, working_years=0,
        baseline_start_age=ns["baseline_start_age_for"](py, 0, title))
    got = scenario["roi_result"]["hs_cumulative_earnings"]
    want = sum(ns["hs_wage_for_timeline_year"](y, 1.0, HS_START + UNDERGRAD)
               for y in range(WINDOW))
    if abs(got - want) > 1:
        return [f"  the dentist's baseline sums ${got:,.0f} over the window; "
                f"ages 22-31 sum to ${want:,.0f}"]
    return []


def check_axis_title(ns):
    """The net-position chart's x-axis must name the moment year 0 actually is.

    "Years after graduation" was ambiguous for exactly the paths this file
    exists for -- a dentist's year 0 is the year they START dental school -- and
    a label asserting a moment the data does not have is the same defect class
    as the timeline itself, so it is checked here rather than left to reading.

    The scenarios are hand-built dicts: net_position_axis_title reads only
    baseline_start_age and enrollment_years, so constructing them literally
    states the ages this file expects instead of asking the app to supply them.
    check_end_to_end below then proves a REAL scenario carries the same keys.
    """
    title = ns["net_position_axis_title"]
    def sc(start_age, enroll=0):
        return ("x", {"baseline_start_age": start_age, "enrollment_years": enroll})
    cases = [
        # (scenarios, must contain, description)
        ([sc(22)], "bachelor", "a path whose year 0 is age 22"),
        ([sc(18, 4)], "bachelor", "the same path with foregone earnings on"),
        ([sc(24)], "graduation", "a master's path, year 0 at 24"),
        ([sc(20)], "graduation", "an associate's path, year 0 at 20"),
        ([sc(18)], "high school", "a path needing no degree"),
        ([sc(22), sc(24)], "comparison", "two paths starting at different ages"),
        ([sc(22), sc(22)], "bachelor", "two paths starting at the same age"),
    ]
    problems = []
    for scenarios, needle, what in cases:
        got = title(scenarios)
        if needle not in got.lower():
            problems.append(f"  {what}: axis reads {got!r}, expected it to name "
                            f"{needle!r}")
    # The specific regression: a dentist must NOT get the bare old string.
    if title([sc(22)]) == "Years after graduation":
        problems.append("  a bachelor's-anchored path still reads the ambiguous "
                        "'Years after graduation'")
    return problems


def check_end_to_end(ns):
    """A real scenario dict must carry the two keys the axis title reads, and
    must produce the bachelor's wording for a dentist. The hand-built dicts
    above cannot catch a rename of either key."""
    title = "Dentists, General"
    if title not in ns["MAJOR_DATA"]:
        return [f"  {title!r} is missing from MAJOR_DATA"]
    py = ns["program_years_for_major"](title)
    scenario = ns["compute_scenario_results"](
        title, 100_000, 6.5, "Standard 10-Year", col_index=100.0,
        hs_wage_index=1.0, enrollment_years=0, working_years=0,
        baseline_start_age=ns["baseline_start_age_for"](py, 0, title))
    problems = []
    for key in ("baseline_start_age", "enrollment_years"):
        if key not in scenario:
            problems.append(f"  a real scenario has no {key!r}; the axis title "
                            "reads it")
    if problems:
        return problems
    got = ns["net_position_axis_title"]([("A", scenario)])
    if "bachelor" not in got.lower():
        problems.append(f"  a real dentist scenario gives axis title {got!r}, "
                        "which does not name the bachelor's")
    return problems


def crossover_scenario(ns, title, loan=120_000, enroll=False):
    py = ns["program_years_for_major"](title)
    enrollment = ns["pre_earnings_years"](title, py) if enroll else 0
    return ns["compute_scenario_results"](
        title, loan, 6.5, "Standard 10-Year", col_index=100.0, hs_wage_index=1.0,
        enrollment_years=enrollment, working_years=0,
        baseline_start_age=ns["baseline_start_age_for"](py, enrollment, title))


def check_crossover(ns):
    """The crossover age must be the age from which the graduate STAYS ahead.

    Two properties, checked against a brute-force recomputation of the series
    rather than against the helper's own return:

      * the reported year is ahead, and every year after it is ahead too;
      * the year before it is NOT ahead (else the answer is not the earliest).

    And the age identity, which is this file's whole subject: the age must be
    the graduate's age at timeline year 0 plus the year.
    """
    problems = []
    sample = ["Dentists, General", "Computer Science", "Lawyers", "Pharmacists",
              "Commercial Divers", "Music Directors and Composers",
              "Architectural and Civil Drafters", "Registered Nurses"]
    for title in sample:
        if title not in ns["MAJOR_DATA"]:
            continue
        for enroll in (False, True):
            sc = crossover_scenario(ns, title, enroll=enroll)
            got = ns["net_position_crossover"](sc, 100.0, 1.0)
            points = ns["build_net_position_series"](
                sc, 100.0, 1.0, ns["NET_POSITION_CROSSOVER_MAX_YEARS"])
            ahead = [p["major"] >= p["hs"] for p in points]
            tag = f"{title} (foregone {'on' if enroll else 'off'})"
            if got["year"] is None:
                if ahead and ahead[-1]:
                    problems.append(f"  {tag}: reported as never getting ahead, "
                                    "but it is ahead in the final year")
                continue
            index = got["year"] - 1
            if not all(ahead[index:]):
                problems.append(f"  {tag}: reported year {got['year']} but the "
                                "path falls behind again after it")
            if index > 0 and ahead[index - 1]:
                problems.append(f"  {tag}: reported year {got['year']} but it "
                                "was already ahead the year before")
            want_age = (sc["baseline_start_age"] + sc["enrollment_years"]
                        + got["year"])
            if got["age"] != want_age:
                problems.append(f"  {tag}: age {got['age']} does not equal the "
                                f"year-0 age plus {got['year']} ({want_age})")
    return problems


def check_crossover_agrees_with_verdict(ns):
    """The sentence must not contradict the verdict printed above it.

    "Is this debt worth it?" is a statement about the ROI window; the crossover
    is a statement about a longer horizon. They answer different questions, but
    they cannot disagree about the window itself: if the path gets ahead on or
    before the window's last year, the premium at the window must be >= 0.
    """
    problems = []
    window = WINDOW
    for title in ("Dentists, General", "Computer Science", "Lawyers",
                  "Registered Nurses", "History Teachers, Postsecondary"):
        if title not in ns["MAJOR_DATA"]:
            continue
        for enroll in (False, True):
            sc = crossover_scenario(ns, title, enroll=enroll)
            got = ns["net_position_crossover"](sc, 100.0, 1.0)
            premium = sc["roi_result"]["earnings_premium"]
            tag = f"{title} (foregone {'on' if enroll else 'off'})"
            inside = got["year"] is not None and got["year"] <= window
            if inside and premium < 0:
                problems.append(f"  {tag}: crosses in year {got['year']} but the "
                                f"{window}-year premium is ${premium:,.0f}")
            if not inside and premium >= 0:
                problems.append(f"  {tag}: {window}-year premium is "
                                f"${premium:,.0f} but it is reported as not "
                                "ahead within the window")
    return problems


def check_crossover_sentence(ns):
    """Every branch produces a sentence, and the beyond-the-window case says so
    -- that is the case where the verdict says no and this says a year, and
    without naming the gap the two read as contradicting each other."""
    problems = []
    say = ns["crossover_sentence"]
    cases = [
        ({"year": None, "age": None}, 10, "never", "no crossover"),
        ({"year": 4, "age": 26}, 10, "age 26", "inside the window"),
        ({"year": 15, "age": 37}, 10, "beyond the 10-year window", "past the window"),
        ({"year": 15, "age": None}, 10, "year 15", "no age available"),
    ]
    for crossover, window, needle, what in cases:
        got = say(crossover, window)
        if needle not in got:
            problems.append(f"  {what}: sentence is {got!r}, expected it to "
                            f"contain {needle!r}")
    if "$" in "".join(say(c, 10) for c, _, _, _ in cases):
        problems.append("  the sentence carries a dollar sign; it is appended to "
                        "details that already carry one, and two in a markdown "
                        "string render as LaTeX (see fmt_money_md)")
    return problems


def negative_controls(ns):
    """Break it deliberately. A guard that passes for the wrong reason is worse
    than none, and all three of these failed to fail in an earlier draft."""
    problems = []

    # (a) The bug itself: the pre-2026-08-14 rule, 18 + the whole programme.
    old_rule = lambda py, enroll, title: HS_START + py
    caught = check_alignment(ns, old_rule)
    if not caught:
        problems.append("  restoring `18 + program_years` did not fail the "
                        "alignment check")
    elif not any("Dentists, General" in p for p in caught):
        problems.append("  restoring `18 + program_years` failed, but not for "
                        "'Dentists, General' -- the reported case")
    if not check_composition(ns, old_rule):
        problems.append("  restoring `18 + program_years` did not fail the "
                        "composition check")

    # (b) ...and it must still PASS for paths with no overlay, or the check is
    # just failing everything and proving nothing.
    plain = [t for t in ns["MAJOR_DATA"]
             if raw_overlay(ns, t) == (0, 0, False)
             and ns["program_years_for_major"](t) > 0]
    if len(plain) < 100:
        problems.append(f"  only {len(plain)} occupations have no overlay; "
                        "expected the large majority")
    stale = [p for p in check_alignment(ns, old_rule)
             if any(f"{t!r}" in p for t in plain)]
    if stale:
        problems.append("  the old rule was reported wrong for an occupation "
                        f"with no training overlay: {stale[0].strip()}")

    # (c) Treat a funded doctorate's stipend years as work rather than school --
    # the distinction curated_school_years' docstring says it cannot make. This
    # is the branch the PhD paths added on 2026-08-14 depend on, and dropping it
    # must be caught on its own, not incidentally by (a).
    unfunded = lambda py, enroll, title: HS_START + max(
        py - ns["curated_school_years"](title), 0)
    # (d) A constant axis title -- the thing it was before -- must fail.
    real_title = ns["net_position_axis_title"]
    ns["net_position_axis_title"] = lambda scenarios: "Years after graduation"
    if not check_axis_title(ns):
        problems.append("  a constant 'Years after graduation' axis title did "
                        "not fail the axis check")
    ns["net_position_axis_title"] = real_title

    # (e) The tempting definition -- the FIRST year ahead rather than the year
    # it stays ahead from. Several paths lead in year 1 on salary alone, fall
    # behind once loan payments start, and recover later; reporting year 1 there
    # names an age the reader does not stay ahead at.
    real_crossover = ns["net_position_crossover"]
    def first_crossing(scenario, col_index, hs_wage_index, max_years=None):
        points = ns["build_net_position_series"](
            scenario, col_index, hs_wage_index,
            max_years or ns["NET_POSITION_CROSSOVER_MAX_YEARS"])
        for point in points:
            if point["major"] >= point["hs"]:
                age = (scenario["baseline_start_age"]
                       + (scenario.get("enrollment_years") or 0) + point["year"])
                return {"year": point["year"], "age": age}
        return {"year": None, "age": None}
    ns["net_position_crossover"] = first_crossing
    if not check_crossover(ns):
        problems.append("  taking the FIRST year ahead rather than the year it "
                        "stays ahead from did not fail the crossover check")
    ns["net_position_crossover"] = real_crossover

    caught = check_alignment(ns, unfunded)
    funded_titles = [t for t in ns["MAJOR_DATA"] if raw_overlay(ns, t)[2]]
    if not funded_titles:
        problems.append("  no funded doctorate found; control (c) proves nothing")
    elif not any(any(f"{t!r}" in p for t in funded_titles) for p in caught):
        problems.append("  ignoring graduate_years_funded did not fail for any "
                        "funded doctorate")
    return problems


def main():
    ns = load_app_namespace()
    sections = [
        ("timeline year 0 means the same thing on both sides",
         check_alignment(ns, ns["baseline_start_age_for"])),
        ("the earnings curve reaches full salary at the right age",
         check_composition(ns, ns["baseline_start_age_for"])),
        ("the foregone-earnings head start ends where earnings begin",
         check_head_start(ns)),
        ("the dentist's baseline sums the right ten ages",
         check_baseline_sum(ns)),
        ("the x-axis names the moment year 0 actually is",
         check_axis_title(ns)),
        ("a real scenario carries what the axis title reads",
         check_end_to_end(ns)),
        ("the crossover is the age the graduate STAYS ahead from",
         check_crossover(ns)),
        ("the crossover cannot contradict the verdict above it",
         check_crossover_agrees_with_verdict(ns)),
        ("every crossover branch produces a usable sentence",
         check_crossover_sentence(ns)),
        ("negative controls", negative_controls(ns)),
    ]
    failed = False
    for label, problems in sections:
        if problems:
            failed = True
            print(f"FAIL: {label}")
            for line in problems[:12]:
                print(line)
            if len(problems) > 12:
                print(f"  ... and {len(problems) - 12} more")
        else:
            print(f"ok: {label}")
    if failed:
        print("\ncheck_timeline_alignment.py FAILED")
        return 1
    print(f"\nall checks passed over {len(ns['MAJOR_DATA'])} occupations")
    return 0


if __name__ == "__main__":
    sys.exit(main())

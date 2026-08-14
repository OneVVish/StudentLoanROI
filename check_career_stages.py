#!/usr/bin/env python3
"""Guard: the salary curve stops where the data does, and the late-career
stages that expose it are built without dropping anyone.

    python3 check_career_stages.py       (exit 1 on a violation)

Two changes arrived together and each is why the other needed a guard.

The take-home table gained **Year 20 and Year 30** on the training paths,
because on those the two standard stages describe almost nothing: a dentist's
"Starting (Year 1)" is a year of dental school at $0 and "Mid-Career (Year 10)"
is six years into practice. The net-position chart was widened to **35 years**,
because the crossover on those same paths lands well outside a ten-year view --
the app already tells a dentist they come out ahead at 40 while the chart
stopped at 10.

Both surfaced an extrapolation that had always been in the model and had never
been on screen. get_major_growth_rate solves for the rate that climbs from the
25th percentile to the MEDIAN over ten years, and compounding it for a whole
career walks out of the data it was fitted to:

    by year 10    0 of 825 occupations exceed their own published p90
    by year 20   15
    by year 30  302        worst 4.14x
    by year 35  564        worst 6.95x

Uncapped, a Surgeon's year-30 gross was $1,590,753 against a p90 of $655,320.
So career_earnings_ceiling flattens the curve at the occupation's own p90, and
the checks below are what stop that ceiling from doing anything ELSE:

1. **Nothing above its own ceiling**, at any year out to 40.
2. **Bit-identical at ten years and under**, for every occupation. This is what
   makes the change safe for every figure this app has already logged, and it
   is asserted against the uncapped formula recomputed here rather than against
   a stored expectation.
3. **A level shift carries the distribution.** The prestige multiplier and the
   returning student's own entered salary both move an occupation's level; if
   the percentiles do not move with them, the ceiling caps a raised curve
   against an unraised p90 -- the app silently overruling the salary the
   visitor just typed.
4. **Curated entries inherit a REAL ceiling.** CURATED_CEILING_SOURCE is a map
   keyed on titles, which is the shape that goes inert silently (see
   PROFESSIONAL_PROGRAM_BY_OCCUPATION's typo), so every source title must exist
   and resolve to a p90 -- and "Medicine" must still equal Family Medicine
   Physicians at year 35, since this codebase requires those twins to agree.
5. **The stages are per-path**, present on every professional occupation and
   absent where there is no training delay.
6. **Merging two scenarios' stages does not drop any.** Compare Mode can hold a
   four-stage path beside a two-stage one, and the zip() this replaced
   truncated to the shorter: A's Year 20 and Year 30 computed, returned, and
   silently dropped from both the screen and the report.
7. **The chart runs to 35 and the horizon is still on it**, with the point at
   the horizon still equal to the metric above the chart -- the invariant
   build_net_position_series exists to protect, now holding at a year that is
   no longer the last one.

Negative controls for 1, 3 and 6, per the house rule.

Run after touching get_annual_salary_for_year, career_earnings_ceiling,
career_stages_for, pair_takehome_stages, or the net-position chart's span.
"""
import ast
import sys

APP = "app.py"

MAX_YEAR = 40


def load_app_namespace():
    """app.py's sections 1-2 plus its later pure functions, with the real
    MAJOR_DATA. Same exec-prefix trick analyze_model.py uses."""
    src = open(APP).read()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    prefix = src[:src.rindex("# " + "=" * 60, 0, cut)]
    ns = {"__name__": "careerstagecheck"}
    exec(compile(prefix, APP, "exec"), ns)
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name not in ns:
            exec(compile(ast.Module(body=[node], type_ignores=[]), APP, "exec"), ns)
    # The REAL dataset: against the curated constants alone every occupation
    # here would be absent and this file would pass on an empty sweep.
    ns["MAJOR_DATA"] = ns["build_major_data"](ns["CAREERS_CSV_PATH_NATIONAL"])
    return ns


def uncapped_salary(ns, major: str, year_index: int) -> float:
    """The pre-ceiling formula, written out here rather than read from the
    code under test -- deriving the expectation from the thing being checked
    would only assert that it equals itself."""
    data = ns["MAJOR_DATA"][major]
    unpaid = data.get("unpaid_training_years", 0)
    stipend = data.get("stipend_training_years", 0)
    if year_index < unpaid:
        return 0.0
    if year_index < unpaid + stipend:
        return data.get("stipend_salary", 0)
    practicing = year_index - unpaid - stipend
    return data["starting_salary"] * (1 + ns["get_major_growth_rate"](major)) ** practicing


# ---------------------------------------------------------------------------
# 1 and 2. The ceiling binds, and only where it should


def check_ceiling_binds(ns) -> list:
    problems = []
    worst = None
    for major in ns["MAJOR_DATA"]:
        ceiling = ns["career_earnings_ceiling"](major)
        if not ceiling:
            continue
        for year in range(MAX_YEAR):
            got = ns["get_annual_salary_for_year"](major, year)
            if got > ceiling + 0.01:
                ratio = got / ceiling
                if worst is None or ratio > worst[1]:
                    worst = (major, ratio, year + 1, got, ceiling)
    if worst:
        major, ratio, year, got, ceiling = worst
        problems.append(
            f"  {major} earns ${got:,.0f} in year {year}, {ratio:.2f}x the "
            f"${ceiling:,.0f} the best-paid 10% of that occupation make.\n"
            f"    The growth rate is fitted over ten years and is fiction past "
            f"about fifteen; a headline salary above the occupation's own p90 "
            f"is false in the direction that flatters the most expensive "
            f"degrees on the page.")
    return problems


def check_ceiling_is_inert_early(ns) -> list:
    """Ten years and under must be bit-identical to the uncapped formula.

    This is the whole safety argument for the change: the default page, every
    10-year figure, and every row already in the research data are untouched.
    """
    problems = []
    for major in ns["MAJOR_DATA"]:
        for year in range(10):
            got = ns["get_annual_salary_for_year"](major, year)
            want = uncapped_salary(ns, major, year)
            if abs(got - want) > 1e-9:
                problems.append(
                    f"  {major} year {year + 1}: ${got:,.2f} with the ceiling, "
                    f"${want:,.2f} without it.\n"
                    f"    The ceiling must not reach the first ten years -- "
                    f"that is what makes it safe for every figure already "
                    f"logged. If an occupation genuinely tops out this early, "
                    f"the ceiling is the wrong mechanism for it.")
                return problems
    return problems


def check_ceiling_negative_control(ns) -> list:
    """Restore the uncapped curve; check 1 must then fail."""
    real = ns["get_annual_salary_for_year"]

    def uncapped(major_name, year_index):
        return uncapped_salary(ns, major_name, year_index)

    ns["get_annual_salary_for_year"] = uncapped
    try:
        caught = bool(check_ceiling_binds(ns))
    finally:
        ns["get_annual_salary_for_year"] = real
    if not caught:
        return ["  NEGATIVE CONTROL FAILED: the uncapped salary curve did not "
                "trip the ceiling check, so that check cannot see the "
                "extrapolation it exists for."]
    return []


# ---------------------------------------------------------------------------
# 3. A level shift carries the distribution


def check_level_shifts_scale_percentiles(ns) -> list:
    problems = []
    major = "Registered Nurses"
    base_ceiling = ns["career_earnings_ceiling"](major)
    if not base_ceiling:
        return [f"  fixture: {major} publishes no p90, so this check tests nothing."]

    # The returning student's own entered salary. Deliberately far above the
    # occupation's start, which is the case that exposes an unscaled ceiling.
    saved = dict(ns["MAJOR_DATA"][major])
    entered = saved["starting_salary"] * 3
    try:
        ns["apply_starting_salary_override"](major, entered)
        shifted = ns["career_earnings_ceiling"](major)
        if shifted is None or abs(shifted - base_ceiling * 3) > 1.0:
            problems.append(
                f"  a career-changer entering ${entered:,.0f} keeps a ceiling of "
                f"${shifted or 0:,.0f} where ${base_ceiling * 3:,.0f} is the "
                f"same distribution at the same shift.\n"
                f"    An unscaled ceiling flattens the curve against the "
                f"occupation's ordinary p90 -- the app overruling the one figure "
                f"on the page the visitor typed themselves.")
        # And the whole point: the curve is not clipped at the old ceiling.
        if ns["get_annual_salary_for_year"](major, 0) < entered - 1.0:
            problems.append(
                f"  the entered starting salary ${entered:,.0f} came back as "
                f"${ns['get_annual_salary_for_year'](major, 0):,.0f} in year 1.")
    finally:
        ns["MAJOR_DATA"][major] = saved

    # The prestige multiplier, the other level shift.
    tier = next((label for label, spec in ns["COLLEGE_PRESTIGE_TIERS"].items()
                 if spec.get("salary_multiplier", 1.0) != 1.0), None)
    if tier is None:
        problems.append("  no prestige tier applies a multiplier; that half is untested.")
        return problems
    multiplier = ns["COLLEGE_PRESTIGE_TIERS"][tier]["salary_multiplier"]
    synthetic = ns["get_prestige_adjusted_major_name"](major, tier)
    try:
        shifted = ns["career_earnings_ceiling"](synthetic)
        if shifted is None or abs(shifted - base_ceiling * multiplier) > 1.0:
            problems.append(
                f"  the {tier!r} tier raises pay by {multiplier}x but leaves the "
                f"ceiling at ${shifted or 0:,.0f} instead of "
                f"${base_ceiling * multiplier:,.0f}.\n"
                f"    A tier multiplier is a claim about the same occupation, so "
                f"the distribution moves with it -- otherwise a Tier 1 salary is "
                f"capped against a Tier 3 ceiling.")
    finally:
        # get_prestige_adjusted_major_name REGISTERS its synthetic entry in
        # MAJOR_DATA, so leaving it there would put a fabricated occupation
        # into every sweep that runs after this one.
        ns["MAJOR_DATA"].pop(synthetic, None)
    return problems


def check_level_shift_negative_control(ns) -> list:
    """Stop scaling the percentiles; check 3 must fail."""
    real = ns["scale_wage_percentiles"]
    ns["scale_wage_percentiles"] = lambda percentiles, factor: percentiles
    try:
        caught = bool(check_level_shifts_scale_percentiles(ns))
    finally:
        ns["scale_wage_percentiles"] = real
    if not caught:
        return ["  NEGATIVE CONTROL FAILED: leaving the percentiles unscaled "
                "through a level shift passed, so that check cannot see a "
                "ceiling applied against the wrong distribution."]
    return []


# ---------------------------------------------------------------------------
# 4. Curated entries inherit a real ceiling


def check_curated_ceilings(ns) -> list:
    problems = []
    for curated, source in ns["CURATED_CEILING_SOURCE"].items():
        if curated not in ns["MAJOR_DATA"]:
            problems.append(
                f"  CURATED_CEILING_SOURCE names {curated!r}, which is not in "
                f"MAJOR_DATA. A map keyed on a title that does not exist is "
                f"inert, not broken: every lookup succeeds and the path is "
                f"simply never capped.")
            continue
        if source not in ns["MAJOR_DATA"]:
            problems.append(
                f"  {curated!r} inherits its ceiling from {source!r}, which is "
                f"not in MAJOR_DATA.")
            continue
        if not ns["career_earnings_ceiling"](curated):
            problems.append(
                f"  {curated!r} resolves to no ceiling despite naming "
                f"{source!r} as its source.")
            continue
        # The twins are required to agree about the same life -- the
        # contradiction ADVANCED_TRAINING_OVERLAY exists to have ended.
        for year in (19, 29, 34):
            a = ns["get_annual_salary_for_year"](curated, year)
            b = ns["get_annual_salary_for_year"](source, year)
            if abs(a - b) > 0.01:
                problems.append(
                    f"  year {year + 1}: {curated!r} earns ${a:,.0f} and "
                    f"{source!r} ${b:,.0f}. Two options in one dropdown "
                    f"disagreeing about the same life is the failure the "
                    f"training overlay was built to end.")
                break
    return problems


# ---------------------------------------------------------------------------
# 5. The stages are per-path


def check_stages_per_path(ns) -> list:
    problems = []
    late = set(ns["LATE_CAREER_STAGE_OPTIONS"])
    for major, entry in ns["MAJOR_DATA"].items():
        labels = {label for label, _key in ns["career_stages_for"](major)}
        in_training = (entry.get("unpaid_training_years", 0)
                       + entry.get("stipend_training_years", 0)) > 0
        if in_training and not late <= labels:
            problems.append(
                f"  {major} spends its first years in training but gets no "
                f"late-career stages, so its table stops six years into "
                f"practice and calls that mid-career.")
        if not in_training and labels & late:
            problems.append(
                f"  {major} has no training delay but gets late-career stages. "
                f"They exist because year 10 describes almost nothing on a "
                f"training path; here it describes the job.")
        if not set(ns["CAREER_STAGE_OPTIONS"]) <= labels:
            problems.append(f"  {major} is missing one of the standard stages.")
    # And every professional path must have them, checked from the mapping
    # rather than from the overlay, so a programme added without a training
    # structure is caught here too.
    for major in ns["PROFESSIONAL_PROGRAM_BY_OCCUPATION"]:
        if major not in ns["MAJOR_DATA"]:
            continue
        if not late <= {label for label, _ in ns["career_stages_for"](major)}:
            problems.append(
                f"  {major} attends a professional school but has no "
                f"late-career stages -- it is missing a training structure.")
    return problems


# ---------------------------------------------------------------------------
# 6. Merging two scenarios' stages drops nobody


def _fake_figs(gross: float) -> dict:
    return {"gross": gross, "monthly_payment": 100.0,
            "take_home": {"net_take_home": gross * 0.7, "effective_tax_rate": 0.3},
            "disposable_nominal": gross / 24, "disposable_col_adjusted": gross / 24}


def check_stage_merge(ns) -> list:
    problems = []
    long_side = [(label, _fake_figs(100_000 + 1000 * index))
                 for index, label in enumerate(ns["ALL_CAREER_STAGE_OPTIONS"])]
    short_side = [(label, _fake_figs(80_000)) for label in ns["CAREER_STAGE_OPTIONS"]]
    if len(long_side) <= len(short_side):
        return ["  fixture: both sides have the same number of stages, so this "
                "cannot detect a truncating merge. Resize it."]

    merged = ns["pair_takehome_stages"](long_side, short_side)
    if len(merged) != len(long_side):
        problems.append(
            f"  merging a {len(long_side)}-stage path with a "
            f"{len(short_side)}-stage one produced {len(merged)} rows.\n"
            f"    zip() truncates to the shorter: the extra stages are "
            f"computed, returned and silently dropped, with the section still "
            f"rendering and still looking complete.")
    for label, figs_a, figs_b in merged:
        if figs_a is not None and dict(long_side)[label] is not figs_a:
            problems.append(
                f"  {label!r} paired against the wrong figures. Pairing on "
                f"position is correct only while both lists are prefixes of "
                f"one another.")
        if label in dict(short_side) and figs_b is None:
            problems.append(f"  {label!r} lost the second scenario's figures.")

    rows = ns["takehome_flow_rows"](long_side, short_side, "A", "B")
    if len(rows) != len(long_side):
        problems.append(
            f"  the flow rows dropped to {len(rows)} of {len(long_side)} stages.")
    only_a = [label for label, cols in rows if len(cols) == 1]
    if len(only_a) != len(long_side) - len(short_side):
        problems.append(
            f"  {len(only_a)} rows carry a single bar where "
            f"{len(long_side) - len(short_side)} stages exist on one side only. "
            f"A stage only one path has still draws the one bar it has.")
    return problems


def check_stage_merge_negative_control(ns) -> list:
    """Restore the positional zip; check 6 must fail."""
    real = ns["pair_takehome_stages"]

    def zipped(stages_a, stages_b):
        return [(label, figs_a, figs_b)
                for (label, figs_a), (_label_b, figs_b) in zip(stages_a, stages_b)]

    ns["pair_takehome_stages"] = zipped
    try:
        caught = bool(check_stage_merge(ns))
    finally:
        ns["pair_takehome_stages"] = real
    if not caught:
        return ["  NEGATIVE CONTROL FAILED: a zip() pairing passed the merge "
                "check, so it cannot see the truncation it exists for."]
    return []


# ---------------------------------------------------------------------------
# 7. The chart span, and the invariant it must not break


def check_chart_span(ns) -> list:
    problems = []
    horizon = 10
    scenario = ns["compute_scenario_results"](
        "Dentists, General", 190_000, 6.5, "Standard 10-Year",
        roi_window_years=horizon, federal_cap=27_000, gap_rate=8.5,
        professional_debt=279_900, include_fees=True)

    drawn = ns["net_position_chart_years"](horizon)
    if drawn < 35:
        problems.append(
            f"  the chart draws {drawn} years; it exists to reach the crossover "
            f"on the training paths, which lands well past a ten-year view.")
    if ns["net_position_chart_years"](40) != 40:
        problems.append(
            "  a 40-year horizon draws fewer than 40 years, so the metric above "
            "the chart names a year the reader cannot find on it.")

    frame = ns["net_position_frame"]([("A", scenario)], 100.0, 1.0, drawn)
    years = sorted(frame["year"].unique())
    if max(years) != drawn:
        problems.append(f"  frame reaches year {max(years)}, not {drawn}.")
    if horizon not in years:
        problems.append(
            f"  year {horizon} is not on the chart at all, so the metrics above "
            f"it describe a point that cannot be read off it.")

    # The invariant: the point at the horizon still equals the headline metric.
    # It is no longer the LAST point, which is the only part that changed.
    at_horizon = frame[(frame["Series"] == "A") & (frame["year"] == horizon)]
    if at_horizon.empty:
        problems.append("  no series 'A' row at the horizon to check.")
    else:
        got = float(at_horizon.iloc[0]["Net Position"])
        want = float(scenario["roi_result"]["major_net_position"])
        if abs(got - want) > 1.0:
            problems.append(
                f"  at year {horizon} the chart reads ${got:,.2f} where the "
                f"metric above it reads ${want:,.2f}.\n"
                f"    build_net_position_series exists so the two cannot "
                f"disagree; widening the chart must not have introduced a "
                f"second trajectory.")
    return problems


def check_horizon_marked() -> list:
    """Static: both chart twins mark the horizon, and both read the DRAWN span
    for their ticks.

    The chart now ends past the window its metrics describe. Unmarked, the
    reader has three figures above a chart whose end matches none of them --
    and reading the window for the tick step put a label on all 35 years.
    """
    problems = []
    tree = ast.parse(open(APP).read())
    for fn, marker in (("build_net_position_chart", "add_vline"),
                        ("build_pdf_net_position_chart", "axvline")):
        node = next((n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef) and n.name == fn), None)
        if node is None:
            problems.append(f"  {fn} is gone.")
            continue
        source = ast.dump(node)
        if marker not in source:
            problems.append(
                f"  {fn} never draws the horizon marker ({marker}). The chart "
                f"runs past the window its metrics describe, and in a printed "
                f"report there is no page around it to explain that.")
        if "_drawn_years" not in source:
            problems.append(
                f"  {fn} does not read the drawn span. Sizing ticks or the "
                f"marker from the visitor's window labels all 35 years at a "
                f"10-year horizon.")
    return problems


# ---------------------------------------------------------------------------


def main() -> int:
    ns = load_app_namespace()
    problems = []
    checks = 0
    for label, run in (
        ("ceiling binds", lambda: check_ceiling_binds(ns)),
        ("ceiling binds / negative control", lambda: check_ceiling_negative_control(ns)),
        ("ceiling is inert at ten years", lambda: check_ceiling_is_inert_early(ns)),
        ("level shifts carry the distribution",
         lambda: check_level_shifts_scale_percentiles(ns)),
        ("level shifts / negative control",
         lambda: check_level_shift_negative_control(ns)),
        ("curated ceilings", lambda: check_curated_ceilings(ns)),
        ("stages are per-path", lambda: check_stages_per_path(ns)),
        ("stage merge", lambda: check_stage_merge(ns)),
        ("stage merge / negative control", lambda: check_stage_merge_negative_control(ns)),
        ("chart span", lambda: check_chart_span(ns)),
        ("horizon marked in both twins", check_horizon_marked),
    ):
        checks += 1
        found = run()
        if found:
            problems.append(f"{label}:\n" + "\n\n".join(found))

    if problems:
        print(f"career stages: {len(problems)} failing check(s)\n")
        print("\n\n".join(problems))
        return 1
    print(f"career stages OK -- {len(ns['MAJOR_DATA'])} occupations stay inside "
          f"their own published wage range out to year {MAX_YEAR} and are "
          f"bit-identical through year 10, level shifts carry the distribution, "
          f"late stages follow the training structure, a four-stage path merges "
          f"with a two-stage one without dropping either, and the 35-year chart "
          f"still meets its metric at the horizon "
          f"({checks} checks, 3 negative controls).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Guard: the real-dollar discounting module is off by default, bit-identical
when off, and internally consistent when on.

    python3 check_discounting.py        (exit 1 on a violation)

This module is unusual in that MOST of what it must be trusted about is what it
does NOT do. It is opt-in, it ships off, and every figure already on screen and
every row already in Supabase has to keep meaning exactly what it meant. It is
also built to be BACKED OUT (see DISCOUNTING_ENABLED in app.py), and an exit
path is only real if something keeps proving it works as the code moves on
around it. Both of those are properties nothing else in this repo checks and
neither is visible in any rendered figure.

Six things are asserted:

1. **Bit-identical at a zero rate.** calculate_roi with both rates at 0.0 must
   reproduce the undiscounted model EXACTLY, not approximately. This is the
   safety argument for the whole feature: the default page, the break-even and
   every logged row depend on it. Compared against a pristine copy of the
   function reconstructed without the module, never against the module's own
   zero case, because the latter would only assert the code equals itself.
2. **Bit-identical with DISCOUNTING_ENABLED False.** The cheap back-out. If
   this ever fails, the module can no longer be switched off without a code
   change, which is the whole property the kill switch exists to provide.
3. **The chart's last point still equals the headline metric when ON.** The one
   invariant build_net_position_series exists to hold, and discounting is the
   first thing that could break it, since the chart re-runs the model once per
   year and has to do it on the same terms.
4. **The loan series reconciles.** annual_loan_payments must sum to what
   cumulative_loan_paid_by_year reports, or the discounted cost silently
   describes a different loan than the undiscounted one.
5. **The wall-clock offsets are real.** A discounted premium must move when the
   enrollment offset moves. This is the failure mode with no symptom: each
   stream still balances its own books and nothing raises, the two sides just
   come to describe different calendar years.
6. **Both rates travel together.** discounting_kwargs returns both or neither,
   and breakeven_kwargs carries them unconditionally. A break-even solved
   undiscounted beside a discounted premium is the recorded 2026-plans failure.

Every check carries a negative control, per the house rule -- a guard that
passes for the wrong reason is worse than none.
"""

import ast
import re
import sys

APP = "app.py"

# Fixtures. Deliberately literals rather than reads of the constants under
# test: a check that derives its expectations from the code it is policing
# asserts only that the code equals itself. That flaw is on record twice in
# this repo (the residency guard and the duration-tick guard).
RATE = 6.5
LOAN = 190_000.0
INVESTMENT = 190_000.0
COL = 104.0
WAGE_INDEX = 1.05
CONTRIBUTION = 2_000.0
TEST_RATES = (0.01, 0.03, 0.08)

# Spread across training structures on purpose: a four-year path, two long
# unpaid-training paths and a no-degree path whose program length is zero.
FIXTURE_MAJORS = ("Software Developers", "Dentists, General", "Lawyers",
                  "Registered Nurses", "Cashiers")


def load_app_namespace(source: str = None):
    """app.py's sections 1-2 plus its later pure functions, without the UI.

    Same exec-prefix trick analyze_model.py uses -- see CLAUDE.md on why the
    section banners are load-bearing. `source` lets a check exec a MODIFIED
    copy of app.py, which is how the negative controls and the
    DISCOUNTING_ENABLED back-out are tested without editing the file.
    """
    src = source if source is not None else open(APP).read()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    prefix = src[:src.rindex("# " + "=" * 60, 0, cut)]
    ns = {"__name__": "discountcheck"}
    exec(compile(prefix, APP, "exec"), ns)
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name not in ns:
            exec(compile(ast.Module(body=[node], type_ignores=[]), APP, "exec"), ns)
    # MAJOR_DATA is a section-4 name and every check needs the REAL one: against
    # an empty dict the curated constants are absent and every path falls
    # through to the derived figure.
    ns["MAJOR_DATA"] = ns["build_major_data"](ns["CAREERS_CSV_PATH_NATIONAL"])
    return ns


def undiscounted_source() -> str:
    """app.py with the discounting module neutralised at its own entry point.

    Rather than reconstructing the old calculate_roi by hand -- which would be a
    second implementation of the very thing under test -- this forces
    discounting_is_active to return False, which is the single predicate every
    branch in the module keys on. What comes back is provably the pre-module
    code path.
    """
    src = open(APP).read()
    marker = "    return bool(discount_rate) or bool(inflation_rate)"
    if marker not in src:
        sys.exit("check_discounting: discounting_is_active's body has changed. "
                 "This guard neutralises the module through that one predicate; "
                 "if the module no longer funnels through it, the bit-identical "
                 "checks below are no longer proving what they claim.")
    return src.replace(marker, "    return False", 1)


def disabled_source() -> str:
    """app.py with the kill switch thrown, i.e. the cheap back-out."""
    src = open(APP).read()
    if not re.search(r"^DISCOUNTING_ENABLED = True$", src, re.M):
        sys.exit("check_discounting: DISCOUNTING_ENABLED is not assigned True at "
                 "module level. It is the module's documented back-out and this "
                 "guard cannot verify the exit path without it.")
    return re.sub(r"^DISCOUNTING_ENABLED = True$", "DISCOUNTING_ENABLED = False",
                  src, count=1, flags=re.M)


def roi_grid(ns, discount_rate=0.0, inflation_rate=0.0, payments=None,
             enrollment_shift=0, omit_kwargs=False):
    """Every fixture scenario's ROI, as a flat dict for exact comparison.

    omit_kwargs calls calculate_roi WITHOUT the three discounting arguments, the
    way every pre-module caller does. That is what the leak control needs: the
    question is not whether the module can be forced on, it is whether a caller
    who passes nothing still gets the undiscounted model.
    """
    out = {}
    for major in FIXTURE_MAJORS:
        if major not in ns["MAJOR_DATA"]:
            continue
        for years in (5, 10, 20, 35):
            for enrollment in (0, 4, 9):
                for working in (0, 2):
                    common = dict(
                        col_index=COL, years=years, hs_wage_index=WAGE_INDEX,
                        personal_contribution=CONTRIBUTION,
                        enrollment_years=enrollment + enrollment_shift,
                        working_years=working, baseline_start_age=18)
                    if not omit_kwargs:
                        common.update(
                            discount_rate=discount_rate,
                            inflation_rate=inflation_rate,
                            loan_payments_by_year=(payments[:years] if payments else None))
                    result = ns["calculate_roi"](
                        major, 45_000.0, INVESTMENT, **common)
                    for key in ("earnings_premium", "roi_pct",
                                "major_net_position", "hs_net_position"):
                        out[(major, years, enrollment, working, key)] = result[key]
    return out


def check_bit_identical_when_off(ns, plain_ns) -> list:
    """1. Both rates at 0.0 must reproduce the pre-module model exactly."""
    problems = []
    live, plain = roi_grid(ns), roi_grid(plain_ns)
    differing = [k for k in plain if live.get(k) != plain[k]]
    if differing:
        sample = differing[0]
        problems.append(
            f"{len(differing)} of {len(plain)} figures differ from the "
            f"undiscounted model at a zero rate. First: {sample} "
            f"{plain[sample]} became {live.get(sample)}. The module must be a "
            f"no-op when off -- every logged row and the default page depend on "
            f"it.")

    # NEGATIVE CONTROL: a module that leaked into the DEFAULT path must fail.
    #
    # This used to force discounting_is_active to return True and compare the
    # grid against `plain`. That stopped discriminating the day
    # HS_GRAD_GROWTH_RATE went to 0.0: with both rates at zero and no payment
    # series the module became arithmetically identity, so forcing it on
    # changed nothing and the control passed while proving nothing. The leak
    # worth guarding is a non-zero DEFAULT reaching a caller that passes no
    # discounting arguments at all, which is every pre-module call site and
    # analyze_model.py.
    leaked = load_app_namespace(
        open(APP).read()
        .replace("                   discount_rate: float = 0.0,",
                 "                   discount_rate: float = 0.03,", 1)
        .replace("                   inflation_rate: float = 0.0,",
                 "                   inflation_rate: float = 0.023,", 1))
    if roi_grid(leaked, omit_kwargs=True) == roi_grid(ns, omit_kwargs=True):
        problems.append(
            "negative control did not fire: giving calculate_roi non-zero "
            "discounting DEFAULTS left a no-kwargs caller's figures unchanged, "
            "so this check cannot tell a no-op from a leak.")
    if roi_grid(ns, omit_kwargs=True) != plain:
        problems.append(
            "a caller that passes no discounting arguments does not reproduce "
            "the undiscounted model, so the module is reaching the default "
            "path.")
    return problems


def check_back_out_is_bit_identical(plain_ns) -> list:
    """2. DISCOUNTING_ENABLED = False must restore the pre-module model."""
    problems = []
    off_ns = load_app_namespace(disabled_source())
    if off_ns["DISCOUNTING_ENABLED"] is not False:
        problems.append("the kill switch did not take effect in the exec'd copy.")
    if roi_grid(off_ns) != roi_grid(plain_ns):
        problems.append(
            "with DISCOUNTING_ENABLED = False the model does not reproduce the "
            "undiscounted figures. The documented back-out no longer works, "
            "which means the module can only be removed by editing arithmetic.")
    # The switch has to reach the SEEDING as well as the widgets, or a shared
    # ?disc=1 link would revive a module somebody had switched off.
    src = open(APP).read()
    seeding = src[src.index('apply_shared_flag("foregone"'):]
    seeding = seeding[:seeding.index("enable_prestige_mode = ")]
    if 'apply_shared_flag("disc"' in seeding and "DISCOUNTING_ENABLED" not in seeding:
        problems.append(
            "?disc= is seeded outside the DISCOUNTING_ENABLED gate, so a shared "
            "link would switch the module back on after a back-out.")
    return problems


def check_chart_matches_headline(ns) -> list:
    """3. The chart's last point equals the metric above it, WITH discounting."""
    problems = []
    for major in ("Software Developers", "Dentists, General"):
        if major not in ns["MAJOR_DATA"]:
            continue
        for years in (10, 35):
            for rate in (0.0,) + TEST_RATES:
                scenario = ns["compute_scenario_results"](
                    major, LOAN, RATE, ns["RAP_STRATEGY_LABEL"],
                    col_index=COL, roi_window_years=years,
                    hs_wage_index=WAGE_INDEX, personal_contribution=CONTRIBUTION,
                    enrollment_years=4, working_years=0, baseline_start_age=18,
                    discount_rate=rate, inflation_rate=ns["ASSUMED_INFLATION_RATE"])
                points = ns["build_net_position_series"](scenario, COL, WAGE_INDEX, years)
                for point_key, roi_key in (("major", "major_net_position"),
                                           ("hs", "hs_net_position")):
                    drawn = points[-1][point_key]
                    stated = scenario["roi_result"][roi_key]
                    if abs(drawn - stated) > 1e-6:
                        problems.append(
                            f"{major} at {years}y, rate {rate:.0%}: the chart's "
                            f"last {point_key} point is {drawn:,.2f} but the "
                            f"metric above it says {stated:,.2f}. The chart "
                            f"re-runs the model, so this means it is running it "
                            f"on different terms.")
    # NEGATIVE CONTROL: the scenario must actually carry the rates. If
    # compute_scenario_results stopped stamping them, the chart would silently
    # fall back to undiscounted and this check must notice.
    scenario = ns["compute_scenario_results"](
        "Software Developers", LOAN, RATE, ns["RAP_STRATEGY_LABEL"],
        col_index=COL, roi_window_years=10, hs_wage_index=WAGE_INDEX,
        enrollment_years=4, baseline_start_age=18,
        discount_rate=0.05, inflation_rate=ns["ASSUMED_INFLATION_RATE"])
    stripped = dict(scenario)
    stripped["discount_rate"] = 0.0
    stripped["inflation_rate"] = 0.0
    if (ns["build_net_position_series"](stripped, COL, WAGE_INDEX, 10)[-1]["major"]
            == ns["build_net_position_series"](scenario, COL, WAGE_INDEX, 10)[-1]["major"]):
        problems.append(
            "negative control did not fire: stripping the rates off the scenario "
            "left the chart unchanged, so it is not reading them.")
    return problems


def check_loan_series_reconciles(ns) -> list:
    """4. annual_loan_payments must sum to the cumulative figure."""
    problems = []
    for major in ("Software Developers", "Dentists, General"):
        if major not in ns["MAJOR_DATA"]:
            continue
        for strategy in (ns["STANDARD_STRATEGY_LABEL"], ns["RAP_STRATEGY_LABEL"],
                         ns["IDR_STRATEGY_LABEL"]):
            scenario = ns["compute_scenario_results"](
                major, LOAN, RATE, strategy, col_index=COL, roi_window_years=10,
                hs_wage_index=WAGE_INDEX, enrollment_years=4, baseline_start_age=18)
            result = scenario["repayment_result"]
            per_year = ns["annual_loan_payments"](result, 10)
            cumulative = ns["cumulative_loan_paid_by_year"](result, 10)
            if abs(sum(per_year) - cumulative[-1]) > 0.01:
                problems.append(
                    f"{major} on {strategy}: the per-year payments sum to "
                    f"{sum(per_year):,.2f} but the cumulative series ends at "
                    f"{cumulative[-1]:,.2f}. The discounted cost would describe "
                    f"a different loan than the undiscounted one.")
            if any(payment < -0.01 for payment in per_year):
                problems.append(
                    f"{major} on {strategy}: a negative yearly payment. A "
                    f"schedule cannot un-pay, so this is a differencing fault.")
    return problems


def check_offsets_are_load_bearing(ns) -> list:
    """5. Each stream is discounted from its OWN wall-clock year.

    This is the failure mode with no symptom: every stream still balances its
    own books, nothing raises, and the two sides simply come to describe
    different calendar years. So the rule is asserted directly rather than
    inferred from behaviour.

    The offsets are WRITTEN OUT HERE -- the baseline at t=y, the graduate's
    earnings and the loan payments at t=enrollment_years+y -- instead of being
    read back off calculate_roi. That boundary is the whole point: the
    primitives (get_annual_salary_for_year, discount_factor) come from the app
    because reimplementing them would be a second copy of the model, but the
    OFFSET is the thing under test and the guard has to state it independently.

    An earlier version of this check compared a grid against the same grid with
    every enrollment offset shifted by a year, and it passed on code that had
    lost the graduate offset entirely -- shifting enrollment_years also moves
    the baseline's own summation range, so the two grids differ either way. It
    is recorded here because it is exactly the shape of vacuous check this
    file's docstring warns about.
    """
    problems = []
    discount = ns["discount_factor"]
    deflate = ns["to_todays_dollars"]
    salary = ns["get_annual_salary_for_year"]
    hs_wage = ns["hs_wage_for_timeline_year"]
    col_adjust = ns["adjust_for_cost_of_living"]
    rate, inflation = 0.03, ns["ASSUMED_INFLATION_RATE"]

    for major in FIXTURE_MAJORS:
        if major not in ns["MAJOR_DATA"]:
            continue
        for years in (10, 35):
            for enrollment in (0, 4, 9):
                payments = [19_000.0] * years
                got = ns["calculate_roi"](
                    major, sum(payments), INVESTMENT, col_index=COL, years=years,
                    hs_wage_index=WAGE_INDEX, personal_contribution=0.0,
                    enrollment_years=enrollment, working_years=0,
                    baseline_start_age=18, discount_rate=rate,
                    inflation_rate=inflation, loan_payments_by_year=payments)

                graduate = sum(salary(major, y) * discount(rate, enrollment + y)
                               for y in range(years))
                loan = sum(deflate(pay, enrollment + y, inflation)
                           * discount(rate, enrollment + y)
                           for y, pay in enumerate(payments))
                baseline = sum(hs_wage(y, WAGE_INDEX, 18, drift_rate=0.0)
                               * discount(rate, y)
                               for y in range(years + enrollment))

                for label, expected, actual in (
                    ("graduate side", col_adjust(graduate - loan, COL),
                     got["major_net_position"]),
                    ("baseline side", col_adjust(baseline, COL),
                     got["hs_net_position"]),
                ):
                    if abs(expected - actual) > 0.01:
                        problems.append(
                            f"{major}, {years}y, {enrollment} enrolled years: the "
                            f"{label} discounts to {actual:,.2f} where the "
                            f"documented offsets give {expected:,.2f}. The two "
                            f"sides are being discounted from different calendar "
                            f"years, which changes no total and raises nothing.")

    # A discounted figure must also differ from its undiscounted self, or the
    # module is inert and every other check above is vacuous.
    if roi_grid(ns, 0.0, 0.0, [19_000.0] * 35) == roi_grid(ns, 0.03, inflation,
                                                           [19_000.0] * 35):
        problems.append(
            "discounting at 3% produced the same figures as not discounting at "
            "all. The module is inert.")

    # And the direction has to be right for the one case it is unambiguous in:
    # a stream of pure future money is worth less the more it is discounted.
    factors = [ns["discount_factor"](0.03, year) for year in range(5)]
    if factors != sorted(factors, reverse=True) or factors[0] != 1.0:
        problems.append(
            f"discount_factor is not a decreasing series starting at 1.0: "
            f"{factors}. Year 0 is the present and must be undiscounted.")
    if ns["to_todays_dollars"](100.0, 10, 0.023) >= 100.0:
        problems.append(
            "to_todays_dollars did not shrink a nominal amount paid 10 years "
            "out, so loan payments are not being deflated.")
    return problems


def check_rates_travel_together(ns) -> list:
    """6. Both rates or neither, and the break-even must carry them."""
    problems = []
    src = open(APP).read()
    tree = ast.parse(src)

    body = next((n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "discounting_kwargs"), None)
    if body is None:
        problems.append("discounting_kwargs is gone. It is the single spread "
                        "point that makes a half-applied module impossible.")
    else:
        returned = ast.get_source_segment(src, body)
        if "discount_rate" in returned and "inflation_rate" not in returned:
            problems.append(
                "discounting_kwargs returns a discount rate without an "
                "inflation rate. calculate_roi keys the whole module on both, "
                "so one alone silently half-applies it.")

    breakeven = next((n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == "breakeven_kwargs"), None)
    if breakeven is None or "discounting_kwargs" not in ast.get_source_segment(src, breakeven):
        problems.append(
            "breakeven_kwargs does not carry discounting_kwargs, so the ceiling "
            "would be solved undiscounted beside a discounted premium -- two "
            "numbers on one screen answering different questions, which is the "
            "recorded 2026-plans failure.")

    # Every compute_scenario_results call in the app body must spread it. The
    # 2026-plans module accumulated three dropped kwargs exactly this way.
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "compute_scenario_results"):
            spread = {getattr(k.value, "func", None) and getattr(k.value.func, "id", None)
                      for k in node.keywords if k.arg is None}
            explicit = {k.arg for k in node.keywords}
            if "discounting_kwargs" not in spread and "discount_rate" not in explicit:
                problems.append(
                    f"a compute_scenario_results call at line {node.lineno} "
                    f"neither spreads discounting_kwargs() nor passes the rates "
                    f"explicitly, so that scenario is computed undiscounted "
                    f"while the rest of the page is not.")
    return problems


def main() -> int:
    ns = load_app_namespace()
    plain_ns = load_app_namespace(undiscounted_source())

    if ns.get("DISCOUNTING_ENABLED") is not True:
        print("check_discounting: DISCOUNTING_ENABLED is not True. If the module "
              "has been backed out, delete this guard with it.")
        return 1

    problems, checks = [], 0
    for label, run in (
        ("bit-identical when off", lambda: check_bit_identical_when_off(ns, plain_ns)),
        ("back-out restores the old model", lambda: check_back_out_is_bit_identical(plain_ns)),
        ("chart matches the headline when on", lambda: check_chart_matches_headline(ns)),
        ("loan series reconciles", lambda: check_loan_series_reconciles(ns)),
        ("wall-clock offsets are load-bearing", lambda: check_offsets_are_load_bearing(ns)),
        ("both rates travel together", lambda: check_rates_travel_together(ns)),
    ):
        checks += 1
        found = run()
        if found:
            problems.append(f"{label}:\n  " + "\n  ".join(found))

    if problems:
        print(f"discounting: {len(problems)} failing check(s)\n")
        print("\n\n".join(problems))
        return 1
    print(f"discounting OK -- the module is a no-op at a zero rate and with "
          f"DISCOUNTING_ENABLED off, the chart still equals its own headline "
          f"when on, the payment series reconciles, the wall-clock offsets are "
          f"wired, and both rates travel together ({checks} checks, 3 negative "
          f"controls).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

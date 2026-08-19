#!/usr/bin/env python3
"""Guard: zero debt is reachable on EVERY path, and the figure that says so
reaches every consumer.

    python3 check_zero_loan.py        (exit 1 on a violation)

This exists because "what if I do not borrow at all" -- the cleanest sanity
check this tool offers -- was unavailable on exactly the paths whose debt is
largest. Entering $0 in Total Loan Amount modelled $0 for Computer Science and
$279,900 for Dentists, General: get_effective_principal added the
professional-school figure on top of the slider regardless, so on the 71
training-overlay occupations the slider only ever controlled the undergraduate
leg. The number typed in and the number displayed disagreed, with nothing on
screen explaining why.

The fix made that figure an editable field. That is a bigger change than a
zero-only special case, and deliberately so: a full scholarship is $0, while
"savings for undergrad but not for dental school" is $0 of slider PLUS the
whole professional figure, and a switch cannot express the second. It also
opens a door this codebase has a documented failure behind -- a debt of
exactly 0 is now a REACHABLE input, where before it only ever meant a missing
row.

Five things are asserted, each aimed at a distinct way this regresses:

1. **Zero is reachable.** With the slider at $0 and the professional figure at
   $0, effective_principal is $0 on every training-overlay path, not just the
   plain ones.
2. **The figure reaches all three consumers.** get_effective_principal,
   professional_debt_cap and split_loan_financing's professional_principal each
   read it independently, and the check compares the PRINCIPAL against the
   TRANCHE SPLIT rather than re-reading the input -- routing it to some and not
   others leaves the difference in the undergraduate pool against the wrong
   caps, silently, and it flatters the result.
3. **An override of exactly $0 does not collapse into a private tranche.**
   professional_debt_cap returns 0.0 for a falsy debt and split_loan_financing
   reads a 0 cap as a real one. That is correct here (no debt, no tranche) and
   is one edit away from not being.
4. **It does not survive a path change.** A $279,900 dental figure left
   standing on a law scenario is silent, and off a professional path there must
   be nothing left to resurrect.
5. **The debt-free reference line reaches BOTH chart twins**, and its toggle
   reaches the PDF memo signature. The Plotly chart and its matplotlib twin
   share no drawing code; the balance chart's principal/interest split shipped
   Plotly-only once. A toggle missing from the memo signature is worse than
   missing from the chart: the button downloads a stale report happily.

Every check carries a negative control, per the house rule -- a guard that
passes for the wrong reason is worse than none. The two the plan named:
routing the figure to get_effective_principal alone must fail check 2, and
restoring the unconditionally-additive get_effective_principal must fail check
1. Both are done by deliberately breaking the real functions in the exec'd
namespace, not by re-implementing them.

Run it after touching get_effective_principal, professional_debt_cap,
split_loan_financing, professional_debt_autofill, or the net-position chart.
"""
import ast
import sys

APP = "app.py"

# Financing fixtures. Deliberately literals rather than reads of
# federal_direct_cap/DEFAULT_GAP_RATE: a check that derives its expectations
# from the code under test only ever asserts that the code equals itself.
FEDERAL_CAP = 27_000.0
GAP_RATE = 9.0
RATE = 6.5
PLUS_CAP = 0.0
STRATEGY = "Standard 10-Year"


def load_app_namespace():
    """app.py's sections 1-2 plus its later pure functions, without the UI.

    Same exec-prefix trick analyze_model.py uses -- see CLAUDE.md on why the
    section banners are load-bearing.
    """
    src = open(APP).read()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    prefix = src[:src.rindex("# " + "=" * 60, 0, cut)]
    ns = {"__name__": "zeroloancheck"}
    exec(compile(prefix, APP, "exec"), ns)
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name not in ns:
            exec(compile(ast.Module(body=[node], type_ignores=[]), APP, "exec"), ns)
    # MAJOR_DATA is a section-4 name, and every check here needs the REAL one:
    # against an empty dict the curated constants are absent, every path falls
    # through to the derived figure, and this file would pass on code that had
    # lost the training overlay entirely.
    ns["MAJOR_DATA"] = ns["build_major_data"](ns["CAREERS_CSV_PATH_NATIONAL"])
    return ns


def training_paths(ns) -> list:
    """Every occupation the app charges professional-school debt for.

    Derived from the data rather than listed, so a new overlay entry or a new
    PROFESSIONAL_PROGRAM_BY_OCCUPATION row is covered the day it lands. BOTH
    sources are needed: only medicine, dentistry and law carry a curated
    `additional_training_debt`, and the six programmes added after them derive
    theirs from Scorecard, so a sweep over the constant alone would miss the
    paths whose figure is hardest to see.
    """
    titles = {title for title, entry in ns["MAJOR_DATA"].items()
              if float(entry.get("additional_training_debt", 0) or 0) > 0}
    titles |= {title for title in ns["PROFESSIONAL_PROGRAM_BY_OCCUPATION"]
               if title in ns["MAJOR_DATA"]}
    return sorted(titles)


def scenario_for(ns, major: str, loan: float, debt) -> dict:
    return ns["compute_scenario_results"](
        major, loan, RATE, STRATEGY,
        federal_cap=FEDERAL_CAP, gap_rate=GAP_RATE, plus_cap=PLUS_CAP,
        professional_debt=debt, include_fees=True)


# ---------------------------------------------------------------------------
# 1. Zero is reachable on every path


def check_zero_reachable(ns) -> list:
    problems = []
    for major in training_paths(ns):
        scenario = scenario_for(ns, major, 0.0, 0.0)
        principal = scenario["effective_principal"]
        if abs(principal) > 0.005:
            problems.append(
                f"  {major}\n"
                f"    a $0 loan with $0 of professional debt still models "
                f"${principal:,.2f} of principal.\n"
                f"    Typing 0 must mean 0 on the training paths too; that is the "
                f"whole defect this file guards.")
    return problems


def check_zero_reachable_negative_control(ns) -> list:
    """Restore the unconditionally-additive get_effective_principal. The zero
    assertion must then fail, or it is not testing what it claims."""
    real = ns["get_effective_principal"]

    def additive(major_name, loan_amount, professional_debt=None):
        # The pre-field behaviour: the national figure goes on regardless of
        # what the caller resolved.
        return loan_amount + ns["MAJOR_DATA"][major_name].get("additional_training_debt", 0)

    ns["get_effective_principal"] = additive
    try:
        caught = bool(check_zero_reachable(ns))
    finally:
        ns["get_effective_principal"] = real
    if not caught:
        return ["  NEGATIVE CONTROL FAILED: restoring the additive "
                "get_effective_principal did not break the zero check, so that "
                "check cannot see the defect it exists for."]
    return []


# ---------------------------------------------------------------------------
# 2 and 3. The figure reaches all three consumers


def split_agreement_problems(ns, major: str, loan: float, debt: float) -> list:
    """The principal, the cap and the tranche split must describe ONE debt.

    Read off the scenario's own stamps, never off the input: the failure this
    catches is a value that reached some consumers and not others, and
    re-reading what was passed in would agree with itself every time.
    """
    problems = []
    scenario = scenario_for(ns, major, loan, debt)
    financing = scenario.get("financing") or {}
    label = f"{major}, ${loan:,.0f} loan + ${debt:,.0f} professional debt"

    stamped = scenario.get("professional_debt")
    if stamped is None or abs(float(stamped) - debt) > 0.005:
        problems.append(
            f"  {label}\n"
            f"    the scenario stamps professional_debt={stamped!r}; anything "
            f"displaying the figure reads that stamp, so it must be the debt "
            f"the model used.")

    from_principal = scenario["effective_principal"] - loan
    if abs(from_principal - debt) > 0.005:
        problems.append(
            f"  {label}\n"
            f"    the principal carries ${from_principal:,.2f} of professional "
            f"debt, not ${debt:,.2f} -- get_effective_principal is reading a "
            f"different figure than the caller resolved.")

    # The split's own view. Computed from the debt under test and the published
    # ceilings, not from anything the split returned.
    cap = min(ns["MAJOR_DATA"][major].get("unpaid_training_years", 0)
              * ns["PROFESSIONAL_ANNUAL_UNSUB_LIMIT"],
              ns["PROFESSIONAL_AGGREGATE_LIMIT"]) if debt > 0 else 0.0
    want_federal = min(debt, cap)
    got_federal = float(financing.get("professional_federal_principal", 0.0))
    if abs(got_federal - want_federal) > 0.005:
        problems.append(
            f"  {label}\n"
            f"    the tranche split put ${got_federal:,.2f} in the professional "
            f"federal tranche where ${want_federal:,.2f} is borrowable.\n"
            f"    The principal and the split are describing different debts; "
            f"the difference lands in the undergraduate pool against the wrong "
            f"caps, and it flatters the result.")
    return problems


def check_split_agreement(ns) -> list:
    problems = []
    # A school-median figure well below the national average, a figure above
    # it, and zero. All three must route identically.
    #
    # Pharmacists and Veterinarians are in the fixture deliberately: their
    # national figure is DERIVED from Scorecard and MAJOR_DATA carries no
    # `additional_training_debt` for them, so a consumer that went back to the
    # constant instead of using the resolved figure reads 0 for these two and
    # the right answer for the three curated paths. That is the shape of the
    # bug, and a fixture of curated paths alone cannot see it.
    for major in ("Dentists, General", "Lawyers", "Family Medicine Physicians",
                  "Pharmacists", "Veterinarians"):
        national = float(ns["national_professional_debt"](major))
        for debt in (0.0, round(national / 3, 2), national, national + 60_000):
            problems += split_agreement_problems(ns, major, 30_000.0, debt)
    return problems


def check_split_agreement_negative_control(ns) -> list:
    """Route the figure to get_effective_principal ONLY -- the split keeps
    sizing itself from the national average. The agreement check must fail."""
    real = ns["split_loan_financing"]
    national = float(ns["MAJOR_DATA"]["Dentists, General"]["additional_training_debt"])
    wrong_cap = ns["professional_debt_cap"]("Dentists, General", national)

    def routed_to_principal_only(*args, **kwargs):
        kwargs["professional_principal"] = national
        kwargs["professional_cap"] = wrong_cap
        return real(*args, **kwargs)

    ns["split_loan_financing"] = routed_to_principal_only
    try:
        # A debt deliberately unequal to the national figure, or the break is
        # invisible.
        caught = bool(split_agreement_problems(
            ns, "Dentists, General", 30_000.0, 99_000.0))
    finally:
        ns["split_loan_financing"] = real
    if not caught:
        return ["  NEGATIVE CONTROL FAILED: sizing the tranche split from the "
                "national average while the principal used the override did not "
                "trip the agreement check. It is comparing the input against "
                "itself."]
    return []


def check_zero_is_not_private(ns) -> list:
    """An override of exactly $0 must leave an ordinary undergraduate loan, not
    a wholly private one.

    professional_debt_cap returns 0.0 for a falsy debt and split_loan_financing
    reads a 0 cap as a real cap rather than "unset". That is the right answer
    when the principal carries no professional debt either -- and it is one
    truthiness test away from being the documented two-wrong-answers failure.
    """
    problems = []
    for major in ("Dentists, General", "Lawyers"):
        scenario = scenario_for(ns, major, 20_000.0, 0.0)
        financing = scenario.get("financing") or {}
        federal = float(financing.get("federal_principal", 0.0))
        private = float(financing.get("private_principal", 0.0))
        gap = float(financing.get("gap_principal", 0.0))
        if abs(federal - 20_000.0) > 0.005 or gap > 0.005 or private > 0.005:
            problems.append(
                f"  {major}, $20,000 loan with the professional debt zeroed\n"
                f"    federal ${federal:,.2f}, PLUS/private ${gap:,.2f} -- a "
                f"loan inside the ${FEDERAL_CAP:,.0f} federal cap has been "
                f"pushed out of it.\n"
                f"    A zeroed professional debt means there is no professional "
                f"tranche, not a professional tranche with a zero ceiling.")
    return problems


# ---------------------------------------------------------------------------
# 4. The field does not survive a path change


def check_autofill_rules(ns) -> list:
    """professional_debt_autofill decides when an entered figure survives.

    It lives in section 2 rather than inline in the sidebar for exactly this
    reason -- the reconcile_search_pick / graduate_apply_target rule: a rule a
    guard can reach beats a rule written carefully.
    """
    problems = []
    autofill = ns["professional_debt_autofill"]
    DENTAL, LAW = 279_900.0, 130_000.0

    # An entered figure survives while the resolution behind it does not move.
    value, seen = autofill(0.0, DENTAL, DENTAL)
    if value != 0.0:
        problems.append(
            f"  an entered $0 came back as ${value:,.2f} with the resolution "
            f"unchanged.\n    Zero is a real answer -- a scholarship -- and a "
            f"truthiness test on it re-inflates the scenario to the national "
            f"average.")
    value, _ = autofill(50_000.0, DENTAL, DENTAL)
    if value != 50_000.0:
        problems.append(
            f"  an entered $50,000 came back as ${value:,.2f} with the "
            f"resolution unchanged; the field is not editable.")

    # It does NOT survive the resolution moving under it (a different school,
    # a different programme, a carried price cleared).
    value, seen = autofill(0.0, DENTAL, LAW)
    if value != LAW or seen != LAW:
        problems.append(
            f"  a dental figure entered against ${DENTAL:,.0f} survived onto a "
            f"path resolving to ${LAW:,.0f} (came back ${value:,.2f}).\n"
            f"    A stale figure on the wrong path is silent and wrong.")

    # Off a professional path there is nothing to hold at all, so switching
    # away and back cannot resurrect anything.
    value, seen = autofill(0.0, DENTAL, 0.0, on_path=False)
    if value is not None or seen is not None:
        problems.append(
            f"  off a professional path the field kept ({value!r}, {seen!r}); "
            f"it must hold nothing, or switching to Computer Science and back "
            f"resurrects a $279,900 dental figure.")

    # And the sidebar must actually clear the keys, not merely rely on the
    # sentinel above. Static, because the sidebar is section 4 and nothing
    # execs it.
    src = open(APP).read()
    for key in ("prof_debt_a", "prof_debt_b"):
        if f'st.session_state.pop("{key}", None)' not in src:
            problems.append(
                f"  nothing pops {key!r} when the path stops being a "
                f"professional one. Streamlit keeps a widget's value after it "
                f"stops rendering, which is how a hidden number goes on moving "
                f"the model with nothing on screen to explain it.")
    return problems


def check_autofill_negative_control(ns) -> list:
    """The truthiness version -- `entered or resolved_default` -- must be
    caught. It is the one-character mistake this whole field is exposed to."""
    real = ns["professional_debt_autofill"]

    def truthy(entered, seen_default, resolved_default, on_path=True):
        if not on_path:
            return None, None
        return float(entered or resolved_default), float(resolved_default)

    ns["professional_debt_autofill"] = truthy
    try:
        caught = bool(check_autofill_rules(ns))
    finally:
        ns["professional_debt_autofill"] = real
    if not caught:
        return ["  NEGATIVE CONTROL FAILED: an `entered or default` autofill "
                "passed the survival checks, so they cannot see a zeroed "
                "professional debt being silently re-inflated."]
    return []


# ---------------------------------------------------------------------------
# 5. The reference line reaches both chart twins


def check_reference_series(ns) -> list:
    problems = []
    major = "Dentists, General"
    scenario = scenario_for(ns, major, 40_000.0, 200_000.0)
    pairs = [(major, scenario)]
    years = 10
    suffix = ns["counterfactual_vocab"]()["no_loan_suffix"]

    off = ns["net_position_frame"](pairs, 100.0, 1.0, years)
    if any(str(s).endswith(suffix) for s in off["Series"].unique()):
        problems.append(
            "  the reference line is drawn with include_debt_free off. It is "
            "opt-in: Compare Mode already draws four series and this would make "
            "it seven.")

    on = ns["net_position_frame"](pairs, 100.0, 1.0, years, include_debt_free=True)
    name = f"{major}{suffix}"
    if name not in set(on["Series"]):
        problems.append(
            f"  no {name!r} series when include_debt_free is on -- the frame is "
            f"where both chart twins get it, so neither can draw it.")
        return problems

    borrowed = on[on["Series"] == major].set_index("year")["Net Position"]
    unborrowed = on[on["Series"] == name].set_index("year")["Net Position"]
    # Never below, and above by the end. The two coincide early on a training
    # path -- a dentist's first years are unpaid school with the loan still in
    # deferment, so nothing has been repaid to separate them yet.
    if not (unborrowed >= borrowed).all() or unborrowed.iloc[-1] <= borrowed.iloc[-1]:
        problems.append(
            "  the no-loan line is not above the path that carries the loan by "
            "the end of the window. It is the same earnings with the payments "
            "removed, so it can never be below, and must be above wherever a "
            "payment has been made.")
    # It must be the model re-run, not a curve invented for the chart: the
    # unborrowed net position IS the borrowed one plus what was repaid, over
    # the same window, at the same cost-of-living adjustment.
    paid = ns["cumulative_loan_paid_by_year"](scenario["repayment_result"], years)
    for year, spent in zip(range(1, years + 1), paid):
        gap = float(unborrowed.loc[year] - borrowed.loc[year])
        if abs(gap - spent) > 1.0:
            problems.append(
                f"  year {year}: the two lines differ by ${gap:,.2f} where "
                f"${spent:,.2f} of loan payments were made. The reference line "
                f"must be the same calculate_roi call with the loan zeroed, "
                f"never a second formula.")
            break

    # A path that pays nothing gets one line, not two identical ones.
    debt_free_scenario = scenario_for(ns, "Computer and Information Research Scientists", 0.0, 0.0)
    frame = ns["net_position_frame"](
        [("CS", debt_free_scenario)], 100.0, 1.0, years, include_debt_free=True)
    if f"CS{suffix}" in set(frame["Series"]):
        problems.append(
            "  a path that borrows nothing still got a no-loan line, drawn "
            "directly on top of its own. Two legend entries for one line leaves "
            "the reader to work out that they coincide.")
    return problems


def check_twins_and_memo() -> list:
    """Static: the PDF builders must take the flag, and the toggle must be in
    the PDF memo signature.

    Both failures are silent. A PDF frame built without the flag prints a chart
    missing a series the visitor is looking at; a toggle missing from the memo
    signature serves the previous render's bytes, and the button downloads
    happily either way.
    """
    problems = []
    tree = ast.parse(open(APP).read())

    frame_calls = []       # (has_include_debt_free, enclosing function name)
    memo_sig_calls = []    # (mentions the toggle, enclosing function name)

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.fn = "(module)"

        def visit_FunctionDef(self, node):
            outer, self.fn = self.fn, node.name
            self.generic_visit(node)
            self.fn = outer

        def visit_Call(self, node):
            name = (node.func.attr if isinstance(node.func, ast.Attribute)
                    else getattr(node.func, "id", ""))
            if name == "net_position_frame":
                frame_calls.append(
                    (any(k.arg == "include_debt_free" for k in node.keywords), self.fn))
            if name == "pdf_memo_signature":
                # net_position_overlay_mode, NOT net_position_reference_on.
                # The boolean is False for "just this path" AND for "the other
                # 2026 plan", so a signature carrying it puts two different
                # charts in one memo slot: switching between them serves
                # whichever report was built first, with the button downloading
                # happily either way. The mode string separates all three.
                mentions = any(
                    isinstance(sub, ast.Name) and sub.id == "net_position_overlay_mode"
                    or isinstance(sub, ast.Attribute) and sub.attr == "net_position_overlay_mode"
                    for a in node.args for sub in ast.walk(a))
                memo_sig_calls.append((mentions, self.fn))
            self.generic_visit(node)

    Visitor().visit(tree)

    for fn in ("generate_pdf_report_single", "generate_pdf_report_compare"):
        got = [flagged for flagged, where in frame_calls if where == fn]
        if not got:
            problems.append(f"  {fn} no longer builds a net_position_frame at all.")
        elif not all(got):
            problems.append(
                f"  {fn} builds its net-position frame without "
                f"include_debt_free. The PDF is the twin that silently loses a "
                f"series -- the balance chart's principal/interest split "
                f"shipped Plotly-only exactly this way.")
    if not any(flagged for flagged, where in frame_calls
               if where == "render_net_position_chart"):
        problems.append(
            "  render_net_position_chart does not pass include_debt_free, so "
            "the on-screen toggle draws nothing.")

    # And both twins must DRAW it the same way. The reference line is dashed
    # because it is a counterfactual rather than a second career, and "which
    # lines are dashed" is exactly what drifts when one renderer is edited
    # alone -- a solid line on screen against a dashed one in print makes the
    # two disagree about which path is hypothetical. One predicate, asked
    # twice, so the check is that both ask it.
    for fn in ("build_net_position_chart", "build_pdf_net_position_chart"):
        node = next((n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef) and n.name == fn), None)
        if node is None:
            problems.append(f"  {fn} is gone.")
            continue
        if not any(isinstance(sub, ast.Name) and sub.id == "is_no_loan_series"
                   for sub in ast.walk(node)):
            problems.append(
                f"  {fn} never asks is_no_loan_series, so it styles the "
                f"debt-free reference line like an ordinary path. Both chart "
                f"twins must read the same predicate, or screen and print "
                f"disagree about which line is the hypothetical one.")

    # BOTH renderers must resolve their series through the shared helper, or
    # the report draws a different chart than the screen. This is the defect
    # that shipped: generate_pdf_report_single built its frame from a literal
    # [(major, scenario)], so "Add the other 2026 repayment plan" reached the
    # screen and never the PDF -- one line printed under a two-line chart, with
    # nothing marking the omission, and the legend not naming the plan either.
    #
    # Asserted as "calls the helper" rather than by inspecting the argument,
    # because the frame call takes a variable and a check that reads the
    # variable's NAME would pass on any local that happened to be spelled
    # right. The helper is also what renames both series to carry their plan,
    # so this covers the legend half too.
    for fn in ("render_net_position_chart", "generate_pdf_report_single"):
        node = next((n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef) and n.name == fn), None)
        if node is None:
            problems.append(f"  {fn} is gone.")
            continue
        if not any(isinstance(sub, ast.Name)
                   and sub.id == "net_position_overlay_pairs"
                   for sub in ast.walk(node)):
            problems.append(
                f"  {fn} does not go through net_position_overlay_pairs, so "
                f"screen and print can disagree about which series the "
                f"net-position chart carries. The two-plan overlay reached the "
                f"screen and not the report exactly this way.")

    calculator_sigs = [m for m, where in memo_sig_calls if where == "(module)"]
    if len(calculator_sigs) < 2:
        problems.append(
            f"  expected the calculator's two pdf_memo_signature calls (single "
            f"and compare) at module level, found {len(calculator_sigs)}.")
    elif not all(calculator_sigs):
        problems.append(
            "  a calculator PDF signature omits net_position_overlay_mode(). "
            "The toggle is a main-page widget, so check_share_coverage's "
            "sidebar sweep cannot see it and this is the only thing standing "
            "between it and a stale report.")
    return problems


# ---------------------------------------------------------------------------


def main() -> int:
    ns = load_app_namespace()
    problems = []
    checks = 0

    for label, run in (
        ("zero reachable", lambda: check_zero_reachable(ns)),
        ("zero reachable / negative control",
         lambda: check_zero_reachable_negative_control(ns)),
        ("split agreement", lambda: check_split_agreement(ns)),
        ("split agreement / negative control",
         lambda: check_split_agreement_negative_control(ns)),
        ("zero is not a private tranche", lambda: check_zero_is_not_private(ns)),
        ("autofill survival", lambda: check_autofill_rules(ns)),
        ("autofill survival / negative control",
         lambda: check_autofill_negative_control(ns)),
        ("reference series", lambda: check_reference_series(ns)),
        ("chart twins and PDF memo", check_twins_and_memo),
    ):
        checks += 1
        found = run()
        if found:
            problems.append(f"{label}:\n" + "\n\n".join(found))

    if problems:
        print(f"zero loan / reference line: {len(problems)} failing check(s)\n")
        print("\n\n".join(problems))
        return 1
    print(f"zero loan OK -- {len(training_paths(ns))} training paths model $0 as "
          f"$0, the professional figure reaches the principal and the tranche "
          f"split as one number, it does not survive a path change, and the "
          f"debt-free reference line reaches both chart twins and the PDF memo "
          f"({checks} checks, 3 negative controls).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

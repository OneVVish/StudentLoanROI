#!/usr/bin/env python3
"""Guard: no loan payment may land in a year the borrower is still enrolled.

    python3 check_in_school_deferment.py     (exit 1 on any mismatch)

The app models a professional path as unpaid school, and until 2026-08-14 it
started the repayment clock at the BACHELOR'S. A dental student with a $242,900
private tranche was charged $3,012 a month for four years against $0 of income:
$144,557 of payments nobody could make, dragging the net-position chart to
-$122,705 before the first pay cheque. The take-home panel printed the same
thing in words -- "Monthly Disposable Income -$3,022" at the Starting stage --
which is the model describing a person who cannot exist. It was reported as a
question about the chart: "why does the net position go negative for dentists in
the first 4 years?"

Federal loans are in in-school deferment while enrolled at least half time, and
private lenders defer too. So repayment begins when ENROLMENT ends, which is
overlay_school_years -- not the whole programme, because a residency is paid
work and a resident really does start repaying.

DEFERMENT IS NOT A DISCOUNT, and half of this file exists to keep the arithmetic
honest about that. Nothing is paid, but everything except the subsidized share
accrues and capitalises: the dentist's balance climbs from $469,900 to $647,953
before the first payment, the payoff moves from 30 years to 34, and lifetime
interest rises sharply. Cash inside a ten-year window falls; total cost rises.
Both have to stay true at once.

THE FAILURE THIS EXISTS TO CATCH IS SILENT. Only the income-driven simulators
emit a per-month payment column; a fixed plan is flat, so everything downstream
reconstructs it as monthly_payment x months. That reconstruction is exactly
wrong after a shift, because the first months are enrolled ones with no payment.
The first version of apply_in_school_deferment dropped the column instead of
filling it, and $50,713 a year went straight back onto a dental student earning
nothing -- while the balance chart beside it correctly showed the climb, and
every one of the other nine guards passed.
"""
import ast
import inspect
import sys

TOLERANCE = 1.0          # dollars
COA, RATE, GAP_RATE = 45_619.0, 6.5, 8.5


def strategies(ns):
    """The three plans to defer, as app.py spells them TODAY.

    Read from the app rather than written out, which is the opposite of the
    rule this file's siblings follow for EXPECTED values -- and right here,
    because these are INPUTS. compute_scenario_results dispatches on the label
    and its final branch is IDR, so an unknown string is not an error: it
    silently runs a different plan. These were literals until the RAP label was
    renamed on 2026-08-14, at which point "Repayment Assistance Plan (RAP)"
    would have fallen through to IDR and this guard would have tested IDR twice
    and RAP never, while passing.
    """
    return (ns["STANDARD_STRATEGY_LABEL"], ns["RAP_STRATEGY_LABEL"],
            ns["IDR_STRATEGY_LABEL"])


def load_app_namespace():
    """app.py's sections 1-2, without the UI."""
    src = open("app.py").read()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    prefix = src[:src.rindex("# " + "=" * 60, 0, cut)]
    ns = {"__name__": "defermentcheck"}
    exec(compile(prefix, "app.py", "exec"), ns)
    ns["MAJOR_DATA"] = ns["build_major_data"](ns["CAREERS_CSV_PATH_NATIONAL"])
    return ns


def build(ns, title, strategy, loan=190_000.0):
    """One scenario, financed the way the page finances it -- caps and all, so
    the PLUS/private tranche is real. A bare call would put the whole balance in
    the federal pool, where an income-driven payment at $0 income is already
    near zero and the bug this file guards would be invisible."""
    py = ns["program_years_for_major"](title)
    gy = ns["graduate_years_for_major"](title)
    cost_years = ns["school_cost_years"](py, gy, title)
    grad_cost_years = gy if cost_years == py else 0
    schedule = ns["compute_loan_schedule_by_year"](COA, 0, 0, 0.027, years=cost_years)
    return ns["compute_scenario_results"](
        title, loan, RATE, strategy, col_index=100.0, hs_wage_index=1.0,
        enrollment_years=0, working_years=0,
        baseline_start_age=ns["baseline_start_age_for"](py, 0, title),
        federal_cap=(ns["federal_direct_cap"](
            ns["undergraduate_schedule"](schedule, grad_cost_years), "dependent")
            + ns["graduate_direct_cap"](grad_cost_years)),
        subsidized_cap=ns["federal_subsidized_cap"](schedule, grad_cost_years),
        plus_cap=ns["parent_plus_cap"](schedule, "dependent", 2026, graduate_years=gy),
        gap_rate=GAP_RATE, include_fees=True)


def deferred_titles(ns):
    return [t for t in sorted(ns["MAJOR_DATA"])
            if ns["overlay_school_years"](t) > 0][:14]


def check_no_payment_while_enrolled(ns, paid_by_year=None):
    """THE headline rule. Not one dollar leaves in a year spent enrolled."""
    paid_by_year = paid_by_year or ns["cumulative_loan_paid_by_year"]
    problems = []
    for title in deferred_titles(ns):
        enrolled = ns["overlay_school_years"](title)
        for strategy in strategies(ns):
            scenario = build(ns, title, strategy)
            paid = paid_by_year(scenario["repayment_result"], enrolled + 2)
            if paid[enrolled - 1] > TOLERANCE:
                problems.append(
                    f"  {title!r} on {strategy}: ${paid[enrolled - 1]:,.0f} paid "
                    f"across {enrolled} years still enrolled")
            if paid[enrolled] <= TOLERANCE:
                problems.append(
                    f"  {title!r} on {strategy}: still paying nothing the year "
                    "after enrolment ends, so repayment never starts")
    return problems


def check_capitalisation(ns):
    """Deferment is not a discount. The balance must GROW by compound interest
    on everything except the subsidized share, and the growth is checked against
    arithmetic written out here rather than against the helper's own return."""
    problems = []
    cases = [(100_000.0, 0.0, 6.5, 48), (100_000.0, 19_000.0, 6.5, 48),
             (242_900.0, 0.0, 8.5, 48), (50_000.0, 19_000.0, 6.5, 36),
             (10_000.0, 19_000.0, 6.5, 48)]
    for principal, subsidized, rate, months in cases:
        got = ns["in_school_deferment"](principal, subsidized, rate, months)
        exempt = min(subsidized, principal)
        want = (principal - exempt) * (1 + rate / 100 / 12) ** months + exempt
        if abs(got["principal"] - want) > TOLERANCE:
            problems.append(
                f"  ${principal:,.0f} ({months}mo @ {rate}%, ${exempt:,.0f} "
                f"subsidized): grew to ${got['principal']:,.0f}, expected "
                f"${want:,.0f}")
        if abs(got["capitalized"] - (want - principal)) > TOLERANCE:
            problems.append(
                f"  ${principal:,.0f}: capitalised ${got['capitalized']:,.0f}, "
                f"expected ${want - principal:,.0f}")
        if len(got["rows"]) != months:
            problems.append(f"  ${principal:,.0f}: {len(got['rows'])} deferment "
                            f"rows for {months} months")
        if any(row["payment"] for row in got["rows"]):
            problems.append(f"  ${principal:,.0f}: a deferment row carries a "
                            "payment")
    # Zero months is a real answer, not an edge case: most paths have no
    # deferment at all and must come back untouched.
    flat = ns["in_school_deferment"](100_000.0, 0.0, 6.5, 0)
    if flat["principal"] != 100_000.0 or flat["rows"]:
        problems.append("  zero deferment months did not leave the balance alone")
    return problems


def check_subsidized_is_exempt(ns):
    """The subsidized share must accrue NOTHING, and the difference it makes has
    to be exactly its own foregone growth -- not merely 'less'."""
    problems = []
    principal, rate, months = 100_000.0, 6.5, 48
    none = ns["in_school_deferment"](principal, 0.0, rate, months)
    some = ns["in_school_deferment"](principal, 19_000.0, rate, months)
    want = 19_000.0 * ((1 + rate / 100 / 12) ** months - 1)
    if abs((none["capitalized"] - some["capitalized"]) - want) > TOLERANCE:
        problems.append(
            f"  exempting $19,000 changed the capitalised interest by "
            f"${none['capitalized'] - some['capitalized']:,.0f}; that share's "
            f"own growth is ${want:,.0f}")
    # A tranche smaller than the ceiling is simply all subsidized.
    small = ns["in_school_deferment"](5_000.0, 19_000.0, rate, months)
    if small["capitalized"] > TOLERANCE:
        problems.append(f"  a $5,000 balance under a $19,000 subsidized ceiling "
                        f"still accrued ${small['capitalized']:,.0f}")
    return problems


def check_totals_absorb_the_deferment(ns):
    """payoff_years must include the enrolled years, and total_interest must
    include the capitalised interest -- the money-in/money-out identity
    check_repayment_invariants asserts fails by exactly that amount otherwise."""
    problems = []
    for title in deferred_titles(ns)[:6]:
        enrolled = ns["overlay_school_years"](title)
        scenario = build(ns, title, "Standard 10-Year")
        result = scenario["repayment_result"]
        if abs(result.get("deferment_months", 0) - enrolled * 12) > 0:
            problems.append(f"  {title!r}: result records "
                            f"{result.get('deferment_months', 0)} deferment "
                            f"months for {enrolled} enrolled years")
        if result["payoff_years"] < enrolled:
            problems.append(f"  {title!r}: payoff of "
                            f"{result['payoff_years']:.1f}y is shorter than the "
                            f"{enrolled}y spent enrolled")
        schedule = result.get("schedule")
        if schedule is None or "payment" not in schedule.columns:
            problems.append(f"  {title!r}: deferred schedule carries no payment "
                            "column, so every consumer reconstructs a flat "
                            "payment from month 1 -- the silent failure")
    return problems


def check_disclosure_cannot_drift(ns):
    """The deferment warning quotes a lifetime interest figure, and the metric
    directly above it reads `combined_repayment or repayment_result` -- which
    differ the moment a visitor carries an existing balance. Quoting the new
    loan's figure under a metric showing the combined one is the contradiction
    this codebase keeps having to fix, so the figure is a REQUIRED keyword-only
    argument. This asserts it stays one: give it a default and the two silently
    become different numbers again.

    Checked on the signature rather than by rendering, because the renderer is
    Streamlit and this file has no runtime -- but a default is exactly the change
    that would reintroduce the bug, and it is visible from here.
    """
    problems = []
    fn = ns.get("render_forgiveness_note")
    if fn is None:
        return ["  render_forgiveness_note is missing"]
    params = inspect.signature(fn).parameters
    arg = params.get("total_interest")
    if arg is None:
        problems.append("  render_forgiveness_note takes no total_interest; the "
                        "warning would quote the new loan's figure under a "
                        "metric showing the combined one")
        return problems
    if arg.kind is not inspect.Parameter.KEYWORD_ONLY:
        problems.append("  total_interest is not keyword-only, so a positional "
                        "call can pass the wrong result silently")
    if arg.default is not inspect.Parameter.empty:
        problems.append(f"  total_interest has a default ({arg.default!r}); a "
                        "missed call site must be a TypeError, not a quietly "
                        "different number")
    # Both call sites must pass it from the SAME expression the metric uses.
    src = open("app.py").read()
    calls = [n for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "render_forgiveness_note"]
    if len(calls) != 2:
        problems.append(f"  expected 2 render_forgiveness_note call sites, "
                        f"found {len(calls)} -- a new one may omit the figure")
    for call in calls:
        names = [k.arg for k in call.keywords]
        if "total_interest" not in names:
            problems.append("  a render_forgiveness_note call site does not pass "
                            "total_interest")
        else:
            value = next(k.value for k in call.keywords if k.arg == "total_interest")
            text = ast.unparse(value)
            if "shown" not in text:
                problems.append(f"  a call site passes total_interest={text}, "
                                "which is not the result the metric shows "
                                "(`combined_repayment or repayment_result`)")
    return problems


def check_private_structure_disclosure(ns):
    """The model assumes a private loan's SHAPE, and says so.

    Two assumptions, both silent until now and neither neutral: repayment
    starts the month enrolment ends with no grace period, and a full
    amortising payment runs through residency. Real private loans commonly
    carry ~6 months of grace with interest accruing, and lenders commonly
    offer interest-only or reduced payments during training.

    BOTH DIRECTIONS OR IT IS ADVERTISING. Either option lowers the monthly
    payment AND raises the total. Saying only the first is the flattering
    half, and this app's argument for being trusted is that it does not take
    it. Asserted directly, because prose decays toward the comfortable.

    It must also stay out of lender recommendations. This tool has no source
    for which lender offers what and no way to keep one current -- the same
    line the extra-payment note holds about servicers.
    """
    problems = []
    disclose = ns["private_structure_disclosure"]
    overlay = ns["ADVANCED_TRAINING_OVERLAY"]
    schooled = [t for t, e in overlay.items()
                if int(e.get("unpaid_training_years") or 0) > 0]
    if not schooled:
        return ["  no path carries unpaid school years, so this check tests nothing"]
    for title in schooled:
        text = disclose(title)
        if not text:
            problems.append(f"  {title}: carries {overlay[title]['unpaid_training_years']} "
                            f"years of unpaid school and discloses nothing about "
                            f"the private loan's shape")
            continue
        if "grace period" not in text:
            problems.append(f"  {title}: does not mention the grace period the "
                            f"model assumes away")
        # BOTH DIRECTIONS.
        lowers = any(w in text for w in ("lowers", "lower"))
        raises = "RAISES" in text or "raises" in text
        if not (lowers and raises):
            problems.append(
                f"  {title}: names {'only the lower payment' if lowers else 'only the cost'}. "
                f"A grace period or reduced training payment does both, and "
                f"stating one is the flattering half.")
        stipend_years = int(overlay[title].get("stipend_training_years") or 0)
        if stipend_years and "training" not in text:
            problems.append(
                f"  {title}: has {stipend_years} stipend years and the "
                f"disclosure never mentions training payments")
        for lender in ("Sallie Mae", "SoFi", "Earnest", "College Ave", "Discover"):
            if lender.lower() in text.lower():
                problems.append(
                    f"  {title}: names {lender}. This tool has no source for "
                    f"which lender offers what.")
    # And it must stay OFF an ordinary path, which has no professional tranche
    # and no training to be shaped around.
    if disclose("Software Developers"):
        problems.append("  an ordinary career got the professional-path "
                        "private-loan disclosure")
    return problems


def negative_controls(ns):
    """Break it deliberately."""
    problems = []

    # (a) The bug as it actually shipped: drop the payment column on a fixed
    # schedule instead of filling it, so downstream reconstructs a flat payment
    # from month 1 of the timeline.
    def blind_paid_by_year(result, years):
        schedule = result.get("schedule")
        if schedule is None or schedule.empty:
            return [0.0] * years
        months = schedule["month"]
        paid = months * result.get("monthly_payment", 0.0)
        return [float(paid[months <= (y + 1) * 12].max() or 0.0)
                for y in range(years)]
    if not check_no_payment_while_enrolled(ns, blind_paid_by_year):
        problems.append("  reconstructing a flat payment from month 1 did not "
                        "fail the no-payment-while-enrolled check")

    # (b) No deferment at all for a path that has enrolled years.
    real = ns["overlay_school_years"]
    ns["overlay_school_years"] = lambda title: 0
    try:
        scenario = build(ns, "Dentists, General", "Standard 10-Year")
        paid = ns["cumulative_loan_paid_by_year"](scenario["repayment_result"], 4)
        if paid[3] <= TOLERANCE:
            problems.append("  removing the deferment still paid nothing over "
                            "four years, so the check proves nothing")
    finally:
        ns["overlay_school_years"] = real

    # (c) Exempting nothing must change the answer -- otherwise the subsidized
    # argument is being ignored and (b) would never notice.
    a = ns["in_school_deferment"](100_000.0, 19_000.0, 6.5, 48)["capitalized"]
    b = ns["in_school_deferment"](100_000.0, 0.0, 6.5, 48)["capitalized"]
    if abs(a - b) < TOLERANCE:
        problems.append("  the subsidized argument made no difference to the "
                        "capitalised interest")
    return problems


def main():
    ns = load_app_namespace()
    sections = [
        ("no payment lands in a year still enrolled",
         check_no_payment_while_enrolled(ns)),
        ("the balance capitalises by the right amount", check_capitalisation(ns)),
        ("the subsidized share accrues nothing", check_subsidized_is_exempt(ns)),
        ("payoff and interest absorb the deferment",
         check_totals_absorb_the_deferment(ns)),
        ("the disclosure cannot quote a different figure than the metric",
         check_disclosure_cannot_drift(ns)),
        ("the private loan's assumed shape is disclosed, both directions",
         check_private_structure_disclosure(ns)),
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
        print("\ncheck_in_school_deferment.py FAILED")
        return 1
    print(f"\nall checks passed over {len(deferred_titles(ns))} deferred paths")
    return 0


if __name__ == "__main__":
    sys.exit(main())

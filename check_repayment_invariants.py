#!/usr/bin/env python3
"""Guard: every repayment simulator must balance its own books.

    python3 check_repayment_invariants.py      (exit 1 on a violation)

This exists because of a bug nothing else could catch. `simulate_rap_schedule`
reported `total_interest: 0.0` for every scenario, on the reasoning that RAP
waives unpaid interest. It waives only the interest a payment does not cover --
so a borrower earning enough to cover it has nothing waived and pays all of it.
On a $190,000 loan that was $165,109 reported as $0.

Nothing flagged it. py_compile passed, the share-coverage guard passed,
analyze_model.py passed, all four PDFs rendered, and the page looked right --
because the figure was internally consistent with itself. It was simply false
about the world. A user reading the screen found it.

What that bug violated is an accounting identity, and identities are checkable:

    payments + forgiven + government_match  ==  principal + interest_paid

Read as money-in/money-out. The borrower's payments cover interest and some
principal; whatever principal they never covered was either forgiven or paid
down by the government's RAP match. Interest the payment never covered is
waived and appears on neither side -- it is a cost that simply never lands.

Reporting $0 interest breaks the right-hand side while payments on the left
stay real, so the identity fails by exactly the missing amount. It would have
failed loudly on the very first RAP scenario.

Run this after touching any simulator, the tranche split, or
combine_repayment_results.
"""
import ast
import sys

TOLERANCE = 1.0   # dollars; amortisation rounds per month


def load_app_namespace():
    """app.py's sections 1-2 plus its later pure functions, without the UI.

    Same exec-prefix trick analyze_model.py uses -- see CLAUDE.md on why the
    section banners are load-bearing.
    """
    src = open("app.py").read()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    prefix = src[:src.rindex("# " + "=" * 60, 0, cut)]
    ns = {"__name__": "invariantcheck"}
    exec(compile(prefix, "app.py", "exec"), ns)
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name not in ns:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), ns)
    return ns


def payments_made(result: dict) -> float:
    """What the borrower actually handed over across the life of the loan.

    Standard emits a flat monthly_payment and no per-month column; IDR and RAP
    emit `payment` because theirs moves with income. Both shapes are real, so
    read whichever is present rather than assuming one.
    """
    schedule = result["schedule"]
    if "payment" in schedule.columns:
        return float(schedule["payment"].sum())
    return float(result.get("monthly_payment", 0.0)) * len(schedule)


def check(label: str, principal: float, result: dict, rate: float = 6.5) -> list:
    problems = []
    interest = float(result.get("total_interest", 0.0) or 0.0)
    forgiven = float(result.get("forgiven_amount", 0.0) or 0.0)
    waived = float(result.get("waived_interest", 0.0) or 0.0)
    match = float(result.get("government_match", 0.0) or 0.0)
    paid = payments_made(result)

    lhs = paid + forgiven + match
    rhs = principal + interest
    if abs(lhs - rhs) > TOLERANCE:
        problems.append(
            f"  {label}\n"
            f"    payments {paid:,.2f} + forgiven {forgiven:,.2f} + gov match {match:,.2f}"
            f" = {lhs:,.2f}\n"
            f"    principal {principal:,.2f} + interest {interest:,.2f} = {rhs:,.2f}\n"
            f"    off by {lhs - rhs:,.2f}"
        )

    # The specific shape of the RAP bug: a real loan at a real rate, repaid
    # over real months, reporting no interest anywhere. Kept as its own check
    # because it names the failure in the terms it will recur in.
    months = len(result["schedule"])
    if rate > 0 and principal > 0 and months > 1 and interest <= 0 and waived <= 0:
        problems.append(
            f"  {label}\n"
            f"    reports NO interest at all -- neither paid nor waived -- on a "
            f"{principal:,.0f} loan repaid over {months} months. Interest cannot "
            f"be zero on both sides unless the rate is zero."
        )
    return problems


def main() -> int:
    ns = load_app_namespace()
    ns["MAJOR_DATA"] = dict(ns["CURATED_MAJOR_DATA"])
    # Two earners, because the plans behave oppositely across them: a high
    # earner covers the interest and has nothing waived or forgiven, a low one
    # has both. A single fixture would have missed the bug this file exists for.
    ns["MAJOR_DATA"]["HighEarner"] = {"starting_salary": 163500, "median_salary": 180000,
                                      "growth_rate": 0.03}
    ns["MAJOR_DATA"]["LowEarner"] = {"starting_salary": 32000, "median_salary": 36000,
                                     "growth_rate": 0.02}

    problems = []
    checked = 0
    for major in ("HighEarner", "LowEarner"):
        for principal in (5_000.0, 50_000.0, 190_000.0, 400_000.0):
            for rate in (0.0, 6.5, 9.07):
                cases = {
                    "Standard": lambda: ns["calculate_standard_repayment"](principal, rate),
                    "Tiered Standard": lambda: ns["calculate_standard_repayment"](
                        principal, rate, ns["calculate_tiered_standard_term"](principal)),
                    "IDR": lambda: ns["calculate_idr_repayment"](principal, rate, major),
                    "RAP": lambda: ns["simulate_rap_schedule"](principal, rate, major, 0),
                }
                for plan, build in cases.items():
                    result = build()
                    checked += 1
                    problems += check(
                        f"{plan}, {major}, ${principal:,.0f} @ {rate}%", principal, result, rate)

    # The combiner has to preserve the identity too: it is what presents a
    # forgivable federal tranche beside a non-forgivable private one as a
    # single bill, and summing the wrong fields there would be invisible.
    fed = ns["simulate_rap_schedule"](60_000.0, 6.5, "LowEarner", 0)
    priv = ns["calculate_standard_repayment"](130_000.0, 8.5)
    combined = ns["combine_repayment_results"](fed, priv)
    checked += 1
    problems += check("combined RAP federal + private", 190_000.0, combined)

    # The payment-override path (the repayment tool's "what you actually pay"
    # input) closes its books with a per-month capped final payment -- a
    # SEPARATE code path from the term-derived close-out, so it gets its own
    # cases. The below-required override must be ignored (identical books to
    # the plain call), and the zero-rate case exercises the loop with no
    # interest to hide behind.
    for principal, rate, override in ((68_900.0, 6.0, 1_600.0),
                                      (68_900.0, 6.0, 10_000.0),
                                      (50_000.0, 9.07, 500.0),
                                      (5_000.0, 0.0, 300.0)):
        result = ns["calculate_standard_repayment"](
            principal, rate, 9, monthly_payment_override=override)
        checked += 1
        problems += check(
            f"Standard override ${override:,.0f}/mo on ${principal:,.0f} @ {rate}%",
            principal, result, rate)

    # The strategy simulator's extra-payment hook: statutory + a stepped-up
    # extra must still balance the books. RAP is the load-bearing case -- a
    # larger payment covers more interest, so the WAIVER shrinks, and the
    # identity is what proves the subsidy-forfeiture arithmetic conserves
    # money rather than double-counting the covered interest.
    extras = ((1, 200.0), (49, 1_600.0))
    for label, principal, rate, result in (
        ("RAP + pivot extras, low earner", 60_000.0, 6.5,
         ns["simulate_rap_schedule"](60_000.0, 6.5, "LowEarner", 0,
                                     extra_payments=extras)),
        ("IDR + pivot extras, low earner", 60_000.0, 6.5,
         ns["calculate_idr_repayment"](60_000.0, 6.5, "LowEarner",
                                       extra_payments=extras)),
        ("RAP + extras, high earner", 190_000.0, 9.07,
         ns["simulate_rap_schedule"](190_000.0, 9.07, "HighEarner", 0,
                                     extra_payments=((13, 900.0),))),
    ):
        checked += 1
        problems += check(label, principal, result, rate)

    # The old-IBR variant: same simulator, two constants swapped -- so the
    # books must balance at 15%/25y, and the month-one payment must be
    # EXACTLY 1.5x the 10% variant's at the same income. The ratio check is
    # the one that matters: a toggle that changes the label but not the rate
    # balances its own books perfectly.
    old_ibr_result = ns["calculate_idr_repayment"](
        290_000.0, 6.5, "HighEarner",
        payment_rate=0.15, max_term_years=25)
    checked += 1
    problems += check("old IBR (15%/25y), high earner", 290_000.0,
                      old_ibr_result, 6.5)
    # The ratio check goes through compare_existing_loan_plans, NOT the
    # simulator directly: the first break tried was a row that passed
    # payment_rate=IDR_PAYMENT_RATE under an "old IBR" label -- the simulator
    # was fine, the WIRING was broken, and a simulator-level check sailed
    # straight past it.
    def _ibr_first(old: bool) -> float:
        rows_ = ns["compare_existing_loan_plans"](290_000.0, 6.5, 175_000.0,
                                                  old_ibr=old)
        r = next(r for label, r, _ in rows_ if label.startswith("IBR"))
        return float(r["schedule"]["payment"].iloc[0])
    old_first, new_first = _ibr_first(True), _ibr_first(False)
    checked += 1
    if abs(old_first - 1.5 * new_first) > 0.01:
        problems.append(
            f"  old-IBR rate not applied to the ROW: month-1 payment "
            f"${old_first:,.2f} is not 1.5x the 10%% variant's "
            f"${new_first:,.2f}. The label may say old IBR while the rate "
            f"stayed 10%%.")

    # Conservation cannot see a MIS-SIZED extra: applying double the extra
    # still balances its own books, since a larger payment is a larger
    # payment. So check the behaviour directly -- month one's payment must be
    # the statutory RAP payment plus EXACTLY the extra.
    rap_extra = ns["simulate_rap_schedule"](60_000.0, 6.5, "LowEarner", 0,
                                            extra_payments=((1, 200.0),))
    statutory = ns["calculate_rap_payment"](32_000.0)["monthly_payment"]
    got = float(rap_extra["schedule"]["payment"].iloc[0])
    checked += 1
    if abs(got - (statutory + 200.0)) > 0.01:
        problems.append(
            f"  RAP extra_payments mis-applied: month-1 payment ${got:,.2f} is "
            f"not the statutory ${statutory:,.2f} plus exactly the $200.00 extra.")

    # The fixed-plan avalanche: several notes, extra targeted highest-rate
    # first with retired notes' payments rolling forward. Three distinct
    # failure shapes, three distinct checks:
    #  - conservation (payments == principals + interest to the cent);
    #  - equivalence: on ONE note the avalanche must reproduce the plain
    #    override path, or the two simulators disagree about the same loan;
    #  - TARGETING: with distinct rates and an extra, the highest-rate note
    #    must die first. Conservation cannot see a reversed sort key --
    #    paying the wrong note first still balances the books perfectly.
    ava = ns["simulate_fixed_avalanche"](
        [{"balance": 46_300.0, "rate": 6.05}, {"balance": 22_600.0, "rate": 3.4},
         {"balance": 8_000.0, "rate": 9.5}],
        10, extra_payments=((1, 300.0), (25, 1_200.0)))
    checked += 1
    problems += check("fixed avalanche, 3 notes + stepped extras",
                      76_900.0, ava, 6.0)
    payoffs = ava["per_loan_payoff_months"]
    checked += 1
    if not (payoffs[2] < payoffs[0] < payoffs[1]):
        problems.append(
            f"  avalanche targeting broken: payoff months {payoffs} for rates "
            f"(6.05, 3.4, 9.5) -- the highest rate must die first and the "
            f"lowest last.")
    # The budget must never shrink: a retired note's payment ROLLS FORWARD
    # into the remaining notes. Conservation cannot see this one either --
    # paying less each month still balances its own books, it just takes
    # longer -- so check the payment column directly: every month except the
    # final partial one pays that month's full budget.
    base_budget = ava["monthly_payment"] - 300.0   # month-1 budget minus its extra
    sched = ava["schedule"]
    checked += 1
    for _, row_ in sched.iloc[:-1].iterrows():
        expected = base_budget + (300.0 if row_["month"] >= 1 else 0.0) \
                   + (1_200.0 if row_["month"] >= 25 else 0.0)
        if abs(row_["payment"] - expected) > 0.01:
            problems.append(
                f"  avalanche budget shrank: month {int(row_['month'])} paid "
                f"${row_['payment']:,.2f} against a ${expected:,.2f} budget -- "
                f"a retired note's payment must roll forward, not vanish.")
            break
    single_ava = ns["simulate_fixed_avalanche"](
        [{"balance": 68_900.0, "rate": 6.0}], 9, extra_payments=((1, 773.0),))
    single_override = ns["calculate_standard_repayment"](
        68_900.0, 6.0, 9,
        monthly_payment_override=single_ava["monthly_payment"])
    checked += 1
    if (abs(single_ava["total_interest"] - single_override["total_interest"]) > 2.0
            or abs(single_ava["payoff_years"] - single_override["payoff_years"]) > 0.1):
        problems.append(
            f"  single-note avalanche disagrees with the override path: "
            f"interest {single_ava['total_interest']:,.2f} vs "
            f"{single_override['total_interest']:,.2f}, payoff "
            f"{single_ava['payoff_years']:.2f} vs "
            f"{single_override['payoff_years']:.2f} -- same loan, same money, "
            f"two answers.")

    # STACKING THE PRIVATE NOTES. The chart used to draw their sum as one
    # line, on a comment that said "the private row itself is already just one
    # loan" -- true before the loan grids shipped and false after. A reader
    # reported it. What makes the stack checkable rather than a matter of
    # taste is that it must ADD UP: a band set that does not sum to the line
    # it replaced is a money bug wearing a chart's clothes.
    stack_fn = ns["private_loan_stack"]
    frame_fn = ns["tranche_balance_frame"]
    priv = [{"balance": 10_300.0, "rate": 7.33, "term": 5, "actual": 0},
            {"balance": 3_000.0, "rate": 9.88, "term": 5, "actual": 0},
            {"balance": 4_000.0, "rate": 9.72, "term": 5, "actual": 0},
            {"balance": 7_300.0, "rate": 6.96, "term": 5, "actual": 0}]
    rows_p = ns["compare_existing_loan_plans"](
        24_000.0, 3.5, 60_000.0, 0, True,
        federal_loans=[{"balance": 24_000.0, "rate": 3.5}], private_loans=priv)
    prow = next(r for label, r, _ in rows_p if label == ns["PRIVATE_ROW_LABEL"])
    bands, band_labels = stack_fn(prow)
    checked += 1
    if bands is None or len(bands) != 4:
        problems.append(
            f"  four private notes produced {bands and len(bands)} bands; the "
            f"chart falls back to one combined line and the reader cannot see "
            f"which loan clears when.")
    else:
        # 1. THE BANDS ADD UP. This is the one that matters: it ties the
        #    picture to the row above it.
        frame = frame_fn(bands, band_labels)
        # An EMPTY frame is the shape of the original bug: tranche_balance_frame
        # accepted exactly two series and returned nothing for four, so the
        # chart quietly fell back to one line. Say that, rather than crashing
        # on a missing column two lines later.
        if frame.empty:
            problems.append(
                f"  tranche_balance_frame returned nothing for {len(bands)} "
                f"bands, so the stack silently falls back to a single combined "
                f"line -- which is the defect this check exists for.")
            frame = None
        summed = (frame.groupby("year")["amount"].sum()
                  if frame is not None else None)
        line = prow["schedule"].groupby("year")["balance"].last()
        shared = (summed.index.intersection(line.index)
                  if summed is not None else [])
        gap = (float((summed.loc[shared] - line.loc[shared]).abs().max())
               if len(shared) else 0.0)
        checked += 1
        if gap > TOLERANCE:
            problems.append(
                f"  the stacked private bands miss their own combined line by "
                f"${gap:,.2f}. The bands and the row are the same money.")
        # 2. HIGHEST RATE AT THE BOTTOM, which is the order the roll-down
        #    attacks them in, so the first band to vanish is the one the page
        #    tells the reader to target.
        rates = [float(b["rate"]) for b in bands]
        checked += 1
        if rates != sorted(rates, reverse=True):
            problems.append(
                f"  private bands are ordered {rates}, not highest-rate-first. "
                f"The band that disappears first must be the one being "
                f"targeted, or the picture argues against the advice.")
        # 3. Distinct labels AND distinct colours. Two bands sharing a label
        #    are summed into one by the plotting layer, silently drawing three
        #    loans where there are four.
        checked += 1
        if len(set(band_labels)) != len(band_labels):
            problems.append(f"  private band labels collide: {band_labels}")
        used = list(ns["PRIVATE_STACK_COLORS"])[:len(bands)]
        checked += 1
        if len(set(used)) != len(used):
            problems.append(
                f"  two private bands would be drawn in the same colour: {used}")

    # THE MARKER, which is where the roll-down payoff becomes visible. Until
    # it existed the figure lived only in a sentence, under a chart whose bands
    # ran to the LATER date -- reported as "I don't see the earlier payoff",
    # which was exactly right. It must agree with the sentence to the tenth of
    # a year, since the two sit inches apart.
    marker_fn = ns["private_payoff_marker"]
    rows_x = ns["compare_existing_loan_plans"](
        24_000.0, 3.5, 60_000.0, 0, True,
        federal_loans=[{"balance": 24_000.0, "rate": 3.5}], private_loans=priv,
        private_extra=130.0)
    xrow = next(r for label, r, _ in rows_x if label == ns["PRIVATE_ROW_LABEL"])
    mark = marker_fn(xrow)
    checked += 1
    if not mark:
        problems.append(
            "  a $130 private extra produced no payoff marker, so the earlier "
            "payoff is claimed in prose above a chart that does not show it")
    else:
        checked += 1
        if abs(mark[0] - xrow["avalanche"]["payoff_years"]) > 1e-9:
            problems.append(
                f"  the chart marker sits at {mark[0]:.2f}y while the sentence "
                f"says {xrow['avalanche']['payoff_years']:.2f}y")
        checked += 1
        if mark[0] >= xrow["payoff_years"]:
            problems.append(
                f"  the marker at {mark[0]:.2f}y is not EARLIER than the drawn "
                f"bands' {xrow['payoff_years']:.2f}y, so it marks nothing")
    # THE SECOND CHART, which is where the roll-down became visible rather
    # than merely stated. It is the same loans drawn a second way, so the two
    # only work as a pair if a loan keeps its identity between them.
    roll_fn = ns["private_rolldown_stack"]
    rbands, rlabels = roll_fn(xrow)
    qbands, qlabels = stack_fn(xrow)
    checked += 1
    if not rbands:
        problems.append(
            "  no roll-down chart for a portfolio with an extra; the earlier "
            "payoff is claimed in prose with no picture behind it")
    else:
        checked += 1
        if rlabels != qlabels:
            problems.append(
                f"  the two charts label their bands differently: "
                f"{qlabels} vs {rlabels}. Same colours in a different order "
                f"makes the pair unreadable, which is the only reason the "
                f"second chart exists.")
        # The roll-down bands must sum to the roll-down's OWN combined curve,
        # exactly as the required bands sum to theirs.
        rframe = frame_fn(rbands, rlabels)
        rsum = rframe.groupby("year")["amount"].sum()
        rline = xrow["avalanche"]["schedule"].groupby("year")["balance"].last()
        rshared = rsum.index.intersection(rline.index)
        rgap = float((rsum.loc[rshared] - rline.loc[rshared]).abs().max())
        checked += 1
        if rgap > TOLERANCE:
            problems.append(
                f"  the roll-down bands miss their own combined curve by "
                f"${rgap:,.2f}")
        # And every band must clear no later than its required twin -- the
        # roll-down can only bring a payoff forward.
        checked += 1
        for lab, q, r in zip(rlabels, qbands, rbands):
            qe = q["schedule"].loc[q["schedule"]["balance"] > 0.005, "year"].max()
            re_ = r["schedule"].loc[r["schedule"]["balance"] > 0.005, "year"].max()
            if re_ > qe + 1e-9:
                problems.append(
                    f"  {lab} clears LATER with the roll-down ({re_:.2f}y vs "
                    f"{qe:.2f}y). Moving a cleared loan's payment onto the "
                    f"next one cannot delay anything.")
                break

    # THE COMMIT ARM MUST BE DRAWABLE, not only describable. The panel priced
    # two arms and the charts drew one, so a combined row said "the private
    # side clears in year 3.8" and "debt-free on the federal side in 5.0
    # years" under bands ending at 4.9 and 6.9.
    commit_fn = ns["commit_arm_stack"]
    pivot_rows = ns["compare_existing_loan_plans"](
        24_000.0, 3.5, 60_000.0, 0, True,
        federal_loans=[{"balance": 24_000.0, "rate": 3.5}],
        private_loans=priv, private_extra=130.0)
    prow2 = next(r for label, r, _ in pivot_rows
                 if label == ns["PRIVATE_ROW_LABEL"])
    an2 = ns["pivot_strategy_analysis"](
        pivot_rows, [{"balance": 24_000.0, "rate": 3.5}], 60_000.0, 0,
        prefer_label=ns["RAP_STRATEGY_LABEL"])
    cbands, clabels, caxis = commit_fn(an2, prow2)
    checked += 1
    if not cbands or len(cbands) != 2:
        problems.append(
            "  the commit arm has no drawable stack, so the panel's second arm "
            "stays prose under a picture of the first")
    else:
        fed_band, priv_band = cbands
        fed_end = float(fed_band["schedule"].loc[
            fed_band["schedule"]["balance"] > 0.005, "year"].max())
        priv_end = float(priv_band["schedule"].loc[
            priv_band["schedule"]["balance"] > 0.005, "year"].max())
        checked += 1
        if abs(priv_end - prow2["avalanche"]["payoff_years"]) > 0.09:
            problems.append(
                f"  the commit arm's private band ends at {priv_end:.2f}y, not "
                f"the roll-down's {prow2['avalanche']['payoff_years']:.2f}y. "
                f"The panel frees the private payment on the roll-down's clock, "
                f"so the picture has to use the same one.")
        checked += 1
        if abs(fed_end - an2["strategy"]["years"]) > 0.09:
            problems.append(
                f"  the commit arm's federal band ends at {fed_end:.2f}y while "
                f"the panel says {an2['strategy']['years']:.2f}y")
        checked += 1
        if fed_end >= an2["ride"]["years"]:
            problems.append(
                f"  committing does not finish EARLIER than riding "
                f"({fed_end:.2f}y against {an2['ride']['years']:.2f}y), so the "
                f"chart argues against the panel it illustrates")
        # THE AXIS FRAME IS THE SUM. Handing a stack one band's curve scales
        # the chart to a fraction of its own height -- a $24,600 stack drawn
        # against a y-axis stopping at $10k, which is how this shipped once.
        checked += 1
        # The STACKED height, not the tallest single band -- an earlier
        # version of this check compared against the latter and passed on an
        # axis built from one band, which is the exact defect it exists for.
        top = float(caxis["balance"].max())
        stacked = None
        for band in cbands:
            curve = band["schedule"][["year", "balance"]].groupby(
                "year", as_index=False).last().set_index("year")["balance"]
            stacked = curve if stacked is None else stacked.add(curve,
                                                                fill_value=0.0)
        peak = float(stacked.max())
        if top < peak - 0.5:
            problems.append(
                f"  the commit chart's axis frame peaks at ${top:,.0f} against "
                f"a stacked height of ${peak:,.0f}. It must be the SUM of the "
                f"bands, or the y-axis is scaled to part of its own picture.")
        # THE PAYMENT VIEW MUST SHOW THE REDIRECT. The federal band steps up
        # the month the private loans clear, by exactly the payment they were
        # taking. That step IS the strategy; without it the two arms differ
        # only in where their lines end and the reader has to take the
        # caption's word for why.
        fed_pay = fed_band["schedule"]
        checked += 1
        if "payment" not in fed_pay.columns:
            problems.append(
                "  the commit arm's federal schedule has no payment column, so "
                "its chart cannot show the freed payment arriving")
        else:
            pm = int(an2["pivot_month"])
            before = fed_pay.loc[fed_pay["month"] <= pm, "payment"]
            after = fed_pay.loc[fed_pay["month"] > pm + 1, "payment"]
            checked += 1
            if not len(before) or not len(after):
                problems.append(
                    f"  the commit arm has no months either side of the pivot "
                    f"at {pm}, so there is no step to draw")
            else:
                step = float(after.iloc[0]) - float(before.iloc[-1])
                if abs(step - float(an2["freed"])) > 1.0:
                    problems.append(
                        f"  the federal payment steps up ${step:,.2f} at the "
                        f"pivot, not the ${float(an2['freed']):,.2f} the panel "
                        f"says is freed. The step is the strategy; if it is the "
                        f"wrong size the picture and the prose disagree.")

    # Nothing to draw when committing saves nothing.
    checked += 1
    if commit_fn({"savings": 0.0, "strategy": {}, "ride": {}}, prow2)[0] is not None:
        problems.append(
            "  a commit chart was offered where committing saves nothing; it "
            "would draw a second picture identical to the first")

    # THE PANEL SAYS WHAT ITS OWN HEADING MEANS. "Commit or ride" named the
    # fork and never defined it, and a reader looking straight at the panel
    # asked what it meant. One string, read by the screen and the PDF, so the
    # two cannot come to explain the same fork differently.
    import ast as _ast
    _src = open("app.py").read()
    _tree = _ast.parse(_src)
    explainer = ns.get("COMMIT_OR_RIDE_EXPLAINER") or ""
    checked += 1
    for word in ("Ride", "Commit", "forgives"):
        if word not in explainer:
            problems.append(
                f"  COMMIT_OR_RIDE_EXPLAINER no longer says {word!r}. It has to "
                f"define BOTH arms and carve out the plans that forgive, or it "
                f"asserts a discharge the fixed rows never reach.")
    for func, what in (("render_existing_loan_comparison", "the panel"),
                       ("generate_pdf_repayment_report", "the PDF")):
        node = next((n for n in _ast.walk(_tree)
                     if isinstance(n, _ast.FunctionDef) and n.name == func), None)
        checked += 1
        if node is None or not any(
                isinstance(x, _ast.Name) and x.id == "COMMIT_OR_RIDE_EXPLAINER"
                for x in _ast.walk(node)):
            problems.append(
                f"  {what} does not read COMMIT_OR_RIDE_EXPLAINER, so the fork "
                f"is named there and never explained.")

    # WHERE AN EXTRA DOLLAR SHOULD GO, which is not a rate comparison. The
    # caption used to fire only when the top private rate beat the top federal
    # one, so it stayed SILENT on the case where the answer is least obvious:
    # a federal loan at a HIGHER rate that is headed for forgiveness, where
    # prepaying loses by $84,314 on this model. Rate ordering is valid only
    # among loans that will actually be repaid in full.
    target_fn = ns["extra_payment_target"]
    hi_fed = [{"balance": 90_000.0, "rate": 8.0}]
    lo_priv = [{"balance": 20_000.0, "rate": 7.0, "term": 10, "actual": 0}]
    forgiving = ns["compare_existing_loan_plans"](
        90_000.0, 8.0, 38_000.0, 0, True, federal_loans=hi_fed,
        private_loans=lo_priv)
    rap_row = next(r for label, r, _ in forgiving
                   if label == ns["RAP_STRATEGY_LABEL"])
    std_row = next(r for label, r, _ in forgiving
                   if label.startswith("Standard"))
    checked += 1
    if float(rap_row.get("forgiven_amount") or 0) <= 0:
        problems.append(
            "  the RAP fixture stopped forgiving anything, so it no longer "
            "demonstrates the case this rule exists for")
    got = target_fn(rap_row, hi_fed, lo_priv)
    checked += 1
    if got != ("private", "forgiveness"):
        problems.append(
            f"  a federal loan at 8% headed for forgiveness, beside a 7% "
            f"private loan, sends extra money {got}. Prepaying a balance that "
            f"will be discharged shrinks the discharge, not the cost -- the "
            f"panel prices that gap at about $84,000.")
    # A plan that forgives NOTHING is plain amortisation on both sides, and
    # then the higher rate really does win. Same loans, same rates.
    got = target_fn(std_row, hi_fed, lo_priv)
    checked += 1
    if got != ("federal", "rate"):
        problems.append(
            f"  on a fixed plan that forgives nothing, an 8% federal loan "
            f"beside a 7% private one sends extra money {got}, not to the "
            f"higher rate. Rate ordering IS right where nothing is discharged.")
    # PSLF forgives tax-free at 120 payments, so it decides even before any
    # forgiven_amount shows up on the row.
    checked += 1
    if target_fn(std_row, hi_fed, lo_priv, pslf=True) != ("private", "forgiveness"):
        problems.append(
            "  PSLF did not send extra money to the private side. A tax-free "
            "discharge at 120 payments is the strongest case there is for not "
            "prepaying the federal balance.")
    # And the ordinary case still works.
    checked += 1
    hi_priv = [{"balance": 20_000.0, "rate": 9.5, "term": 10, "actual": 0}]
    if target_fn(std_row, hi_fed, hi_priv) != ("private", "rate"):
        problems.append(
            "  a 9.5% private loan beside an 8% federal one on a fixed plan "
            "no longer targets private; the plain rate rule has broken.")

    # A FLAT STACKED PAYMENT CHART SAYS NOTHING THE TABLE HAS NOT. On the
    # private row with equal terms and no override every band is constant and
    # they all end together, so the picture's only content is the split, which
    # the balance chart shows as area and the table states as one figure. It
    # earns its place when the TOTAL steps, because a step is what a payment
    # chart can show and a balance chart cannot.
    informative = ns["payment_stack_is_informative"]
    equal_priv = [{"balance": b, "rate": r, "term": 5, "actual": 0}
                  for b, r in ((10_300, 7.33), (3_000, 9.88),
                               (4_000, 9.72), (7_300, 6.96))]
    mixed_priv = [{"balance": b, "rate": r, "term": t, "actual": 0}
                  for b, r, t in ((10_300, 7.33, 10), (3_000, 9.88, 5),
                                  (7_300, 6.96, 15))]
    over_priv = [{"balance": 10_300.0, "rate": 7.33, "term": 5, "actual": 400},
                 {"balance": 3_000.0, "rate": 9.88, "term": 5, "actual": 0}]

    def _priv_bands(loans):
        rws = ns["compare_existing_loan_plans"](
            24_000.0, 3.5, 60_000.0, 0, True,
            federal_loans=[{"balance": 24_000.0, "rate": 3.5}],
            private_loans=loans)
        prw = next(r for label, r, _ in rws if label == ns["PRIVATE_ROW_LABEL"])
        return ns["private_loan_stack"](prw), rws, prw

    (bands_e, labels_e), rows_e, _ = _priv_bands(equal_priv)
    checked += 1
    if informative(bands_e, labels_e):
        problems.append(
            "  four private notes on equal terms with no override draw a "
            "payment chart of four flat bands ending together, and it is "
            "being kept. That is a screen spent repeating the table.")
    (bands_m, labels_m), _, _ = _priv_bands(mixed_priv)
    checked += 1
    if not informative(bands_m, labels_m):
        problems.append(
            "  private notes on MIXED terms were called uninformative, but "
            "their total steps down as each clears -- which is the one thing "
            "this chart exists to show.")
    (bands_o, labels_o), _, _ = _priv_bands(over_priv)
    checked += 1
    if not informative(bands_o, labels_o):
        problems.append(
            "  a per-note Actual $/mo override was called uninformative; it "
            "makes one band end before the others, so the total steps.")
    # A COMBINED row keeps its chart: the federal and private parts end at
    # different times, and under an income-driven plan the federal part also
    # rises with income.
    std_e = next(r for label, r, _ in rows_e if label.startswith("Standard"))
    cb, cl, _cc, _cby = ns["repayment_balance_stack"]("Standard (10-year)",
                                                      std_e, rows_e)
    checked += 1
    if not informative(cb, cl):
        problems.append(
            "  a combined row's federal-and-private payment chart was called "
            "uninformative, but the two parts clear at different times")
    # And an unstacked line is not this rule's business: it keeps its own
    # caption, which exists to be compared against the plans whose payment moves.
    checked += 1
    if not informative(None, ns["TRANCHE_LABELS"]):
        problems.append(
            "  the rule started suppressing UNSTACKED payment lines, which "
            "have their own justification and their own caption")

    # WHETHER SPARE MONEY WOULD HELP, for a borrower who has entered none.
    # With no private loan to free up and no extra, there is no second arm to
    # price and the panel rendered a prompt instead of an answer -- yet the
    # answer needs no amount, because it is a fact about the plan.
    worth = ns["extra_payment_worth_it"]
    checked += 1
    fixed_says = worth(std_row)
    if "would help" not in fixed_says or "not help" in fixed_says:
        problems.append(
            f"  on a plan that forgives nothing, prepaying is said to be "
            f"{fixed_says[:60]!r}. Every extra dollar shortens a fixed plan.")
    checked += 1
    forgiving_says = worth(rap_row)
    if "not help" not in forgiving_says:
        problems.append(
            f"  on a plan whose remainder is written off, prepaying is said to "
            f"be {forgiving_says[:60]!r}. A dollar paid early there shrinks the "
            f"discharge, not the cost.")
    checked += 1
    if "not help" not in worth(std_row, pslf=True):
        problems.append(
            "  under PSLF prepaying is not called out as unhelpful, and a "
            "tax-free discharge is the strongest case there is against it")
    # IT MUST AGREE WITH THE CAPTION BESIDE IT. Both answer "does prepaying
    # this balance do anything" and they sit on one screen; one rule, so they
    # cannot come to disagree.
    for row_, label_ in ((rap_row, "a forgiving plan"), (std_row, "a fixed plan")):
        checked += 1
        says_no = "not help" in worth(row_)
        targets_private = target_fn(row_, hi_fed, lo_priv) == ("private",
                                                               "forgiveness")
        if says_no != targets_private:
            problems.append(
                f"  on {label_} the panel and the caption disagree about "
                f"whether federal prepayment is worth it "
                f"({says_no} against {targets_private})")

    # THE PAYMENT VIEW OF THE ROLL-DOWN, whose whole claim is that the budget
    # NEVER SHRINKS: a cleared loan's share moves onto the next one instead of
    # going back into the borrower's pocket. That is checkable directly -- the
    # bands must sum to the same figure every month until the last note dies.
    # Conservation cannot see this (paying less each month still balances its
    # own books, it just takes longer), which is the same reason the federal
    # avalanche has a budget check of its own.
    if rbands:
        pay_frame = ns["tranche_payment_frame"](rbands, rlabels)
        per_month = pay_frame.groupby("year")["amount"].sum()
        budget = float(xrow["avalanche"]["monthly_payment"])
        # Every month but the last, which is a partial payoff.
        steady = per_month.iloc[:-1]
        checked += 1
        if len(steady) and float((steady - budget).abs().max()) > 0.51:
            worst = float((steady - budget).abs().idxmax())
            problems.append(
                f"  the roll-down payment stack does not hold its budget: at "
                f"year {worst:.2f} the bands sum to "
                f"${float(per_month.loc[worst]):,.2f} against ${budget:,.2f}. "
                f"A cleared loan's payment must roll onto the next, not vanish.")
        # And it must actually MOVE: at least one band has to grow after
        # another dies, or the chart is four flat lines and shows nothing.
        checked += 1
        wide = pay_frame.pivot(index="year", columns="component", values="amount")
        grew = any(float(wide[name].max()) - float(wide[name].iloc[0]) > 1.0
                   for name in wide.columns)
        if not grew:
            problems.append(
                "  no band's payment ever rises, so nothing rolls onto "
                "anything and the payment chart is telling the reader the "
                "opposite of what the caption claims.")

    # And no marker when the roll-down saves no time: a rule sitting on the
    # curve's own end date is noise.
    checked += 1
    if marker_fn(prow) is not None:
        problems.append(
            "  a marker was drawn with no months saved; it would sit on the "
            "bands' own end date and label it as a finding")

    # The boundaries, both of them, because each is a different answer.
    checked += 1
    if stack_fn({"per_loan": [dict(priv[0], schedule=prow["schedule"])]})[0] is not None:
        problems.append("  a single private loan produced a stack; a stack of "
                        "one is a line with extra steps")
    checked += 1
    many = ns["compare_existing_loan_plans"](
        24_000.0, 3.5, 60_000.0, 0, True,
        federal_loans=[{"balance": 24_000.0, "rate": 3.5}],
        private_loans=priv + [{"balance": 2_500.0, "rate": 8.5, "term": 5,
                               "actual": 0}])
    mrow = next(r for label, r, _ in many if label == ns["PRIVATE_ROW_LABEL"])
    if stack_fn(mrow)[0] is not None:
        problems.append(
            f"  {ns['MAX_STACKED_PRIVATE_LOANS'] + 1} private loans produced a "
            f"stack, but only {ns['MAX_STACKED_PRIVATE_LOANS']} validated hues "
            f"exist -- two loans would share a colour.")

    # WHICH SPLIT EACH ROW GETS. The private row splits per loan; every
    # COMBINED row splits federal against private, which is the split this repo
    # already calls the one a reader asks about and which the balance chart in
    # this tool simply never used. Never per FEDERAL loan: an income-driven
    # plan pools them by law, so those rows have no per-loan schedules and
    # splitting only the fixed ones would look like a rendering fault.
    stack_for = ns["repayment_balance_stack"]
    for label, res, _ in rows_p:
        bands, blabels, _c, by = stack_for(label, res, rows_p)
        checked += 1
        if label == ns["PRIVATE_ROW_LABEL"]:
            if by != "loan" or not bands or len(bands) != 4:
                problems.append(
                    f"  the private row got {bands and len(bands)} band(s) "
                    f"by {by!r}; four notes must split per loan")
        else:
            if by != "loan type" or not bands or len(bands) != 2:
                problems.append(
                    f"  {label!r} got {bands and len(bands)} band(s) by {by!r}; "
                    f"a combined row splits federal against private")
            elif tuple(blabels) != tuple(ns["REPAYMENT_STACK_LABELS"]):
                problems.append(
                    f"  {label!r} labelled its bands {blabels}, not the "
                    f"tool's own REPAYMENT_STACK_LABELS")
            else:
                # And they add up, exactly as the per-loan bands must.
                f2 = frame_fn(bands, blabels)
                s2 = f2.groupby("year")["amount"].sum()
                l2 = res["schedule"].groupby("year")["balance"].last()
                sh2 = s2.index.intersection(l2.index)
                g2 = float((s2.loc[sh2] - l2.loc[sh2]).abs().max()) if len(sh2) else 0.0
                if g2 > TOLERANCE:
                    problems.append(
                        f"  {label!r}'s federal and private bands miss their "
                        f"own combined line by ${g2:,.2f}")

    # THE PIVOT MUST PRICE THE ROLL-DOWN IT RECOMMENDS. Before this the panel
    # said "once the private side clears in year 5.0, redirect its $498/mo" on
    # a screen that also said those loans clear in 3.8 years. A borrower paying
    # the extra frees a bigger payment sooner, so ignoring it understated the
    # pivot arm AND contradicted the sentence beneath it.
    pivot = ns["pivot_strategy_analysis"](
        rows_x, [{"balance": 24_000.0, "rate": 3.5}], 60_000.0, 0,
        prefer_label=ns["RAP_STRATEGY_LABEL"])
    checked += 1
    if pivot is None:
        problems.append("  no pivot analysis for a portfolio with both sides")
    else:
        av = xrow["avalanche"]
        # BOTH HALVES: when the freed payment arrives, and how big it is. The
        # key is pivot_month, not pivot_years -- an earlier draft of this check
        # read a key that does not exist and fell back to the expected value,
        # which is an assertion that cannot fail.
        want_month = int(round(av["payoff_years"] * 12))
        checked += 1
        if abs(int(pivot["pivot_month"]) - want_month) > 1:
            problems.append(
                f"  the pivot frees the private payment at month "
                f"{pivot['pivot_month']}, but the roll-down clears those loans "
                f"at month {want_month}. The panel would say year "
                f"{pivot['pivot_month'] / 12:.1f} on a screen that also says "
                f"{av['payoff_years']:.1f}.")
        checked += 1
        if abs(float(pivot["freed"]) - av["monthly_payment"]) > 0.51:
            problems.append(
                f"  the pivot frees ${pivot.get('freed', 0):,.2f}/mo where the "
                f"roll-down hands over ${av['monthly_payment']:,.2f}. The panel "
                f"must free the payment the borrower is actually making.")

    # PER-NOTE TERMS, which is what the private side needs: a federal plan sets
    # one term for the whole portfolio, while private notes each carry their
    # own and a borrower can hold a 5-year note beside a 15-year one.
    av = ns["simulate_fixed_avalanche"]
    PRIVATE_TERM_YEARS = ns["PRIVATE_TERM_YEARS"]
    mixed = [{"balance": 10_300.0, "rate": 7.33, "term": 10, "actual": 0},
             {"balance": 3_000.0, "rate": 9.88, "term": 5, "actual": 0},
             {"balance": 4_000.0, "rate": 9.72, "term": 7, "actual": 0},
             {"balance": 7_300.0, "rate": 6.96, "term": 15, "actual": 0}]
    for extra in (0.0, 130.0):
        r = av(mixed, PRIVATE_TERM_YEARS, per_loan_terms=True,
               extra_payments=((1, extra),) if extra else ())
        checked += 1
        problems += check(f"private avalanche, mixed terms, ${extra:.0f} extra",
                          sum(l["balance"] for l in mixed), r, 7.5)

    # THE FEDERAL PATH IS PROTECTED BY AN EQUIVALENCE, not by hoping. With the
    # flag OFF a `term` on a loan must be ignored entirely, and with every note
    # on the SAME term the two modes must agree to the cent -- which is the
    # generalisation asserting it did not change the arithmetic it grew out of.
    equal = [{"balance": 46_300.0, "rate": 6.05, "term": 10, "actual": 0},
             {"balance": 22_600.0, "rate": 3.4, "term": 10, "actual": 0},
             {"balance": 8_000.0, "rate": 9.5, "term": 10, "actual": 0}]
    off = av(equal, 10, extra_payments=((1, 300.0),))
    on = av(equal, 10, extra_payments=((1, 300.0),), per_loan_terms=True)
    checked += 1
    if (abs(off["total_interest"] - on["total_interest"]) > 0.01
            or abs(off["payoff_years"] - on["payoff_years"]) > 1e-9):
        problems.append(
            f"  per_loan_terms changed the answer on EQUAL terms: interest "
            f"{off['total_interest']:,.2f} vs {on['total_interest']:,.2f}. "
            f"With one term for every note the two modes are the same "
            f"calculation, and the federal path depends on that.")
    misleading = [{"balance": 46_300.0, "rate": 6.05, "term": 2},
                  {"balance": 22_600.0, "rate": 3.4, "term": 30}]
    checked += 1
    if av(misleading, 10)["payoff_years"] != av(
            [{"balance": 46_300.0, "rate": 6.05},
             {"balance": 22_600.0, "rate": 3.4}], 10)["payoff_years"]:
        problems.append(
            "  a `term` on a loan changed the result with per_loan_terms OFF. "
            "The federal grid never sets one, but the flag is what keeps that "
            "true rather than an accident of the caller.")

    # TARGETING, AND IT CANNOT BE THE FEDERAL ASSERTION. With one term the
    # highest-rate note dies first; with mixed terms that is FALSE, because a
    # short low-rate note legitimately clears before a long high-rate one. The
    # honest question is where the EXTRA went, so compare payoff months against
    # the same run without it and ask which note moved.
    trap = [{"balance": 5_000.0, "rate": 4.0, "term": 3},
            {"balance": 20_000.0, "rate": 10.0, "term": 10}]
    base = av(trap, PRIVATE_TERM_YEARS, per_loan_terms=True)
    with_extra = av(trap, PRIVATE_TERM_YEARS, per_loan_terms=True,
                    extra_payments=((1, 130.0),))
    moved = [(b or 0) - (w or 0) for b, w in
             zip(base["per_loan_payoff_months"],
                 with_extra["per_loan_payoff_months"])]
    checked += 1
    if moved[1] <= 0 or moved[0] != 0:
        problems.append(
            f"  the extra did not go to the highest rate: payoff months moved "
            f"{moved} for rates (4.0, 10.0). The 10% note must move and the 4% "
            f"note must not.")
    # AND PIN THE REASON, so nobody restores the federal form of this check:
    # on this fixture the LOWEST-rate note really does clear first, because its
    # term is three years. A guard asserting highest-rate-first would fail on
    # correct code.
    checked += 1
    if not base["per_loan_payoff_months"][0] < base["per_loan_payoff_months"][1]:
        problems.append(
            "  the 3-year 4% note no longer clears before the 10-year 10% one, "
            "so this fixture has stopped demonstrating why the federal "
            "highest-rate-first assertion must not be copied here.")

    # Multi-loan grids: two federal notes on a fixed plan chained with two
    # private loans (one paying an override) -- four schedules combined twice
    # over, which is the repayment tool's whole-bill shape. The books must
    # balance against the SUMMED principals: a chain that dropped or
    # double-counted a tranche is invisible in any single result.
    multi_rows = ns["compare_existing_loan_plans"](
        0, 0, 32_000.0,
        federal_loans=[{"balance": 40_000.0, "rate": 6.5},
                       {"balance": 20_000.0, "rate": 3.0}],
        private_loans=[{"balance": 30_000.0, "rate": 8.0, "term": 10, "actual": 0},
                       {"balance": 15_000.0, "rate": 9.5, "term": 7,
                        "actual": 400.0}])
    _std_multi = next(r for label, r, _ in multi_rows
                      if label.startswith("Standard"))
    checked += 1
    problems += check("multi-loan Standard combined (2 federal + 2 private)",
                      105_000.0, _std_multi)
    # The RAP row separately: its federal side is the POOLED simulation, so a
    # pool that silently drops a note stays internally consistent (the
    # wrong-but-consistent class again) and only the identity against the
    # SUMMED principals can see it. The Standard row cannot stand in -- it is
    # built from the per-loan chain, a different code path.
    _rap_multi = next((r for label, r, _ in multi_rows if "RAP" in label), None)
    if _rap_multi is None:
        problems.append("  multi-loan RAP row missing from compare_existing_loan_plans")
    else:
        checked += 1
        problems += check("multi-loan RAP combined (pooled federal + 2 private)",
                          105_000.0, _rap_multi)

    if problems:
        print(f"repayment invariants: {len(problems)} violation(s) across {checked} cases\n")
        print("\n\n".join(problems))
        return 1
    print(f"repayment invariants OK -- {checked} cases balance to within ${TOLERANCE:.2f}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

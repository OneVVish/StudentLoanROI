#!/usr/bin/env python3
"""Guard: the Student Aid Index tables are the published ones, and 0 is a real answer.

Two failure modes, both silent, and they are not the same shape.

THE TABLES EXPIRE EVERY JANUARY-ISH. ED reissues the whole Federal Need
Analysis Methodology each spring for the next award year. A stale table does
not raise, does not look wrong and does not fail any other check in this repo:
it quietly reports a different family's number, off by roughly a year of
inflation, for every household that answered the question. This is the
POVERTY_GUIDELINES_2026 problem, arriving on a second set of constants.

So every figure below is a LITERAL, transcribed from the published source, and
never read back off the constant it is checking. A check that derives its
expectation from the code under test asserts only that the code equals itself
-- the flaw this repo already records against an early check_chart_axes and
against the residency guard. Refreshing the tables therefore means editing this
file too, and that is the point: it forces the new figures to be read off the
source twice.

  Source: "2027-28 Student Aid Index (SAI) and Pell Grant Eligibility Guide,
  Version 1", Federal Student Aid, published 2026-06-12. Same tables as
  Federal Register notice 2026-10986 (2026-06-02).

AND AN EXPLICIT ZERO IS A REAL ANSWER. The formula takes two overrides --
income earned from work, and income tax actually paid -- where None means
"derive it" and 0 means "we paid none". A call site that launders the field
through `x or None` collapses those into one, so a family that really paid no
federal income tax is handed an ESTIMATED allowance instead, which is larger,
which lowers their SAI. It errs in the flattering direction and nothing on
screen says so. That is the `entered or default` defect CLAUDE.md records for
professional-school debt, and this catches it in the renderer by AST, because
the renderer lives in section 5 where the exec below cannot reach.

Run it:  python3 check_sai_worksheet.py     (exit 1 on any failure)
"""
import ast
import re
import sys

APP = "app.py"

# ---------------------------------------------------------------------------
# Transcribed from the guide. NEVER read these off app.py.
PUBLISHED_IPA = {2: 30300, 3: 37720, 4: 46590, 5: 54970, 6: 64290}
PUBLISHED_IPA_EXTRA = 7260
PUBLISHED_IPA_SIZE_7 = 71550        # the guide's own worked example, line 6
PUBLISHED_EEA_CAP = 5200            # the guide's own worked example, line 7
PUBLISHED_EEA_RATE = 0.35
PUBLISHED_STUDENT_IPA = 12220       # line 25
PUBLISHED_STUDENT_INCOME_RATE = 0.50
PUBLISHED_STUDENT_ASSET_RATE = 0.20
PUBLISHED_PARENT_ASSET_RATE = 0.12
PUBLISHED_APA = 0                   # Table A4, every age, both columns
PUBLISHED_FLOOR = -1500
PUBLISHED_A5_MIN_AAI = -8900
PUBLISHED_A5_MIN_CONTRIBUTION = -1958
# Table A5: (adjusted available income, contribution at exactly that income)
PUBLISHED_A5_EDGES = ((22600, 4972), (28300, 6397), (34000, 8050),
                      (39900, 10056), (45600, 12336))
# Table A3: (net worth, adjusted net worth at exactly that net worth)
PUBLISHED_A3_EDGES = ((0, 0), (180000, 72000), (540000, 252000), (905000, 471000))
# Table A1
PUBLISHED_HI_RATE, PUBLISHED_HI_RATE_HIGH, PUBLISHED_OASDI_RATE = 0.0145, 0.0235, 0.062
PUBLISHED_PAYROLL = {"mfj": (250000, 352200), "hoh": (200000, 176100)}
PUBLISHED_AWARD_YEAR, PUBLISHED_TAX_YEAR = "2027-28", 2025


def load_app_namespace():
    """Exec app.py's section 1-2 prefix, the analyze_model.py pattern."""
    src = open(APP, encoding="utf-8").read()
    marker = "# 3. PAGE CONFIG & SESSION STATE"
    if marker not in src:
        sys.exit(f"{APP}: section 3 banner is gone; this guard cannot find the "
                 f"end of the pure prefix.")
    cut = src.rindex("\n#", 0, src.index(marker))
    ns = {}
    exec(compile(src[:cut], APP, "exec"), ns)
    return ns, src


def check_published_tables(ns):
    """Every constant against the transcribed literal."""
    out = []
    eq = lambda got, want, what: None if got == want else out.append(
        f"  TABLE   {what}: app.py says {got!r}, the published guide says {want!r}")

    eq(ns["SAI_AWARD_YEAR"], PUBLISHED_AWARD_YEAR, "award year")
    eq(ns["SAI_TAX_YEAR"], PUBLISHED_TAX_YEAR, "tax year")
    # Prior-prior. If a refresh moves one and not the other the two halves of
    # the form describe different years, which is the OEWS vintage mismatch.
    start = int(str(ns["SAI_AWARD_YEAR"]).split("-")[0])
    if start - ns["SAI_TAX_YEAR"] != 2:
        out.append(f"  TABLE   award year {ns['SAI_AWARD_YEAR']} is not two years "
                   f"after tax year {ns['SAI_TAX_YEAR']}; the FAFSA is prior-prior.")

    for size, want in PUBLISHED_IPA.items():
        eq(ns["SAI_INCOME_PROTECTION_ALLOWANCE"].get(size), want, f"IPA family size {size}")
    eq(ns["SAI_IPA_PER_EXTRA_MEMBER"], PUBLISHED_IPA_EXTRA, "IPA per extra member")
    eq(ns["sai_income_protection_allowance"](7), PUBLISHED_IPA_SIZE_7,
       "IPA family size 7 (the guide's worked example)")
    eq(ns["SAI_EEA_CAP"], PUBLISHED_EEA_CAP, "employment expense allowance cap")
    eq(ns["SAI_EEA_RATE"], PUBLISHED_EEA_RATE, "employment expense allowance rate")
    eq(ns["SAI_STUDENT_IPA"], PUBLISHED_STUDENT_IPA, "student income protection allowance")
    eq(ns["SAI_STUDENT_INCOME_RATE"], PUBLISHED_STUDENT_INCOME_RATE, "student income rate")
    eq(ns["SAI_STUDENT_ASSET_RATE"], PUBLISHED_STUDENT_ASSET_RATE, "student asset rate")
    eq(ns["SAI_PARENT_ASSET_RATE"], PUBLISHED_PARENT_ASSET_RATE, "parent asset rate")
    eq(ns["SAI_ASSET_PROTECTION_ALLOWANCE"], PUBLISHED_APA, "asset protection allowance")
    eq(ns["SAI_FLOOR"], PUBLISHED_FLOOR, "SAI floor")
    eq(ns["SAI_A5_MIN_AAI"], PUBLISHED_A5_MIN_AAI, "Table A5 minimum AAI")
    eq(ns["SAI_A5_MIN_CONTRIBUTION"], PUBLISHED_A5_MIN_CONTRIBUTION,
       "Table A5 minimum contribution")
    eq(ns["SAI_HI_RATE"], PUBLISHED_HI_RATE, "Medicare HI rate")
    eq(ns["SAI_HI_RATE_HIGH"], PUBLISHED_HI_RATE_HIGH, "Medicare HI rate, upper")
    eq(ns["SAI_OASDI_RATE"], PUBLISHED_OASDI_RATE, "OASDI rate")
    for status, want in PUBLISHED_PAYROLL.items():
        eq(tuple(ns["SAI_PAYROLL_THRESHOLDS"].get(status, ())), want,
           f"payroll thresholds, {status}")

    for aai, want in PUBLISHED_A5_EDGES:
        eq(round(ns["sai_parents_contribution"](aai)), want,
           f"Table A5 contribution at AAI {aai:,}")
    for net_worth, want in PUBLISHED_A3_EDGES:
        eq(round(ns["sai_business_net_worth_adjustment"](net_worth)), want,
           f"Table A3 adjusted net worth at {net_worth:,}")
    eq(round(ns["sai_parents_contribution"](PUBLISHED_A5_MIN_AAI - 100)),
       PUBLISHED_A5_MIN_CONTRIBUTION, "Table A5 below the minimum")
    return out


def check_structure(ns):
    """Properties the literals cannot state: continuity, direction, the floor."""
    out = []
    sai = ns["compute_student_aid_index"]

    # A published bracket schedule is continuous. A transcription typo in a
    # base or a rate shows up as a step at the boundary, which no single-point
    # literal above can see.
    for aai, _ in PUBLISHED_A5_EDGES:
        below, at = ns["sai_parents_contribution"](aai - 1), ns["sai_parents_contribution"](aai)
        if abs(at - below) > 1.0:
            out.append(f"  SHAPE   Table A5 jumps ${at - below:,.0f} at AAI {aai:,}; "
                       f"the published schedule is continuous.")
    for net_worth, _ in PUBLISHED_A3_EDGES[1:]:
        below = ns["sai_business_net_worth_adjustment"](net_worth - 1)
        at = ns["sai_business_net_worth_adjustment"](net_worth)
        if abs(at - below) > 1.0:
            out.append(f"  SHAPE   Table A3 jumps ${at - below:,.0f} at net worth "
                       f"{net_worth:,}; the published schedule is continuous.")

    # More income can never lower the SAI; a larger household can never raise it.
    for two in (True, False):
        prev = None
        for agi in range(0, 320000, 10000):
            got = sai(agi, 4, two_parents=two)["sai"]
            if prev is not None and got < prev:
                out.append(f"  SHAPE   SAI falls from {prev:,} to {got:,} between "
                           f"${agi - 10000:,} and ${agi:,} "
                           f"({'two parents' if two else 'one parent'}).")
            prev = got
        for size in range(3, 8):
            bigger = sai(120000, size + 1, two_parents=two)["sai"]
            smaller = sai(120000, size, two_parents=two)["sai"]
            if bigger > smaller:
                out.append(f"  SHAPE   a household of {size + 1} is assessed more "
                           f"({bigger:,}) than one of {size} ({smaller:,}).")

    # The floor binds and nothing goes under it.
    for agi in (0, 5000, 20000):
        got = sai(agi, 6)["sai"]
        if got < PUBLISHED_FLOOR:
            out.append(f"  SHAPE   SAI {got:,} at ${agi:,} is below the "
                       f"{PUBLISHED_FLOOR:,} floor.")

    # THE ASSET PROTECTION ALLOWANCE IS ZERO, so the first dollar of savings
    # must move the answer. If someone reinstates a nonzero allowance, every
    # figure this tool prints for a saving family becomes too low, silently.
    base = sai(120000, 4)["sai"]
    if sai(120000, 4, parent_assets=10000)["sai"] <= base:
        out.append("  SHAPE   $10,000 of parent assets does not raise the SAI; the "
                   "asset protection allowance is $0 for this award year.")
    # 12 cents on the dollar, assessed at the top marginal rate of Table A5.
    moved = sai(120000, 4, parent_assets=10000)["sai"] - base
    want = round(10000 * PUBLISHED_PARENT_ASSET_RATE * 0.47)
    if abs(moved - want) > 2:
        out.append(f"  SHAPE   $10,000 of assets moved the SAI by {moved:,}; at "
                   f"12 percent into the 47 percent bracket it should be {want:,}.")
    # Student assets are assessed harder than the parents', 20 against 12.
    if (sai(120000, 4, student_assets=10000)["sai"] - base) <= moved:
        out.append("  SHAPE   student assets are not assessed harder than parents'; "
                   "the published rates are 20 percent against 12.")
    return out


def check_zero_is_a_real_answer(ns, src):
    """An explicit 0 override must be honored, not read as 'not set'."""
    out = []
    sai = ns["compute_student_aid_index"]

    # The FUNCTION half. Passing 0 must differ from passing nothing.
    estimated = sai(90000, 4)
    explicit_zero = sai(90000, 4, income_tax_paid=0)
    if explicit_zero["sai"] == estimated["sai"]:
        out.append("  ZERO    income_tax_paid=0 gives the same SAI as omitting it. "
                   "A family that paid no federal income tax is being handed an "
                   "estimated allowance instead, which lowers their SAI.")
    if explicit_zero["sai"] < estimated["sai"]:
        out.append("  ZERO    income_tax_paid=0 LOWERS the SAI; a smaller allowance "
                   "must raise it.")
    if sai(90000, 4, earned_income=0)["sai"] <= estimated["sai"]:
        out.append("  ZERO    earned_income=0 does not raise the SAI. With no wages "
                   "there is no payroll allowance, so it must.")
    if not estimated["tax_estimated"] or explicit_zero["tax_estimated"]:
        out.append("  ZERO    the result does not report whether line 4 was "
                   "estimated, so the caption cannot tell the reader which it is.")

    # The CALL SITE half, by AST: the renderer lives in section 5, which the
    # exec above never reaches. `x or None` is the laundering that collapses an
    # explicit 0 back into "derive it".
    tree = ast.parse(src)
    renderer = next((n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef)
                     and n.name == "render_sai_worksheet"), None)
    if renderer is None:
        out.append("  ZERO    render_sai_worksheet is gone; its call site is unchecked.")
        return out
    for call in ast.walk(renderer):
        if not (isinstance(call, ast.Call)
                and getattr(call.func, "id", "") == "compute_student_aid_index"):
            continue
        for kw in call.keywords:
            if kw.arg not in ("income_tax_paid", "earned_income"):
                continue
            if (isinstance(kw.value, ast.BoolOp) and isinstance(kw.value.op, ast.Or)):
                out.append(
                    f"  ZERO    render_sai_worksheet passes {kw.arg} as `x or None`. "
                    f"That reads an explicit 0 as 'not set' and substitutes an "
                    f"estimate, which errs in the flattering direction.")
    return out


MONEY = re.compile(r"\$\s?\d|\d[\d,]*\s*(?:dollars|percent of your)")


def check_css_profile(ns, src):
    """The CSS Profile note may say DIRECTION and must never say an amount.

    Institutional Methodology is unpublished and varies by school, so any
    dollar figure here would be invented -- and it would sit on screen beside a
    federal figure that is traceable line by line, wearing the same authority.
    A reader cannot tell those apart, which is why this is a guard and not a
    comment.
    """
    out = []
    divergences = ns["css_profile_divergences"]

    # Each input must produce exactly one note, so none can be dropped silently.
    for kwarg in ("home_equity", "business_net_worth", "student_assets"):
        got = divergences(**{kwarg: 50000})
        if len(got) != 1:
            out.append(f"  PROFILE {kwarg}=50000 produced {len(got)} note(s), not 1.")
    if len(divergences(noncustodial_parent=True)) != 1:
        out.append("  PROFILE a second parent household produced no note.")
    if divergences():
        out.append("  PROFILE a family with none of the four circumstances still "
                   "gets a divergence note, so the section fires on everyone.")

    # NO MONEY. Not in any heading, not in any explanation.
    everything = divergences(home_equity=50000, noncustodial_parent=True,
                             business_net_worth=50000, student_assets=50000)
    for heading, explanation in everything:
        for text in (heading, explanation):
            if MONEY.search(text):
                out.append(
                    f"  PROFILE the CSS Profile note states an amount: {text[:70]!r}. "
                    f"Institutional Methodology is unpublished and per-school, so "
                    f"any figure here is invented.")

    # The two Profile-only inputs must never reach the FEDERAL arithmetic. The
    # FAFSA does not ask either question, so feeding them in would make the
    # worksheet stop matching the form whose line numbers it prints.
    signature = ns["compute_student_aid_index"].__code__.co_varnames
    for forbidden in ("home_equity", "noncustodial_parent", "noncustodial"):
        if forbidden in signature:
            out.append(f"  PROFILE compute_student_aid_index takes {forbidden!r}. "
                       f"The federal formula does not ask that question.")
    tree = ast.parse(src)
    renderer = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                     and n.name == "render_sai_worksheet"), None)
    if renderer is not None:
        for call in ast.walk(renderer):
            if (isinstance(call, ast.Call)
                    and getattr(call.func, "id", "") == "compute_student_aid_index"):
                for kw in call.keywords:
                    if kw.arg in ("home_equity", "noncustodial_parent"):
                        out.append(f"  PROFILE the renderer passes {kw.arg} into the "
                                   f"federal formula.")
    return out


# Anything that would reintroduce a per-state allowance. The names are the old
# EFC's own, which is what someone restoring it would reach for.
STATE_PARAMS = ("state", "state_of_residence", "home_state", "stabbr",
                "state_tax", "state_tax_allowance", "state_and_other")


def check_no_state_in_formula(ns, src):
    """State of residence is not an input to Formula A, and must not become one.

    It appears nowhere in lines 1 to 37 of the published worksheet. The only
    place the guide uses it is Step 1, the MAXIMUM PELL determination, where
    the 175% and 225% tests are against the poverty guideline for a family
    size and state. Pell is not implemented here, so no part of this tool has
    any business asking.

    THE OLD EFC DID HAVE A STATE AND OTHER TAX ALLOWANCE, varying by state of
    residence, and FAFSA Simplification replaced it with the payroll tax
    allowance. So the missing field looks like an omission to anyone reasoning
    from pre-2024 knowledge, and the obvious "fix" is to add a state input and
    an allowance to go with it. That would silently LOWER the SAI for families
    in high-tax states -- a bigger allowance, less available income -- and it
    would look like an improvement while making the figure wrong.

    Line 4 is federal and territory income tax only, which the guide states
    outright: "if the parent filed both a U.S. federal income tax return and
    an income tax return from Puerto Rico or another U.S. territory".
    """
    out = []
    signature = ns["compute_student_aid_index"].__code__.co_varnames[
        :ns["compute_student_aid_index"].__code__.co_argcount
        + ns["compute_student_aid_index"].__code__.co_kwonlyargcount]
    for name in signature:
        if name.lower() in STATE_PARAMS:
            out.append(f"  STATE   compute_student_aid_index takes {name!r}. State of "
                       f"residence is not an input to Formula A; the old EFC's "
                       f"state tax allowance was removed by FAFSA Simplification.")
    for constant in ("SAI_STATE_TAX_ALLOWANCE", "SAI_STATE_TAX_RATES",
                     "SAI_STATE_AND_OTHER_TAX"):
        if constant in ns:
            out.append(f"  STATE   {constant} exists. There is no per-state allowance "
                       f"in the 2027-28 formula.")
    tree = ast.parse(src)
    renderer = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                     and n.name == "render_sai_worksheet"), None)
    if renderer is not None:
        for call in ast.walk(renderer):
            if (isinstance(call, ast.Call)
                    and getattr(call.func, "id", "") == "compute_student_aid_index"):
                for kw in call.keywords:
                    if kw.arg and kw.arg.lower() in STATE_PARAMS:
                        out.append(f"  STATE   the renderer passes {kw.arg} into the "
                                   f"formula.")
    # And the tool has to SAY so, because the absence is the surprising part.
    if "state income tax gets no allowance" not in src.lower():
        out.append("  STATE   the tool no longer tells anyone that state income tax "
                   "gets no allowance. The absence of the field is exactly what a "
                   "family from a high-tax state will assume was counted.")
    return out


def negative_controls(ns):
    """Break it deliberately. A guard that has never failed is not evidence."""
    out = []

    # 1. A stale table. Roll the IPA back to the 2026-27 figures and the
    #    published-table check must name it.
    stale = dict(ns)
    stale["SAI_INCOME_PROTECTION_ALLOWANCE"] = {2: 29500, 3: 36700, 4: 45300,
                                                5: 53500, 6: 62600}
    if not any("IPA family size 4" in f for f in check_published_tables(stale)):
        out.append("  CONTROL a stale income protection allowance was NOT caught.")

    # 2. A transcription typo inside Table A5 that keeps every edge literal
    #    correct at its own point but breaks continuity nowhere else looks.
    broken = dict(ns)
    schedule = list(ns["SAI_A5_SCHEDULE"])
    schedule[3] = (34000, 39900, 8050, 0.34 + 0.10)
    broken["SAI_A5_SCHEDULE"] = tuple(schedule)
    src_ns = {}
    exec("def sai_parents_contribution(aai):\n"
         "    if aai < SAI_A5_MIN_AAI: return float(SAI_A5_MIN_CONTRIBUTION)\n"
         "    for floor, cap, base, rate in SAI_A5_SCHEDULE:\n"
         "        if cap is None or aai <= cap:\n"
         "            return base + rate * (aai - (floor if floor is not None else 0))\n",
         broken, src_ns)
    broken["sai_parents_contribution"] = src_ns["sai_parents_contribution"]
    if not any("Table A5" in f for f in check_published_tables(broken)):
        out.append("  CONTROL a wrong Table A5 marginal rate was NOT caught.")

    # 3. A reinstated asset protection allowance -- the single most likely
    #    "fix" someone would make, since every prior award year had one.
    with_apa = dict(ns)
    with_apa["SAI_ASSET_PROTECTION_ALLOWANCE"] = 30000
    # Rebuild the function against the mutated constant, so the control
    # exercises the real arithmetic rather than a re-typed copy of it.
    src = open(APP, encoding="utf-8").read()
    start = src.index("def compute_student_aid_index(")
    end = src.index("\n# The worksheet, in the FEDERAL", start)
    exec(compile(src[start:end], APP, "exec"), with_apa)
    if not any("asset protection allowance" in f for f in check_structure(with_apa)):
        out.append("  CONTROL a reinstated asset protection allowance was NOT caught.")

    # 4. A dollar figure smuggled into the CSS Profile note -- the one thing
    #    that section must never do, since nothing published supports it.
    leaky = dict(ns)
    leaky["css_profile_divergences"] = lambda **kw: [
        ("Your home", "A Profile school will expect about $4,200 more a year.")]
    if not any("states an amount" in f for f in check_css_profile(leaky, src_text())):
        out.append("  CONTROL a dollar figure in the CSS Profile note was NOT caught.")

    # 5. A state input reintroduced on the formula -- the "fix" someone
    #    reasoning from the old EFC would reach for.
    stateful = dict(ns)
    def _with_state(parent_agi, family_size, *, state=None, **kw):   # noqa: ANN001
        return ns["compute_student_aid_index"](parent_agi, family_size, **kw)
    stateful["compute_student_aid_index"] = _with_state
    if not any("STATE" in f for f in check_no_state_in_formula(stateful, src_text())):
        out.append("  CONTROL a state input on the SAI formula was NOT caught.")

    return out


def src_text():
    return open(APP, encoding="utf-8").read()


def main():
    ns, src = load_app_namespace()
    failures = (check_published_tables(ns) + check_structure(ns)
                + check_zero_is_a_real_answer(ns, src)
                + check_css_profile(ns, src)
                + check_no_state_in_formula(ns, src) + negative_controls(ns))
    if failures:
        print("SAI worksheet: %d problem(s)\n" % len(failures))
        print("\n".join(failures))
        return 1
    print("SAI worksheet OK -- %d published figures, Table A5 and A3 continuous, "
          "SAI monotone in income and household size, an explicit $0 override "
          "honored at the call site, the CSS Profile note states direction and "
          "never an amount, state of residence never enters Formula A, "
          "5 negative controls all caught."
          % (len(PUBLISHED_IPA) + len(PUBLISHED_A5_EDGES) + len(PUBLISHED_A3_EDGES) + 18))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Guard: the repayment tool's joint-against-separate comparison.

    python3 check_filing_status_cost.py     (exit 1 on any violation)

WHAT IT PROTECTS. Filing separately cuts an income-driven payment and raises
the tax bill. Until 2026-10 the tool stated neither half; now it states both,
and every way that can go wrong is SILENT: a bracket table copied from the
wrong filing status, the interest deduction left on a separate return, a
community property couple priced as if they split nothing, or a sentence that
turns an upper bound into a point estimate. Each looks like a plausible number.

THE ANCHORS ARE LITERALS FROM REV. PROC. 2024-40 (Tables 1 and 4, and .30 for
the interest deduction) plus the OBBBA standard deductions, never read back off
FILING_TAX_TABLES: a check that derives its expectation from the constant under
test asserts only that the constant equals itself.

THE PAYMENTS ARE THE TABLE'S OWN. filing_status_comparison re-runs
compare_existing_loan_plans rather than re-deriving a payment, so this checks
that its figures are exactly the plan rows' first-year payments.

Negative controls run on every invocation: each mutates one function's source,
re-executes it, and must make the check fail.
"""
import ast
import copy
import re
import sys

JOINT_2025 = (31500, ((23850, .10), (96950, .12), (206700, .22), (394600, .24),
                      (501050, .32), (751600, .35), (float("inf"), .37)))
SEPARATE_2025 = (15750, ((11925, .10), (48475, .12), (103350, .22),
                         (197300, .24), (250525, .32), (375800, .35),
                         (float("inf"), .37)))

FED = [{"balance": 60_000.0, "rate": 6.5}]


def load():
    src = open("app.py").read()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    prefix = src[:src.rindex("# " + "=" * 60, 0, cut)]
    ns = {"__name__": "filingcheck"}
    exec(compile(prefix, "app.py", "exec"), ns)
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name not in ns:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), ns)
    return ns, src


def kw(**extra):
    base = dict(balance=0, rate=0, dependents=0, forgivable=True, family_size=2,
                federal_loans=FED, private_loans=[])
    base.update(extra)
    return base


def year1(ns, result):
    return float(ns["payment_series"](result)["payment"].head(12).sum())


def row(ns, income, spouse, status):
    rows = ns["compare_existing_loan_plans"](
        annual_income=income, spouse_income=spouse, filing_status=status, **kw())
    return {l: r for l, r, _ in rows}


def check(ns) -> list:
    problems = []
    tax = ns["filing_federal_income_tax"]
    cmp_fn = ns["filing_status_comparison"]
    sentences = ns["filing_status_sentences"]
    money = ns["fmt_money"]

    # 1. The tables, against the Rev. Proc. literals.
    tables = ns["FILING_TAX_TABLES"]
    for key, want in (("joint", JOINT_2025), ("separate", SEPARATE_2025)):
        got = (tables[key][0], tuple(tuple(b) for b in tables[key][1]))
        if got != want:
            problems.append(f"  FILING_TAX_TABLES[{key!r}] is not Rev. Proc. 2024-40 "
                            "with the OBBBA standard deduction")

    # 2. Two returns worked by hand off those tables.
    #    Separate, $60,000: taxable 44,250 -> 1,192.50 + 12% of 32,325 = 5,071.50
    #    Joint, $140,000: taxable 108,500 -> 2,385 + 8,772 + 22% of 11,550 = 13,698
    for agi, status, want in ((60_000, "separate", 5_071.50),
                              (140_000, "joint", 13_698.00)):
        got = tax(agi, status)
        if abs(got - want) > 0.01:
            problems.append(f"  {status} tax on ${agi:,} is ${got:,.2f}, worked "
                            f"by hand it is ${want:,.2f}")

    # 3. Equal incomes carry no bracket penalty in 2025, because the separate
    #    table is exactly half the joint one. A real property, not a coincidence.
    if abs(2 * tax(70_000, "separate") - tax(140_000, "joint")) > 0.01:
        problems.append("  two separate returns of $70,000 do not cost exactly "
                        "one joint return of $140,000")

    # 4. The interest deduction: full to $170,000, gone at $200,000, linear.
    ded = ns["student_loan_interest_deduction"]
    for magi, want in ((150_000, 2500), (170_000, 2500), (185_000, 1250),
                       (200_000, 0), (250_000, 0)):
        if abs(ded(4_000, magi) - want) > 0.01:
            problems.append(f"  the deduction at ${magi:,} MAGI is "
                            f"${ded(4_000, magi):,.0f}, not ${want:,}")
    if abs(ded(900, 100_000) - 900) > 0.01:
        problems.append("  the deduction is not capped at the interest paid")

    # 5. Payments are the plan rows' own; separate sees only the borrower.
    c = cmp_fn(kw(), 60_000, 80_000)
    joint = row(ns, 60_000, 80_000, ns["FILING_JOINT"])
    sep = row(ns, 60_000, 0, ns["FILING_SEPARATE"])
    if not c or not c["plans"]:
        problems.append("  no comparison for a married couple with a spouse income")
        return problems
    for p in c["plans"]:
        for field, rows in (("joint_year1", joint), ("separate_year1", sep)):
            want = year1(ns, rows[p["label"]])
            if abs(p[field] - want) > 0.01:
                problems.append(f"  {p['label']} {field} is ${p[field]:,.2f}, the "
                                f"plan table's row says ${want:,.2f}")
        if p["extra_tax"] < -0.01:
            problems.append(f"  {p['label']}: filing separately LOWERS tax, which "
                            "the 2025 tables cannot produce")
        # $60,000 + $80,000 = $140,000, under the phaseout, and $60,000 at 6.5%
        # accrues $3,900 a year, so the joint return keeps the full $2,500.
        if abs(p["deduction"] - 2500) > 0.01:
            problems.append(f"  {p['label']}: the joint deduction is "
                            f"${p['deduction']:,.0f}, not the full $2,500")
        want_extra = (tax(60_000, "separate") + tax(80_000, "separate")
                      - tax(137_500, "joint"))
        if abs(p["extra_tax"] - want_extra) > 0.01:
            problems.append(f"  {p['label']}: extra tax ${p['extra_tax']:,.2f}, "
                            f"want ${want_extra:,.2f}")

    # 6. Community property: each separate return carries half the combined
    #    income, so the separate payment is figured on $70,000; the borrower's
    #    own $60,000 rides separately; and with equal halves the only tax cost
    #    left is the lost deduction.
    cp = cmp_fn(kw(), 60_000, 80_000, community_property=True)
    half = row(ns, 70_000, 0, ns["FILING_SEPARATE"])
    for p in cp["plans"]:
        if abs(p["separate_year1"] - year1(ns, half[p["label"]])) > 0.01:
            problems.append(f"  community property {p['label']}: the separate "
                            "payment is not figured on half the combined income")
        if p["own_year1"] is None or abs(p["own_year1"] - year1(ns, sep[p["label"]])) > 0.01:
            problems.append(f"  community property {p['label']}: the own-earnings "
                            "payment is missing or not on the borrower's income")
        want = tax(140_000, "joint") - tax(140_000 - 2500, "joint")
        if abs(p["extra_tax"] - want) > 0.01:
            problems.append(f"  community property {p['label']}: extra tax "
                            f"${p['extra_tax']:,.2f}, want only the lost deduction "
                            f"${want:,.2f}")

    # 7. Nothing to compare.
    if cmp_fn(kw(), 60_000, 0) is not None:
        problems.append("  a comparison was built with no spouse income")

    # 8. The words. "At most", parts that add up, the left-out list, the 2012
    #    citation, and the case where tax eats the saving.
    lines = sentences(c, money=money)
    body = " ".join(lines)
    for p_line in lines:
        m = re.search(r"by \$([\d,]+) and raises federal income tax by at least "
                      r"\$([\d,]+), so it saves at most \$([\d,]+)", p_line)
        if m:
            a, b, n = (int(x.replace(",", "")) for x in m.groups())
            if a - b != n:
                problems.append(f"  printed ${a:,} less ${b:,} is not the printed "
                                f"${n:,}")
    if "at most" not in body:
        problems.append("  the saving is not stated as an upper bound")
    if "state income tax" not in body:
        problems.append("  the sentence does not name state income tax as left out")
    if "2012-26348" not in " ".join(sentences(cp, money=money)):
        problems.append("  the community property sentence does not cite the 2012 rule")
    rich = cmp_fn(kw(), 15_000, 400_000)
    if rich and not any("more than it saves" in l or "does not lower" in l
                        for l in sentences(rich, money=money)):
        problems.append("  a couple whose extra tax exceeds the payment cut is "
                        "still told it saves money")
    return problems


def check_renderer(src) -> list:
    """The wiring, which no call can reach: the renderer is in section 4."""
    problems = []
    start = src.index("def render_existing_loan_comparison(")
    body = src[start:src.index("\ndef ", start + 10)]
    if "spouse_income_entered > 0 and spouse_no_loans" not in body:
        problems.append("  the comparison is not gated on the spouse having no "
                        "federal loans")
    call = body[body.index("rows = compare_existing_loan_plans("):]
    call = call[:call.index(")\n")]
    if "spouse_income=spouse_income," not in call or "spouse_income_entered" in call:
        problems.append("  the plan table's payment can see the spouse income on "
                        "a separate return")
    if "filing_comparison=filing_cmp" not in body:
        problems.append("  the comparison does not reach the PDF")
    if 'affordability["joint_with_spouse"] = False' not in body:
        problems.append("  the affordability note still says the tax is unmodelled "
                        "beside a comparison that models it")
    return problems


def mutate(ns, src, fn_name, old, new):
    """A copy of ns with one function's source edited and re-executed."""
    node = next(n for n in ast.parse(src).body
                if isinstance(n, ast.FunctionDef) and n.name == fn_name)
    text = ast.get_source_segment(src, node)
    if old not in text:
        raise SystemExit(f"negative control anchor missing in {fn_name}: {old!r}")
    m = dict(ns)
    exec(compile(text.replace(old, new), "app.py", "exec"), m)
    # Functions close over their module globals, so rebind the callers too.
    for name in ("filing_status_comparison", "filing_status_sentences"):
        n2 = next(n for n in ast.parse(src).body
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        if name != fn_name:
            exec(compile(ast.get_source_segment(src, n2), "app.py", "exec"), m)
    return m


def negative_controls(ns, src) -> list:
    failures = []
    controls = [
        ("the CP split removed", "filing_status_comparison",
         "(combined / 2, combined / 2) if community_property else (b, sp)", "(b, sp)"),
        ("the joint return's interest deduction dropped", "filing_status_comparison",
         'tax_joint = filing_federal_income_tax(combined - deduction, "joint")',
         'tax_joint = filing_federal_income_tax(combined, "joint")'),
        ("rounding removed", "filing_status_sentences",
         'cut = round(p["joint_year1"] - p["separate_year1"])\n'
         '        extra = round(max(0.0, p["extra_tax"]))',
         'cut = p["joint_year1"] - p["separate_year1"]\n'
         '        extra = max(0.0, p["extra_tax"])'),
        ("'at most' dropped", "filing_status_sentences",
         "so it saves at \"\n                       f\"most", "so it saves \"\n                       f\""),
    ]
    for label, fn, old, new in controls:
        if not check(mutate(ns, src, fn, old, new)):
            failures.append(f"  negative control did not fail: {label}")
    m = dict(ns)
    m["FILING_TAX_TABLES"] = copy.deepcopy(ns["FILING_TAX_TABLES"])
    m["FILING_TAX_TABLES"]["separate"] = (15750, ((11925, .10), (48475, .12),
        (103350, .22), (197300, .24), (250525, .32), (626350, .35),
        (float("inf"), .37)))   # the SINGLE 35% bracket, the likeliest copy error
    exec(compile(ast.get_source_segment(src, next(
        n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)
        and n.name == "filing_federal_income_tax")), "app.py", "exec"), m)
    if not check(m):
        failures.append("  negative control did not fail: the single-filer table "
                        "copied into the separate one")
    if not check_renderer(src.replace("spouse_income_entered > 0 and spouse_no_loans",
                                      "spouse_income_entered > 0")):
        failures.append("  negative control did not fail: the spouse-loans gate removed")
    return failures


def main() -> int:
    ns, src = load()
    problems = check(ns) + check_renderer(src) + negative_controls(ns, src)
    if problems:
        print(f"filing status cost: {len(problems)} violation(s)\n")
        print("\n".join(problems))
        return 1
    print("filing status cost OK -- both 2025 tables match Rev. Proc. 2024-40, "
          "the payments are the plan rows' own, community property splits the "
          "income, the saving is an upper bound, and 6 negative controls fail "
          "as they should.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

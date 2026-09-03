#!/usr/bin/env python3
"""Guard: the repayment tool's affordability note.

    python3 check_affordability_flag.py     (exit 1 on any violation)

WHAT IT PROTECTS. The tool renders every plan row with equal confidence, and
for some portfolios every row is unpayable. `repayment_affordability` says so.
Everything that makes that note safe is invisible from the screen: which basis
the percentage is on, whether the thresholds are the cited ones, and whether
the prose has quietly acquired a verdict.

THE BASIS IS THE SILENT ONE. Gross and take-home produce plausible numbers
either way, so a mislabelled basis is not visible to a reader or a reviewer.
It is on GROSS deliberately: the repayment tool has no state, so a federal-only
take-home estimate would overstate take-home and understate the burden, and a
warning that errs toward reassurance is worse than no warning.

NO INVENTED THRESHOLD. There is no published line meaning "you cannot pay
this". The two thresholds are app.py's own and are cited (10% of gross from
student loan budgeting guidance; 36% from the back-end debt-to-income ceiling
mortgage lenders apply to ALL debts combined). This checks that no third one
has appeared.

NO VERDICT AND NO LEGAL TERMS. Default, garnishment and bankruptcy are legal
questions with no arithmetic answer, and SCOPE.md puts "should I do this" out
of scope. The note states a ratio and points at free help.
"""
import ast
import re
import sys

# The distressed portfolio this feature was written for, from an
# r/StudentLoans post: $130,000 federal and $180,000 private at 15% on
# $53,000. Held as literals rather than derived, so a model change that made
# this affordable would fail here rather than quietly disarm the fixture.
HARD = dict(fed=130_000.0, fed_rate=6.5, priv=180_000.0, priv_rate=15.0,
            income=53_000.0)
EASY = dict(fed=25_000.0, fed_rate=6.5, priv=0.0, priv_rate=0.0,
            income=62_000.0)

BANNED = ("garnish", "default on", "bankrupt", "sue you", "wage garnish",
          "you should", "you must", "we recommend", "do not ")


def load():
    src = open("app.py").read()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    prefix = src[:src.rindex("# " + "=" * 60, 0, cut)]
    ns = {"__name__": "affordcheck"}
    exec(compile(prefix, "app.py", "exec"), ns)
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name not in ns:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), ns)
    return ns, src


def rows_for(ns, spec, **kw):
    kw.setdefault("family_size", 1)
    return ns["compare_existing_loan_plans"](
        balance=spec["fed"], rate=spec["fed_rate"], annual_income=spec["income"],
        private_balance=spec["priv"], private_rate=spec["priv_rate"], **kw)


def flag_for(ns, spec, needle, **kw):
    rows = rows_for(ns, spec, **kw)
    pr = next((r for l, r, _ in rows if l == ns["PRIVATE_ROW_LABEL"]), None)
    pm = float(pr.get("monthly_payment") or 0.0) if pr else 0.0
    py = float(pr.get("payoff_years") or 0.0) if pr else 0.0
    res = next(r for l, r, _ in rows if needle in l)
    return ns["repayment_affordability"](
        res, spec["income"], private_monthly=pm, private_payoff_years=py,
        filing_status=kw.get("filing_status", ns["FILING_SINGLE"]),
        spouse_income=kw.get("spouse_income", 0.0)), res, pm


def check_gross_basis(ns):
    """The thresholds must be the RAW gross constants, untouched by a tax rate.

    get_loan_to_income_risk_tier divides its thresholds by (1 - tax rate), so
    passing anything but 0.0 moves them. Comparing the returned thresholds
    against the constants is a behavioural check on the basis: it fails if a
    future edit starts passing an effective rate, which is the exact change
    that would silently put this on a take-home footing.
    """
    out = []
    flag, _, _ = flag_for(ns, HARD, "RAP")
    for key, const in (("manageable_threshold", "LOAN_TO_INCOME_GROSS_MANAGEABLE_PCT"),
                       ("caution_threshold", "LOAN_TO_INCOME_GROSS_CAUTION_PCT")):
        if abs(flag[key] - float(ns[const])) > 1e-9:
            out.append(f"  {key} is {flag[key]:.2f}, not the published gross "
                       f"{const} of {float(ns[const]):.2f}. Something is passing "
                       f"an effective tax rate, which puts this on a take-home "
                       f"basis while the prose still says 'before tax'")
    lines = " ".join(ns["affordability_sentences"](flag)).lower()
    if "before tax" not in lines:
        out.append("  the note does not say the percentage is before tax; a "
                   "reader cannot tell which basis a plausible number is on")
    return out


def check_no_invented_threshold(ns, src):
    """No numeric threshold of its own, and the cited ones are imported."""
    out = []
    body = re.search(r"def repayment_affordability\(.*?\n(?=def )", src, re.S)
    body = body.group(0) if body else ""
    if "get_loan_to_income_risk_tier" not in body:
        out.append("  repayment_affordability does not use "
                   "get_loan_to_income_risk_tier; the thresholds must be the "
                   "cited ones rather than new numbers")
    # A bare percentage comparison would be a threshold invented here.
    for m in re.finditer(r"(pct|ratio)\s*[<>]=?\s*([0-9]+(?:\.[0-9]+)?)", body):
        if m.group(2) not in ("100", "100.0", "0", "0.0"):
            out.append(f"  compares the percentage against a literal "
                       f"{m.group(2)}; thresholds belong in "
                       f"LOAN_TO_INCOME_GROSS_*, not here")
    return out


def check_returns_none(ns):
    """Nothing to say means nothing said."""
    out = []
    rows = rows_for(ns, HARD)
    res = next(r for l, r, _ in rows if "RAP" in l)
    if ns["repayment_affordability"](res, 0.0) is not None:
        out.append("  a zero income still produces a flag; with no income "
                   "there is no share to report")
    if ns["repayment_affordability"](res, None) is not None:
        out.append("  a missing income still produces a flag")
    if ns["repayment_affordability"]({"monthly_payment": 0.0}, 50_000.0) is not None:
        out.append("  a zero payment still produces a flag; a note on an empty "
                   "form teaches people to ignore it")
    return out


def check_private_share(ns):
    """The private figure must be the private row's, recomputed independently."""
    out = []
    flag, _, pm = flag_for(ns, HARD, "RAP")
    if abs(flag["private_monthly"] - pm) > 0.01:
        out.append(f"  the note reports {flag['private_monthly']:,.2f} of private "
                   f"payment against the private row's {pm:,.2f}")
    # Recomputed longhand rather than read back off the flag.
    want = pm / (HARD["income"] / 12.0) * 100.0
    if abs(flag["private_pct"] - want) > 1e-6:
        out.append(f"  private_pct is {flag['private_pct']:.2f}, longhand says "
                   f"{want:.2f}")
    if flag["private_monthly"] >= flag["monthly"]:
        out.append("  the private payment is not smaller than the combined one; "
                   "the fixture no longer exercises the decomposition")
    return out


def check_fires_and_stays_silent(ns):
    """Both directions. A flag that never fires and one that always fires are
    equally useless, and only the second gets noticed."""
    out = []
    hard, _, _ = flag_for(ns, HARD, "RAP")
    if not ns["affordability_sentences"](hard):
        out.append("  silent on the portfolio this feature exists for "
                   f"({hard['pct']:.0f}% of gross, tier {hard['tier']!r})")
    easy, _, _ = flag_for(ns, EASY, "RAP")
    if ns["affordability_sentences"](easy):
        out.append(f"  fires on an ordinary borrower at {easy['pct']:.0f}% of "
                   f"gross; a note under every portfolio is furniture")
    return out


def check_no_verdict(ns):
    """States a ratio and points at help. No outcome, no instruction."""
    out = []
    flag, _, _ = flag_for(ns, HARD, "RAP")
    text = " ".join(ns["affordability_sentences"](flag)).lower()
    for word in BANNED:
        if word in text:
            out.append(f"  the note contains {word!r}. Default, garnishment and "
                       f"bankruptcy are legal questions with no arithmetic "
                       f"answer, and SCOPE.md excludes 'should I do this'")
    if "nonprofit" not in text:
        out.append("  the note does not point at nonprofit counseling, which is "
                   "the only thing it can offer in place of a verdict")
    for party in ("sofi", "earnest", "navient", "nelnet", "sallie"):
        if party in text:
            out.append(f"  names {party!r}; this project has no source for what "
                       f"any lender or servicer offers")
    return out


def check_cliff(ns):
    """The cliff sentence: when the private note clears and what is left.

    THE FIGURE IS ON TODAY'S INCOME, and that is the whole reason this is a
    sentence rather than a chart. Growing the income first would make it depend
    on the 3% assumption compounding for a decade; the cliff itself is driven
    by a LOAN TERM, which is known. So the check recomputes it against today's
    gross and fails if anything has started projecting.

    It must also NOT fire when there is no "after" to describe: on Standard
    (10-year) the federal plan ends when the private note does, and announcing
    a cliff there would promise relief that is simply the end of the loan.
    """
    out = []
    rap, rap_res, pm = flag_for(ns, HARD, "RAP")
    if not rap["has_cliff"]:
        out.append("  no cliff on a 30-year plan beside a 10-year private note, "
                   "which is the case the sentence exists for")
        return out

    combined = rap["monthly"]
    want_after = combined - pm
    if abs(rap["after_private_monthly"] - want_after) > 0.01:
        out.append(f"  after-cliff payment is {rap['after_private_monthly']:,.2f}; "
                   f"combined minus private is {want_after:,.2f}")
    # Today's gross, longhand. If a projection creeps in, this diverges.
    want_pct = want_after / (HARD["income"] / 12.0) * 100.0
    if abs(rap["after_private_pct"] - want_pct) > 1e-6:
        out.append(f"  after-cliff share is {rap['after_private_pct']:.2f}%, but "
                   f"on TODAY's income it is {want_pct:.2f}%. Something is "
                   f"growing the income, which makes the figure depend on an "
                   f"assumption the sentence exists to avoid")
    if rap["after_private_pct"] >= rap["pct"]:
        out.append("  the after-cliff share is not lower than the current one; "
                   "there is no cliff to report")

    # And the negative case, in the same fixture rather than a separate one.
    std, _, _ = flag_for(ns, HARD, "Standard (10")
    if std["has_cliff"]:
        out.append("  claims a cliff on Standard (10-year), where the federal "
                   "plan ends when the private note does. There is no 'after' "
                   "there, and naming one promises relief that is just the end "
                   "of the loan")

    text = " ".join(ns["affordability_sentences"](rap)).lower()
    if "today" not in text:
        out.append("  the cliff sentence does not say the figure is on today's "
                   "income; a reader cannot tell it from a projection")
    return out


def check_filing_status(ns):
    """The spouse-income line: fires only when it is true, and names no amount.

    THE MECHANISM WAS ALREADY IN A TOOLTIP on the filing control, and a
    borrower in distress does not hover tooltips. On r/StudentLoans the single
    most upvoted diagnostic reply on a "my payment jumped" thread was a
    stranger asking whether the poster files jointly. This surfaces the same
    fact where the payment is.

    DIRECTION, NEVER AN AMOUNT. The separate-filing payment IS computable, and
    quoting it would be flattering in one direction: filing separately raises
    the tax bill by an amount this tool does not model at all. A saving stated
    without its cost is the error this repo keeps recording, so the check
    refuses a dollar figure or a percentage in that sentence.
    """
    out = []
    joint, _, _ = flag_for(ns, HARD, "RAP", family_size=2,
                           filing_status=ns["FILING_JOINT"], spouse_income=40_000.0)
    if not joint["joint_with_spouse"]:
        out.append("  a joint filer with spouse income is not flagged as such")
    joint_text = [l for l in ns["affordability_sentences"](joint)
                  if "file jointly" in l or "filing separately" in l.lower()]
    if not joint_text:
        out.append("  no sentence names the spouse's income as the reason the "
                   "payment counts it")
    else:
        line = joint_text[0]
        import re as _re
        if _re.search(r"\$[\d,]+|\d+\s*%|\d+\s*percent", line):
            out.append(f"  the filing sentence quotes a figure: {line!r}. "
                       f"Filing separately raises the tax bill by an amount "
                       f"this tool does not model, so a saving stated without "
                       f"its cost is flattering in one direction")
        if "does not model" not in line:
            out.append("  the filing sentence does not say the tax side is "
                       "unmodelled; without that it reads as a recommendation")

    # Both negative cases, in the same fixture rather than separate ones.
    single, _, _ = flag_for(ns, HARD, "RAP")
    if single["joint_with_spouse"]:
        out.append("  a single filer is flagged as filing jointly")
    if any("jointly" in l for l in ns["affordability_sentences"](single)):
        out.append("  the filing sentence renders for a single filer")
    nospouse, _, _ = flag_for(ns, HARD, "RAP", family_size=2,
                              filing_status=ns["FILING_JOINT"], spouse_income=0.0)
    if nospouse["joint_with_spouse"]:
        out.append("  flagged as joint-with-spouse when the spouse earns "
                   "nothing; there is no spouse income being counted, so the "
                   "sentence would name a lever that moves nothing")
    return out


def main() -> int:
    ns, src = load()
    checks = (
        ("the basis is gross and says so", check_gross_basis, (ns,)),
        ("no threshold invented here", check_no_invented_threshold, (ns, src)),
        ("nothing to say means nothing said", check_returns_none, (ns,)),
        ("the private share is the private row's", check_private_share, (ns,)),
        ("fires where it must, silent where it must", check_fires_and_stays_silent, (ns,)),
        ("no verdict, no legal terms, no lender", check_no_verdict, (ns,)),
        ("the cliff, on today's income", check_cliff, (ns,)),
        ("the spouse-income line", check_filing_status, (ns,)),
    )
    problems = []
    for name, fn, args in checks:
        found = fn(*args)
        if found:
            problems.append(f"{name}:\n" + "\n".join(found))
    if problems:
        print(f"affordability flag: {len(problems)} failing check(s)\n")
        print("\n\n".join(problems))
        return 1
    print(f"affordability flag OK -- {len(checks)} checks: gross basis on the "
          f"published thresholds, silent when there is nothing to say, the "
          f"private share is the private row's, and the note states a ratio "
          f"rather than a verdict.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

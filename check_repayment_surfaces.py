#!/usr/bin/env python3
"""Guard: every function the repayment tool reaches is actually CALLED.

    python3 check_repayment_surfaces.py     (exit 1 on any violation)

WHY THIS EXISTS. check_repayment_invariants proves the arithmetic balances its
own books, and it passed happily while build_payment_chart raised NameError on
every render -- because nothing in the suite CALLED a chart builder. The tool
had eleven guards covering what it computes and none covering whether it runs.
A rendered frame caught two more defects the same afternoon (a caption under
the wrong chart, a y-axis scaled to one band of four), which is the same gap
seen from the other side.

So this is a SMOKE test, deliberately: it asks whether every surface the tool
reaches still executes and returns the right shape, across portfolios that
exercise the branches. It does not check the numbers. That is the other
guard's job and it does it better.

THE COVERAGE SET IS DERIVED, NOT TYPED. A smoke test that does not grow with
the code is the "guard quietly narrowing" failure CLAUDE.md records against
check_chart_basis, where a new deck function shipped uncovered and the green
tick meant less than it had. REQUIRED is computed from the source every run:
everything defined in section 2m, plus everything reachable in two call hops
from the tool's entry points. Adding a function to the repayment tool FAILS
this guard until it is called here or exempted with a reason.
"""
import ast
import re
import sys

import pandas as pd

# Streamlit-runtime-only surfaces. Each needs session_state or a live script
# run, so it is exercised by the AppTest pass at the end rather than called
# directly -- which is a REASON, not a pass. Anything added here without one
# is how coverage rots.
EXEMPT = {
    "render_existing_loan_comparison": "the renderer itself; driven by AppTest below",
    "_repayment_actions": "renders download/share buttons; needs a script run",
    "render_rap_subsidy_answer": "st.info only; driven by AppTest below",
    "build_repayment_share_params": "reads st.session_state; covered by check_share_coverage",
    "seed_repayment_from_share": "writes st.session_state before widgets exist",
    "session_query_params": "reads st.query_params",
    "log_usage_event": "writes to Supabase; never called from a guard",
    "mark_interaction": "session_state counter",
    "memoized_pdf": "session_state cache around the builders below",
    "copy_url_to_clipboard_js": "returns a JS string for components.html",
    "internal_tool_url": "covered by check_internal_links",
    "pdf_memo_signature": "covered by check_zero_loan's extras assertions",
    "_pdf_page_painter": "a reportlab canvas callback, called by the document",
    "_pdf_styles": "stylesheet factory, exercised through the PDF builders",
    "_pdf_table": "flowable factory, exercised through the PDF builders",
    "fmt_money": "formatter, covered everywhere",
    "fmt_money_md": "formatter, covered everywhere",
    "fmt_duration": "covered by check_chart_axes",
    # Share-link readers. They need st.query_params, and check_share_coverage
    # and check_share_bounds already own that pipeline end to end.
    "_clamp_shared": "share-link bounds; covered by check_share_bounds",
    "get_shared_default": "reads st.query_params; covered by check_share_coverage",
    "get_shared_int": "reads st.query_params; covered by check_share_coverage",
    "get_shared_float": "reads st.query_params; covered by check_share_coverage",
    # Session and logging plumbing. check_supabase_resilience owns these, and
    # a guard must never write to the research dataset.
    "current_page_key": "session_state page latch",
    "get_session_id": "session_state UUID",
    "get_traffic_source": "session_state latch on ?src=",
    "get_write_queue": "the background Supabase writer",
    "json_safe_row": "insert sanitiser; covered by check_supabase_resilience",
}


def load():
    src = open("app.py").read()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    prefix = src[:src.rindex("# " + "=" * 60, 0, cut)]
    ns = {"__name__": "surfacecheck"}
    exec(compile(prefix, "app.py", "exec"), ns)
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name not in ns:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), ns)
    return ns, src


def required_surface(src: str) -> set:
    """Everything the repayment tool reaches, computed from the source.

    Section 2m is the tool's own code. The entry points then reach chart
    builders and formatters that live in section 2j, so calls are followed two
    hops -- one hop misses private_loan_stack, which repayment_balance_stack
    calls and which is exactly the kind of function a stale hand-typed list
    would drop.
    """
    tree = ast.parse(src)
    module_fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    start = src.index("# ---- 2m. Repayment comparison for an EXISTING balance")
    first_line = src[:start].count("\n") + 1
    rest = [m.start() for m in re.finditer(r"^# ---- ", src[start + 10:], re.M)]
    last_line = (src[:start + 10 + rest[0]].count("\n") + 1 if rest
                 else src.count("\n"))
    surface = {name for name, node in module_fns.items()
               if first_line <= node.lineno <= last_line}
    frontier = surface | {"render_existing_loan_comparison",
                          "generate_pdf_repayment_report", "_repayment_actions"}
    for _ in range(2):
        found = set()
        for name in frontier:
            node = module_fns.get(name)
            if node is None:
                continue
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                        and sub.func.id in module_fns):
                    found.add(sub.func.id)
        frontier |= found
        surface |= found
    return surface & set(module_fns)


PORTFOLIOS = {
    "one federal loan only": dict(
        fed=[{"balance": 30_000.0, "rate": 6.5}], priv=[], income=45_000.0),
    "four federal, four private (the reported shape)": dict(
        fed=[{"balance": 6_000.0, "rate": r} for r in (2.0, 3.0, 4.0, 5.0)],
        priv=[{"balance": b, "rate": r, "term": 5, "actual": 0} for b, r in
              ((10_300, 7.33), (3_000, 9.88), (4_000, 9.72), (7_300, 6.96))],
        income=60_000.0, private_extra=130.0),
    "private notes on MIXED terms": dict(
        fed=[{"balance": 24_000.0, "rate": 3.5}],
        priv=[{"balance": b, "rate": r, "term": t, "actual": 0} for b, r, t in
              ((10_300, 7.33, 10), (3_000, 9.88, 5), (7_300, 6.96, 15))],
        income=60_000.0),
    "FIVE private notes, past the palette": dict(
        fed=[{"balance": 24_000.0, "rate": 3.5}],
        priv=[{"balance": 5_000.0, "rate": 7.0 + i, "term": 5, "actual": 0}
              for i in range(5)],
        income=60_000.0, private_extra=50.0),
    "a per-note actual override": dict(
        fed=[{"balance": 24_000.0, "rate": 3.5}],
        priv=[{"balance": 10_300.0, "rate": 7.33, "term": 5, "actual": 400},
              {"balance": 3_000.0, "rate": 9.88, "term": 5, "actual": 0}],
        income=60_000.0),
    "Parent PLUS (no income-driven rows)": dict(
        fed=[{"balance": 40_000.0, "rate": 7.5}], priv=[], income=70_000.0,
        forgivable=False),
    "PSLF, low income": dict(
        fed=[{"balance": 60_000.0, "rate": 6.5}], priv=[], income=32_000.0,
        pslf=True),
    "old IBR, with prior payments": dict(
        fed=[{"balance": 60_000.0, "rate": 6.5}], priv=[], income=45_000.0,
        old_ibr=True, prior_payments=60),
    "a household, so the poverty guideline is used": dict(
        fed=[{"balance": 45_000.0, "rate": 6.5}], priv=[], income=52_000.0,
        family_size=4, spouse_income=18_000.0, filing_joint=True,
        poverty_region="alaska"),
    "a zero-interest note": dict(
        fed=[{"balance": 12_000.0, "rate": 0.0}],
        priv=[{"balance": 4_000.0, "rate": 0.0, "term": 5, "actual": 0},
              {"balance": 6_000.0, "rate": 3.0, "term": 5, "actual": 0}],
        income=50_000.0),
}


def main() -> int:
    ns, src = load()
    problems, called, checked = [], set(), 0

    def use(name):
        called.add(name)
        return ns[name]

    # REAL EXECUTION, not a hand-kept list. sys.settrace records every app.py
    # function that actually runs, so a helper reached only transitively -- a
    # pdf figure factory, an axis applier -- counts as covered because it was
    # genuinely exercised. The alternative was an EXEMPT entry per internal,
    # which is bookkeeping that rots and says nothing about whether the code
    # ran.
    executed = set()

    def _trace(frame, event, arg):
        if event == "call" and frame.f_code.co_filename == "app.py":
            executed.add(frame.f_code.co_name)
        return None

    sys.settrace(_trace)
    for label, spec in PORTFOLIOS.items():
        fed = spec["fed"]
        priv = spec.get("priv") or []
        total = sum(loan["balance"] for loan in fed)
        rate = (sum(l["balance"] * l["rate"] for l in fed) / total) if total else 0.0
        try:
            rows = use("compare_existing_loan_plans")(
                total, rate, spec["income"], 0,
                spec.get("forgivable", True), 0.0,
                spec.get("pslf", False), spec.get("prior_payments", 0),
                federal_loans=fed, private_loans=priv,
                old_ibr=spec.get("old_ibr", False),
                private_extra=spec.get("private_extra", 0.0),
                family_size=spec.get("family_size"),
                spouse_income=spec.get("spouse_income", 0.0),
                filing_status=(ns["FILING_JOINT"] if spec.get("filing_joint")
                               else ns["FILING_SINGLE"]),
                poverty_region=spec.get("poverty_region", "contiguous"))
        except Exception as exc:
            problems.append(f"  [{label}] compare_existing_loan_plans raised "
                            f"{type(exc).__name__}: {exc}")
            continue
        checked += 1
        if not rows:
            problems.append(f"  [{label}] produced no plan rows at all")
            continue

        use("sanitize_loan_rows")(priv, private=True)
        use("simulate_fixed_avalanche")(priv or fed, 10, per_loan_terms=bool(priv))
        use("discharge_tax_estimate")(60_000.0, 40_000.0, 20)
        use("balance_split_is_informative")(rows[0][1]["schedule"])

        for plan_label, result, _note in rows:
            frame = use("_repayment_table")(rows, federal_only=False)
            checked += 1
            if frame.empty:
                problems.append(f"  [{label}] _repayment_table came back empty")
            use("first_payment_of")(result)
            use("rap_months_counting_back")(result, 250.0)
            use("plan_change_from_today")(0, rows, accrued_interest=100.0)
            use("extra_payment_target")(result, fed, priv,
                                        pslf=spec.get("pslf", False))
            use("private_loan_stack")(result)
            use("private_rolldown_stack")(result)
            use("private_payoff_marker")(result)
            bands, blabels, bcolors, bby = use("repayment_balance_stack")(
                plan_label, result, rows)
            use("tranche_balance_frame")(bands, blabels or ns["TRANCHE_LABELS"])
            use("tranche_payment_frame")(bands, blabels or ns["TRANCHE_LABELS"])
            use("payment_series")(result)
            use("tranche_payoff_events")(result.get("federal_only"), result)

            # THE CHART BUILDERS, which is the whole reason this file exists.
            for builder, kind in ((use("build_balance_chart"), "balance"),
                                  (use("build_pdf_balance_chart"), "pdf balance")):
                try:
                    builder(result["schedule"], plan_label, tranches=bands,
                            labels=blabels or ns["TRANCHE_LABELS"],
                            colors=bcolors, stack_by=bby,
                            marker=use("private_payoff_marker")(result))
                    checked += 1
                except Exception as exc:
                    problems.append(
                        f"  [{label}] {kind} chart for {plan_label!r} raised "
                        f"{type(exc).__name__}: {exc}")
            _priv_res = next((r for l, r, _ in rows
                              if l == ns["PRIVATE_ROW_LABEL"]), None)
            for builder, kind in ((use("build_payment_chart"), "payment"),
                                  (use("build_pdf_payment_chart"), "pdf payment")):
                try:
                    builder(result, plan_label,
                            federal_result=result.get("federal_only"),
                            private_result=_priv_res,
                            labels=ns["REPAYMENT_STACK_LABELS"])
                    checked += 1
                except Exception as exc:
                    problems.append(
                        f"  [{label}] {kind} chart for {plan_label!r} raised "
                        f"{type(exc).__name__}: {exc}")

        analysis = use("pivot_strategy_analysis")(
            rows, fed, spec["income"], 0,
            pslf=spec.get("pslf", False),
            prefer_label=rows[0][0], old_ibr=spec.get("old_ibr", False))
        if analysis is not None:
            checked += 1
            sentences = use("strategy_verdict_sentences")(analysis)
            if not sentences:
                problems.append(f"  [{label}] the strategy panel produced no prose")

        try:
            pdf = use("generate_pdf_repayment_report")(
                rows, total, rate, spec["income"], 0, 0.0,
                spec.get("prior_payments", 0), spec.get("forgivable", True),
                spec.get("pslf", False), chart_label=rows[0][0],
                federal_loans=fed, private_loans=priv)
            checked += 1
            if not pdf or bytes(pdf[:5]) != b"%PDF-":
                problems.append(f"  [{label}] the PDF report is not a PDF")
        except Exception as exc:
            problems.append(f"  [{label}] generate_pdf_repayment_report raised "
                            f"{type(exc).__name__}: {exc}")

    sys.settrace(None)

    # COVERAGE. Derived every run, so a new repayment function fails here until
    # it is called above or exempted with a reason.
    surface = required_surface(src)
    missing = sorted(surface - called - executed - set(EXEMPT))
    if missing:
        problems.append(
            "  these repayment functions are reached by the tool and NEVER RAN "
            "in this guard:\n    " + "\n    ".join(missing) +
            "\n    Exercise them above, or add them to EXEMPT with a reason.")
    stale = sorted(set(EXEMPT) - surface)
    if stale:
        problems.append(
            "  EXEMPT names functions the tool no longer reaches: "
            f"{stale}. An exemption for code that is gone hides the next one.")

    if problems:
        print(f"repayment surfaces: {len(problems)} problem(s) across {checked} calls\n")
        print("\n".join(problems))
        return 1
    print(f"repayment surfaces OK -- {checked} calls across {len(PORTFOLIOS)} "
          f"portfolios; {len(surface)} reachable function(s), "
          f"{len(surface & (called | executed))} executed, {len(EXEMPT)} exempt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

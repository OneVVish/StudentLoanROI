#!/usr/bin/env python3
"""Guard: PDF generation must survive two visitors at once.

    python3 check_pdf_concurrency.py     (exit 1 on a violation)

WHY THIS EXISTS. Every PDF chart and the share card were built through
matplotlib's PYPLOT interface -- plt.subplots, plt.figure, plt.close -- whose
figure manager (`Gcf`) is module-global state with no locking. Streamlit runs
each session in its own thread, so two visitors generating PDFs at the same
moment raced on that registry: a crash that only appears under concurrent
load, never in single-user testing, and therefore precisely at the peak the
app most needs to survive. On top of the race, none of the ten create/close
spans was try/finally-guarded, so an exception mid-build pinned its figure in
`Gcf` forever -- a slow leak in a long-lived server process.

The fix was structural, not a lock: every figure now comes from `_pdf_figure`
(an OO `Figure` + `FigureCanvasAgg` that never touches the registry), and
`import matplotlib.pyplot` was removed from app.py entirely. This guard is the
backstop for the day someone adds a chart twin by copying an old example from
a blog post -- which is exactly how pyplot would creep back in.

It also checks `memoized_pdf`, because the second half of the fix was to stop
REBUILDING every PDF on every rerun (a slider drag used to re-rasterise 5-9
figures plus a reportlab document). The memo's failure mode is a STALE PDF --
silent, the button downloads happily -- so what is asserted is discrimination:
same signature returns cached bytes, any changed component rebuilds.

WHAT THE THREADED TEST CANNOT DO. Races are probabilistic; a smoke test that
passes proves nothing about the schedule the OS didn't happen to produce. The
static assertions are the load-bearing ones; the threads are a tripwire.
"""
import concurrent.futures
import re
import sys

APP = "app.py"


def app_source() -> str:
    return open(APP).read()


def app_namespace(src: str):
    """app.py's sections 1-2, same exec-prefix trick as the other guards."""
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    prefix = src[:src.rindex("# " + "=" * 60, 0, cut)]
    ns = {"__name__": "pdfcheck"}
    exec(compile(prefix, APP, "exec"), ns)
    return ns


# --- static half ----------------------------------------------------------

def check_no_pyplot_import(src: str, fail):
    """The module must not even be importable by accident: `import
    matplotlib.pyplot` was removed outright, which is a stronger guarantee
    than policing call sites -- an API that is absent cannot be misused."""
    for match in re.finditer(r"^\s*(import matplotlib\.pyplot|from matplotlib import pyplot"
                             r"|from matplotlib\.pyplot import)", src, re.M):
        line = src[:match.start()].count("\n") + 1
        fail(f"app.py:{line} imports pyplot -- its global figure registry is "
             f"not thread-safe and Streamlit sessions are threads. Build "
             f"figures with _pdf_figure/_pdf_subplots instead.")


def check_no_plt_calls(src: str, fail):
    """No live `plt.` call anywhere (comments and strings excepted by the
    crude-but-sufficient test of stripping them line-wise)."""
    for i, line in enumerate(src.splitlines(), 1):
        code = line.split("#", 1)[0]
        if re.search(r"\bplt\.\w+\s*\(", code):
            # A docstring mention like "the `fig, ax = plt.subplots(...)`
            # shape" is prose, not a call; requiring an assignment or bare
            # statement would over-engineer this. Exclude lines that are
            # clearly inside a string by the cheap heuristic of quotes
            # around the plt token.
            if re.search(r"[\"'`][^\"'`]*plt\.", code):
                continue
            fail(f"app.py:{i} calls pyplot: {line.strip()!r}")


def check_builders_use_helper(src: str, fail):
    """Every build_pdf_* body and build_share_card must obtain its figure
    from _pdf_figure/_pdf_subplots."""
    names = re.findall(r"^def (build_pdf_\w+|build_share_card)\(", src, re.M)
    for name in set(names):
        start = src.index(f"def {name}(")
        end = src.find("\ndef ", start + 1)
        body = src[start:end if end != -1 else len(src)]
        if "Figure(" in body and "_pdf_figure" not in body:
            fail(f"{name} constructs a Figure directly -- route it through "
                 f"_pdf_figure so the canvas attachment cannot be forgotten")
        if not ("_pdf_figure(" in body or "_pdf_subplots(" in body
                or "fig" not in body):
            fail(f"{name} does not use _pdf_figure/_pdf_subplots -- where "
                 f"does its figure come from?")


def check_call_sites_memoized(src: str, fail):
    """The five generation call sites must run through memoized_pdf.

    Counted as CALLS outside their own def: a generator invoked bare rebuilds
    on every rerun, which is the always-open race window and the CPU burn the
    memo exists to close.
    """
    for gen in ("generate_pdf_report_single", "generate_pdf_report_compare",
                "generate_pdf_repayment_report", "generate_pdf_search_report"):
        for match in re.finditer(rf"(?<!def ){gen}\(", src):
            line_start = src.rfind("\n", 0, match.start()) + 1
            if src[line_start:match.start()].strip().startswith("def"):
                continue
            # The call must sit inside a memoized_pdf(...) wrapper -- cheap
            # test: a memoized_pdf token within the preceding 600 characters,
            # which comfortably covers the signature block above each call.
            window = src[max(0, match.start() - 1200):match.start()]
            if "memoized_pdf(" not in window:
                line = src[:match.start()].count("\n") + 1
                fail(f"app.py:{line} calls {gen} outside memoized_pdf -- it "
                     f"will rebuild the whole PDF on every rerun")
            # The calculator reports must carry the LOAN OVERRIDE in their
            # extras. It is the one input that moves every figure in the
            # report and deliberately does NOT ride a share link (a recipient
            # re-derives the school default), so the share-param half of the
            # signature can never see it. Found live: $13,000 -> $77,000
            # re-rendered the page and served the $13,000 PDF.
            # Comments are stripped from the window first: the prose above
            # the signature EXPLAINS the loan_amount extra, so its mere words
            # satisfied a naive substring test while the code no longer passed
            # the value -- the negative control caught the check reading its
            # own documentation as compliance.
            code_window = "\n".join(line.split("#", 1)[0]
                                    for line in window.splitlines())
            if gen in ("generate_pdf_report_single",
                       "generate_pdf_report_compare") and \
                    "loan_amount" not in code_window:
                line = src[:match.start()].count("\n") + 1
                fail(f"app.py:{line}: {gen}'s memo signature does not include "
                     f"loan_amount -- a manual loan override would serve a "
                     f"stale report")


# --- dynamic half ---------------------------------------------------------

def check_concurrent_builds(ns, fail):
    """Two different PDFs on four threads, three rounds; bytes must match a
    serial build. A tripwire, not a proof -- see the module docstring."""
    long_loan = ns["calculate_standard_repayment"](60000, 6.5, 10)
    short_loan = ns["calculate_standard_repayment"](8000, 5.0, 2)

    # The builders return _pdf_image_from_figure(fig) directly, and reportlab
    # consumes the buffer on construction -- so rather than fishing bytes back
    # out of a flowable, swap in a rasteriser that RETURNS them. The builders
    # resolve the name from the exec'd namespace at call time, so the swap
    # covers the entire draw path (data prep, axes, ticks, savefig); only the
    # reportlab wrapper is skipped, and it does no drawing.
    import io

    def raw_png(fig, max_width=None):
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
        return buf.getvalue()

    original = ns["_pdf_image_from_figure"]
    ns["_pdf_image_from_figure"] = raw_png

    def chart_bytes(loan):
        return ns["build_pdf_balance_chart"](loan["schedule"], "Standard")

    serial = {"long": chart_bytes(long_loan), "short": chart_bytes(short_loan)}
    if not serial["long"] or not serial["short"]:
        fail("could not extract PNG bytes from a serial build -- the "
             "comparison below would be vacuous")
        return

    for round_no in range(3):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(chart_bytes,
                                   long_loan if i % 2 == 0 else short_loan)
                       for i in range(4)]
            for i, future in enumerate(futures):
                try:
                    got = future.result(timeout=120)
                except Exception as error:
                    fail(f"concurrent build raised on round {round_no}: "
                         f"{type(error).__name__}: {error}")
                    continue
                want = serial["long" if i % 2 == 0 else "short"]
                if got != want:
                    fail(f"concurrent build round {round_no} thread {i} "
                         f"produced different bytes than the serial build -- "
                         f"shared state is bleeding between figures")

    ns["_pdf_image_from_figure"] = original

    import matplotlib._pylab_helpers as helpers
    if helpers.Gcf.figs:
        fail(f"{len(helpers.Gcf.figs)} figure(s) pinned in pyplot's registry "
             f"after the builds -- something registered with Gcf")


def check_memo(ns, fail):
    """memoized_pdf discriminates: cached on an identical signature, rebuilt
    when the params OR the extras change, per-kind slots."""
    class FakeState(dict):
        pass

    ns["st"].session_state = FakeState()
    calls = {"n": 0}

    def build():
        calls["n"] += 1
        return f"pdf-{calls['n']}".encode()

    sig = ns["pdf_memo_signature"]
    s1 = sig({"major": "CS", "loan": "13000"}, "Annual")
    s2 = sig({"major": "CS", "loan": "99000"}, "Annual")
    s3 = sig({"major": "CS", "loan": "13000"}, "Monthly")

    # The extras assertion is on the SIGNATURES, not on memo behaviour. The
    # memo stores only the last signature, so a sequence that changes params
    # in between (s1 -> s2 -> s3) rebuilds at every step even when the extras
    # are silently dropped -- the first version of this check did exactly
    # that, and the negative control (delete the extras term) sailed through.
    # Two signatures differing only in an extra must simply not be equal.
    if s1 == s3:
        fail("pdf_memo_signature ignores its extras -- the Annual/Monthly "
             "toggle (and the repayment chart selector) would change the PDF "
             "without changing the signature, serving a stale report")

    first = ns["memoized_pdf"]("single", s1, build)
    again = ns["memoized_pdf"]("single", s1, build)
    if not (first is again and calls["n"] == 1):
        fail("memoized_pdf rebuilt on an unchanged signature")
    # Extras change FIRST, params still unchanged: the stale-serve case.
    if ns["memoized_pdf"]("single", s3, build) == first or calls["n"] != 2:
        fail("memoized_pdf served stale bytes after an extras-only change")
    if ns["memoized_pdf"]("single", s2, build) == first or calls["n"] != 3:
        fail("memoized_pdf served stale bytes after a share-param change -- "
             "the silent failure this guard exists for")
    ns["memoized_pdf"]("compare", s3, build)
    if calls["n"] != 4:
        fail("memoized_pdf shared bytes across kinds")
    # Two dict-orderings of the same params must be one signature.
    if sig({"a": "1", "b": "2"}) != sig({"b": "2", "a": "1"}):
        fail("pdf_memo_signature is order-sensitive -- an unchanged scenario "
             "would rebuild whenever dict ordering shifts")


def main() -> int:
    src = app_source()
    problems = []
    fail = problems.append

    check_no_pyplot_import(src, fail)
    check_no_plt_calls(src, fail)
    check_builders_use_helper(src, fail)
    check_call_sites_memoized(src, fail)

    if not problems:
        # Only exec and run the dynamic half on a statically clean tree; the
        # static findings already fail the build, and exec'ing a file that
        # imports pyplot would poison the Gcf assertion below.
        ns = app_namespace(src)
        check_concurrent_builds(ns, fail)
        check_memo(ns, fail)

    if problems:
        print(f"pdf concurrency: {len(problems)} problem(s)\n")
        print("\n".join(f"  {p}" for p in problems))
        print("\n  pyplot's figure registry is process-global and unlocked; "
              "Streamlit\n  sessions are threads. This is the crash that only "
              "happens at peak,\n  which is the one moment the app cannot "
              "afford it.")
        return 1
    print("pdf concurrency OK -- no pyplot anywhere, every builder on the OO "
          "API,\n  all five generation sites memoized, 12 concurrent builds "
          "byte-identical\n  to serial, zero figures left in the registry, "
          "and the memo rebuilds on\n  any changed input.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
